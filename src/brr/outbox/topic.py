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
- ``topic: show <slug> [since <span>] [kinds: a, b] [depth: heads|cut|whole]
  [bench: true|false]`` (move 5c, reshaped in 5d) — the topic's index
  rendered into the ask delivered to the seat (its body carries the
  rendering, capped at 24 KB), and into the bench at the place
  ``topics/<slug>`` only with ``bench: true`` (``topic_show``).
- ``topic: rune <slug> <glyph>`` (move 5d) — set the heddle's ``rune:``: one
  grapheme of one or two code points; the chip and the rail read it at the
  next boundary.
- ``topic: assign <slug> -> <event-id | run-id>`` (move 5c) — assign a run
  that ended in error with no topic (``topic_unset``), once.

A bare ``topic: <slug>`` is not a verb: it is the act's topic, read by the
rows that deliver the act (``run_topic``).

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
#: The words that make `topic:` a verb. Anything else — a bare slug — is the
#: act's topic (move 5c) and the file falls through the table to the row that
#: delivers it.
OPS = ("new", "split", "merge", "retire", "show", "assign", "rune")
_TARGET_ID_RE = re.compile(r"^(?:evt-\d{10,}-[a-z0-9]{4}|run-\d{6}-\d{4}-[a-z0-9]{4})$")


def is_op(raw: object) -> bool:
    """``True`` when a ``topic:`` value is this verb's, not an act's topic.

    An act's topic is exactly one slug that is not an op word
    (``topic: the-loom``). Anything else — an op (``new foo``), or a value
    that is not one slug (``rename a b``, ``a, b``) — is claimed by the verb,
    so a mistyped op is dropped with a notice instead of falling through to
    ``event`` and delivering its signature body as a chat message.
    """
    words = str(raw or "").split()
    if not words:
        return False
    if len(words) == 1 and heddles.SLUG_RE.match(words[0]) and words[0].lower() not in OPS:
        return False
    return True


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
    if op == "show":
        from .. import topic_show

        query = topic_show.parse_query(rest)
        if isinstance(query, str):
            return None
        return op, [query.slug], [rest]
    if op == "rune":
        words = rest.split(" ")
        if len(words) != 2 or not words[0] or not words[1]:
            return None
        return op, [words[0]], [words[1]]
    if op == "assign":
        parts = _ARROW_RE.split(rest)
        if len(parts) != 2:
            return None
        sources, targets = _slugs(parts[0]), _slugs(parts[1])
        if len(sources) != 1 or len(targets) != 1 or not _TARGET_ID_RE.match(targets[0]):
            return None
        return op, sources, targets
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
    if parsed is None and raw.split(" ", 1)[0].lower() == "show":
        from .. import topic_show

        reason = topic_show.parse_query(raw.partition(" ")[2])
        return _refuse(
            f, f"topic dropped: {raw!r} — {reason}; write `topic: show <slug> [since <span>] "
            "[kinds: messages, produce] [depth: heads|cut|whole] [bench: true|false]`",
            kind="dropped",
        )
    if parsed is None:
        return _refuse(
            f, f"topic dropped: {raw!r} is not a topic verb — write `topic: new <slug>`, "
            "`topic: split <slug> -> a, b`, `topic: merge a, b -> c`, `topic: retire <slug>`, "
            "`topic: show <slug> [since <span>] [kinds: …] [depth: …] [bench: …]`, "
            "`topic: rune <slug> <glyph>` or `topic: assign <slug> -> <event-id>`",
            kind="dropped",
        )
    op, sources, targets = parsed
    if daemon._is_strand(meta):
        return _refuse(
            f, f"topic refused: {op} {' '.join(sources + targets)} — the heddles are the "
            "whole cloth's; a strand names the layer in its return value instead",
        )
    slug_args = sources + (targets if op in ("split", "merge") else [])
    bad = [s for s in slug_args if not heddles.SLUG_RE.match(s)]
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
        if op == "show":
            from .. import topic_show

            return _show(f, ctx, topic_show.parse_query(targets[0]))
        if op == "rune":
            return _rune(f, ctx, directory, sources[0], targets[0])
        if op == "assign":
            return _assign(f, ctx, sources[0], targets[0])
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
    if op == "show":  # a read, not a change: its packet is `fold_requested`
        return _finish(f, text, kind="advisory", promoted=1)
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


# ── Move 5c: `show` and `assign` ─────────────────────────────────────────


