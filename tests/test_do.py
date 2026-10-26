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


from brr import emotes
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


# ── --mood ───────────────────────────────────────────────────────────


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


# ── --reply's promise/follow-up contract (evt-…-s0vo, 2026-08-19) ─────


# ── --reply's live-inbox pre-check (brnrd#1698 half 2) ──────────────────


# ── --item: the asks join (design-the-water-line.md §The asks lane) ────


def _repo_with_warp_item(tmp_path, monkeypatch, headline="Ship the digest"):
    """A tmp git repo with an enabled account home and one open item —
    the minimum ``_item_context`` needs to resolve ``w-1`` at all. Mirrors
    ``test_item_cli.py``'s own ``_repo_with_home`` fixture exactly, since
    ``--item``'s existence check runs through the same ``items.resolve_item``
    authority ``brnrd item`` itself is built on."""
    from _helpers import init_git_repo

    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    from brr import account

    account.resolve_context(repo, {}, create=True)
    assert main(["item", "new", headline, "--type", "action"]) == 0
    return repo


def test_do_item_binds_reply_and_writes_asks_jsonl_row(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-1", "--item", "w-1", "--body", "hi", "--no-follow-up",
    ]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "reply evt-1 ✓ · item w-1 ✓"
    rows = [
        json.loads(line)
        for line in (outbox / ".asks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [{"event": "evt-1", "item": "w-1"}]
    # And the say reached the item file itself — the surface the dashboard
    # reads — not only the run's own binding file.
    from brr import account

    ctx = account.resolve_context(tmp_path / "repo", {}, create=False)
    text = (account.work_surface_path(ctx) / "warp" / "w-1.md").read_text(encoding="utf-8")
    assert "says: evt-1" in text
    assert "stage: understood" in text


def test_do_item_accepts_goal_id(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    assert main(["item", "new", "Keep ignition healthy", "--type", "goal"]) == 0
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-1", "--item", "g-1", "--body", "hi", "--no-follow-up",
    ]) == 0
    assert "item g-1 ✓" in capsys.readouterr().out
    rows = [json.loads(line) for line in (outbox / ".asks.jsonl").read_text().splitlines()]
    assert rows == [{"event": "evt-1", "item": "g-1"}]


def test_do_item_unknown_item_is_refused_before_staging(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main([
        "do", "--reply", "evt-1", "--item", "w-999", "--body", "hi", "--no-follow-up",
    ]) == 1
    err = capsys.readouterr().err
    assert "w-999" in err
    assert "Nothing was staged" in err
    assert not list(outbox.glob("do-*.md"))
    assert not (outbox / ".asks.jsonl").exists()


def test_do_item_repeated_binds_multiple_items_to_one_reply(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(["item", "new", "A second ask", "--type", "action"]) == 0
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-1", "--item", "w-1", "--item", "w-2",
        "--body", "answers both", "--no-follow-up",
    ]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "reply evt-1 ✓ · item w-1 ✓ · item w-2 ✓"
    rows = [
        json.loads(line)
        for line in (outbox / ".asks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {"event": "evt-1", "item": "w-1"}, {"event": "evt-1", "item": "w-2"},
    ]


def test_do_item_with_no_reply_is_refused(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main(["do", "--note", "evt-1", "--item", "w-1"]) == 1
    err = capsys.readouterr().err
    assert "no preceding --reply" in err
    assert "Nothing was staged" in err
    assert not list(outbox.glob("do-*.md"))


def test_do_item_after_gate_is_refused(tmp_path, monkeypatch, capsys):
    """A gate send has no waiting event to bind — ``--item`` pairs only
    with ``--reply``. A ``--body-file`` follows so this exercises the
    item-after-gate guard specifically, not the separate (and otherwise
    indistinguishable from stderr alone) "gate has no body yet" pairing
    error: with the guard gone the call would stage cleanly instead of
    refusing, which the two assertions below both catch."""
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    body_file = tmp_path / "gate-body.md"
    body_file.write_text("hi", encoding="utf-8")

    assert main([
        "do", "--gate", "telegram", "--item", "w-1", "--body-file", str(body_file),
    ]) == 1
    err = capsys.readouterr().err
    assert "--gate" in err
    assert "not --reply" in err
    assert "Nothing was staged" in err
    assert not list(outbox.glob("do-*.md"))


def test_do_item_writes_no_row_when_reply_drain_fails(tmp_path, monkeypatch, capsys):
    """The row is written only when the reply's own drain verdict comes back
    ✓ — an ask binding for a reply that never landed is the same lie
    ``--promise`` already refuses to leave (docs/portals.md)."""
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    notice = {
        "at": "2026-08-19T00:00:00Z", "kind": "refused",
        "text": "reply refused: event evt-1 not pending",
    }
    monkeypatch.setattr(
        time, "sleep",
        _consume_after_one_sleep(outbox, "do-*-reply-*.md", notice=notice),
    )

    assert main([
        "do", "--reply", "evt-1", "--item", "w-1", "--body", "hi", "--no-follow-up",
    ]) == 1
    out = capsys.readouterr().out.strip()
    assert "reply evt-1 ✗" in out
    assert "item w-1" not in out
    assert not (outbox / ".asks.jsonl").exists()


# ── --part: rung 3's inbound stamp (design-the-water-line.md §The asks lane) ──


def test_do_part_binds_an_excerpt_to_the_item(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-1", "--item", "w-1", "--part", "the fuel gauge bit",
        "--body", "hi", "--no-follow-up",
    ]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "reply evt-1 ✓ · item w-1 part×1 ✓"
    rows = [
        json.loads(line)
        for line in (outbox / ".asks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [{"event": "evt-1", "item": "w-1", "part": "the fuel gauge bit"}]


def test_do_part_repeatable_per_item_writes_one_row_each(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-1", "--item", "w-1", "--part", "first bit", "--part", "second bit",
        "--body", "hi", "--no-follow-up",
    ]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "reply evt-1 ✓ · item w-1 part×2 ✓"
    rows = [
        json.loads(line)
        for line in (outbox / ".asks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {"event": "evt-1", "item": "w-1", "part": "first bit"},
        {"event": "evt-1", "item": "w-1", "part": "second bit"},
    ]


def test_do_part_mixed_with_a_plain_item_writes_a_mixed_batch(tmp_path, monkeypatch, capsys):
    """One item carries a `--part`, a sibling `--item` on the same reply
    carries none — the plain one keeps the old two-field row."""
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(["item", "new", "A second ask", "--type", "action"]) == 0
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-1", "--item", "w-1", "--part", "quoted bit",
        "--item", "w-2", "--body", "answers both", "--no-follow-up",
    ]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "reply evt-1 ✓ · item w-1 part×1 ✓ · item w-2 ✓"
    rows = [
        json.loads(line)
        for line in (outbox / ".asks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {"event": "evt-1", "item": "w-1", "part": "quoted bit"},
        {"event": "evt-1", "item": "w-2"},
    ]


def test_do_part_with_no_preceding_item_is_refused(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main([
        "do", "--reply", "evt-1", "--part", "x", "--body", "hi", "--no-follow-up",
    ]) == 1
    err = capsys.readouterr().err
    assert "no preceding --item" in err
    assert "Nothing was staged" in err
    assert not list(outbox.glob("do-*.md"))


def test_do_part_after_gate_is_refused(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    body_file = tmp_path / "gate-body.md"
    body_file.write_text("hi", encoding="utf-8")

    assert main([
        "do", "--gate", "telegram", "--part", "x", "--body-file", str(body_file),
    ]) == 1
    err = capsys.readouterr().err
    assert "--gate" in err
    assert "not --reply" in err
    assert "Nothing was staged" in err
    assert not list(outbox.glob("do-*.md"))


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


# ── multiple verbs, ordering, and pairing errors ────────────────────


# ── --card ───────────────────────────────────────────────────────────


# ── passthrough: `-- <command> [args...]` ───────────────────────────


# ── brnrd cut (design-the-bolt.md) — the porcelain over cut: ────────


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


# ── The ask at the reply (his 2026-09-23 steer): every correspondent event
# yields ≥ 1 ask — a say on an existing item or a new one, never a default ──


def test_do_new_item_mints_after_the_reply_is_accepted_and_binds_it(tmp_path, monkeypatch, capsys):
    repo = _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-9", "--new-item", "The reset is an advisory, measured",
        "--body", "heard", "--no-follow-up",
    ]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "reply evt-9 ✓ · item w-2 minted ✓"
    rows = [
        json.loads(line)
        for line in (outbox / ".asks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [{"event": "evt-9", "item": "w-2"}]
    from brr import account, items as items_mod

    ctx = account.resolve_context(repo, {}, create=False)
    warp_root = account.work_surface_path(ctx) / "warp"
    text = (warp_root / "w-2.md").read_text(encoding="utf-8")
    assert text.startswith("# The reset is an advisory, measured")
    assert "type: action" in text
    assert "refs: evt-9" in text
    assert "says: evt-9" in text
    # Born understood: the acknowledgment is the second stage, so the
    # console's buckets never file a just-minted ask as unaddressed.
    assert "stage: understood" in text
    assert items_mod.resolve_item(warp_root, "w-2") is not None


def test_do_new_item_is_not_minted_when_the_reply_is_refused(tmp_path, monkeypatch, capsys):
    repo = _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(
        time, "sleep",
        _consume_after_one_sleep(
            outbox, "do-*-reply-*.md",
            notice={"at": "2026-09-23T00:00:00Z", "kind": "refused",
                    "text": "reply refused: event evt-9 not pending"},
        ),
    )

    assert main([
        "do", "--reply", "evt-9", "--new-item", "Never minted", "--body", "x", "--no-follow-up",
    ]) == 1
    from brr import account

    ctx = account.resolve_context(repo, {}, create=False)
    warp_root = account.work_surface_path(ctx) / "warp"
    assert not (warp_root / "w-2.md").exists()
    assert not (outbox / ".asks.jsonl").exists()


def test_do_reply_with_a_warp_and_no_ask_disposition_is_refused_before_staging(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)

    assert main(["do", "--reply", "evt-9", "--body", "hi", "--no-follow-up"]) == 1
    err = capsys.readouterr().err
    assert "--reply evt-9 carries no ask" in err
    assert "--new-item" in err and "--no-ask" in err and "Never a default" in err
    assert not list(outbox.glob("do-*")), "nothing staged"


def test_do_no_ask_records_the_deliberate_zero(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-9", "--body", "thanks", "--no-follow-up",
        "--no-ask", "a thank-you, no ask in it",
    ]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "reply evt-9 ✓ · no ask ✓"
    rows = [
        json.loads(line)
        for line in (outbox / ".asks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [{"event": "evt-9", "no_ask": "a thank-you, no ask in it"}]
    # The zero never renders as a say.
    from brr import do as do_mod

    assert do_mod.read_asks(outbox) == []


def test_do_several_asks_in_one_event_mint_several_items(tmp_path, monkeypatch, capsys):
    _repo_with_warp_item(tmp_path, monkeypatch)
    capsys.readouterr()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _do_env(monkeypatch, outbox)
    _portal_state(outbox)
    monkeypatch.setattr(time, "sleep", _consume_after_one_sleep(outbox, "do-*-reply-*.md"))

    assert main([
        "do", "--reply", "evt-9", "--item", "w-1", "--new-item", "Ask two", "--new-item", "Ask three",
        "--body", "heard all three", "--no-follow-up",
    ]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "reply evt-9 ✓ · item w-1 ✓ · item w-2 minted ✓ · item w-3 minted ✓"


# ── owned PRs · checks (the verdict that reaches the screen) ────────


