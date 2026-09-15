"""Heddles light themselves — the reader behind the layer panel.

design-the-loom.md §20: a heddle is a **layer** over the work — a topic file
``<home>/surface/topics/<slug>.md`` whose frontmatter carries a ``rune:`` and
a ``signature:`` (the things that light it). The frame, never the weaver,
lights a heddle when a row of the run matches its signature; brightness
decays with the time since the last match, so the rail shows *where the work
has been lately*, not a declaration (§19 item 1: derivation over
declaration).

The signature has four optional lists::

    ---
    rune: ⚒
    signature:
      places: [src/brr/**, docs/*.md]    # repo-relative paths or globs
      words: [ToS, GDPR]                  # case-insensitive terms
      produce: [knot, "#1975"]            # loom kinds and/or refs
      threads: [run-260914-1644-ocgl]     # run or dispatch-event ids
    ---
    # The title

    ids: old-alias

and each list is matched against one kind of row the frame already writes:

| term      | row                                                             |
| --------- | --------------------------------------------------------------- |
| places    | ``runs/<id>/boundaries.jsonl`` → ``place.path`` (move 3)        |
| words     | the run's delivered messages (``<node>/messages/*.md``)         |
| produce   | ``runs/<id>/produce.jsonl`` (move 4) and ``.relics.jsonl``       |
| threads   | this run's own id, its strands' dispatch/run ids                 |

plus **claims** — ``.topics`` (the weaver's self-declared claim) and a
strand's ``topic:`` (declared on its ``spawn:``) — each a match with its own
``at``. A claim is one more signature term; it decays like the rest.

plus **assignments** (move 5c, design-the-loom §21) — the rows this run
wrote into ``surface/topics/<slug>.index.jsonl`` (an alias's file lights the
topic that absorbed it), kind ``assigned`` at the row's ``at``: an assigned
act brightens a heddle exactly like a match. The index, its alias-resolving
reader :func:`index`, the thread map and the dispatch-time :func:`propose`
live at the bottom of this module.

**Brightness** is ``0.5 ** (age / HALF_LIFE_SECONDS)`` with a one-hour half
life. Why an hour: the chip is read at the tempo of boundaries (seconds to
minutes) and a chase inside a session touches its places every few minutes,
so a layer worked an hour ago reads half-lit, a morning's work (four hours)
reads ~6% — under :data:`DARK_BELOW`, off the chip — and yesterday is dark.
Shorter (minutes) and a heddle flickers out during one long test run; longer
(a day) and every topic the run ever brushed stays lit, which is the
declaration this replaces.

**Cheap.** :func:`light` is incremental: per run it remembers the byte offset
of each ledger it has read and the messages it has scored, so a call reads
only what was appended since the last one. The cache resets when a topic
file changes (a new signature re-scores the run from the start — bounded by
the boundary transcript's own cap). A home with no topic files has no
heddles and costs one ``listdir``. Nothing here runs a subprocess.

The grammar is a deliberate YAML *subset* (the package carries no YAML
dependency): top-level ``key: value`` scalars, and ``signature:`` with
indented ``name: [a, b]`` flow lists or ``- item`` block lists. One leniency
real YAML lacks, on purpose: an unquoted ``- #1975`` is the ref, not a
comment, because that is what a person means by it. :func:`render_topic`
always writes such refs quoted.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Brightness halves every hour since the last match (see module docstring).
HALF_LIFE_SECONDS = 3600.0
#: Below this a heddle is dark: off the chip, still in the portal list.
DARK_BELOW = 0.05
#: How many names the chip leads with.
CHIP_BRIGHTEST = 3

TOPICS_DIRNAME = "topics"
#: Where ``topic: retire`` and ``topic: merge`` move a topic file — a nested
#: directory, so neither this reader nor ``warpGraph.isTopicFile`` sees it.
RETIRED_DIRNAME = "retired"
SIGNATURE_KINDS = ("places", "words", "produce", "threads")
LOOM_KINDS = ("knot", "heddle", "card", "page")
#: Relic kinds read as the loom's — the same table move 5 carries as
#: ``hud.RELIC_LOOM_KIND`` (#1976); kept local until that lands.
RELIC_LOOM_KIND = {
    "commit": "knot", "merge": "knot",
    "branch": "heddle", "pr": "heddle",
    "issue": "card", "item": "card",
    "kb": "page",
}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
BOUNDARIES_NAME = "boundaries.jsonl"
PRODUCE_NAME = "produce.jsonl"
RELICS_NAME = ".relics.jsonl"
TOPICS_CLAIM_NAME = ".topics"
MESSAGES_DIRNAME = "messages"
_DELIVERED_STATUSES = frozenset({"delivered", "collected", "carried"})
_READ_CAP_BYTES = 4 * 1024 * 1024


# ── The topic file ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Signature:
    places: tuple[str, ...] = ()
    words: tuple[str, ...] = ()
    produce: tuple[str, ...] = ()
    threads: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.places or self.words or self.produce or self.threads)

    def as_dict(self) -> dict[str, list[str]]:
        return {kind: list(getattr(self, kind)) for kind in SIGNATURE_KINDS}

    def union(self, other: "Signature") -> "Signature":
        return Signature(**{
            kind: _dedupe(getattr(self, kind) + getattr(other, kind))
            for kind in SIGNATURE_KINDS
        })


@dataclass(frozen=True)
class Topic:
    slug: str
    path: Path
    title: str
    rune: str = ""
    signature: Signature = field(default_factory=Signature)
    #: ``ids:`` aliases (a merged topic keeps its absorbed ids), slug excluded.
    aliases: tuple[str, ...] = ()
    #: ``split-into:`` — non-empty means the file is a breadcrumb, never lit.
    split_into: tuple[str, ...] = ()
    #: Other top-level frontmatter scalars, kept verbatim on rewrite.
    extra: tuple[tuple[str, str], ...] = ()
    #: Everything after the frontmatter block (title line, rows, body).
    body: str = ""


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return tuple(out)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _strip_comment(text: str) -> str:
    """Drop a trailing `` # comment`` outside quotes and flow brackets. A
    value that *starts* with ``#`` is kept (the ``- #1975`` leniency), and so
    is one inside ``[…]`` (``[knot, #1975]``)."""
    quote = ""
    depth = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif char == "#" and depth == 0 and index > 0 and text[index - 1] in " \t":
            return text[:index].rstrip()
    return text


def _flow_list(text: str) -> list[str]:
    inner = text.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    items: list[str] = []
    current: list[str] = []
    quote = ""
    for char in inner:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            current.append(char)
        elif char == ",":
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    items.append("".join(current))
    return [v for v in (_unquote(item) for item in items) if v]


def split_frontmatter(text: str) -> tuple[list[str] | None, str]:
    """``(frontmatter lines, rest)`` — ``None`` when the file has no block."""
    normalised = text.replace("\r\n", "\n")
    lines = normalised.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, normalised
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], "\n".join(lines[index + 1:])
    return None, normalised


def parse_frontmatter(lines: list[str]) -> tuple[dict[str, str], Signature]:
    """The subset grammar: scalars, and ``signature:`` with four lists."""
    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {kind: [] for kind in SIGNATURE_KINDS}
    in_signature = False
    current_list: str | None = None
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        stripped = raw.strip()
        if indent == 0:
            current_list = None
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = _strip_comment(value.strip())
            if key == "signature":
                in_signature = True
                if value.startswith("{"):
                    in_signature = False  # flow maps are not in the subset
                continue
            in_signature = False
            if key:
                scalars[key] = _unquote(value)
            continue
        if not in_signature:
            continue
        if stripped.startswith("- "):
            if current_list is not None:
                item = _unquote(_strip_comment(stripped[2:].strip()))
                if item:
                    lists[current_list].append(item)
            continue
        key, sep, value = stripped.partition(":")
        key = key.strip()
        if not sep or key not in lists:
            current_list = None
            continue
        value = _strip_comment(value.strip())
        if value:
            lists[key].extend(_flow_list(value))
            current_list = None
        else:
            current_list = key
    signature = Signature(**{kind: _dedupe(lists[kind]) for kind in SIGNATURE_KINDS})
    return scalars, signature


_TITLE_RE = re.compile(r"^#[ \t]+(.*)$")
_TOPIC_ROW_RE = re.compile(r"^(ids|split-into):[ \t]*(.*)$")


def _title_and_rows(body: str) -> tuple[str | None, dict[str, str]]:
    """The frontend's ``parsePage`` over the body: a ``# `` title, then rows."""
    lines = body.split("\n")
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    title = None
    if index < len(lines):
        match = _TITLE_RE.match(lines[index])
        if match:
            title = match.group(1).strip()
            index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    rows: dict[str, str] = {}
    while index < len(lines):
        match = _TOPIC_ROW_RE.match(lines[index])
        if not match:
            break
        rows.setdefault(match.group(1), match.group(2).strip())
        index += 1
    return title, rows


def _ids(value: str) -> tuple[str, ...]:
    return _dedupe(t for t in re.split(r"[\s,]+", value or "") if SLUG_RE.match(t))


def parse_topic_text(slug: str, path: Path, text: str) -> Topic:
    fm_lines, rest = split_frontmatter(text)
    scalars, signature = parse_frontmatter(fm_lines or [])
    title, rows = _title_and_rows(rest)
    rune = scalars.pop("rune", "")
    return Topic(
        slug=slug,
        path=path,
        title=title or slug,
        rune=rune,
        signature=signature,
        aliases=tuple(a for a in _ids(rows.get("ids", "")) if a != slug),
        split_into=_ids(rows.get("split-into", "")),
        extra=tuple(scalars.items()),
        body=rest,
    )


def parse_topic_file(path: Path) -> Topic | None:
    slug = path.stem
    if path.suffix != ".md" or slug == "index" or not SLUG_RE.match(slug):
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_topic_text(slug, path, text)


def _yaml_item(value: str) -> str:
    if (
        not value
        or value[0] in "#&*!|>%@`\"'[]{},-?:"
        or re.search(r"[:,\[\]{}]\s|\s#|[\"']", value)
        or value != value.strip()
    ):
        return json.dumps(value, ensure_ascii=False)
    return value


def render_frontmatter(topic: Topic) -> str:
    lines = ["---"]
    if topic.rune:
        lines.append(f"rune: {_yaml_item(topic.rune)}")
    for key, value in topic.extra:
        lines.append(f"{key}: {value}")
    lines.append("signature:")
    for kind in SIGNATURE_KINDS:
        values = getattr(topic.signature, kind)
        rendered = ", ".join(_yaml_item(v) for v in values)
        lines.append(f"  {kind}: [{rendered}]")
    lines.append("---")
    return "\n".join(lines) + "\n"


def render_topic(topic: Topic) -> str:
    """The file text: canonical frontmatter, then the body as authored."""
    body = topic.body.lstrip("\n")
    if not body.strip():
        body = f"# {topic.title}\n"
    return render_frontmatter(topic) + body if body.endswith("\n") else (
        render_frontmatter(topic) + body + "\n"
    )


def topics_dir(account_home: Path | None) -> Path | None:
    if account_home is None:
        return None
    return Path(account_home) / "surface" / TOPICS_DIRNAME


def load_topics(directory: Path | None) -> list[Topic]:
    """Every live topic in *directory* — split breadcrumbs and nested files
    (``retired/``) excluded. Sorted by slug."""
    if directory is None or not directory.is_dir():
        return []
    topics: list[Topic] = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return []
    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        topic = parse_topic_file(Path(entry.path))
        if topic is not None and not topic.split_into:
            topics.append(topic)
    return topics


# ── Matching ─────────────────────────────────────────────────────────────


def _glob_regex(pattern: str) -> re.Pattern[str]:
    pattern = pattern.strip()
    while pattern.startswith("./"):
        pattern = pattern[2:]
    has_glob = any(ch in pattern for ch in "*?[")
    if not has_glob:
        base = re.escape(pattern.rstrip("/"))
        return re.compile(rf"^{base}(?:/.*)?$")
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(out) + "$")


