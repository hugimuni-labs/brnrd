"""Claude Code interactive ``/usage`` quota collector.

Claude's head-less ``--print`` result JSON carries spend and context accounting
but not subscription quota windows. The quota surface that *does* exist today is
the interactive TUI's ``/usage`` panel. This module drives that panel through a
short-lived pseudo-terminal, parses the screen text, and stores the same
``levels`` snapshot shape the portal facets already consume.

The collector is deliberately best-effort and throttled by the caller: spawning
Claude's TUI is much heavier than reading Codex's rollout JSON, so it should be
treated as a cached daemon-side probe, not a hook command that runs at every
tool boundary.
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
from pathlib import Path
from typing import Any

SNAPSHOT_NAME = ".claude-usage-levels.json"

_CLAUDE_FLAVOURS = {"claude"}

COLLECTED_SLOTS: frozenset[str] = frozenset({"quota"})

DEFAULT_MODEL = "haiku"
DEFAULT_TIMEOUT_SECONDS = 18.0
DEFAULT_TTL_SECONDS = 300.0

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
    """True when *runner_name*'s vessel is Claude Code."""
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

    for idx, line in enumerate(lines):
        key = _line_key(line)
        if key.startswith("currentsession"):
            found = _scan_bucket(lines, idx)
            if found:
                session = _prefer_bucket(session, found)
        elif key.startswith("currentweek"):
            found = _scan_bucket(lines, idx)
            if found:
                week = _prefer_bucket(week, found)

    levels: dict[str, Any] = {
        "source": "claude /usage PTY",
        "updated_at": _updated_at(),
    }
    parts: list[str] = []
    if session:
        bucket = _bucket_summary("session", session[0], session[1])
        levels["session_used_percentage"] = session[0]
        levels["session_reset"] = session[1]
        parts.append(str(bucket["summary"]))
    if week:
        bucket = _bucket_summary("week", week[0], week[1])
        levels["week_used_percentage"] = week[0]
        levels["week_reset"] = week[1]
        parts.append(str(bucket["summary"]))
    if parts:
        levels["quota"] = {"summary": "; ".join(parts)}
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
    return ["claude", "--model", chosen, "--safe-mode"]


def _read_until(
    master_fd: int,
    chunks: list[bytes],
    deadline: float,
    *,
    stop_pattern: bytes | None = None,
) -> None:
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
        if stop_pattern:
            window = b"".join(chunks[-8:])
            compact_pattern = stop_pattern.replace(b" ", b"")
            compact_window = window.replace(b" ", b"")
            if stop_pattern in window or compact_pattern in compact_window:
                return


def capture_usage_raw(
    *,
    cwd: Path | str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    model: str | None = None,
    env: dict[str, str] | None = None,
) -> bytes:
    """Drive ``claude`` interactively, type ``/usage``, and return raw TUI bytes.

    The command is run in ``--safe-mode`` so project/local hooks and plugins do
    not fire recursively. It still uses Claude's normal subscription auth and
    does not send a model prompt.
    """
    deadline = time.monotonic() + max(3.0, float(timeout_seconds))
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
        _read_until(master, chunks, min(deadline, time.monotonic() + 5.0))
        if time.monotonic() < deadline:
            os.write(master, b"/usage\r")
            _read_until(master, chunks, deadline, stop_pattern=b"Usage credits")
            _read_until(master, chunks, min(deadline, time.monotonic() + 1.5))
        # Leave the usage panel before trying to exit; terminate if the TUI keeps
        # running. The captured data is already in hand.
        try:
            os.write(master, b"\x1b")
            time.sleep(0.1)
            os.write(master, b"/exit\r")
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
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


def load_or_refresh_snapshot(
    outbox_dir: Path | None,
    *,
    cwd: Path | str | None = None,
    max_age_seconds: float = DEFAULT_TTL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    model: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Read a fresh cached snapshot, or refresh it through the PTY probe."""
    if outbox_dir is None:
        return None
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
