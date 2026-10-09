"""Grok Build subscription quota from the interactive ``/usage`` panel.

The headless result envelope has no subscription windows. This best-effort
PTY probe opens only the usage panel, without a model prompt, in an isolated
temporary directory. Quota is account state; callers share one cached reading.
The captured 1.0.50 panel exposes a weekly bucket, not a session bucket.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import pty
import re
import select
import struct
import subprocess
import tempfile
import termios
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .grok_status import supported

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = ".grok-usage-levels.json"
COLLECTED_SLOTS: frozenset[str] = frozenset({"quota"})
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_BOOT_SECONDS = 6.0
DEFAULT_TTL_SECONDS = 30.0
TTL_ENV_VAR = "BRR_GROK_USAGE_TTL"

_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_CSI_RE = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")
_HEADER_RE = re.compile(r"^Weekly limit \(([^()]+)\)$")
_PERCENT_RE = re.compile(r"^[█▓▒░\s]*(\d+(?:\.\d+)?)\s*%$")
_RESET_RE = re.compile(r"^Resets:\s*(.+)$")


def _updated_at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _screen_lines(raw: bytes | str) -> list[str]:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    text = _OSC_RE.sub("", text)
    row: str | None = None

    def cursor(match: re.Match[str]) -> str:
        nonlocal row
        if match.group(3) in {"H", "f"}:
            next_row = match.group(1).split(";")[0] or "1"
            separator = " " if next_row == row else "\n"
            row = next_row
            return separator
        return ""

    # Dropping cursor positioning outright joins unrelated rows and also
    # joins words drawn at separate columns ("Weekly" + "limit").
    text = _CSI_RE.sub(cursor, text).replace("\r", "\n")
    return [re.sub(r"\s+", " ", line).strip(" │") for line in text.splitlines()]


def _reset_epoch(reset: str, *, now: datetime | None = None) -> float | None:
    """Read the observed month/day/24h clock in the host's local timezone.

    Grok prints neither year nor zone. ``timestamp`` on a naive datetime uses
    the host's timezone rules at the reset date, including DST transitions.
    """
    now = now or datetime.now()
    match = re.fullmatch(r"([A-Za-z]+) (\d{1,2}), (\d{2}):(\d{2})", reset)
    if not match:
        return None
    months = ("January February March April May June July August September "
              "October November December").split()
    try:
        candidate = datetime(
            now.year, months.index(match[1]) + 1, int(match[2]),
            int(match[3]), int(match[4]),
        )
        # Weekly windows cannot be months in the past: rollover around New Year.
        if (now - candidate).days > 2:
            candidate = candidate.replace(year=now.year + 1)
        return candidate.timestamp()
    except ValueError:
        return None


def parse_usage_text(raw: bytes | str) -> dict[str, Any]:
    """Normalize only the quota block actually observed in Grok's panel."""
    lines = _screen_lines(raw)
    levels: dict[str, Any] = {"source": "grok /usage PTY", "updated_at": _updated_at()}
    for index, line in enumerate(lines):
        header = _HEADER_RE.fullmatch(line)
        if not header:
            continue
        used: float | None = None
        reset: str | None = None
        for following in lines[index + 1:index + 9]:
            if _HEADER_RE.fullmatch(following) or following.startswith(
                ("Loading session usage", "Session usage", "Context usage")
            ):
                break
            percent = _PERCENT_RE.fullmatch(following)
            if percent:
                value = float(percent[1])
                if 0 <= value <= 100:
                    used = value
            reset_match = _RESET_RE.fullmatch(following)
            if reset_match:
                reset = reset_match[1]
        if used is None:
            continue
        remaining = 100.0 - used
        epoch = _reset_epoch(reset) if reset else None
        if reset and epoch is None:
            logger.warning("grok /usage: unparseable reset string %r", reset)
        summary = f"week {remaining:g}% left"
        if reset:
            # The boundary chip parses the shared Claude-style reset grammar.
            # Keep Grok's exact text in week_reset; normalize only the summary.
            display = (
                datetime.fromtimestamp(epoch, timezone.utc).strftime("%b %d, %I:%M%p (UTC)")
                .replace("AM", "am").replace("PM", "pm")
                if epoch is not None else f"{reset}, host local time"
            )
            summary += f" (resets {display})"
        levels.update({
            "plan_type": header[1], "week_used_percentage": used,
            "week_reset": reset, "week_resets_at": epoch,
            "quota": {
                "summary": summary,
                "buckets": {"week": {"remaining_percentage": remaining}},
                "week_resets_at": epoch,
                "reset_timezone": "host local time (not printed by Grok)",
            },
        })
    return levels


