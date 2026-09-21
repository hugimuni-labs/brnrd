"""One handler per outbox frontmatter key.

Every handler takes one :class:`~brr.outbox.shapes.OutboxFile` and returns
one :class:`~brr.outbox.shapes.Handled`. The handlers for the keys ``main``
already routed are ``daemon._drain_outbox``'s branch bodies, **moved**: an
assembler lifted exact line ranges (named in each docstring), dedented them,
and rewrote only two things, both mechanically —

- a ``continue`` that targeted the drain's dispatch loop became
  ``return _handled(f, <verb>, promoted)`` (a ``continue`` inside a loop of
  the branch's own, such as ``also:``'s, is untouched);
- a name defined in ``daemon.py`` became ``daemon.<name>``, looked up at call
  time exactly as the old global lookup was, so every
  ``monkeypatch.setattr(daemon, "<helper>", …)`` still lands. Names ``daemon``
  only imported are imported here directly.

The verbs move 4 adds (``land``, ``fold``) and move 4b's (``mark``,
``stake``, ``cut-at``) are at the bottom; their machinery lives in
``land.py``, ``fold.py``, ``mark.py`` and ``stake.py``.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from .. import account
from .. import await_verb
from .. import config as conf
from .. import conversations
from .. import cut_verb
from .. import halt_verb
from .. import daemon
from .. import hold_verb
from .. import hooks as hooks_mod
from .. import message_store
from .. import protocol
from .. import resource_hold
from .. import shuttle
from . import notices
from .shapes import Handled, OutboxFile, Produce


def _handled(
    f: OutboxFile,
    verb: str,
    promoted: int,
    *,
    then: OutboxFile | None = None,
    produce: tuple[Produce, ...] = (),
) -> Handled:
    """The result, read off what the handler actually did.

    ``outcome`` is derived, never declared by the handler: a promotion is
    ``accepted``; no promotion with a refused/dropped notice written for this
    file (``notices.attributed``) is ``refused``; anything else ``deferred``.
    """
    current = notices._current.get()
    written = list(current.written) if current is not None else []
    counted = [text for kind, text in written if kind in notices.COUNTED_AGAINST]
    if promoted:
        outcome = "accepted"
    elif counted:
        outcome = "refused"
    else:
        outcome = "deferred"
    last = counted[-1] if counted else (written[-1][1] if written else None)
    return Handled(
        verb=verb, outcome=outcome, notice=last, produce=produce,
        promoted=promoted, then=then,
    )


def handle_runner_policy(f: OutboxFile) -> Handled:
    """`runner_policy:` — moved from ``daemon._drain_outbox`` on main (lines 8615–8629)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    outbox_dir = f.ctx.outbox_dir
    responses_dir = f.ctx.responses_dir
    stats = f.ctx.stats
    promoted = 0
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        if daemon._queue_runner_policy_proposal(
            emit,
            task,
            responses_dir,
            event_id,
            fm,
            body,
            account_context=account_context,
        ):
            promoted += 1
            if stats is not None:
                stats["current"] = stats.get("current", 0) + 1
                stats["runner_policy"] = stats.get("runner_policy", 0) + 1
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'runner_policy', promoted)


def handle_config_change(f: OutboxFile) -> Handled:
    """`config_change:` — moved from ``daemon._drain_outbox`` on main (lines 8632–8647)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    outbox_dir = f.ctx.outbox_dir
    repo_root = f.ctx.repo_root
    responses_dir = f.ctx.responses_dir
    stats = f.ctx.stats
    promoted = 0
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        if daemon._queue_config_change_proposal(
            emit,
            task,
            repo_root,
            responses_dir,
            event_id,
            fm,
            body,
            account_context=account_context,
        ):
            promoted += 1
            if stats is not None:
                stats["current"] = stats.get("current", 0) + 1
                stats["config_change"] = stats.get("config_change", 0) + 1
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'config_change', promoted)


#: Keys the halt file carries for its own sake. Popped before the
#: announcement falls through to the delivery rows, so what reaches
#: ``gate``/``event`` is a plain reply and nothing downstream has to know
#: this verb exists.
_HALT_KEYS = ("halt", "reason", "carry", "resumable", "shell", "core")


def handle_halt(f: OutboxFile) -> Handled:
    """`halt:` — the seat ends (design-the-four-stops.md §The two verbs).

    The only outbox verb that ends a seat rather than parking it, and the
    reason it exists: every exit was closed, each for a good reason, and
    together they built a seat that **cannot leave** — invisible, and
    measured at 778,000 tokens re-read per boundary for a day and a half.

    Shaped like ``handle_cut``, deliberately, because the precaution is the
    same one: a declaration the resident writes, diffed against facts the
    daemon already attests, bounced once with the diff named. What differs
    is what it declares — not *what this stretch produced* but *why this
    body ends, and what happens to the work*.

    Also like ``cut:``, it **falls through**: the accepted halt pops its own
    keys and hands the announcement to the delivery rows after it, so *"it
    announces itself with its reason as a message"* rides the reply lane
    that already works rather than a second one grown here.
    """
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    repo_root = f.ctx.repo_root
    stats = f.ctx.stats
    promoted = 0
    halt_guard = daemon._OutboxEntryGuard(outbox_dir, fpath)
    with halt_guard:
        declaration, parse_error = halt_verb.parse_halt(fm)
        if parse_error:
            daemon._record_outbox_notice(
                outbox_dir, f"halt dropped: {parse_error}",
                kind="dropped", lifetime="run", source_file=fpath.name,
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'halt', promoted)
        if daemon._is_strand(task.meta):
            # A strand is a thought; the seat is a life. #1991's rule, at
            # the one verb where confusing the two would end somebody
            # else's run: a strand ends by finishing, and its produce
            # reaches its parent through `submit:`, not by halting a seat
            # it does not own.
            daemon._record_outbox_notice(
                outbox_dir,
                "halt refused: a strand is a thought, not the seat — it ends "
                "by finishing. `submit: true` attests your branch and report "
                "to your parent; the parent's `stop:` releases you.",
                kind="refused", lifetime="run", source_file=fpath.name,
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'halt', promoted)
        pending_events = (
            daemon._pending_events_for_agent(
                inbox_dir, event_id,
                strand=False,
                account_context=account_context,
                repo_label=task.meta.get("repo_label"),
                observer_run_id=task.id,
            )
            if inbox_dir is not None else []
        )
        open_items = daemon._halt_open_items(
            task,
            pending_events=pending_events,
            repo_root=repo_root,
            outbox_dir=outbox_dir,
        )
        unnamed = daemon._halt_bounce_lines(declaration, open_items)
        if unnamed:
            bounces = int(task.meta.get("halt_bounces") or 0)
            if bounces + 1 < daemon._HALT_BOUNCE_CAP:
                task.meta["halt_bounces"] = bounces + 1
                daemon._record_outbox_notice(
                    outbox_dir,
                    daemon._halt_bounce_notice(declaration, unnamed),
                    kind="refused", lifetime="run", source_file=fpath.name,
                )
                daemon._retire_outbox_staging(fpath)
                return _handled(f, 'halt', promoted)
            # The cap is spent. Accept — a verb that could be blocked
            # forever rebuilds the seat that cannot leave with a guard's
            # face on it — and carry what it went ahead over, permanently,
            # as the record's own dissent.
            task.meta["halt_bounces"] = bounces + 1
        task.meta["pending_halt"] = daemon._halt_spec(
            task, declaration, open_items, dissent=unnamed,
        )
        # A halt outranks any park staged this same turn, including the
        # bolt's own park-on-live-strands: the seat said it is ending, and
        # a park would silently convert that into staying.
        if task.meta.pop("pending_resource_hold", None) is not None:
            daemon._record_outbox_notice(
                outbox_dir,
                "halt: a park staged this turn was dropped — a seat that "
                "halts does not park instead",
                kind="advisory", lifetime="run", source_file=fpath.name,
            )
        promoted += 1
        if stats is not None:
            stats["halt"] = stats.get("halt", 0) + 1
        emit(
            "halt_accepted",
            run_id=task.id,
            event_id=event_id,
            kind=declaration.kind,
            reason=declaration.reason,
            unnamed=len(unnamed),
        )
        for key in _HALT_KEYS:
            fm.pop(key, None)
        if not body.strip():
            body = daemon._halt_body(
                daemon._halt_spec(
                    task, declaration, open_items, dissent=unnamed,
                )["declaration"]
            )
        fm.pop("event", None)
        fm.pop("gate", None)
        halt_source = str(getattr(task, "source", "") or "")
        if halt_source and not daemon._gate_owns_source(halt_source):
            # Same fallback `cut:` uses, same reason: a gate-less wake has
            # no reply lane of its own, and this is the one message that
            # must not sit readable only on a run node — it says the seat
            # is gone.
            try:
                halt_cfg = conf.load_config(repo_root or emit.brr_dir.parent)
                notify_gate = daemon._cached_notify_gate(
                    task, halt_cfg, emit.brr_dir,
                    conversation_key=str(getattr(task, "conversation_key", "") or ""),
                )
            except Exception:  # noqa: BLE001 - never lose the delivery we have
                notify_gate = ""
            if notify_gate:
                fm["gate"] = notify_gate
    if halt_guard.tripped:
        return _handled(f, 'halt', promoted)
    return _handled(f, 'halt', promoted, then=f.rewritten(fm, body))


def handle_respawn(f: OutboxFile) -> Handled:
    """`respawn:` — moved from ``daemon._drain_outbox`` on main (lines 8650–8670)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    repo_root = f.ctx.repo_root
    stats = f.ctx.stats
    promoted = 0
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        dispatched = daemon._queue_respawn_request(
            emit, task, repo_root, inbox_dir, event_id, fm, body, outbox_dir,
        )
        if dispatched:
            promoted += 1
            message_path, _blocked = daemon._stage_outbound(
                task, account_context,
                body=body or task.body,
                kind="dispatch",
                target_gate="respawn",
                source_ref=str(fpath),
            )
            if message_path:
                message_store.transition(
                    message_path, message_store.DELIVERED,
                    gate="dispatch", platform_message_id="respawn-event",
                )
            if stats is not None:
                stats["respawn"] = stats.get("respawn", 0) + 1
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'respawn', promoted)


