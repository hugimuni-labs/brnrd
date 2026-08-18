"""Tests for ``brnrd do`` — CLI-entry tests, mirroring ``test_cli.py``.

Every test drives ``brr.cli.main`` (the actual entry point) rather than
``brr.do``'s functions directly, per the task's own instruction: verify the
porcelain the way a resident's shell actually calls it. Fixtures always
carry ``attention.pending_event_count`` — a fixture missing that field
degrades other renderers to an unknown state elsewhere in this codebase,
so every fixture here matches the real daemon payload shape rather than a
trimmed-down one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from brr import do as do_mod
from brr.cli import main


def _portal_state(outbox: Path, **overrides):
    payload = {
        "version": 1,
        "change_token": "tok-1",
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "outbound": {"replies_current": 0, "replies_other": 0, "outbound_messages": 0},
        "notices": [],
        "resources": {
            "quota": {"status": "known", "summary": "weekly 42%"},
            "coexisting_runs": {
                "spawn_pool": {"max_concurrent": 3, "active": 1, "available": 2},
            },
        },
    }
    payload.update(overrides)
    (outbox / "portal-state.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _do_env(monkeypatch, outbox):
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))


# ── bare snapshot ────────────────────────────────────────────────────


def test_bare_do_prints_the_portal_snapshot(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(
        outbox,
        attention={"pending_event_count": 1, "pending_outbox_file_count": 0},
        inbound={"events": [{"id": "evt-2", "source": "telegram", "summary": "hi"}]},
    )

    assert main(["do"]) == 0
    out = capsys.readouterr().out
    assert "run=run-1" in out
    assert "pending events (1): evt-2 telegram: hi" in out
    assert "outbound: current=0 other=0 outbound=0" in out
    assert "notices: none" in out
    assert "quota: weekly 42%" in out
    assert "spawn pool: 1/3 used, 2 available" in out


def test_bare_do_renders_notices(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(
        outbox,
        notices=[{"at": "t", "kind": "refused", "text": "spawn refused: no pool capacity"}],
    )

    assert main(["do"]) == 0
    out = capsys.readouterr().out
    assert "notices (1): refused: spawn refused: no pool capacity" in out


def test_bare_do_outside_a_wake_says_why(monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["do"]) == 1
    err = capsys.readouterr().err
    assert "no run outbox" in err


def test_bare_do_no_live_portal_state(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)

    assert main(["do"]) == 1
    err = capsys.readouterr().err
    assert "no live portal-state.json" in err


def test_do_outbox_flag_overrides_environment(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    _portal_state(outbox)

    assert main(["do", "--outbox", str(outbox)]) == 0
    assert "run=run-1" in capsys.readouterr().out


def test_do_explicit_bad_outbox_names_the_path_when_staging(tmp_path, monkeypatch, capsys):
    """A relative ``--outbox`` that resolves nowhere from the current cwd is a
    caller mistake, not a crash. Repro shape from #1337: cwd inside the
    outbox, then the same relative path re-passed to ``--outbox`` doubles
    against cwd (`<outbox>/.brr/outbox/<evt>`) instead of naming itself —
    precedent for `monkeypatch.chdir` mid-test: tests/test_cli.py:52, :1113.
    """
    outbox = tmp_path / ".brr" / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    monkeypatch.chdir(outbox)
    relative = ".brr/outbox/evt-1"

    assert main(["do", "--outbox", relative, "--note", "evt-1"]) == 1
    err = capsys.readouterr().err
    assert relative in err
    assert str(outbox / relative) in err
    assert "no such directory" in err
    assert "Nothing was staged" in err


def test_do_bare_explicit_bad_outbox_is_not_silently_swallowed(tmp_path, monkeypatch, capsys):
    """The asymmetry #1337 named: bare `brnrd do` (no verbs) used to read a bad
    explicit ``--outbox`` back as an innocent "no live portal-state.json"
    absence, identical to the legitimate no-run case. An *explicit* argument
    that fails to resolve must say so instead — only the env-derived
    fallback stays lenient.
    """
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    bad = tmp_path / "does-not-exist"

    assert main(["do", "--outbox", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "no such directory" in err
    assert "no live portal-state.json" not in err


def test_do_env_derived_absent_outbox_still_reads_as_absence(tmp_path, monkeypatch, capsys):
    """The leniency this fix must not touch: a live-run env var pointing at a
    directory that no longer exists (e.g. a stale ``BRR_OUTBOX_DIR`` from a
    finished run) is the legitimate "nothing to report" case, not a caller
    typo — `do.read_portal_state`'s ``OSError`` swallow is correct here and
    must stay untouched by the new explicit-argument check.
    """
    stale = tmp_path / "long-gone"
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(stale))
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["do"]) == 1
    err = capsys.readouterr().err
    assert "no live portal-state.json" in err
    assert "no such directory" not in err


# ── --mood ───────────────────────────────────────────────────────────


def test_mood_command_explicit_bad_outbox_names_the_path(tmp_path, capsys):
    """`brnrd mood` is the standalone verb (`cli.cmd_mood`), not `do
    --mood` — it shares the same `Path(explicit) if explicit else
    _wake_outbox_dir()` shape #1337 names, so it needs the same check.
    """
    bad = tmp_path / "does-not-exist"

    assert main(["mood", "focused", "--outbox", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "no such directory" in err
    assert "Nothing was written" in err


def test_do_mood_resolves_and_writes_the_control_file(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main(["do", "--mood", "focused", "--mood-note", "deep in it"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("mood ")
    assert "fo.cus ✓" in out

    lines = (outbox / ".mood").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "fo.cus"
    assert lines[1] == "deep in it"


def test_do_mood_unresolved_writes_nothing_and_names_near_misses(
    tmp_path, monkeypatch, capsys,
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main(["do", "--mood", "zzzznotarealface"]) == 1
    out = capsys.readouterr().out
    assert "✗ no match" in out
    assert not (outbox / ".mood").exists()


def test_do_mood_note_without_mood_is_rejected(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main(["do", "--mood-note", "x"]) == 1
    assert "--mood-note only applies with --mood" in capsys.readouterr().err


# ── --note / --reply / --gate: the verdict-observation contract ────────


def _consume_after_one_sleep(outbox, glob, *, notice=None, bolt=None):
    """A ``time.sleep`` replacement: on first call, retire the one staged
    file matching *glob* (mirroring ``_retire_outbox_staging`` — the file
    just needs to stop existing at the path this module wrote), optionally
    dropping a matching notice into ``portal-state.json`` first, and/or
    stamping the ``bolt`` facet ``cmd_cut``'s accept path reads back
    (``daemon.py`` sets ``task.meta["bolt"]`` in the same drain branch that
    accepts a ``cut:`` — a real accepted cut always produces one)."""

    def _sleep(_seconds):
        matches = list(outbox.glob(glob))
        if not matches:
            return
        if notice is not None or bolt is not None:
            payload = json.loads((outbox / "portal-state.json").read_text(encoding="utf-8"))
            if notice is not None:
                payload.setdefault("notices", []).append(notice)
            if bolt is not None:
                payload["bolt"] = bolt
            (outbox / "portal-state.json").write_text(json.dumps(payload), encoding="utf-8")
        matches[0].unlink()

    return _sleep


#: A minimal "the daemon really did accept this" bolt facet — the shape
#: ``daemon.py`` stamps into ``task.meta["bolt"]`` (and, next tick,
#: ``portal-state.json``) the instant a ``cut:`` clears ``_cut_mismatches``
#: with nothing to annotate. Fixtures for a *clean* accepted cut pass this
#: so ``cmd_cut``'s post-#1221 bolt-facet confirmation has something real to
#: find — a fixture with no ``bolt`` key models a cut the daemon never
#: actually recorded (see ``test_cut_reports_unconfirmed_when_the_bolt_facet_never_appears``).
_CLEAN_BOLT = {"accepted": True, "annotated": 0, "accepted_at": "2026-08-08T00:00:00Z"}


def test_do_note_ok_when_consumed_cleanly(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-note-*.md"))

    assert main(["do", "--note", "evt-42at"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "note evt-42at ✓"


def test_do_reply_stages_canonical_frontmatter_and_reports_ok(
    tmp_path, monkeypatch, capsys,
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    body_file = tmp_path / "body.md"
    body_file.write_text("the reply text\n", encoding="utf-8")

    written = {}

    def _sleep(_seconds):
        matches = list(outbox.glob("do-*-reply-*.md"))
        if matches:
            written["text"] = matches[0].read_text(encoding="utf-8")
            matches[0].unlink()

    monkeypatch.setattr(time, "sleep", _sleep)

    assert main([
        "do", "--reply", "evt-9s45", "--body-file", str(body_file),
    ]) == 0
    assert capsys.readouterr().out.strip() == "reply evt-9s45 ✓"
    assert written["text"] == "---\nevent: evt-9s45\n---\nthe reply text\n"


def test_do_reply_inline_body(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main(["do", "--reply", "evt-1", "--body", "hello"]) == 0
    assert capsys.readouterr().out.strip() == "reply evt-1 ✓"


def test_do_gate_stages_and_reports_ok(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    body_file = tmp_path / "body.md"
    body_file.write_text("ping\n", encoding="utf-8")
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-gate-*.md"))

    assert main(["do", "--gate", "telegram", "--body-file", str(body_file)]) == 0
    assert capsys.readouterr().out.strip() == "gate telegram ✓"


def test_do_reply_failed_surfaces_the_matching_notice(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    notice = {
        "at": "2026-08-03T00:00:00Z", "kind": "refused",
        "text": "reply dropped: event evt-9 not found in any inbox (the id is wrong, or the event is gone)",
    }
    monkeypatch.setattr(
        time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md", notice=notice),
    )

    assert main(["do", "--reply", "evt-9", "--body", "hi"]) == 1
    out = capsys.readouterr().out.strip()
    assert out.startswith("reply evt-9 ✗ refused: reply dropped: event evt-9 not found")


def test_do_note_ignores_an_unrelated_fresh_notice(tmp_path, monkeypatch, capsys):
    """A notice that landed fresh but names a different verb/target is not
    evidence about *this* directive — the OK path stays OK."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    unrelated = {
        "at": "t", "kind": "refused",
        "text": "spawn refused: the account home is a shared host tree",
    }
    monkeypatch.setattr(
        time, "sleep", _consume_after_one_sleep(outbox, "do-*-note-*.md", notice=unrelated),
    )

    assert main(["do", "--note", "evt-1"]) == 0
    assert capsys.readouterr().out.strip() == "note evt-1 ✓"


