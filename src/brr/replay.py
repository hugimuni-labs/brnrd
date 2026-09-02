"""Replay a captured wake's prompt under modified prompt files (w-56 rung 1).

The maintainer's ask (2026-08-15 memo, warp item ``w-56``): edit a prompt
block, replay N captured wakes under it, and see where the *first act*
would diverge. Actually running a core and diffing its first act costs
quota, needs runner auth, and is non-deterministic — that's rung 2. **You
cannot diff a divergence until you can rebuild the input that produced
it**, and nothing before this module could rebuild a wake under different
prompts. This is rung 1: deterministic, free, testable, and it answers one
question honestly — *what would this wake have read, had these prompt
files been in place instead?*

Substitute, do not re-assemble
-------------------------------
A captured wake's volatile blocks (quota, live runs, portal state, the
injected event, dominion digest, kb health) cannot be honestly re-derived
after the fact — that world is gone, and pretending otherwise is how a
replay tool starts lying about what a resident actually saw. So this
module never rebuilds a prompt from scratch. It takes the literal captured
``prompt.md`` bytes, locates the spans that came from **files on disk**
(``run.md``, ``weave.md``, ``register.md``, ``daemon-substrate.md``,
``identity-core.md``, ``diffense.md``, ``introspection.md``, and the
curated ``portals.md`` extract), and splices in the corresponding file
from ``--prompts <dir>`` when one is supplied — holding every other byte
identical.

Locating a block: checkable, not heuristic
-------------------------------------------
The assembled prompt carries no delimiter of its own — no HTML comment,
no marker, nothing a regex could anchor on (confirmed by reading
``prompts._join_prompt_parts`` / ``_glue_preamble``: it is a flat
``"\\n\\n".join(...)`` of blocks and nothing more). What *is* checkable is
the run's own ``boot-score.json``, persisted beside ``prompt.md`` for
every daemon wake (:mod:`brr.run_context`): it names every block that
entered, in manifest order, with its exact rendered byte length
(:class:`brr.bootscore.ContractEntry.bytes` — "measured at render time
from the text that actually entered the prompt", per that field's own
docstring).

So: reorder the manifest into the assembly's *actual* render order (see
:func:`_true_render_order` — the manifest's own listed order is not
always that order; see its docstring for the one confirmed divergence),
then walk cumulative byte offsets through the captured ``prompt.md``,
consuming the known ``"\\n\\n"`` glue between blocks. This is checkable
because it is self-verifying: at every block boundary the walk asserts
the next two bytes are exactly the glue it expects, and at the end it
asserts the walk accounted for every byte of the file. Either holds
exactly, or the whole replay refuses with the offset and byte counts that
disagree — never a silent partial match. See :func:`locate_captured_prompt`.

This caught a real, load-bearing case while building it: **every captured
run in this repo (until #1753) had ``boot-score.json``'s ``body.mounted:
true``** (``boot.mount`` is on) — meaning ``prompt.md`` is the short
kernel+trailer text and the file-backed blocks (``run.md``, ``weave.md``,
``daemon-substrate.md``, the very files this tool exists to let someone
edit) never entered its bytes at all; they rode a resumed session
transcript instead (:mod:`brr.transcript`). The self-check above refused
those runs outright rather than reporting a zero-byte "no change" that
would in fact be true only by accident — correct, but it meant the tool
had never successfully replayed a real wake and could not, on any run this
repo had.

Reconstitution (#1753)
-----------------------
``daemon.py`` now persists the mounted-out blocks beside the capture —
``prompt-mounted.json``, written by :func:`brr.run_context.write_mounted_blocks`
from the exact ``mount_sink`` dict the wake's own build diverted, never
re-derived later from whatever the prompt files on disk say today. When a
captured run's ``boot-score.json`` says ``body.mounted: true``,
:func:`locate_captured_prompt` requires that sidecar, uses it to reconstitute
the full assembly — every block back at its manifest-recorded position,
mounted or not — and *then* runs the same offset walk described above
against that reconstituted whole. The self-check is unchanged in what it
demands (exact byte reconciliation or refusal); it now simply receives a
complete input on a mounted wake instead of one with ~30-45% of its bytes
missing. A mounted run captured before the sidecar existed still refuses,
with a message naming that specifically (never the generic mismatch line,
and never by guessing from the current prompt files) — see the "predates
the sidecar" branch in :func:`locate_captured_prompt`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The two headings `prompts._build_portal_verb_grammar_block` extracts from
# `docs/portals.md` — reused rather than re-derived so a substituted
# `portals.md` is sliced exactly the way the live wake would slice it.
from .prompts import _PORTAL_VERB_GRAMMAR_HEADINGS, _extract_markdown_sections

SEP = b"\n\n"

# block_key of the manifest's trailer entry — the Run Context Bundle. Always
# last in both the manifest and the real render; named once so the reorder
# and the walk agree on which entry that is.
_TRAILER_KEY = "run-context-bundle"

# block_keys `_join_prompt_parts` appends *after* the inject stack, right
# before the trailer — see `_true_render_order`'s docstring for why the
# manifest disagrees with this.
_TAIL_SPECIAL_KEYS = frozenset({"diffense", "introspection"})

# The one block whose file-backed text is a curated extract of a larger
# file rather than the file verbatim (`prompts._MOUNTABLE_TEXT_BUILDERS`
# names the same fact for the live daemon mount path; kept as a constant
# here rather than importing that registry, since it is a dict of builders
# keyed for a different call shape — the *fact* "this key is special" is
# what's shared, not the builder).
_CURATED_EXTRACT_KEYS = frozenset({"portal-verb-grammar"})

_COMPUTED_LOCATION = "computed"


class ReplayLocateError(Exception):
    """The captured prompt's block layout could not be verified.

    Raised instead of returning a best-guess span: a substitution built on
    an unverified offset is worse than no tool at all (see this module's
    docstring — the whole reason a manifest-driven offset walk is safe is
    that it either reconciles exactly or refuses).
    """


@dataclass(frozen=True)
class BlockSpan:
    """One manifest block, located in the reconstituted wake input.

    ``start``/``end`` are a byte range in ``prompt.md`` for an ordinary
    (prose) block. A block that was mounted out of the prose instead
    carries its text in ``mounted_text`` and ``start``/``end`` are ``None``
    — it never occupied any range in ``prompt.md``, so a real offset there
    would be a lie about where the bytes came from.
    """

    block_key: str
    label: str
    location: str
    start: int | None
    end: int | None
    mounted_text: str | None = None

    @property
    def file_backed(self) -> bool:
        return self.location != _COMPUTED_LOCATION

    @property
    def is_mounted(self) -> bool:
        """Reconstituted from the mount sidecar rather than sliced from `prompt.md`."""
        return self.mounted_text is not None


@dataclass(frozen=True)
class LocateResult:
    """The outcome of locating every block in one captured wake.

    ``spans`` covers every *present, non-zero-byte* block from the
    manifest, in the order they actually appear in ``prompt_bytes`` —
    computed blocks included, since their byte length is load-bearing for
    the walk even though nothing ever substitutes them.
    """

    run_id: str
    prompt_bytes: bytes
    spans: list[BlockSpan]


def _true_render_order(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reorder captured manifest entries to match the actual render order.

    **Historical compensation, kept for backward compatibility only
    (#1753).** Every manifest written before the writer-side fix
    (``prompts._collect_toggle_contracts``, split out of
    ``_collect_preamble_contracts`` in the same change) listed ``diffense``
    and ``introspection`` right after ``portal-verb-grammar`` — before the
    inject stack (identity-core, dominion, work-surface, ...) — while
    ``prompts._join_prompt_parts`` actually appends those same two blocks
    *after* the inject stack, immediately before the trailer (Run Context
    Bundle). A manifest written by the fixed writer already lists them in
    render order, so this reorder is a no-op on it (filtering an
    already-correctly-ordered list into head/tail_special/trailer buckets
    and concatenating them back reproduces the same order). It cannot be
    deleted: every run captured before the fix is still on disk with the
    old order baked into its ``boot-score.json``, and ``replay`` has no way
    to tell a fixed manifest from an old one except by re-deriving the
    correct order itself — which is exactly what this function does. So it
    stays, doing nothing on new captures and the real compensation on old
    ones, until every pre-fix run ages out of the archive.

    On a wake where both toggles are off (the overwhelming common case —
    they render 0 bytes and are absent from the manifest as `present:
    false`) this reorder is a no-op regardless of writer version, since a
    walk skips zero-byte entries either way. It only matters, and is only
    tested here, when one or both toggles are on.
    """
    head = [c for c in contracts if c["block_key"] not in _TAIL_SPECIAL_KEYS and c["block_key"] != _TRAILER_KEY]
    tail_special = [c for c in contracts if c["block_key"] in _TAIL_SPECIAL_KEYS]
    trailer = [c for c in contracts if c["block_key"] == _TRAILER_KEY]
    return head + tail_special + trailer


