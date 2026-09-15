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

Move 5d reshaped the ask into a query (:func:`parse_query`)::

    show <slug> [since <span>] [kinds: a, b, c] [depth: heads|cut|whole] [bench: true|false]

— the bracketed parts in any order. ``kinds`` filters the acts (default
``messages, produce``); ``depth`` sets how much of a message renders —
outbound replies and inbound events alike, §21's "a message either way"
(``heads`` its first line, ``cut`` its first and last paragraphs, ``whole``
the body; default ``whole``); produce, strands, bolts and folds always render
as heads. ``bench`` (default false) writes the page; the rendering itself
rides the ask event's body either way, capped at :data:`INLINE_CAP_BYTES`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import heddles
from . import protocol

_LINE_MAX = 120
#: The ask event's body carries the rendering up to this many UTF-8 bytes;
#: past it, whole acts are dropped and one line says how many.
INLINE_CAP_BYTES = 24 * 1024

#: The query's kind names → the index's kinds. Singular spellings are
#: tolerated; the plural is the grammar.
KINDS = {
    "messages": "message",
    "strands": "strand",
    "bolts": "bolt",
    "folds": "fold",
    "produce": "produce",
    "events": "event",
}
_KIND_ALIASES = {**{k: k for k in KINDS}, "message": "messages", "strand": "strands",
                 "bolt": "bolts", "fold": "folds", "event": "events"}
DEFAULT_KINDS = ("messages", "produce")
DEPTHS = ("heads", "cut", "whole")
DEFAULT_DEPTH = "whole"
#: Kinds whose body ``depth`` shapes — a message either way (§21).
_DEPTH_KINDS = frozenset({"message", "event"})
_CLAUSE_RE = re.compile(r"(?:(?<=\s)|^)(since|kinds|depth|bench)(?:\s*:\s*|\s+)", re.IGNORECASE)


@dataclass(frozen=True)
class Query:
    """One ``topic: show`` — what to pull up and how deep."""

    slug: str
    since: str | None = None
    kinds: tuple[str, ...] = DEFAULT_KINDS
    depth: str = DEFAULT_DEPTH
    bench: bool = False

    @property
    def index_kinds(self) -> frozenset[str]:
        return frozenset(KINDS[k] for k in self.kinds)

    def describe(self) -> str:
        """The query as the grammar would write it back, defaults omitted."""
        parts = [self.slug]
        if self.since:
            parts.append(f"since {self.since}")
        if self.kinds != DEFAULT_KINDS:
            parts.append("kinds: " + ", ".join(self.kinds))
        if self.depth != DEFAULT_DEPTH:
            parts.append(f"depth: {self.depth}")
        if self.bench:
            parts.append("bench: true")
        return " ".join(parts)


def parse_query(text: object) -> Query | str:
    """``<slug> [since <span>] [kinds: …] [depth: …] [bench: …]`` → a
    :class:`Query`, or a one-line reason it is not the grammar.

    The clauses come in any order, each at most once; the key's colon is
    optional (``since 3d`` and ``since: 3d`` both read); a ``kinds`` list is
    split on commas and spaces. Nothing before the first clause but the slug.
    """
    from . import heddles

    raw = " ".join(str(text or "").split())
    slug, _, rest = raw.partition(" ")
    if not heddles.SLUG_RE.match(slug):
        return f"{slug!r} is not a topic slug"
    marks = list(_CLAUSE_RE.finditer(rest))
    if rest and (not marks or marks[0].start() != 0):
        return f"{rest.split(':', 1)[0].split()[0]!r} is not a clause — since · kinds: · depth: · bench:"
    values: dict[str, str] = {}
    for i, mark in enumerate(marks):
        key = mark.group(1).lower()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(rest)
        if key in values:
            return f"{key} is given twice"
        values[key] = rest[mark.end():end].strip().rstrip(",").strip()
    since = values.get("since")
    if since is not None and (len(since.split()) != 1 or heddles.parse_span(since) is None):
        return f"since {since!r} is not a span (e.g. 2h, 3d, 1w)"
    kinds = DEFAULT_KINDS
    if "kinds" in values:
        names = [n for n in re.split(r"[\s,]+", values["kinds"].lower()) if n]
        unknown = [n for n in names if n not in _KIND_ALIASES]
        if not names or unknown:
            return (f"kinds: {', '.join(unknown) or '(empty)'} — the kinds are "
                    + " · ".join(KINDS))
        ordered: list[str] = []
        for name in names:
            plural = _KIND_ALIASES[name]
            if plural not in ordered:
                ordered.append(plural)
        kinds = tuple(ordered)
    depth = values.get("depth", DEFAULT_DEPTH).lower()
    if depth not in DEPTHS:
        return f"depth: {depth!r} — heads · cut · whole"
    bench_text = values.get("bench", "false").lower()
    if bench_text not in ("true", "false"):
        return f"bench: {bench_text!r} — true · false"
    return Query(slug=slug, since=since, kinds=kinds, depth=depth, bench=bench_text == "true")
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


