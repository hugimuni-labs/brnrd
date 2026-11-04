"""What a thread's bodies spent, read from each harness's own usage records.

The unit is the daemon's (5921cc6d): cost-weighted tokens, in fresh-input
equivalents. About 96% of raw tokens are cache re-reads, so a raw count
measures context size times calls, not work.

Claude Code writes one transcript row per content block, each repeating its
message's usage: summing rows counts 2.13x (153 local transcripts,
2026-10-21). Rows are keyed by ``message.id``. Codex's rollout carries a
cumulative total; its last ``token_count`` is the session.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .ledger import kind_facts

WEIGHTS = {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_creation": 1.25}

_TOKENS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([km]?)\s*$", re.IGNORECASE)


def parse_tokens(raw: str) -> int:
    """``2m``, ``500k`` or a plain count; anything else, or zero, is refused."""
    match = _TOKENS.match(str(raw or ""))
    if not match:
        raise ValueError(f"--budget {raw!r}: expected a token count like 500k or 2m")
    value = round(float(match.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()])
    if value <= 0:
        raise ValueError(f"--budget {raw!r}: a budget is a positive count")
    return value


def format_tokens(n: int | float) -> str:
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


class _Reader:
    """One file read forward from where the last tick stopped."""

    def __init__(self):
        self.offset, self.total, self.seen = 0, 0.0, set()


_FILES: dict[Path, _Reader] = {}


def _claude_rows(path: Path) -> float:
    reader = _FILES.setdefault(path, _Reader())
    try:
        size = path.stat().st_size
        if size < reader.offset:
            reader.__init__()
        if size == reader.offset:
            return reader.total
        with path.open("rb") as handle:
            handle.seek(reader.offset)
            chunk = handle.read(size - reader.offset)
    except OSError:
        return reader.total
    # Only whole lines: the harness may be mid-write on the last one.
    end = chunk.rfind(b"\n") + 1
    reader.offset += end
    for line in chunk[:end].splitlines():
        if b'"usage"' not in line:
            continue
        try:
            row = json.loads(line)
            message = row["message"]
            usage = message["usage"]
        except (ValueError, KeyError, TypeError):
            continue
        if row.get("type") != "assistant" or not isinstance(usage, dict):
            continue
        key = message.get("id") or row.get("requestId")
        if key in reader.seen:
            continue
        if key:
            reader.seen.add(key)
        reader.total += sum(WEIGHTS[name] * float(usage.get(field) or 0) for name, field in (
            ("input", "input_tokens"), ("output", "output_tokens"),
            ("cache_read", "cache_read_input_tokens"),
            ("cache_creation", "cache_creation_input_tokens")))
    return reader.total


def claude_projects() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def claude_spent(room: Path) -> float:
    """Every session Claude Code kept for the body's working directory.

    A body runs in ``<room>/self``; Claude names its folder by that path with
    each non-alphanumeric character a dash. Subagents keep their own files
    under ``<session>/subagents/``: their spend is the strand's too.
    """
    folder = claude_projects() / re.sub(r"[^A-Za-z0-9]", "-", str(Path(room).resolve() / "self"))
    if not folder.is_dir():
        return 0.0
    return sum(_claude_rows(path) for path in [*folder.glob("*.jsonl"), *folder.glob("*/subagents/*.jsonl")])


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


_ROLLOUTS: dict[str, Path | None] = {}


def _codex_total(path: Path) -> float:
    """The last cumulative ``total_token_usage`` in a rollout, weighted."""
    reader = _FILES.setdefault(path, _Reader())
    try:
        size = path.stat().st_size
        if size == reader.offset:
            return reader.total
        with path.open("rb") as handle:
            handle.seek(max(size - 200_000, 0))
            lines = handle.read().splitlines()
    except OSError:
        return reader.total
    reader.offset = size
    for line in reversed(lines):
        if b'"token_count"' not in line:
            continue
        try:
            usage = json.loads(line)["payload"]["info"]["total_token_usage"]
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(usage, dict):
            continue
        cached = float(usage.get("cached_input_tokens") or 0)
        reader.total = (WEIGHTS["input"] * (float(usage.get("input_tokens") or 0) - cached)
                        + WEIGHTS["cache_read"] * cached
                        + WEIGHTS["cache_creation"] * float(usage.get("cache_write_input_tokens") or 0)
                        + WEIGHTS["output"] * float(usage.get("output_tokens") or 0))
        break
    return reader.total


def codex_spent(room: Path) -> float:
    """Each ``codex exec`` names its session in the body log's ``thread.started``."""
    try:
        text = (Path(room) / "port" / "body.log").read_text(errors="replace")
    except OSError:
        return 0.0
    total = 0.0
    for ident in dict.fromkeys(re.findall(r'"type":"thread\.started","thread_id":"([0-9a-f-]+)"', text)):
        if ident not in _ROLLOUTS or _ROLLOUTS[ident] is None:
            found = sorted([*(codex_home() / "sessions").glob(f"*/*/*/rollout-*-{ident}.jsonl"),
                            *(codex_home() / "archived_sessions").glob(f"rollout-*-{ident}.jsonl")])
            _ROLLOUTS[ident] = found[-1] if found else None
        if _ROLLOUTS[ident] is not None:
            total += _codex_total(_ROLLOUTS[ident])
    return total


READERS = {"claude": claude_spent, "codex": codex_spent}

# A tick reads every live thread; files are read forward, and a thread's sum
# is reused for this long.
TTL = 15.0
_THREADS: dict[tuple[str, str], tuple[float, int | None]] = {}


