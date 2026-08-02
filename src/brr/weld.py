"""THE WELD, machinery half (#972): warp items and the runs they ignite
reference each other through resolver addresses.

The maintainer's live read named the defect: "temporal repeating instead of
referencing" — a run renders its relics while the warp item that ignited it
carries its own ``refs:``, so the same object appears twice with no shared
address. The weld is two directions on one item, riding the existing relic
manifest (``.relics.jsonl``) with **no new store** (design-work-layers.md
§Storage cosmology — the item stays the conjunction point):

- **Ignition annotates the item.** When the daemon starts a run whose event
  body names a warp item address (``layer#slug``, one of the five resolver
  namespaces), the run's manifest gains an ``item`` relic carrying that
  address, and the item's section in ``surface/layers/<layer>.md`` gains a
  ``taken: run-…`` row — the residue the item leaves in the layer as it
  crosses into the shed.
- **Capture lands the produce back.** At run finalize, each ``item`` relic in
  the manifest receives the run's forge produce (pr / merge / issue relics)
  onto the item's ``refs:`` row in the qualified ``owner/repo#N`` grammar —
  a *reference*, never a copy of the other side's content.

Addresses are never guessed: an address that does not resolve (no such layer
file, or no ``## `` heading whose anchor matches the slug) is skipped with a
log line, and nothing is written for it. The slug computation deliberately
mirrors the frontend's ``headingAnchor`` (``src/frontend/src/lib/surface.ts``)
so both sides of the dashboard compute identical addresses.

Layer files are authored surface (both hands hold the pen), so every edit
here is minimal and row-scoped: one ``taken:`` row, appended run ids, and
appended refs — never a rewrite of prose. The surface commit rides the
existing capture net (``daemon._capture_dominion`` commits the whole account
home after a thought); this module never commits or pushes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import account
from . import relics

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .account import AccountContext

# The item-address grammar (#972 spec): ``<layer>#<slug>``, both sides
# lowercase ``[a-z0-9-]`` starting alphanumeric. Stricter than what
# ``heading_anchor`` can emit (anchors may carry ``_``) on purpose — the
# address namespace stays clean, and a heading that slugs outside it simply
# has no address, same as a bare ``#N`` naming no repo.
ITEM_ADDRESS_RE = re.compile(r"^[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$")

# Scan form of the same grammar inside free text. The lookbehind excludes a
# ``/`` so the tail of a qualified forge ref (``owner/repo#N`` — the *other*
# resolver namespace with a ``#``) can never be misread as a warp address.
_ADDRESS_SCAN_RE = re.compile(
    r"(?<![\w/-])([a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*)(?![\w-])"
)

# Mirrors ``surface.ts::headingAnchor``: JS ``\w`` is ASCII-only, so the
# strip class is spelled out rather than using Python's Unicode-aware ``\w``.
_ANCHOR_DROP_RE = re.compile(r"[^A-Za-z0-9_\s-]")
_ANCHOR_SPACE_RE = re.compile(r"\s+")

_HEADING_RE = re.compile(r"^##[ \t]+(.*)$")
# The recognized-row block, same set as ``backchannelPage.ts::ROW_RE``. A
# ``taken:`` row is deliberately *not* in it — the frontend's row block ends
# at the first unrecognized line, so ``taken:`` must always land after every
# recognized row to leave the item's schema rows parseable.
_ROW_RE = re.compile(r"^(kind|state|needs|refs|prompt):[ \t]*")
_TAKEN_RE = re.compile(r"^taken:[ \t]*(.*)$")
_REFS_RE = re.compile(r"^refs:[ \t]*(.*)$")

# The qualified forge grammar (shipped d939ca7c): ``owner/repo#N`` is the
# resolvable form; a bare ``#N`` names no repo on an account-global surface.
_QUALIFIED_REF_RE = re.compile(r"^([\w.-]+/[\w.-]+)#(\d+)$")


def heading_anchor(text: str) -> str:
    """GitHub-style anchor slug of a heading, mirroring the frontend.

    Must stay in lockstep with ``surface.ts::headingAnchor`` — the two are
    the one address computation performed in two languages: lowercase, strip
    non-word chars (ASCII word, keeping whitespace and hyphens), trim,
    spaces→hyphens.
    """
    cleaned = _ANCHOR_DROP_RE.sub("", text.lower())
    return _ANCHOR_SPACE_RE.sub("-", cleaned.strip())


def is_item_address(text: str) -> bool:
    """Whether *text* is a well-formed ``<layer>#<slug>`` item address."""
    return bool(ITEM_ADDRESS_RE.fullmatch(text or ""))


