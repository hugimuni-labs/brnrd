"""Bundled tool documentation.

Tool-level docs (how brnrd works, pipeline, internals) ship with the
package as ``*.md`` files in this folder. Users can override any
topic per-repo by dropping a file at ``.brr/docs/<topic>.md``.

Project-specific knowledge lives in ``kb/``, never here.
"""

from __future__ import annotations

from pathlib import Path


_DOCS_DIR = Path(__file__).resolve().parent


def _override_dir(repo_root: Path | None) -> Path | None:
    if repo_root is None:
        return None
    from .. import gitops

    return gitops.shared_brr_dir(repo_root) / "docs"


def list_topics(repo_root: Path | None = None) -> list[str]:
    """Return sorted, deduplicated list of doc topics available.

    Includes bundled topics plus any per-repo overrides or additions.
    """
    topics: set[str] = set()
    if _DOCS_DIR.exists():
        topics.update(p.stem for p in _DOCS_DIR.glob("*.md"))
    overrides = _override_dir(repo_root)
    if overrides and overrides.exists():
        topics.update(p.stem for p in overrides.glob("*.md"))
    return sorted(topics)


def effective_topic_path(topic: str, repo_root: Path | None = None) -> Path:
    """The path a doc topic *would* be read from — mirrors ``prompts.effective_prompt_path``.

    Order: the per-repo override (``<shared-brr-dir>/docs/<topic>.md``), then the
    bundled ``src/brr/docs/<topic>.md``. Returns the bundled path even when neither
    exists, so a caller that wants one *location* to report (a manifest, a mounted
    ``Perceive``) always has something to name.
    """
    overrides = _override_dir(repo_root)
    if overrides is not None:
        candidate = overrides / f"{topic}.md"
        if candidate.exists():
            return candidate
    return _DOCS_DIR / f"{topic}.md"


def read_topic(topic: str, repo_root: Path | None = None) -> str | None:
    """Read a topic's markdown, preferring a per-repo override.

    Returns None if the topic does not exist or the name is malformed.
    """
    if not topic or "/" in topic or topic.startswith("."):
        return None
    overrides = _override_dir(repo_root)
    if overrides is not None:
        candidate = overrides / f"{topic}.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    bundled = _DOCS_DIR / f"{topic}.md"
    if bundled.exists():
        return bundled.read_text(encoding="utf-8")
    return None


def format_listing(repo_root: Path | None = None) -> str:
    """Human-readable listing of bundled docs."""
    topics = list_topics(repo_root)
    if not topics:
        return "[brnrd] no bundled docs available"
    lines = ["Available brnrd docs:", ""]
    overrides = _override_dir(repo_root)
    for topic in topics:
        note = ""
        if overrides is not None and (overrides / f"{topic}.md").exists():
            note = "  (overridden)"
        lines.append(f"  {topic}{note}")
    lines.append("")
    lines.append("These docs are bundled for prompts and project references.")
    return "\n".join(lines)