def relative_place(path: str | None, roots: Iterable[Path | str] = ()) -> str | None:
    """*path* as a repo-relative POSIX place, or ``None`` when it cannot be.

    An absolute path under one of *roots* is relativised against the first
    that contains it; a path inside a run worktree nested in the host
    checkout (``.brr/worktrees/<id>/…``) loses that prefix too, so a place
    reads the same from either tree. A relative path is taken as already
    repo-relative. An absolute path under no root is not a place here.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    text = path.strip().replace("\\", "/")
    if text.endswith("…"):
        return None
    if text.startswith("/"):
        for root in roots:
            base = str(root).rstrip("/")
            if base and (text == base or text.startswith(base + "/")):
                text = text[len(base):].lstrip("/")
                break
        else:
            return None
    while text.startswith("./"):
        text = text[2:]
    match = re.match(r"^\.brr/worktrees/[^/]+/(.*)$", text)
    if match:
        text = match.group(1)
    return text or None


def _word_regex(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", re.IGNORECASE)


@dataclass
class _Compiled:
    topic: Topic
    places: list[re.Pattern[str]]
    words: list[re.Pattern[str]]
    kinds: frozenset[str]
    refs: frozenset[str]
    threads: frozenset[str]
    names: frozenset[str]


def _compile(topic: Topic) -> _Compiled:
    sig = topic.signature
    kinds = frozenset(p.lower() for p in sig.produce if p.lower() in LOOM_KINDS)
    refs = frozenset(_norm_ref(p) for p in sig.produce if p.lower() not in LOOM_KINDS)
    return _Compiled(
        topic=topic,
        places=[_glob_regex(p) for p in sig.places if p.strip()],
        words=[_word_regex(w) for w in sig.words if w.strip()],
        kinds=kinds,
        refs=frozenset(r for r in refs if r),
        threads=frozenset(t.strip() for t in sig.threads if t.strip()),
        names=frozenset((topic.slug,) + topic.aliases),
    )


def _norm_ref(ref: object) -> str:
    text = str(ref or "").strip()
    if re.fullmatch(r"\d+", text):
        return "#" + text
    return text.lower() if re.fullmatch(r"[0-9a-fA-F]{7,40}", text) else text


def _ref_matches(term: str, ref: str) -> bool:
    if not term or not ref:
        return False
    if term == ref:
        return True
    # A sha term matches a longer/shorter spelling of the same commit.
    if re.fullmatch(r"[0-9a-f]{7,40}", term) and re.fullmatch(r"[0-9a-f]{7,40}", ref):
        return ref.startswith(term) or term.startswith(ref)
    return False


# ── Time ─────────────────────────────────────────────────────────────────


def _epoch(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.timestamp()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def event_epoch(event: Mapping[str, Any]) -> float | None:
    """When an inbox event was made: ``created``, else the id's nanoseconds."""
    at = _epoch(event.get("created") or event.get("created_at"))
    if at is not None:
        return at
    match = re.match(r"^evt-(\d{16,})-", str(event.get("id") or ""))
    return int(match.group(1)) / 1e9 if match else None


