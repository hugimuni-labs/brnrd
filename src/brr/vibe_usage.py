"""Exact-session usage collection for the Vibe shell (verified CLI 2.25.5).

Vibe's headless ``-p --output json`` prints public history entries only:
session id per entry, never tokens, model or cost. The same invocation
persists everything the output omits — on the Unified Harness under
``$VIBE_HOME/logs/session/unified/<session_id>/`` (per-action ``usage``
records in ``journal/*.jsonl``; the session rollup in
``generations/<n>/projection-state.json``; the observed model in
``generations/<n>/runtime-state.json``), and on the legacy harness in
``logs/session/session_<ts>_<short>/meta.json`` under ``stats`` (tokens,
per-million list prices and a pre-computed cost).

This collector reads that journal back — keyed by the session id extracted
from the CLI's own stdout, never by newest-mtime across sessions — and
persists a normalized sidecar beside the run's other control files, the
same two-write seam ``claude_status`` uses for its result levels (#1027:
the per-run outbox is swept, the shared dir is not).

Honesty rules, matching the daemon's telemetry conventions:

- Cumulative session tokens and the last context window are separate
  fields (``session_tokens`` vs ``context_tokens``); they are never merged
  into one number.
- Cost is only ever a *list-price estimate* — Vibe computes it from the
  configured per-million prices — and is absent (``None``) when the
  journal carries no prices. It is never a subscription bill, and prices
  are never invented for a path that does not persist them.
- Missing or malformed journal files degrade to ``available: false`` with
  a reason: no fake model, no zeroed tokens. A session id that cannot be
  extracted writes no sidecar at all — there is no exact coordinate.

No quota or allowance is claimed here: Vibe exposes no usage-rate surface
the daemon can poll, so this module deliberately stays off the quota
sibling's ground (``runner_quota``) and records observed usage only.

Live collection: Vibe's native hooks pass ``session_id`` on every
``pre_tool``/``post_tool``/``post_agent`` invocation
(``vibe/core/hooks/models.py`` ``HookSessionContext``), so a mid-run
collector is possible later. This slice is terminal-only: the journal is
complete exactly when the headless invocation exits, and the sidecar is
written on that same boundary as the final reply.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Mirrors ``claude_status``'s ``.claude-result-levels.json`` placement.
SIDECAR_NAME = ".vibe-usage.json"


def _vibe_home(env: dict[str, str] | None = None) -> Path:
    resolved = env if env is not None else os.environ
    return Path(resolved.get("VIBE_HOME") or str(Path.home() / ".vibe"))


def extract_session_id(stdout: str) -> str | None:
    """The one session id the CLI's own history declares, or ``None``.

    Every public history entry carries ``sessionId``. A single invocation
    is a single session; distinct ids in one stdout mean the output is not
    what the adapter contract expects, and an ambiguous coordinate is
    worth less than none — never a guess.
    """
    try:
        history = json.loads(stdout) if stdout.strip() else None
    except (ValueError, TypeError):
        return None
    if not isinstance(history, list):
        return None
    ids: set[str] = set()
    for entry in history:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("sessionId")
        if isinstance(sid, str) and sid.strip():
            ids.add(sid.strip())
    if len(ids) == 1:
        return ids.pop()
    return None


def _tokens(mapping: Any, *, input_key: str, cached_key: str, output_key: str,
            total_key: str) -> dict[str, int] | None:
    """Normalize a usage mapping; incomplete or non-integer data is absent."""
    if not isinstance(mapping, dict):
        return None
    out: dict[str, int] = {}
    for field, key in (
        ("input", input_key), ("cached", cached_key),
        ("output", output_key), ("total", total_key),
    ):
        value = mapping.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        out[field] = value
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _latest_generation(session_dir: Path) -> Path | None:
    """The highest-numbered generation dir — sequence order, never mtime.

    Within one session the numbered generation dirs are the journal's own
    ordering; picking by mtime would tie the read to the filesystem clock
    for no gain.
    """
    generations = session_dir / "generations"
    if not generations.is_dir():
        return None
    best: tuple[int, Path] | None = None
    for child in generations.iterdir():
        if not child.is_dir() or not child.name.isdigit():
            continue
        key = int(child.name)
        if best is None or key > best[0]:
            best = (key, child)
    return best[1] if best is not None else None


def _collect_unified(session_id: str, home: Path) -> dict[str, Any] | None:
    session_dir = home / "logs" / "session" / "unified" / session_id
    if not session_dir.is_dir():
        return None
    generation = _latest_generation(session_dir)
    if generation is None:
        return None
    projection = _read_json(generation / "projection-state.json")
    snapshot = (
        projection.get("snapshot") if isinstance(projection, dict) else None
    )
    session = snapshot.get("session") if isinstance(snapshot, dict) else None
    session_tokens = (
        _tokens(session.get("tokenUsage"), input_key="inputTokens",
                cached_key="cachedInputTokens", output_key="outputTokens",
                total_key="totalTokens")
        if isinstance(session, dict) else None
    )
    context_tokens = (
        _tokens(session.get("contextUsage"), input_key="inputTokens",
                cached_key="cachedInputTokens", output_key="outputTokens",
                total_key="totalTokens")
        if isinstance(session, dict) else None
    )
    runtime = _read_json(generation / "runtime-state.json")
    metadata = runtime.get("session_metadata") if isinstance(runtime, dict) else None
    model = (
        metadata.get("active_model")
        if isinstance(metadata, dict)
        and isinstance(metadata.get("active_model"), str)
        and metadata.get("active_model").strip()
        else None
    )
    if session_tokens is None and model is None:
        return None
    # The Unified Harness persists no prices: cost stays honestly absent.
    return {
        "source": "unified",
        "model": model,
        "session_tokens": session_tokens,
        "context_tokens": context_tokens,
        "pricing": None,
        "list_price_cost_usd": None,
    }


def _collect_legacy(session_id: str, home: Path) -> dict[str, Any] | None:
    """Match a legacy ``session_*`` dir by its meta's own session id.

    The directory name only carries the id's first segment, so the match is
    made on ``meta.json``'s ``session_id`` field — an exact read, never a
    name-prefix guess and never the newest directory.
    """
    root = home / "logs" / "session"
    if not root.is_dir():
        return None
    for meta_path in sorted(root.glob("session_*/meta.json")):
        meta = _read_json(meta_path)
        if not isinstance(meta, dict) or meta.get("session_id") != session_id:
            continue
        stats = meta.get("stats")
        if not isinstance(stats, dict):
            return None
        pricing = None
        prices = (
            stats.get("input_price_per_million"),
            stats.get("output_price_per_million"),
        )
        if all(isinstance(p, (int, float)) and not isinstance(p, bool) for p in prices):
            cached_price = stats.get("cached_input_price_per_million")
            pricing = {
                "input_per_million": float(prices[0]),
                "output_per_million": float(prices[1]),
                "cached_input_per_million": (
                    float(cached_price)
                    if isinstance(cached_price, (int, float))
                    and not isinstance(cached_price, bool)
                    else None
                ),
            }
        cost = stats.get("session_cost")
        config = meta.get("config")
        model = (
            config.get("active_model")
            if isinstance(config, dict)
            and isinstance(config.get("active_model"), str)
            and config.get("active_model").strip()
            else None
        )
        session_tokens = None
        values = (
            stats.get("session_prompt_tokens"),
            stats.get("session_cached_tokens"),
            stats.get("session_completion_tokens"),
        )
        if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
               for v in values):
            session_tokens = {
                "input": values[0], "cached": values[1], "output": values[2],
                "total": values[0] + values[2],
            }
        context = stats.get("context_tokens")
        context_tokens = (
            {"total": context}
            if isinstance(context, int) and not isinstance(context, bool)
            else None
        )
        if session_tokens is None and model is None:
            return None
        return {
            "source": "legacy",
            "model": model,
            "session_tokens": session_tokens,
            "context_tokens": context_tokens,
            "pricing": pricing,
            # Vibe's own list-price arithmetic, never a subscription bill.
            "list_price_cost_usd": (
                float(cost)
                if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                else None
            ),
        }
    return None


def collect(session_id: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Normalized usage for exactly this session, or an honest unavailable.

    A payload with ``available: false`` is a *result*, not an error: the
    caller persists it as the invocation's coordinate with the reason the
    journal could not answer.
    """
    home = _vibe_home(env)
    found = _collect_unified(session_id, home)
    if found is None:
        found = _collect_legacy(session_id, home)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "available": found is not None,
        "collected_at": datetime.now(UTC).isoformat(),
        "reason": None if found is not None else "session journal not found",
    }
    if found is not None:
        payload.update(found)
    else:
        payload.update({
            "source": None, "model": None, "session_tokens": None,
            "context_tokens": None, "pricing": None,
            "list_price_cost_usd": None,
        })
    return payload


