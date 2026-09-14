"""Read-only access to the resident's bench (design-the-loom.md §6, §18).

The bench is the weaver's draft layer under the kb: a fold or an
inspection at a place, one markdown file per commit —
``<bench_home>/<repo-label>/<place>/<commit>.md`` — with a small flat
frontmatter (``place``, ``commit``, ``question`` optional, ``made_at``)
and ``mark: keep|drop`` lines appended as the user marks it.

Nothing in this module writes a bench file (the fold verb is move 4 of
the loom rewrite, out of scope here) — it only lists and reads what is
already on disk, the way ``design-the-loom.md`` §18 describes the
dashboard rendering the folder: "it serves the folder; it does not
decide what is in it."

``bench_home`` is a single local filesystem root, not an
account-to-directory mapping: §18 also names the sync this needs before
the *hosted*, multi-tenant deployment can read an individual account's
home (out of scope, named there and in this move's own spec) — until
that lands, this reads whatever directory the running process is
pointed at, which is exactly the self-hosted/VPS shape
(``docs/guides/vps-install.md``) today. An unconfigured or absent root
renders as an empty list, never a 404 or a null — the same "nothing
folded yet" shape as a configured-but-empty one (playbook §Invariants,
"the five shapes of an empty result").
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FRONTMATTER_RE = re.compile(r"^---\n(.*?\n)---\n?", re.DOTALL)

# Cap on `list_bench_files` — matches the run-ledger feed's own row cap
# (`_RUN_LEDGER_API_LIMIT` in `routers/dashboard.py`); this store keys
# rows to commits rather than a subscription window, but an account with
# years of folds still owes the dashboard a bounded response.
_LIST_LIMIT = 200


def _parse_bench_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse a bench file's frontmatter and body.

    Flat ``key: value`` lines, with one exception: ``mark: keep|drop``
    lines may repeat (one appended each time the user marks the file) and
    collect into a ``marks`` list in file order, rather than the last one
    silently overwriting the rest the way a plain flat-dict parse would.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    marks: list[str] = []
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if key == "mark":
            if value:
                marks.append(value)
            continue
        meta[key] = value
    meta["marks"] = marks
    return meta, text[match.end():]


def _sort_key(entry: dict[str, Any]) -> str:
    """Newest first, by `made_at` when present and parseable.

    A missing or unparseable `made_at` sinks to the bottom rather than
    raising or silently claiming "now" — a malformed fold should read as
    the oldest thing on the bench, not the freshest.
    """
    made_at = entry.get("made_at")
    if isinstance(made_at, str) and made_at:
        try:
            dt = datetime.fromisoformat(made_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
    return ""


def _safe_root(bench_home: Path | None) -> Path | None:
    if bench_home is None:
        return None
    text = str(bench_home)
    if not text:
        return None
    return bench_home


def list_bench_files(bench_home: Path | None) -> list[dict[str, Any]]:
    """List every bench file under *bench_home*, newest first, capped.

    Empty (never absent-vs-empty ambiguity) when *bench_home* is unset or
    the folder does not exist yet — see the module docstring.
    """
    root = _safe_root(bench_home)
    if root is None or not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        # `<repo>/<place...>/<commit>.md` — at least a repo segment and a
        # commit file; a bare `<repo>/<commit>.md` has an empty place.
        if len(parts) < 2:
            continue
        repo = parts[0]
        place = "/".join(parts[1:-1])
        commit = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _ = _parse_bench_frontmatter(text)
        out.append(
            {
                "repo": meta.get("repo", repo),
                "place": meta.get("place", place),
                "commit": meta.get("commit", commit),
                "question": meta.get("question"),
                "made_at": meta.get("made_at"),
                "marks": meta.get("marks", []),
                "path": f"{repo}/{place}/{commit}",
            }
        )
    out.sort(key=_sort_key, reverse=True)
    return out[:_LIST_LIMIT]


def _resolves_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def read_bench_file(
    bench_home: Path | None, repo: str, place: str, commit: str
) -> dict[str, Any] | None:
    """Read one bench file's frontmatter + body, or ``None`` if absent.

    *repo* / *place* / *commit* arrive from the URL path — untrusted.
    The only real gate is the resolved-path containment check below
    (also closes symlink escapes, per the same class the "anything under
    .brr is writable inside every container" pitfall names for a
    different tree); segment rejection is defense in depth on top of it,
    not instead of it.
    """
    root = _safe_root(bench_home)
    if root is None or not repo or not place or not commit:
        return None
    for segment in (*place.split("/"), repo, commit):
        if segment in ("", ".", "..") :
            return None
    candidate = root / repo / place / f"{commit}.md"
    if not _resolves_within(candidate, root):
        return None
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _parse_bench_frontmatter(text)
    return {
        "repo": meta.get("repo", repo),
        "place": meta.get("place", place),
        "commit": meta.get("commit", commit),
        "question": meta.get("question"),
        "made_at": meta.get("made_at"),
        "marks": meta.get("marks", []),
        "path": f"{repo}/{place}/{commit}",
        "body": body,
    }