def handle_spawn(f: OutboxFile) -> Handled:
    """`spawn:` — moved from ``daemon._drain_outbox`` on main (lines 8673–8694)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    stats = f.ctx.stats
    promoted = 0
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        dispatched = daemon._queue_spawn_request(
            emit, task, inbox_dir, event_id, fm, body, outbox_dir,
            account_context=account_context,
        )
        if dispatched:
            promoted += 1
            message_path, _blocked = daemon._stage_outbound(
                task, account_context,
                body=body,
                kind="dispatch",
                target_gate="spawn",
                source_ref=str(fpath),
            )
            if message_path:
                message_store.transition(
                    message_path, message_store.DELIVERED,
                    gate="dispatch", platform_message_id="spawn-event",
                )
            if stats is not None:
                stats["spawn"] = stats.get("spawn", 0) + 1
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'spawn', promoted)


def handle_ask(f: OutboxFile) -> Handled:
    """`ask:` — moved from ``daemon._drain_outbox`` on main (lines 8696–8696, 8698–8706)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    stats = f.ctx.stats
    promoted = 0
    _allowance_ask = daemon._allowance_ask_spec(fm)
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        dispatched = daemon._queue_allowance_ask(
            task, inbox_dir, body, _allowance_ask, outbox_dir,
        )
        if dispatched:
            promoted += 1
            if stats is not None:
                stats["ask"] = stats.get("ask", 0) + 1
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'ask', promoted)


def handle_submit(f: OutboxFile) -> Handled:
    """`submit:` — moved from ``daemon._drain_outbox`` on main (lines 8709–8724)."""
    fpath = f.path
    body = f.body
    task = f.run
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    repo_root = f.ctx.repo_root
    stats = f.ctx.stats
    promoted = 0
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        handled = daemon._queue_submit_request(
            task, inbox_dir, body, outbox_dir, repo_root,
        )
        if handled:
            promoted += 1
            if stats is not None:
                stats["submit"] = stats.get("submit", 0) + 1
            emit(
                "spawn_submitted", run_id=task.id,
                event_id=event_id,
                generation=int(
                    task.meta["submitted_produce"]["spawn_submit_generation"]
                ),
            )
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'submit', promoted)


def handle_to(f: OutboxFile) -> Handled:
    """`to:` — moved from ``daemon._drain_outbox`` on main (lines 8727–8747)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    stats = f.ctx.stats
    promoted = 0
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        handled = daemon._queue_child_message(
            emit, task, inbox_dir, event_id, fm, body, outbox_dir,
        )
        if handled:
            promoted += 1
            message_path, _blocked = daemon._stage_outbound(
                task, account_context,
                body=body,
                kind="dispatch",
                target_gate="spawn-message",
                source_ref=str(fpath),
            )
            if message_path:
                message_store.transition(
                    message_path, message_store.DELIVERED,
                    gate="dispatch", platform_message_id="spawn-message-event",
                )
            if stats is not None:
                stats["spawn_message"] = stats.get("spawn_message", 0) + 1
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'to', promoted)


def handle_stop(f: OutboxFile) -> Handled:
    """`stop:` — moved from ``daemon._drain_outbox`` on main (lines 8750–8770)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    stats = f.ctx.stats
    promoted = 0
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        handled = daemon._queue_stop_request(
            emit, task, inbox_dir, event_id, fm, body, outbox_dir,
        )
        if handled:
            promoted += 1
            message_path, _blocked = daemon._stage_outbound(
                task, account_context,
                body=body or f"stop {fm.get('stop')}",
                kind="dispatch",
                target_gate="stop",
                source_ref=str(fpath),
            )
            if message_path:
                message_store.transition(
                    message_path, message_store.DELIVERED,
                    gate="dispatch", platform_message_id="stop-request",
                )
            if stats is not None:
                stats["stop"] = stats.get("stop", 0) + 1
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'stop', promoted)


