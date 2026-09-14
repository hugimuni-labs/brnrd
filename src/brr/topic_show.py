"""`topic: show <slug>` — a topic's index, pulled up as a page.

design-the-loom §21 item 4: *pull-up is a fold*. The frame reads
``heddles.index`` (the topic's own rows and every alias's, oldest first) and
renders one line per act — a message with its first line, a strand with its
title and outcome, a bolt, a fold, produce with its ref, an inbound event
with its first line — into the bench file at the place ``topics/<slug>``
(``outbox/topic.py`` writes it). ``brnrd hud --topic <slug>`` prints the same
rendering, read-only.

Every lookup is best-effort and local: a message is found under the run node
it was written to, a strand or an event in the inboxes the caller names, a
strand's outcome on its run's manifest. What is not found renders as its
ref — never a guess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from . import heddles
from . import protocol

_LINE_MAX = 120
_KIND_LABEL = {
    "event": "event",
    "message": "message",
    "strand": "strand",
    "bolt": "bolt",
    "fold": "fold",
    "produce": "produce",
}


def _clip(text: str) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= _LINE_MAX else line[: _LINE_MAX - 1].rstrip() + "…"


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip() and line.strip() != "---":
            return _clip(line.strip())
    return ""


def _message_line(home: Path | None, ref: str) -> str:
    run, _, stem = ref.partition("/")
    if home is None or not run or not stem:
        return ""
    for path in sorted((Path(home) / "runs").glob(f"*/{run}/messages/{stem}.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        return _first_line(protocol.frontmatter_body(text))
    return ""


def _find_event(inbox_dirs: Iterable[Path], event_id: str) -> dict[str, Any] | None:
    for directory in inbox_dirs:
        path = Path(directory) / f"{event_id}.md"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = protocol.parse_frontmatter(text)
        meta["body"] = protocol.frontmatter_body(text)
        return meta
    return None


def _strand_line(event: dict[str, Any] | None, runs_dirs: Iterable[Path]) -> str:
    if event is None:
        return ""
    from .run import Run, run_manifest_path

    title = _clip(str(event.get("title") or "")) or _first_line(event.get("body") or "")
    outcome = str(event.get("status") or "")
    run_id = str(event.get("run_id") or "")
    if run_id:
        for runs_dir in runs_dirs:
            manifest = Run.from_file(run_manifest_path(Path(runs_dir), run_id))
            if manifest is not None:
                outcome = manifest.status or outcome
                break
    parts = [title] if title else []
    if outcome:
        parts.append(outcome)
    if run_id:
        parts.append(run_id)
    return " · ".join(parts)


def render_rows(
    account_home: Path | None,
    rows: list[dict[str, Any]],
    *,
    inbox_dirs: Iterable[Path] = (),
    runs_dirs: Iterable[Path] = (),
) -> list[str]:
    """One Markdown list line per index row, oldest first."""
    inbox_dirs = [Path(d) for d in inbox_dirs if d]
    runs_dirs = [Path(d) for d in runs_dirs if d]
    lines: list[str] = []
    for row in rows:
        kind = str(row.get("kind") or "")
        ref = str(row.get("ref") or "")
        at = str(row.get("at") or "")
        detail = ""
        if kind == "message":
            detail = _message_line(account_home, ref)
        elif kind == "event":
            event = _find_event(inbox_dirs, ref)
            detail = _first_line(event.get("body") or "") if event else ""
        elif kind == "strand":
            detail = _strand_line(_find_event(inbox_dirs, ref), runs_dirs)
        elif kind == "produce":
            detail = f"`{ref[:10]}`" if ref else ""
        label = _KIND_LABEL.get(kind, kind or "act")
        line = f"- {at} · {label} `{ref}`"
        if detail:
            line += f" — {detail}"
        run = str(row.get("run") or "")
        if run and kind not in ("strand",) and run not in ref:
            line += f" · {run}"
        lines.append(line)
    return lines


def render(
    account_home: Path | None,
    slug: str,
    *,
    since: object = None,
    now: object = None,
    inbox_dirs: Iterable[Path] = (),
    runs_dirs: Iterable[Path] = (),
) -> str:
    """The page body: a title, the counts by kind, then one line per act."""
    rows = heddles.index(account_home, slug, since, now=now)
    canonical = heddles.resolve_slug(account_home, slug) or slug
    title = canonical
    for topic in heddles.load_topics(heddles.topics_dir(account_home)):
        if topic.slug == canonical:
            title = topic.title or canonical
            break
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row.get("kind"))] = counts.get(str(row.get("kind")), 0) + 1
    head = f"# {title} — the topic's index"
    if canonical != slug:
        head += f" (`{slug}` resolves to `{canonical}`)"
    lines = [head, ""]
    span = f" since {since}" if since is not None else ""
    if not rows:
        lines.append(f"No acts assigned to `{canonical}`{span}.")
        return "\n".join(lines) + "\n"
    summary = " · ".join(f"{kind} {counts[kind]}" for kind in heddles.INDEX_KINDS if counts.get(kind))
    lines.append(f"{len(rows)} acts{span}: {summary}")
    lines.append("")
    lines.extend(render_rows(account_home, rows, inbox_dirs=inbox_dirs, runs_dirs=runs_dirs))
    return "\n".join(lines) + "\n"