def scan_item_addresses(text: str) -> list[str]:
    """Candidate item addresses in free text, unique, in first-mention order.

    Grammar-level only — resolution against the layer files is the caller's
    second gate. A qualified forge ref's tail never matches (see the scan
    regex), so ``owner/repo#123`` yields nothing here.
    """
    seen: list[str] = []
    for match in _ADDRESS_SCAN_RE.finditer(text or ""):
        address = match.group(1)
        if address not in seen:
            seen.append(address)
    return seen


def layers_dir(ctx: "AccountContext | None") -> Path | None:
    """The account's ``surface/layers/`` directory, or ``None`` when there is
    no enabled account home (or no layer has ever been authored — no
    directory means no warp, and the weld has nothing to weld)."""
    if ctx is None or not getattr(ctx, "enabled", False):
        return None
    path = account.work_surface_path(ctx) / "layers"
    return path if path.is_dir() else None


def resolve_address(layers_root: Path | None, address: str) -> Path | None:
    """The layer file an address resolves into, or ``None``.

    Resolution demands both halves attested: ``<layer>.md`` exists under the
    layers directory AND a ``## `` heading whose anchor equals the slug
    exists in it. Anything less is unresolvable — skipped by callers, never
    guessed into a partial write.
    """
    if layers_root is None or not is_item_address(address):
        return None
    layer, slug = address.split("#", 1)
    path = layers_root / f"{layer}.md"
    try:
        if not path.is_file():
            return None
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return None
    if _find_section(lines, slug) is None:
        return None
    return path


def _find_section(lines: list[str], slug: str) -> tuple[int, int] | None:
    """``(heading_index, end_index)`` of the item section whose heading
    anchors to *slug*; ``end_index`` is exclusive (next ``## `` or EOF).
    First match wins, same as the frontend's anchor lookup."""
    for i, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match and heading_anchor(match.group(1).strip()) == slug:
            end = i + 1
            while end < len(lines) and not _HEADING_RE.match(lines[end]):
                end += 1
            return i, end
    return None


def _rows_end(lines: list[str], heading_idx: int, section_end: int) -> int:
    """Index just past the item's recognized-row block — where a ``taken:``
    row (or a new ``refs:`` row) belongs. Skips the conventional single blank
    line after the heading, then every contiguous recognized row."""
    i = heading_idx + 1
    if i < section_end and lines[i].strip() == "":
        i += 1
    while i < section_end and _ROW_RE.match(lines[i]):
        i += 1
    return i


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines), encoding="utf-8")


def mark_taken(path: Path, slug: str, run_id: str) -> bool:
    """Add ``taken: <run_id>`` to the item's section, idempotently.

    An existing ``taken:`` row gains the id space-separated; a section with
    none gains the row directly after its recognized rows (before the body,
    after the schema — see ``_ROW_RE``'s note). A run id already present is
    a no-op: re-running ignition never duplicates the residue. Returns
    whether the file changed.
    """
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return False
    section = _find_section(lines, slug)
    if section is None:
        return False
    start, end = section
    for i in range(start + 1, end):
        match = _TAKEN_RE.match(lines[i])
        if match:
            ids = match.group(1).split()
            if run_id in ids:
                return False
            lines[i] = "taken: " + " ".join([*ids, run_id])
            _write_lines(path, lines)
            return True
    insert = _rows_end(lines, start, end)
    row = [f"taken: {run_id}"]
    if insert < len(lines) and lines[insert].strip():
        # Keep the row visually separated from a body that follows with no
        # blank line of its own (the no-rows item shape).
        row.append("")
    lines[insert:insert] = row
    _write_lines(path, lines)
    return True


