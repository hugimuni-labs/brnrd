"""Shared runtime for gate state, progress cards, and response streaming.

Chat gates (telegram, slack, cloud) share the state-file, progress-card,
backoff, and streaming response-delivery helpers directly. The GitHub gate
has its own poller and progress transport, but reuses ``deliver_stream`` so
interim, terminal, and out-of-bound sends follow the same queue semantics.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .. import protocol

_BACKOFF_MAX = 120
_PROGRESS_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
GATE_HEALTH_DEGRADED_AFTER_S = 300
_BUILTIN_GATES = ("telegram", "slack", "github", "cloud")
_PRIVATE_STATE_MODE = 0o600


# ── Gate state file (.brr/gates/<gate>.json) ─────────────────────────


def state_path(brr_dir: Path, gate: str) -> Path:
    return brr_dir / "gates" / f"{gate}.json"


def load_state(brr_dir: Path, gate: str) -> dict:
    path = state_path(brr_dir, gate)
    if path.exists():
        if os.name == "posix":
            path.chmod(_PRIVATE_STATE_MODE)
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(brr_dir: Path, gate: str, state: dict) -> None:
    """Atomically save secret-bearing gate state with private POSIX mode.

    Gate state may contain access tokens.  The temporary file is created
    private before it is replaced into place, so both new files and rewrites
    repair permissive existing modes without exposing a partially-written
    secret.  Windows ACLs provide the platform's access control; POSIX mode
    bits are enforced here only where they are meaningful.
    """
    path = state_path(brr_dir, gate)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        if os.name == "posix":
            os.fchmod(fd, _PRIVATE_STATE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(json.dumps(state, indent=2) + "\n")
        os.replace(tmp_name, path)
    finally:
        if fd != -1:
            os.close(fd)
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


# ── Per-gate health (.brr/gates/<gate>.health.json) ────────────────


def health_path(brr_dir: Path, gate: str) -> Path:
    return brr_dir / "gates" / f"{gate}.health.json"


def load_health(brr_dir: Path, gate: str) -> dict:
    path = health_path(brr_dir, gate)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def record_health(
    brr_dir: Path,
    gate: str,
    *,
    ok: bool,
    error: str | None = None,
) -> None:
    """Atomically record one ingestion-loop outcome without touching cursor state."""
    now = datetime.now(timezone.utc).isoformat()
    health = load_health(brr_dir, gate)
    health.setdefault("last_poll_ok", None)
    health.setdefault("last_error", None)
    health.setdefault("last_error_at", None)
    if ok:
        health["last_poll_ok"] = now
    else:
        health["last_error"] = error
        health["last_error_at"] = now

    path = health_path(brr_dir, gate)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(health, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def configured_gates(brr_dir: Path) -> list[str]:
    """Return configured built-in ingestion gates for this runtime directory."""
    from . import import_gate

    configured: list[str] = []
    for gate in _BUILTIN_GATES:
        try:
            module = import_gate(gate)
            if module.is_configured(brr_dir):
                configured.append(gate)
        except (ImportError, OSError, ValueError, json.JSONDecodeError):
            continue
    return configured


def gate_health_rows(
    brr_dir: Path,
    *,
    gates: Iterable[str] | None = None,
    now: datetime | None = None,
    degraded_after_s: int = GATE_HEALTH_DEGRADED_AFTER_S,
) -> list[dict]:
    """Classify configured gates from their last successful poll timestamp."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows: list[dict] = []
    for gate in gates if gates is not None else configured_gates(brr_dir):
        health = load_health(brr_dir, gate)
        last_poll_ok = health.get("last_poll_ok")
        age_seconds: int | None = None
        if isinstance(last_poll_ok, str):
            try:
                polled_at = datetime.fromisoformat(last_poll_ok.replace("Z", "+00:00"))
                if polled_at.tzinfo is None:
                    polled_at = polled_at.replace(tzinfo=timezone.utc)
                age_seconds = max(0, int((now - polled_at).total_seconds()))
            except ValueError:
                last_poll_ok = None
        status = (
            "never"
            if age_seconds is None
            else "degraded" if age_seconds > degraded_after_s else "ok"
        )
        last_error = health.get("last_error")
        rows.append(
            {
                "gate": gate,
                "last_poll_ok": last_poll_ok,
                "age_seconds": age_seconds,
                "last_error": last_error if isinstance(last_error, str) else None,
                "status": status,
            }
        )
    return rows


def record_loop_health(
    brr_dir: Path | None,
    gate: str | None,
    *,
    ok: bool,
    error: str | None = None,
) -> None:
    if brr_dir is None or gate is None:
        return
    try:
        record_health(brr_dir, gate, ok=ok, error=error)
    except OSError as exc:
        print(f"[brnrd:{gate}] health record failed: {exc}")


