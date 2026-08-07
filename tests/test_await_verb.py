"""``await:`` parsing and evaluation — the wait with nothing to forget (#1187)."""

from __future__ import annotations

from brr import await_verb


# ── parse_await ──────────────────────────────────────────────────────


def test_parse_await_requires_timeout():
    file_path, timeout, error = await_verb.parse_await({"await": "true"})
    assert file_path is None
    assert timeout is None
    assert "timeout" in error


def test_parse_await_rejects_unparseable_timeout():
    _file, timeout, error = await_verb.parse_await(
        {"await": "true", "timeout": "banana"}
    )
    assert timeout is None
    assert "not a parseable duration" in error


def test_parse_await_rejects_non_positive_timeout():
    _file, timeout, error = await_verb.parse_await(
        {"await": "true", "timeout": "0s"}
    )
    assert timeout is None
    assert "positive" in error


def test_parse_await_bare_marker_arms_a_plain_wait():
    """The whole documented shape: a ceiling and nothing else."""
    file_path, timeout, error = await_verb.parse_await(
        {"await": "true", "timeout": "20m"}
    )
    assert error is None
    assert timeout == 1200.0
    assert file_path is None


def test_parse_await_accepts_an_empty_marker():
    file_path, timeout, error = await_verb.parse_await(
        {"await": "", "timeout": "5m"}
    )
    assert (file_path, timeout, error) == (None, 300.0, None)


def test_parse_await_takes_an_optional_file_trigger():
    file_path, timeout, error = await_verb.parse_await(
        {"await": "true", "timeout": "5m", "file": "/tmp/gate.log"}
    )
    assert error is None
    assert file_path == "/tmp/gate.log"
    assert timeout == 300.0


def test_parse_await_refuses_a_retired_spawn_condition():
    """#1187's actual failure: five child ids, one typo, the whole directive
    silently discarded. A directive still carrying v1's condition grammar is
    refused *by name*, never ignored-with-the-extra-terms-dropped — and the
    refusal points at the verb that replaced it.

    Neuter check (do this by hand, don't ship it): make ``parse_await``
    ignore the ``await:`` value instead of validating it against
    ``_MARKER_VALUES`` and rerun — this test goes red, because a stale
    ``await: spawn:evt-x`` would then arm a *silently different* wait than
    the one its author typed.
    """
    file_path, timeout, error = await_verb.parse_await(
        {"await": "spawn:evt-abcd", "timeout": "5m"}
    )
    assert (file_path, timeout) == (None, None)
    assert "brnrd await" in error
    assert "no longer takes conditions" in error


def test_parse_await_refuses_a_retired_pid_condition():
    _file, _timeout, error = await_verb.parse_await(
        {"await": "pid:4242", "timeout": "5m"}
    )
    assert "brnrd await" in error


def test_parse_await_refuses_the_old_pipe_list():
    _file, _timeout, error = await_verb.parse_await(
        {"await": "file:/tmp/x | event", "timeout": "5m"}
    )
    assert "brnrd await" in error


def test_parse_await_refuses_a_file_value_still_wearing_the_condition_prefix():
    """``file: file:/tmp/x`` would otherwise arm a wait on a path that cannot
    exist — a silent never-fires, which is the failure mode, not a typo."""
    _file, _timeout, error = await_verb.parse_await(
        {"await": "true", "timeout": "5m", "file": "file:/tmp/x"}
    )
    assert "brnrd await" in error


# ── evaluate ─────────────────────────────────────────────────────────


def test_evaluate_resolves_on_any_pending_event():
    """``event`` is the semantics now, not a condition someone can omit."""
    outcome, which = await_verb.evaluate(None, [{"id": "evt-x", "source": "telegram"}])
    assert (outcome, which) == ("event", None)


def test_evaluate_resolves_on_a_spawn_completion_with_no_id_named():
    """A strand finishing already creates an event, so a dispatcher waiting on
    "whichever child finishes first" needs to name nothing at all."""
    pending = [{"source": "spawn_completed", "spawned_by_run": "run-child"}]
    assert await_verb.evaluate(None, pending) == ("event", None)


def test_evaluate_file_trigger_fires(tmp_path):
    target = tmp_path / "gate.log"
    assert await_verb.evaluate(str(target), []) == (None, None)
    target.write_text("done", encoding="utf-8")
    assert await_verb.evaluate(str(target), []) == ("condition", f"file:{target}")


def test_evaluate_pending_events_outrank_the_file_trigger(tmp_path):
    """A wait a correspondent cannot interrupt is the failure #959 exists to
    end — so when both would fire, the message is the answer, never the file.

    Neuter check (do this by hand, don't ship it): move the ``file_path``
    branch above the ``pending_events`` branch in ``evaluate`` and rerun —
    this test goes red, and the verb starts masking correspondents behind a
    trigger the caller added as a *composing* one.
    """
    target = tmp_path / "gate.log"
    target.write_text("done", encoding="utf-8")
    pending = [{"id": "evt-maintainer", "source": "telegram"}]
    assert await_verb.evaluate(str(target), pending) == ("event", None)


def test_evaluate_nothing_pending_stays_unresolved():
    assert await_verb.evaluate(None, []) == (None, None)
    assert await_verb.evaluate("/tmp/definitely-not-here-4242", []) == (None, None)
