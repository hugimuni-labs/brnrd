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


# ── --mood ───────────────────────────────────────────────────────────


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


def _consume_after_one_sleep(outbox, glob, *, notice=None):
    """A ``time.sleep`` replacement: on first call, retire the one staged
    file matching *glob* (mirroring ``_retire_outbox_staging`` — the file
    just needs to stop existing at the path this module wrote), optionally
    dropping a matching notice into ``portal-state.json`` first."""

    def _sleep(_seconds):
        matches = list(outbox.glob(glob))
        if not matches:
            return
        if notice is not None:
            payload = json.loads((outbox / "portal-state.json").read_text(encoding="utf-8"))
            payload.setdefault("notices", []).append(notice)
            (outbox / "portal-state.json").write_text(json.dumps(payload), encoding="utf-8")
        matches[0].unlink()

    return _sleep


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


def test_do_note_still_queued_when_never_consumed(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    ticks = []
    monkeypatch.setattr(time, "sleep", lambda s: ticks.append(s))

    assert main(["do", "--note", "evt-1", "--timeout", "0.2"]) == 1
    assert capsys.readouterr().out.strip() == "note evt-1 ? still queued"
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