def handle_note(f: OutboxFile) -> Handled:
    """`note:` — moved from ``daemon._drain_outbox`` on main (lines 8772–8772, 8774–8792)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    address_sources = f.ctx.address_sources
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    outbox_dir = f.ctx.outbox_dir
    stats = f.ctx.stats
    promoted = 0
    note_target = str(fm.get("note") or "").strip()
    # Close-without-speaking (the design's ``noted`` state): retire
    # a pending event deliberately with no outbound message. Same
    # union resolution as ``event:``; refusals land in notices.
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        # move 5b (§19.2): the frame's card-delta item is a pending item,
        # not an inbox event — `note:` on its id accepts it here, before the
        # event resolution would refuse an id no drawer holds.
        from .. import card_frame

        if note_target.startswith(card_frame.DELTA_ID_PREFIX) and card_frame.clear_delta(
            getattr(task, "meta", {}), note_target,
        ):
            if stats is not None:
                stats["note"] = stats.get("note", 0) + 1
            emit("card_delta_noted", run_id=task.id, event_id=event_id, target_event=note_target)
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'note', 1)
        # Move 5e: resolve the file before the retire moves it out of the
        # pending union, so a `topic:` on the note can stamp it after.
        noted_path = None
        if str(fm.get("topic") or "").strip():
            try:
                found, _responses, _ambiguous = daemon._resolve_event_target(
                    address_sources, note_target,
                )
                noted_path = found.get("_path") if isinstance(found, dict) else None
            except Exception:  # noqa: BLE001
                noted_path = None
        noted_id = daemon._note_event_closed(
            task, address_sources, note_target, body, outbox_dir,
            current_event_id=event_id,
        )
        if noted_id:
            _stamp_noted_topics(f, noted_id, noted_path)
            promoted += 1
            if stats is not None:
                stats["note"] = stats.get("note", 0) + 1
            emit(
                "event_noted",
                run_id=task.id,
                event_id=event_id,
                target_event=noted_id,
            )
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'note', promoted)


def _stamp_also_topics(task: Any, also_event: dict, thread: str) -> None:
    """Move 5e: an ``also:`` sibling is answered by the same reply, so it is
    stamped with the same topics the primary event was — the act scope's
    list (its own ``topic:``, else its one inherited topic). Never raises."""
    from .. import run_topic

    try:
        scope = run_topic.current_act()
        if scope is None:
            return
        slugs = scope.topics()
        also_id = str(also_event.get("id") or "")
        if not slugs or not also_id:
            return
        run_topic.confirm_event(
            scope.account_home, None, also_id, slugs,
            run=str(getattr(task, "id", "") or ""), thread=thread,
            event_path=also_event.get("_path"),
        )
    except Exception:  # noqa: BLE001 - a stamp never costs the delivery
        return


def _stamp_noted_topics(f: OutboxFile, noted_id: str, event_path: object) -> None:
    """Move 5e: ``note: <id>`` + ``topic: <slug[, slug…]>`` stamps the event
    it retires — the acknowledgement carries the stamp, never a separate act.
    Unknown slugs drop with one advisory; an event already stamped keeps its
    topic (nothing is reclassified). Never raises."""
    from .. import run_topic

    raw = str(f.frontmatter.get("topic") or "").strip()
    if not raw:
        return
    try:
        scope = run_topic.current_act()
        home = scope.account_home if scope is not None else None
        say = (scope.notice if scope is not None and scope.notice is not None
               else (lambda _kind, _text: None))
        live, dropped = run_topic.live_topics(home, raw)
        if dropped:
            tail = (f"the event carries {', '.join(live)}" if live
                    else "the event retires unstamped")
            say("advisory", f"note {noted_id}: topic: {', '.join(dropped)} names no heddle — "
                f"dropped; {tail}")
        if not live:
            return
        thread = ""
        if isinstance(event_path, Path) and event_path.is_file():
            try:
                thread = conversations.conversation_key_for_event(
                    protocol._read_event(event_path)) or ""
            except Exception:  # noqa: BLE001
                thread = ""
        stamped = run_topic.confirm_event(
            home, None, noted_id, live,
            run=str(getattr(f.run, "id", "") or ""), thread=thread,
            event_path=event_path if isinstance(event_path, Path) else None,
        )
        if not stamped and isinstance(event_path, Path) and event_path.is_file():
            held = str(protocol._read_event(event_path).get("topic") or "").strip()
            if held:
                say("advisory", f"note {noted_id}: already stamped {held} — topic: "
                    f"{', '.join(live)} not applied (nothing is reclassified)")
    except Exception:  # noqa: BLE001 - a stamp never costs the retire
        return


def handle_await(f: OutboxFile) -> Handled:
    """`await:` — moved from ``daemon._drain_outbox`` on main (lines 8795–8908)."""
    fpath = f.path
    fm = f.frontmatter
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    repo_root = f.ctx.repo_root
    stats = f.ctx.stats
    promoted = 0
    # A select, not a sleep (#959), with nothing left for the caller
    # to get wrong (#1187): the directive carries a ceiling and, at
    # most, one optional `file:` trigger. `brnrd await` is the front
    # door and stages this file itself.
    #
    # Strands arm this too, and the reasoning that used to refuse
    # them was simply wrong. It called the hold "a resident-level
    # cost decision" — but a strand does not spend the resident's
    # single-flight slot; it occupies a slot in the *spawn pool*
    # machinery), and it already occupies its worker by existing.
    # Awaiting costs nothing the strand is not already
    # paying, while refusing it left the runs least able to recover —
    # a strand blocked on a subprocess — with only a shell sleep
    # loop, which is the exact boundary-free stretch #959 exists to
    # end.
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        file_path, timeout_seconds, error = await_verb.parse_await(fm)
        if error:
            daemon._record_outbox_notice(
                outbox_dir, f"await dropped: {error}", kind="dropped",
                lifetime="run",
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'await', promoted)
        # #1327: a `spawn_completed` the daemon already rendered to this
        # run (stamped `observed_by` in `_pending_events_for_agent`)
        # never flips out of `status: pending` until run end (the
        # #1146 stamp is not removal) — so it stays in every later
        # tick's pending set and resolves every subsequent wait
        # instantly, forever. Snapshot those ids at arm time; the
        # resolve side excludes exactly this set, so the wait fires
        # only on what is genuinely new since arming.
        #
        # The snapshot is deliberately narrow: **only** a retired
        # self-parented completion this run has already observed. It is
        # NOT "everything pending right now". Excluding a live
        # correspondent's message that happened to be pending at arm
        # time would make the wait sleep straight through them, and
        # `prompts/daemon-substrate.md` promises the opposite in as many
        # words — "any *other* pending event resolves it the same way …
        # the queue never starves". The wider class (any event pending
        # before arming) is a separate design call and is not taken here.
        #
        # Note the call below is not a pure read: `_pending_events_for_agent`
        # writes `observed_by`/`observed_at` via `protocol.update_event_meta`
        # for unobserved self-parented completions. In a live wake the
        # heartbeat has almost always stamped them already, so this only
        # ever pulls that write slightly earlier — but it is a write, and
        # a future reader should not have to discover that.
        armed_pending = (
            daemon._pending_events_for_agent(
                inbox_dir, event_id,
                strand=daemon._is_strand(task.meta),
                account_context=account_context,
                repo_label=task.meta.get("repo_label"),
                observer_run_id=task.id,
            )
            if inbox_dir is not None else []
        )
        previous = task.meta.get("await") or {}
        task.meta["await"] = {
            "file": file_path,
            "timeout_seconds": timeout_seconds,
            "armed_at": time.time(),
            # An exact generation stamp, not a timestamp: `armed_at`
            # renders to whole seconds, and `brnrd await` re-arms on
            # every call — two calls inside one second would otherwise
            # be indistinguishable, and the CLI would read the *previous*
            # call's sticky-resolved outcome as this call's answer.
            "generation": str(time.time_ns()),
            "resolved": False,
            "armed_pending_ids": sorted(
                {
                    str(ev["id"])
                    for ev in armed_pending
                    if ev.get("id")
                    and ev.get("source") == "spawn_completed"
                    and str(ev.get("spawn_parent_run_id") or "") == task.id
                    and ev.get("observed_by")
                }
            ),
        }
        if daemon._truthy(fm.get("initiative-default")) and timeout_seconds is None:
            task.meta["await"]["initiative_default"] = True
            # A Shell lease ending is not the end of the idle stretch.
            task.meta["await"]["idle_since"] = (
                previous.get("idle_since", previous.get("armed_at", time.time()))
                if previous.get("initiative_default") and not previous.get("resolved")
                else time.time()
            )
        # design-the-seat-that-never-quits.md §machinery slice 3: a
        # proxy for "a correspondent just reached this seat" — the
        # first-ever arm is, ordinarily, the resident replying to
        # whatever woke it and immediately going quiet. Seeded once
        # (``setdefault``, never overwritten by a later bare re-arm)
        # so the hold-cost park's live-window refusal has a baseline
        # even before any fresh event ever refines it
        # (``_resolve_await_state``'s own update, keyed off the
        # event's real timestamp).
        task.meta.setdefault("hold_correspondent_at", time.time())
        home = (
            account.context_home_root(account_context)
            if account_context is not None else outbox_dir.parent.parent
        )
        entity = shuttle.Shuttle.load(home)
        # #1991: the arm is where the seat claims the row — a strand never
        # does (its wait stays on its own run record). The resolve is gated
        # on the run id this arm stamps, so a row left `listening` under a
        # run that is no longer arming it (a seat stopped mid-wait: only
        # `_finalize_completed` releases the row) would otherwise never
        # close. Reclaim it here, through the graph's own edges, instead of
        # through the ungated resolve that used to repair it by accident.
        if (
            not daemon._is_strand(task.meta)
            and entity.state == "listening"
            and entity.run_id != task.id
        ):
            entity.transition(
                "awake", why="await_armed:reclaimed", by="daemon",
                run_id=task.id,
                repo_root=str(repo_root or entity.repo_root),
                conversation_key=task.conversation_key,
            )
        if not daemon._is_strand(task.meta) and entity.state == "awake":
            entity.transition(
                "listening", why="await_armed", by="daemon",
                run_id=task.id,
                repo_root=str(repo_root or entity.repo_root),
                conversation_key=task.conversation_key,
            )
        promoted += 1
        if stats is not None:
            stats["await"] = stats.get("await", 0) + 1
        emit(
            "await_armed",
            run_id=task.id,
            event_id=event_id,
            file=file_path,
            timeout_seconds=timeout_seconds,
        )
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'await', promoted)


def handle_hold(f: OutboxFile) -> Handled:
    """`hold:` — moved from ``daemon._drain_outbox`` on main (lines 8911–9009)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    outbox_dir = f.ctx.outbox_dir
    repo_root = f.ctx.repo_root
    stats = f.ctx.stats
    promoted = 0
    # design-the-continuous-seat.md / design-the-allowance.md: the
    # resident's own proactive resource hold (the "notify near
    # 1-2% quota, await a manual reset" case) — distinct from
    # ``await:`` in the one way that matters: this ends the run's
    # process (see resource_hold.py's module docstring for why
    # keeping it alive is precisely the bug) rather than blocking
    # inside it, so it is handled outside the normal outbox/reply
    # flow — arming here just validates and stages the spec; the
    # actual status transition happens once this attempt's worker
    # loop unwinds (mirroring how `cut:`'s bolt is recorded here
    # but its run-completion effect lands at the worker tail).
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        hold_spec, hold_error = hold_verb.parse_hold(fm)
        if hold_error:
            daemon._record_outbox_notice(
                outbox_dir, f"hold dropped: {hold_error}",
                kind="dropped", lifetime="run", source_file=fpath.name,
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'hold', promoted)
        native_session_id = daemon._native_session_id_for(task)
        provider = (
            hold_spec["provider"]
            or daemon._resource_hold_provider_for_runner(
                task.meta.get("runner_shell") or task.meta.get("runner_name")
            )
        )
        resume_condition = hold_spec["resume_condition"]
        reset_deadline = hold_spec["reset_deadline_hint"]
        hold_cfg = conf.load_config(repo_root) if repo_root else {}
        refusal = daemon._resident_hold_refusal(task, resume_condition, hold_cfg)
        if refusal:
            # #1890 redo: the resident cannot park by choice. `await`
            # is the resting state; a park is for a wall the daemon
            # itself can confirm. The daemon's own parks (turn-end
            # safety net, starvation, a bolt on live strands, the
            # hold-cost opt-in) never pass through this branch.
            daemon._record_outbox_notice(
                outbox_dir, refusal,
                kind="refused", lifetime="run", source_file=fpath.name,
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'hold', promoted)
        if resume_condition == resource_hold.RESUME_RESET and reset_deadline is None:
            reset_deadline = daemon._codex_reset_deadline(None, native_session_id)
        if resume_condition == resource_hold.RESUME_RESET and reset_deadline is None:
            # Explicit, concrete boundary rather than a silent
            # downgrade nobody is told about. The wall is confirmed
            # (starvation measured), only its clock is not — so the
            # seat parks on the measured refill instead: `operator`
            # is not a wall and would let a tick wake a seat that
            # cannot run.
            resume_condition = resource_hold.RESUME_REFILL
            daemon._record_outbox_notice(
                outbox_dir,
                "hold: resume: reset requested but no measured "
                "provider reset deadline is available — armed as "
                "resume: refill (a measured quota refill thaws it) instead",
                kind="advisory", lifetime="run", source_file=fpath.name,
            )
        task.meta["pending_resource_hold"] = {
            "reason": hold_spec["reason"],
            "provider": provider,
            "detail": body or None,
            "native_session_id": native_session_id,
            "resume_kind": (
                resource_hold.RESUME_NATIVE if native_session_id
                else resource_hold.RESUME_UNSUPPORTED
            ),
            "resume_condition": resume_condition,
            "reset_deadline": reset_deadline,
        }
        if resume_condition == resource_hold.RESUME_REFILL:
            # The resident chose the starvation park itself
            # ("for however long you choose, if you choose to
            # hibernate because of the starvation"): the record
            # carries the floors and the bucket the thaw reads,
            # exactly as the daemon-armed one does.
            last_pct = task.meta.get("quota_binding_pct")
            task.meta["pending_resource_hold"]["quota"] = {
                "binding_remaining_pct": (
                    float(last_pct) if isinstance(last_pct, (int, float)) else None
                ),
                "starve_floor_pct": daemon._seat_starve_floor_pct(hold_cfg),
                "refill_floor_pct": daemon._seat_refill_floor_pct(hold_cfg),
                "runner": str(task.meta.get("runner_name") or ""),
                "model": str(task.meta.get("runner_core") or ""),
            }
        promoted += 1
        if stats is not None:
            stats["hold"] = stats.get("hold", 0) + 1
        emit(
            "hold_requested",
            run_id=task.id,
            event_id=event_id,
            reason=hold_spec["reason"],
            resume_condition=resume_condition,
        )
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'hold', promoted)


