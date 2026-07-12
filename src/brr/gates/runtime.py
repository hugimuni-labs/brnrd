"""Shared runtime for gate state, progress cards, and response streaming.

Chat gates (telegram, slack, cloud) share the state-file, progress-card,
backoff, and streaming response-delivery helpers directly. The GitHub gate
has its own poller and progress transport, but reuses ``deliver_stream`` so
interim, terminal, and out-of-bound sends follow the same queue semantics.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

from .. import protocol

_BACKOFF_MAX = 120
_PROGRESS_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ── Gate state file (.brr/gates/<gate>.json) ─────────────────────────


def state_path(brr_dir: Path, gate: str) -> Path:
    return brr_dir / "gates" / f"{gate}.json"


def load_state(brr_dir: Path, gate: str) -> dict:
    path = state_path(brr_dir, gate)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(brr_dir: Path, gate: str, state: dict) -> None:
    path = state_path(brr_dir, gate)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


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
            if poll_interval:
                time.sleep(poll_interval)
            backoff = 1
        except Exception as e:  # noqa: BLE001 - gate threads must not die
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
