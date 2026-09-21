"""The outbox verb table: frontmatter key → handler, in precedence order.

A staged file may carry several routing keys. The drain walks :data:`ROWS`
top to bottom and hands the file to the **first** row whose selector matches;
every later row is skipped — ``spawn: true`` beside ``to:`` and ``note:`` is a
spawn, and nothing else. This is exactly the order ``_drain_outbox`` applied on
``main`` as a flat run of ``if <selector>: … continue``:

    runner_policy › config_change › halt › respawn › spawn › ask › submit › to
    › stop
    › note › await › hold › [land › fold › topic › mark › stake › cut-at] › cut
    › gate › thread › event

Two properties of that order are kept on purpose and named here:

- **The control verbs outrank speech.** Every key above ``cut`` consumes the
  file; a file that also says ``event:`` or ``gate:`` is not delivered.
- **``topic:`` is a verb only as an op** (``new`` · ``split`` · ``merge`` ·
  ``retire`` · ``show`` · ``assign``). A bare slug is the act's topic
  (move 5c) and the file falls through to the rows that deliver it.
- **``halt:`` outranks every other verb but the two policy proposals**, and
  it is strict about its own keys: ``halt_verb.parse_halt`` refuses any field
  outside its grammar *by name*, so a file that says ``halt:`` beside
  ``spawn:`` or ``event:`` is refused whole rather than silently swallowing
  the other verb's request. ``respawn:`` sits directly below it because
  ``respawn:`` retires **into** it (design-the-four-stops.md) — the older
  verb still routes for the dashboard tap and the strand mint, and
  ``halt:``'s successor is minted through its machinery.

- **``cut:`` falls through.** So does an accepted ``halt:``, for the same
  reason and through the same seam: it pops its own keys and hands the
  announcement to the delivery rows below. An accepted bolt pops ``event``/``gate``, may set
  ``gate`` to the notify fallback, and hands the rewritten file to the rows
  after it (``gate``, then ``event``) — the handler returns ``then=``.

``event`` is the fallback row (``selects=None``): a file no earlier row claimed
is a reply — to ``event: <id>`` if it names one, to the waking event if not.

The frame-owned verbs added by move 4 (``land``, ``fold``), move 5b's
``topic`` (the resident's heddles, ``topic.py``) and move 4b's
(``mark``, ``stake``, ``cut-at``) sit after ``hold`` and before ``cut``: no
file ``main`` already routed changes hands, and a ``stake:`` riding a
``spawn:``/``respawn:`` request stays that request's modifier.

``protocol._OUTBOX_ROUTING_KEYS`` (the lenient parser's gate) must equal the
keys these selectors read — ``tests/test_protocol.py`` derives the set from
this module's ``ROWS`` by AST, so a row added here without listing its key
there fails loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .. import daemon
from . import verbs
from .shapes import Handled, OutboxFile

Selector = Callable[[dict], object]


@dataclass(frozen=True)
class Row:
    key: str
    selects: Selector | None
    handler: Callable[[OutboxFile], Handled]


def _selects_runner_policy(fm: dict) -> object:
    return daemon._runner_policy_proposal_requested(fm)


def _selects_config_change(fm: dict) -> object:
    return daemon._config_change_requested(fm)


def _selects_halt(fm: dict) -> object:
    return "halt" in fm


def _selects_respawn(fm: dict) -> object:
    return daemon._truthy(fm.get("respawn"))


def _selects_spawn(fm: dict) -> object:
    return daemon._truthy(fm.get("spawn"))


def _selects_ask(fm: dict) -> object:
    return daemon._allowance_ask_spec(fm) is not None


def _selects_submit(fm: dict) -> object:
    return daemon._truthy(fm.get("submit"))


def _selects_to(fm: dict) -> object:
    return str(fm.get("to") or "").strip()


def _selects_stop(fm: dict) -> object:
    return str(fm.get("stop") or "").strip()


def _selects_note(fm: dict) -> object:
    return str(fm.get("note") or "").strip()


def _selects_await(fm: dict) -> object:
    return "await" in fm


def _selects_hold(fm: dict) -> object:
    return "hold" in fm


def _selects_land(fm: dict) -> object:
    return "land" in fm


def _selects_fold(fm: dict) -> object:
    return "fold" in fm


def _selects_topic(fm: dict) -> object:
    # Move 5c: `topic:` is also every act's modifier (`event:` + `topic:
    # the-loom`). Only an op claims the file; a bare slug falls through to
    # the rows below, which carry it as the act's topic.
    # Move 5e: on an `event:` reply or an `also:` burst a slug *list* is
    # the modifier too — the answered event is stamped with every slug.
    from . import topic

    value = fm.get("topic")
    if not topic.is_op(value):
        return False
    replies = any(str(fm.get(key) or "").strip() for key in ("event", "also"))
    return not (replies and topic.is_slug_list(value))


def _selects_mark(fm: dict) -> object:
    return "mark" in fm


def _selects_stake(fm: dict) -> object:
    return "stake" in fm


def _selects_cut_at(fm: dict) -> object:
    return "cut-at" in fm


def _selects_cut(fm: dict) -> object:
    return "cut" in fm


def _selects_gate(fm: dict) -> object:
    return str(fm.get("gate") or "").strip()


def _selects_thread(fm: dict) -> object:
    # Preserve event/also addressing and gate:'s existing thread modifier.
    return "thread" in fm and not any(key in fm for key in ("event", "also"))


ROWS: tuple[Row, ...] = (
    Row("runner_policy", _selects_runner_policy, verbs.handle_runner_policy),
    Row("config_change", _selects_config_change, verbs.handle_config_change),
    Row("halt", _selects_halt, verbs.handle_halt),
    Row("respawn", _selects_respawn, verbs.handle_respawn),
    Row("spawn", _selects_spawn, verbs.handle_spawn),
    Row("ask", _selects_ask, verbs.handle_ask),
    Row("submit", _selects_submit, verbs.handle_submit),
    Row("to", _selects_to, verbs.handle_to),
    Row("stop", _selects_stop, verbs.handle_stop),
    Row("note", _selects_note, verbs.handle_note),
    Row("await", _selects_await, verbs.handle_await),
    Row("hold", _selects_hold, verbs.handle_hold),
    Row("land", _selects_land, verbs.handle_land),
    Row("fold", _selects_fold, verbs.handle_fold),
    Row("topic", _selects_topic, verbs.handle_topic),
    Row("mark", _selects_mark, verbs.handle_mark),
    Row("stake", _selects_stake, verbs.handle_stake),
    Row("cut-at", _selects_cut_at, verbs.handle_cut_at),
    Row("cut", _selects_cut, verbs.handle_cut),
    Row("gate", _selects_gate, verbs.handle_gate),
    Row("thread", _selects_thread, verbs.handle_thread),
    Row("event", None, verbs.handle_event),
)


#: The rows that put a message in front of a person. ``note:``, ``spawn:``,
#: ``await:`` and the control verbs are not messages and write no ledger row
#: here; a ``cut:`` that falls through reaches ``gate``/``event`` below it and
#: is ledgered by whichever of those delivers its announcement.
CHAT_BOUND = frozenset({"gate", "thread", "event"})


def _message_target(row: Row, f: OutboxFile) -> str:
    """The destination a chat-bound file addresses, as one coordinate."""
    fm = f.frontmatter
    if row.key == "gate":
        gate = str(fm.get("gate") or "").strip()
        thread = str(fm.get("thread") or "").strip()
        return f"gate:{gate}" + (f" thread:{thread}" if thread else "")
    if row.key == "thread":
        return f"thread:{str(fm.get('thread') or '').strip()}"
    return f"event:{str(fm.get('event') or '').strip() or f.ctx.event_id}"


def _ledgered_message(row: Row, f: OutboxFile) -> Handled:
    """Run a chat-bound handler with its ``message`` row (``actions.py``).

    ``attempted`` before the handler fires; then the handler's own derived
    outcome names the second state — ``accepted`` (it promoted the reply) is
    ``confirmed`` with the delivery's coordinate as evidence, ``refused`` or
    ``deferred`` (nothing promoted) is ``failed`` with the notice that names
    the file. A handler that raises leaves the row ``attempted``: the closeout
    stamps that ``ambiguous``, which is what a send with no answer *is*.
    """
    from .. import actions

    outbox_dir = f.ctx.outbox_dir
    target = _message_target(row, f)
    meta = getattr(f.run, "meta", None)
    run_id = str(getattr(f.run, "id", "") or "")
    by = f"strand:{run_id}" if isinstance(meta, dict) and daemon._is_strand(meta) else "seat"
    act_id = actions.append(
        outbox_dir, verb="message", target=target, state="attempted", by=by,
        why=f.path.name,
    )
    result = row.handler(f)
    if act_id:
        if result.outcome == "accepted":
            actions.transition(outbox_dir, act_id, "confirmed", evidence=target)
        else:
            actions.transition(
                outbox_dir, act_id, "failed",
                evidence=result.notice or "nothing promoted",
            )
    return result


def dispatch(f: OutboxFile) -> list[Handled]:
    """Run *f* through the table: the first matching row, and — when that
    handler falls through — the first matching row after it, and so on.

    Selectors run unguarded, as the drain's ``if`` tests did; each handler
    runs inside a notice attribution for its own file.
    """
    with _act_scope(f):
        return _dispatch(f)


def _act_scope(f: OutboxFile):
    """Move 5c: one topic for everything this file writes (``run_topic``)."""
    from .. import account
    from .. import run_topic

    task = f.run
    meta = getattr(task, "meta", None) or {}
    home = None
    if f.ctx.account_context is not None:
        try:
            home = account.context_home_root(f.ctx.account_context)
        except Exception:  # noqa: BLE001
            home = None
    run_id = str(getattr(task, "id", "") or "")
    return run_topic.acting(run_topic.ActScope(
        f.frontmatter, task,
        outbox_dir=f.ctx.outbox_dir, account_home=home, inbox_dir=f.ctx.inbox_dir,
        notice=lambda kind, text: daemon._record_outbox_notice(
            f.ctx.outbox_dir, text, kind=kind, lifetime="run", verb="topic", run=run_id,
        ),
        is_strand=bool(daemon._is_strand(meta)) if isinstance(meta, dict) else False,
        resolve_event=_event_resolver(f),
    ))


def _event_resolver(f: OutboxFile):
    """Move 5d: ``event id → the pending event`` across every drawer this
    run addresses — the same resolution an ``event:`` reply gets."""
    sources = list(f.ctx.address_sources or [])

    def resolve(event_id: str):
        if not sources:
            return None
        event, _responses, _ambiguous = daemon._resolve_event_target(sources, event_id)
        return event

    return resolve


def _dispatch(f: OutboxFile) -> list[Handled]:
    from . import notices

    results: list[Handled] = []
    start = 0
    while start < len(ROWS):
        for index in range(start, len(ROWS)):
            row = ROWS[index]
            if row.selects is not None and not row.selects(f.frontmatter):
                continue
            with notices.attributed(
                source_file=f.path.name,
                verb=row.key,
                run=str(getattr(f.run, "id", "") or ""),
            ):
                if row.key in CHAT_BOUND:
                    result = _ledgered_message(row, f)
                else:
                    result = row.handler(f)
            results.append(result)
            if result.then is None:
                return results
            f = result.then
            start = index + 1
            break
        else:
            return results
    return results
