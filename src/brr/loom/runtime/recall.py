"""``related:``: the topic pages and threads closest to a new wish, settled ones included.

Recall comes at the act that needs it. ``send --wish new:`` (which is also
how a thread opens), ``wish new`` and ``ask`` print one advice line naming
the closest topic pages first, then the closest threads, so a body about to
stamp a duplicate sees the page that holds the reasons and the thread that
holds the work. Nothing is refused. ``recall "<words>"`` prints the same on
demand, with each page's head. The wake shows at most two heads (``wake_topics``):
the thread's own ``topic:`` pages, else the closest above ``FLOOR``.

Closeness is BM25 over each thread's wish lines (README frontmatter and the
ledger), its id, its headline and its first paragraph; and over each topic
page's slug, title, head and entry titles (``topics/<slug>.md``). Topics and
threads are ranked as two corpora, and topics are named first. It is lexical
because the duplicates it must catch share whole phrases, and it needs no
index file and no dependency: about a hundred short documents are read per call.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from .home import Home
from .selfrepo import ReadmeError, git, parse_readme

#: How many threads one line names.
TOP = 3
#: Distinct query words a thread must share to be named: one shared word is noise.
MIN_SHARED = 2
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""
    about after all also and any are because been before being both but can could did does
    each every for from had has have her here his how into its just like more most much must
    not now off once only other our out over own same she should since some such than that
    the their them then there these they this those through too under until upon very was
    way were what when where which while who whom why will with would you your
""".split())
_CLOSED = {"settled", "retired"}
#: Topic pages the related line and the wake name at most.
TOPICS = 2
#: The BM25 score a topic must reach to be shown at wake: below it, the wake shows nothing.
#: Measured on brnrd-self's 21 pages: the occasion's own area scores 10 to 17, an unrelated
#: spec at most 4.6. A second page must also reach ``SECOND`` of the first's score.
FLOOR = 6.0
SECOND = 0.6
#: Bytes one head takes at wake, and the whole block.
HEAD_MAX = 300
WAKE_MAX = 600
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BOILER = re.compile(r"\s*Entries newest first\.\s*")


def words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOP]


def _first_paragraph(body: str) -> str:
    """The prose under the headline, up to the first blank line or section."""
    lines, seen = [], False
    for line in body.splitlines():
        if line.startswith("#"):
            if seen:
                break
            continue
        if not line.strip():
            if seen:
                break
            continue
        seen = True
        lines.append(line)
    return " ".join(lines)[:800]


def corpus(home: Home, facts=None) -> dict[str, tuple[str, list[str]]]:
    """``thread -> (status, words)`` for every thread folder on disk."""
    found: dict[str, tuple[str, list[str]]] = {}
    threads = home.root / "self" / "threads"
    extra: dict[str, list[str]] = {}
    if facts is not None:
        from .wishes import wishes
        for wish in wishes(facts).values():
            if wish.thread and wish.line:
                extra.setdefault(wish.thread, []).append(wish.line)
    for path in sorted(threads.glob("*/README.md")):
        thread = path.parent.name
        try:
            text = path.read_text(encoding="utf-8")
            data = parse_readme(text)
        except (OSError, UnicodeDecodeError, ReadmeError):
            continue
        wished = [str(v) for k, v in data.items() if k.startswith("wish-")]
        body = text.split("\n---\n", 1)[-1]
        doc = " ".join([thread.replace("-", " "), str(data.get("headline", "")),
                        _first_paragraph(body), *wished, *extra.get(thread, ())])
        found[thread] = (str(data.get("status", "")), words(doc))
    return found


def topic_page(text: str) -> tuple[str, str, list[str]]:
    """(title, head, entry titles) of a topic page: the head is the prose between the
    title and the first section, the entries are its ``### `` lines, newest first."""
    title, head, entries, seen = "", [], [], False
    for line in text.splitlines():
        if line.startswith("### "):
            entries.append(line[4:].strip())
            seen = True
        elif line.startswith("## "):
            seen = True
        elif line.startswith("# ") and not title:
            title = line[2:].strip()
        elif title and not seen and line.strip():
            head.append(line.strip())
    return title, " ".join(head), entries


