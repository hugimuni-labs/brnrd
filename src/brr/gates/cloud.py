"""Cloud gate — drains a brnrd project inbox into the local ``.brr/``.

This is the daemon side of the inbox-as-service protocol
(``kb/design-brnrd-protocol.md``). It long-polls brnrd for events
scoped to the connected project, writes them as ordinary
``.brr/inbox/`` events (so the runner handles them exactly like
Telegram / Slack messages), and posts runner responses back to
brnrd, which forwards them to the originating platform.

Credentials + the ``since`` cursor live in ``.brr/gates/cloud.json``
via the shared gate runtime. ``brr brnrd connect`` populates them
through the device-flow pairing handshake.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .. import protocol
from . import runtime

# Server long-polls up to ~25s; the client timeout must comfortably
# exceed that so a quiet inbox isn't read as a transport error.
_POLL_WAIT_S = 25
_HTTP_TIMEOUT_S = 60
_DEFAULT_DAEMON_NAME = "daemon"


# ── HTTP seam ────────────────────────────────────────────────────────


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    json: dict | None = None,
    params: dict | None = None,
    timeout: float = _HTTP_TIMEOUT_S,
) -> dict:
    """Single chokepoint for brnrd HTTP. Tests monkeypatch this."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.request(
        method,
        base_url.rstrip("/") + path,
        json=json,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(
            f"brnrd {method} {path} -> {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json() if resp.content else {}


# ── State ────────────────────────────────────────────────────────────


def _load_state(brr_dir: Path) -> dict:
    return runtime.load_state(brr_dir, "cloud")


def _save_state(brr_dir: Path, state: dict) -> None:
    runtime.save_state(brr_dir, "cloud", state)


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return bool(state.get("token") and state.get("brnrd_url") and state.get("project_id"))


# ── Device-flow connect ──────────────────────────────────────────────


def connect(
    brr_dir: Path,
    *,
    brnrd_url: str,
    daemon_name: str = _DEFAULT_DAEMON_NAME,
    poll_interval_s: float = 2.0,
    timeout_s: float = 600.0,
    out: Callable[[str], None] = print,
) -> dict:
    """Run the device-flow pairing and persist the daemon token.

    Starts a pair, prints the approval URL + code, then polls until
    the account approves it against a project (via the dashboard or
    the approve endpoint) and brnrd hands back a project-scoped
    daemon token.
    """
    pair = _request(brnrd_url, "POST", "/v1/accounts/pair")
    out(f"[brr] Approve this daemon at: {pair['pair_url']}")
    out(f"[brr] Pair code: {pair['pair_code']}")

    deadline = time.monotonic() + timeout_s
    while True:
        status = _request(
            brnrd_url,
            "GET",
            f"/v1/accounts/pair/{pair['pair_code']}",
            params={"poll_secret": pair["poll_secret"]},
        )
        if status.get("status") == "paired" and status.get("daemon_token"):
            break
        if time.monotonic() > deadline:
            raise TimeoutError("pairing timed out — re-run `brr brnrd connect`")
        time.sleep(poll_interval_s)

    state = _load_state(brr_dir)
    state.update(
        {
            "brnrd_url": brnrd_url.rstrip("/"),
            "token": status["daemon_token"],
            "project_id": status["project_id"],
            "daemon_name": daemon_name,
            "since": state.get("since", 0),
        }
    )
    _save_state(brr_dir, state)
    out(f"[brr] Connected to brnrd project {status['project_id']}.")
    return state


def setup(brr_dir: Path) -> None:
    """``brr setup cloud`` points at the device-flow connect verb."""
    print("[brr] Run `brr brnrd connect` to link this daemon to a brnrd project.")


def auth(brr_dir: Path) -> None:
    setup(brr_dir)


def bind(brr_dir: Path) -> None:
    setup(brr_dir)


# ── Gate loop ────────────────────────────────────────────────────────


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    """Register once, then long-poll + deliver in a backoff loop."""
    state = _load_state(brr_dir)
    try:
        _register(state)
    except Exception as e:  # noqa: BLE001 - registration is best-effort
        print(f"[brr:cloud] register failed: {e}")
    runtime.run_loop(
        lambda: _loop_once(brr_dir, inbox_dir, responses_dir),
        label="cloud",
    )


def _register(state: dict) -> None:
    _request(
        state["brnrd_url"],
        "POST",
        "/v1/daemons/register",
        token=state["token"],
        json={"daemon_name": state.get("daemon_name", _DEFAULT_DAEMON_NAME), "capabilities": {}},
    )


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    since = state.get("since", 0)

    result = _request(
        state["brnrd_url"],
        "GET",
        "/v1/daemons/inbox",
        token=state["token"],
        params={"since": since, "wait": _POLL_WAIT_S},
    )
    events = result.get("events", [])
    for ev in events:
        protocol.create_event(
            inbox_dir,
            source="cloud",
            body=ev.get("body") or "",
            cloud_event_id=ev["event_id"],
            cloud_reply_to=json.dumps(ev.get("reply_to") or {}),
        )
    cursor = result.get("cursor", since)
    if cursor > since:
        state["since"] = cursor
        _save_state(brr_dir, state)

    _deliver_responses(brr_dir, inbox_dir, responses_dir, state)


def _deliver_responses(
    brr_dir: Path, inbox_dir: Path, responses_dir: Path, state: dict
) -> None:
    def deliver(event: dict, body: str) -> None:
        cloud_event_id = event.get("cloud_event_id")
        if not cloud_event_id:
            raise RuntimeError("missing cloud_event_id")
        _request(
            state["brnrd_url"],
            "POST",
            "/v1/daemons/responses",
            token=state["token"],
            json={"event_id": cloud_event_id, "body_markdown": body, "status": "done"},
        )

    runtime.deliver_responses(inbox_dir, responses_dir, "cloud", deliver)
