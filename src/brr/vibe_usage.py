"""Exact-session usage collection for the Vibe shell (verified CLI 2.25.5).

Vibe's headless ``-p --output json`` prints public history entries only:
session id per entry, never tokens, model or cost. The same invocation
persists everything the output omits — on the Unified Harness under
``$VIBE_HOME/logs/session/unified/<session_id>/`` (per-action ``usage``
records in ``journal/*.jsonl``; the cumulative session rollup and the last
context window in ``generations/<n>/projection-state.json``; the observed
model in ``generations/<n>/runtime-state.json``), and on the legacy
harness in ``logs/session/session_<ts>_<short>/meta.json`` under ``stats``
(tokens, per-million list prices and a pre-computed cost).

This collector reads that journal back — keyed by the session id extracted
from the CLI's own stdout, never by newest-mtime across sessions — and
persists a normalized sidecar beside the run's other control files, the
same seam ``claude_status`` uses for its result levels.

Honesty rules, matching the daemon's telemetry conventions:

- Cumulative session tokens and the last context window are separate
  fields (``session_tokens`` vs ``context_tokens``); they are never merged
  into one number. The rollup was verified cumulative against real
  multi-action sessions: the projection's ``tokenUsage`` equals the sum
  of the journal's per-action usage (exactly in three of four sampled
  sessions; one shows a small shortfall where auxiliary model calls do
  not accrue to the session rollup).
- Cost is only ever a *list-price estimate* — Vibe computes it from the
  configured per-million prices — and is absent (``None``) when the
  journal carries no prices. It is never a subscription bill, and prices
  are never invented for a path that does not persist them.
- The observed model is only reported where the journal proves it (the
  unified runtime state's ``active_model``). The legacy ``meta.json``
  records a *configured* model, which is not proof of the model that
  actually served, so the legacy path reports ``model: None``.
- Missing or malformed journal files, non-finite or negative numbers, and
  inconsistent token shapes (``cached`` over ``input``, ``total`` off the
  input+output sum) all degrade to absent values with a reason: no fake
  model, no zeroed tokens, and never an exception — telemetry failure
  must not change a good reply's exit code (``capture_stdout`` returns
  ``None`` rather than raising).
- A session id that cannot be extracted, or that is not a plain
  identifier, writes no sidecar at all: there is no exact coordinate, and
  a coordinate that could carry a path separator is rejected before the
  filesystem ever sees it.

No quota or allowance is claimed here: Vibe exposes no usage-rate surface
the daemon can poll, so this module deliberately stays off the quota
sibling's ground (``runner_quota``) and records observed usage only.

Sidecar layout: the per-run outbox keeps one fixed name
(``.vibe-usage.json``) — a run's own readers and the closeout copy expect
it there, and the outbox is private to the run. The shared dir never gets
that fixed name: concurrent children would overwrite one writable cell
with two facts. There the sidecar is run-scoped
(``.vibe-usage-<run_id>.json``) and readers must pass the matching
``run_id``. ``clear_sidecars`` removes both before an invocation, so a
retry that produces no session id can never serve the previous attempt's
tokens.

Live collection: Vibe's native hooks pass ``session_id`` on every
``pre_tool``/``post_tool``/``post_agent`` invocation
(``vibe/core/hooks/models.py`` ``HookSessionContext``), so a mid-run
collector is possible later. This slice is terminal-only: the journal is
complete exactly when the headless invocation exits, and the sidecar is
written on that same boundary as the final reply.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: The per-run outbox copy; mirrors ``claude_status``'s placement.
SIDECAR_NAME = ".vibe-usage.json"

#: A session id is a plain identifier — anything that could traverse the
#: filesystem (``/``, ``..``) is not a coordinate, it is an attack surface.
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID.fullmatch(session_id))


def _vibe_home(env: dict[str, str] | None = None) -> Path:
    resolved = env if env is not None else os.environ
    return Path(resolved.get("VIBE_HOME") or str(Path.home() / ".vibe"))


def extract_session_id(stdout: str) -> str | None:
    """The one session id the CLI's own history declares, or ``None``.

    Every public history entry carries ``sessionId``. A single invocation
    is a single session; distinct ids in one stdout mean the output is not
    what the adapter contract expects, and an ambiguous coordinate is
    worth less than none — never a guess. An id that is not a plain
    identifier is discarded the same way: it is not a coordinate.
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
        if isinstance(sid, str) and valid_session_id(sid.strip()):
            ids.add(sid.strip())
    if len(ids) == 1:
        return ids.pop()
    return None


