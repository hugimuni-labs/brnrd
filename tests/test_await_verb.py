"""``await:`` parsing and condition evaluation (#959)."""

from __future__ import annotations

import os

from brr import await_verb


# ── parse_await ──────────────────────────────────────────────────────


def test_parse_await_requires_timeout():
    conditions, timeout, error = await_verb.parse_await({"await": "file:/tmp/x"})
    assert conditions is None
    assert timeout is None
    assert "timeout" in error


def test_parse_await_rejects_unparseable_timeout():
    conditions, timeout, error = await_verb.parse_await(
        {"await": "event", "timeout": "banana"}
    )
    assert conditions is None
    assert "not a parseable duration" in error


def test_parse_await_rejects_non_positive_timeout():
    conditions, timeout, error = await_verb.parse_await(
        {"await": "event", "timeout": "0s"}
    )
    assert conditions is None
    assert "positive" in error


def test_parse_await_rejects_unknown_condition():
    conditions, timeout, error = await_verb.parse_await(
        {"await": "carrier-pigeon:42", "timeout": "5m"}
    )
    assert conditions is None
    assert timeout is None
    assert "unrecognised condition" in error


def test_parse_await_rejects_malformed_pid():
    conditions, timeout, error = await_verb.parse_await(
        {"await": "pid:notanumber", "timeout": "5m"}
    )
    assert conditions is None
    assert "not a numeric pid" in error


def test_parse_await_rejects_empty_file_path():
    conditions, timeout, error = await_verb.parse_await(
        {"await": "file:", "timeout": "5m"}
    )
    assert conditions is None
    assert "names no path" in error


def test_parse_await_parses_file_pid_spawn_and_appends_event():
    conditions, timeout, error = await_verb.parse_await(
        {"await": "file:/tmp/gate.log | pid:4242 | spawn:evt-abcd", "timeout": "20m"}
    )
    assert error is None
    assert timeout == 1200.0
    kinds = [c["kind"] for c in conditions]
    assert kinds == ["file", "pid", "spawn", "event"]
    assert conditions[0]["value"] == "/tmp/gate.log"
    assert conditions[1]["value"] == 4242
    assert conditions[2]["value"] == "evt-abcd"


def test_parse_await_structurally_always_includes_event():
    """#959's central guarantee: a resident cannot forget the message condition.

    Neuter check (do this by hand, don't ship it): comment out the
    "append EVENT_CONDITION when absent" block in ``parse_await`` and rerun
    this test — it goes red, because a bare ``file:`` await would then parse
    to exactly one condition with no ``event`` member at all. That is the
    guard #959 asks to be neutered and watched fail.
    """
    conditions, _timeout, error = await_verb.parse_await(
        {"await": "file:/tmp/only-this", "timeout": "5m"}
    )
    assert error is None
    assert any(c["kind"] == "event" for c in conditions)


def test_parse_await_with_no_conditions_still_gets_event():
    conditions, _timeout, error = await_verb.parse_await({"timeout": "5m"})
    assert error is None
    assert conditions == [dict(await_verb.EVENT_CONDITION)]


def test_parse_await_explicit_event_is_not_duplicated():
    conditions, _timeout, error = await_verb.parse_await(
        {"await": "event", "timeout": "5m"}
    )
    assert error is None
    assert conditions == [dict(await_verb.EVENT_CONDITION)]


def test_parse_await_rejects_empty_token_in_pipe_list():
    conditions, _timeout, error = await_verb.parse_await(
        {"await": "file:/tmp/x || pid:1", "timeout": "5m"}
    )
    assert conditions is None
    assert "empty condition" in error


# ── pid_alive ────────────────────────────────────────────────────────


def test_pid_alive_true_for_own_process():
    assert await_verb.pid_alive(os.getpid()) is True


