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
import logging
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

logger = logging.getLogger(__name__)

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
_DATE_ONLY_RESET_RE = re.compile(
    r"^(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s*\((?P<zone>[^)]+)\)$",
    re.IGNORECASE,
)
_CREDIT_SPEND_RE = re.compile(
    r"(?P<spent>[$\u20ac\u00a3]?\s*\d+(?:[.,]\d+)?)\s*/\s*"
    r"(?P<limit>[$\u20ac\u00a3]?\s*\d+(?:[.,]\d+)?)\s*spent",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"(?P<currency>[$\u20ac\u00a3])?\s*(?P<amount>\d+(?:[.,]\d+)?)")

# A per-model week bucket's reset is elided from the summary when it names the
# same instant as the primary week reset. Compared in epoch seconds, not the
# rendered strings, because the panel renders one instant two ways across a
# reset boundary (2026-07-23 live: "Jul 24, 12am (Europe/Berlin)" for
# `week_resets_at` vs "Jul 23, 11:59pm (Europe/Berlin)" for a model bucket's
# reset \u2014 the same 1784844000-vs-1784843940 instant, 60s apart in rendered
# text). 2 minutes comfortably covers that skew while staying far short of any
# *genuinely* different reset, which differs by hours.
_RESET_ELIDE_TOLERANCE_SECONDS = 120.0


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
    text = reset_text.strip()
    now = now if now is not None else datetime.now(timezone.utc)
    match = _RESET_EPOCH_RE.match(text)
    date_only_match = None if match else _DATE_ONLY_RESET_RE.match(text)
    if not match and not date_only_match:
        return None
    zone_name = (match or date_only_match).group("zone").strip()
    try:
        zone = ZoneInfo(zone_name)
    except Exception:
        return None
    now_local = now.astimezone(zone)
    hour = 0
    minute = 0
    if match:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        if not (1 <= hour <= 12) or not (0 <= minute <= 59):
            return None
        if match.group("ampm").lower() == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12

    match = match or date_only_match
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


def _quota_header_key(key: str) -> bool:
    return "currentsession" in key or "currentweek" in key


def _parse_amount(raw: str | None) -> tuple[float | None, str | None]:
    if not raw:
        return None, None
    match = _AMOUNT_RE.search(raw)
    if not match:
        return None, None
    try:
        amount = float(match.group("amount").replace(",", "."))
    except ValueError:
        return None, match.group("currency")
    return amount, match.group("currency")


def _fmt_money(amount: float, currency: str | None) -> str:
    return f"{currency or '$'}{amount:.2f}"


def _parse_credit_spend_line(line: str) -> tuple[float | None, float | None, str | None, str | None] | None:
    match = _CREDIT_SPEND_RE.search(line)
    if not match:
        return None
    spent, spent_currency = _parse_amount(match.group("spent"))
    limit, limit_currency = _parse_amount(match.group("limit"))
    reset = None
    reset_match = re.search(r"resets\s*(.+)$", line, flags=re.IGNORECASE)
    if reset_match and reset_match.group(1).strip():
        reset = reset_match.group(1).strip()
    return spent, limit, spent_currency or limit_currency, reset


def _usage_credits_summary(
    used: float | None,
    spent: float | None,
    limit: float | None,
    currency: str | None,
    reset: str | None,
) -> str:
    parts: list[str] = []
    if used is not None:
        parts.append(f"{_fmt_pct(max(0.0, 100.0 - used))}% left")
    if spent is not None and limit is not None:
        parts.append(f"{_fmt_money(spent, currency)} / {_fmt_money(limit, currency)} spent")
    elif spent is not None:
        parts.append(f"{_fmt_money(spent, currency)} spent")
    if reset:
        parts.append(f"resets {reset}")
    return "usage credits " + ("; ".join(parts) if parts else "available")


