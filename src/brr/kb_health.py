"""KB graph statistics for the maintenance pass.

The deterministic preflight (:mod:`brr.kb_preflight`) tells the
LLM-driven maintenance pass *what's wrong*. This module tells it
*how the graph is shaped*: how many pages by kind, who's heavily
referenced, which pages might be drifting toward orphan status, and
how big the chronological log is getting.

The stats are advisory. The maintenance pass uses them to decide
whether the kb needs splitting, compressing, or just a small touch —
without scanning every page itself.

The module is deliberately stdlib-only and side-effect-free; it
reads from disk once and returns a frozen ``GraphStats`` snapshot.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


# Page-name prefix → kind. Pages that don't match any prefix get
# bucketed under ``"other"`` so the count is exhaustive. The order
# matters only for stability of the output, not for correctness.
_KIND_PREFIXES: tuple[tuple[str, str], ...] = (
    ("subject-", "subject"),
    ("decision-", "decision"),
    ("plan-", "plan"),
    ("design-", "design"),
    ("research-", "research"),
    ("notes-", "notes"),
    ("deck-", "deck"),
    ("review-", "review"),
)

# Names that get their own bucket because they're structural, not
# regular content pages. ``index.md`` is the entry point; ``log.md``
# is the chronological narrative.
_STRUCTURAL_NAMES: dict[str, str] = {
    "index.md": "index",
    "log.md": "log",
}

# How many "top" entries to surface in each stats list. Five is the
# sweet spot for prompt injection: enough signal, short enough that
# it doesn't dominate the maintenance prompt.
_TOP_N = 5


# Same link-extraction shape as kb_preflight. Duplicated here so this
# module stays loose-coupled — both regexes are small and stable.
_INLINE_LINK_RE = re.compile(r"\[(?:[^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_REFERENCE_LINK_RE = re.compile(r"^\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
_LOG_ENTRY_RE = re.compile(r"^## \[", re.MULTILINE)


@dataclass(frozen=True)
class GraphStats:
    """A snapshot of the kb graph's shape at scan time.

    All collections are ordered for reproducible prompt output:
    ``pages_by_kind`` follows the order in :data:`_KIND_PREFIXES`
    (with ``index`` and ``log`` last), and the ``top_*`` lists are
    sorted descending by their numeric key.
    """

    total_pages: int
    total_bytes: int
    pages_by_kind: dict[str, int] = field(default_factory=dict)
    largest_pages: list[tuple[str, int]] = field(default_factory=list)
    in_degree_top: list[tuple[str, int]] = field(default_factory=list)
    peer_orphans: list[str] = field(default_factory=list)
    log_entry_count: int = 0
    log_bytes: int = 0


def compute_graph_stats(repo_root: Path) -> GraphStats:
    """Return a :class:`GraphStats` snapshot for ``repo_root/kb``.

    Returns an all-zero ``GraphStats`` when ``kb/`` does not exist;
    the maintenance pass can format that block trivially and the
    skip-fast contract from :mod:`brr.kb_preflight` still drops it
    when ``kb/`` was untouched.
    """
    repo_root = repo_root.resolve()
    kb_dir = repo_root / "kb"
    if not kb_dir.is_dir():
        return GraphStats(total_pages=0, total_bytes=0)

    pages = sorted(p for p in kb_dir.rglob("*.md") if p.is_file())
    if not pages:
        return GraphStats(total_pages=0, total_bytes=0)

    # Total bytes excludes log.md because the log is by-design
    # monotonically-growing chronological narrative; mixing it into
    # the body-size total drowns out the synthesis-layer signal.
    total_bytes = 0
    sizes: dict[str, int] = {}
    kind_counter: Counter[str] = Counter()
    log_bytes = 0
    log_entry_count = 0

    for page in pages:
        rel = page.relative_to(kb_dir).as_posix()
        size = page.stat().st_size
        sizes[rel] = size
        kind_counter[_classify(page.name)] += 1
        if page.name == "log.md":
            log_bytes = size
            text = page.read_text(encoding="utf-8")
            log_entry_count = max(0, len(_LOG_ENTRY_RE.split(text)) - 1)
        else:
            total_bytes += size

    in_degree, kb_pages = _compute_in_degree(kb_dir, pages)

    largest = sorted(
        ((f"kb/{r}", s) for r, s in sizes.items() if r != "log.md"),
        key=lambda kv: (-kv[1], kv[0]),
    )[:_TOP_N]

    in_degree_top = sorted(
        ((f"kb/{r}", d) for r, d in in_degree.items()),
        key=lambda kv: (-kv[1], kv[0]),
    )[:_TOP_N]

    # Peer-orphan: a page is reachable from the index (otherwise the
    # preflight catches it) but no peer kb page links to it. The
    # index entry alone suggests material that nothing else
    # references — a candidate for absorption into a subject hub.
    peer_orphans = sorted(
        f"kb/{r}"
        for r, d in in_degree.items()
        if d == 0 and r != "index.md"
    )

    return GraphStats(
        total_pages=len(kb_pages),
        total_bytes=total_bytes,
        pages_by_kind=dict(_ordered_kinds(kind_counter)),
        largest_pages=largest,
        in_degree_top=in_degree_top,
        peer_orphans=peer_orphans,
        log_entry_count=log_entry_count,
        log_bytes=log_bytes,
    )


def format_graph_stats(stats: GraphStats) -> str:
    """Render *stats* as a Markdown block for prompt injection.

    Returns ``""`` for an empty kb so the maintenance prompt can drop
    the block entirely. Otherwise produces a compact "Graph stats"
    section with totals, top-N lists, and the log breakdown.
    """
    if stats.total_pages == 0:
        return ""

    lines = ["## Graph stats (kb shape)", ""]
    lines.append(
        f"- {stats.total_pages} pages, {stats.total_bytes} bytes "
        "(excluding log.md)"
    )
    if stats.pages_by_kind:
        kinds = ", ".join(
            f"{kind}={count}"
            for kind, count in stats.pages_by_kind.items()
            if count > 0
        )
        lines.append(f"- by kind: {kinds}")
    if stats.log_entry_count or stats.log_bytes:
        lines.append(
            f"- kb/log.md: {stats.log_entry_count} entries, "
            f"{stats.log_bytes} bytes"
        )
    if stats.largest_pages:
        lines.append("- largest pages:")
        for path, size in stats.largest_pages:
            lines.append(f"  - {path} — {size} bytes")
    if stats.in_degree_top:
        lines.append("- most-referenced pages (peer in-degree):")
        for path, degree in stats.in_degree_top:
            lines.append(f"  - {path} — {degree} inbound links")
    if stats.peer_orphans:
        lines.append(
            "- peer orphans (indexed but no peer page links to them):"
        )
        for path in stats.peer_orphans:
            lines.append(f"  - {path}")
    lines.append("")
    return "\n".join(lines)


# ── Internals ────────────────────────────────────────────────────────


def _classify(name: str) -> str:
    """Bucket a page filename into a kind label."""
    if name in _STRUCTURAL_NAMES:
        return _STRUCTURAL_NAMES[name]
    for prefix, kind in _KIND_PREFIXES:
        if name.startswith(prefix):
            return kind
    return "other"


def _ordered_kinds(counter: Counter[str]) -> list[tuple[str, int]]:
    """Yield (kind, count) pairs in the canonical reporting order.

    Subject hubs first, then decisions, plans, designs, research,
    notes, decks, reviews, anything else, then structural index and
    log. The ordering keeps the prompt-injected block stable across
    runs even when the counter dict's insertion order shifts.
    """
    order: list[str] = [kind for _, kind in _KIND_PREFIXES]
    order.append("other")
    order.extend(_STRUCTURAL_NAMES.values())
    return [(kind, counter[kind]) for kind in order if counter[kind]]


def _compute_in_degree(
    kb_dir: Path, pages: list[Path],
) -> tuple[dict[str, int], set[str]]:
    """Return ``(in_degree_by_rel_path, kb_page_rel_paths_set)``.

    In-degree counts inbound links from other kb pages. Links from
    ``index.md`` and self-links are excluded so the metric reflects
    how much the rest of the kb leans on each page. ``log.md``
    targets are skipped too (the log links chronicle past work and
    can keep references to slashed pages).
    """
    kb_pages = {p.relative_to(kb_dir).as_posix() for p in pages}
    in_degree: dict[str, int] = {p: 0 for p in kb_pages}
    for page in pages:
        if page.name in {"index.md", "log.md"}:
            continue
        source_rel = page.relative_to(kb_dir).as_posix()
        text = page.read_text(encoding="utf-8")
        for raw in _link_targets(text):
            target_rel = _kb_relative_target(page, kb_dir, raw)
            if target_rel is None:
                continue
            if target_rel == source_rel:
                continue
            if target_rel in in_degree:
                in_degree[target_rel] += 1
    return in_degree, kb_pages


def _link_targets(text: str):
    for match in _INLINE_LINK_RE.finditer(text):
        yield match.group(1)
    for match in _REFERENCE_LINK_RE.finditer(text):
        yield match.group(1)


def _kb_relative_target(
    source: Path, kb_dir: Path, raw: str,
) -> str | None:
    """Resolve *raw* relative to *source* and return its kb-relative
    path when it lands inside ``kb_dir``, else ``None``."""
    if raw.startswith(("http://", "https://", "mailto:")):
        return None
    target = raw.split("#", 1)[0]
    if not target:
        return None
    resolved = (source.parent / target).resolve()
    try:
        rel = resolved.relative_to(kb_dir)
    except ValueError:
        return None
    return rel.as_posix()
