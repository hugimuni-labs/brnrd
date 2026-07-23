"""Deterministic kb consistency scan, injected into the resident's wake.

The preflight reads the on-disk shape of ``kb/`` and produces a list
of structured findings. It does not modify anything; the resident sees
the findings on wake (via ``prompts._build_kb_health_block``) and folds
fixes into its own thought.

Skip-fast contract: when the preflight returns no findings, the wake
block is dropped entirely — a clean preflight is silent, never a tax on
every wake.

(Earlier versions fed an LLM-driven kb-maintenance prompt spawned as a
separate post-task pass; that pass was removed 2026-06-08 in favour of
the resident curating the shared kb as part of its thought. See
``kb/design-agent-dominion.md`` and ``kb/subject-daemon.md``.)

Each finding carries a ``severity``:

- ``error``: a structural inconsistency the scanner is confident
  about. Existing types: ``missing-from-index``, ``stale-index-entry``,
  ``broken-link``, ``missing-index``, ``log-ordering-violation``.
- ``warning``: a heuristic advisory worth acting on when proportional.
  Types: ``oversized-page``, ``missing-status-marker``,
  ``revision-history-heavy``.
- ``info``: a soft hint. Types: ``recent-log-budget-exceeded``,
  ``hub-coverage``, ``proposal-scaffolding``.

Contradictions with the log and similar judgement calls aren't the
scanner's job — they need synthesis the resident does directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Pages that are entry points by definition — being absent from the
# index would be a contradiction (index can't index itself), and the
# log is curated narrative, not a target page in the index sense.
_INDEX_BASENAMES_EXEMPT_FROM_INDEX = frozenset({"index.md", "log.md"})

# Page-size advisory threshold. Pages this big are hard to read in one
# sitting and usually indicate accumulated layers — either split into
# a hub plus daughter pages, or compress to current state. ``log.md``
# grows monotonically and is exempt.
_OVERSIZED_PAGE_BYTES = 32_768

# Names that don't make sense to size-check or to require a Status
# line. The index is structural; the log is chronological narrative;
# the wiki/notes pages are by-design discursive.
_OVERSIZED_PAGE_EXEMPT = frozenset({"log.md"})

# Page-name prefixes that should carry a ``Status:`` line near the
# top so a cold reader knows whether the page is live or historical.
_STATUS_REQUIRED_PREFIXES: tuple[str, ...] = (
    "plan-", "design-", "decision-", "deck-",
)

# How many non-blank lines we'll look at to find a Status marker
# before giving up. Generous enough to allow a paragraph of preamble.
_STATUS_SEARCH_LINES = 12

# Soft mirror of ``prompts._MAX_LOG_BYTES``. Duplicated here so the
# preflight module stays free of prompt-layer imports; if they ever
# drift, the maintenance pass will surface the discrepancy.
_RECENT_LOG_BYTES = 4096

# Heuristic for running-diff bloat. A single dated lineage breadcrumb
# is the *recommended* shape (AGENTS.md → "State first, history in
# git"), so we count match instances across multiple signals — a page
# crossing the threshold is reliably a scrapbook, not just a page
# with a healthy breadcrumb. ``## Lineage``-style sections with terse
# dated bullets stay clean because the bullets aren't labelled
# "amendment" / "revision" / "note".
_REVISION_HEAVY_THRESHOLD = 5
_REVISION_HEAVY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # A "Revision history" or "Amendments" section header at the top
    # of a page — AGENTS.md asks for a single Lineage breadcrumb at
    # the bottom instead.
    re.compile(
        r"^## (Revision history|Amendments)\b",
        re.MULTILINE | re.IGNORECASE,
    ),
    # Dated bullets explicitly labelled "amendment" / "revision" /
    # "note". A handful of these in one page is the running-diff
    # pattern; a single Lineage bullet without the label is fine.
    re.compile(
        r"^- \*\*\d{4}-\d{2}-\d{2} (amendment|revision|note)\b",
        re.MULTILINE | re.IGNORECASE,
    ),
    # Inline "the YYYY-MM-DD amendment" prose mentioning past
    # amendments inline rather than rewriting to current state.
    re.compile(r"\bthe \d{4}-\d{2}-\d{2} amendment\b", re.IGNORECASE),
    # Dated `## [YYYY-MM-DD]` section headers outside of log.md
    # (those belong in the log; here they're inline changelog).
    re.compile(r"^## \[\d{4}-\d{2}-\d{2}\]", re.MULTILINE),
    # Inline "the old X" mentions and "previously / originally" —
    # one of these is fine for context, several is bloat.
    re.compile(r"\bthe old\b", re.IGNORECASE),
    re.compile(r"\bpreviously\b", re.IGNORECASE),
    re.compile(r"\boriginally\b", re.IGNORECASE),
    re.compile(r"^>\s+\*\*Superseded\b", re.MULTILINE),
)

# Hub-coverage advisory: an index.md section with this many design /
# plan / decision / deck pages *and no* subject-*.md page is a soft
# nudge to synthesise a hub. The threshold is deliberately low (two
# load-bearing artifacts is enough material to be worth a paragraph
# of synthesis) so the nudge fires before the section becomes
# unwieldy, not after.
_HUB_COVERAGE_THRESHOLD = 2
_HUB_COVERAGE_ARTIFACT_PREFIXES: tuple[str, ...] = (
    "design-", "plan-", "decision-", "deck-",
)

# Header signatures that read as proposal scaffolding on an accepted
# or shipped page. A single ``## Goals`` block on a shipped design is
# usually fine; two or more proposal-shape headers together is the
# pattern that warrants compression to current-state synthesis.
_PROPOSAL_SCAFFOLDING_THRESHOLD = 2
_PROPOSAL_SCAFFOLDING_HEADERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^##\s+Goals\b", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^##\s+Non-?goals\b", re.MULTILINE | re.IGNORECASE),
    re.compile(
        r"^##\s+Alternatives(?:\s+considered)?\b",
        re.MULTILINE | re.IGNORECASE,
    ),
    re.compile(r"^##\s+Why this PR\b", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^##\s+Proposed approach\b", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^##\s+Open questions\b", re.MULTILINE | re.IGNORECASE),
)

# Status values that indicate the page is no longer in-flight, and the
# proposal scaffolding should have been compressed by now.
_PROPOSAL_SCAFFOLDING_LANDED_STATUSES: tuple[str, ...] = (
    "accepted", "shipped",
)

# Severity rank for stable sort: errors first, then warnings, then
# informational hints.
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}

# Match Markdown H2 headings used as index.md section dividers. The
# pattern intentionally captures everything after ``## `` so the caller
# can strip italic / bracketed decoration like ``*(paused)*``.
_H2_HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$", re.MULTILINE)
# Strip italic-wrapped suffix decoration commonly used in index.md to
# annotate a section's status, e.g. ``Fleet & overlays *(paused …)*``.
_SECTION_DECORATION_RE = re.compile(r"\s*\*\([^)]*\)\*\s*$")


# Match Markdown inline links like ``[text](path)`` and reference-style
# links ``[text]: path``. We capture the URL/path component; anchors
# (``#section``) and titles (``"hover"``) get stripped afterwards.
_INLINE_LINK_RE = re.compile(r"\[(?:[^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_REFERENCE_LINK_RE = re.compile(r"^\[[^\]]+\]:\s*(\S+)", re.MULTILINE)

_LOG_ENTRY_RE = re.compile(r"^## \[", re.MULTILINE)

# Match the dated form of a log heading — ``## [YYYY-MM-DD] ...`` — used
# to pull the date out of an entry whose text starts at a ``_LOG_ENTRY_RE``
# boundary. A plain ``## Title`` (no bracket) never reaches
# ``_LOG_ENTRY_RE`` in the first place; a bracketed-but-undated heading
# (rare, malformed) fails this and is treated as undated: skipped by the
# ordering check, not an error.
_LOG_DATE_HEADING_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]")


@dataclass(frozen=True)
class Finding:
    """A single deterministic kb consistency observation.

    ``severity`` is one of ``error`` / ``warning`` / ``info``. Errors
    are structural inconsistencies the scanner is confident about;
    warnings are heuristic advisories the maintenance pass should
    act on when proportional; info is a soft hint.
    """

    type: str
    target: str
    description: str
    severity: str = "error"

    def render(self) -> str:
        """Return the bullet form used inside the maintenance prompt.

        ``error`` findings render with their type prominent so the
        existing prompt format stays familiar. Advisories prefix the
        severity so a reader can triage at a glance.
        """
        sev = "" if self.severity == "error" else f" [{self.severity}]"
        return f"- **{self.type}**{sev} `{self.target}` — {self.description}"


def scan(repo_root: Path, kb_dir: Path | None = None) -> list[Finding]:
    """Return all kb consistency findings for *repo_root*.

    *kb_dir* is the directory actually holding the pages. Defaults to
    ``repo_root / "kb"`` (the repo-committed shape); pass the resolved
    home-knowledge path explicitly for a repo that dogfoods that shape
    instead (see ``knowledge.active_kb_dir`` / ``AGENTS.md`` →
    "Knowledge base" → "Where the kb lives"). *kb_dir* need not live
    inside *repo_root* at all — cross-kb links into the project's own
    code (``../src/...``-style, written back when the kb was still
    ``repo_root/kb``) are re-resolved against that historical root as a
    fallback when they don't resolve as a plain sibling of *kb_dir*, so
    an external kb doesn't spuriously report every code link as broken.

    Returns an empty list when the kb directory is missing or when the
    kb is fully consistent. Findings are stable-sorted by
    ``(severity_rank, type, target)`` so the formatted output is
    reproducible and structural errors appear before advisories.
    """
    repo_root = repo_root.resolve()
    kb_dir = kb_dir.resolve() if kb_dir is not None else repo_root / "kb"
    if not kb_dir.is_dir():
        return []

    pages = sorted(p for p in kb_dir.rglob("*.md") if p.is_file())
    index_path = kb_dir / "index.md"

    findings: list[Finding] = []
    findings.extend(_check_index_coverage(kb_dir, index_path, pages))
    findings.extend(_check_index_targets_exist(repo_root, kb_dir, index_path))
    findings.extend(_check_broken_links(repo_root, kb_dir, pages))
    findings.extend(_check_oversized_pages(kb_dir, pages))
    findings.extend(_check_missing_status_marker(kb_dir, pages))
    findings.extend(_check_revision_history_heavy(kb_dir, pages))
    findings.extend(_check_recent_log_budget(kb_dir))
    findings.extend(_check_log_ordering(kb_dir))
    findings.extend(_check_hub_coverage(kb_dir, index_path))
    findings.extend(_check_proposal_scaffolding(kb_dir, pages))

    findings.sort(key=lambda f: (
        _SEVERITY_RANK.get(f.severity, 99),
        f.type,
        f.target,
    ))
    return findings


def format_findings(findings: list[Finding]) -> str:
    """Render *findings* as a Markdown block for prompt injection.

    Returns ``""`` when there's nothing to inject — the caller should
    use that as the signal to either skip the pass or use the bare
    base prompt.
    """
    if not findings:
        return ""
    bullets = "\n".join(f.render() for f in findings)
    return (
        "## Findings (deterministic preflight)\n\n"
        f"{bullets}\n"
    )


# ── Internals ────────────────────────────────────────────────────────


def _check_index_coverage(
    kb_dir: Path, index_path: Path, pages: list[Path],
) -> list[Finding]:
    """Every kb page (except index/log) must be linked from index.md."""
    if not index_path.exists():
        return [Finding(
            type="missing-index",
            target="kb/index.md",
            description=(
                "kb/index.md does not exist; it is the canonical entry "
                "point for the kb graph and must list every page."
            ),
        )]
    indexed = _kb_targets_linked_from(index_path, kb_dir)
    findings: list[Finding] = []
    for page in pages:
        if page == index_path:
            continue
        if page.name in _INDEX_BASENAMES_EXEMPT_FROM_INDEX:
            continue
        rel = page.relative_to(kb_dir).as_posix()
        if rel not in indexed:
            findings.append(Finding(
                type="missing-from-index",
                target=f"kb/{rel}",
                description=(
                    "page exists in kb/ but no link from kb/index.md "
                    "points to it; add a one-line entry under the "
                    "appropriate subject heading."
                ),
            ))
    return findings


def _check_index_targets_exist(
    repo_root: Path, kb_dir: Path, index_path: Path,
) -> list[Finding]:
    """Every relative link in index.md must resolve to a real file."""
    if not index_path.exists():
        return []
    findings: list[Finding] = []
    for raw in _markdown_link_targets(index_path):
        if _is_external(raw):
            continue
        resolved = _resolve_relative(index_path, raw, repo_root=repo_root, kb_dir=kb_dir)
        if resolved is None:
            continue
        in_project = resolved.is_relative_to(repo_root)
        in_kb = resolved.is_relative_to(kb_dir)
        if (in_project or in_kb) and not resolved.exists():
            base = repo_root if in_project else kb_dir
            findings.append(Finding(
                type="stale-index-entry",
                target=str(resolved.relative_to(base)),
                description=(
                    "kb/index.md links to this path but no file exists; "
                    "remove the entry or fix the link."
                ),
            ))
    return findings


def _check_broken_links(
    repo_root: Path, kb_dir: Path, pages: list[Path],
) -> list[Finding]:
    """Any relative link in a kb page must resolve to a real file."""
    findings: list[Finding] = []
    for page in pages:
        if page.name == "log.md":
            # The log is curated narrative; older entries may mention
            # paths that have since been removed and that's fine.
            continue
        for raw in _markdown_link_targets(page):
            if _is_external(raw):
                continue
            resolved = _resolve_relative(page, raw, repo_root=repo_root, kb_dir=kb_dir)
            if resolved is None:
                continue
            if not (resolved.is_relative_to(repo_root) or resolved.is_relative_to(kb_dir)):
                continue
            if resolved.exists():
                continue
            findings.append(Finding(
                type="broken-link",
                target=f"kb/{page.relative_to(kb_dir).as_posix()} → {raw}",
                description=(
                    "link points to a path that doesn't exist; either "
                    "remove the link or fix the target."
                ),
            ))
    return findings


def _check_oversized_pages(kb_dir: Path, pages: list[Path]) -> list[Finding]:
    """Pages bigger than the readability threshold get a soft advisory.

    The kb is a graph of pages a cold reader should be able to hold
    in one sitting. Past ~32KB the page is usually doing more than
    one thing — either it's a hub that's grown daughter material, or
    it's accumulated running-diff bloat. Either way, the maintenance
    pass should consider splitting or compressing.

    ``log.md`` is exempt because it's append-only chronological
    narrative; size grows monotonically and that is the point.
    """
    findings: list[Finding] = []
    for page in pages:
        if page.name in _OVERSIZED_PAGE_EXEMPT:
            continue
        size = page.stat().st_size
        if size <= _OVERSIZED_PAGE_BYTES:
            continue
        rel = page.relative_to(kb_dir).as_posix()
        findings.append(Finding(
            type="oversized-page",
            target=f"kb/{rel}",
            description=(
                f"page is {size} bytes (threshold {_OVERSIZED_PAGE_BYTES}); "
                "consider splitting into a hub plus daughter pages, or "
                "compressing accumulated history into a lineage breadcrumb."
            ),
            severity="warning",
        ))
    return findings


def _check_missing_status_marker(
    kb_dir: Path, pages: list[Path],
) -> list[Finding]:
    """Plan, design, decision, and deck pages must carry a Status line.

    A cold reader needs to know whether a page is live, shipped, or
    historical at a glance. The Status line lives near the top so it
    can't be missed; we scan the first
    :data:`_STATUS_SEARCH_LINES` non-blank lines.
    """
    findings: list[Finding] = []
    for page in pages:
        name = page.name
        if not name.startswith(_STATUS_REQUIRED_PREFIXES):
            continue
        if not _has_status_marker(page):
            rel = page.relative_to(kb_dir).as_posix()
            findings.append(Finding(
                type="missing-status-marker",
                target=f"kb/{rel}",
                description=(
                    "page lacks a `Status:` line near the top; add "
                    "`Status: active|accepted on YYYY-MM-DD|"
                    "shipped on YYYY-MM-DD|superseded by <link> on "
                    "YYYY-MM-DD` so a cold reader can triage at a "
                    "glance."
                ),
                severity="warning",
            ))
    return findings


def _check_revision_history_heavy(
    kb_dir: Path, pages: list[Path],
) -> list[Finding]:
    """Pages that read like running diffs of their own past wording.

    Multiple dated-amendment bullets, dated section headers, or
    "Superseded step" blockquotes inside a single page usually mean
    the page is accumulating a changelog instead of being rewritten
    to current state. AGENTS.md → "State first, history in git" asks
    that this be compressed to a lineage breadcrumb. ``log.md`` is
    exempt — it *is* the chronological narrative.
    """
    findings: list[Finding] = []
    for page in pages:
        if page.name == "log.md":
            continue
        text = page.read_text(encoding="utf-8")
        hits = sum(len(pat.findall(text)) for pat in _REVISION_HEAVY_PATTERNS)
        if hits < _REVISION_HEAVY_THRESHOLD:
            continue
        rel = page.relative_to(kb_dir).as_posix()
        findings.append(Finding(
            type="revision-history-heavy",
            target=f"kb/{rel}",
            description=(
                f"page shows {hits} signs of running-diff / amendment "
                "wording; rewrite to current state and leave a single "
                "lineage breadcrumb at the bottom (see AGENTS.md → "
                "\"State first, history in git\")."
            ),
            severity="warning",
        ))
    return findings


def _check_recent_log_budget(kb_dir: Path) -> list[Finding]:
    """Flag when the newest log entry is bigger than the prompt budget.

    Recent log entries are injected into every task prompt, capped
    by a byte budget (see :mod:`brr.prompts`). When the most recent
    entry alone exceeds that budget, it pushes older entries out
    silently and consumes context that would otherwise carry kb
    breadcrumbs. The maintenance pass should compress the entry.
    """
    log_path = kb_dir / "log.md"
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8")
    parts = _LOG_ENTRY_RE.split(text)
    if len(parts) <= 1:
        return []
    newest = f"## [{parts[-1]}".rstrip()
    size = len(newest.encode("utf-8"))
    if size <= _RECENT_LOG_BYTES:
        return []
    header = newest.splitlines()[0]
    return [Finding(
        type="recent-log-budget-exceeded",
        target=f"kb/log.md ({header})",
        description=(
            f"newest log entry is {size} bytes (prompt budget "
            f"{_RECENT_LOG_BYTES}); compress to its load-bearing "
            "facts so older entries still fit in the conversation "
            "context block."
        ),
        severity="info",
    )]


def _check_log_ordering(kb_dir: Path) -> list[Finding]:
    """Flag the first place kb/log.md's dated headings go non-ascending.

    ``AGENTS.md`` → "Read recent activity" mandates that ``kb/log.md``
    appends newest entries to the *bottom* (ascending by date), and
    ``prompts._read_recent_log`` walks the file bottom-up trusting
    exactly that. A hand-written wake once started *prepending* instead
    and ran both conventions for nine days — silently breaking the
    bottom-up walk for that whole span with no signal anywhere. This is
    the deterministic guard against recurrence: it doesn't touch the
    log's content or its data, only checks the ``## [YYYY-MM-DD]``
    heading date sequence.

    Only bracketed, dated headings participate; a plain ``## Title``
    heading is skipped, not an error (behaviour #1). Reports the first
    adjacent pair that breaks non-decreasing order — a heading lower in
    the file dated earlier than the heading above it — and stops there;
    later breaks in the same file aren't separate findings (behaviour
    #3). The consequence line models ``prompts._read_recent_log``'s
    bottom-up, byte-budget walk using this module's own
    ``_RECENT_LOG_BYTES`` mirror (the entry-count cap lives only in
    ``prompts`` and isn't duplicated here) to report how many entries
    positioned above the offending boundary fall outside what a wake
    would actually reach (behaviour #4).
    """
    log_path = kb_dir / "log.md"
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8")
    matches = list(_LOG_ENTRY_RE.finditer(text))
    if len(matches) < 2:
        return []

    # (line_no, date-or-None, utf8 byte size) for every "## [" entry, in
    # file order — dated and undated alike, since the byte-budget walk
    # below needs every entry's size regardless of whether it's dated.
    entries: list[tuple[int, str | None, int]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entry_text = text[start:end]
        date_match = _LOG_DATE_HEADING_RE.match(entry_text)
        date = date_match.group(1) if date_match else None
        line_no = text.count("\n", 0, start) + 1
        entries.append((line_no, date, len(entry_text.encode("utf-8"))))

    dated = [(i, ln, d) for i, (ln, d, _) in enumerate(entries) if d is not None]

    boundary = None
    for k in range(1, len(dated)):
        _, prev_line, prev_date = dated[k - 1]
        _, cur_line, cur_date = dated[k]
        if cur_date < prev_date:
            boundary = (prev_line, prev_date, cur_line, cur_date)
            break
    if boundary is None:
        return []
    prev_line, prev_date, cur_line, cur_date = boundary

    # Mirror prompts._read_recent_log's bottom-up walk: always keep the
    # newest (last) entry, then keep accepting older entries while the
    # running byte total stays within budget; the first entry that would
    # push it over budget, and everything older than it, is unreached.
    used = 0
    reached_from = len(entries)
    for i in range(len(entries) - 1, -1, -1):
        entry_bytes = entries[i][2]
        projected = used + entry_bytes
        if i != len(entries) - 1 and projected > _RECENT_LOG_BYTES:
            break
        used = projected
        reached_from = i
    unreachable = sum(
        1 for line_no, date, _ in entries[:reached_from]
        if date is not None and line_no < cur_line
    )
    noun = "entry" if unreachable == 1 else "entries"
    consequence = (
        f"{unreachable} {noun} above this boundary will not reach a wake "
        f"under the current recent-log byte budget ({_RECENT_LOG_BYTES} "
        "bytes)."
    )

    return [Finding(
        type="log-ordering-violation",
        target=f"kb/log.md:{cur_line}",
        description=(
            f"heading `[{cur_date}]` at line {cur_line} is dated earlier "
            f"than the heading `[{prev_date}]` above it at line "
            f"{prev_line}; kb/log.md must append newest entries to the "
            "bottom (AGENTS.md → \"Read recent activity\"). "
            + consequence
        ),
    )]


def _check_hub_coverage(kb_dir: Path, index_path: Path) -> list[Finding]:
    """Flag index sections that accumulate artifacts without a hub.

    An ``index.md`` H2 section that lists several design / plan /
    decision / deck pages but no ``subject-*.md`` page is a soft
    nudge to synthesise. The kb-shape rule
    (`AGENTS.md` → "Subject pages") asks for a hub when *new work
    plus existing related material* can form a useful synthesis;
    this advisory surfaces sections where the existing material has
    already accumulated past the comfortable threshold so the next
    agent working in that area knows to consider it.

    The advisory is intentionally one entry per *section*, not per
    page: the action is to write or extend a hub for that section,
    which is a single decision regardless of how many artifact pages
    sit under it.
    """
    if not index_path.exists():
        return []
    findings: list[Finding] = []
    for title, kb_targets in _index_sections(index_path, kb_dir):
        has_hub = any(t.startswith("subject-") for t in kb_targets)
        if has_hub:
            continue
        artifact_count = sum(
            1
            for t in kb_targets
            if t.startswith(_HUB_COVERAGE_ARTIFACT_PREFIXES)
        )
        if artifact_count < _HUB_COVERAGE_THRESHOLD:
            continue
        findings.append(Finding(
            type="hub-coverage",
            target=f"kb/index.md §{title}",
            description=(
                f"section has {artifact_count} design/plan/decision "
                "pages but no `subject-*.md` hub; consider writing a "
                "subject page that synthesises the current shape of "
                "this area and links to the artifacts as receipts "
                "(see AGENTS.md → \"Subject pages\")."
            ),
            severity="info",
        ))
    return findings


def _check_proposal_scaffolding(
    kb_dir: Path, pages: list[Path],
) -> list[Finding]:
    """Flag accepted/shipped pages still carrying proposal scaffolding.

    Goals / Non-goals / Alternatives considered / Why this PR /
    Proposed approach / Open questions sections all belong to a page
    while it is *in flight*. Once the design ships or the decision
    is accepted, those sections describe history rather than current
    state — AGENTS.md → "State first, history in git" asks that they
    be compressed into a one-line rationale plus, if warranted, a
    short ``## Rejected alternatives`` appendix.

    The advisory fires only when two or more of those headers are
    present together. A single ``## Goals`` block on a shipped
    design is usually a fine paragraph of context; the pattern that
    matters is the *retained proposal shape*.
    """
    findings: list[Finding] = []
    for page in pages:
        status = _status_marker_value(page)
        if status is None:
            continue
        if not any(s in status for s in _PROPOSAL_SCAFFOLDING_LANDED_STATUSES):
            continue
        text = page.read_text(encoding="utf-8")
        hits = sum(
            1 for pat in _PROPOSAL_SCAFFOLDING_HEADERS if pat.search(text)
        )
        if hits < _PROPOSAL_SCAFFOLDING_THRESHOLD:
            continue
        rel = page.relative_to(kb_dir).as_posix()
        findings.append(Finding(
            type="proposal-scaffolding",
            target=f"kb/{rel}",
            description=(
                f"page is {status.strip()} but still carries {hits} "
                "proposal-shape sections (Goals / Alternatives / "
                "Why this PR / etc.); compress to current-state "
                "synthesis and, if needed, a short Rejected "
                "alternatives appendix."
            ),
            severity="info",
        ))
    return findings


def _index_sections(
    index_path: Path, kb_dir: Path,
) -> Iterable[tuple[str, list[str]]]:
    """Yield ``(section_title, [kb_relative_target,…])`` for index.md.

    Sections are delimited by H2 headings. Targets are the kb-relative
    paths the section's links resolve to; non-kb / external links and
    fragment-only links are dropped. The H1 prelude before the first
    H2 is omitted because the index's prelude is editorial, not a
    section.
    """
    text = index_path.read_text(encoding="utf-8")
    headings = list(_H2_HEADING_RE.finditer(text))
    for i, match in enumerate(headings):
        title = _SECTION_DECORATION_RE.sub("", match.group("title")).strip()
        if not title:
            continue
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[start:end]
        targets: list[str] = []
        for raw in _INLINE_LINK_RE.findall(body):
            resolved = _resolve_relative(index_path, raw)
            if resolved is None:
                continue
            try:
                rel = resolved.relative_to(kb_dir).as_posix()
            except ValueError:
                continue
            targets.append(rel)
        for raw in _REFERENCE_LINK_RE.findall(body):
            resolved = _resolve_relative(index_path, raw)
            if resolved is None:
                continue
            try:
                rel = resolved.relative_to(kb_dir).as_posix()
            except ValueError:
                continue
            targets.append(rel)
        yield title, targets


def _status_marker_value(page: Path) -> str | None:
    """Return the lowercased text after ``Status:`` near the top of *page*.

    Returns ``None`` if no Status marker is found within the first
    :data:`_STATUS_SEARCH_LINES` non-blank lines.
    """
    looked = 0
    for raw in page.read_text(encoding="utf-8").splitlines():
        line = raw.strip().lstrip("*").lstrip()
        if not line:
            continue
        looked += 1
        lower = line.lower()
        if lower.startswith("status:"):
            return lower[len("status:"):].strip("* ").strip()
        if looked >= _STATUS_SEARCH_LINES:
            break
    return None


def _has_status_marker(page: Path) -> bool:
    """Return True when *page* has a ``Status:`` line near the top.

    Thin wrapper over :func:`_status_marker_value` so the structural
    check (does it exist?) and the value-extracting check (what does
    it say?) share the same parser and emphasis-tolerance rules.
    """
    return _status_marker_value(page) is not None


def _kb_targets_linked_from(page: Path, kb_dir: Path) -> set[str]:
    """Return ``rel/inside/kb`` paths that *page* links to."""
    out: set[str] = set()
    for raw in _markdown_link_targets(page):
        if _is_external(raw):
            continue
        resolved = _resolve_relative(page, raw)
        if resolved is None:
            continue
        try:
            rel = resolved.relative_to(kb_dir)
        except ValueError:
            continue
        out.add(rel.as_posix())
    return out


def _markdown_link_targets(page: Path) -> Iterable[str]:
    """Yield raw URL/path components from inline and reference links."""
    text = page.read_text(encoding="utf-8")
    for match in _INLINE_LINK_RE.finditer(text):
        yield match.group(1)
    for match in _REFERENCE_LINK_RE.finditer(text):
        yield match.group(1)


def _resolve_relative(
    page: Path,
    raw: str,
    *,
    repo_root: Path | None = None,
    kb_dir: Path | None = None,
) -> Path | None:
    """Resolve *raw* (anchor stripped) relative to *page*'s directory.

    Returns ``None`` for fragment-only links (``#section``) — they
    point inside the same page and can't be checked structurally.

    When *repo_root* and *kb_dir* are given, *kb_dir* is external (not
    ``repo_root/kb``), the plain resolution doesn't exist, and *raw*
    starts with ``../`` (i.e. it's trying to leave the kb, not point at
    a sibling page), retries the resolution as if the kb still lived at
    ``repo_root/kb`` — the historical assumption most existing
    ``../src/...``-style code links were written under. The retry is
    returned even when it *also* doesn't exist, so a genuinely broken
    code link still reports inside ``repo_root`` (where a caller's
    ``is_relative_to`` gate expects it) instead of landing outside every
    known root and silently going unchecked. A plain sibling-style
    target (no ``../``) is never redirected — it either exists inside
    *kb_dir* or is a real broken kb-internal link, either way `primary`
    is already the right answer.
    """
    target = raw.split("#", 1)[0]
    if not target:
        return None
    primary = (page.parent / target).resolve()
    if primary.exists() or repo_root is None or kb_dir is None:
        return primary
    legacy_kb_dir = (repo_root / "kb").resolve()
    if kb_dir == legacy_kb_dir or not target.startswith(".."):
        return primary
    return (legacy_kb_dir / target).resolve()


def _is_external(raw: str) -> bool:
    """Skip absolute URLs and URI schemes; they're outside the kb."""
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE)) or raw.startswith("mailto:")
