"""`stake:` / `cut-at:` in the outbox — the seat's side of the instrument (move 4b).

A stake is the user's word, and it arrives where the user speaks: the waking
event's frontmatter, or a message's lead ``stake:`` line
(``daemon._stake_facet`` arms both at a boundary; ``daemon._raise_stake_at_cut``
reads a raise while the seat is parked). On ``spawn:`` and ``respawn:`` it is
those rows' modifier — they outrank this one — so a file this row claims is
the seat speaking about a stake on its own behalf, and the seat has exactly
one thing to say (design-the-loom §16: *raise · refuse · leave*, and only the
user raises):

    ---
    stake: refuse
    event: <id>          # optional — the waking event when absent
    ---
    one line: why

**Refuse** parks the ask: the event gains ``stake_refused: <why>`` and stays
where it is — pending if it was pending, never dropped, never marked handled.
If the refused stake is the one armed on this run it stops metering
(``state: refused``) and a cut it had stamped for this turn's end is lifted.
What the seat tells the user is its own reply; this verb writes no message.

Refused here: an amount (``stake: 5%`` — the seat does not stake itself; a
raise it wrote would move the user's hard stop), ``cut-at:`` without a stake,
a strand (its stake is its allowance), no line of why, an event that carries
no stake.
"""

from __future__ import annotations

from .. import daemon
from .. import protocol
from .. import resource_hold
from .. import stake as stake_mod
from .shapes import Handled, OutboxFile


def _refuse(f: OutboxFile, verb: str, text: str, *, kind: str = "refused") -> Handled:
    from .verbs import _handled

    daemon._record_outbox_notice(f.ctx.outbox_dir, text, kind=kind, lifetime="run")
    daemon._retire_outbox_staging(f.path)
    return _handled(f, verb, 0)


def handle_stake(f: OutboxFile) -> Handled:
    from .verbs import _handled

    task = f.run
    meta = getattr(task, "meta", None)
    meta = meta if isinstance(meta, dict) else {}
    raw = " ".join(str(f.frontmatter.get("stake") or "").split())
    if raw.lower() != stake_mod.REFUSE:
        return _refuse(
            f, "stake",
            f"stake refused: `stake: {raw}` — a stake is the user's word and arrives "
            "on their message; the seat answers one with `stake: refuse` + a line, "
            "and hands one to a thread on `spawn:`/`respawn:`",
        )
    if daemon._is_strand(meta):
        return _refuse(
            f, "stake",
            "stake refused: a strand's stake is its allowance — `ask: allowance "
            "+<tokens>` or park; nothing was refused",
        )
    why = " ".join(f.body.split())
    if not why:
        return _refuse(
            f, "stake",
            "stake refused: `stake: refuse` needs one line of why in the body; "
            "nothing was refused",
        )
    target = str(f.frontmatter.get("event") or "").strip() or str(f.ctx.event_id or "")
    event, _responses, ambiguous = daemon._resolve_event_target(
        f.ctx.address_sources or [], target,
    )
    if event is None:
        detail = (
            "ambiguous — " + ", ".join(str(e.get("id")) for e in ambiguous)
            if ambiguous else "no pending event by that id"
        )
        return _refuse(f, "stake", f"stake refused: {target} — {detail}", kind="dropped")
    if stake_mod.request_from(event, str(event.get("body") or "")) is None:
        return _refuse(
            f, "stake", f"stake refused: {event.get('id')} carries no stake to refuse",
        )
    event_id = str(event.get("id") or "")
    try:
        protocol.update_event_meta(event, stake_refused=why)
    except (OSError, ValueError) as exc:
        return _refuse(
            f, "stake", f"stake dropped: {event_id} — could not record the refusal: {exc}",
            kind="dropped",
        )
    armed = meta.get("stake") if isinstance(meta.get("stake"), dict) else None
    lifted = ""
    if armed is not None and str(armed.get("event_id") or "") == event_id:
        armed["state"] = "refused"
        armed["refused"] = why
        pending = meta.get("pending_resource_hold")
        if isinstance(pending, dict) and pending.get("reason") == resource_hold.REASON_STAKE_CUT:
            meta.pop("pending_resource_hold", None)
            lifted = " · the cut it had stamped is lifted"
    request = meta.get("stake_request")
    if isinstance(request, dict) and request.get("event_id") == event_id:
        # Refused before the first boundary armed it.
        meta.pop("stake_request", None)
    seen = [str(x) for x in (meta.get("stake_seen_events") or [])]
    if event_id not in seen:
        seen.append(event_id)
        meta["stake_seen_events"] = seen[-64:]
    daemon._record_outbox_notice(
        f.ctx.outbox_dir,
        f"stake refused by the seat: {event_id} — {why}; the ask stays as it was "
        f"(`stake_refused` on the event){lifted}",
        kind="advisory", lifetime="run",
    )
    f.ctx.emit(
        "stake_refused", run_id=getattr(task, "id", ""), event_id=f.ctx.event_id,
        target_event=event_id, why=why,
    )
    stats = f.ctx.stats
    if stats is not None:
        stats["stake"] = stats.get("stake", 0) + 1
    daemon._retire_outbox_staging(f.path)
    return _handled(f, "stake", 1)


def handle_cut_at(f: OutboxFile) -> Handled:
    raw = " ".join(str(f.frontmatter.get("cut-at") or "").split())
    return _refuse(
        f, "cut-at",
        f"cut-at refused: `cut-at: {raw}` rides a stake — on the user's request, "
        "or beside `stake:` on `spawn:`/`respawn:`; nothing was armed",
    )
