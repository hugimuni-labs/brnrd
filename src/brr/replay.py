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
run in this repo has ``boot-score.json``'s ``body.mounted: true``**
(``boot.mount`` is on) — meaning ``prompt.md`` is the short kernel+trailer
text and the file-backed blocks (``run.md``, ``weave.md``,
``daemon-substrate.md``, the very files this tool exists to let someone
edit) never enter its bytes at all; they ride a resumed session transcript
instead (:mod:`brr.transcript`). The self-check above refuses those runs
outright rather than reporting a zero-byte "no change" that would in fact
be true only by accident. See the rung-1 report for the sizing of this gap
and what would close it.
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
    """One manifest block, located as an exact byte range in `prompt.md`."""

    block_key: str
    label: str
    location: str
    start: int
    end: int

    @property
    def file_backed(self) -> bool:
        return self.location != _COMPUTED_LOCATION


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

    ``prompts._collect_preamble_contracts`` (the function that builds the
    manifest rows for the file-backed preamble blocks) lists ``diffense``
    and ``introspection`` right after ``portal-verb-grammar`` — before the
    inject stack (identity-core, dominion, work-surface, ...). But
    ``prompts._join_prompt_parts`` — the function that actually joins the
    rendered text — appends those same two blocks *after* the inject
    stack, immediately before the trailer (Run Context Bundle). Read both
    functions side by side and the divergence is exactly these two keys;
    everything else is already in lockstep (``_collect_preamble_contracts``
    says as much in its own comment).

    On a wake where both toggles are off (the overwhelming common case —
    they render 0 bytes and are absent from the manifest as `present:
    false`) this reorder is a no-op, since a walk skips zero-byte entries
    either way. It only matters, and is only tested here, when one or both
    toggles are on.
    """
    head = [c for c in contracts if c["block_key"] not in _TAIL_SPECIAL_KEYS and c["block_key"] != _TRAILER_KEY]
    tail_special = [c for c in contracts if c["block_key"] in _TAIL_SPECIAL_KEYS]
    trailer = [c for c in contracts if c["block_key"] == _TRAILER_KEY]
    return head + tail_special + trailer


def locate_captured_prompt(run_dir: Path) -> LocateResult:
    """Locate every manifest block as an exact byte span in `prompt.md`.

    Raises :class:`ReplayLocateError` — never returns a partial or
    best-guess result — when:

    - ``prompt.md`` or ``boot-score.json`` is missing from ``run_dir``;
    - the recorded ``prompt_bytes`` total disagrees with the file's actual
      size (the fast top-level check — this alone catches every mounted
      wake in this repo's own archive, since a mounted wake's manifest
      still records the *unmounted* size of blocks that never entered the
      prose at all);
    - a computed offset boundary does not hold the expected ``"\\n\\n"``
      glue (a slower, block-precise version of the same check, for the
      case where the totals happen to agree but a block's position does
      not — e.g. a hand-edited or truncated capture).
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

    recorded_total = score.get("prompt_bytes")
    if recorded_total is not None and recorded_total != len(prompt_bytes):
        raise ReplayLocateError(
            f"boot-score.json records prompt_bytes={recorded_total} but the captured "
            f"prompt.md is {len(prompt_bytes)} bytes — refusing rather than guessing "
            "at the mismatch. The most likely cause: this wake was rendered with "
            "`boot.mount` on, so file-backed blocks (run.md, weave.md, "
            "daemon-substrate.md, ...) were seeded as a resumed session transcript "
            "instead of entering prompt.md's own text, and the manifest still "
            "records their *unmounted* size."
        )

    contracts = _true_render_order(score.get("contracts") or [])
    present = [c for c in contracts if c.get("present") and c.get("bytes")]

    spans: list[BlockSpan] = []
    offset = 0
    for idx, c in enumerate(present):
        n = int(c["bytes"])
        end = offset + n
        if end > len(prompt_bytes):
            raise ReplayLocateError(
                f"block {c['block_key']!r}: computed span [{offset}, {end}) runs past "
                f"the captured prompt's end ({len(prompt_bytes)} bytes) — the recorded "
                "block layout does not reconcile with this capture; refusing"
            )
        spans.append(BlockSpan(
            block_key=c["block_key"],
            label=c.get("label", c["block_key"]),
            location=c.get("location", _COMPUTED_LOCATION),
            start=offset,
            end=end,
        ))
        offset = end
        if idx + 1 < len(present):
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
        old_text = located.prompt_bytes[span.start:span.end].decode("utf-8", errors="replace")
        if not span.file_backed:
            deltas.append(BlockDelta(
                block_key=span.block_key, label=span.label, location=span.location,
                status="computed", old_bytes=span.end - span.start, new_bytes=None,
            ))
            pieces.append(located.prompt_bytes[span.start:span.end])
            continue

        name = Path(span.location).name
        in_scope = wanted is None or span.block_key in wanted
        new_text = _new_block_text(span, prompts_dir) if in_scope else None
        if new_text is None:
            deltas.append(BlockDelta(
                block_key=span.block_key, label=span.label, location=span.location,
                status="unchanged", old_bytes=span.end - span.start, new_bytes=None,
            ))
            pieces.append(located.prompt_bytes[span.start:span.end])
            continue

        matched_names.add(name)
        new_bytes_val = new_text.encode("utf-8")
        old_len = span.end - span.start
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