def _tokens(mapping: Any, *, input_key: str, cached_key: str, output_key: str,
            total_key: str) -> dict[str, int] | None:
    """Normalize a usage mapping; incomplete or inconsistent data is absent.

    Consistency requirements: non-negative integers, ``cached`` within
    ``input``, and ``total`` equal to the input + output sum. A mapping
    that fails any of these is not usage, it is corruption — reported as
    absent rather than repaired.
    """
    if not isinstance(mapping, dict):
        return None
    values: dict[str, int] = {}
    for field, key in (
        ("input", input_key), ("cached", cached_key),
        ("output", output_key), ("total", total_key),
    ):
        value = mapping.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        values[field] = value
    if values["cached"] > values["input"]:
        return None
    if values["total"] != values["input"] + values["output"]:
        return None
    return values


def _finite_nonnegative(value: Any) -> float | None:
    """A price or cost that can be believed, or ``None``.

    ``isinstance(x, float)`` alone accepts ``nan`` and ``inf``; a
    telemetry number that cannot be compared is not a number.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


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


def _collect_unified(session_id: str, home: Path) -> tuple[dict[str, Any] | None, bool]:
    """(normalized payload or None, whether the session dir exists)."""
    session_dir = home / "logs" / "session" / "unified" / session_id
    if not session_dir.is_dir():
        return None, False
    generation = _latest_generation(session_dir)
    if generation is None:
        return None, True
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
        return None, True
    # The Unified Harness persists no prices: cost stays honestly absent.
    return {
        "source": "unified",
        "model": model,
        "session_tokens": session_tokens,
        "context_tokens": context_tokens,
        "pricing": None,
        "list_price_cost_usd": None,
    }, True


def _collect_legacy(session_id: str, home: Path) -> tuple[dict[str, Any] | None, bool]:
    """(normalized payload or None, whether any meta declared this id).

    The directory name only carries the id's first segment, so the match
    is made on ``meta.json``'s own ``session_id`` field — an exact read,
    never a name-prefix guess and never the newest directory. The legacy
    ``config.active_model`` is what was *configured*, not proof of the
    model that served, so no model is reported here.
    """
    root = home / "logs" / "session"
    if not root.is_dir():
        return None, False
    for meta_path in sorted(root.glob("session_*/meta.json")):
        meta = _read_json(meta_path)
        if not isinstance(meta, dict) or meta.get("session_id") != session_id:
            continue
        stats = meta.get("stats")
        if not isinstance(stats, dict):
            return None, True
        pricing = None
        input_price = _finite_nonnegative(stats.get("input_price_per_million"))
        output_price = _finite_nonnegative(stats.get("output_price_per_million"))
        if input_price is not None and output_price is not None:
            cached_price = _finite_nonnegative(
                stats.get("cached_input_price_per_million")
            )
            pricing = {
                "input_per_million": input_price,
                "output_per_million": output_price,
                "cached_input_per_million": cached_price,
            }
        cost = _finite_nonnegative(stats.get("session_cost"))
        session_tokens = None
        values = (
            stats.get("session_prompt_tokens"),
            stats.get("session_cached_tokens"),
            stats.get("session_completion_tokens"),
        )
        if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
               for v in values) and values[1] <= values[0]:
            session_tokens = {
                "input": values[0], "cached": values[1], "output": values[2],
                "total": values[0] + values[2],
            }
        context = stats.get("context_tokens")
        context_tokens = (
            {"total": context}
            if isinstance(context, int) and not isinstance(context, bool)
            and context >= 0
            else None
        )
        if session_tokens is None:
            return None, True
        return {
            "source": "legacy",
            # Configured is not observed: no model claim on this path.
            "model": None,
            "session_tokens": session_tokens,
            "context_tokens": context_tokens,
            "pricing": pricing,
            # Vibe's own list-price arithmetic, never a subscription bill.
            "list_price_cost_usd": cost,
        }, True
    return None, False


def collect(session_id: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Normalized usage for exactly this session, or an honest unavailable.

    A payload with ``available: false`` is a *result*, not an error: the
    caller persists it as the invocation's coordinate with the reason the
    journal could not answer. An unparsable id is refused before the
    filesystem is touched; a filesystem failure underneath the scan is
    the same honest unavailable, never an exception.
    """
    payload: dict[str, Any] = {
        "session_id": session_id,
        "available": False,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": None, "model": None, "session_tokens": None,
        "context_tokens": None, "pricing": None,
        "list_price_cost_usd": None, "reason": None,
    }
    if not isinstance(session_id, str) or not valid_session_id(session_id):
        payload["reason"] = "no valid session id"
        return payload
    home = _vibe_home(env)
    try:
        found, journal_present = _collect_unified(session_id, home)
        if found is None and not journal_present:
            found, legacy_present = _collect_legacy(session_id, home)
            journal_present = legacy_present
    except OSError:
        payload["reason"] = "session journal unreadable"
        return payload
    if found is not None:
        payload.update(found)
        payload["available"] = True
        return payload
    payload["reason"] = (
        "session journal present, no usable usage"
        if journal_present else "session journal not found"
    )
    return payload


