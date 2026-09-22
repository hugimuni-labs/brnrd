"""``.item`` — read the same way ``.topic`` is (design-the-ask.md §Build cut
step 4, his 2026-09-22 steer: make in-hand derived, never typed).

Mirrors ``tests/test_the_event_carries_its_topic.py``'s own shape for the
sibling control: the control grammar, resolution (a ``w-N`` id, a callsign,
the closed-item guard), and :func:`run_item.settle`'s idempotency + the
event-meta fallback a strand's ``spawn:``-carried ``item:`` rides.
"""

from __future__ import annotations

from pathlib import Path

from brr import items, protocol, run_item
from brr.run import Run


def _warp(tmp_path: Path) -> Path:
    root = tmp_path / "surface" / "warp"
    root.mkdir(parents=True)
    return root


def _item(root: Path, item_id: str, headline: str = "T", *, sign: str | None = None) -> Path:
    text = items.new_item_text(headline, item_type="action", sign=sign)
    path = root / f"{item_id}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _task_with_event(tmp_path: Path, **meta) -> tuple[Run, Path]:
    inbox = tmp_path / "inbox"
    event_path = protocol.create_event(inbox, "telegram", "hello", **meta)
    task = Run.from_event(protocol._read_event(event_path))
    return task, inbox


# ── the control grammar ─────────────────────────────────────────────────


def test_read_control_strips_an_optional_prefix_and_backticks(tmp_path):
    (tmp_path / ".item").write_text("item: `w-1`\n", encoding="utf-8")
    assert run_item.read_control(tmp_path) == "w-1"


def test_read_control_bare_line(tmp_path):
    (tmp_path / ".item").write_text("mira\n", encoding="utf-8")
    assert run_item.read_control(tmp_path) == "mira"


def test_read_control_absent_or_blank_is_none(tmp_path):
    assert run_item.read_control(tmp_path) is None
    (tmp_path / ".item").write_text("\n\n", encoding="utf-8")
    assert run_item.read_control(tmp_path) is None
    assert run_item.read_control(None) is None


def test_the_control_name_is_the_topic_siblings_own_sibling():
    from brr import run_topic

    assert run_item.CONTROL_NAME == ".item"
    assert run_item.CONTROL_NAME != run_topic.CONTROL_NAME


# ── resolution: a w-N id, a callsign, the closed-item guard ─────────────


def test_resolve_a_bare_w_n_id(tmp_path):
    root = _warp(tmp_path)
    _item(root, "w-1")
    assert run_item.resolve(root, "w-1") == "w-1"
    assert run_item.resolve(root, "w-999") is None


def test_resolve_a_callsign(tmp_path):
    root = _warp(tmp_path)
    _item(root, "w-1", sign="mira")
    assert run_item.resolve(root, "mira") == "w-1"
    assert run_item.resolve(root, "MIRA") == "w-1"
    assert run_item.resolve(root, "nope") is None


def test_resolve_refuses_a_closed_item(tmp_path):
    root = _warp(tmp_path)
    path = _item(root, "w-1")
    items.mark_done(path, date="2026-09-22")
    assert run_item.resolve(root, "w-1") is None


def test_resolve_blank_and_no_root(tmp_path):
    root = _warp(tmp_path)
    assert run_item.resolve(root, "") is None
    assert run_item.resolve(None, "w-1") is None


# ── settle: idempotency, refusal, the event-meta fallback ───────────────


def test_settle_folds_the_control_in_and_is_idempotent(tmp_path):
    root = _warp(tmp_path)
    _item(root, "w-1")
    task, _inbox = _task_with_event(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".item").write_text("w-1\n", encoding="utf-8")

    assert run_item.settle(task, outbox_dir=outbox, warp_root=root) == "w-1"
    text = (root / "w-1.md").read_text(encoding="utf-8")
    assert f"attempts: {task.id}" in text and "stage: making" in text

    # idempotent: re-settling the same control line writes nothing more
    before = text
    assert run_item.settle(task, outbox_dir=outbox, warp_root=root) == "w-1"
    assert (root / "w-1.md").read_text(encoding="utf-8") == before


def test_settle_never_moves_stage_backward_past_making(tmp_path):
    root = _warp(tmp_path)
    path = _item(root, "w-1")
    # a later control claim arrives after the item already reached "making"
    # (say, from an earlier run) — settle must not move it back down.
    lines = items._edit_lines(path)
    from brr import asks as asks_mod

    asks_mod._set_ask_row(lines, "stage", "delivered")
    items._write_lines(path, lines)
    task, _inbox = _task_with_event(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".item").write_text("w-1\n", encoding="utf-8")

    run_item.settle(task, outbox_dir=outbox, warp_root=root)
    assert "stage: delivered" in path.read_text(encoding="utf-8")
    assert "stage: making" not in path.read_text(encoding="utf-8")


