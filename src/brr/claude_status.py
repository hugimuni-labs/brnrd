"""Claude Code result-JSON level collector for the Claude Shell.

Claude Code's interactive ``/usage`` panel shows subscription windows, but that
is a TUI scrape handled separately by :mod:`brr.claude_usage`. Under the
head-less ``claude --print`` mode brr uses, ``statusLine`` also does not fire.
The result seam that *does* exist head-less is ``--output-format json``: the
final result object carries the reply text plus session accounting such as
``total_cost_usd`` and ``modelUsage[model].contextWindow``.

This module normalizes that result JSON into the same level snapshot shape the
portal facets already consume. It deliberately collects only the slots proved by
the head-less result JSON:

- ``spend`` — ``total_cost_usd`` (or the sum of per-model ``costUSD``).
- ``context_window`` — estimated headroom from per-model token counts and
  ``contextWindow``.

Subscription quota / reset windows remain unavailable from Claude result JSON;
the daemon merges this snapshot with the cached interactive ``/usage`` snapshot
when both exist.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

SNAPSHOT_NAME = ".claude-result-levels.json"

_CLAUDE_FLAVOURS = {"claude"}

# The head-less result JSON does not carry subscription quota windows.
COLLECTED_SLOTS: frozenset[str] = frozenset({"spend", "context_window"})


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


def _fmt_usd(value: float) -> str:
    if abs(value) < 0.1 and value:
        return f"${value:.4f}"
    return f"${value:.2f}"


def _camel_or_snake(data: dict[str, Any], camel: str, snake: str) -> Any:
    return data.get(camel) if camel in data else data.get(snake)


def _model_usage_cost(model_usage: Any) -> float | None:
    if not isinstance(model_usage, dict):
        return None
    total = 0.0
    found = False
    for usage in model_usage.values():
        if not isinstance(usage, dict):
            continue
        cost = _num(_camel_or_snake(usage, "costUSD", "cost_usd"))
        if cost is None:
            continue
        total += cost
        found = True
    return total if found else None


def _model_context_remaining(model_usage: Any) -> float | None:
    """Return the most conservative per-model context headroom estimate."""
    if not isinstance(model_usage, dict):
        return None
    lowest_remaining: float | None = None
    for usage in model_usage.values():
        if not isinstance(usage, dict):
            continue
        window = _num(_camel_or_snake(usage, "contextWindow", "context_window"))
        if not window or window <= 0:
            continue
        used_parts = [
            _camel_or_snake(usage, "inputTokens", "input_tokens"),
            _camel_or_snake(usage, "cacheReadInputTokens", "cache_read_input_tokens"),
            _camel_or_snake(
                usage, "cacheCreationInputTokens", "cache_creation_input_tokens"
            ),
        ]
        used = sum(v for v in (_num(part) for part in used_parts) if v is not None)
        remaining = max(0.0, min(100.0, 100.0 * (1.0 - used / window)))
        if lowest_remaining is None or remaining < lowest_remaining:
            lowest_remaining = remaining
    return lowest_remaining


def _model_usage_ids(model_usage: Any) -> list[str] | None:
    """Return the real model id(s) keying this run's ``modelUsage``.

    ``modelUsage`` is keyed by the actual resolved model id (e.g.
    ``claude-opus-4-...``), not an opaque blob — the one place "which model
    ran this" survives past the run. ``_model_usage_tokens`` already walks
    ``.values()`` for token totals; this walks ``.keys()`` for the id(s),
    sorted for a stable, deterministic row (#255).
    """
    if not isinstance(model_usage, dict):
        return None
    ids = sorted(
        key
        for key, usage in model_usage.items()
        if isinstance(key, str) and key.strip() and isinstance(usage, dict)
    )
    return ids or None


def _model_usage_tokens(model_usage: Any) -> dict[str, Any] | None:
    if not isinstance(model_usage, dict):
        return None
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    found = False
    highest_used: float | None = None
    for usage in model_usage.values():
        if not isinstance(usage, dict):
            continue
        fields = {
            "input_tokens": _camel_or_snake(usage, "inputTokens", "input_tokens"),
            "output_tokens": _camel_or_snake(usage, "outputTokens", "output_tokens"),
            "cache_read_input_tokens": _camel_or_snake(
                usage, "cacheReadInputTokens", "cache_read_input_tokens"
            ),
            "cache_creation_input_tokens": _camel_or_snake(
                usage, "cacheCreationInputTokens", "cache_creation_input_tokens"
            ),
        }
        for key, raw in fields.items():
            value = _num(raw)
            if value is None:
                continue
            totals[key] += int(value)
            found = True
        window = _num(_camel_or_snake(usage, "contextWindow", "context_window"))
        if window and window > 0:
            used_parts = [
                fields["input_tokens"],
                fields["cache_read_input_tokens"],
                fields["cache_creation_input_tokens"],
            ]
            used = sum(v for v in (_num(part) for part in used_parts) if v is not None)
            used_pct = max(0.0, min(100.0, 100.0 * used / window))
            if highest_used is None or used_pct > highest_used:
                highest_used = used_pct
    if not found:
        return None
    if highest_used is not None:
        totals["context_window_used_percent"] = round(highest_used, 6)
    return totals


# --- Substitution-reason capture (2026-07-16) --------------------------------
# When a pinned run comes back served by a different Core, *which* model ran is
# already attested (``modelUsage`` keys -> ``core_mismatch``). *Why* it switched
# used to be mined from the ``--output-format json`` envelope on the theory that
# a server-side fallback documents ``stop_reason: "refusal"``, a ``fallback``
# content block, and ``usage.iterations`` ``fallback_message`` entries.
#
# Measured 2026-07-16 against a genuinely refused run: **the envelope carries
# none of them.** A fable-pinned run that was refused and served Opus answers
# with ``terminal_reason: "completed"``, ``subtype: "success"``,
# ``is_error: false``, ``stop_reason: "end_turn"``, no ``content`` key, no
# ``stop_details`` key, and ``usage.iterations: []``. Every field the envelope
# path inspected is absent, so ``substitution_reason`` returned ``None`` on
# 100% of real substitutions. The envelope declares success; only
# ``modelUsage`` betrays the swap, which is what ``core_mismatch`` already
# reads.
#
# The reason exists in exactly one place: Claude Code's per-message session
# transcript, which records a ``system`` row with ``subtype:
# "model_refusal_fallback"`` carrying ``apiRefusalCategory``, ``originalModel``,
# ``fallbackModel`` and the user-facing blurb. The envelope's ``session_id``
# keys it, so this is a deterministic lookup rather than a newest-mtime guess:
# the id is a UUID, so we glob for it instead of reverse-engineering Claude
# Code's cwd-to-directory slug (``/`` and ``.`` both fold to ``-``), which would
# break the moment that encoding changes.
#
# The envelope keys are still recorded as schema forensics: if a future CLI
# version *does* start carrying a reason, the key names are how we notice.
_REASON_ENVELOPE_KEYS = ("stop_reason", "stop_details", "subtype", "is_error")
_REFUSAL_SUBTYPE = "model_refusal_fallback"
_CLAUDE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def session_transcript_path(
    session_id: str | None,
    projects_root: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Locate a Claude Code session transcript by its ``session_id``.

    ``session_id`` is a UUID, unique across every project directory, so a glob
    finds it without depending on how Claude Code encodes a cwd into a
    directory name. Returns ``None`` when the id is empty, the projects root
    does not exist, or no transcript matches.
    """
    if not session_id or not str(session_id).strip():
        return None
    root = Path(projects_root) if projects_root else _CLAUDE_PROJECTS_ROOT
    try:
        if not root.is_dir():
            return None
        matches = sorted(root.glob(f"*/{str(session_id).strip()}.jsonl"))
    except OSError:
        return None
    return matches[0] if matches else None


def session_refusal(
    session_id: str | None,
    projects_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Read the refusal/fallback reason out of a run's session transcript.

    Returns ``None`` when no transcript is found or it records no refusal —
    i.e. for every clean run. When a refusal *did* fire, returns the structured
    reason the envelope never carries. Reads the last refusal when a session
    was refused more than once; ``count`` preserves the fact that it repeated.
    """
    path = session_transcript_path(session_id, projects_root)
    if path is None:
        return None
    found: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or _REFUSAL_SUBTYPE not in line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if (
                    isinstance(row, dict)
                    and row.get("type") == "system"
                    and row.get("subtype") == _REFUSAL_SUBTYPE
                ):
                    found.append(row)
    except OSError:
        return None
    if not found:
        return None
    last = found[-1]
    reason: dict[str, Any] = {"count": len(found)}
    for src, dst in (
        ("apiRefusalCategory", "category"),
        ("trigger", "trigger"),
        ("direction", "direction"),
        ("originalModel", "from"),
        ("fallbackModel", "to"),
        ("content", "message"),
    ):
        value = last.get(src)
        if value is not None and str(value).strip():
            reason[dst] = value if src != "content" else str(value).strip()
    return reason
# Terminal reasons that describe a *normal* completion, not a substitution.
# A successful server-side fallback answers with ``end_turn``; only a
# non-benign ``stop_reason`` (e.g. ``refusal``) is worth surfacing as a reason.
_BENIGN_STOP_REASONS = frozenset(
    {"end_turn", "max_tokens", "stop_sequence", "tool_use"}
)


def fallback_signals(payload: Any) -> dict[str, Any] | None:
    """Capture substitution/fallback signals from a Claude result envelope.

    Returns ``None`` only when *payload* is not a dict; otherwise always
    returns at least ``envelope_keys`` so a substituted run shows exactly what
    the CLI does and does not carry. Known-suspect scalar fields, any
    ``fallback`` content blocks, and per-attempt ``usage.iterations`` models are
    added when present.
    """
    if not isinstance(payload, dict):
        return None
    signals: dict[str, Any] = {
        "envelope_keys": sorted(k for k in payload if isinstance(k, str)),
    }
    for key in _REASON_ENVELOPE_KEYS:
        value = payload.get(key)
        if value is not None:
            signals[key] = value
    content = payload.get("content")
    if isinstance(content, list):
        blocks = [
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "fallback"
        ]
        if blocks:
            signals["fallback_blocks"] = blocks
    usage = payload.get("usage")
    if isinstance(usage, dict):
        iterations = usage.get("iterations")
        if isinstance(iterations, list) and iterations:
            signals["iterations"] = [
                {
                    "type": item.get("type") if isinstance(item, dict) else None,
                    "model": item.get("model") if isinstance(item, dict) else None,
                }
                for item in iterations
            ]
    return signals


def substitution_reason(levels: dict[str, Any] | None) -> str | None:
    """Compact, ledger-ready summary of *why* a Core substitution happened.

    Reads the ``fallback_signals`` a levels snapshot captured and renders a
    short string, or ``None`` when no substitution signal is present. This is
    the reason that rides next to ``core_mismatch`` in the run ledger. The bare
    ``envelope_keys`` forensics alone are not a reason and never render here.
    """
    if not isinstance(levels, dict):
        return None
    signals = levels.get("fallback_signals")
    if not isinstance(signals, dict):
        return None
    parts: list[str] = []
    # Session-transcript refusal first: it is the only source measured to
    # actually carry a reason, so it leads the rendered string.
    refusal = signals.get("refusal")
    if isinstance(refusal, dict):
        category = refusal.get("category")
        parts.append(
            f"refusal={category}" if category else "refusal"
        )
        served_from, served_to = refusal.get("from"), refusal.get("to")
        if served_from and served_to:
            parts.append(f"fallback={served_from}->{served_to}")
        count = refusal.get("count")
        if isinstance(count, int) and count > 1:
            parts.append(f"refusals={count}")
    stop_reason = signals.get("stop_reason")
    if stop_reason and str(stop_reason) not in _BENIGN_STOP_REASONS:
        parts.append(f"stop_reason={stop_reason}")
    details = signals.get("stop_details")
    if isinstance(details, dict) and details.get("category"):
        parts.append(f"category={details['category']}")
    blocks = signals.get("fallback_blocks")
    if isinstance(blocks, list) and blocks:
        last = blocks[-1]
        target = last.get("to") if isinstance(last, dict) else None
        served = target.get("model") if isinstance(target, dict) else None
        parts.append(f"fallback->{served}" if served else "fallback_block")
    iterations = signals.get("iterations")
    if isinstance(iterations, list):
        served = [
            str(item.get("model"))
            for item in iterations
            if isinstance(item, dict) and item.get("type") == "fallback_message"
        ]
        if served:
            parts.append("fallback_message:" + ",".join(served))
    return ";".join(parts) if parts else None


def parse_result(
    payload: dict[str, Any],
    projects_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Normalize Claude ``--output-format json`` into a levels snapshot.

    *projects_root* overrides where session transcripts are looked up; it
    exists for tests and defaults to Claude Code's real projects directory.
    """
    payload = payload if isinstance(payload, dict) else {}
    levels: dict[str, Any] = {
        "source": "claude result JSON",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    total = _num(payload.get("total_cost_usd"))
    if total is None:
        total = _model_usage_cost(payload.get("modelUsage"))
    if total is not None:
        levels["spend"] = {
            "summary": f"{_fmt_usd(total)} this session (estimated)",
            "total_cost_usd": round(total, 6),
        }

    remaining = _model_context_remaining(payload.get("modelUsage"))
    if remaining is not None:
        levels["context_window"] = {
            "summary": f"{_fmt_pct(remaining)}% context left (est)",
            "remaining_percentage": remaining,
        }
    tokens = _model_usage_tokens(payload.get("modelUsage"))
    if tokens:
        levels["tokens"] = tokens

    model_ids = _model_usage_ids(payload.get("modelUsage"))
    if model_ids:
        levels["model_ids"] = model_ids

    signals = fallback_signals(payload)
    # The reason lives in the session transcript, not the envelope (see the
    # Substitution-reason capture note above): key it off ``session_id`` and
    # merge it in, so ``substitution_reason`` has something real to render.
    refusal = session_refusal(payload.get("session_id"), projects_root)
    if refusal:
        signals = signals or {}
        signals["refusal"] = refusal
    if signals:
        levels["fallback_signals"] = signals

    return levels


def resolved_model_id(levels: dict[str, Any] | None) -> str | None:
    """Join the real model id(s) a levels snapshot's ``modelUsage`` observed.

    Callers (``run_ledger.py``, the daemon's post-run manifest update) use
    this to prefer the actually-resolved model over the static runner
    catalog's ``"default"`` placeholder (#255) — but only once a run has
    finished and produced result JSON; there is no way to know the real
    model before the run starts, so the catalog placeholder is still the
    correct value at dispatch time. Multiple ids happen when a run's own
    subagents resolved to a different tier than the top-level turn; joined
    with ``"+"`` rather than silently picking one and hiding the rest.
    """
    if not isinstance(levels, dict):
        return None
    ids = levels.get("model_ids")
    if not isinstance(ids, list) or not ids:
        return None
    cleaned = [str(item).strip() for item in ids if str(item).strip()]
    return "+".join(cleaned) if cleaned else None


def result_text(payload: dict[str, Any], fallback: str) -> str:
    """Return the user-facing reply carried by Claude result JSON.

    ``fallback`` exists for the genuinely-not-JSON caller path; when we're
    here at all, *payload* already parsed as the CLI's structured result
    envelope, so ``fallback`` is normally the same raw JSON text that
    envelope came from. Falling back to it when ``result``/``errors`` are
    both empty would hand the caller the JSON blob back as if it were the
    reply — observed for real on a run whose stream aborted mid-turn
    (``terminal_reason: aborted_streaming``, ``result: ""``, no ``errors``),
    which leaked the raw envelope into the conversation history as that
    run's "response". Surface a diagnostic instead of the envelope.
    """
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result.rstrip() + "\n"
    errors = payload.get("errors")
    if isinstance(errors, list):
        parts = [str(item).strip() for item in errors if str(item).strip()]
        if parts:
            return "\n".join(parts) + "\n"
    if isinstance(result, str):
        # Valid JSON envelope, but neither a usable result nor errors — the
        # raw envelope is never an appropriate reply.
        reason = payload.get("terminal_reason") or payload.get("stop_reason")
        detail = f": {reason}" if reason else ""
        return f"(runner produced no reply text{detail})\n"
    return fallback


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
    if outbox_dir is None:
        return None
    path = Path(outbox_dir) / SNAPSHOT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def capture_stdout(stdout: str, env: dict[str, str] | None = None) -> str:
    """Parse Claude result JSON if present, write levels, and return reply text.

    Non-JSON stdout is passed through unchanged so custom Claude commands that do
    not opt into ``--output-format json`` keep working.
    """
    reply, _ = capture_stdout_with_model(stdout, env)
    return reply


def capture_stdout_with_model(
    stdout: str, env: dict[str, str] | None = None,
) -> tuple[str, str | None]:
    """Capture Claude's result envelope and return reply + observed Core.

    The model id is returned on the same boundary that unwraps stdout, so the
    caller can enforce a pin before accepting or writing the reply. The levels
    snapshot remains the durable telemetry projection of the same envelope.
    """
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        return stdout, None
    if not isinstance(payload, dict):
        return stdout, None
    levels = parse_result(payload)
    write_snapshot(_outbox_dir(env or os.environ), levels)
    return result_text(payload, stdout), resolved_model_id(levels)
