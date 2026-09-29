"""A say's words, kept at home — never on the server.

His 2026-09-29 steer: "I don't want to keep the messages on the server itself,
but showing them I think I'd want to." The cloud nulls a message's body once it
is answered (``brnrd/inbox.py``), so an answered say had no text anywhere a
reader could reach; the daemon's conversation store
(``<repo>/.brr/conversations/<thread>/<evt>.jsonl``) is the one copy that
survives, and it is local and unversioned.

So a stamped say is written to the account home, under the warp:

    surface/warp/says/<evt>.md     the message: who, when, which thread, words
    surface/warp/says/index.md     home repo URL + branch + the ids — no text

``warp/`` is already exempt from the wake's page walk, and this directory is
nested, so the item parsers (``asks.build_asks``, ``warpGraph.isWarpItemFile``)
skip it by construction. The corpus mirror ships only ``index.md`` from here
(``account.corpus_files``); the hosted reader turns an id listed there into a
link to the file on the home repo's forge, where the words live.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SAYS_DIRNAME = "says"
INDEX_NAME = "index.md"
#: Corpus-relative prefix of the says directory — the mirror filter and the
#: hosted reader both key on it.
SAYS_PREFIX = "surface/warp/says/"
INDEX_PATH = SAYS_PREFIX + INDEX_NAME

_EVENT_RE = re.compile(r"^evt-\d{16,20}-[a-z0-9]{2,8}$")
_INDEX_ROW_RE = re.compile(r"^(home|branch|says):[ \t]*(.*)$")


def says_dir(warp_root: Path) -> Path:
    return warp_root / SAYS_DIRNAME


def is_event_id(value: str) -> bool:
    return bool(_EVENT_RE.fullmatch(value or ""))


def find_event_record(event_id: str, repo_roots: Iterable[Path]) -> dict[str, Any] | None:
    """The inbound ``kind: event`` row for *event_id* from a conversation store.

    The store is one ``<evt>.jsonl`` per event under each thread directory;
    its first ``event`` row is the message as it arrived (body intact — the
    local copy is never nulled). ``None`` when no store holds it.
    """
    if not is_event_id(event_id):
        return None
    for root in repo_roots:
        conv = Path(root) / ".brr" / "conversations"
        if not conv.is_dir():
            continue
        for path in sorted(conv.glob(f"*/{event_id}.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if (
                    isinstance(row, dict)
                    and row.get("kind") == "event"
                    and row.get("event_id") == event_id
                    and isinstance(row.get("body"), str)
                ):
                    return row
    return None


def render_say(record: Mapping[str, Any]) -> str:
    """The say file: the message verbatim under a few provenance rows."""
    event_id = str(record.get("event_id", ""))
    rows = [
        ("at", record.get("ts")),
        ("from", record.get("correspondent_key")),
        ("thread", record.get("conversation_key")),
        ("source", record.get("source")),
    ]
    head = "\n".join(f"{key}: {value}" for key, value in rows if value)
    body = str(record.get("body", "")).rstrip()
    return f"# {event_id}\n\n{head}\n\n---\n\n{body}\n"


def _home_forge(home_root: Path) -> tuple[str | None, str]:
    """``(https web URL of the home repo | None, branch)``."""
    from . import forges, gitops

    branch = "main"
    try:
        branch = gitops.default_branch(home_root) or gitops.current_branch(home_root) or branch
    except Exception:  # noqa: BLE001 - an unreadable branch keeps the default
        pass
    try:
        remote = gitops.default_remote(home_root) or "origin"
        url = gitops.remote_url(home_root, remote)
    except Exception:  # noqa: BLE001 - no remote ⇒ no link, never an error
        url = None
    if not url:
        return None, branch
    match = forges.detect_forge(url)
    if match is None:
        return None, branch
    return f"https://{match.host}/{match.owner}/{match.repo}", branch


def write_index(warp_root: Path) -> Path | None:
    """Rewrite ``says/index.md`` from the say files present — ids only."""
    directory = says_dir(warp_root)
    if not directory.is_dir():
        return None
    ids = sorted(
        p.stem for p in directory.glob("evt-*.md") if p.is_file() and is_event_id(p.stem)
    )
    home_root = warp_root.parent.parent
    home, branch = _home_forge(home_root)
    lines = [
        "# Says — the words live in the files beside this one, never on the server",
        "",
    ]
    if home:
        lines.append(f"home: {home}")
    lines.append(f"branch: {branch}")
    lines.append(f"says: {' '.join(ids)}")
    path = directory / INDEX_NAME
    text = "\n".join(lines) + "\n"
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return path
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return path


def _bind_item(text: str, item_id: str | None, part: str | None) -> str:
    """Grow the ``items:`` row (and a ``part <id>:`` line) in a say file's head.

    One message, one file: a say that feeds several items is written once and
    each item that stamps it is added here, so the message shows everywhere it
    went. A ``--part`` excerpt names which part of the message belongs to that
    item; the words are already in the file below, so this only points.
    """
    if not item_id:
        return text
    head, sep, body = text.partition("\n---\n")
    lines = head.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("items:"):
            items = line[len("items:"):].split()
            if item_id not in items:
                lines[i] = "items: " + " ".join([*items, item_id])
            break
    else:
        while lines and lines[-1] == "":
            lines.pop()
        lines.append(f"items: {item_id}")
    if part:
        clean = " ".join(part.split())
        row = f"part {item_id}: {clean}"
        if row not in lines:
            lines.append(row)
    return "\n".join(lines).rstrip("\n") + "\n" + sep + body if sep else "\n".join(lines)


def write_say(
    warp_root: Path | None,
    event_id: str,
    repo_roots: Iterable[Path],
    *,
    item_id: str | None = None,
    part: str | None = None,
    index: bool = True,
) -> bool:
    """Write (or extend) ``says/<evt>.md`` from the local conversation store.

    The message is copied once, as it arrived; a later stamp from another
    item only grows the file's ``items:`` row. ``False`` when nothing
    changed: no warp, an id that is not an event, no store holds the event,
    or the binding was already recorded.
    """
    if warp_root is None or not is_event_id(event_id):
        return False
    path = says_dir(warp_root) / f"{event_id}.md"
    try:
        if path.is_file():
            before = path.read_text(encoding="utf-8")
            after = _bind_item(before, item_id, part)
            if after == before:
                return False
            path.write_text(after, encoding="utf-8")
            return True
        record = find_event_record(event_id, repo_roots)
        if record is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_bind_item(render_say(record), item_id, part), encoding="utf-8")
    except OSError:
        return False
    if index:
        write_index(warp_root)
    return True


def account_repo_roots(ctx: Any) -> list[Path]:
    """Every repo root the account knows — where conversation stores live."""
    roots: list[Path] = []
    for repo in (getattr(ctx, "repos", None) or {}).values():
        root = getattr(repo, "root", None)
        if root is not None:
            roots.append(Path(root))
    return roots


def backfill(warp_root: Path, repo_roots: Iterable[Path]) -> dict[str, int]:
    """Write a say file for every event id already on an item's ``says:`` row."""
    from .asks import _load_warp_files, _parse_markdown, _split

    roots = list(repo_roots)
    written = missing = present = 0
    for name, text in _load_warp_files(warp_root):
        item_id = name[:-3]
        rows = _parse_markdown(item_id, text)["rows"]
        for event_id in _split(rows.get("says", "")):
            if not is_event_id(event_id):
                continue
            existed = (says_dir(warp_root) / f"{event_id}.md").exists()
            write_say(warp_root, event_id, roots, item_id=item_id, index=False)
            if existed:
                present += 1
            elif (says_dir(warp_root) / f"{event_id}.md").exists():
                written += 1
            else:
                missing += 1
    write_index(warp_root)
    return {"written": written, "present": present, "missing": missing}


def parse_index(text: str) -> dict[str, Any]:
    """``{"home", "branch", "says": set}`` from an ``index.md`` body."""
    out: dict[str, Any] = {"home": None, "branch": "main", "says": set()}
    for line in text.splitlines():
        match = _INDEX_ROW_RE.match(line.strip())
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "says":
            out["says"] = {v for v in value.split() if is_event_id(v)}
        elif value:
            out[key] = value
    return out


def say_url(index: Mapping[str, Any], event_id: str) -> str | None:
    """The forge link for a say listed in *index*; ``None`` otherwise."""
    home = index.get("home")
    if not home or event_id not in (index.get("says") or ()):
        return None
    if not str(home).startswith("https://"):
        return None
    return f"{home}/blob/{index.get('branch') or 'main'}/{SAYS_PREFIX}{event_id}.md"


def is_mirrored(corpus_path: str) -> bool:
    """Whether a corpus path may leave the machine: from ``says/``, only the index."""
    if not corpus_path.startswith(SAYS_PREFIX):
        return True
    return corpus_path == INDEX_PATH