def handle_cut(f: OutboxFile) -> Handled:
    """`cut:` — moved from ``daemon._drain_outbox`` on main (lines 9012–9171)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    repo_root = f.ctx.repo_root
    stats = f.ctx.stats
    promoted = 0
    # The bolt (kb/design-the-bolt.md): a run's completion,
    # declared by the resident and checked against what the daemon
    # itself attests — the positive artifact the closeout guard's
    # negative machinery never had. ``cut_verb`` is pure parse/
    # validate; this branch owns the cross-reference against
    # pending events / relics / the blueprint, and the
    # cap-3-then-accept-annotated bounce ladder the maintainer
    # signed (design doc, forks 1/3/4). The daemon acts on nothing
    # in ``asks`` itself — dispositions are recorded intent;
    # ``note:``/``event:`` remain the only verbs that actually
    # retire or reply to an event.
    #
    # Guarded (#1379), but this branch can fall through (no
    # `continue`) into the shared gate/event tail below — so unlike
    # every other branch here, a tripped guard must `continue`
    # explicitly rather than let a half-built `declaration`/`body`
    # fall into that shared logic.
    cut_guard = daemon._OutboxEntryGuard(outbox_dir, fpath)
    with cut_guard:
        declaration, parse_error = cut_verb.parse_cut(fm)
        if parse_error:
            daemon._record_outbox_notice(
                outbox_dir, f"cut dropped: {parse_error}",
                kind="dropped", lifetime="run", source_file=fpath.name,
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'cut', promoted)
        pending_events = (
            daemon._pending_events_for_agent(
                inbox_dir, event_id,
                strand=(
                    daemon._is_strand(task.meta) if hasattr(task, "meta") else False
                ),
                account_context=account_context,
                repo_label=(
                    task.meta.get("repo_label")
                    if hasattr(task, "meta") else None
                ),
                observer_run_id=task.id,
            )
            if inbox_dir is not None else []
        )
        mismatches = daemon._cut_mismatches(
            task, declaration,
            pending_events=pending_events,
            repo_root=repo_root,
            outbox_dir=outbox_dir,
            reply_text=body,
        )
        if (
            declaration == cut_verb.CutDeclaration()
            and daemon._declaration_shaped_cut_body(body)
        ):
            mismatches.insert(
                0,
                "declaration-shaped body — staging casualty, not a woven reply",
            )
        if mismatches:
            bounce_kinds = daemon._cut_bounce_kinds(mismatches)
            prior_kinds = list(task.meta.get("cut_bounce_kinds") or [])
            task.meta["cut_bounce_kinds"] = prior_kinds + [
                kind for kind in bounce_kinds if kind not in prior_kinds
            ]
            bounces_so_far = int(
                (task.meta.get("cut_bounces") or 0)
                if hasattr(task, "meta") else 0
            )
            if bounces_so_far + 1 < daemon._CUT_BOUNCE_CAP:
                if hasattr(task, "meta"):
                    task.meta["cut_bounces"] = bounces_so_far + 1
                daemon._record_outbox_notice(
                    outbox_dir,
                    "cut bounced: " + " · ".join(mismatches),
                    kind="refused", lifetime="run", source_file=fpath.name,
                )
                daemon._retire_outbox_staging(fpath)
                return _handled(f, 'cut', promoted)
            # Cap reached: accept anyway rather than hold the run
            # hostage — a guard may only assert what an artifact
            # proves, and remedy severity matches signal confidence.
            if hasattr(task, "meta"):
                task.meta["cut_bounces"] = bounces_so_far + 1
        annotated = len(mismatches)
        accepted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if hasattr(task, "meta"):
            task.meta["bolt"] = {
                "accepted_at": accepted_at,
                "annotated": annotated,
                "attempts": int(task.meta.get("cut_bounces") or 0) + (
                    0 if annotated else 1
                ),
                "bounces": list(task.meta.get("cut_bounce_kinds") or []),
                **cut_verb.durable_declaration(
                    declaration,
                    dissent=mismatches if annotated else (),
                ),
            }
        if hasattr(task, "meta"):
            _assign_bolt_topic(task, accepted_at)
        if stats is not None:
            stats["cut"] = stats.get("cut", 0) + 1
        parked = daemon._park_bolt_on_live_strands(
            task, declaration, outbox_dir=outbox_dir, source_file=fpath.name,
        )
        if parked and stats is not None:
            stats["hold"] = stats.get("hold", 0) + 1
        emit(
            "cut_accepted",
            run_id=task.id,
            event_id=event_id,
            annotated=annotated,
        )
        if annotated:
            # Machine-spoken, never the resident's voice (the design's
            # own rule) — a trailing block the daemon appends, never
            # folded into the woven body it did not write.
            plural = "s" if annotated != 1 else ""
            body = (
                (body.rstrip("\n") + "\n\n" if body.strip() else "")
                + "---\n"
                + f"daemon: {annotated} check{plural} unresolved — "
                + " · ".join(mismatches)
            )
        # Gate-owned wakes keep using the current event's verified
        # reply lane. A gate-less wake has no such carrier, so aim the
        # accepted body's delivery through the same memoized
        # ``notify.gate`` fallback as the terminal stream. Acceptance
        # and its durable stamp happened above: failure to resolve or
        # deliver this fallback must never roll the bolt back.
        fm.pop("event", None)
        fm.pop("gate", None)
        cut_source = str(getattr(task, "source", "") or "")
        if cut_source and not daemon._gate_owns_source(cut_source):
            # Both calls below read disk (`.brr/config`, gate health,
            # thread history). They sit *after* an accepted, stamped
            # bolt and *inside* ``cut_guard``, whose ``__exit__``
            # quarantines the declaration file and trips the
            # ``continue`` below — so an unguarded raise here would
            # not merely fail to improve delivery, it would destroy
            # the delivery this branch already had. Before this
            # fallback existed the window held two ``fm.pop`` calls
            # and could not raise; keep that property. Resolution
            # failure degrades to the pre-existing undeliverable
            # staging, which is honest and recorded.
            try:
                cut_cfg = conf.load_config(
                    repo_root or emit.brr_dir.parent,
                )
                notify_gate = daemon._cached_notify_gate(
                    task,
                    cut_cfg,
                    emit.brr_dir,
                    conversation_key=str(
                        getattr(task, "conversation_key", "") or ""
                    ),
                )
            except Exception:  # noqa: BLE001 - see the comment above
                notify_gate = ""
            if notify_gate:
                fm["gate"] = notify_gate
    if cut_guard.tripped:
        return _handled(f, 'cut', promoted)
    return _handled(f, 'cut', promoted, then=f.rewritten(fm, body))


def _assign_bolt_topic(task, accepted_at: str) -> None:
    """Move 5c: the bolt carries the act's one topic (``cut: true`` +
    ``topic: <slug>``, else the run's) and lands one ``bolt`` index row."""
    from .. import run_topic

    try:
        topic = run_topic.act_topic(task)
        if not topic or not isinstance(task.meta.get("bolt"), dict):
            return
        task.meta["bolt"]["topic"] = topic
        run_topic.assign(
            run_topic.act_home(), topic, kind="bolt",
            ref=f"{task.id}/bolt/{accepted_at}", run=task.id, at=accepted_at,
        )
    except Exception:  # noqa: BLE001 - a topic never rolls a bolt back
        return