def capture_usage_raw(
    *, cwd: Path | str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> bytes:
    """Open ``/usage`` without a prompt or repository hooks; always reap Grok.

    ``cwd`` is accepted for collector compatibility; the probe deliberately
    uses a temporary directory. Inherited git discovery pins must be stripped:
    Grok's session startup can checkpoint/detach the pinned repo even from a
    different cwd.
    """
    probe_env = dict(env if env is not None else os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GROK_RESUME_SESSION", "GROK_ACTIVE_MODEL"):
        probe_env.pop(key, None)
    probe_env.setdefault("TERM", "xterm-256color")
    probe_env.setdefault("NO_COLOR", "1")
    chunks: list[bytes] = []
    master, slave = pty.openpty()
    proc = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        with tempfile.TemporaryDirectory(prefix="brr-grok-usage-") as isolated:
            proc = subprocess.Popen(
                ["grok", "--fullscreen", "--no-alt-screen", "--no-subagents",
                 "--no-auto-update"], stdin=slave, stdout=slave, stderr=slave,
                cwd=isolated, env=probe_env, close_fds=True,
            )
            os.close(slave)
            slave = -1
            started = time.monotonic()
            deadline = started + max(0.0, float(timeout_seconds))
            sent = False
            try:
                while time.monotonic() < deadline:
                    if not sent and time.monotonic() - started >= DEFAULT_BOOT_SECONDS:
                        os.write(master, b"/usage\r")
                        sent = True
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if ready:
                        try:
                            data = os.read(master, 65536)
                        except OSError:
                            break
                        if not data:
                            break
                        chunks.append(data)
                        if b"\x1b[6n" in data:
                            os.write(master, b"\x1b[1;1R")
                        levels = parse_usage_text(b"".join(chunks))
                        if levels.get("quota") and levels.get("week_reset"):
                            break
                    if not ready and proc.poll() is not None:
                        break
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
    finally:
        os.close(master)
        if slave != -1:
            os.close(slave)
    return b"".join(chunks)


def capture_levels(**kwargs: Any) -> dict[str, Any]:
    """A failed probe is an absent quota, never an invented zero reading."""
    try:
        levels = parse_usage_text(capture_usage_raw(**kwargs))
        if "quota" not in levels:
            levels["error"] = "no quota bucket parsed from /usage screen"
        return levels
    except Exception as exc:
        return {"source": "grok /usage PTY", "updated_at": _updated_at(),
                "error": str(exc) or exc.__class__.__name__}


def write_snapshot(cache_dir: Path | None, levels: dict[str, Any]) -> Path | None:
    if cache_dir is None:
        return None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / SNAPSHOT_NAME
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(levels, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError:
        return None


def load_snapshot(cache_dir: Path | None) -> dict[str, Any] | None:
    if cache_dir is None:
        return None
    try:
        data = json.loads((cache_dir / SNAPSHOT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_or_refresh_snapshot(
    cache_dir: Path | None, *, max_age_seconds: float | None = None,
    env: dict[str, str] | None = None, **kwargs: Any,
) -> dict[str, Any] | None:
    """One shared account cache, with failed refreshes explicitly recorded."""
    if cache_dir is None:
        return None
    if max_age_seconds is None:
        try:
            max_age_seconds = max(0.0, float(
                (env if env is not None else os.environ).get(TTL_ENV_VAR, DEFAULT_TTL_SECONDS)
            ))
        except ValueError:
            max_age_seconds = DEFAULT_TTL_SECONDS
    try:
        if time.time() - (cache_dir / SNAPSHOT_NAME).stat().st_mtime < max_age_seconds:
            cached = load_snapshot(cache_dir)
            if cached is not None:
                return cached
    except OSError:
        pass
    levels = capture_levels(env=env, **kwargs)
    write_snapshot(cache_dir, levels)
    return levels
