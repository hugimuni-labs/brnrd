"""Claude Code statusLine collector — the level-facet source for the Claude vessel.

Claude Code's ``statusLine`` feature invokes a configured command every time it
refreshes the footer, handing it **session JSON on stdin**. brr registers
``brr statusline`` as that command (in the same ``.claude/settings.local.json``
it writes the hooks into), so the same boundary-abstraction-over-vessels that
carries hooks also carries live cost/quota — no streaming, no API key, no brnrd
ownership required for a subscription run (``kb/design-resident-boundary.md``
§8). The command does two things on each fire:

1. **Collect** — parse the session JSON into a normalized *levels* snapshot
   (quota / spend / context_window summaries) and write it to
   ``<outbox>/.statusline.json``, a dotfile the daemon's drain skips. The
   daemon folds it into the resources facet on the next heartbeat.
2. **Render** — print a short footer string back to stdout so Claude's UI shows
   the same boundary levels to the human watching.

**Schema caveat (smoke-verify before trusting).** The exact field nesting of
Claude's statusLine JSON is the maintainer's reported finding, not yet
fire-verified here (pitfall: *fire it before you rule on it*). So the parse is
deliberately **defensive**: every field is optional, alternative names are
tolerated, and a shape that does not match degrades to an empty snapshot
(facets read ``absent``) rather than crashing or fabricating a number. When the
real schema is confirmed, tighten :func:`parse_session` and drop the fallbacks.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Normalized level snapshot the daemon reads (a dotfile; the drain skips it).
SNAPSHOT_NAME = ".statusline.json"

# Media that expose a statusLine-style level collector. Per-vessel asymmetry
# (§8): Claude hands spend/quota/context over the footer JSON; Codex does not.
_STATUSLINE_FLAVOURS = {"claude"}


def supported(runner_name: str | None) -> bool:
    """True when *runner_name*'s vessel exposes a statusLine level collector."""
    if not runner_name:
        return False
    slug = str(runner_name).strip().lower()
    return any(slug == f or slug.startswith(f) for f in _STATUSLINE_FLAVOURS)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _fmt_reset(value: Any) -> str | None:
    """Format a reset marker (unix epoch or ISO string) as ``resets HH:MMZ``."""
    if value is None:
        return None
    dt: datetime | None = None
    epoch = _num(value)
    if epoch is not None and epoch > 1_000_000_000:
        try:
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            dt = None
    if dt is None and isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return "resets " + dt.astimezone(timezone.utc).strftime("%H:%MZ")


def _bucket_summary(label: str, bucket: Any) -> str | None:
    """One quota bucket → 'label NN% left (resets HH:MMZ)'.

    ``used_percentage`` is consumption, so headroom = ``100 - used``. A bucket
    may instead carry ``remaining_percentage`` directly; tolerate both.
    """
    if not isinstance(bucket, dict):
        return None
    remaining = _num(bucket.get("remaining_percentage"))
    if remaining is None:
        used = _num(bucket.get("used_percentage"))
        if used is not None:
            remaining = max(0.0, 100.0 - used)
    if remaining is None:
        return None
    text = f"{label} {_fmt_pct(remaining)}% left"
    reset = _fmt_reset(bucket.get("resets_at") or bucket.get("reset_at"))
    return f"{text} ({reset})" if reset else text


def parse_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Claude session JSON into the *levels* snapshot shape.

    Returns ``{"quota"|"spend"|"context_window": {"summary": ...}, "source",
    "updated_at"}`` with only the slots it could prove. Every field is optional;
    an unrecognized shape yields a snapshot with no level slots (facets stay
    ``absent``), never an exception.
    """
    payload = payload if isinstance(payload, dict) else {}
    levels: dict[str, Any] = {
        "source": "claude statusLine",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    rate = payload.get("rate_limits")
    if isinstance(rate, dict):
        parts = [
            s for s in (
                _bucket_summary("5h", rate.get("five_hour")),
                _bucket_summary("7d", rate.get("seven_day")),
            ) if s
        ]
        if parts:
            five = rate.get("five_hour") if isinstance(rate.get("five_hour"), dict) else {}
            levels["quota"] = {
                "summary": "; ".join(parts),
                "five_hour_used_percentage": _num(five.get("used_percentage")),
            }

    cost = payload.get("cost")
    total = None
    if isinstance(cost, dict):
        total = _num(cost.get("total_cost_usd"))
    elif cost is not None:
        total = _num(cost)
    if total is not None:
        levels["spend"] = {
            "summary": f"${total:.2f} this session (estimated)",
            "total_cost_usd": round(total, 4),
        }

    ctx = payload.get("context_window")
    remaining = None
    if isinstance(ctx, dict):
        remaining = _num(ctx.get("remaining_percentage"))
    elif ctx is not None:
        remaining = _num(ctx)
    if remaining is not None:
        levels["context_window"] = {
            "summary": f"{_fmt_pct(remaining)}% context left",
            "remaining_percentage": remaining,
        }

    return levels


def render_footer(levels: dict[str, Any]) -> str:
    """A short one-line footer for Claude's UI, from the parsed levels."""
    chips: list[str] = ["brr"]
    quota = levels.get("quota") if isinstance(levels.get("quota"), dict) else {}
    if quota.get("summary"):
        chips.append(str(quota["summary"]).split(";")[0].strip())
    ctx = (
        levels.get("context_window")
        if isinstance(levels.get("context_window"), dict) else {}
    )
    if ctx.get("summary"):
        chips.append(f"ctx {str(ctx['summary']).split('%')[0].strip()}%")
    spend = levels.get("spend") if isinstance(levels.get("spend"), dict) else {}
    if spend.get("total_cost_usd") is not None:
        chips.append(f"${spend['total_cost_usd']:.2f}")
    return " · ".join(chips)


def _outbox_dir(env: dict[str, str]) -> Path | None:
    outbox = env.get("BRR_OUTBOX_DIR")
    if outbox:
        return Path(outbox)
    portal = env.get("BRR_PORTAL_STATE")
    return Path(portal).parent if portal else None


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
    """Read the level snapshot the collector last wrote, or None."""
    if outbox_dir is None:
        return None
    path = Path(outbox_dir) / SNAPSHOT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def run(stdin_text: str, env: dict[str, str]) -> tuple[str, int]:
    """Collect from one statusLine fire and return ``(footer, exit_code)``.

    Always exits 0 and always prints *some* footer: a statusLine command that
    errors out would clutter Claude's UI, and the collector is best-effort —
    a fire that can't be parsed simply leaves the last snapshot in place.
    """
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    levels = parse_session(payload if isinstance(payload, dict) else {})
    write_snapshot(_outbox_dir(env), levels)
    return render_footer(levels), 0


def main() -> int:
    import sys

    stdin_text = ""
    try:
        stdin_text = sys.stdin.read()
    except (OSError, ValueError):
        stdin_text = ""
    footer, code = run(stdin_text, dict(os.environ))
    sys.stdout.write(footer)
    return code
