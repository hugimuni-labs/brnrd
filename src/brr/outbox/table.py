"""The outbox verb table: frontmatter key → handler, in precedence order.

A staged file may carry several routing keys. The drain walks :data:`ROWS`
top to bottom and hands the file to the **first** row whose selector matches;
every later row is skipped — ``spawn: true`` beside ``to:`` and ``note:`` is a
spawn, and nothing else. This is exactly the order ``_drain_outbox`` applied on
``main`` as a flat run of ``if <selector>: … continue``:

    runner_policy › config_change › respawn › spawn › ask › submit › to › stop
    › note › await › hold › [land › fold › mark › stake › cut-at] › cut › gate
    › event

Two properties of that order are kept on purpose and named here:

- **The control verbs outrank speech.** Every key above ``cut`` consumes the
  file; a file that also says ``event:`` or ``gate:`` is not delivered.
- **``cut:`` falls through.** An accepted bolt pops ``event``/``gate``, may set
  ``gate`` to the notify fallback, and hands the rewritten file to the rows
  after it (``gate``, then ``event``) — the handler returns ``then=``.

``event`` is the fallback row (``selects=None``): a file no earlier row claimed
is a reply — to ``event: <id>`` if it names one, to the waking event if not.

The frame-owned verbs added by move 4 (``land``, ``fold``) and the 4b stubs
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


ROWS: tuple[Row, ...] = (
    Row("runner_policy", _selects_runner_policy, verbs.handle_runner_policy),
    Row("config_change", _selects_config_change, verbs.handle_config_change),
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
    Row("mark", _selects_mark, verbs.handle_mark),
    Row("stake", _selects_stake, verbs.handle_stake),
    Row("cut-at", _selects_cut_at, verbs.handle_cut_at),
    Row("cut", _selects_cut, verbs.handle_cut),
    Row("gate", _selects_gate, verbs.handle_gate),
    Row("event", None, verbs.handle_event),
)


def dispatch(f: OutboxFile) -> list[Handled]:
    """Run *f* through the table: the first matching row, and — when that
    handler falls through — the first matching row after it, and so on.

    Selectors run unguarded, as the drain's ``if`` tests did; each handler
    runs inside a notice attribution for its own file.
    """
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