#: `boot-score.json` sidecar carrying the mounted-out block text, written by
#: `run_context.write_mounted_blocks` beside `prompt.md` on any wake where
#: `body.mounted` is true (#1753). Named once so the writer and this reader
#: cannot drift on the filename.
MOUNTED_SIDECAR_NAME = "prompt-mounted.json"


def _load_mount_sidecar(run_dir: Path) -> dict[str, str]:
    """Load `prompt-mounted.json`'s block map, or refuse with a specific reason.

    Called only when `boot-score.json` says `body.mounted: true` — a mounted
    wake with no sidecar predates #1753 and has no checkable way to
    reconstitute its mounted blocks; that is a different, more specific
    fact than "the bytes don't add up", and the constraint this module was
    built under is that a caller must be told *which* is true, never left to
    read a generic mismatch and guess.
    """
    sidecar_path = run_dir / MOUNTED_SIDECAR_NAME
    if not sidecar_path.exists():
        raise ReplayLocateError(
            f"{run_dir} was captured with `boot.mount` on (boot-score.json's "
            f"body.mounted=true) but carries no {MOUNTED_SIDECAR_NAME} — this run "
            "was captured before the sidecar existed (#1753), so the mounted-out "
            "blocks (run.md, weave.md, daemon-substrate.md, ...) are gone for "
            "good; replay has no checkable way to reconstitute them, and refuses "
            "rather than guessing from whatever the prompt files on disk say today."
        )
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReplayLocateError(f"{MOUNTED_SIDECAR_NAME} in {run_dir} is not valid JSON: {exc}") from exc
    blocks = sidecar.get("blocks")
    if not isinstance(blocks, dict):
        raise ReplayLocateError(
            f"{MOUNTED_SIDECAR_NAME} in {run_dir} has no dict-shaped 'blocks' key — "
            "refusing to guess at its shape"
        )
    return blocks


