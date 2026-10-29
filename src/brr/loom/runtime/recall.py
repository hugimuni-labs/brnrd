"""``related:``: the threads closest to a new wish, settled ones included.

Recall comes at the act that needs it, not at wake. ``send --wish new:``
(which is also how a thread opens), ``wish new`` and ``ask`` print one
advice line naming the closest threads, so a body about to stamp a duplicate
sees the thread that already holds it. Nothing is refused.

Closeness is BM25 over each thread's wish lines (README frontmatter and the
ledger), its id, its headline and its first paragraph. It is lexical because
the duplicates it must catch share whole phrases, and it needs no index file
and no dependency: about a hundred short documents are read per call.
"""

from __future__ import annotations

import math
import re
from collections import Counter

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


def related_line(home: Home, query: str, *, facts=None, exclude=()) -> str:
    """``related: forge-link (settled 10-24) · machines · follow-main``, or ``""``."""
    docs = corpus(home, facts)
    named = []
    for _score, thread in rank(query, docs, exclude=set(exclude))[:TOP]:
        status = docs[thread][0]
        if status in _CLOSED:
            when = _settled_on(home, thread)
            named.append(f"{thread} ({status}{' ' + when if when else ''})")
        else:
            named.append(thread)
    if not named:
        return ""
    return ("related: " + " · ".join(named)
            + " (closest threads by wish and headline; read one before stamping a duplicate)")