def _outbox_dir(env: dict[str, str]) -> Path | None:
    """``BRR_OUTBOX_DIR``, falling back to the ``BRR_PORTAL_STATE`` parent."""
    outbox = env.get("BRR_OUTBOX_DIR")
    if not outbox:
        portal = env.get("BRR_PORTAL_STATE")
        outbox = str(Path(portal).parent) if portal else None
    return Path(outbox) if outbox else None


def _shared_dir(env: dict[str, str]) -> Path | None:
    """The account/repo-shared ``.brr`` dir, when the caller exposed one.

    The per-run outbox is deleted wholesale when the run's task slot is
    retired, so the copy a *later* run can find must live here. The name
    is run-scoped: two concurrent children sharing this dir each keep
    their own record, and no child overwrites another's fact.
    """
    shared = env.get("BRR_SHARED_DIR")
    return Path(shared) if shared else None


def shared_sidecar_name(run_id: str) -> str:
    return f".vibe-usage-{run_id}.json"


def write_sidecar(dir_path: Path, payload: dict[str, Any],
                  *, name: str = SIDECAR_NAME) -> Path | None:
    """Atomic overwrite; telemetry must never break the adapter's reply."""
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        staged = dir_path / (name + ".tmp")
        staged.write_text(
            json.dumps(payload, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        target = dir_path / name
        staged.replace(target)
        return target
    except OSError:
        return None


def load_sidecar(dir_path: Path, *, run_id: str | None = None,
                 name: str | None = None) -> dict[str, Any] | None:
    """Read a sidecar back; a shared read must match its own ``run_id``.

    With ``run_id`` the run-scoped shared name is read and the payload's
    own ``run_id`` must agree — a mismatched record is not this run's
    fact. Without it the fixed outbox name is read.
    """
    if name is None:
        name = shared_sidecar_name(run_id) if run_id else SIDECAR_NAME
    payload = _read_json(dir_path / name)
    if not isinstance(payload, dict):
        return None
    if run_id is not None and payload.get("run_id") != run_id:
        return None
    return payload


def clear_sidecars(env: dict[str, str] | None = None) -> None:
    """Drop this run's stale sidecars before invoking the shell.

    The adapter may be invoked more than once for one run (a retry): a
    fresh attempt that never yields a session id must not leave the
    previous attempt's tokens standing as if they were current.
    """
    resolved = env if env is not None else os.environ
    run_id = str(resolved.get("BRR_RUN_ID") or "").strip()
    outbox = _outbox_dir(resolved)
    if outbox is not None:
        try:
            (outbox / SIDECAR_NAME).unlink(missing_ok=True)
        except OSError:
            pass
    shared = _shared_dir(resolved)
    if shared is not None and run_id:
        try:
            (shared / shared_sidecar_name(run_id)).unlink(missing_ok=True)
        except OSError:
            pass


def capture_stdout(stdout: str, env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Collect this invocation's usage from its own declared session id.

    Called on the same boundary that unwraps the final reply, after the
    headless invocation has exited and its journal is complete. Returns
    the persisted payload, or ``None`` when no exact session coordinate
    could be extracted (nothing is written then — there is nothing exact
    to persist). A ``BRR_RUN_ID`` stamps provenance and scopes the shared
    copy's name. This never raises: telemetry must not change the reply.
    """
    try:
        session_id = extract_session_id(stdout)
        if session_id is None:
            return None
        payload = collect(session_id, env)
        resolved = env if env is not None else os.environ
        run_id = str(resolved.get("BRR_RUN_ID") or "").strip()
        if run_id:
            payload["run_id"] = run_id
        outbox = _outbox_dir(resolved)
        if outbox is not None:
            write_sidecar(outbox, payload)
        shared = _shared_dir(resolved)
        if shared is not None and run_id:
            # Run-scoped name: concurrent children never share this cell.
            write_sidecar(shared, payload, name=shared_sidecar_name(run_id))
        return payload
    except Exception:  # telemetry must not break a good reply
        return None
