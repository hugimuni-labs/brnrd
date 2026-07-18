from __future__ import annotations

from pathlib import Path

from brr import account, daemon, message_store, protocol
from brr.gates import runtime
from brr.run import Run


def _context(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    ctx = account.resolve_context(
        repo,
        {"home.path": str(home), "repo.label": "Gurio/brr"},
    )
    return repo, home, ctx


def _event(inbox: Path, *, status: str = "processing") -> dict:
    path = protocol.create_event(inbox, "telegram", "task")
    event = protocol._read_event(path)
    protocol.set_status(event, status)
    return event


def test_delivery_transition_stamps_platform_receipt(tmp_path):
    _repo, _home, ctx = _context(tmp_path)
    path = message_store.stage(
        ctx,
        repo_label="Gurio/brr",
        run_id="run-1",
        body="answer",
        kind="terminal",
        target_event="evt-1",
    )

    assert message_store.read(path)["status"] == "pending"
    assert message_store.transition(
        path,
        message_store.DELIVERED,
        gate="telegram",
        platform_message_id=42,
        delivered_at="2026-07-18T12:00:00+00:00",
    )

    delivered = message_store.read(path)
    assert delivered["status"] == "delivered"
    assert delivered["platform_gate"] == "telegram"
    assert delivered["platform_message_id"] == 42
    assert delivered["delivered_at"] == "2026-07-18T12:00:00+00:00"
    assert delivered["body"] == "answer"


def test_gate_retry_uses_message_status_and_does_not_double_post(tmp_path):
    _repo, _home, ctx = _context(tmp_path)
    inbox, responses = tmp_path / "inbox", tmp_path / "responses"
    event = _event(inbox)
    message = message_store.stage(
        ctx,
        repo_label="Gurio/brr",
        run_id="run-1",
        body="interim",
        kind="interim",
        target_event=event["id"],
    )
    protocol.write_partial(
        responses, event["id"], "interim", message_path=message,
    )
    sent: list[str] = []

    def deliver(_event, body):
        sent.append(body)
        return {"message_id": "platform-7"}

    runtime.deliver_stream(inbox, responses, "telegram", deliver)
    runtime.deliver_stream(inbox, responses, "telegram", deliver)

    assert sent == ["interim"]
    stored = message_store.read(message)
    assert stored["status"] == "delivered"
    assert stored["platform_message_id"] == "platform-7"


def test_unknown_event_reply_becomes_undeliverable(tmp_path, monkeypatch):
    repo, _home, ctx = _context(tmp_path)
    brr_dir = repo / ".brr"
    responses, inbox = brr_dir / "responses", brr_dir / "inbox"
    inbox.mkdir(parents=True)
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    source = outbox / "reply.md"
    source.write_text("---\nevent: evt-orphan\n---\n\nanswer", encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda *_args: None)
    task = Run(
        id="run-owner",
        event_id="evt-current",
        body="task",
        meta={"repo_label": "Gurio/brr"},
    )

    count = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "", "evt-current"),
        task,
        responses,
        "evt-current",
        outbox,
        inbox,
        repo_root=repo,
        account_context=ctx,
    )

    assert count == 0
    messages = message_store.list_messages(
        message_store.run_messages_dir(ctx, "Gurio/brr", task.id),
    )
    assert len(messages) == 1
    assert messages[0]["status"] == "undeliverable"
    assert messages[0]["target_event"] == "evt-orphan"
    assert "no live gate owner" in messages[0]["reason"]
    assert not source.exists()
    assert (outbox / ".processed" / "reply.md").exists()


def test_legacy_migration_is_idempotent(tmp_path):
    repo, home, ctx = _context(tmp_path)
    brr_dir = repo / ".brr"
    run = Run(id="run-old", event_id="evt-old", body="old task")
    run.save(brr_dir / "runs")
    partial = brr_dir / "responses" / "evt-old.partials" / "000001.md"
    partial.parent.mkdir(parents=True)
    partial.write_text("orphaned interim", encoding="utf-8")
    archived = home / "knowledge" / "replies" / "Gurio__brr" / "run-archive.md"
    archived.parent.mkdir(parents=True)
    archived.write_text(
        "---\nrun: run-archive\nevent: evt-archive\nsource: telegram\n"
        "delivered_at: 2026-07-10T10:00:00+00:00\n---\n\nold answer\n",
        encoding="utf-8",
    )

    first = message_store.migrate_legacy(
        ctx,
        repo_root=repo,
        repo_label="Gurio/brr",
        brr_dir=brr_dir,
    )
    second = message_store.migrate_legacy(
        ctx,
        repo_root=repo,
        repo_label="Gurio/brr",
        brr_dir=brr_dir,
    )

    assert first == {"partials": 1, "replies": 1}
    assert second == {"partials": 0, "replies": 0}
    orphan = message_store.list_messages(
        message_store.run_messages_dir(ctx, "Gurio/brr", "run-old"),
    )[0]
    archived_message = message_store.list_messages(
        message_store.run_messages_dir(ctx, "Gurio/brr", "run-archive"),
    )[0]
    assert orphan["status"] == "undeliverable"
    assert archived_message["status"] == "delivered"
    assert archived_message["platform_gate"] == "telegram"