def brightness(last_match_epoch: float | None, now_epoch: float) -> float:
    if last_match_epoch is None:
        return 0.0
    age = max(0.0, now_epoch - last_match_epoch)
    return round(0.5 ** (age / HALF_LIFE_SECONDS), 4)


# ── The reader ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Heddle:
    slug: str
    rune: str
    brightness: float
    last_match_at: str | None
    matched_by: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "rune": self.rune,
            "brightness": self.brightness,
            "last_match_at": self.last_match_at,
            "matched_by": list(self.matched_by),
        }


@dataclass
class _RunState:
    fingerprint: tuple = ()
    compiled: list[_Compiled] = field(default_factory=list)
    offsets: dict[str, int] = field(default_factory=dict)
    first_seen: dict[str, float] = field(default_factory=dict)
    scored: set[str] = field(default_factory=set)
    #: slug → (last match epoch, {kind: epoch of that kind's last match})
    last: dict[str, dict[str, float]] = field(default_factory=dict)


_CACHE: dict[str, _RunState] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 64


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _fingerprint(directory: Path | None) -> tuple:
    if directory is None:
        return ()
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return ()
    rows = []
    for entry in entries:
        if not entry.name.endswith(".md"):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        rows.append((entry.name, stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(rows))


def _hit(state: _RunState, slug: str, kind: str, at: float | None) -> None:
    if at is None:
        return
    record = state.last.setdefault(slug, {})
    if at > record.get(kind, float("-inf")):
        record[kind] = at


def _read_new_lines(state: _RunState, path: Path) -> list[str]:
    key = str(path)
    offset = state.offsets.get(key, 0)
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size < offset:
        offset = 0
    if size == offset:
        state.offsets[key] = offset
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(min(size - offset, _READ_CAP_BYTES))
    except OSError:
        return []
    end = chunk.rfind(b"\n")
    if end < 0:
        return []
    state.offsets[key] = offset + end + 1
    return chunk[: end + 1].decode("utf-8", errors="replace").splitlines()


def _json_rows(lines: list[str]) -> Iterable[dict[str, Any]]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _score_boundaries(state: _RunState, run_dir: Path | None, roots, run_id: str) -> None:
    if run_dir is None:
        return
    for row in _json_rows(_read_new_lines(state, Path(run_dir) / BOUNDARIES_NAME)):
        at = _epoch(row.get("at"))
        place = row.get("place") if isinstance(row.get("place"), dict) else {}
        rel = relative_place(place.get("path"), roots)
        for compiled in state.compiled:
            slug = compiled.topic.slug
            if rel is not None and any(p.match(rel) for p in compiled.places):
                _hit(state, slug, "place", at)
            if run_id and run_id in compiled.threads:
                _hit(state, slug, "thread", at)


def _score_produce_row(state: _RunState, kind: str | None, ref: str | None, at: float | None, extra_refs=()) -> None:
    refs = [_norm_ref(r) for r in (ref, *extra_refs) if r]
    for compiled in state.compiled:
        if kind and kind in compiled.kinds:
            _hit(state, compiled.topic.slug, "produce", at)
            continue
        if any(_ref_matches(term, r) for term in compiled.refs for r in refs):
            _hit(state, compiled.topic.slug, "produce", at)


def _score_produce(state: _RunState, run_dir: Path | None, outbox_dir: Path | None, now: float) -> None:
    if run_dir is not None:
        for row in _json_rows(_read_new_lines(state, Path(run_dir) / PRODUCE_NAME)):
            extra = []
            if row.get("pr") not in (None, ""):
                extra.append(f"#{row.get('pr')}")
            _score_produce_row(
                state, str(row.get("kind") or "") or None, row.get("ref"),
                _epoch(row.get("at")), extra,
            )
    if outbox_dir is not None:
        path = Path(outbox_dir) / RELICS_NAME
        key = str(path)
        if key not in state.first_seen:
            state.first_seen[key] = now
        for record in _json_rows(_read_new_lines(state, path)):
            relic_kind = str(record.get("kind") or "")
            kind = RELIC_LOOM_KIND.get(relic_kind)
            refs: list[str] = []
            for name in ("sha", "name", "path", "address", "url"):
                if record.get(name):
                    refs.append(str(record[name]))
            if record.get("number") not in (None, ""):
                refs.append(f"#{record['number']}")
            if record.get("pr") not in (None, ""):
                refs.append(f"#{record['pr']}")
            # A relic carries no time; it is seen when this reader first reads it.
            at = _epoch(record.get("at")) or now
            _score_produce_row(state, kind, None, at, refs)


def _score_messages(state: _RunState, node_dir: Path | None) -> None:
    if node_dir is None:
        return
    directory = Path(node_dir) / MESSAGES_DIRNAME
    try:
        names = sorted(e.name for e in os.scandir(directory) if e.name.endswith(".md"))
    except OSError:
        return
    worded = [c for c in state.compiled if c.words]
    for name in names:
        key = f"msg:{name}"
        if key in state.scored:
            continue
        try:
            text = (directory / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm_lines, body = split_frontmatter(text)
        meta: dict[str, str] = {}
        for line in fm_lines or []:
            k, sep, v = line.partition(":")
            if sep:
                meta[k.strip()] = v.strip()
        status = meta.get("status", "")
        if status not in _DELIVERED_STATUSES:
            if status in ("undeliverable",):
                state.scored.add(key)
            continue
        state.scored.add(key)
        at = _epoch(meta.get("delivered_at")) or _epoch(meta.get("created_at"))
        for compiled in worded:
            if any(p.search(body) for p in compiled.words):
                _hit(state, compiled.topic.slug, "word", at)


def _slug_for(state: _RunState, name: str) -> str | None:
    for compiled in state.compiled:
        if name in compiled.names:
            return compiled.topic.slug
    return None


def _score_claims(
    state: _RunState,
    outbox_dir: Path | None,
    strand_claims: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
) -> None:
    if outbox_dir is not None:
        path = Path(outbox_dir) / TOPICS_CLAIM_NAME
        try:
            stat = path.stat()
            first = path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        except (OSError, IndexError):
            first = []
            stat = None
        if stat is not None and first:
            line = first[0].strip()
            if line.lower().startswith("topics:"):
                line = line[len("topics:"):].strip()
            for token in re.split(r"[\s·]+", line):
                slug = _slug_for(state, token) if SLUG_RE.match(token or "") else None
                if slug:
                    _hit(state, slug, "claim", stat.st_mtime)
    for claim in strand_claims or ():
        if not isinstance(claim, Mapping):
            continue
        at = _epoch(claim.get("at"))
        for token in claim.get("topics") or ():
            slug = _slug_for(state, str(token))
            if slug:
                _hit(state, slug, "claim", at)
        ids = {str(claim.get(k) or "") for k in ("event", "run")}
        for compiled in state.compiled:
            if ids & compiled.threads:
                _hit(state, compiled.topic.slug, "thread", at)
    for event in events or ():
        if not isinstance(event, Mapping):
            continue
        if str(event.get("source") or "") not in ("spawn_completed", "spawn_submitted"):
            continue
        at = event_epoch(event)
        ids = {
            str(event.get(k) or "")
            for k in ("spawned_by_run", "spawned_by_event", "id")
        } - {""}
        for compiled in state.compiled:
            if ids & compiled.threads:
                _hit(state, compiled.topic.slug, "thread", at)
        raw = str(event.get("spawn_topics") or "")
        for token in re.split(r"[\s,·]+", raw):
            slug = _slug_for(state, token) if token else None
            if slug:
                _hit(state, slug, "claim", at)
        if event.get("spawn_pr_number") not in (None, ""):
            _score_produce_row(state, "heddle", f"#{event.get('spawn_pr_number')}", at,
                               [str(event.get("spawn_published_branch") or "")])


def light(
    account_home: Path | None,
    run_dir: Path | None,
    now: object = None,
    *,
    outbox_dir: Path | None = None,
    node_dir: Path | None = None,
    roots: Iterable[Path | str] = (),
    run_id: str = "",
    strand_claims: Iterable[Mapping[str, Any]] = (),
    events: Iterable[Mapping[str, Any]] = (),
) -> list[Heddle]:
    """Score every topic's signature against the run's rows; brightest first.

    *run_dir* is the run's ``<brr>/runs/<id>/`` (boundaries, produce);
    *outbox_dir* its outbox (``.topics``, ``.relics.jsonl``); *node_dir* its
    account run node (delivered ``messages/``); *roots* the checkouts a
    boundary's absolute path is relativised against; *strand_claims* the
    ``topic:`` claims this run's ``spawn:`` requests declared
    (``{topics, at, event}``); *events* the pending events the portal already
    read (a strand's return). Never raises; unknown is dark, never a guess.
    """
    now_epoch = _epoch(now) if now is not None else None
    if now_epoch is None:
        now_epoch = _dt.datetime.now(tz=_dt.timezone.utc).timestamp()
    directory = topics_dir(account_home)
    fingerprint = _fingerprint(directory)
    if not fingerprint:
        return []
    key = str(run_dir or outbox_dir or "")
    roots = tuple(roots)
    try:
        with _CACHE_LOCK:
            state = _CACHE.get(key)
            if state is None or state.fingerprint != fingerprint:
                state = _RunState(fingerprint=fingerprint)
                state.compiled = [_compile(t) for t in load_topics(directory)]
                if len(_CACHE) >= _CACHE_MAX and key not in _CACHE:
                    _CACHE.pop(next(iter(_CACHE)))
                _CACHE[key] = state
            _score_boundaries(state, run_dir, roots, run_id)
            _score_produce(state, run_dir, outbox_dir, now_epoch)
            _score_messages(state, node_dir)
            _score_claims(state, outbox_dir, strand_claims, events)
            _score_assigned(state, account_home, run_id)
            heddles = []
            for compiled in state.compiled:
                record = state.last.get(compiled.topic.slug, {})
                last = max(record.values()) if record else None
                matched_by = tuple(sorted(record, key=lambda k: -record[k]))
                heddles.append(Heddle(
                    slug=compiled.topic.slug,
                    rune=compiled.topic.rune,
                    brightness=brightness(last, now_epoch),
                    last_match_at=_iso(last),
                    matched_by=matched_by,
                ))
    except Exception:  # noqa: BLE001 - a reader must never sink a boundary
        return []
    heddles.sort(key=lambda h: (-h.brightness, h.slug))
    return heddles


# ── The chip ─────────────────────────────────────────────────────────────


def short_name(slug: str) -> str:
    return slug[4:] if slug.startswith("the-") and len(slug) > 4 else slug


def chip_segment(heddles: Iterable[Mapping[str, Any] | Heddle]) -> str | None:
    """``♦ workshop · loom · summit · (post, legal)`` — or ``None`` when dark.

    The brightest three lit heddles lead, brightest first; any other lit
    (above :data:`DARK_BELOW`) heddles follow in parentheses after one more
    ``·``, so a dim layer is visible without competing with the chase.
    Brightness is not printed: the chip changes only when the order or the
    set does, which is exactly its change gate.
    """
    rows: list[tuple[float, str]] = []
    for heddle in heddles or ():
        if isinstance(heddle, Heddle):
            slug, value = heddle.slug, heddle.brightness
        elif isinstance(heddle, Mapping):
            slug, value = str(heddle.get("slug") or ""), heddle.get("brightness")
        else:
            continue
        if not slug or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if value < DARK_BELOW:
            continue
        rows.append((float(value), slug))
    if not rows:
        return None
    rows.sort(key=lambda r: (-r[0], r[1]))
    lead = [short_name(slug) for _, slug in rows[:CHIP_BRIGHTEST]]
    rest = [short_name(slug) for _, slug in rows[CHIP_BRIGHTEST:]]
    text = "♦ " + " · ".join(lead)
    if rest:
        text += " · (" + ", ".join(rest) + ")"
    return text


# ── Assignment: the index, the thread map, the proposal (move 5c) ────────
#
# design-the-loom §21: every act that enters the loom belongs to exactly one
# topic at the moment of entry. The topic is the *file*: one
# ``surface/topics/<slug>.index.jsonl`` per topic, one row per act
# ``{kind, ref, at, run}``, appended by the frame at assignment. A merged
# topic's index file stays where it was; its slug lives on as an ``ids:``
# alias of the topic it merged into, and :func:`index` reads both — a merge
# is a remap at read time, never a rewrite of history.

INDEX_SUFFIX = ".index.jsonl"
#: ``{thread key: {topic, at, run}}`` — the thread's last assigned topic, the
#: proposal's fallback. A JSON file, not a topic: ``load_topics`` and the warp
#: graph read ``*.md`` only.
THREADS_NAME = "threads.json"
INDEX_KINDS = ("message", "strand", "bolt", "fold", "produce", "event")
_INDEX_LOCK = threading.Lock()
_SPAN_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|m|h|d|w)\s*$", re.IGNORECASE)
_SPAN_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_span(text: object) -> float | None:
    """``"2h"`` → 7200.0 · ``"3d"`` → 259200.0 · anything else → ``None``."""
    match = _SPAN_RE.match(str(text or ""))
    if not match:
        return None
    return float(match.group(1)) * _SPAN_UNIT[match.group(2).lower()]


def index_path(account_home: Path | None, slug: str) -> Path | None:
    directory = topics_dir(account_home)
    if directory is None or not SLUG_RE.match(slug or ""):
        return None
    return directory / f"{slug}{INDEX_SUFFIX}"


def _topic_names(account_home: Path | None) -> dict[str, Topic]:
    """Every live topic by slug *and* by each ``ids:`` alias — first claim wins,
    the warp graph's rule."""
    names: dict[str, Topic] = {}
    topics = load_topics(topics_dir(account_home))
    for topic in topics:
        names.setdefault(topic.slug, topic)
    for topic in topics:
        for alias in topic.aliases:
            names.setdefault(alias, topic)
    return names


def resolve_slug(account_home: Path | None, name: object) -> str | None:
    """The live topic *name* means — itself, or the topic that absorbed it as
    an alias. ``None`` for an unknown name, a split breadcrumb, or no home."""
    text = str(name or "").strip()
    if not SLUG_RE.match(text):
        return None
    topic = _topic_names(account_home).get(text)
    return topic.slug if topic is not None else None


def _read_index_file(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            data = handle.read(_READ_CAP_BYTES)
    except OSError:
        return []
    return list(_json_rows(data.decode("utf-8", errors="replace").splitlines()))


def append_index(
    account_home: Path | None,
    slug: str,
    *,
    kind: str,
    ref: str,
    at: object = None,
    run: str = "",
) -> bool:
    """Append one act to *slug*'s index. ``True`` when a row was written.

    Deduped by ``ref``: an act has one id, and a second assignment of the same
    ref (a retried drain, a heartbeat that saw the same control twice) writes
    nothing. The caller resolves *slug* first (:func:`resolve_slug`); an
    unknown kind, an empty ref or no home writes nothing. Never raises.
    """
    path = index_path(account_home, slug)
    ref = " ".join(str(ref or "").split())
    if path is None or kind not in INDEX_KINDS or not ref:
        return False
    epoch = _epoch(at) if at is not None else None
    if epoch is None:
        epoch = _dt.datetime.now(tz=_dt.timezone.utc).timestamp()
    row = {"kind": kind, "ref": ref, "at": _iso(epoch), "run": str(run or "")}
    try:
        with _INDEX_LOCK:
            if any(r.get("ref") == ref for r in _read_index_file(path)):
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def index(
    account_home: Path | None,
    slug: str,
    since: object = None,
    *,
    now: object = None,
) -> list[dict[str, Any]]:
    """*slug*'s acts, oldest first — its own file and every alias's, merged by
    ``at`` and deduped by ``ref`` (the earliest row wins).

    *slug* may itself be an alias: it resolves to the topic that absorbed it,
    so asking for a merged-away name reads the merged whole. An unknown slug
    still reads its own file if one exists (a retired topic's history is not
    erased). *since* is an epoch, an ISO time, or a span (``"2h"``, ``"3d"``)
    counted back from *now*. Never raises.
    """
    if not SLUG_RE.match(str(slug or "")):
        return []
    names = _topic_names(account_home)
    topic = names.get(slug)
    files = [slug]
    if topic is not None:
        files = [topic.slug, *topic.aliases]
        if slug not in files:
            files.append(slug)
    cutoff: float | None = None
    if since is not None:
        span = parse_span(since)
        if span is not None:
            now_epoch = _epoch(now) if now is not None else None
            if now_epoch is None:
                now_epoch = _dt.datetime.now(tz=_dt.timezone.utc).timestamp()
            cutoff = now_epoch - span
        else:
            cutoff = _epoch(since)
    rows: list[tuple[float, int, dict[str, Any]]] = []
    order = 0
    for name in _dedupe(files):
        path = index_path(account_home, name)
        if path is None:
            continue
        for row in _read_index_file(path):
            at = _epoch(row.get("at"))
            if at is None or not row.get("ref"):
                continue
            rows.append((at, order, row))
            order += 1
    rows.sort(key=lambda item: (item[0], item[1]))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for at, _, row in rows:
        ref = str(row.get("ref"))
        if ref in seen:
            continue
        seen.add(ref)
        # `since` filters after the dedupe: an act is dated by its first
        # assignment, so a later duplicate row never re-enters the window.
        if cutoff is not None and at < cutoff:
            continue
        out.append({
            "kind": row.get("kind"), "ref": ref, "at": row.get("at"),
            "run": row.get("run") or "",
        })
    return out


def _threads_path(account_home: Path | None) -> Path | None:
    directory = topics_dir(account_home)
    return directory / THREADS_NAME if directory is not None else None


def _read_threads(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_thread_topic(
    account_home: Path | None, thread: str, slug: str, *, at: object = None, run: str = "",
) -> bool:
    """Remember *slug* as *thread*'s last assigned topic. Never raises."""
    path = _threads_path(account_home)
    thread = str(thread or "").strip()
    if path is None or not thread or not SLUG_RE.match(slug or ""):
        return False
    epoch = _epoch(at) if at is not None else None
    try:
        with _INDEX_LOCK:
            data = _read_threads(path)
            data[thread] = {
                "topic": slug,
                "at": _iso(epoch if epoch is not None else _dt.datetime.now(
                    tz=_dt.timezone.utc).timestamp()),
                "run": str(run or ""),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                           encoding="utf-8")
            os.replace(tmp, path)
    except OSError:
        return False
    return True


def thread_topic(account_home: Path | None, thread: str) -> str | None:
    """*thread*'s last assigned topic, resolved through aliases — ``None`` when
    the thread has none or it no longer names a live topic."""
    path = _threads_path(account_home)
    if path is None or not str(thread or "").strip():
        return None
    record = _read_threads(path).get(str(thread).strip())
    if not isinstance(record, dict):
        return None
    return resolve_slug(account_home, record.get("topic"))


_PATHLIKE_RE = re.compile(r"[\w.\-]+(?:/[\w.\-*]+)+")


def propose(
    account_home: Path | None, text: object, *, thread: str = "",
) -> tuple[str | None, str]:
    """The frame's proposal for an inbound act: ``(slug, why)``.

    ``why`` is ``"signature"`` when a topic's signature matched the text best
    (most distinct terms hit: words, produce refs, thread ids, and path-shaped
    tokens against places; ties go to the slug order), ``"thread"`` when
    nothing matched and *thread*'s last assigned topic stands,
    ``"suggested"`` when neither stands and the text yields a candidate for a
    *new* heddle (:func:`suggest_slug` — minted only if the run says so), and
    ``"none"`` otherwise. A proposal names a live topic; a suggestion names
    one that does not exist yet. Never raises.
    """
    try:
        topics = load_topics(topics_dir(account_home))
        body = str(text or "")
        best: tuple[int, str] | None = None
        if topics and body.strip():
            paths = [relative_place(p) for p in _PATHLIKE_RE.findall(body)]
            paths = [p for p in paths if p]
            for topic in topics:
                compiled = _compile(topic)
                hits = sum(1 for p in compiled.words if p.search(body))
                hits += sum(
                    1 for ref in compiled.refs
                    if re.search(r"(?<![\w#])" + re.escape(ref) + r"(?!\w)", body, re.IGNORECASE)
                )
                hits += sum(1 for t in compiled.threads if t in body)
                hits += sum(1 for pattern in compiled.places if any(pattern.match(p) for p in paths))
                if hits and (best is None or hits > best[0]):
                    best = (hits, topic.slug)
        if best is not None:
            return best[1], "signature"
        fallback = thread_topic(account_home, thread) if thread else None
        if fallback:
            return fallback, "thread"
        suggested = suggest_slug(account_home, body)
        if suggested:
            return suggested, "suggested"
    except Exception:  # noqa: BLE001 - a proposal must never sink a dispatch
        return None, "none"
    return None, "none"


# ── The suggestion: a new heddle's name, minted from the event (move 5d) ──
#
# When nothing proposes a live topic, the frame offers a *candidate* so that
# minting costs the resident one line (`new` alone in `.topic`). The name is
# the text's first noun-ish phrase of one to three words — a heuristic with
# no part-of-speech model, on purpose: leading filler (greetings, pronouns,
# modals, request verbs, articles) is skipped, the phrase runs until a
# function word or a punctuation mark, and a leading ``the`` is kept when a
# content word follows it (the house style: ``the-loom``, ``the-post``). A
# suggestion is a prior, never an assignment: nothing is indexed until a run
# writes `new`.

SUGGEST_MAX_WORDS = 3
SUGGEST_MAX_CHARS = 40
_SUGGEST_SCAN_LINES = 6
_SUGGEST_WORD_MAX = 24

#: Skipped while the phrase has not started: filler, pronouns, modals,
#: request verbs, articles and prepositions.
_SUGGEST_LEAD = frozenset("""
hey hi hello yo ok okay so well and but also then now just please pls plz thanks thank
hmm ah oh um umm lol btw fyi re fw fwd yes yeah yep sure maybe quick quickly question
can could would will should shall may might must you u i im ive id we were weve let lets
us me my our your his her their its it this that these those there here what whats how
why when where who which whose do does did done is are was be been being am have has
had get got go going gonna wanna want wants need needs like a an to of in on at for
with from by about into onto over under as if or not no some any all look looking check
see tell show give take make try write read fix add open run start draft review update
find think work build ship send post merge land move put keep use help new next another
more talk discuss chat ask say plan explain consider figure
""".split())
#: End a phrase that has started.
_SUGGEST_BOUNDARY = frozenset("""
a an and or but for of to in on at with from by about into onto over under as if than
then so is are was were be been am it its this that these those i you we they he she me
us him them my your our their can could would should will shall may might must do does
did has have had not no please when where what why how which who whose there here the
vs via per after before since until while because
""".split())
_SUGGEST_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?|[^\sa-z0-9]")
_SUGGEST_MARKUP_RE = re.compile(r"https?://\S+|`[^`]*`|\[([^\]]*)\]\([^)]*\)|[@#][\w-]+")


def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def slug_phrase(text: object) -> str | None:
    """The first noun-ish phrase of 1–3 words in *text*, slugified — or
    ``None`` when no line yields one (empty text, only filler, a script the
    ASCII fold drops). Pure: no home, no collision check."""
    in_fence = False
    scanned = 0
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped == "---":
            continue
        scanned += 1
        if scanned > _SUGGEST_SCAN_LINES:
            break
        line = _SUGGEST_MARKUP_RE.sub(lambda m: m.group(1) or " ", stripped)
        line = re.sub(r"^(?:[#>*+\-]+|\d+[.)])\s*", "", line)
        phrase = _phrase(_SUGGEST_TOKEN_RE.findall(_ascii_fold(line).lower()))
        if phrase:
            return phrase
    return None


def _phrase(tokens: list[str]) -> str | None:
    phrase: list[str] = []
    for token in tokens:
        if not token[0].isalnum():
            if token in "_'’":  # joined inside words by the fold, never a stop
                continue
            if phrase and phrase != ["the"]:
                break
            phrase = []
            continue
        word = re.sub(r"['’]", "", token)[:_SUGGEST_WORD_MAX]
        if not phrase or phrase == ["the"]:
            if word == "the":
                phrase = ["the"]
                continue
            if word in _SUGGEST_LEAD or word in _SUGGEST_BOUNDARY:
                phrase = []
                continue
            phrase.append(word)
        elif word in _SUGGEST_BOUNDARY:
            break
        else:
            phrase.append(word)
        if len(phrase) >= SUGGEST_MAX_WORDS:
            break
    if not phrase or phrase == ["the"] or all(w.isdigit() for w in phrase if w != "the"):
        return None
    slug = "-".join(phrase)
    while len(slug) > SUGGEST_MAX_CHARS and "-" in slug:
        slug = slug.rsplit("-", 1)[0]
    slug = slug[:SUGGEST_MAX_CHARS].strip("-")
    return slug if SLUG_RE.match(slug) else None


def taken_slugs(account_home: Path | None) -> set[str]:
    """Every name a new heddle must not take: live slugs and their ``ids:``
    aliases, split breadcrumbs, retired files, and any slug that still has an
    index file (minting over it would inherit that history at read time)."""
    names = set(_topic_names(account_home))
    directory = topics_dir(account_home)
    if directory is None:
        return names
    for pattern in ("*.md", f"{RETIRED_DIRNAME}/*.md", f"*{INDEX_SUFFIX}"):
        for path in directory.glob(pattern):
            names.add(path.name.split(".", 1)[0])
    return names


def suggest_slug(account_home: Path | None, text: object) -> str | None:
    """A candidate slug for a new heddle, from *text*: :func:`slug_phrase`,
    with ``-2``, ``-3``… appended until it collides with nothing in
    :func:`taken_slugs`. ``None`` with no home (nothing could mint it) or
    when the text yields no phrase. Never raises."""
    if account_home is None:
        return None
    try:
        base = slug_phrase(text)
        if not base:
            return None
        taken = taken_slugs(account_home)
        if base not in taken:
            return base
        for n in range(2, 1000):
            candidate = f"{base[:SUGGEST_MAX_CHARS - len(str(n)) - 1].rstrip('-')}-{n}"
            if candidate not in taken:
                return candidate
    except Exception:  # noqa: BLE001 - a suggestion must never sink a dispatch
        return None
    return None


def _score_assigned(state: _RunState, account_home: Path | None, run_id: str) -> None:
    """Index rows this run wrote light their topic — kind ``assigned``, at the
    row's ``at``, the same decay as any match. Read incrementally per file;
    an alias's file lights the topic that absorbed it."""
    if not run_id or account_home is None:
        return
    for compiled in state.compiled:
        for name in compiled.names:
            path = index_path(account_home, name)
            if path is None:
                continue
            for row in _json_rows(_read_new_lines(state, path)):
                if str(row.get("run") or "") == run_id:
                    _hit(state, compiled.topic.slug, "assigned", _epoch(row.get("at")))
