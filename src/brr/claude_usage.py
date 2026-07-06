"""Claude Code interactive ``/usage`` quota collector.

Claude's head-less ``--print`` result JSON carries spend and context accounting
but not subscription quota windows. The quota surface that *does* exist today is
the interactive TUI's ``/usage`` panel. This module drives that panel through a
short-lived pseudo-terminal in ``--ax-screen-reader`` mode (flat text, no chrome),
types ``/usage``, parses the screen text, and stores the same ``levels`` snapshot
shape the portal facets already consume.

The collector is deliberately best-effort and throttled by the caller: even the
optimized probe takes a few seconds, so it should be treated as a cached
daemon-side probe, not a hook command that runs at every tool boundary.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SNAPSHOT_NAME = ".claude-usage-levels.json"

_CLAUDE_FLAVOURS = {"claude"}

COLLECTED_SLOTS: frozenset[str] = frozenset({"quota"})

DEFAULT_MODEL = "haiku"
DEFAULT_BOOT_SECONDS = 2.5
DEFAULT_TIMEOUT_SECONDS = 8.0
# The optimized probe costs ~3.5s. Refresh runs on the daemon's 30s
# heartbeat, so any TTL below the beat interval means "probe every beat" —
# 10s buys the freshest data the heartbeat can deliver, at one PTY spawn
# (~3.5s) per beat while a claude run is live (maintainer's call,
# 2026-07-03: cost data freshest when needed). Raise via
# BRR_CLAUDE_USAGE_TTL if the per-beat spawn ever bites; the meaningful
# values are multiples of the beat.
DEFAULT_TTL_SECONDS = 10.0
TTL_ENV_VAR = "BRR_CLAUDE_USAGE_TTL"
# After session+week parse, keep reading briefly: per-model week buckets
# ("Current week (Fable)") render after the all-models bucket.
COMPLETE_GRACE_SECONDS = 0.5

_ENV_CONTAMINANTS = {
    "CLAUDE_CODE_SAFE_MODE",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
    "CLAUDE_EFFORT",
    "AI_AGENT",
}

_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC_RE = re.compile(r"\x1b(?:[78=>]|[()#][0-9A-Za-z]|[@-Z\\-_])")
_USED_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*used", re.IGNORECASE)


def supported(runner_name: str | None) -> bool:
    """True when *runner_name*'s Shell is Claude Code."""
    if not runner_name:
        return False
    slug = str(runner_name).strip().lower()
    return any(slug == f or slug.startswith(f) for f in _CLAUDE_FLAVOURS)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _updated_at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clean_terminal_text(raw: bytes | str) -> str:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    text = text.replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return text


