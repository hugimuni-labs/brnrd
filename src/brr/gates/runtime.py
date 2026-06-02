"""Shared runtime for poll/deliver gates (telegram, slack, cloud).

These gates differ only in *how* they talk to their platform; the
plumbing around it is identical: a JSON state file under
``.brr/gates/<gate>.json``, per-task progress-card state under
``.brr/gates/<gate>/progress/<task>.json``, a crash-resilient
backoff loop, and a response-delivery skeleton that walks
``protocol.list_done`` and cleans up after a successful send.

This module owns that plumbing so each gate is just its platform
client plus a ``deliver`` closure. The webhook/PR-shaped GitHub
gate (``gates/github/``) is a different protocol and stays out.
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


# ── Per-task progress-card state ─────────────────────────────────────


def task_card_path(brr_dir: Path, gate: str, task_id: str) -> Path:
    """Per-task progress-card state file.

    Each task owns its own file under
    ``.brr/gates/<gate>/progress/<task-id>.json``, so concurrent
    workers handling different tasks never share a state surface.
    See ``kb/design-concurrent-execution.md``.
    """
    safe = _PROGRESS_SAFE_RE.sub("_", task_id) if task_id else "_unknown"
    return brr_dir / "gates" / gate / "progress" / f"{safe}.json"


def load_task_card(brr_dir: Path, gate: str, task_id: str) -> dict | None:
    """Return this task's previously-rendered card state, or None."""
    path = task_card_path(brr_dir, gate, task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_task_card(brr_dir: Path, gate: str, task_id: str, entry: dict) -> None:
    """Write this task's card state file (atomic via rename)."""
    path = task_card_path(brr_dir, gate, task_id)
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
            print(f"[brr:{label}] error: {e}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)


# ── Response delivery skeleton ───────────────────────────────────────


def deliver_responses(
    inbox_dir: Path,
    responses_dir: Path,
    source: str,
    deliver: Callable[[dict, str], None],
) -> None:
    """Deliver completed responses for *source*, then clean up.

    For each done event with a response, call ``deliver(event, body)``.
    A raised exception marks a per-event delivery failure: it is
    logged and skipped (no cleanup), so a transient platform error
    retries on the next loop. Cleanup (event + response file removal)
    happens only after a successful deliver.
    """
    for event in protocol.list_done(inbox_dir, source):
        eid = event["id"]
        body = protocol.read_response(responses_dir, eid)
        if body is None:
            continue
        try:
            deliver(event, body)
        except Exception as e:  # noqa: BLE001 - one bad event must not stall the rest
            print(f"[brr:{source}] delivery error for {eid}: {e}")
            continue
        protocol.cleanup(event["_path"], protocol.response_path(responses_dir, eid))