def _message_body(home: Path | None, ref: str) -> str | None:
    run, _, stem = ref.partition("/")
    if home is None or not run or not stem:
        return None
    for path in sorted((Path(home) / "runs").glob(f"*/{run}/messages/{stem}.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        return protocol.frontmatter_body(text)
    return None


def _message_line(home: Path | None, ref: str) -> str:
    return _first_line(_message_body(home, ref) or "")


def _paragraphs(text: str) -> list[str]:
    blocks = re.split(r"\n[ \t]*\n", str(text or "").replace("\r\n", "\n").strip("\n"))
    return [b.strip("\n") for b in blocks if b.strip()]


def shape(text: str, depth: str) -> str:
    """A message body at *depth*: ``heads`` its first line · ``cut`` its first
    paragraph and last paragraph, joined by `` … `` when they differ ·
    ``whole`` the body as written (trailing blank lines trimmed)."""
    if depth == "heads":
        return _first_line(text)
    paragraphs = _paragraphs(text)
    if not paragraphs:
        return ""
    if depth == "cut":
        first, last = paragraphs[0], paragraphs[-1]
        return first if first == last else f"{first} … {last}"
    return "\n\n".join(paragraphs)


def _block(text: str) -> list[str]:
    """A multi-line body as the list item's continuation, indented two."""
    return [f"  {line}" if line.strip() else "" for line in text.split("\n")]


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


def render_act(
    account_home: Path | None,
    row: dict[str, Any],
    *,
    depth: str = "heads",
    inbox_dirs: Iterable[Path] = (),
    runs_dirs: Iterable[Path] = (),
) -> list[str]:
    """One act as Markdown: a list line, plus the body indented under it when
    *depth* (``cut`` · ``whole``) shapes a message or an inbound event."""
    kind = str(row.get("kind") or "")
    ref = str(row.get("ref") or "")
    at = str(row.get("at") or "")
    detail = ""
    body: str | None = None
    deep = depth in ("cut", "whole") and kind in _DEPTH_KINDS
    if kind == "message":
        text = _message_body(account_home, ref)
        if deep and text is not None:
            body = shape(text, depth)
        else:
            detail = _first_line(text or "")
    elif kind == "event":
        event = _find_event(inbox_dirs, ref)
        text = event.get("body") or "" if event else None
        if deep and text is not None:
            body = shape(text, depth)
        else:
            detail = _first_line(text or "")
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
    lines = [line]
    if body:
        lines.extend(_block(body))
    return lines


def render_rows(
    account_home: Path | None,
    rows: list[dict[str, Any]],
    *,
    depth: str = "heads",
    inbox_dirs: Iterable[Path] = (),
    runs_dirs: Iterable[Path] = (),
) -> list[str]:
    """Markdown for every index row, oldest first (see :func:`render_act`)."""
    inbox_dirs = [Path(d) for d in inbox_dirs if d]
    runs_dirs = [Path(d) for d in runs_dirs if d]
    lines: list[str] = []
    for row in rows:
        lines.extend(render_act(account_home, row, depth=depth,
                                inbox_dirs=inbox_dirs, runs_dirs=runs_dirs))
    return lines


def render(
    account_home: Path | None,
    slug: str,
    *,
    since: object = None,
    now: object = None,
    inbox_dirs: Iterable[Path] = (),
    runs_dirs: Iterable[Path] = (),
    kinds: Iterable[str] | None = None,
    depth: str = "heads",
    cap_bytes: int | None = None,
) -> str:
    """The page body: a title, the counts by kind, then the acts.

    *kinds* (the query's names, ``messages`` · ``produce`` …) filters the
    acts; ``None`` keeps every kind. *depth* shapes messages and events.
    *cap_bytes* bounds the result: acts are kept whole, oldest first, until
    the next would pass the cap; then one line — ``… N more acts; bench:
    true for the page`` — closes it.
    """
    from . import heddles

    rows = heddles.index(account_home, slug, since, now=now)
    if kinds is not None:
        wanted = frozenset(KINDS.get(_KIND_ALIASES.get(k, k), k) for k in kinds)
        rows = [r for r in rows if str(r.get("kind")) in wanted]
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
    inbox_dirs = [Path(d) for d in inbox_dirs if d]
    runs_dirs = [Path(d) for d in runs_dirs if d]
    text = "\n".join(lines) + "\n"
    if cap_bytes is None:
        return text + "\n".join(render_rows(
            account_home, rows, depth=depth, inbox_dirs=inbox_dirs, runs_dirs=runs_dirs,
        )) + "\n"
    used = len(text.encode("utf-8"))
    for index, row in enumerate(rows):
        act = "\n".join(render_act(account_home, row, depth=depth,
                                   inbox_dirs=inbox_dirs, runs_dirs=runs_dirs)) + "\n"
        size = len(act.encode("utf-8"))
        left = len(rows) - index
        tail = f"… {left} more acts; bench: true for the page\n"
        if used + size > cap_bytes or (
            left > 1 and used + size + len(tail.encode("utf-8")) > cap_bytes
        ):
            return text + tail
        text += act
        used += size
    return text