def _show(f: OutboxFile, ctx, query) -> Handled:
    """`topic: show <slug> [since <span>] [kinds: …] [depth: …] [bench: …]`.

    The index is rendered for the query (``topic_show.render``) and delivered
    to the seat as an ask the way ``fold:`` does — a ``source: fold`` event
    whose **body carries the rendering** (capped at
    ``topic_show.INLINE_CAP_BYTES``), so the seat reads it in the boundary
    the ask lands in. ``bench: true`` also writes the page, uncapped, at the
    place ``topics/<slug>`` (the ``fold:`` store, commit = ``HEAD``) and
    rewrites it on every show — unlike a fold's bench file, whose body is the
    weaver's. Without it nothing is written to the bench."""
    from .. import protocol
    from .. import topic_show
    from . import fold

    task = f.run
    meta = getattr(task, "meta", None) or {}
    slug = query.slug
    home = account.context_home_root(ctx)
    canonical = heddles.resolve_slug(home, slug)
    index_file = heddles.index_path(home, slug)
    if canonical is None and (index_file is None or not index_file.exists()):
        return _refuse(f, f"topic refused: show {slug} — no heddle and no index by that name")
    label = str(meta.get("repo_label") or "")
    if not label and getattr(ctx, "default_repo", None) is not None:
        label = str(getattr(ctx.default_repo, "label", "") or "")
    if f.ctx.inbox_dir is None:
        return _refuse(f, f"topic dropped: show {slug} — no inbox to deliver the ask to", kind="dropped")
    place = f"{heddles.TOPICS_DIRNAME}/{canonical or slug}"
    brr_dir = getattr(f.ctx.emit, "brr_dir", None)
    render_kw = dict(
        since=query.since,
        inbox_dirs=[f.ctx.inbox_dir],
        runs_dirs=[Path(brr_dir) / "runs"] if brr_dir is not None else [],
        kinds=query.kinds,
        depth=query.depth,
    )
    path: Path | None = None
    commit = ""
    if query.bench:
        if not label or f.ctx.repo_root is None:
            return _refuse(f, f"topic dropped: show {slug} bench: true — this run has no repo checkout "
                           "to key the bench to", kind="dropped")
        commit = fold.read_head(f.ctx.repo_root)
        if not commit:
            return _refuse(f, f"topic dropped: show {slug} — `git rev-parse HEAD` did not answer",
                           kind="dropped")
        page = topic_show.render(home, slug, **render_kw)
        made_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = fold.bench_path(home, account.slug_repo_label(label), place, commit)
        _write(path, (
            "---\n"
            f"place: {place}\n"
            f"commit: {commit}\n"
            f"topic: {canonical or slug}\n"
            f"since: {query.since or ''}\n"
            f"kinds: {', '.join(query.kinds)}\n"
            f"depth: {query.depth}\n"
            f"made_at: {made_at}\n"
            "---\n" + page
        ))
    acts = sum(1 for r in heddles.index(home, slug, query.since) if r.get("kind") in query.index_kinds)
    described = query.describe().replace(slug, canonical or slug, 1)
    head = [f"topic show {described} · {acts} acts"]
    if path is not None:
        head.append(f"bench: {path}")
    # The cap bounds the whole body the seat reads, the head lines included.
    room = topic_show.INLINE_CAP_BYTES - len(("\n".join(head) + "\n\n").encode("utf-8"))
    inline = topic_show.render(home, slug, cap_bytes=room, **render_kw)
    conversation_key = str(getattr(task, "conversation_key", "") or f.ctx.emit.conversation_key or "")
    fields = dict(
        conversation_key=" ".join(conversation_key.split()),
        focus_place=place,
        focus_question=f"the index of {canonical or slug}",
        fold_by_run=str(getattr(task, "id", "") or ""),
    )
    if label:
        fields["repo_label"] = label
    if path is not None:
        fields.update(focus_commit=commit, focus_bench_path=str(path))
    try:
        event_path = protocol.create_event(
            f.ctx.inbox_dir, fold.SOURCE, "\n".join(head) + "\n\n" + inline, **fields,
        )
    except (OSError, ValueError) as exc:
        where = f"the page is at {path} but " if path is not None else ""
        return _refuse(
            f, f"topic dropped: show {slug} — {where}the ask was not delivered: {exc}", kind="dropped",
        )
    f.ctx.emit(
        "fold_requested",
        run_id=getattr(task, "id", ""),
        event_id=f.ctx.event_id,
        fold_event=event_path.stem,
        place=place,
        commit=commit,
        bench_path=str(path) if path is not None else "",
    )
    where = f"{path.name} at {place}" if path is not None else f"inline in {event_path.stem}"
    return _accepted(f, "show", f"topic show {canonical or slug} → {where} ({acts} acts)",
                     [canonical or slug])


# ── Move 5d: `rune` ──────────────────────────────────────────────────────

#: Code points that may follow a base to make one grapheme of two: combining
#: marks (by category), variation selectors, skin-tone modifiers.
_RUNE_JOINERS = (range(0xFE00, 0xFE10), range(0x1F3FB, 0x1F400), range(0xE0100, 0xE01F0))
_REGIONAL = range(0x1F1E6, 0x1F200)


