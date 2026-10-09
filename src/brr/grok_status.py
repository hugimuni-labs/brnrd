"""Grok Build result-JSON level collector.

Headless ``grok --output-format json`` prints one object when the turn
finishes: the reply (``text``), ``sessionId``, token totals (``usage``),
per-model rows (``modelUsage``), and ``total_cost_usd`` when the server
stamped a complete cost. This module reads that object. It does not read
a journal, scrape a TUI, or invent a subscription window.

Collected, and only when the envelope actually carries them:

- ``spend`` — ``total_cost_usd``. Omitted when Grok omits the float
  (pool and OAuth traffic often do, and a partial cost omits every float
  so a caller cannot sum ``modelUsage`` into a bill).
- ``tokens`` — the headless ``usage`` object. ``input_tokens`` is uncached
  input. Dropped when ``usage_is_incomplete`` is set, because that flag
  means the totals may under-count.
- ``model_ids`` — the keys of ``modelUsage``, the models that actually ran.

Subscription quota and reset time come from the separate interactive
``/usage`` probe in :mod:`brr.grok_usage`, merged by the daemon. This result
collector supplies neither quota nor context-window headroom: the envelope
has no window size. Unknown capacity is not unlimited.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

SNAPSHOT_NAME = ".grok-result-levels.json"

#: This result collector fills spend; grok_usage separately fills quota.
#: The daemon unions their slot sets. Context-window headroom stays unwired.
COLLECTED_SLOTS: frozenset[str] = frozenset({"spend"})

_TOKEN_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)


def _is_grok(slug: str) -> bool:
    return slug == "grok" or slug.startswith("grok-")


def supported(runner_name: str | None) -> bool:
    """True when *runner_name*'s Shell is Grok Build."""
    if not runner_name:
        return False
    return _is_grok(str(runner_name).strip().lower())


def valid_session_id(value: Any) -> str | None:
    """A Grok session id, or ``None``.

    Resume matches a non-UUID against session titles. Only a UUID is safe
    to hand to ``--resume``: anything else can select a different
    conversation, or several. The original spelling is kept; Grok stores
    the id as the directory name.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        uuid.UUID(text)
    except ValueError:
        return None
    return text


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fmt_usd(value: float) -> str:
    if abs(value) < 0.1 and value:
        return f"${value:.4f}"
    return f"${value:.2f}"


def parse_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize one Grok result object into a levels snapshot.

    The snapshot carries only fields the object proved. No quota key, no
    context-window percentage, no cost summed from per-model rows.
    """
    payload = payload if isinstance(payload, dict) else {}
    levels: dict[str, Any] = {
        "source": "grok result JSON",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    incomplete = payload.get("usage_is_incomplete") is True
    if not incomplete:
        usage = payload.get("usage")
        if isinstance(usage, dict):
            tokens = {
                key: count
                for key in _TOKEN_FIELDS
                if (count := _int(usage.get(key))) is not None
            }
            if tokens:
                levels["tokens"] = tokens
    if payload.get("cost_is_partial") is not True and not incomplete:
        total = _float(payload.get("total_cost_usd"))
        if total is not None:
            spend: dict[str, Any] = {
                "summary": f"{_fmt_usd(total)} this session",
                "total_cost_usd": round(total, 6),
            }
            ticks = _int(payload.get("total_cost_usd_ticks"))
            if ticks is not None:
                spend["total_cost_usd_ticks"] = ticks
            levels["spend"] = spend
    model_usage = payload.get("modelUsage")
    if isinstance(model_usage, dict):
        ids = [str(key).strip() for key in model_usage if str(key).strip()]
        if ids:
            levels["model_ids"] = ids
    return levels


def resolved_model_id(levels: dict[str, Any] | None) -> str | None:
    """The model ids ``modelUsage`` named, joined with ``+`` when several ran."""
    if not isinstance(levels, dict):
        return None
    ids = levels.get("model_ids")
    if not isinstance(ids, list) or not ids:
        return None
    cleaned = [str(item).strip() for item in ids if str(item).strip()]
    return "+".join(cleaned) if cleaned else None


def session_id(stdout: str) -> str | None:
    """``sessionId`` from a Grok result object, when it is a UUID."""
    try:
        payload = json.loads(stdout) if stdout.strip() else None
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") == "error":
        return None
    return valid_session_id(payload.get("sessionId"))


def _outbox_dir(env: dict[str, str]) -> Path | None:
    outbox = env.get("BRR_OUTBOX_DIR")
    if outbox:
        return Path(outbox)
    portal = env.get("BRR_PORTAL_STATE")
    return Path(portal).parent if portal else None


def _shared_dir(env: dict[str, str]) -> Path | None:
    shared = env.get("BRR_SHARED_DIR")
    return Path(shared) if shared else None


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


def mark_cross_run(levels: dict[str, Any] | None) -> dict[str, Any] | None:
    """A previous run's spend, labelled as such, with its tokens removed.

    Token totals belong to the run that produced the envelope. Serving
    them as this run's reading would book someone else's session onto
    the ledger. Spend can still be shown, once the summary stops saying
    it is this session.
    """
    if not isinstance(levels, dict):
        return None
    spend = levels.get("spend")
    if not isinstance(spend, dict):
        return None
    summary = str(spend.get("summary") or "")
    summary = summary.replace("this session", "a previous Grok session")
    return {
        "source": levels.get("source"),
        "updated_at": levels.get("updated_at"),
        "spend": {**spend, "summary": summary},
    }


def _measured(levels: dict[str, Any]) -> bool:
    return any(key in levels for key in ("tokens", "spend", "model_ids"))


def capture_stdout_with_model(
    stdout: str, env: dict[str, str] | None = None,
) -> tuple[str, str | None, bool]:
    """Unwrap a Grok result object to reply text, observed model, and failure.

    The third element is true when the object is Grok's error envelope or
    a success object with no reply. Non-JSON stdout passes through: a
    profile that did not opt into the envelope keeps its text. Telemetry
    writes never change the reply and never raise.
    """
    resolved_env = env if env is not None else os.environ
    try:
        payload = json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError:
        return stdout, None, False
    if not isinstance(payload, dict):
        return stdout, None, False
    if payload.get("type") == "error":
        message = payload.get("message")
        text = message.strip() if isinstance(message, str) and message.strip() else "Grok returned an error"
        return text if text.endswith("\n") else text + "\n", None, True
    reply = payload.get("text")
    if not isinstance(reply, str) or not reply.strip():
        return "(runner produced no reply text)\n", None, True
    if not reply.endswith("\n"):
        reply += "\n"
    try:
        levels = parse_result(payload)
        if _measured(levels):
            run_id = str(resolved_env.get("BRR_RUN_ID") or "").strip()
            if run_id:
                levels["run_id"] = run_id
            write_snapshot(_outbox_dir(resolved_env), levels)
            shared = _shared_dir(resolved_env)
            if shared is not None:
                write_snapshot(shared, levels)
        observed = resolved_model_id(levels)
    except Exception:
        observed = None
    return reply, observed, False
