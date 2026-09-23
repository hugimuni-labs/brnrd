"""The run's ``.item`` — a run's own claim on the ask it is working, read
the same way ``.topic`` is (design-the-ask.md §Build cut step 4, folded in
by his 2026-09-22 steer, evt-1790102795181974000-n04a: hand-written
``stage: making`` is "a poor resident-facing design, prone to forgetfulness
errors" — make in-hand **derived**, never typed).

Sibling of ``run_topic.py``'s ``.topic``, drastically simpler: there is no
propose/mint apparatus here — an item already exists (minted by
``brnrd item new``) or the control names nothing this run can use, and
either way this is a bounded, best-effort read. One file, one read, one
derived write:

1. **The claim.** A seat writes one line to ``.item`` in its own outbox —
   a ``w-N`` id or a ``sign:`` callsign (``items.py``/``asks.py``'s own
   grammar), resolved the same way ``accept <target>`` resolves one (see
   :func:`resolve`). A strand receives the same claim through its own
   ``spawn:`` directive's ``item:`` key — generic event-meta inheritance
   (``Run.from_event`` copies every event frontmatter field onto
   ``task.meta``), the same mechanism a strand's assigned ``topic:``
   already rides (``daemon._queue_spawn_request``); there is no outbox to
   write a literal ``.item`` file into yet at spawn-request time, so
   :func:`settle` reads ``task.meta["item"]`` as a fallback when no
   ``.item`` control exists.
2. **The derived rows.** Read at the same cadence ``.topic`` is
   (``daemon._frame_heartbeat``, once per heartbeat, never per boundary):
   :func:`settle` folds the current control into ``asks.mark_in_hand`` —
   this run's id lands in ``attempts:`` and ``stage:`` advances to
   ``making`` when it hadn't reached that far. Never backward, never past
   ``making`` from here — delivery (``stage: delivered`` + ``return:``) is
   a separate act, derived at close from the run's own produce
   (``daemon._ask_delivery_capture``, beside the weld's own capture half).

Never raises: a control read/write here must not cost the heartbeat or the
run that carries it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

CONTROL_NAME = ".item"
_CONTROL_READ_CAP = 512

#: ``task.meta`` keys this module owns — mirrors ``run_topic``'s own naming
#: so the two sibling controls read the same way in a debugger or a test.
META_CONTROL_STAMP = "run_item_control"
META_RUN_ITEM = "run_item"

Notice = Callable[[str, str], None]


def read_control(outbox_dir: Path | None) -> str | None:
    """The first non-blank line of ``.item``, an optional ``item:`` prefix
    stripped — a raw target (a ``w-N`` id or a callsign), unresolved.
    ``None`` when *outbox_dir* is ``None``, the file is absent, empty,
    unreadable, or a symlink. Never raises."""
    if outbox_dir is None:
        return None
    path = Path(outbox_dir) / CONTROL_NAME
    try:
        if path.is_symlink():
            return None
        with path.open("rb") as handle:
            raw = handle.read(_CONTROL_READ_CAP)
    except OSError:
        return None
    lines = raw.decode("utf-8", errors="replace").splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    if first.lower().startswith("item:"):
        first = first[len("item:"):].strip()
    return first.strip("`").strip() or None


def resolve(warp_root: Path | None, target: str) -> str | None:
    """*target* (a ``w-N`` id or a callsign) resolved to a real, **open**
    item — the same two-door resolution the ``accept``/``reroute``
    grammar's own target uses (``asks.apply_inbound_directive``), plus the
    weld's own guard (``weld.annotate_ignition``): a ``done:``/``retired:``
    item has no work left to claim, regardless of who names it. ``None``
    when *target* is blank, names nothing, or names a closed item."""
    from . import asks as asks_mod
    from . import items as items_mod

    target = (target or "").strip()
    if not target:
        return None
    item_id = target if items_mod.ALLOCATED_ID_RE.fullmatch(target) else None
    if item_id is None:
        item_id = asks_mod.resolve_sign(warp_root, target)
    if item_id is None:
        return None
    path = items_mod.resolve_item(warp_root, item_id)
    if path is None:
        return None
    item = items_mod.parse_item(path)
    if item is None or item.state != "open":
        return None
    return item_id


def settle(
    task: Any,
    *,
    outbox_dir: Path | None,
    warp_root: Path | None,
    notice: Notice | None = None,
) -> str | None:
    """Fold the current ``.item`` into the item it names.

    Idempotent per control line (a stamp compare, mirroring
    ``run_topic.settle``) and per row (the underlying writes already
    dedupe) — safe to call every heartbeat. Returns the resolved item id,
    or the run's last-settled one when the control is unchanged, absent, or
    unresolvable this time (a transient typo does not erase a prior good
    claim). Never raises — a control read must not sink the heartbeat that
    carries it.
    """
    from . import asks as asks_mod

    meta = getattr(task, "meta", None)
    if not isinstance(meta, dict):
        return None
    try:
        raw = read_control(outbox_dir)
        if raw is None:
            # A strand's dispatch stamps `item:` on its own waking event —
            # generic event-meta inheritance (`Run.from_event`), the same
            # mechanism a strand's assigned `topic:` rides
            # (`daemon._queue_spawn_request`). Read once as a fallback when
            # no `.item` control exists; the stamp compare below then makes
            # it idempotent exactly like a resident-authored line would be.
            raw = str(meta.get("item") or "").strip() or None
        if raw is None:
            return meta.get(META_RUN_ITEM) or None
        if meta.get(META_CONTROL_STAMP) == raw:
            return meta.get(META_RUN_ITEM) or None
        meta[META_CONTROL_STAMP] = raw
        say = notice or (lambda _kind, _text: None)
        item_id = resolve(warp_root, raw)
        if item_id is None:
            say(
                "refused",
                f".item refused: {raw!r} is not a known open w-N id or sign",
            )
            return meta.get(META_RUN_ITEM) or None
        meta[META_RUN_ITEM] = item_id
        asks_mod.mark_in_hand(
            warp_root, item_id, run_id=str(getattr(task, "id", "") or ""),
        )
        return item_id
    except Exception:  # noqa: BLE001 - a control read must never sink a heartbeat
        return meta.get(META_RUN_ITEM) or None
