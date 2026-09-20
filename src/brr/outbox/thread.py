"""Address a known conversation without reopening its inbound event."""

from __future__ import annotations

from .. import conversations, daemon, protocol, trust
from .shapes import Handled, OutboxFile


def handle(f: OutboxFile) -> Handled:
    from .verbs import _handled

    ctx = f.ctx
    key = str(f.frontmatter.get("thread") or "").strip()

    def refuse(reason: str) -> Handled:
        daemon._record_outbox_notice(
            ctx.outbox_dir, f"thread refused: {reason} ({key!r})",
            kind="refused", lifetime="run",
        )
        daemon._retire_outbox_staging(f.path)
        return _handled(f, "thread", 0)

    records = conversations.read_records(ctx.emit.brr_dir, key) if key else []
    if not records:
        return refuse("unknown conversation")
    last = next((r for r in reversed(records) if r.get("kind") == "event"), None)
    if last is None:
        return refuse("correspondent is not an account user")
    # Read the exact recorded inbound, even if closed. Never fall back to an
    # older owner event when the latest sender or its authorization is gone.
    event = None
    target_inbox = target_responses = None
    event_id = str(last.get("event_id") or "")
    if event_id and "/" not in event_id and "\\" not in event_id:
        for inbox, responses in ctx.address_sources:
            candidate = protocol._read_event(inbox / f"{event_id}.md")
            if candidate and conversations.conversation_key_for_event(candidate) == key:
                event, target_inbox, target_responses = candidate, inbox, responses
                break
    if event is None or trust.resolve_tier(event) != trust.OWNER:
        return refuse("correspondent is not an account user")
    gate = str(event.get("source") or "")
    if not daemon._gate_can_deliver(ctx.emit.brr_dir, gate):
        return refuse(f"gate {gate!r} is not deliverable on this account")
    if conversations.gate_thread_key(event) != key or (
        gate == "cloud" and not event.get("cloud_event_id")
    ):
        return refuse("conversation has no usable gate address")
    if not f.body:
        return refuse("message has no body")

    # Gate addresses come only from the daemon's inbound record, never from
    # supplied chat IDs. Cloud's event ID also routes closed-event continuations.
    address = {k: v for k, v in event.items() if k.startswith(gate + "_")}
    address["conversation_key"] = key
    message_path, blocked = daemon._stage_outbound(
        f.run, ctx.account_context, body=f.body, kind="outbound",
        target_gate=gate, target_thread=key, source_ref=str(f.path),
        outbox_dir=ctx.outbox_dir,
    )
    promoted = 0
    if not blocked and daemon._deliver_out_of_bound(
        daemon._WorkerEmit(ctx.emit.brr_dir, key, ctx.event_id), f.run,
        target_responses, target_inbox, ctx.event_id, gate, address, f.body,
        ctx.outbox_dir, message_path=message_path, account_context=ctx.account_context,
    ):
        promoted = 1
        daemon._project_said(f.run, ctx.outbox_dir, f"thread:{key}", f.body)
        if ctx.stats is not None:
            ctx.stats["outbound"] = ctx.stats.get("outbound", 0) + 1
            ctx.stats["delivered"] = ctx.stats.get("delivered", 0) + 1
    daemon._retire_outbox_staging(f.path)
    return _handled(f, "thread", promoted)