def thread_spent(home, facts, thread: str) -> int | None:
    """Weighted tokens over every strand leased for ``thread``.

    None when no strand of the thread ran on a shell with a reader: an
    unknown is not a zero.
    """
    key = (str(home.root), thread)
    cached = _THREADS.get(key)
    if cached and time.monotonic() - cached[0] < TTL:
        return cached[1]
    strands = {}
    for lease in kind_facts(facts, "lease"):
        if lease.kind == "lease" and lease.data.get("thread") == thread:
            strands[lease.data.get("strand")] = lease.data.get("shell")
    total, known = 0.0, False
    for strand, shell in strands.items():
        reader = READERS.get(str(shell))
        if reader and strand:
            known = True
            total += reader(home.room(strand))
    value = int(total) if known else None
    _THREADS[key] = (time.monotonic(), value)
    return value


def allocation(facts, thread: str, opener: str, writers: dict[str, str], default: int) -> int | None:
    """The opening letter's ``budget`` (or ``default``), plus each later grant.

    Only a thread opened by another thread has one: it is the parent a
    strand returns to. A grant counts only from a strand of that parent, so
    a body cannot raise its own by writing a port file.
    """
    if not opener.startswith("thread:"):
        return None
    key = (id(facts), thread, len(facts), opener, default)
    if key not in _ALLOCATED:
        _ALLOCATED.clear()
        _ALLOCATED[key] = _allocation(facts, thread, opener, writers, default)
    return _ALLOCATED[key]


_ALLOCATED: dict[tuple, int] = {}


def _allocation(facts, thread, opener, writers, default) -> int:
    parent = opener.removeprefix("thread:")
    total, first = None, True
    for letter in kind_facts(facts, "letter"):
        if letter.kind != "letter" or letter.data.get("to") != f"thread:{thread}":
            continue
        grant = letter.data.get("budget")
        from_parent = writers.get(str(letter.data.get("from"))) == parent
        if first:
            first = False
            total = int(grant) if from_parent and isinstance(grant, int) and grant > 0 else default
        elif from_parent and isinstance(grant, int) and grant > 0:
            total += grant
    return default if total is None else total


def budget_text(spent: int | None, allocated: int | None) -> str:
    """The status segment: ``spent 1.2M/2M``, or ``spent 1.2M`` with no allocation."""
    if spent is None:
        return ""
    return f"spent {format_tokens(spent)}" + (f"/{format_tokens(allocated)}" if allocated else "")


def over_line(thread: str, opener: str, spent: int, allocated: int) -> str:
    """The warning a strand past its allocation reads at the crossing and every +5% (jack ``_over``)."""
    over = (spent - allocated) * 100 // allocated
    return (f"budget: thread {thread} has spent {format_tokens(spent)} of {format_tokens(allocated)} tokens (+{over}%). "
            f"Write to {opener}: what is done, what is left, and how much more you ask. "
            f"Its answer raises the allocation with `send --to thread:{thread} --budget <n>`. "
            "Past it, only landing runs: reads, edits under self/, git add/commit, `brnrd runtime …`.")


# Past the allocation a body may still land what it did (trust-guards, inbox's i1jbi):
# reads, the self's files, git's recording verbs and the loom's own verbs. Everything
# else is refused until the opener raises the allocation.
LANDING_TOOLS = frozenset({"Read", "Glob", "Grep", "LS", "TodoWrite", "ToolSearch"})
_FILE_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
_LANDING_COMMAND = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:"
    r"git(?:\s+-C\s+\S+)?\s+(?:add|commit|status|diff|log|show|rev-parse|stash\s+list)\b"
    r"|(?:\S*/)?brnrd\s+runtime\b|python3?\s+-m\s+brr\.loom\.runtime\b"
    r"|(?:cd|cat|head|tail|ls|wc|grep|rg|pwd|echo|true)\b|sed\s+-n\b)")


def _segments(command: str) -> list[str] | None:
    """The command's parts between ``;``, ``&``, ``|`` and newlines outside quotes, so a
    commit message's ``;`` is no separator. ``None`` for a command substitution outside
    single quotes: what it runs is not a landing verb, whatever the part starts with."""
    parts, part, quote, i = [], [], "", 0
    while i < len(command):
        char = command[i]
        if quote != "'" and (char == "`" or command.startswith("$(", i)):
            return None
        if char == "\\" and quote != "'" and i + 1 < len(command):
            part.append(command[i:i + 2])
            i += 2
            continue
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "&" and (command[i - 1:i] in "<>" and i or command[i + 1:i + 2] == ">"):
            pass  # a redirect (``2>&1``, ``&>``), not a separator
        elif char in ";&|\n":
            parts.append("".join(part))
            part = []
            i += 1
            continue
        part.append(char)
        i += 1
    parts.append("".join(part))
    return parts


def landing_call(tool: str, tool_input, self_dir) -> bool:
    """True when a call over budget is part of landing, not of more work."""
    if tool in LANDING_TOOLS:
        return True
    if not isinstance(tool_input, dict):
        return False
    if tool in _FILE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        try:
            Path(path).resolve().relative_to(Path(self_dir).resolve())
            return True
        except (ValueError, OSError):
            return False
    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        parts = _segments(command)
        return parts is not None and bool(command.strip()) and all(
            not part.strip() or _LANDING_COMMAND.match(part.strip()) for part in parts)
    return False


def over_refusal(thread: str, opener: str, line: str) -> str:
    """The budget refusal: what, that nothing raised it, the override, and what stays open."""
    spent = line.partition(" has spent ")[2].partition(" tokens")[0]
    return (f"refused: spend past allocation (thread {thread} has spent {spent} tokens); "
            f"no `send --budget` from {opener} raises it.\n"
            f"override: {opener} raises it with `send --to thread:{thread} --budget <n>`; write to it what is done and left.\n"
            "still open: reads, edits under self/, git add/commit/status/diff/log, `brnrd runtime …` (send-self, send, wish).")