"""`topic:` — the resident mints, splits, merges and retires its heddles.

design-the-loom §20: a heddle is a layer over the work, one topic file
``<home>/surface/topics/<slug>.md`` with a ``signature:`` in its
frontmatter. The layers are managed *by verb*, through the outbox like every
other act, so each change lands in the ledger (a notice) rather than as a
silent file edit:

- ``topic: new <slug>`` — body = the signature as YAML (``places:`` ·
  ``words:`` · ``produce:`` · ``threads:``, optionally under ``signature:``,
  plus ``rune:`` / ``title:``). Refused when the slug is already a topic.
- ``topic: split <slug> -> a, b`` — ``a`` and ``b`` are written with the
  parent's signature (the resident narrows each by editing); the parent
  becomes the breadcrumb the warp graph already reads (``split-into: a b``)
  and stops lighting.
- ``topic: merge a, b -> c`` — ``c`` carries the union of the signatures and
  ``ids:`` aliases for every absorbed slug (so old item links keep
  resolving, the grammar ``warpGraph.ts`` already reads); absorbed files move
  to ``topics/retired/``. ``c`` may be one of the sources.
- ``topic: retire <slug>`` — the file moves to ``topics/retired/``.

``->`` and ``→`` both read as the arrow. Every write is whole-or-nothing:
the verb checks every precondition before it touches a file.

Refused for strands: the heddles are the whole cloth's, and a strand is one
thread of it. Dropped with no account home. Each accepted change records an
``advisory`` notice naming the files — no produce row: move 5 reads
``produce.jsonl``'s ``heddle`` kind as a branch or PR (#1976), and a topic
is not produce of the work.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import replace
from pathlib import Path

from .. import account
from .. import daemon
from .. import heddles
from .shapes import Handled, OutboxFile

VERB = "topic"
_ARROW_RE = re.compile(r"\s*(?:->|→)\s*")


def _finish(f: OutboxFile, text: str, *, kind: str, promoted: int) -> Handled:
    from .verbs import _handled

    daemon._record_outbox_notice(f.ctx.outbox_dir, text, kind=kind, lifetime="run")
    daemon._retire_outbox_staging(f.path)
    return _handled(f, VERB, promoted)


def _refuse(f: OutboxFile, text: str, *, kind: str = "refused") -> Handled:
    return _finish(f, text, kind=kind, promoted=0)


def _slugs(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,]+", text.strip()) if t]


def parse(raw: str) -> tuple[str, list[str], list[str]] | None:
    """``(op, sources, targets)`` or ``None`` when *raw* is not the grammar."""
    text = " ".join(str(raw or "").split())
    op, _, rest = text.partition(" ")
    op = op.lower()
    if op in ("new", "retire"):
        names = _slugs(rest)
        return (op, names, []) if len(names) == 1 else None
    if op in ("split", "merge"):
        parts = _ARROW_RE.split(rest)
        if len(parts) != 2:
            return None
        sources, targets = _slugs(parts[0]), _slugs(parts[1])
        if op == "split" and (len(sources) != 1 or len(targets) < 2):
            return None
        if op == "merge" and (len(sources) < 2 or len(targets) != 1):
            return None
        return op, sources, targets
    return None


def signature_from_body(body: str) -> tuple[heddles.Signature, str, str]:
    """``(signature, rune, title)`` from a verb body in the subset grammar."""
    lines = [line for line in (body or "").replace("\r\n", "\n").split("\n")]
    if lines and lines[0].strip() == "---":
        fm, rest = heddles.split_frontmatter(body)
        lines = fm or []
    has_block = any(line.strip() == "signature:" for line in lines)
    if not has_block:
        wrapped: list[str] = ["signature:"]
        scalars: list[str] = []
        for line in lines:
            key = line.split(":", 1)[0].strip()
            if not line.startswith((" ", "\t", "-")) and key in heddles.SIGNATURE_KINDS:
                wrapped.append("  " + line)
            elif line.startswith((" ", "\t", "-")):
                wrapped.append("  " + line)
            else:
                scalars.append(line)
        lines = scalars + wrapped
    values, signature = heddles.parse_frontmatter(lines)
    return signature, values.get("rune", ""), values.get("title", "")


def _set_row(body: str, key: str, value: str) -> str:
    """Set ``key: value`` in the title-then-rows block the warp graph reads."""
    lines = body.replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and re.match(r"^#[ \t]+", lines[index]):
        index += 1
    else:
        index = 0
    title_end = index
    while index < len(lines) and not lines[index].strip():
        index += 1
    row_re = re.compile(r"^(ids|split-into):[ \t]*(.*)$")
    start = index
    while index < len(lines) and row_re.match(lines[index]):
        if lines[index].startswith(f"{key}:"):
            lines[index] = f"{key}: {value}"
            return "\n".join(lines)
        index += 1
    if start == index:
        # No row block yet: one blank line after the title, the row, a blank.
        insert = ["", f"{key}: {value}"]
        if index < len(lines) and lines[index].strip():
            insert.append("")
        lines[title_end:start] = insert
        return "\n".join(lines)
    lines.insert(index, f"{key}: {value}")
    return "\n".join(lines)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _retire_path(directory: Path, slug: str) -> Path:
    base = directory / heddles.RETIRED_DIRNAME
    path = base / f"{slug}.md"
    if not path.exists():
        return path
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return base / f"{slug}.{stamp}.md"


def handle(f: OutboxFile) -> Handled:
    task = f.run
    meta = getattr(task, "meta", None) or {}
    raw = " ".join(str(f.frontmatter.get("topic") or "").split())
    parsed = parse(raw)
    if parsed is None:
        return _refuse(
            f, f"topic dropped: {raw!r} is not a topic verb — write `topic: new <slug>`, "
            "`topic: split <slug> -> a, b`, `topic: merge a, b -> c` or `topic: retire <slug>`",
            kind="dropped",
        )
    op, sources, targets = parsed
    if daemon._is_strand(meta):
        return _refuse(
            f, f"topic refused: {op} {' '.join(sources + targets)} — the heddles are the "
            "whole cloth's; a strand names the layer in its return value instead",
        )
    bad = [s for s in sources + targets if not heddles.SLUG_RE.match(s)]
    if bad:
        return _refuse(
            f, f"topic dropped: {', '.join(repr(b) for b in bad)} — a topic slug is "
            "lowercase letters, digits and hyphens", kind="dropped",
        )
    ctx = f.ctx.account_context
    if ctx is None:
        return _refuse(f, f"topic dropped: {op} — no account home holds the topics", kind="dropped")
    directory = account.work_surface_path(ctx) / heddles.TOPICS_DIRNAME
    existing = {t.slug: t for t in _all_topics(directory)}

    try:
        if op == "new":
            return _new(f, directory, existing, sources[0])
        if op == "retire":
            return _retire(f, directory, existing, sources[0])
        if op == "split":
            return _split(f, directory, existing, sources[0], targets)
        return _merge(f, directory, existing, sources, targets[0])
    except OSError as exc:
        return _refuse(f, f"topic dropped: {op} — a write failed: {exc}", kind="dropped")


def _all_topics(directory: Path) -> list[heddles.Topic]:
    """Live topics *and* split breadcrumbs — a breadcrumb's slug is taken."""
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.md")):
        topic = heddles.parse_topic_file(path)
        if topic is not None:
            out.append(topic)
    return out


