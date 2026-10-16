"""Read Grok subscription quota over HTTP, without starting the client.

Only the stored, unexpired first-party access key is used. Authentication is
read-only: no refresh grant, browser, subprocess, or credential writes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .grok_status import supported

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = ".grok-usage-levels.json"
COLLECTED_SLOTS: frozenset[str] = frozenset({"quota"})
DEFAULT_TIMEOUT_SECONDS = 12.0
#: Shared by dashboard/daemon readers; the loom adds its own lock/attempt floor.
DEFAULT_TTL_SECONDS = 600.0
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


BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
AUTH_SCOPE = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828"


class _ReadingError(ValueError):
    """A collector diagnostic safe to persist without request/credential data."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the stored bearer key to a redirect target.
        return None


def _access_key(env: dict[str, str]) -> dict[str, Any]:
    home = Path(env.get("GROK_HOME") or Path(env.get("HOME") or Path.home()) / ".grok")
    path = Path(env.get("GROK_AUTH_PATH") or home / "auth.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _ReadingError("grok access key absent or auth.json unreadable") from None
    auth = data.get(AUTH_SCOPE) if isinstance(data, dict) else None
    if (not isinstance(auth, dict) or not isinstance(auth.get("key"), str)
            or not auth["key"].strip()):
        raise _ReadingError("grok first-party access key absent")
    try:
        expiry = datetime.fromisoformat(auth["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise _ReadingError("grok access key expiry absent or invalid") from None
    if expiry.timestamp() <= time.time():
        raise _ReadingError("grok access key expired; read-only collector cannot refresh it")
    return auth


def capture_usage_raw(
    *, cwd: Path | str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> bytes:
    """GET the request captured from Grok 1.0.60's billing extension.

    ``cwd`` remains accepted for collector compatibility. The endpoint is
    pinned: an inherited proxy-base override must not receive this credential.
    HTTP errors are terminal; in particular 401 never starts token refresh.
    """
    auth = _access_key(env if env is not None else os.environ)
    headers = {"Authorization": "Bearer " + auth["key"],
               "X-XAI-Token-Auth": "xai-grok-cli",
               "X-Grok-Client-Version": "1.0.60", "X-Grok-Client-Mode": "interactive"}
    if isinstance(auth.get("user_id"), str):
        headers["X-UserId"] = auth["user_id"]
    request = urllib.request.Request(BILLING_URL, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout_seconds) as response:
        return response.read(1024 * 1024)


def parse_billing(raw: bytes | str) -> dict[str, Any]:
    """Only a weekly bucket actually declared by the billing service is fuel."""
    data = json.loads(raw)
    config = data.get("config") if isinstance(data, dict) else None
    if not isinstance(config, dict):
        raise _ReadingError("grok billing response has no config")
    period = config.get("currentPeriod")
    if not isinstance(period, dict) or period.get("type") != "USAGE_PERIOD_TYPE_WEEKLY":
        raise _ReadingError("grok billing response has no weekly period")
    used = config.get("creditUsagePercent")
    if (isinstance(used, bool) or not isinstance(used, (int, float))
            or not math.isfinite(used) or not 0 <= used <= 100):
        raise _ReadingError("grok billing response has no valid usage percentage")
    try:
        reset = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
        if reset.tzinfo is None:
            raise ValueError
        epoch = reset.timestamp()
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        raise _ReadingError("grok billing response has no valid reset timestamp") from None
    display = reset.astimezone(timezone.utc).strftime("%b %d, %I:%M%p (UTC)").replace("AM", "am").replace("PM", "pm")
    return {"source": "grok billing HTTP", "updated_at": _updated_at(),
            "week_used_percentage": used, "week_reset": period["end"],
            "week_resets_at": epoch,
            "quota": {"summary": f"week {100 - used:g}% left (resets {display})",
                      "buckets": {"week": {"remaining_percentage": 100 - used}},
                      "week_resets_at": epoch, "reset_timezone": "UTC"}}


def capture_levels(**kwargs: Any) -> dict[str, Any]:
    """A failed read is absent quota with a named error, never a live zero."""
    try:
        return parse_billing(capture_usage_raw(**kwargs))
    except urllib.error.HTTPError as exc:
        error = f"grok billing HTTP {exc.code}; no authentication refresh attempted"
    except (urllib.error.URLError, OSError):
        error = "grok billing request failed"
    except _ReadingError as exc:
        error = str(exc)
    except (ValueError, TypeError, OverflowError):
        # urllib can include a malformed Authorization value in its exception.
        error = "invalid grok credential or billing response"
    return {"source": "grok billing HTTP", "updated_at": _updated_at(), "error": error}


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


def _grok_installed(env: dict[str, str] | None = None) -> bool:
    path = (env if env is not None else os.environ).get("PATH")
    return shutil.which("grok", path=path) is not None


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
    if not _grok_installed(env):
        # No grok on this host: absent, not an error, and never a probe.
        return load_snapshot(cache_dir)
    levels = capture_levels(env=env, **kwargs)
    write_snapshot(cache_dir, levels)
    return levels