def _scan_usage_credits(lines: list[str], start: int) -> dict[str, Any] | None:
    used: float | None = None
    spent: float | None = None
    limit: float | None = None
    currency: str | None = None
    reset: str | None = None
    for offset, line in enumerate(lines[start:start + 8]):
        key = _line_key(line)
        if offset > 0 and (
            _quota_header_key(key)
            or key == "usagecredits"
            or key.startswith("last24h")
            or key.startswith("skills")
        ):
            break
        if "usagecreditsareoff" in key:
            return {
                "enabled": False,
                "summary": "usage credits off",
            }
        if used is None:
            match = _USED_RE.search(line)
            if match:
                used = _num(match.group(1))
        parsed_spend = _parse_credit_spend_line(line)
        if parsed_spend:
            spent, limit, parsed_currency, reset = parsed_spend
            currency = parsed_currency or currency
    if used is None and spent is None and limit is None and reset is None:
        return None
    remaining = max(0.0, 100.0 - used) if used is not None else None
    return {
        "enabled": True,
        "used_percentage": used,
        "remaining_percentage": remaining,
        "spent_amount": spent,
        "limit_amount": limit,
        "currency": currency,
        "reset": reset,
        "resets_at": _reset_epoch(reset),
        "summary": _usage_credits_summary(used, spent, limit, currency, reset),
    }


def _scan_bucket(lines: list[str], start: int) -> tuple[float, str | None] | None:
    used: float | None = None
    reset: str | None = None
    for line in lines[start + 1:start + 9]:
        key = _line_key(line)
        if _quota_header_key(key) or key == "usagecredits":
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


# The boot banner's second line, printed once per PTY session before `/usage`
# is even typed: `<model desc> · Claude <Tier>` (e.g. `Haiku 4.5 · Claude Max`,
# confirmed live 2026-07-23 against this very account). It is the *only* place
# in the whole capture that names the account's subscription plan — the
# `/usage` panel body itself never repeats it (issue #580). Read defensively:
# an unrecognized banner shape yields `None`, never a guess. `lines` is
# already whitespace-collapsed by `parse_usage_text`, so the pattern needs no
# squashed-text tolerance.
_PLAN_LINE_RE = re.compile(r"·\s*Claude\s+(?P<tier>[A-Za-z][\w]*(?:\s+[\w]+)*)\s*$")


def _plan_type_from_lines(lines: list[str]) -> str | None:
    """The subscription tier named in the boot banner, or ``None``.

    Kept as the tier text exactly as rendered (``"Max"``, not a lowercased or
    prefixed spelling) — nothing here is invented past what the banner says.
    """
    for line in lines:
        match = _PLAN_LINE_RE.search(line)
        if match:
            tier = match.group("tier").strip()
            if tier:
                return tier
    return None


_WEEK_LABEL_RE = re.compile(r"current\s*week\s*\(([^)]*)\)", re.IGNORECASE)