# ── stage() idempotency via source_ref ───────────────────────────────


def test_stage_idempotent_on_same_source_ref(tmp_path):
    """Staging the same source_ref twice returns the existing path, no duplicate file."""
    _repo, _home, ctx = _context(tmp_path)
    ref = "/outbox/evt-x/001-reply.md"
    p1 = message_store.stage(
        ctx, repo_label="Gurio/brr", run_id="run-idem",
        body="content", kind="interim", source_ref=ref,
    )
    p2 = message_store.stage(
        ctx, repo_label="Gurio/brr", run_id="run-idem",
        body="content", kind="interim", source_ref=ref,
    )
    assert p1 == p2
    msgs = message_store.list_messages(message_store.run_messages_dir(ctx, "Gurio/brr", "run-idem"))
    assert len(msgs) == 1


def test_stage_monotone_sequence_numbers(tmp_path):
    """Each stage call produces a file with a strictly higher sequence than the last."""
    _repo, _home, ctx = _context(tmp_path)
    paths = [
        message_store.stage(
            ctx, repo_label="Gurio/brr", run_id="run-seq",
            body=f"msg {i}", kind="interim",
        )
        for i in range(3)
    ]
    seqs = [int(p.name.split("-", 1)[0]) for p in paths]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 3


# ── receipt_id() extracts platform ids ───────────────────────────────


def test_receipt_id_plain_int():
    assert message_store.receipt_id(42) == "42"


def test_receipt_id_telegram_result_dict():
    """Telegram returns {ok: true, result: {message_id: N, ...}}."""
    result = {"ok": True, "result": {"message_id": 99, "chat": {"id": 1}}}
    assert message_store.receipt_id(result) == "99"


def test_receipt_id_slack_ts():
    """Slack returns {ok: true, ts: '...', channel: '...'}."""
    result = {"ok": True, "ts": "1234567890.123456", "channel": "C0123"}
    assert message_store.receipt_id(result) == "1234567890.123456"


def test_receipt_id_github_html_url():
    result = {"html_url": "https://github.com/Gurio/brr/issues/42#issuecomment-1"}
    assert message_store.receipt_id(result) == "https://github.com/Gurio/brr/issues/42#issuecomment-1"


def test_receipt_id_none_returns_empty():
    assert message_store.receipt_id(None) == ""


# ── transition() guards ───────────────────────────────────────────────


def test_transition_rejects_non_terminal_status(tmp_path):
    _repo, _home, ctx = _context(tmp_path)
    path = message_store.stage(
        ctx, repo_label="Gurio/brr", run_id="run-bad", body="x", kind="t",
    )
    import pytest
    with pytest.raises(ValueError, match="invalid message terminal status"):
        message_store.transition(path, "staged")


def test_transition_noop_on_already_delivered(tmp_path):
    """A delivered message silently returns True; a different terminal returns False."""
    _repo, _home, ctx = _context(tmp_path)
    path = message_store.stage(
        ctx, repo_label="Gurio/brr", run_id="run-noop", body="x", kind="t",
    )
    message_store.transition(path, message_store.DELIVERED, gate="g1")
    # Same terminal: idempotent True.
    assert message_store.transition(path, message_store.DELIVERED, gate="g2") is True
    # Different terminal: False, original preserved.
    assert message_store.transition(path, message_store.UNDELIVERABLE, reason="oops") is False
    assert message_store.read(path)["platform_gate"] == "g1"


# ── corpus layer path shape ───────────────────────────────────────────


def test_run_messages_dir_slug_and_path_shape(tmp_path):
    _repo, home, ctx = _context(tmp_path)
    d = message_store.run_messages_dir(ctx, "Gurio/brr", "run-260718-test-x")
    assert d == Path(home) / "runs" / "Gurio__brr" / "run-260718-test-x" / "messages"
