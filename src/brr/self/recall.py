"""Exact existing KB lookup, with a disposable FTS5 fallback outside the home."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3

from .. import knowledge
from .home import require_home
from .memory import home_sources


def index_path(home: Path) -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser().resolve()
    path = root / "brnrd" / "self" / (hashlib.sha256(str(home.resolve()).encode()).hexdigest() + ".sqlite3")
    if path.is_relative_to(home.resolve()):
        raise ValueError("Self search cache must be outside the home; change XDG_CACHE_HOME")
    return path


def recall(home: Path, query: str, *, limit: int = 20) -> list[knowledge.SearchHit]:
    """Preserve exact substring hits; FTS adds multi-term recall on a miss.

    The cache is disposable. Refresh checks content, not just timestamps, so
    edits and deletions are reflected even when an editor preserves mtime.
    FTS availability varies with the Python SQLite build; exact search is
    still useful if SQLite was compiled without it.
    """
    require_home(home)
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    if not query.strip() or limit <= 0:
        return []
    hits = knowledge.search(home, query, limit=limit, search_sources=home_sources(home))
    if hits:
        return hits
    tokens = re.findall(r"\w+", query, re.UNICODE)
    if not tokens:
        return []
    path = index_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(path) as db:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS lines USING fts5(path UNINDEXED, line_no UNINDEXED, text)")
            db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, digest TEXT NOT NULL)")
            old = dict(db.execute("SELECT path, digest FROM files"))
            present = set()
            for source in home_sources(home):
                for doc in knowledge._iter_docs(source.root):
                    if not doc.resolve().is_relative_to(home.resolve()):
                        continue
                    rel = str(doc.relative_to(home))
                    present.add(rel)
                    text = doc.read_text(encoding="utf-8", errors="replace")
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    if old.get(rel) == digest:
                        continue
                    db.execute("DELETE FROM lines WHERE path = ?", (rel,))
                    db.executemany("INSERT INTO lines VALUES (?, ?, ?)",
                                   [(rel, i, line) for i, line in enumerate(text.splitlines(), 1)])
                    db.execute("INSERT OR REPLACE INTO files VALUES (?, ?)", (rel, digest))
            for rel in old.keys() - present:
                db.execute("DELETE FROM lines WHERE path = ?", (rel,))
                db.execute("DELETE FROM files WHERE path = ?", (rel,))
            expression = " AND ".join('"' + t + '"' for t in tokens)
            rows = db.execute("SELECT path, line_no, text FROM lines WHERE lines MATCH ? "
                              "ORDER BY bm25(lines), path, CAST(line_no AS INTEGER) LIMIT ?",
                              (expression, limit)).fetchall()
            return [knowledge.SearchHit("self knowledge (FTS5)", home / p, int(n), t.strip())
                    for p, n, t in rows]
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc):
            raise
        return []