def _week_model_label(line: str) -> str | None:
    """Model name for a per-model week bucket, ``None`` for the primary week.

    ``Current week (all models)`` and bare ``Current week`` are the primary
    weekly quota. ``Current week (Fable)`` — a per-model bucket the TUI added
    alongside it — returns ``"Fable"``. Only the parenthetical immediately
    after "Current week" counts; a reset timezone paren later in a compact
    one-line bucket must not be mistaken for a model label.
    """
    match = _WEEK_LABEL_RE.search(line)
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
    usage_credits: dict[str, Any] | None = None

    for idx, line in enumerate(lines):
        key = _line_key(line)
        if "usagecredits" in key:
            found_credits = _scan_usage_credits(lines, idx)
            if found_credits:
                usage_credits = found_credits
        elif "currentsession" in key:
            found = _scan_bucket(lines, idx) or _parse_bucket_line(line)
            if found:
                session = _prefer_bucket(session, found)
        elif "currentweek" in key:
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
    plan_type = _plan_type_from_lines(lines)
    if plan_type:
        levels["plan_type"] = plan_type
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
        # Elide the reset in the summary when it names the same instant as the
        # primary week's — the per-model buckets share the weekly window, and
        # the Runner line this summary feeds should not repeat the same
        # timestamp. Comparing the *parsed epochs* rather than the rendered
        # strings matters because Claude's own panel renders one instant two
        # ways across a reset boundary ("Jul 24, 12am (Europe/Berlin)" vs
        # "Jul 23, 11:59pm (Europe/Berlin)", 60s apart) — a string compare
        # flickers on exactly that boundary. Tolerance is 2 minutes: comfortably
        # wider than the observed 60s skew, still tight enough that two
        # genuinely different resets (which differ by hours at least) never
        # collide.
        model_reset_epoch = _reset_epoch(found[1])
        summary_reset = found[1]
        if week:
            week_epoch = levels.get("week_resets_at")
            if week_epoch is not None and model_reset_epoch is not None:
                if abs(model_reset_epoch - week_epoch) <= _RESET_ELIDE_TOLERANCE_SECONDS:
                    summary_reset = None
            elif found[1] == week[1]:
                summary_reset = None
        bucket = _bucket_summary(f"{label} week", found[0], summary_reset)
        levels.setdefault("week_models", {})[label] = {
            "used_percentage": found[0],
            "reset": found[1],
            "resets_at": model_reset_epoch,
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
    if usage_credits:
        levels["usage_credits"] = usage_credits
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


def _quota_buckets_complete(
    levels: dict[str, Any],
    *,
    wait_for_credits: bool = False,
) -> bool:
    if "session_used_percentage" not in levels or "week_used_percentage" not in levels:
        return False
    if wait_for_credits:
        return "usage_credits" in levels
    return True


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
    wait_for_credits: bool = False,
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
            if _quota_buckets_complete(
                parse_usage_text(b"".join(chunks)),
                wait_for_credits=wait_for_credits,
            ):
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
    wait_for_credits: bool = False,
) -> dict[str, Any]:
    """Return a best-effort Claude usage levels snapshot, never raising."""
    try:
        levels = parse_usage_text(
            capture_usage_raw(
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                model=model,
                env=env,
                wait_for_credits=wait_for_credits,
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
    wait_for_credits: bool = False,
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
        wait_for_credits=wait_for_credits,
    )
    levels = carry_forward_sections(_carry_candidates(outbox_dir), levels)
    write_snapshot(outbox_dir, levels)
    return levels


def _carry_candidates(outbox_dir: Path) -> list[dict[str, Any]]:
    """Snapshots to carry async sections from, newest first.

    Own dir first (a heartbeat refreshing a file it already wrote), then every
    sibling run's snapshot by recency. Two reasons it has to be a *list* and not
    just the latest one:

    - snapshots are written per run, so a new run's outbox starts empty — and
      the run boundary is exactly where the reported loss became visible;
    - a partial scrape that already landed leaves a snapshot with the section
      *missing*, and carrying "from the newest snapshot" would then carry the
      hole itself. The damage would heal only when a complete scrape happened to
      land. Reading per section, newest-first, heals it on the very next tick.
    """
    candidates: list[dict[str, Any]] = []
    own = load_snapshot(outbox_dir)
    if own is not None:
        candidates.append(own)
    try:
        siblings = sorted(
            (
                path
                for path in outbox_dir.parent.glob(f"*/{SNAPSHOT_NAME}")
                if path.parent != outbox_dir
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return candidates
    for path in siblings:
        found = load_snapshot(path.parent)
        if found is not None:
            candidates.append(found)
    return candidates


# Sections of the `/usage` panel that render *asynchronously* and can simply
# fail to appear — Claude Code fetches them separately and prints
# "Per-model breakdown unavailable (rate limited — try again in a moment)" in
# their place. A scrape that lands in that window parses cleanly; it just has
# no per-model rows and no usage-credits block in it.
#
# `quota` is different in kind: it is the *primary* session/week reading, not
# an optional extra, and a scrape normally draws all of it or none of it. It
# is listed here only for that "none of it" case — a PTY scrape that renders
# no session/week rows at all, so `parse_usage_text` returns no `quota` key
# and the scrape proved nothing (see `capture_levels`). A fresh scrape that
# parsed even a partial quota block is left exactly as parsed; the loop below
# only ever carries a section that is entirely *absent* from `fresh`.
# `quota` goes first so its carry (if any) lands in `fresh` before the
# `week_models` branch below reads `fresh["quota"]`.
_ASYNC_SECTIONS = ("quota", "usage_credits", "week_models")

# `quota` and `week_models` are percentages of the account's subscription
# window — a *plan* quantity. `usage_credits` is a separate dollar-denominated
# pool the issue never named as affected, so it is not plan-scoped here (#580).
# A candidate whose `plan_type` is a *different, known* identity from the
# fresh reading's is not a stale copy of the same quantity — it is a reading
# of a different quantity in the same units, and carrying it forward would
# reintroduce exactly the units bug #580 reports. Only two *present* and
# *unequal* identities disqualify a candidate; either side missing a
# `plan_type` is unknown, not different, and must not block a carry — that is
# the #561 partial-scrape case this must keep working exactly as before.
_PLAN_SCOPED_SECTIONS = frozenset({"quota", "week_models"})

# How long a section may be carried across a scrape that didn't render it.
# Long enough to ride out the panel's rate-limit windows, short enough that a
# section which is genuinely *gone* (credits turned off, plan changed) leaves
# the dashboard within a working session rather than haunting it. An explicit
# "usage credits are off" still overwrites immediately — that is the panel
# stating a fact, not failing to render one.
_CARRY_MAX_AGE_SECONDS = 12 * 3600.0


def _plan_compatible(fresh_plan: Any, candidate_plan: Any) -> bool:
    """Whether a candidate's plan identity is safe to carry given the fresh one.

    Absence on either side is unknown, not different (#580's boundary
    condition) — only two *present* strings that disagree make a candidate
    incompatible.
    """
    if not isinstance(fresh_plan, str) or not fresh_plan.strip():
        return True
    if not isinstance(candidate_plan, str) or not candidate_plan.strip():
        return True
    return fresh_plan.strip() == candidate_plan.strip()


def carry_forward_sections(
    previous: dict[str, Any] | list[dict[str, Any]] | None,
    fresh: dict[str, Any],
) -> dict[str, Any]:
    """Keep async `/usage` sections a fresh scrape failed to render.

    The reported regression (2026-07-13, "we have lost the claude credits"):
    the dashboard's Claude credits row vanished. Nothing had changed in the
    parser or the panel — a heartbeat refresh had simply caught `/usage` while
    its per-model/credits region was rate-limited, and the snapshot write
    replaced a *complete* reading with a partial one. Field by field, known
    became unknown, and stayed that way until a lucky scrape restored it.

    A section that fails to render is not a section that is gone. Absence of
    evidence, again — the same shape as Codex's positional window labels, one
    layer down. So a missing async section is taken from the newest snapshot
    that actually *has* it (bounded by :data:`_CARRY_MAX_AGE_SECONDS`), with
    ``carried_from`` stamped on the credits block so a dollar figure can never
    pass itself off as freshly seen. Everything the scrape *did* prove —
    session, week, resets — is taken from the fresh reading, unconditionally.

    ``quota`` rides the same mechanism for the one case where the scrape
    proved nothing at all: no session/week rows rendered, so `fresh` carries
    no `quota` key whatsoever. That is the *only* trigger — a fresh reading
    that parsed even a partial quota block is never touched, so the promise
    above stays literally true for anything the scrape did prove. The carried
    `quota` block is stamped with the same `carried_from` discipline as
    `usage_credits`, so a stale reading can't pass itself off as fresh either.

    *previous* is a list of candidate snapshots, newest first (a lone dict is
    accepted for callers that have only one). Searching per section rather than
    trusting the single newest snapshot is what heals a hole that has already
    been written: the newest snapshot is often the partial one.

    A plan change is a *regime boundary*, not a staleness question (#580): for
    `quota` and `week_models`, a candidate whose known `plan_type` disagrees
    with the fresh reading's known `plan_type` is skipped, even if it is
    otherwise the newest thing that has the section. Skipping it is a refusal,
    not a downgrade — no warning-annotated carry, because there is no way to
    annotate a percentage into meaning the right thing again. Logged once per
    call, at the point a mismatch is *detected*, and the line says which of
    the two outcomes followed: the section was carried from an older
    same-plan snapshot, or nothing was carried at all.
    """
    candidates = [previous] if isinstance(previous, dict) else list(previous or [])
    candidates = [
        snapshot
        for snapshot in candidates
        if isinstance(snapshot, dict) and _within_carry_window(snapshot.get("updated_at"))
    ]
    if not candidates:
        return fresh
    plan_change_logged = False
    for section in _ASYNC_SECTIONS:
        if section in fresh:
            continue
        if section in _PLAN_SCOPED_SECTIONS:
            fresh_plan = fresh.get("plan_type")
            source = None
            blocked_plan: str | None = None
            for snapshot in candidates:
                if section not in snapshot:
                    continue
                candidate_plan = snapshot.get("plan_type")
                if _plan_compatible(fresh_plan, candidate_plan):
                    source = snapshot
                    break
                if blocked_plan is None and isinstance(candidate_plan, str) and candidate_plan.strip():
                    blocked_plan = candidate_plan.strip()
            if blocked_plan is not None and not plan_change_logged:
                # Say which of the two things actually happened. A blocked
                # candidate does not mean a blocked carry — an older snapshot
                # from the *current* plan can still serve, and a line reading
                # "refusing to carry" when the section was carried anyway is
                # the #568 defect: a fixed refusal string describing a
                # different failure than the one that occurred.
                logger.info(
                    "claude_usage: plan changed (%s -> %s); %s for %s",
                    blocked_plan,
                    fresh_plan,
                    "carried from an older same-plan reading instead"
                    if source is not None
                    else "refusing to carry across the regime boundary",
                    section,
                )
                plan_change_logged = True
        else:
            source = next((s for s in candidates if section in s), None)
        if source is None:
            continue
        carried = source[section]
        if section in ("usage_credits", "quota") and isinstance(carried, dict):
            carried = {**carried, "carried_from": source.get("updated_at")}
        if section == "quota" and isinstance(carried, dict):
            carried["summary"] = _mark_carried(
                carried.get("summary"), source.get("updated_at")
            )
        fresh[section] = carried
        if section == "quota":
            # A silent heal is how the underlying loss (partial scrape erases
            # a known quota reading, blinding `_fire_due_schedules`'s pacing
            # decision) stayed invisible for days — nothing in the daemon log
            # distinguished "no pacing needed" from "pacing blind". This is
            # the one line that makes a quota carry visible.
            logger.info(
                "claude_usage: carried quota forward from %s "
                "(fresh /usage scrape parsed no quota buckets)",
                source.get("updated_at"),
            )
        # The per-model weekly buckets ride inside `quota.buckets` for pacing
        # (`runner_quota.binding_quota_remaining_pct`); carrying `week_models`
        # without them would leave the two halves of one reading disagreeing.
        if section == "week_models":
            quota = fresh.get("quota")
            quota_carried_from = (
                quota.get("carried_from") if isinstance(quota, dict) else None
            )
            # `quota` and `week_models` are carried independently — each
            # searches the candidate list for its own newest source, and
            # those sources can differ. If `quota` itself was carried this
            # round, only fold `week_models`' bucket in when it comes from
            # that *same* source; otherwise the merge would stitch one
            # reading's session/week facts to a different reading's per-model
            # bucket, which is exactly the disagreement carrying is meant to
            # prevent. `quota` came from the fresh scrape (not carried) is the
            # original, unconditional case — that quota is trustworthy
            # regardless of where the week_models heal comes from.
            consistent_source = (
                quota_carried_from is None
                or quota_carried_from == source.get("updated_at")
            )
            prior_models = ((source.get("quota") or {}).get("buckets") or {}).get(
                "week_models"
            )
            if (
                isinstance(quota, dict)
                and isinstance(prior_models, dict)
                and consistent_source
            ):
                buckets = quota.setdefault("buckets", {})
                buckets.setdefault("week_models", prior_models)
    return fresh


_CARRIED_MARK_RE = re.compile(r"\s*\[carried from [^\]]*\]\s*$")


def _mark_carried(summary: Any, stamp: Any) -> Any:
    """Stamp a carried ``quota`` summary so the *rendered* line says so.

    ``carried_from`` alone is not enough. It has exactly one reader in the
    tree (``gates.cloud`` republishes the *credits* block's copy for the
    dashboard); nothing reads it for ``quota``. But the ``quota`` summary
    string is what every surface renders — the wake's posture line, the
    Runner line, the dashboard — so a carried reading would otherwise appear
    as a fresh one everywhere a human or a wake actually looks, which is the
    failure carrying exists to prevent, only quieter.

    Marking the summary itself means every existing consumer inherits the
    honesty without a new renderer. Idempotent: a re-carried summary is
    re-stamped with the newer source rather than accumulating marks.
    """
    if not isinstance(summary, str) or not summary.strip():
        return summary
    base = _CARRIED_MARK_RE.sub("", summary)
    label = str(stamp).strip() if stamp else ""
    return f"{base} [carried from {label}]" if label else f"{base} [carried]"


def _within_carry_window(stamp: Any, now: float | None = None) -> bool:
    if not isinstance(stamp, str) or not stamp.strip():
        return False
    try:
        moment = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    age = (time.time() if now is None else now) - moment.timestamp()
    return 0 <= age <= _CARRY_MAX_AGE_SECONDS