def _ref_present(row_value: str, ref: str) -> bool:
    """Whether a qualified forge ref is already on a ``refs:`` row, in any of
    its authored spellings: the plain qualified form, or a markdown link whose
    URL names the same thread (``…/owner/repo/issues/N`` or ``…/pull/N``)."""
    qualified = _QUALIFIED_REF_RE.match(ref)
    if qualified is None:
        return ref in row_value
    repo, number = qualified.groups()
    pattern = re.compile(
        re.escape(repo) + "#" + number + r"(?!\d)"
        + "|" + re.escape(repo) + r"/(?:issues|pull)/" + number + r"(?!\d)"
    )
    return bool(pattern.search(row_value))


def append_item_refs(path: Path, slug: str, refs: list[str]) -> list[str]:
    """Append qualified refs onto the item's ``refs:`` row, deduped.

    Refs already present — in the qualified grammar or as an equivalent forge
    link — are skipped, so re-running finalize appends nothing twice. A
    section with no ``refs:`` row gains one inside the recognized-row block.
    Returns the refs actually added (empty ⇒ the file was not touched).
    """
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return []
    section = _find_section(lines, slug)
    if section is None:
        return []
    start, end = section
    refs_idx: int | None = None
    for i in range(start + 1, end):
        if _REFS_RE.match(lines[i]):
            refs_idx = i
            break
    row_value = lines[refs_idx] if refs_idx is not None else ""
    missing = [ref for ref in refs if not _ref_present(row_value, ref)]
    if not missing:
        return []
    if refs_idx is not None:
        current = _REFS_RE.match(lines[refs_idx]).group(1).strip()  # type: ignore[union-attr]
        parts = ([current] if current else []) + missing
        lines[refs_idx] = "refs: " + " · ".join(parts)
    else:
        insert = _rows_end(lines, start, end)
        row = ["refs: " + " · ".join(missing)]
        if insert < len(lines) and lines[insert].strip() and not _TAKEN_RE.match(lines[insert]):
            row.append("")
        lines[insert:insert] = row
    _write_lines(path, lines)
    return missing


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
    layers_root: Path | None,
    outbox_dir: Path | None,
    *,
    run_id: str,
    body: str,
) -> list[str]:
    """Ignition, the item's half: scan *body* for item addresses; for each
    one that resolves, append an ``item`` relic to the run's manifest and a
    ``taken: <run_id>`` row to the item. Returns the resolved addresses.

    Unresolvable addresses are skipped with a log line naming them — never
    guessed, never a partial write. Idempotent against the manifest (an
    already-reported address is not re-appended) and against the layer file
    (``mark_taken``).
    """
    if layers_root is None or not body:
        return []
    addresses = scan_item_addresses(body)
    if not addresses:
        return []
    already = {
        str(record.get("address") or "")
        for record in relics.read_reported(outbox_dir)
        if record.get("kind") == "item"
    }
    resolved: list[str] = []
    for address in addresses:
        target = resolve_address(layers_root, address)
        if target is None:
            print(
                f"[brnrd] weld: item address {address!r} does not resolve "
                f"under {layers_root} — skipped"
            )
            continue
        if address not in already:
            relics.append(outbox_dir, "item", address=address)
        slug = address.split("#", 1)[1]
        mark_taken(target, slug, run_id)
        resolved.append(address)
    return resolved


def capture_refs(
    layers_root: Path | None,
    *,
    records: list[dict[str, Any]],
    origin_repo: str | None,
) -> dict[str, list[str]]:
    """Capture, the run's half: land the run's forge produce back on every
    item its manifest names. Returns ``{address: [refs added]}`` for the
    items that actually changed.

    Reads the same collected relic list the ledger row records, so the item's
    ``refs:`` and the run's ``external_refs`` cannot disagree about what the
    run produced. Re-running is a no-op (``append_item_refs`` dedupes).
    """
    if layers_root is None:
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
        target = resolve_address(layers_root, address)
        if target is None:
            print(
                f"[brnrd] weld: item address {address!r} no longer resolves "
                f"under {layers_root} — refs not landed"
            )
            continue
        slug = address.split("#", 1)[1]
        added = append_item_refs(target, slug, refs)
        if added:
            welded[address] = added
    return welded