# ── Per-run progress-card state ──────────────────────────────────────


def run_card_path(brr_dir: Path, gate: str, run_id: str) -> Path:
    """Per-run progress-card state file.

    Each run owns its own file under
    ``.brr/gates/<gate>/progress/<run-id>.json``, so overlapping
    thoughts (ad-hoc sessions, a second daemon) never share a state
    surface. See ``kb/subject-daemon.md``.
    """
    safe = _PROGRESS_SAFE_RE.sub("_", run_id) if run_id else "_unknown"
    return brr_dir / "gates" / gate / "progress" / f"{safe}.json"


def load_run_card(brr_dir: Path, gate: str, run_id: str) -> dict | None:
    """Return this run's previously-rendered card state, or None."""
    path = run_card_path(brr_dir, gate, run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_run_card(brr_dir: Path, gate: str, run_id: str, entry: dict) -> None:
    """Write this run's card state file (atomic via rename)."""
    path = run_card_path(brr_dir, gate, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


# ── Backoff loop ─────────────────────────────────────────────────────


def run_loop(
    loop_once: Callable[[], None],
    *,
    label: str,
    poll_interval: float = 0.0,
    backoff_max: int = _BACKOFF_MAX,
    brr_dir: Path | None = None,
    gate: str | None = None,
) -> None:
    """Run *loop_once* forever, retrying with exponential backoff.

    ``poll_interval`` is the post-success pause (0 for gates whose
    own poll long-polls, e.g. Telegram's getUpdates). Designed to run
    in a daemon thread; exceptions are caught and retried.
    """
    backoff = 1
    while True:
        try:
            loop_once()
            record_loop_health(brr_dir, gate, ok=True)
            if poll_interval:
                time.sleep(poll_interval)
            backoff = 1
        except Exception as e:  # noqa: BLE001 - gate threads must not die
            record_loop_health(brr_dir, gate, ok=False, error=str(e))
            print(f"[brnrd:{label}] error: {e}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)


# ── Response delivery skeleton ───────────────────────────────────────


def deliver_stream(
    inbox_dir: Path,
    responses_dir: Path,
    source: str,
    deliver_partial: Callable[[dict, str], None],
    deliver_terminal: Callable[[dict, str], None] | None = None,
) -> None:
    """Stream a per-event response queue, then the terminal response.

    The multi-response delivery surface (see
    ``kb/design-multi-response.md``). For each **active**
    (``processing`` or ``done``) event matching *source*, oldest first:

    1. deliver each pending interim response in order, deleting it
       after a successful send — so delivery is resumable: a transient
       platform error retries from the first undelivered partial on the
       next loop;
    2. **only when the event is ``done``**, deliver the terminal
       response (``<eid>.md``) and clean up the event, terminal file,
       and partials queue.

    *deliver_partial* sends an interim message; *deliver_terminal* sends
    the closing message (defaults to *deliver_partial*). The split lets
    a gate decorate the terminal differently (e.g. the GitHub gate's
    branch footer rides only the terminal). A raised exception stops
    that one event and is logged; other events still flow.
    """
    if deliver_terminal is None:
        deliver_terminal = deliver_partial
    for event in protocol.list_active(inbox_dir, source):
        eid = event["id"]
        try:
            for ppath in protocol.list_partials(responses_dir, eid):
                body = protocol.read_partial(ppath)
                if body:
                    deliver_partial(event, body)
                ppath.unlink(missing_ok=True)
            if event.get("status") == "done":
                body = protocol.read_response(responses_dir, eid)
                if body is not None:
                    deliver_terminal(event, body)
                protocol.cleanup(
                    event["_path"],
                    protocol.response_path(responses_dir, eid),
                    protocol.partials_dir(responses_dir, eid),
                )
        except Exception as e:  # noqa: BLE001 - one bad event must not stall the rest
            print(f"[brnrd:{source}] delivery error for {eid}: {e}")
            continue


def deliver_responses(
    inbox_dir: Path,
    responses_dir: Path,
    source: str,
    deliver: Callable[[dict, str], None],
) -> None:
    """Deliver responses for *source* (interim + terminal), then clean up.

    Thin wrapper over :func:`deliver_stream` for gates whose interim and
    terminal messages are delivered the same way (telegram, slack,
    cloud). A plain single-response run delivers exactly one message and
    cleans up on ``done`` — unchanged from before the streaming queue.
    """
    deliver_stream(inbox_dir, responses_dir, source, deliver)