def _sidecar_dirs(env: dict[str, str] | None = None) -> list[Path]:
    """The outbox copy first, then the durable shared copy when exposed.

    Same resolution order as ``claude_status``: ``BRR_OUTBOX_DIR``, falling
    back to the ``BRR_PORTAL_STATE`` parent; ``BRR_SHARED_DIR`` for the
    copy that outlives this run's own outbox.
    """
    resolved = env if env is not None else os.environ
    dirs: list[Path] = []
    outbox = resolved.get("BRR_OUTBOX_DIR")
    if not outbox:
        portal = resolved.get("BRR_PORTAL_STATE")
        outbox = str(Path(portal).parent) if portal else None
    if outbox:
        dirs.append(Path(outbox))
    shared = resolved.get("BRR_SHARED_DIR")
    if shared:
        dirs.append(Path(shared))
    return dirs


def write_sidecar(dir_path: Path, payload: dict[str, Any]) -> Path | None:
    """Atomic overwrite; telemetry must never break the adapter's reply."""
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        staged = dir_path / (SIDECAR_NAME + ".tmp")
        staged.write_text(
            json.dumps(payload, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        target = dir_path / SIDECAR_NAME
        staged.replace(target)
        return target
    except OSError:
        return None


def load_sidecar(dir_path: Path) -> dict[str, Any] | None:
    payload = _read_json(dir_path / SIDECAR_NAME)
    return payload if isinstance(payload, dict) else None


def capture_stdout(stdout: str, env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Collect this invocation's usage from its own declared session id.

    Called on the same boundary that unwraps the final reply, after the
    headless invocation has exited and its journal is complete. Returns
    the persisted payload, or ``None`` when no exact session coordinate
    could be extracted (nothing is written then — there is nothing exact
    to persist). A ``BRR_RUN_ID`` stamps provenance on every copy.
    """
    session_id = extract_session_id(stdout)
    if session_id is None:
        return None
    payload = collect(session_id, env)
    resolved = env if env is not None else os.environ
    run_id = str(resolved.get("BRR_RUN_ID") or "").strip()
    if run_id:
        payload["run_id"] = run_id
    for dir_path in _sidecar_dirs(resolved):
        write_sidecar(dir_path, payload)
    return payload