def test_settle_refuses_an_unresolvable_target_by_notice(tmp_path):
    root = _warp(tmp_path)
    task, _inbox = _task_with_event(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".item").write_text("w-999\n", encoding="utf-8")
    notices: list[tuple[str, str]] = []

    result = run_item.settle(
        task, outbox_dir=outbox, warp_root=root,
        notice=lambda kind, text: notices.append((kind, text)),
    )
    assert result is None
    assert notices and notices[-1][0] == "refused" and "w-999" in notices[-1][1]


def test_settle_falls_back_to_event_meta_when_no_control_file(tmp_path):
    """A strand's `spawn:` directive carries `item:` — the daemon stamps it
    onto the child's own waking event (generic event-meta inheritance,
    `daemon._queue_spawn_request`), and `.item` never gets written as a
    literal file for a strand. `settle` must still fold it in."""
    root = _warp(tmp_path)
    _item(root, "w-1")
    task, _inbox = _task_with_event(tmp_path, item="w-1")
    outbox = tmp_path / "outbox"
    outbox.mkdir()  # no .item file written here

    assert run_item.settle(task, outbox_dir=outbox, warp_root=root) == "w-1"
    assert f"attempts: {task.id}" in (root / "w-1.md").read_text(encoding="utf-8")


def test_settle_a_control_file_overrides_the_event_meta_fallback(tmp_path):
    root = _warp(tmp_path)
    _item(root, "w-1")
    _item(root, "w-2")
    task, _inbox = _task_with_event(tmp_path, item="w-1")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".item").write_text("w-2\n", encoding="utf-8")

    assert run_item.settle(task, outbox_dir=outbox, warp_root=root) == "w-2"


def test_settle_no_control_no_meta_is_none_and_never_raises(tmp_path):
    root = _warp(tmp_path)
    task, _inbox = _task_with_event(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    assert run_item.settle(task, outbox_dir=outbox, warp_root=root) is None
    # a non-dict meta must not raise either
    class _Bare:
        pass

    assert run_item.settle(_Bare(), outbox_dir=outbox, warp_root=root) is None


# ── the strand path: `item:` on `spawn:`, through the real drain ────────


def test_a_spawn_carrying_item_stamps_the_childs_dispatch_event(tmp_path, monkeypatch):
    """Mirrors ``test_outbox_topic.py``'s own
    ``test_a_spawn_carrying_topic_stays_a_spawn_and_records_the_claim`` —
    the same real ``_drain_outbox`` path, for ``item:`` instead of
    ``topic:``. The child's dispatch event carries the raw target
    unconditionally; resolving it against the warp is deferred to
    :func:`run_item.settle` at the child's own first heartbeat, the same
    way an assigned topic is resolved once, not twice."""
    from brr import daemon
    from brr.run import Run

    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "go", status="processing",
                                telegram_user_id="42", telegram_chat_id="42")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    (outbox / "s.md").write_text(
        "---\nspawn: true\nitem: w-1\ntitle: t\n---\ndo the thing\n", encoding="utf-8",
    )
    monkeypatch.setattr(daemon.updates, "emit", lambda _b, pkt: None)
    task = Run(id="run-seat", event_id=own.stem, body="go", source="telegram",
               meta={"repo_label": "hugimuni-labs/brnrd"})
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    daemon._drain_outbox(emit, task, brr_dir / "responses", own.stem, outbox, inbox,
                         repo_root=tmp_path, account_context=None, stats={})
    spawned = [
        protocol.parse_frontmatter(p.read_text(encoding="utf-8")) for p in inbox.glob("*.md")
        if p.stem != own.stem
    ]
    assert [e.get("item") for e in spawned] == ["w-1"]


def test_a_spawn_with_no_item_stamps_nothing(tmp_path, monkeypatch):
    from brr import daemon
    from brr.run import Run

    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "go", status="processing",
                                telegram_user_id="42", telegram_chat_id="42")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    (outbox / "s.md").write_text(
        "---\nspawn: true\ntitle: t\n---\ndo the thing\n", encoding="utf-8",
    )
    monkeypatch.setattr(daemon.updates, "emit", lambda _b, pkt: None)
    task = Run(id="run-seat", event_id=own.stem, body="go", source="telegram",
               meta={"repo_label": "hugimuni-labs/brnrd"})
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    daemon._drain_outbox(emit, task, brr_dir / "responses", own.stem, outbox, inbox,
                         repo_root=tmp_path, account_context=None, stats={})
    spawned = [
        protocol.parse_frontmatter(p.read_text(encoding="utf-8")) for p in inbox.glob("*.md")
        if p.stem != own.stem
    ]
    assert spawned and all("item" not in e for e in spawned)