def test_pid_alive_false_for_a_pid_that_cannot_exist():
    # PID 1 always exists on a real system; use an absurdly large, almost
    # certainly-unassigned pid instead of assuming a fixed dead pid number.
    assert await_verb.pid_alive(2**30) is False


# ── evaluate ─────────────────────────────────────────────────────────


def test_evaluate_file_condition_fires(tmp_path):
    target = tmp_path / "gate.log"
    conditions = [{"kind": "file", "raw": f"file:{target}", "value": str(target)}]
    outcome, which = await_verb.evaluate(conditions, [])
    assert (outcome, which) == (None, None)
    target.write_text("done", encoding="utf-8")
    outcome, which = await_verb.evaluate(conditions, [])
    assert outcome == "condition"
    assert which == f"file:{target}"


def test_evaluate_pid_condition_fires_on_exit():
    dead_pid = 2**30
    conditions = [{"kind": "pid", "raw": f"pid:{dead_pid}", "value": dead_pid}]
    outcome, which = await_verb.evaluate(conditions, [])
    assert outcome == "condition"
    assert which == f"pid:{dead_pid}"


def test_evaluate_pid_condition_does_not_fire_while_alive():
    conditions = [{"kind": "pid", "raw": f"pid:{os.getpid()}", "value": os.getpid()}]
    outcome, which = await_verb.evaluate(conditions, [])
    assert (outcome, which) == (None, None)


def test_evaluate_spawn_condition_matches_run_id():
    conditions = [{"kind": "spawn", "raw": "spawn:run-child", "value": "run-child"}]
    pending = [{"source": "spawn_completed", "spawned_by_run": "run-child"}]
    outcome, which = await_verb.evaluate(conditions, pending)
    assert (outcome, which) == ("condition", "spawn:run-child")


def test_evaluate_spawn_condition_matches_dispatch_event_id():
    conditions = [{"kind": "spawn", "raw": "spawn:evt-orig", "value": "evt-orig"}]
    pending = [{"source": "spawn_completed", "spawned_by_event": "evt-orig"}]
    outcome, which = await_verb.evaluate(conditions, pending)
    assert (outcome, which) == ("condition", "spawn:evt-orig")


def test_evaluate_spawn_condition_ignores_unrelated_completions():
    """The named ``spawn:`` term doesn't fire for a *different* child's
    completion — but the structural ``event`` outcome still does, because an
    unrelated pending event is exactly what that outcome is for."""
    conditions = [{"kind": "spawn", "raw": "spawn:run-child", "value": "run-child"}]
    pending = [{"source": "spawn_completed", "spawned_by_run": "run-other"}]
    outcome, which = await_verb.evaluate(conditions, pending)
    assert (outcome, which) == ("event", None)


def test_evaluate_falls_back_to_event_outcome_for_unrelated_pending():
    conditions = [dict(await_verb.EVENT_CONDITION)]
    pending = [{"id": "evt-unrelated", "source": "telegram"}]
    outcome, which = await_verb.evaluate(conditions, pending)
    assert (outcome, which) == ("event", None)


def test_evaluate_prefers_named_condition_over_event_when_both_true():
    """A pending event that *is* the spawn completion names the condition.

    Ordering matters: a resident named ``spawn:run-child`` specifically, and
    the only pending event is exactly that completion — the three-outcome
    contract says the *specific* answer, not the generic "a message showed
    up" one, when both would technically be true.
    """
    conditions = [
        {"kind": "spawn", "raw": "spawn:run-child", "value": "run-child"},
        dict(await_verb.EVENT_CONDITION),
    ]
    pending = [{"source": "spawn_completed", "spawned_by_run": "run-child"}]
    outcome, which = await_verb.evaluate(conditions, pending)
    assert (outcome, which) == ("condition", "spawn:run-child")


def test_evaluate_nothing_pending_stays_unresolved():
    conditions = [dict(await_verb.EVENT_CONDITION)]
    outcome, which = await_verb.evaluate(conditions, [])
    assert (outcome, which) == (None, None)
