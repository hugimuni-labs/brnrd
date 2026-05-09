"""Deterministic kb consistency scan for the post-task maintenance pass.

The preflight reads the on-disk shape of ``kb/`` and produces a list
of structured findings — orphaned pages, broken cross-links, index
drift. It does not modify anything; the LLM-driven kb-maintenance
prompt either acts on the findings or explains why no action is
needed.

Skip-fast contract: when the preflight returns no findings *and*
``kb/`` was untouched by the preceding task, the maintenance pass can
be skipped entirely. That keeps the daemon's hot path cheap and
turns kb-maintenance into a true safety net rather than a tax on
every run.

Findings only cover things a deterministic scanner can be confident
about:

- ``missing-from-index``: a kb page exists on disk but is not linked
  from ``kb/index.md``.
- ``stale-index-entry``: ``kb/index.md`` links to a path that doesn't
  exist on disk.
- ``broken-link``: any kb page links (relatively) to a path that
  doesn't exist on disk.

Lifecycle-marker drift, subject-hub coverage, contradiction with the
log, and similar judgement calls live in the LLM redundancy pass on
top — they need synthesis the scanner can't do.
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


# Match Markdown inline links like ``[text](path)`` and reference-style
# links ``[text]: path``. We capture the URL/path component; anchors
# (``#section``) and titles (``"hover"``) get stripped afterwards.
_INLINE_LINK_RE = re.compile(r"\[(?:[^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_REFERENCE_LINK_RE = re.compile(r"^\[[^\]]+\]:\s*(\S+)", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    """A single deterministic kb consistency observation."""

    type: str
    target: str
    description: str

    def render(self) -> str:
        """Return the bullet form used inside the maintenance prompt."""
        return f"- **{self.type}** `{self.target}` — {self.description}"


def scan(repo_root: Path) -> list[Finding]:
    """Return all kb consistency findings for *repo_root*.

    Returns an empty list when ``kb/`` is missing or when the kb is
    fully consistent. The order of findings is stable
    (``missing-from-index`` first, then ``stale-index-entry``, then
    ``broken-link`` sorted by source then target) so the formatted
    output is reproducible.
    """
    kb_dir = repo_root / "kb"
    if not kb_dir.is_dir():
        return []

    pages = sorted(p for p in kb_dir.rglob("*.md") if p.is_file())
    index_path = kb_dir / "index.md"

    findings: list[Finding] = []
    findings.extend(_check_index_coverage(kb_dir, index_path, pages))
    findings.extend(_check_index_targets_exist(repo_root, index_path))
    findings.extend(_check_broken_links(repo_root, kb_dir, pages))
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
    repo_root: Path, index_path: Path,
) -> list[Finding]:
    """Every relative link in index.md must resolve to a real file."""
    if not index_path.exists():
        return []
    findings: list[Finding] = []
    for raw in _markdown_link_targets(index_path):
        if _is_external(raw):
            continue
        resolved = _resolve_relative(index_path, raw)
        if resolved is None:
            continue
        if resolved.is_relative_to(repo_root) and not resolved.exists():
            findings.append(Finding(
                type="stale-index-entry",
                target=str(resolved.relative_to(repo_root)),
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
            resolved = _resolve_relative(page, raw)
            if resolved is None:
                continue
            if not resolved.is_relative_to(repo_root):
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
    findings.sort(key=lambda f: (f.type, f.target))
    return findings


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


def _resolve_relative(page: Path, raw: str) -> Path | None:
    """Resolve *raw* (anchor stripped) relative to *page*'s directory.

    Returns ``None`` for fragment-only links (``#section``) — they
    point inside the same page and can't be checked structurally.
    """
    target = raw.split("#", 1)[0]
    if not target:
        return None
    return (page.parent / target).resolve()


def _is_external(raw: str) -> bool:
    """Skip absolute URLs and URI schemes; they're outside the kb."""
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE)) or raw.startswith("mailto:")