def _line_key(line: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", line.lower())


_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Two shapes seen live: undated session resets ("11:59pm (Europe/Berlin)")
# and dated week resets ("Jul 10, 12am (Europe/Berlin)") — no year either way.
_RESET_EPOCH_RE = re.compile(
    r"^(?:(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2}),\s*)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)\s*"
    r"\((?P<zone>[^)]+)\)$",
    re.IGNORECASE,
)


def _reset_epoch(reset_text: str | None, *, now: datetime | None = None) -> float | None:
    """Best-effort UTC epoch for a TUI-scraped reset string, or ``None``.

    Claude's ``/usage`` panel never gives a raw epoch the way Codex's
    session rollout does (``codex_status._fmt_reset``) — only free text, in
    two different shapes: the session (5h) reset carries no date
    (``"11:59pm (Europe/Berlin)"``), the week reset carries month+day but no
    year (``"Jul 10, 12am (Europe/Berlin)"``). This computes the next future
    occurrence of that wall-clock time in that zone rather than parsing a
    year that isn't there. Never raises — any parse or zone failure is
    ``None``, not a guess.
    """
    if not reset_text:
        return None
    match = _RESET_EPOCH_RE.match(reset_text.strip())
    if not match:
        return None
    try:
        zone = ZoneInfo(match.group("zone").strip())
    except Exception:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    if not (1 <= hour <= 12) or not (0 <= minute <= 59):
        return None
    if match.group("ampm").lower() == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12

    now = now if now is not None else datetime.now(timezone.utc)
    now_local = now.astimezone(zone)

    mon, day = match.group("mon"), match.group("day")
    if mon:
        month = _MONTH_ABBR.get(mon.strip().lower())
        if month is None:
            return None
        try:
            candidate = now_local.replace(
                month=month, day=int(day), hour=hour, minute=minute,
                second=0, microsecond=0,
            )
        except ValueError:
            return None
        # No year in the source text: a result more than 2 days in the past
        # means the reset is actually next year (a week-boundary date named
        # just after a year turnover).
        if candidate < now_local - timedelta(days=2):
            try:
                candidate = candidate.replace(year=candidate.year + 1)
            except ValueError:
                return None
    else:
        candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_local:
            candidate = candidate + timedelta(days=1)

    return candidate.astimezone(timezone.utc).timestamp()


def _reset_text(line: str) -> str | None:
    key = _line_key(line)
    if not key.startswith("resets"):
        return None
    match = re.match(r"\s*resets\s*(.*)", line, flags=re.IGNORECASE)
    if match and match.group(1).strip():
        return match.group(1).strip()
    # Some TUI captures lose the space after "Resets".
    raw = line.strip()
    return raw[6:].strip() or None


def _parse_bucket_line(line: str) -> tuple[float, str | None] | None:
    """Parse a one-line ``Current session: N% used · resets ...`` bucket."""
    match = _USED_RE.search(line)
    if not match:
        return None
    used = _num(match.group(1))
    if used is None:
        return None
    reset = _reset_text(line)
    if reset is None:
        reset_match = re.search(r"resets\s*(.+)", line, flags=re.IGNORECASE)
        if reset_match and reset_match.group(1).strip():
            reset = reset_match.group(1).strip()
    return used, reset


def _scan_bucket(lines: list[str], start: int) -> tuple[float, str | None] | None:
    used: float | None = None
    reset: str | None = None
    for line in lines[start + 1:start + 9]:
        key = _line_key(line)
        if key.startswith("currentsession") or key.startswith("currentweek"):
            break
        if used is None:
            match = _USED_RE.search(line)
            if match:
                used = _num(match.group(1))
        if reset is None:
            reset = _reset_text(line)
        if used is not None and reset is not None:
            return used, reset
    return (used, reset) if used is not None else None


def _bucket_summary(label: str, used: float, reset: str | None) -> dict[str, Any]:
    remaining = max(0.0, 100.0 - used)
    summary = f"{label} {_fmt_pct(remaining)}% left"
    if reset:
        summary += f" (resets {reset})"
    return {
        "summary": summary,
        "used_percentage": used,
        "remaining_percentage": remaining,
        "reset": reset,
    }


def _reset_score(value: str | None) -> int:
    if not value:
        return 0
    return len(value) + value.count(" ") * 3


def _prefer_bucket(
    current: tuple[float, str | None] | None,
    candidate: tuple[float, str | None],
) -> tuple[float, str | None]:
    if current is None:
        return candidate
    # If the percentage actually changed while the panel refreshed, the later
    # capture is fresher. When the value is the same, prefer the cleanest reset
    # string; terminal repaint captures often include both a partial and a clean
    # copy of the same panel.
    if candidate[0] != current[0]:
        return candidate
    if _reset_score(candidate[1]) > _reset_score(current[1]):
        return candidate
    return current


_WEEK_LABEL_RE = re.compile(r"\s*current\s*week\s*\(([^)]*)\)", re.IGNORECASE)


def _week_model_label(line: str) -> str | None:
    """Model name for a per-model week bucket, ``None`` for the primary week.

    ``Current week (all models)`` and bare ``Current week`` are the primary
    weekly quota. ``Current week (Fable)`` — a per-model bucket the TUI added
    alongside it — returns ``"Fable"``. Only the parenthetical immediately
    after "Current week" counts; a reset timezone paren later in a compact
    one-line bucket must not be mistaken for a model label.
    """
    match = _WEEK_LABEL_RE.match(line)
    if not match:
        return None
    content = match.group(1).strip()
    if not content or _line_key(content) == "allmodels":
        return None
    return content


def parse_usage_text(raw: bytes | str) -> dict[str, Any]:
    """Normalize a Claude ``/usage`` terminal capture into a levels snapshot."""
    text = _clean_terminal_text(raw)
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    session: tuple[float, str | None] | None = None
    week: tuple[float, str | None] | None = None
    week_models: dict[str, tuple[float, str | None]] = {}

    for idx, line in enumerate(lines):
        key = _line_key(line)
        if key.startswith("currentsession"):
            found = _scan_bucket(lines, idx) or _parse_bucket_line(line)
            if found:
                session = _prefer_bucket(session, found)
        elif key.startswith("currentweek"):
            found = _scan_bucket(lines, idx) or _parse_bucket_line(line)
            if found:
                label = _week_model_label(line)
                if label is None:
                    week = _prefer_bucket(week, found)
                else:
                    week_models[label] = _prefer_bucket(
                        week_models.get(label), found
                    )

    levels: dict[str, Any] = {
        "source": "claude /usage PTY",
        "updated_at": _updated_at(),
    }
    parts: list[str] = []
    # Numeric remaining-percent per bucket, keyed for `pacing.*` policy
    # consumers (`runner_quota.binding_quota_remaining_pct`) — populated
    # alongside the rendered `summary` string so quota pacing can read a
    # number instead of parsing prose (kb/design-director-loop.md §B1).
    buckets: dict[str, Any] = {}
    if session:
        bucket = _bucket_summary("session", session[0], session[1])
        levels["session_used_percentage"] = session[0]
        levels["session_reset"] = session[1]
        # Computed, not scraped (2026-07-06) — see `_reset_epoch`'s docstring
        # for why this can't be a passthrough the way Codex's is.
        levels["session_resets_at"] = _reset_epoch(session[1])
        parts.append(str(bucket["summary"]))
        buckets["session"] = {"remaining_percentage": bucket["remaining_percentage"]}
    if week:
        bucket = _bucket_summary("week", week[0], week[1])
        levels["week_used_percentage"] = week[0]
        levels["week_reset"] = week[1]
        levels["week_resets_at"] = _reset_epoch(week[1])
        parts.append(str(bucket["summary"]))
        buckets["week"] = {"remaining_percentage": bucket["remaining_percentage"]}
    week_model_buckets: dict[str, Any] = {}
    for label, found in week_models.items():
        # Elide the reset in the summary when it matches the primary week's —
        # the per-model buckets share the weekly window, and the Runner line
        # this summary feeds should not repeat the same timestamp.
        summary_reset = None if week and found[1] == week[1] else found[1]
        bucket = _bucket_summary(f"{label} week", found[0], summary_reset)
        levels.setdefault("week_models", {})[label] = {
            "used_percentage": found[0],
            "reset": found[1],
            "resets_at": _reset_epoch(found[1]),
        }
        parts.append(str(bucket["summary"]))
        week_model_buckets[label] = {
            "remaining_percentage": bucket["remaining_percentage"]
        }
    if week_model_buckets:
        buckets["week_models"] = week_model_buckets
    if parts:
        levels["quota"] = {"summary": "; ".join(parts)}
        if buckets:
            levels["quota"]["buckets"] = buckets
    return levels


def _probe_env(env: dict[str, str] | None = None) -> dict[str, str]:
    probe_env = dict(env or os.environ)
    for key in _ENV_CONTAMINANTS:
        probe_env.pop(key, None)
    probe_env.setdefault("TERM", "xterm-256color")
    probe_env.setdefault("NO_COLOR", "1")
    return probe_env


def _usage_command(
    model: str | None = None, env: dict[str, str] | None = None
) -> list[str]:
    env = env or os.environ
    chosen = model or env.get("BRR_CLAUDE_USAGE_MODEL") or DEFAULT_MODEL
    return ["claude", "--ax-screen-reader", "--model", chosen, "--safe-mode"]


def _quota_buckets_complete(levels: dict[str, Any]) -> bool:
    return "session_used_percentage" in levels and "week_used_percentage" in levels


def _read_available(master_fd: int, chunks: list[bytes], deadline: float) -> None:
    while time.monotonic() < deadline:
        timeout = min(0.1, max(0.0, deadline - time.monotonic()))
        ready, _, _ = select.select([master_fd], [], [], timeout)
        if master_fd not in ready:
            continue
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            return
        if not data:
            return
        chunks.append(data)


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass


def capture_usage_raw(
    *,
    cwd: Path | str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    model: str | None = None,
    env: dict[str, str] | None = None,
) -> bytes:
    """Drive ``claude`` interactively, type ``/usage``, and return raw TUI bytes.

    The command runs in ``--ax-screen-reader`` mode for flat, fast output and in
    ``--safe-mode`` so project/local hooks and plugins do not fire recursively.
    It still uses Claude's normal subscription auth and does not send a model
    prompt.
    """
    deadline = time.monotonic() + max(3.0, float(timeout_seconds))
    boot_seconds = min(DEFAULT_BOOT_SECONDS, max(0.0, float(timeout_seconds) - 0.5))
    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        proc = subprocess.Popen(
            _usage_command(model, env),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=str(cwd) if cwd else None,
            env=_probe_env(env),
            close_fds=True,
        )
    except Exception:
        os.close(master)
        os.close(slave)
        raise

    os.close(slave)
    chunks: list[bytes] = []
    try:
        boot_deadline = min(deadline, time.monotonic() + boot_seconds)
        _read_available(master, chunks, boot_deadline)
        while time.monotonic() < boot_deadline:
            time.sleep(min(0.05, boot_deadline - time.monotonic()))
        if time.monotonic() < deadline:
            os.write(master, b"/usage\r")
        grace_deadline: float | None = None
        while time.monotonic() < deadline:
            _read_available(master, chunks, min(deadline, time.monotonic() + 0.1))
            if _quota_buckets_complete(parse_usage_text(b"".join(chunks))):
                # session+week are in; linger briefly for trailing per-model
                # week buckets that render after the all-models one.
                if grace_deadline is None:
                    grace_deadline = min(
                        deadline, time.monotonic() + COMPLETE_GRACE_SECONDS
                    )
                elif time.monotonic() >= grace_deadline:
                    break
        _terminate_process(proc)
    finally:
        try:
            os.close(master)
        except OSError:
            pass
    return b"".join(chunks)


def capture_levels(
    *,
    cwd: Path | str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    model: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a best-effort Claude usage levels snapshot, never raising."""
    try:
        levels = parse_usage_text(
            capture_usage_raw(
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                model=model,
                env=env,
            )
        )
        if "quota" not in levels:
            levels["error"] = "no quota buckets parsed from /usage screen"
        return levels
    except Exception as exc:  # noqa: BLE001 - collector must not break a run
        return {
            "source": "claude /usage PTY",
            "updated_at": _updated_at(),
            "error": str(exc) or exc.__class__.__name__,
        }


def write_snapshot(outbox_dir: Path | None, levels: dict[str, Any]) -> Path | None:
    if outbox_dir is None:
        return None
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        path = outbox_dir / SNAPSHOT_NAME
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(levels, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError:
        return None


def load_snapshot(outbox_dir: Path | None) -> dict[str, Any] | None:
    if outbox_dir is None:
        return None
    path = Path(outbox_dir) / SNAPSHOT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _fresh(path: Path, max_age_seconds: float) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < max_age_seconds
    except OSError:
        return False


def _ttl_seconds(env: dict[str, str] | None = None) -> float:
    raw = (env or os.environ).get(TTL_ENV_VAR)
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_TTL_SECONDS


def load_or_refresh_snapshot(
    outbox_dir: Path | None,
    *,
    cwd: Path | str | None = None,
    max_age_seconds: float | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    model: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Read a fresh cached snapshot, or refresh it through the PTY probe.

    ``max_age_seconds`` defaults to :data:`DEFAULT_TTL_SECONDS`, overridable
    via the :data:`TTL_ENV_VAR` environment variable.
    """
    if outbox_dir is None:
        return None
    if max_age_seconds is None:
        max_age_seconds = _ttl_seconds(env)
    path = Path(outbox_dir) / SNAPSHOT_NAME
    if _fresh(path, max_age_seconds):
        return load_snapshot(outbox_dir)
    levels = capture_levels(
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        model=model,
        env=env,
    )
    write_snapshot(outbox_dir, levels)
    return levels