def _accepted(f: OutboxFile, op: str, text: str, slugs: list[str]) -> Handled:
    f.ctx.emit(
        "topic_changed",
        run_id=getattr(f.run, "id", ""),
        event_id=f.ctx.event_id,
        op=op,
        slugs=" ".join(slugs),
    )
    stats = f.ctx.stats
    if stats is not None:
        stats["topic"] = stats.get("topic", 0) + 1
    return _finish(f, text, kind="advisory", promoted=1)


def _new(f, directory: Path, existing, slug: str) -> Handled:
    if slug in existing:
        return _refuse(
            f, f"topic refused: new {slug} — surface/topics/{slug}.md already exists; "
            "edit its signature in place, or `topic: merge`",
        )
    signature, rune, title = signature_from_body(f.body)
    topic = heddles.Topic(
        slug=slug, path=directory / f"{slug}.md", title=title or slug, rune=rune,
        signature=signature, body=f"# {title or slug}\n",
    )
    _write(topic.path, heddles.render_topic(topic))
    kinds = ", ".join(
        f"{k} {len(getattr(signature, k))}" for k in heddles.SIGNATURE_KINDS
        if getattr(signature, k)
    ) or "an empty signature — it lights on claims only"
    return _accepted(f, "new", f"topic new {slug} → surface/topics/{slug}.md ({kinds})", [slug])