def rune_problem(glyph: str) -> str | None:
    """``None`` when *glyph* is one grapheme of one or two code points; else
    why not. No grapheme library: the second code point must be a combining
    mark, a variation selector or a skin-tone modifier — or the pair two
    regional indicators (a flag)."""
    import unicodedata

    if not glyph:
        return "empty"
    if len(glyph) > 2:
        return f"{len(glyph)} code points — a rune is one grapheme of one or two"
    first = glyph[0]
    if unicodedata.category(first)[0] in "CZM":
        return f"U+{ord(first):04X} is not a visible base character"
    if len(glyph) == 1:
        return None
    second = glyph[1]
    if ord(first) in _REGIONAL and ord(second) in _REGIONAL:
        return None
    if unicodedata.category(second).startswith("M") or any(ord(second) in r for r in _RUNE_JOINERS):
        return None
    return "two graphemes — a rune is one"


def _rune(f: OutboxFile, ctx, directory: Path, slug: str, glyph: str) -> Handled:
    """`topic: rune <slug> <glyph>` — set ``rune:`` in the topic file's
    frontmatter, everything else kept. Refused for a glyph that is not one
    grapheme of one or two code points, or a slug that is not a live heddle
    (an alias is not: the rune belongs to the topic file, named by its own
    slug)."""
    problem = rune_problem(glyph)
    if problem:
        return _refuse(f, f"topic refused: rune {slug} {glyph!r} — {problem}")
    topic = next((t for t in heddles.load_topics(directory) if t.slug == slug), None)
    if topic is None:
        home = account.context_home_root(ctx)
        owner = heddles.resolve_slug(home, slug)
        hint = f" — it is an alias of {owner}; `topic: rune {owner} {glyph}`" if owner else ""
        return _refuse(f, f"topic refused: rune {slug} — not a live heddle{hint}")
    if topic.rune == glyph:
        return _finish(f, f"topic rune {slug}: already {glyph} — nothing written",
                       kind="advisory", promoted=0)
    _write(topic.path, heddles.render_topic(replace(topic, rune=glyph)))
    was = f" (was {topic.rune})" if topic.rune else ""
    return _accepted(
        f, "rune", f"topic rune {slug} → {glyph}{was} · surface/topics/{slug}.md; "
        "the chip and the rail read it at the next boundary", [slug],
    )


def _assign(f: OutboxFile, ctx, slug: str, target: str) -> Handled:
    """`topic: assign <slug> -> <event-id | run-id>` — assign a run that ended
    in error with no topic, once. Refused for anything already assigned:
    nothing is reclassified after the fact."""
    from .. import protocol
    from .. import run_topic
    from ..run import Run, run_manifest_path

    home = account.context_home_root(ctx)
    canonical = heddles.resolve_slug(home, slug)
    if canonical is None:
        return _refuse(f, f"topic refused: assign {slug} — no heddle by that name; `topic: new {slug}` first")
    brr_dir = getattr(f.ctx.emit, "brr_dir", None)
    if brr_dir is None:
        return _refuse(f, f"topic dropped: assign {slug} -> {target} — no runs directory", kind="dropped")
    runs_dir = Path(brr_dir) / "runs"
    event_id = target if target.startswith("evt-") else ""
    run_id = target if target.startswith("run-") else ""
    event_file = Path(f.ctx.inbox_dir) / f"{event_id}.md" if (event_id and f.ctx.inbox_dir) else None
    if event_id and not run_id and event_file is not None and event_file.is_file():
        run_id = str(protocol.parse_frontmatter(event_file.read_text(encoding="utf-8")).get("run_id") or "")
    manifest = Run.from_file(run_manifest_path(runs_dir, run_id)) if run_id else None
    if manifest is None:
        return _refuse(f, f"topic refused: assign {slug} -> {target} — no run found for it")
    if manifest.meta.get(run_topic.META_UNSET) is not True or manifest.meta.get(run_topic.META_EVENT_TOPIC):
        return _refuse(
            f, f"topic refused: assign {slug} -> {target} — {manifest.id} is not topic-unset "
            "(an assigned act is never reclassified)",
        )
    event_id = event_id or manifest.event_id
    manifest.meta[run_topic.META_EVENT_TOPIC] = canonical
    manifest.meta.pop(run_topic.META_UNSET, None)
    manifest.save(runs_dir)
    run_topic.stamp_event(f.ctx.inbox_dir, event_id, topic=canonical)
    run_topic.assign(
        home, canonical, kind="event", ref=event_id, run=manifest.id,
        thread=manifest.conversation_key or "",
    )
    return _accepted(f, "assign", f"topic assign {canonical} -> {event_id} ({manifest.id} was topic-unset)",
                     [canonical])