def test_do_note_ignores_a_stale_pre_existing_notice(tmp_path, monkeypatch, capsys):
    """Snapshot-before-stage: a notice already sitting in ``notices`` before
    this directive was even staged must never be blamed on it."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(
        outbox,
        notices=[{
            "at": "t0", "kind": "refused",
            "text": "note dropped: event evt-1 not found in any inbox",
        }],
    )
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-note-*.md"))

    assert main(["do", "--note", "evt-1"]) == 0
    assert capsys.readouterr().out.strip() == "note evt-1 ✓"


def _consume_then_notice_after_delay(outbox, glob, notice, *, delay_calls=3):
    """Simulate the #1219 timing hole without a real wait: the daemon
    retires the staged file and appends the drop notice as two *separate*
    writes within the same drain tick (``daemon.py``'s ``_drain_outbox``
    runs first — deletes the file, writes ``.notices.jsonl`` — then several
    more steps, then ``_write_live_portal_state`` finally rewrites
    ``portal-state.json`` with that notice folded in). A poll landing
    between those two writes sees the file already gone but the notice not
    yet reflected. Here: the file vanishes on the first ``sleep`` call: the
    notice reaches ``portal-state.json`` only a couple of calls later."""

    calls = {"n": 0}

    def _sleep(_seconds):
        calls["n"] += 1
        matches = list(outbox.glob(glob))
        if matches:
            matches[0].unlink()
        if calls["n"] == delay_calls:
            payload = json.loads(
                (outbox / "portal-state.json").read_text(encoding="utf-8"),
            )
            payload.setdefault("notices", []).append(notice)
            (outbox / "portal-state.json").write_text(
                json.dumps(payload), encoding="utf-8",
            )

    return _sleep


def test_do_note_catches_a_notice_that_lands_after_the_file_is_already_gone(
    tmp_path, monkeypatch, capsys,
):
    """#1219: a fresh notice that reaches ``portal-state.json`` a couple of
    daemon ticks *after* the staged file was already retired must still be
    read as the verdict — trusting "file gone, no notice (yet)" as proof of
    a clean accept is exactly the timing hole #1219 measured live (three
    grammar-refused ``cut:`` directives, each reported ``accepted``)."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    notice = {
        "at": "2026-08-08T00:00:00Z", "kind": "dropped",
        "text": "note dropped: event evt-1 not found in any inbox",
    }
    monkeypatch.setattr(
        time, "sleep",
        _consume_then_notice_after_delay(outbox, "do-*-note-*.md", notice),
    )

    assert main(["do", "--note", "evt-1"]) == 1
    out = capsys.readouterr().out.strip()
    assert out.startswith("note evt-1 ✗ dropped: note dropped: event evt-1")