def _retire(f, directory: Path, existing, slug: str) -> Handled:
    if slug not in existing:
        return _refuse(f, f"topic refused: retire {slug} — no surface/topics/{slug}.md")
    target = _retire_path(directory, slug)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(existing[slug].path, target)
    rel = target.relative_to(directory.parent).as_posix()
    return _accepted(f, "retire", f"topic retire {slug} → {rel}", [slug])


def _split(f, directory: Path, existing, slug: str, targets: list[str]) -> Handled:
    parent = existing.get(slug)
    if parent is None:
        return _refuse(f, f"topic refused: split {slug} — no surface/topics/{slug}.md")
    if parent.split_into:
        return _refuse(f, f"topic refused: split {slug} — it already split into {' '.join(parent.split_into)}")
    if slug in targets or len(set(targets)) != len(targets):
        return _refuse(f, f"topic refused: split {slug} -> {', '.join(targets)} — the targets must be distinct new slugs")
    taken = [t for t in targets if t in existing]
    if taken:
        return _refuse(f, f"topic refused: split {slug} — {', '.join(taken)} already exist; nothing was written")
    for target in targets:
        child = heddles.Topic(
            slug=target, path=directory / f"{target}.md",
            title=f"{parent.title} · {target}", signature=parent.signature,
            body=f"# {parent.title} · {target}\n",
        )
        _write(child.path, heddles.render_topic(child))
    breadcrumb = replace(parent, body=_set_row(parent.body, "split-into", " ".join(targets)))
    _write(parent.path, heddles.render_topic(breadcrumb))
    return _accepted(
        f, "split",
        f"topic split {slug} → {', '.join(targets)} (each carries {slug}'s signature — narrow it; "
        f"{slug}.md is now a split-into breadcrumb)",
        [slug, *targets],
    )


def _merge(f, directory: Path, existing, sources: list[str], target: str) -> Handled:
    if len(set(sources)) != len(sources):
        return _refuse(f, f"topic refused: merge {', '.join(sources)} — a source is named twice")
    missing = [s for s in sources if s not in existing]
    if missing:
        return _refuse(f, f"topic refused: merge — no topic file for {', '.join(missing)}; nothing was written")
    breadcrumbs = [s for s in sources if existing[s].split_into]
    if breadcrumbs:
        return _refuse(f, f"topic refused: merge — {', '.join(breadcrumbs)} already split; merge its children")
    if target not in sources and target in existing:
        return _refuse(
            f, f"topic refused: merge -> {target} — {target} already exists and is not a source; "
            f"name it as a source to merge into it",
        )
    base = existing.get(target) or existing[sources[0]]
    signature = base.signature
    aliases: list[str] = list(base.aliases) if target in existing else []
    rune = base.rune
    for slug in sources:
        topic = existing[slug]
        signature = signature.union(topic.signature)
        rune = rune or topic.rune
        for name in (slug, *topic.aliases):
            if name != target and name not in aliases:
                aliases.append(name)
    body = base.body if target in existing else f"# {base.title}\n"
    body = _set_row(body, "ids", " ".join(aliases))
    merged = heddles.Topic(
        slug=target, path=directory / f"{target}.md", title=base.title, rune=rune,
        signature=signature, aliases=tuple(aliases), extra=base.extra, body=body,
    )
    _write(merged.path, heddles.render_topic(merged))
    moved = []
    for slug in sources:
        if slug == target:
            continue
        dest = _retire_path(directory, slug)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(existing[slug].path, dest)
        moved.append(dest.relative_to(directory.parent).as_posix())
    return _accepted(
        f, "merge",
        f"topic merge {', '.join(sources)} → {target} (ids: {' '.join(aliases)}; moved {', '.join(moved) or 'nothing'})",
        [*sources, target] if target not in sources else list(sources),
    )
