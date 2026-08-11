"""THE WELD, machinery half (#972, re-grounded 2026-08-11): warp items and
the runs they ignite reference each other through item ids.

The item space moved from layer files (``surface/layers/<layer>.md``,
addressed ``layer#slug``) to one file per item (``surface/warp/<id>.md``,
addressed by the id alone) — ``items.py`` owns the grammar and the file
edits; this module is the run-lifecycle glue, riding the existing relic
manifest (``.relics.jsonl``) with no new store:

- **Ignition annotates the item.** When the daemon starts a run whose event
  body names an item id (a bare ``w-<N>`` token, or an explicit
  ``item: <id>`` line — the address the dashboard's copy-prompt appends),
  the run's manifest gains an ``item`` relic carrying the id and the item
  gains a ``taken: run-…`` row — the residue it leaves as it crosses into
  the shed.
- **Capture lands the produce back.** At run finalize, each ``item`` relic
  receives the run's forge produce (pr / issue / merge relics) onto the
  item's ``refs:`` row in the qualified ``owner/repo#N`` grammar — a
  *reference*, never a copy of the other side's content.

Ids are never guessed: an id that does not resolve to an item file is
skipped with a log line, and nothing is written for it. Item files are
authored surface, so every edit is row-scoped; the surface commit rides
the existing capture net (``daemon._capture_dominion``) — this module
never commits or pushes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import items
from . import relics

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .account import AccountContext


def is_item_address(text: str) -> bool:
    """Whether *text* is a well-formed item id (slug grammar; the allocator
    mints ``w-<N>``, but a hand-authored slug id is equally legal)."""
    return bool(items.ITEM_ID_RE.fullmatch(text or ""))


def scan_item_addresses(text: str) -> list[str]:
    """Candidate item ids in free text — see ``items.scan_item_ids``."""
    return items.scan_item_ids(text)


def warp_dir(ctx: "AccountContext | None") -> Path | None:
    """The account's ``surface/warp/`` directory, or ``None`` when there is
    no enabled account home (or no item has ever been authored — no
    directory means no warp, and the weld has nothing to weld)."""
    return items.warp_dir(ctx)


def resolve_address(warp_root: Path | None, address: str) -> Path | None:
    """The item file an id resolves to, or ``None`` — never guessed."""
    return items.resolve_item(warp_root, address)


def qualified_forge_refs(
    records: list[dict[str, Any]], origin_repo: str | None,
) -> list[str]:
    """The run's forge produce as qualified ``owner/repo#N`` refs, deduped.

    Only relic kinds that name a forge thread count: ``pr`` and ``issue``
    (via ``number``) and ``merge`` (via its ``pr`` field). The repo is the
    record's own ``repo`` field when it names another project, else the
    run's origin — and a record whose repo cannot be attested either way is
    dropped rather than guessed, same bar as URL derivation in ``relics``.
    """
    out: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        kind = str(record.get("kind") or "")
        if kind in {"pr", "issue"}:
            number = record.get("number")
        elif kind == "merge":
            number = record.get("pr")
        else:
            continue
        try:
            n = int(number)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        repo = str(record.get("repo") or "").strip().strip("/")
        if "/" not in repo:
            repo = str(origin_repo or "").strip().strip("/")
        if "/" not in repo:
            continue
        ref = f"{repo}#{n}"
        if ref not in out:
            out.append(ref)
    return out


def annotate_ignition(
    warp_root: Path | None,
    outbox_dir: Path | None,
    *,
    run_id: str,
    body: str,
) -> list[str]:
    """Ignition, the item's half: scan *body* for item ids; for each one
    that resolves, append an ``item`` relic to the run's manifest and a
    ``taken: <run_id>`` row to the item. Returns the resolved ids.

    Unresolvable ids are skipped with a log line naming them — never
    guessed, never a partial write. Idempotent against the manifest and
    against the item file (``items.mark_taken``).
    """
    if warp_root is None or not body:
        return []
    addresses = items.scan_item_ids(body)
    if not addresses:
        return []
    already = {
        str(record.get("address") or "")
        for record in relics.read_reported(outbox_dir)
        if record.get("kind") == "item"
    }
    resolved: list[str] = []
    for address in addresses:
        target = items.resolve_item(warp_root, address)
        if target is None:
            print(
                f"[brnrd] weld: item id {address!r} does not resolve "
                f"under {warp_root} — skipped"
            )
            continue
        if address not in already:
            relics.append(outbox_dir, "item", address=address)
        items.mark_taken(target, run_id)
        resolved.append(address)
    return resolved


def capture_refs(
    warp_root: Path | None,
    *,
    records: list[dict[str, Any]],
    origin_repo: str | None,
) -> dict[str, list[str]]:
    """Capture, the run's half: land the run's forge produce back on every
    item its manifest names. Returns ``{id: [refs added]}`` for the items
    that actually changed.

    Reads the same collected relic list the ledger row records, so the
    item's ``refs:`` and the run's ``external_refs`` cannot disagree about
    what the run produced. Re-running is a no-op (``items.append_refs``
    dedupes).
    """
    if warp_root is None:
        return {}
    addresses: list[str] = []
    for record in records:
        if not isinstance(record, dict) or record.get("kind") != "item":
            continue
        address = str(record.get("address") or "")
        if is_item_address(address) and address not in addresses:
            addresses.append(address)
    if not addresses:
        return {}
    refs = qualified_forge_refs(records, origin_repo)
    if not refs:
        return {}
    welded: dict[str, list[str]] = {}
    for address in addresses:
        target = items.resolve_item(warp_root, address)
        if target is None:
            print(
                f"[brnrd] weld: item id {address!r} no longer resolves "
                f"under {warp_root} — refs not landed"
            )
            continue
        added = items.append_refs(target, refs)
        if added:
            welded[address] = added
    return welded