def locate_captured_prompt(run_dir: Path) -> LocateResult:
    """Locate every manifest block in the reconstituted wake input.

    For an unmounted wake this is a byte span in `prompt.md`, same as
    always. For a mounted wake (`boot-score.json`'s `body.mounted: true`)
    it also draws on `prompt-mounted.json` (#1753) — the sidecar that
    persists the exact text `daemon.py` diverted out of the prose at
    capture time — so a block that never entered `prompt.md` is still
    located, just from the sidecar instead of an offset.

    Raises :class:`ReplayLocateError` — never returns a partial or
    best-guess result — when:

    - ``prompt.md`` or ``boot-score.json`` is missing from ``run_dir``;
    - the wake was mounted and ``prompt-mounted.json`` is missing (a capture
      that predates #1753 — see :func:`_load_mount_sidecar`) or malformed;
    - the recorded ``prompt_bytes`` total disagrees with the reconstituted
      total — `prompt.md`'s size plus the sidecar's, when mounted; `prompt.md`'s
      size alone otherwise (the fast top-level check);
    - a mounted block's sidecar text doesn't match its manifest-recorded
      byte length (the sidecar's own precision check);
    - a computed offset boundary in `prompt.md` does not hold the expected
      ``"\\n\\n"`` glue (a slower, block-precise version of the fast check,
      for the case where the totals happen to agree but a block's position
      does not — e.g. a hand-edited or truncated capture).
    """
    prompt_path = run_dir / "prompt.md"
    score_path = run_dir / "boot-score.json"
    if not prompt_path.exists():
        raise ReplayLocateError(f"no prompt.md in {run_dir} — nothing captured to replay")
    if not score_path.exists():
        raise ReplayLocateError(
            f"no boot-score.json in {run_dir} — this run predates the manifest, "
            "or ran with no boot-score path armed; replay has no checkable way "
            "to locate blocks without it"
        )

    prompt_bytes = prompt_path.read_bytes()
    try:
        score = json.loads(score_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReplayLocateError(f"boot-score.json in {run_dir} is not valid JSON: {exc}") from exc

    mounted = bool((score.get("body") or {}).get("mounted"))
    sidecar_blocks: dict[str, str] = _load_mount_sidecar(run_dir) if mounted else {}

    def _is_mounted(block_key: str) -> bool:
        return mounted and block_key in sidecar_blocks

    recorded_total = score.get("prompt_bytes")
    sidecar_total = sum(len(t.encode("utf-8")) for t in sidecar_blocks.values())
    reconstituted_total = len(prompt_bytes) + sidecar_total
    if recorded_total is not None and recorded_total != reconstituted_total:
        detail = (
            f"prompt.md ({len(prompt_bytes)}B) plus prompt-mounted.json's sidecar "
            f"blocks ({sidecar_total}B) reconstitute to {reconstituted_total} bytes"
            if mounted else
            f"the captured prompt.md is {len(prompt_bytes)} bytes"
        )
        raise ReplayLocateError(
            f"boot-score.json records prompt_bytes={recorded_total} but {detail} — "
            "refusing rather than guessing at the mismatch."
            + ("" if mounted else (
                " The most likely cause: this wake was rendered with `boot.mount` "
                "on, so file-backed blocks (run.md, weave.md, daemon-substrate.md, "
                "...) were seeded as a resumed session transcript instead of "
                "entering prompt.md's own text, and the manifest still records "
                "their *unmounted* size."
            ))
        )

    contracts = _true_render_order(score.get("contracts") or [])
    present = [c for c in contracts if c.get("present") and c.get("bytes")]

    # Pass 1: walk only the blocks that actually occupy `prompt.md` bytes —
    # a mounted block contributed none, and (per `_join_prompt_parts`/`_take`)
    # no separator was emitted in its place either, so it must be invisible
    # to this walk entirely, not just zero-width within it.
    in_prompt = [c for c in present if not _is_mounted(c["block_key"])]
    located_by_key: dict[str, tuple[int, int]] = {}
    offset = 0
    for idx, c in enumerate(in_prompt):
        n = int(c["bytes"])
        end = offset + n
        if end > len(prompt_bytes):
            raise ReplayLocateError(
                f"block {c['block_key']!r}: computed span [{offset}, {end}) runs past "
                f"the captured prompt's end ({len(prompt_bytes)} bytes) — the recorded "
                "block layout does not reconcile with this capture; refusing"
            )
        located_by_key[c["block_key"]] = (offset, end)
        offset = end
        if idx + 1 < len(in_prompt):
            got = prompt_bytes[offset:offset + len(SEP)]
            if got != SEP:
                raise ReplayLocateError(
                    f"block {c['block_key']!r}: expected the standard blank-line "
                    f"separator at byte {offset}, found {got!r} instead — the "
                    "captured prompt does not match the recorded block layout; refusing"
                )
            offset += len(SEP)

    if offset != len(prompt_bytes):
        raise ReplayLocateError(
            f"reconstructed {offset} bytes from the recorded block layout but "
            f"prompt.md is {len(prompt_bytes)} bytes — {len(prompt_bytes) - offset} "
            "byte(s) unaccounted for; refusing rather than guessing at the mismatch"
        )

    # Pass 2: emit every present block in true render order — mounted blocks
    # included, as zero-footprint spans backed by the sidecar text, sitting
    # exactly where the manifest says they belong relative to their
    # `prompt.md`-backed neighbours. This is the reconstitution: the full
    # assembly, not the mount-truncated one.
    spans: list[BlockSpan] = []
    for c in present:
        key = c["block_key"]
        label = c.get("label", key)
        location = c.get("location", _COMPUTED_LOCATION)
        if _is_mounted(key):
            text = sidecar_blocks[key]
            declared = int(c["bytes"])
            actual = len(text.encode("utf-8"))
            if actual != declared:
                raise ReplayLocateError(
                    f"block {key!r}: boot-score.json records {declared} bytes but "
                    f"{MOUNTED_SIDECAR_NAME}'s sidecar text for it is {actual} bytes — "
                    "refusing rather than guessing at the mismatch"
                )
            spans.append(BlockSpan(
                block_key=key, label=label, location=location,
                start=None, end=None, mounted_text=text,
            ))
        else:
            start, end = located_by_key[key]
            spans.append(BlockSpan(block_key=key, label=label, location=location, start=start, end=end))

    return LocateResult(run_id=run_dir.name, prompt_bytes=prompt_bytes, spans=spans)


def _new_block_text(span: BlockSpan, prompts_dir: Path) -> str | None:
    """The text `span`'s block would render as under `prompts_dir`.

    ``None`` when ``prompts_dir`` supplies nothing for this block — the
    caller reports that as "unchanged", never as an error; a partial
    ``--prompts`` directory is the documented, ordinary case.

    Applies the same transform every renderer applies before joining: a
    plain ``.strip()`` of the file, except the one curated-extract block
    (`portal-verb-grammar`), which re-runs the exact section extraction
    the live wake uses (:func:`brr.prompts._extract_markdown_sections`) so
    a substituted `portals.md` is sliced the same way a real wake would
    slice it.
    """
    name = Path(span.location).name
    candidate = prompts_dir / name
    if not candidate.exists():
        return None
    text = candidate.read_text(encoding="utf-8")
    if span.block_key in _CURATED_EXTRACT_KEYS:
        return _extract_markdown_sections(text, _PORTAL_VERB_GRAMMAR_HEADINGS)
    return text.strip()


@dataclass(frozen=True)
class BlockDelta:
    """One block's substitution outcome — always emitted, changed or not."""

    block_key: str
    label: str
    location: str
    status: str          # "substituted" | "unchanged" | "computed" | "not-present"
    old_bytes: int
    new_bytes: int | None  # None when status != "substituted"
    old_text: str | None = None
    new_text: str | None = None


@dataclass(frozen=True)
class ReplayResult:
    """The full roster + spliced text for one `replay` invocation."""

    run_id: str
    deltas: list[BlockDelta]
    unmatched_files: list[str]  # basenames in --prompts dir matching no block
    spliced_bytes: bytes
    total_delta: int


def plan_replacement(
    run_dir: Path,
    prompts_dir: Path,
    *,
    block_filter: "list[str] | None" = None,
) -> ReplayResult:
    """Locate + substitute in one call — the whole of `brnrd replay`'s work.

    ``block_filter`` (``--block``) restricts substitution to the named
    ``block_key``(s); every other file-backed block still reports as
    ``"unchanged"`` in the roster (never silently omitted — see the
    module's "always print the substitution roster" requirement).
    """
    located = locate_captured_prompt(run_dir)
    wanted = set(block_filter) if block_filter else None

    deltas: list[BlockDelta] = []
    pieces: list[bytes] = []
    matched_names: set[str] = set()
    total_delta = 0

    for span in located.spans:
        # A mounted span's bytes never lived in `prompt.md` — its baseline is
        # the sidecar text `locate_captured_prompt` already verified against
        # the manifest. Every other span slices `prompt.md` exactly as before.
        old_piece = (
            span.mounted_text.encode("utf-8") if span.is_mounted
            else located.prompt_bytes[span.start:span.end]
        )
        old_text = old_piece.decode("utf-8", errors="replace")
        old_len = len(old_piece)

        if not span.file_backed:
            deltas.append(BlockDelta(
                block_key=span.block_key, label=span.label, location=span.location,
                status="computed", old_bytes=old_len, new_bytes=None,
            ))
            pieces.append(old_piece)
            continue

        name = Path(span.location).name
        in_scope = wanted is None or span.block_key in wanted
        new_text = _new_block_text(span, prompts_dir) if in_scope else None
        if new_text is None:
            deltas.append(BlockDelta(
                block_key=span.block_key, label=span.label, location=span.location,
                status="unchanged", old_bytes=old_len, new_bytes=None,
            ))
            pieces.append(old_piece)
            continue

        matched_names.add(name)
        new_bytes_val = new_text.encode("utf-8")
        deltas.append(BlockDelta(
            block_key=span.block_key, label=span.label, location=span.location,
            status="substituted", old_bytes=old_len, new_bytes=len(new_bytes_val),
            old_text=old_text, new_text=new_text,
        ))
        total_delta += len(new_bytes_val) - old_len
        pieces.append(new_bytes_val)

    spliced = SEP.join(pieces)

    supplied_names = {p.name for p in prompts_dir.glob("*.md")} if prompts_dir.is_dir() else set()
    unmatched = sorted(supplied_names - matched_names)

    return ReplayResult(
        run_id=located.run_id,
        deltas=deltas,
        unmatched_files=unmatched,
        spliced_bytes=spliced,
        total_delta=total_delta,
    )


def format_human(result: ReplayResult) -> str:
    """The default, readable report: roster first, then a diff per change."""
    import difflib

    lines: list[str] = [f"# replay — {result.run_id}", ""]
    lines.append("## Substitution roster")
    for d in result.deltas:
        if d.status == "substituted":
            sign = "+" if d.new_bytes >= d.old_bytes else ""
            lines.append(
                f"  ~ {d.block_key:<24} {d.old_bytes:>7}B -> {d.new_bytes:>7}B "
                f"({sign}{d.new_bytes - d.old_bytes}B)  {d.location}"
            )
        elif d.status == "unchanged":
            lines.append(f"  = {d.block_key:<24} {d.old_bytes:>7}B            (unchanged)  {d.location}")
        elif d.status == "computed":
            lines.append(f"  · {d.block_key:<24} {d.old_bytes:>7}B            (computed, not file-backed)")
        else:
            lines.append(f"  ? {d.block_key:<24} {d.location} — not present in this wake")
    lines.append("")
    lines.append(f"total delta: {result.total_delta:+d}B")

    if result.unmatched_files:
        lines.append("")
        lines.append("## Files in --prompts that matched no block")
        for name in result.unmatched_files:
            lines.append(f"  ? {name}")

    changed = [d for d in result.deltas if d.status == "substituted"]
    if changed:
        lines.append("")
        lines.append("## Diff")
        for d in changed:
            lines.append(f"### {d.block_key} ({d.location})")
            diff = difflib.unified_diff(
                (d.old_text or "").splitlines(keepends=True),
                (d.new_text or "").splitlines(keepends=True),
                fromfile=f"captured/{d.block_key}", tofile=f"replay/{d.block_key}",
            )
            lines.append("".join(diff).rstrip("\n") or "(no textual diff — byte-identical)")
    return "\n".join(lines) + "\n"


def to_dict(result: ReplayResult) -> dict[str, Any]:
    """The `--json` shape — deltas plus the roster, no prose."""
    return {
        "run_id": result.run_id,
        "total_delta": result.total_delta,
        "unmatched_files": result.unmatched_files,
        "blocks": [
            {
                "block_key": d.block_key,
                "label": d.label,
                "location": d.location,
                "status": d.status,
                "old_bytes": d.old_bytes,
                "new_bytes": d.new_bytes,
            }
            for d in result.deltas
        ],
    }