def test_do_note_still_queued_when_never_consumed(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    ticks = []
    monkeypatch.setattr(time, "sleep", lambda s: ticks.append(s))

    assert main(["do", "--note", "evt-1", "--timeout", "0.2"]) == 1
    out = capsys.readouterr().out.strip()
    # #1379: the QUEUED detail now names how long the staged file has been
    # sitting there (the staging file's own mtime, real wall clock) rather
    # than reading identically whether the daemon is 2s or 40m behind.
    assert out.startswith("note evt-1 ? still queued (")
    assert out.endswith("s)")
    assert ticks  # the wait actually polled, not a bare no-op


def test_do_timeout_flag_bounds_every_sleep_call(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    seen = []
    monkeypatch.setattr(time, "sleep", lambda s: seen.append(s))

    assert main(["do", "--note", "evt-1", "--timeout", "0.05"]) == 1
    # Every sleep call is bounded by the small --timeout, never the 30s
    # default `DEFAULT_TIMEOUT_SECONDS`.
    assert seen
    assert all(s <= 0.05 + 1e-6 for s in seen)


# ── batched wait: N verbs share one deadline, not N (#1337) ─────────


class _FakeClock:
    """A monotonic clock that only advances when something sleeps — keeps
    the elapsed-time assertion below instant while still exercising the
    real polling loop body (mirrors ``tests/test_cli.py``'s ``_FakeClock``
    for ``brnrd await``)."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_do_n_verbs_share_one_wait_not_n(tmp_path, monkeypatch, capsys):
    """The defect's second half, pinned: before this fix, staging and
    waiting were fused per verb (the old ``cli.py`` ``_do_note``/
    ``_do_reply``/``_do_gate``), so N verbs against a daemon that never
    drains cost N independent ``--timeout`` waits run back to back — three
    ``--note`` directives, none of them ever consumed, would run the clock
    to ~3x ``--timeout``. Batched, they share one deadline: this asserts
    the *simulated* elapsed time lands at ~1x ``--timeout``, not 3x,
    without any real wall-clock wait.
    """
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    clock = _FakeClock()
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)

    rc = main([
        "do",
        "--note", "evt-1", "--note", "evt-2", "--note", "evt-3",
        "--timeout", "5",
    ])
    assert rc == 1
    parts = capsys.readouterr().out.strip().split(" · ")
    assert len(parts) == 3
    for i, part in enumerate(parts, start=1):
        assert part.startswith(f"note evt-{i} ? still queued (")
        assert part.endswith("s)")

    # The old fused shape would have run the simulated clock to
    # ~3 * 5 = 15s (one full timeout per verb, sequentially). Batched, the
    # three directives share one deadline: elapsed lands at ~1 timeout,
    # with slack only for the final partial poll interval.
    assert clock.now < 5 + do_mod.POLL_INTERVAL_SECONDS + 1e-6


def test_do_n_verbs_all_staged_before_the_shared_wait_begins(tmp_path, monkeypatch, capsys):
    """The other half of the contract: every directive in the batch is
    staged *before* the shared wait starts polling — not stage-one,
    wait-one, stage-next. A sleep spy checks how many of the batch's own
    staged files already exist by the time the very first poll fires; the
    old fused shape would see only 1 (note #1 — note #2 isn't staged until
    note #1's own wait loop returns).
    """
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    seen_at_first_sleep = {}

    def _sleep(_seconds):
        seen_at_first_sleep.setdefault(
            "count", len(list(outbox.glob("do-*-note-*.md"))),
        )

    monkeypatch.setattr(time, "sleep", _sleep)

    main(["do", "--note", "evt-1", "--note", "evt-2", "--note", "evt-3", "--timeout", "0.05"])
    assert seen_at_first_sleep.get("count") == 3


def test_do_batched_notice_fails_at_most_one_directive(tmp_path, monkeypatch, capsys):
    """Batching widens the pre-existing substring-correlation gap
    (``do.py``'s module docstring): with several directives unconsumed at
    once, one fresh notice whose text happens to satisfy more than one
    directive's needles could otherwise fail every directive it textually
    matches, not just the one it actually names. ``evt-11``'s notice text
    contains ``evt-1`` as a literal substring, so both directives' needle
    tuples match the same single notice — a real, not contrived, collision
    shape. The claim-once cap means only one directive (stage order) is
    charged with it.
    """
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    notice = {
        "at": "2026-08-08T00:00:00Z", "kind": "refused",
        "text": "note dropped: event evt-11 not found in any inbox",
    }

    def _sleep(_seconds):
        for p in outbox.glob("do-*-note-*.md"):
            p.unlink()
        payload = json.loads((outbox / "portal-state.json").read_text(encoding="utf-8"))
        payload.setdefault("notices", []).append(notice)
        (outbox / "portal-state.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(time, "sleep", _sleep)

    assert main(["do", "--note", "evt-1", "--note", "evt-11"]) == 1
    out = capsys.readouterr().out.strip()
    parts = out.split(" · ")
    assert len(parts) == 2
    failed = [p for p in parts if "✗" in p]
    ok = [p for p in parts if "✓" in p]
    # exactly one of the two is charged with the notice, never both.
    assert len(failed) == 1
    assert len(ok) == 1
    assert failed[0].startswith("note evt-1 ✗")  # stage order decides


# ── multiple verbs, ordering, and pairing errors ────────────────────


def test_do_multiple_verbs_join_one_summary_line(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    def _sleep(_seconds):
        for p in outbox.glob("do-*.md"):
            p.unlink()

    monkeypatch.setattr(time, "sleep", _sleep)

    rc = main([
        "do", "--mood", "focused", "--note", "evt-1", "--reply", "evt-2", "--body", "hi",
    ])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "mood b·_·d fo.cus ✓ · note evt-1 ✓ · reply evt-2 ✓"


def test_do_body_with_no_preceding_verb_is_rejected(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)

    assert main(["do", "--body", "orphan"]) == 1
    err = capsys.readouterr().err
    assert "no preceding --reply/--gate" in err
    assert not list(outbox.glob("do-*.md"))


def test_do_gate_rejects_inline_body(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)

    assert main(["do", "--gate", "forge", "--body", "hi"]) == 1
    assert "only pairs with --body-file" in capsys.readouterr().err


def test_do_two_replies_back_to_back_with_no_body_is_rejected(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)

    assert main([
        "do", "--reply", "evt-1", "--reply", "evt-2", "--body", "hi",
    ]) == 1
    err = capsys.readouterr().err
    assert "--reply evt-1 has no --body-file/--body" in err


def test_do_reply_missing_body_file_is_rejected_before_staging(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)

    assert main([
        "do", "--reply", "evt-1", "--body-file", str(tmp_path / "missing.md"),
    ]) == 1
    assert "could not read" in capsys.readouterr().err
    assert not list(outbox.glob("do-*.md"))


def test_do_verbs_outside_a_wake_writes_nothing(monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["do", "--note", "evt-1"]) == 1
    assert "Nothing was written" in capsys.readouterr().err


# ── --card ───────────────────────────────────────────────────────────


def test_do_card_overwrites_the_control_file(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    card_file = tmp_path / "card.md"
    card_file.write_text("## Now\nworking on it", encoding="utf-8")

    assert main(["do", "--card", str(card_file)]) == 0
    assert capsys.readouterr().out.strip() == "card ✓"
    assert (outbox / ".card").read_text(encoding="utf-8") == "## Now\nworking on it\n"


def test_do_card_missing_file_is_reported(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main(["do", "--card", str(tmp_path / "missing.md")]) == 1
    assert "card ✗ could not read" in capsys.readouterr().out


# ── passthrough: `-- <command> [args...]` ───────────────────────────


def test_do_passthrough_execs_after_staging_verdicts_to_stderr(
    tmp_path, monkeypatch, capsys,
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    calls = {}

    def _fake_execvp(cmd, argv):
        calls["cmd"] = cmd
        calls["argv"] = list(argv)
        raise SystemExit(0)

    monkeypatch.setattr("os.execvp", _fake_execvp)

    with pytest.raises(SystemExit) as exc:
        main([
            "do", "--mood", "focused", "--timeout", "0.05", "--", "echo", "hi", "there",
        ])
    assert exc.value.code == 0
    assert calls == {"cmd": "echo", "argv": ["echo", "hi", "there"]}

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "mood " in captured.err and "fo.cus ✓" in captured.err


def test_do_passthrough_with_no_verbs_still_execs(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    calls = {}

    def _fake_execvp(cmd, argv):
        calls["cmd"] = cmd
        calls["argv"] = list(argv)
        raise SystemExit(0)

    monkeypatch.setattr("os.execvp", _fake_execvp)

    with pytest.raises(SystemExit):
        main(["do", "--", "git", "status"])
    assert calls == {"cmd": "git", "argv": ["git", "status"]}
    # No verbs and a passthrough present ⇒ no bare-snapshot spam on stdout
    # either; brnrd do's own output (if any) stays on stderr.
    assert capsys.readouterr().out == ""


def test_do_passthrough_command_not_found_reports_and_exits_127(
    tmp_path, monkeypatch, capsys,
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    def _fake_execvp(cmd, argv):
        raise OSError("No such file or directory")

    monkeypatch.setattr("os.execvp", _fake_execvp)

    assert main(["do", "--", "totally-not-a-real-command"]) == 127
    assert "passthrough command not found" in capsys.readouterr().err


def test_do_without_dashdash_is_unaffected(tmp_path, monkeypatch, capsys):
    """No literal ``--`` in argv ⇒ `args.passthrough` stays `None` and every
    other verb behaves exactly as it did before the passthrough existed."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main(["do"]) == 0
    out = capsys.readouterr().out
    assert "run=run-1" in out


# ── brnrd cut (design-the-bolt.md) — the porcelain over cut: ────────


def test_cut_errors_without_an_outbox(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    declaration = tmp_path / "bolt.md"
    declaration.write_text("---\n---\nDone.\n", encoding="utf-8")

    assert main(["cut", str(declaration), "--timeout", "0.01"]) == 1
    assert "no run outbox" in capsys.readouterr().err


def test_cut_explicit_bad_outbox_names_the_path(tmp_path, monkeypatch, capsys):
    """#1337: sibling of `do`/`await` coverage above — `cut` shares the same
    ``Path(args.outbox) if args.outbox else _wake_outbox_dir()`` one-liner
    the ticket names, so it needs the same explicit-argument check.
    """
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    declaration = tmp_path / "bolt.md"
    declaration.write_text("---\n---\nDone.\n", encoding="utf-8")
    bad = tmp_path / "does-not-exist"

    assert main(["cut", str(declaration), "--outbox", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "no such directory" in err
    assert "Nothing was written" in err


def test_cut_errors_for_a_missing_file(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main(["cut", str(tmp_path / "nope.md")]) == 1
    assert "is not a file" in capsys.readouterr().err


def test_cut_reports_accepted_when_consumed_cleanly(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    declaration.write_text("---\n---\nAll done.\n", encoding="utf-8")
    monkeypatch.setattr(
        time, "sleep",
        _consume_after_one_sleep(outbox, "do-*-cut-*.md", bolt=_CLEAN_BOLT),
    )

    assert main(["cut", str(declaration)]) == 0
    out = capsys.readouterr().out.strip()
    # The verdict names its render surface (2026-08-08, his ask): a resident
    # reading "accepted" must learn what should visibly change, or the next
    # bolt night produces another misdiagnosis.
    assert out.startswith("[brnrd cut] accepted — ")
    assert "summons strip" in out


def test_cut_splices_the_marker_into_an_existing_frontmatter_block(
    tmp_path, monkeypatch, capsys,
):
    """The declaration keeps its own frontmatter (``asks:``/``produce:``/
    ...); the porcelain only guarantees the ``cut: true`` marker line."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    declaration.write_text(
        "---\nproduce: none\nowed: none\n---\nAll done.\n", encoding="utf-8",
    )

    staged = {}

    def _sleep(_seconds):
        matches = list(outbox.glob("do-*-cut-*.md"))
        if matches:
            staged["text"] = matches[0].read_text(encoding="utf-8")
            payload = json.loads(
                (outbox / "portal-state.json").read_text(encoding="utf-8"),
            )
            payload["bolt"] = _CLEAN_BOLT
            (outbox / "portal-state.json").write_text(
                json.dumps(payload), encoding="utf-8",
            )
            matches[0].unlink()

    monkeypatch.setattr(time, "sleep", _sleep)

    assert main(["cut", str(declaration)]) == 0
    assert staged["text"] == (
        "---\ncut: true\nproduce: none\nowed: none\n---\nAll done.\n"
    )


def test_cut_refuses_incident_lenient_shape_before_staging(
    tmp_path, monkeypatch, capsys,
):
    """The 2026-08-13 casualty: declaration rows after the lenient fence
    must not be double-wrapped into a minimal bolt with machine text body."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    declaration.write_text(
        "cut: true\n---\nasks:\n"
        "  - event: evt-1786643067802091788-wuxo\n"
        "    disposition: answered\n",
        encoding="utf-8",
    )

    assert main(["cut", str(declaration), "--timeout", "0.01"]) == 1
    err = capsys.readouterr().err
    assert "invalid cut declaration shape" in err
    assert "lists must be keyed mappings" in err
    assert "evt-...: answered" in err
    assert list(outbox.glob("do-*-cut-*.md")) == []


def test_cut_refuses_a_strands_row_stranded_in_the_lenient_body(
    tmp_path, monkeypatch, capsys,
):
    """`strands:` (#1197) is a declaration key exactly like `asks:` — a row
    for it stranded after the lenient fence is the same staging casualty,
    not a legal minimal bolt with machine text swallowed into the body."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    declaration.write_text(
        "cut: true\n---\nstrands:\n"
        "  run-child: handoff — the next wake converges it\n",
        encoding="utf-8",
    )

    assert main(["cut", str(declaration), "--timeout", "0.01"]) == 1
    err = capsys.readouterr().err
    assert "invalid cut declaration shape" in err
    assert list(outbox.glob("do-*-cut-*.md")) == []


def test_cut_refuses_bullet_list_inside_canonical_declaration(
    tmp_path, monkeypatch, capsys,
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    declaration.write_text(
        "---\ncut: true\nasks:\n  - event: evt-one\n"
        "    disposition: answered\n---\nDone.\n",
        encoding="utf-8",
    )

    assert main(["cut", str(declaration)]) == 1
    assert "lists must be keyed mappings" in capsys.readouterr().err
    assert list(outbox.glob("do-*-cut-*.md")) == []


def test_cut_lenient_minimal_shape_stages_without_double_wrap(
    tmp_path, monkeypatch,
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    lenient = "cut: true\n---\nAll done.\n"
    declaration.write_text(lenient, encoding="utf-8")
    staged = {}

    def _sleep(_seconds):
        matches = list(outbox.glob("do-*-cut-*.md"))
        if matches:
            staged["text"] = matches[0].read_text(encoding="utf-8")
            payload = json.loads((outbox / "portal-state.json").read_text())
            payload["bolt"] = _CLEAN_BOLT
            (outbox / "portal-state.json").write_text(json.dumps(payload))
            matches[0].unlink()

    monkeypatch.setattr(time, "sleep", _sleep)

    assert main(["cut", str(declaration)]) == 0
    assert staged["text"] == lenient


def test_cut_canonical_declaration_with_marker_stages_byte_for_byte(
    tmp_path, monkeypatch,
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    canonical = (
        "---\ncut: true\nproduce: none\nowed: none\n---\n"
        "The loom is tied off.\n"
    )
    declaration.write_text(canonical, encoding="utf-8")
    staged = {}

    def _sleep(_seconds):
        matches = list(outbox.glob("do-*-cut-*.md"))
        if matches:
            staged["text"] = matches[0].read_text(encoding="utf-8")
            payload = json.loads((outbox / "portal-state.json").read_text())
            payload["bolt"] = _CLEAN_BOLT
            (outbox / "portal-state.json").write_text(json.dumps(payload))
            matches[0].unlink()

    monkeypatch.setattr(time, "sleep", _sleep)

    assert main(["cut", str(declaration)]) == 0
    assert staged["text"] == canonical


def test_cut_reports_unconfirmed_when_the_bolt_facet_never_appears(
    tmp_path, monkeypatch, capsys,
):
    """#1221: an OK drain verdict (the directive was consumed, no refusal
    notice named it) is not proof of an accepted bolt — ``task.meta["bolt"]``
    is a separate write the *accept* branch of the same drain makes, folded
    into ``portal-state.json`` on a later tick. If that facet never shows up,
    the porcelain must say so plainly rather than print ``accepted`` for a
    run the daemon never actually recorded a bolt for."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)  # no `bolt` key, and nothing below ever adds one
    declaration = tmp_path / "bolt.md"
    declaration.write_text("---\n---\nDone.\n", encoding="utf-8")
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-cut-*.md"))

    assert main(["cut", str(declaration)]) == 1
    err = capsys.readouterr().err.strip()
    assert err.startswith("[brnrd cut] ? unconfirmed")
    assert "check notices" in err


def test_cut_reports_bounced_with_the_named_diff(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    declaration.write_text("---\n---\nDone.\n", encoding="utf-8")
    notice = {
        "at": "2026-08-08T00:00:00Z", "kind": "refused",
        "text": "cut bounced: evt-…-4e17 undispositioned",
    }
    monkeypatch.setattr(
        time, "sleep", _consume_after_one_sleep(outbox, "do-*-cut-*.md", notice=notice),
    )

    assert main(["cut", str(declaration)]) == 1
    err = capsys.readouterr().err.strip()
    assert err == "[brnrd cut] bounced — refused: cut bounced: evt-…-4e17 undispositioned"


def test_cut_still_queued_when_never_consumed(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    declaration = tmp_path / "bolt.md"
    declaration.write_text("---\n---\nDone.\n", encoding="utf-8")

    ticks = []
    monkeypatch.setattr(time, "sleep", lambda s: ticks.append(s))

    assert main(["cut", str(declaration), "--timeout", "0.2"]) == 1
    err = capsys.readouterr().err.strip()
    # #1379: same age-naming detail as the `do --note` QUEUED case.
    assert err.startswith("[brnrd cut] ? still queued (")
    assert err.endswith("s)")
    assert ticks


def test_cut_reports_the_annotated_count_on_a_forced_accept(
    tmp_path, monkeypatch, capsys,
):
    """A cap-3 forced accept exits 0 — the bolt stands — but the daemon's
    dissent must be visible in the same call, not only in the delivered
    body: the portal's `bolt` facet carries `annotated`, and the porcelain
    reads it back after the drain consumed the file."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(
        outbox,
        bolt={"accepted": True, "annotated": 2, "accepted_at": "2026-08-08T00:00:00Z"},
    )
    declaration = tmp_path / "bolt.md"
    declaration.write_text("---\n---\nAs far as it wove.\n", encoding="utf-8")
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-cut-*.md"))

    assert main(["cut", str(declaration)]) == 0
    out = capsys.readouterr().out
    assert "accepted, annotated — 2 check(s) unresolved" in out