def topics(root: Path) -> dict[str, tuple[str, str, list[str]]]:
    """``slug -> topic_page`` for every ``topics/<slug>.md`` in a self, its README aside."""
    found: dict[str, tuple[str, str, list[str]]] = {}
    for path in sorted((Path(root) / "topics").glob("*.md")):
        if path.name == "README.md":
            continue
        try:
            found[path.stem] = topic_page(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return found


def topic_corpus(pages: dict[str, tuple[str, str, list[str]]]) -> dict[str, tuple[str, list[str]]]:
    """The topic pages as ``rank`` reads them: slug, title, head and entry titles."""
    return {slug: ("topic", words(" ".join([slug.replace("-", " "), title, head, *entries])))
            for slug, (title, head, entries) in pages.items()}


def _plain(text: str) -> str:
    return " ".join(_LINK.sub(r"\1", text).split())


def _entry(title: str) -> str:
    """``date · decision``: an entry title without its thread link."""
    return " · ".join(part for part in title.split(" · ") if not _LINK.fullmatch(part.strip()))


def head_block(slug: str, page: tuple[str, str, list[str]], most: int = HEAD_MAX) -> str:
    """A topic page at wake: its path and title, its head, its newest decisions, in ``most`` bytes."""
    title, head, entries = page
    count = f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"
    lines = [f"topics/{slug}.md · {_plain(title)} ({count})"]
    prose = _plain(_BOILER.sub(" ", head))
    if prose:
        lines.append(prose)
    if entries:
        lines.append("newest: " + "; ".join(_plain(_entry(e)) for e in entries[:2]))
    out = ""
    for line in lines:
        room = most - len(out.encode("utf-8")) - 1
        if room <= 1:
            break
        if len(line.encode("utf-8")) > room:
            line = line.encode("utf-8")[: room - 3].decode("utf-8", "ignore") + "…"
        out += line + "\n"
    return out


def pinned(readme: str) -> list[str]:
    """The slugs a thread README names in its ``topic:`` frontmatter (``fuel`` or ``[fuel, wake]``)."""
    try:
        data = parse_readme(readme)
    except ReadmeError:
        return []
    raw = data.get("topic") or data.get("topics") or ""
    items = raw if isinstance(raw, list) else str(raw).strip("[]").split(",")
    return [str(item).strip().strip("'\"") for item in items if str(item).strip()]


def wake_topics(root: Path, readme: str, query: str, most: int = WAKE_MAX) -> str:
    """``## Topics`` for a wake, or ``""``: the README's ``topic:`` pages, else the closest
    ``TOPICS`` pages by recall over ``query`` that reach ``FLOOR``. Heads only, in ``most`` bytes."""
    pages = topics(root)
    if not pages:
        return ""
    chosen = [slug for slug in pinned(readme) if slug in pages][:TOPICS]
    why = "this thread's topic pages"
    if not chosen:
        ranked = rank(query, topic_corpus(pages))[:TOPICS]
        chosen = [slug for score, slug in ranked if score >= max(FLOOR, SECOND * ranked[0][0])]
        why = "closest topic pages to this occasion"
    if not chosen:
        return ""
    head = f"## Topics\n{why}; open one before deciding in its area\n"
    each = max(0, (most - len(head.encode("utf-8"))) // len(chosen))
    return head + "".join(head_block(slug, pages[slug], each) for slug in chosen)


def rank(query: str, docs: dict[str, tuple[str, list[str]]], *, exclude=()) -> list[tuple[float, str]]:
    """BM25 scores, best first, over threads sharing at least ``MIN_SHARED`` query words."""
    asked = set(words(query))
    if not asked or not docs:
        return []
    k1, b = 1.2, 0.75
    count = len(docs)
    average = sum(len(w) for _s, w in docs.values()) / count or 1.0
    df = Counter(word for _s, w in docs.values() for word in set(w))
    scored = []
    for thread, (_status, doc) in docs.items():
        if thread in exclude:
            continue
        tf = Counter(doc)
        shared = [w for w in asked if w in tf]
        if len(shared) < MIN_SHARED:
            continue
        score = 0.0
        for word in shared:
            idf = math.log(1 + (count - df[word] + 0.5) / (df[word] + 0.5))
            score += idf * tf[word] * (k1 + 1) / (tf[word] + k1 * (1 - b + b * len(doc) / average))
        scored.append((score, thread))
    return sorted(scored, key=lambda row: (-row[0], row[1]))


def _settled_on(home: Home, thread: str) -> str:
    """``MM-DD`` of the commit that last changed the thread's status, or ``""``."""
    repo = home.root / "self"
    if not (repo / ".git").exists():
        return ""
    out = git(repo, "log", "-1", "-G", "^status:", "--format=%cs", "--",
              f"threads/{thread}/README.md", check=False).stdout.strip()
    return out[5:] if len(out) == 10 else ""


def related_threads(home: Home, query: str, *, facts=None, exclude=()) -> list[str]:
    """The ``TOP`` closest threads, a closed one with its status and date."""
    docs = corpus(home, facts)
    named = []
    for _score, thread in rank(query, docs, exclude=set(exclude))[:TOP]:
        status = docs[thread][0]
        if status in _CLOSED:
            when = _settled_on(home, thread)
            named.append(f"{thread} ({status}{' ' + when if when else ''})")
        else:
            named.append(thread)
    return named


def related_topics(root: Path, query: str, most: int = TOPICS) -> list[tuple[str, tuple[str, str, list[str]]]]:
    """The ``most`` closest topic pages of the self at ``root``, best first."""
    pages = topics(root)
    return [(slug, pages[slug]) for _score, slug in rank(query, topic_corpus(pages))[:most]]


def related_line(home: Home, query: str, *, facts=None, exclude=()) -> str:
    """``related: topics/fuel.md (6) · forge-link (settled 10-24) · machines``, or ``""``."""
    pages = [f"topics/{slug}.md ({len(page[2])})" for slug, page in related_topics(home.root / "self", query)]
    named = related_threads(home, query, facts=facts, exclude=exclude)
    if not pages and not named:
        return ""
    what = "closest topic pages, then threads" if pages else "closest threads by wish and headline"
    return ("related: " + " · ".join(pages + named)
            + f" ({what}; read one before stamping a duplicate)")


def recall_text(home: Home, query: str, *, facts=None) -> str:
    """``recall``: the closest topic pages with their heads, then the closest threads."""
    found = related_topics(home.root / "self", query, 3)
    out = "".join(head_block(slug, page, 400) for slug, page in found)
    named = related_threads(home, query, facts=facts)
    if named:
        out += "threads: " + " · ".join(named) + "\n"
    return out or "nothing shares two words with that; try other words\n"


def wake_query(readme: str, owed: Path | None) -> str:
    """What a wake is about: the thread's spec and wish lines, and the letters it answers."""
    import json
    try:
        data = parse_readme(readme)
    except ReadmeError:
        data = {}
    body = readme.split("\n---\n", 1)[-1]
    parts = [str(data.get("headline", "")), _first_paragraph(body),
             *(str(v) for k, v in data.items() if k.startswith("wish-"))]
    try:
        letters = json.loads(Path(owed).read_text(encoding="utf-8")) if owed else []
    except (OSError, ValueError):
        letters = []
    parts += [str(item.get("body", ""))[:2000] for item in letters if isinstance(item, dict)]
    return " ".join(parts)