def handle_gate(f: OutboxFile) -> Handled:
    """`gate:` — moved from ``daemon._drain_outbox`` on main (lines 9172–9172, 9174–9222)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    inbox_dir = f.ctx.inbox_dir
    outbox_dir = f.ctx.outbox_dir
    responses_dir = f.ctx.responses_dir
    stats = f.ctx.stats
    promoted = 0
    gate = str(fm.get("gate") or "").strip()
    # Gate-addressed: an agent-initiated message to a destination
    # with no waiting event (a scheduled ping, an out-of-bound
    # note). Synthesize an already-`done` event the gate delivers
    # and cleans up; it never wakes a thought.
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        gate_available = daemon._gate_can_deliver(emit.brr_dir, gate)
        unrouted_thread = (
            daemon._unroutable_thread(gate, fm) if gate_available else ""
        )
        message_path, blocked = daemon._stage_outbound(
            task,
            account_context,
            body=body,
            kind="outbound",
            target_gate=gate,
            target_thread=str(fm.get("thread") or ""),
            source_ref=str(fpath),
            status=(
                message_store.PENDING
                if gate_available else message_store.UNDELIVERABLE
            ),
            reason=(
                # #1750: `target_thread` records what was *asked*
                # and stays honest doing it; paired with a bare
                # `status: delivered` it reads as a routed delivery
                # the gate never performed. The reason field is the
                # one part of this record that survives the
                # DELIVERED transition unchanged, so it is where
                # the difference belongs.
                daemon._unrouted_thread_reason(gate, unrouted_thread)
                if unrouted_thread else ""
            ) if gate_available
            else f"gate {gate!r} is not deliverable on this account",
            outbox_dir=outbox_dir,
        )
        if blocked:
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'gate', promoted)
        if daemon._deliver_out_of_bound(
            emit, task, responses_dir, inbox_dir, event_id, gate, fm, body,
            outbox_dir, message_path=message_path,
            account_context=account_context,
        ):
            promoted += 1
            daemon._project_said(task, outbox_dir, f"gate:{gate}", body)
            if stats is not None:
                stats["outbound"] = stats.get("outbound", 0) + 1
                stats["delivered"] = stats.get("delivered", 0) + 1
        daemon._retire_outbox_staging(fpath)
    return _handled(f, 'gate', promoted)


def handle_event(f: OutboxFile) -> Handled:
    """`event:` — the reply row; move 4b adds one read around it.

    A reply answering a ``mark: keep`` promotion ask that names its page
    records ``promoted_to`` on the bench file (``mark.py``). The target is
    read *before* delivery — a delivered ask no longer resolves.
    """
    from . import mark

    try:
        promotion = mark.promotion_target(f)
    except Exception:  # noqa: BLE001 - the promotion read never costs the reply
        promotion = None
    result = _deliver_event(f)
    if promotion is not None and mark.ask_retired(promotion):
        try:
            mark.record_promotion(f, promotion)
        except Exception:  # noqa: BLE001
            pass
    return result


def _deliver_event(f: OutboxFile) -> Handled:
    """`event:` — moved from ``daemon._drain_outbox`` on main (lines 9224–9601)."""
    fpath = f.path
    fm = f.frontmatter
    body = f.body
    task = f.run
    account_context = f.ctx.account_context
    address_sources = f.ctx.address_sources
    emit = f.ctx.emit
    event_id = f.ctx.event_id
    outbox_dir = f.ctx.outbox_dir
    responses_dir = f.ctx.responses_dir
    stats = f.ctx.stats
    promoted = 0
    raw_target = str(fm.get("event") or "").strip()
    raw_target = raw_target or event_id
    # The unconditional `event:` reply tail (#1379) — no explicit
    # `continue` needed after the `with`: this is the last thing the
    # loop does with this `fpath`, success or quarantined alike, so
    # falling out of the block already lands back at `for fpath in
    # entries:`.
    with daemon._OutboxEntryGuard(outbox_dir, fpath):
        # Short-id addressing (#906 fast-follow): the letter chrome renders
        # a shortened id (``evt-…8jwi``), and the resident's reply naturally
        # reconstructs that same short form — including for a self-reply,
        # where the mismatch used to misfire ``cross`` and bounce the reply
        # as "not pending". Resolve before computing ``cross`` so a short
        # form of *this* event or another pending one both land correctly.
        # Resolution spans the whole inbox union (#936) — a telegram-woken
        # run can close a cloud letter and vice versa — and an ambiguous
        # short id (shared tail across pending events, within or across
        # inboxes) is refused, never guessed.
        resolved_event, target_responses, ambiguous = daemon._resolve_event_target(
            address_sources, raw_target,
        )
        if ambiguous:
            candidates = ", ".join(
                hooks_mod._short_event_id(ev.get("id")) for ev in ambiguous
            )
            daemon._record_outbox_notice(
                outbox_dir,
                f"reply dropped: event {raw_target} is ambiguous — matches "
                f"{len(ambiguous)} pending events ({candidates}); address "
                "the full id — the message was NOT delivered",
                # Text says "dropped"; this is the ambiguous-target
                # refusal, same shape as `_note_event_closed`'s.
                kind="refused",
                lifetime="run",
            )
            daemon._stage_outbound(
                task,
                account_context,
                body=body,
                kind="interim",
                target_event=raw_target,
                source_ref=str(fpath),
                status=message_store.UNDELIVERABLE,
                reason=f"event {raw_target} is ambiguous ({candidates})",
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'event', promoted)
        if (
            resolved_event is not None
            # ``getattr``: this is the hot path every reply drain walks, and
            # an AttributeError here would break replies wholesale. A record
            # without ``meta`` is not a strand.
            and daemon._is_strand(getattr(task, "meta", None))
            and not daemon._strand_may_address(resolved_event, event_id)
        ):
            # The mail-reading hole, closed. Inbound isolation is enforced
            # on the read side (`_pending_events_for_agent`); this is the
            # same boundary on the write side. `gate:` is deliberately NOT
            # guarded — a strand may speak to a human whenever the work
            # demands it — but it may not *answer another thread's letter*,
            # which would retire someone else's pending event under the
            # strand's hand and land in a conversation it cannot even read.
            short = hooks_mod._short_event_id(resolved_event.get("id"))
            daemon._record_outbox_notice(
                outbox_dir,
                f"reply refused: event {short} belongs to another thread — a "
                "strand-stack run may only answer its own waking event or a "
                "parent's `to:` steer. To reach a human directly, use "
                "`gate: <name>`; to report back, your terminal stream is "
                "already the return value your dispatcher collects. The "
                "message was NOT delivered.",
                kind="refused",
                lifetime="run",
            )
            daemon._stage_outbound(
                task,
                account_context,
                body=body,
                kind="interim",
                target_event=raw_target,
                source_ref=str(fpath),
                status=message_store.UNDELIVERABLE,
                reason=(
                    f"event {short} is not addressable by a strand-stack run"
                ),
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'event', promoted)
        target = (
            str(resolved_event.get("id") or "") if resolved_event else raw_target
        )
        cross = target != event_id
        target_event = resolved_event if cross else None
        # A cross-inbox target's partials must land in the responses dir
        # its own delivery loop reads (the one paired with its inbox), or
        # the reply would sit in a queue nobody polls.
        target_responses_dir = (
            target_responses
            if cross and target_event is not None and target_responses is not None
            else responses_dir
        )
        if cross and target_event is None:
            # Unknown or non-pending target — don't deliver to the wrong
            # thread; drop with a console note that names the actual cause
            # (#936: "already handled, or the id is wrong" hid a third
            # cause, the wrong inbox — now impossible, and the remaining
            # two are stated apart).
            cause = daemon._event_refusal_cause(address_sources, raw_target, target)
            daemon._record_outbox_notice(
                outbox_dir,
                f"reply dropped: {cause} — the message was NOT delivered",
                kind="refused",
                lifetime="run",
            )
            daemon._stage_outbound(
                task,
                account_context,
                body=body,
                kind="interim",
                target_event=target,
                source_ref=str(fpath),
                status=message_store.UNDELIVERABLE,
                reason=cause,
            )
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'event', promoted)
        target_source = str(
            (target_event or {}).get("source") or getattr(task, "source", "")
        )
        own_source = str(getattr(task, "source", "") or "")
        redirected = False
        if (
            cross
            and target_event is not None
            and target_source
            and target_source != own_source
            and daemon._gate_owns_source(target_source)
            and not daemon._gate_can_deliver(emit.brr_dir, target_source)
        ):
            # Cross-gate, not reachable from this run (#578): a real gate
            # owns the target event, but it's neither this run's own gate
            # nor configured/running here — staging a reply for it would
            # sit ``pending`` in a store nobody polls. Record the foreign
            # target as explicitly undeliverable, retire it exactly like
            # any other cross reply, and redirect the body onto this run's
            # own live gate instead, prefixed with its origin, so the
            # answer still reaches someone rather than nobody.
            daemon._stage_outbound(
                task,
                account_context,
                body=body,
                kind="interim",
                target_event=target,
                target_gate=target_source,
                target_thread=str(target_event.get("conversation_key") or ""),
                source_ref=str(fpath),
                status=message_store.UNDELIVERABLE,
                reason=(
                    f"gate {target_source!r} is not reachable from this run "
                    "— redirected to the run's own gate"
                ),
            )
            daemon._set_event_status_if_present(target_event, "done")
            daemon._record_outbox_notice(
                outbox_dir,
                f"reply redirected: event {target} is owned by gate "
                f"{target_source!r}, not reachable from this run — delivered "
                "on this run's own gate instead, prefixed with its origin",
                # Delivered, and *not where it was addressed*. Content ✓,
                # lifecycle ✓, addressing ✗ — and addressing is the one of
                # the three the resident is told to check `notices` for. A
                # run that believes its answer reached the asker, when it
                # reached a different lane, has an unanswered correspondent
                # and possibly a reader who should not have had the text.
                # Counts, therefore, and says which failed.
                kind="redirected",
                lifetime="run",
            )
            summary = daemon._short_event_summary(target_event)
            origin_note = (
                f"re: {summary} — originally on {target_source}\n\n"
                if summary else f"re: an event on {target_source}\n\n"
            )
            body = origin_note + body
            target = event_id
            cross = False
            target_event = None
            target_responses_dir = responses_dir
            target_source = own_source
            redirected = True
        also_events, also_refused = daemon._resolve_also_targets(
            task, address_sources, fm,
            target=target, target_event=target_event,
            current_event_id=event_id, outbox_dir=outbox_dir,
        )
        if also_refused:
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'event', promoted)
        # Interim replies ride the target event's own gate. Dispatch-tree
        # sources (spawn, spawn_completed, dispatch_message) have no gate and
        # no collector for interims — only a strand's *terminal* report is
        # collected — so a partial written here would orphan and the record
        # would sit pending forever. Say so at staging time instead — but
        # only about a source we can actually see: an absent one is unknown,
        # not impossible.
        deliverable = not target_source or daemon._gate_owns_source(target_source)
        if not deliverable:
            # "No run left unheard" was built for the *terminal* reply and
            # never wired to the interim, so a schedule-woken seat could
            # deliver its last word and not one word before it.
            #
            # Live 2026-09-18: ten plain outbox messages — every PR
            # announcement, an audit report, a measurement, a stopping note —
            # died here across one morning, because the run's own waking event
            # was the co-maintainer tick. `_resolve_notify_gate`'s docstring
            # names `schedule` as the case it exists for; the fallback simply
            # did not reach this staging site. Worse, `resources.delivery`
            # reported `would_land: true` throughout, because it models the
            # terminal route — the facet whose whole job is this question was
            # answering about the other path.
            #
            # The dispatch-tree sources this branch was written for (`spawn`,
            # `spawn_completed`, `dispatch_message`) are unaffected: nothing
            # resolves a notify gate for a strand's own thread, so they still
            # stage undeliverable with the same reason.
            fallback_cfg = conf.load_config(
                f.ctx.repo_root or emit.brr_dir.parent,
            ) if emit.brr_dir else {}
            fallback_gate = daemon._resolve_notify_gate(
                fallback_cfg, emit.brr_dir,
                conversation_key=str(getattr(task, "conversation_key", "") or ""),
            ) if emit.brr_dir else ""
            if fallback_gate:
                target_source = fallback_gate
                deliverable = True
        undeliverable_reason = (
            f"no gate owns {target_source or 'unknown'} events; route via "
            "gate:<name> if a person must read it"
        )
        message_path, blocked = daemon._stage_outbound(
            task,
            account_context,
            body=body,
            kind="interim",
            target_event=target,
            target_gate=target_source,
            target_thread=str(
                (target_event or {}).get("conversation_key")
                or getattr(task, "conversation_key", "")
            ),
            # A redirect already staged one durable row for the foreign
            # target above (``source_ref=str(fpath)``); this second row is
            # the redirected delivery itself and needs its own identity, or
            # ``stage``'s idempotency-by-source_ref would just hand back
            # that first (undeliverable) row instead of creating this one.
            source_ref=str(fpath) + ("#redirect" if redirected else ""),
            status=(
                message_store.PENDING if deliverable
                else message_store.UNDELIVERABLE
            ),
            reason=(
                "" if deliverable else
                undeliverable_reason
            ),
            outbox_dir=outbox_dir,
        )
        if blocked:
            daemon._retire_outbox_staging(fpath)
            return _handled(f, 'event', promoted)
        # The retire below is guarded by ``cross and target_event is not None``;
        # ``not deliverable`` is *wider* than that, because ``target_source``
        # falls back to the run's own source when there is no cross target — so
        # a plain outbox message from any gate-less run (schedule: every
        # self-woken run) lands here with nothing to retire. One predicate, both
        # readers, so the notice cannot claim a retire the inbox never made.
        # Asserting one optimistically is the worse failure: it tells a resident
        # its waking event is handled while it is still pending.
        retires_target = not deliverable and cross and target_event is not None
        if not deliverable:
            # Daemon-minted sources (spawn_completed, schedule) have no correspondent,
            # so an undeliverable reply is not a loss — use kind="advisory" rather than
            # "dropped" to avoid inflating the dropped/refused count (#1351). Other
            # gateless sources represent a genuine absence of delivery plumbing.
            notice_kind = (
                "advisory"
                if target_source in ("spawn_completed", "schedule")
                else "dropped"
            )
            daemon._record_outbox_notice(
                outbox_dir,
                (
                    f"event {target} retired done; reply text staged "
                    f"undeliverable — {undeliverable_reason}"
                    if retires_target else
                    f"reply text staged undeliverable — {undeliverable_reason}"
                ),
                kind=notice_kind,
                lifetime="run",
            )
        ppath = (
            protocol.write_partial(
                target_responses_dir, target, body, message_path=message_path,
            )
            if body and deliverable else None
        )
        daemon._retire_outbox_staging(fpath)
        if retires_target:
            # Nothing will deliver this, but the resident *did* answer it:
            # retire the event so the unowned-source inbox stops growing
            # (#454), with the text preserved as an undeliverable record.
            daemon._set_event_status_if_present(target_event, "done")
        if not ppath:
            return _handled(f, 'event', promoted)
        promoted += 1
        daemon._project_said(task, outbox_dir, target, body)
        if stats is not None:
            key = "other" if cross else "current"
            stats[key] = stats.get(key, 0) + 1
            # #743. ``current`` is *not* "a message reached a correspondent":
            # a parked ``runner_policy`` / ``config_change`` proposal
            # increments it too. ``delivered`` counts only the writes that
            # put text in front of a reader, so the terminal-route
            # classification can ask that question directly instead of
            # subtracting the proposal verbs — a subtraction the next verb
            # added would silently rejoin.
            stats["delivered"] = stats.get("delivered", 0) + 1
        if not cross:
            # Remember what was already delivered to the waking thread so the
            # terminal-stream dispatch can skip an exact duplicate — the
            # "deliver via outbox *and* restate on stdout" double-post the old
            # required-terminal-reply contract used to push residents into.
            # In-process only (a dynamic attribute, never serialized).
            digests = getattr(task, "_delivered_current_digests", None)
            if digests is None:
                digests = set()
                task._delivered_current_digests = digests  # type: ignore[attr-defined]
            digests.add(hashlib.sha256(body.encode("utf-8")).hexdigest())
        if cross and target_event is not None:
            daemon._set_event_status_if_present(target_event, "done")
        artifact_key = emit.conversation_key
        artifact_event_id = event_id
        if cross and target_event is not None:
            artifact_key = conversations.conversation_key_for_event(target_event) or ""
            artifact_event_id = target
            if artifact_key:
                conversations.append_event(emit.brr_dir, artifact_key, target_event)
        # One physical send, marked at the write side so a reader never
        # has to re-derive it: every artifact this burst mints — the
        # primary reply and each `also:` sibling below — carries the
        # same `delivery_id` (the partial file this one send actually
        # wrote). `unread_pile` counts distinct delivery ids rather
        # than raw artifact rows, so a burst answering N events is one
        # message, not N (daemon-substrate.md's "a burst is one turn").
        delivery_id = str(ppath) if ppath else ""
        if artifact_key:
            conversations.append_artifact(
                emit.brr_dir, artifact_key,
                kind="interim_response",
                path=str(ppath),
                run_id=task.id,
                event_id=artifact_event_id,
                label=(f"reply:{target}" if cross else f"interim:{event_id}"),
                body=body,
                extra=({"delivery_id": delivery_id} if delivery_id else None),
            )
        for also_event in also_events:
            # Same reply, one more retired letter: a burst is one turn,
            # so every `also:` id shares this delivery rather than
            # minting its own partial (kb design-multi-response's
            # "one complete reply per event" — this is that reply,
            # attached twice).
            also_id = str(also_event.get("id") or "")
            if not also_id:
                continue
            try:
                protocol.update_event_meta(
                    also_event, also_of=target, also_at=daemon._utc_now(),
                )
            except OSError:
                pass
            daemon._set_event_status_if_present(also_event, "done")
            also_key = conversations.conversation_key_for_event(also_event) or ""
            _stamp_also_topics(task, also_event, also_key)
            if also_key:
                conversations.append_event(emit.brr_dir, also_key, also_event)
                conversations.append_artifact(
                    emit.brr_dir, also_key,
                    kind="interim_response",
                    path=str(ppath),
                    run_id=task.id,
                    event_id=also_id,
                    label=f"also:{also_id}",
                    body=body,
                    extra=({"delivery_id": delivery_id} if delivery_id else None),
                )
            emit(
                "event_also_handled",
                run_id=task.id,
                event_id=event_id,
                target_event=also_id,
            )
        emit(
            "interim_response",
            run_id=task.id,
            event_id=event_id,
            path=str(ppath),
            target_event=(target if cross else None),
        )
    return _handled(f, 'event', promoted)


# ── move 4: the frame-owned verbs ──────────────────────────────────────


def _guarded(f: OutboxFile, verb: str, run) -> Handled:
    """Run a frame-owned verb inside the same per-file guard every moved
    handler wears (#1379): a raise costs this file — quarantined, one
    notice — never the tick."""
    guard = daemon._OutboxEntryGuard(f.ctx.outbox_dir, f.path)
    with guard:
        return run(f)
    return _handled(f, verb, 0)


def handle_land(f: OutboxFile) -> Handled:
    """`land: <pr>` — the frame merges a PR it has read green (``land.py``)."""
    from . import land

    return _guarded(f, "land", land.handle)


def handle_fold(f: OutboxFile) -> Handled:
    """`fold: <place>` — the frame opens a bench file and asks the weaver (``fold.py``)."""
    from . import fold

    return _guarded(f, "fold", fold.handle)


def handle_topic(f: OutboxFile) -> Handled:
    """`topic: new|split|merge|retire` — the resident manages its heddles (``topic.py``)."""
    from . import topic

    return _guarded(f, "topic", topic.handle)


def handle_mark(f: OutboxFile) -> Handled:
    """`mark: keep|drop <bench path>` — the user's write on a bench file (``mark.py``)."""
    from . import mark

    return _guarded(f, "mark", mark.handle)


def handle_stake(f: OutboxFile) -> Handled:
    """`stake: refuse` — the seat's answer to a stake (``stake.py``)."""
    from . import stake

    return _guarded(f, "stake", stake.handle_stake)


def handle_cut_at(f: OutboxFile) -> Handled:
    """`cut-at:` alone — it rides a stake; refused (``stake.py``)."""
    from . import stake

    return _guarded(f, "cut-at", stake.handle_cut_at)
