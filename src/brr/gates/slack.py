"""Slack gate — polls channel history, delivers responses.

Uses stdlib urllib only (zero deps).  Credentials and runtime state
live in ``.brr/gates/slack.json``.

Required setup:
- Create a Slack app with ``channels:history``, ``channels:read``,
  ``chat:write`` scopes.
- Run ``brr auth slack`` to save the bot token and channel ID.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from pathlib import Path

from .. import protocol

_BACKOFF_MAX = 120
_POLL_INTERVAL = 5


# ── Slack API helpers ────────────────────────────────────────────────


def _slack_api(token: str, method: str, params: dict | None = None) -> dict:
    url = f"https://slack.com/api/{method}"
    body = json.dumps(params or {}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
    return data


# ── State ────────────────────────────────────────────────────────────


def _state_path(brr_dir: Path) -> Path:
    return brr_dir / "gates" / "slack.json"


def _load_state(brr_dir: Path) -> dict:
    path = _state_path(brr_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_state(brr_dir: Path, state: dict) -> None:
    path = _state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# ── Interactive setup ────────────────────────────────────────────────


def auth(brr_dir: Path) -> None:
    state = _load_state(brr_dir)
    token = input("Slack bot token (xoxb-...): ").strip()
    if not token:
        print("[brr] No token provided.")
        return
    try:
        _slack_api(token, "auth.test")
        print("[brr] Token validated.")
    except Exception as e:
        print(f"[brr] Auth failed: {e}")
        return
    state["token"] = token
    _save_state(brr_dir, state)
    print("[brr] Token saved")


def connect(brr_dir: Path) -> None:
    state = _load_state(brr_dir)
    if "token" not in state:
        print("[brr] Run `brr auth slack` first.")
        return
    channel = input("Slack channel ID (C0...): ").strip()
    if not channel:
        print("[brr] No channel provided.")
        return
    state["channel"] = channel
    try:
        _slack_api(state["token"], "chat.postMessage", {
            "channel": channel, "text": "brr connected.",
        })
        print("[brr] Test message sent.")
    except Exception as e:
        print(f"[brr] Failed: {e}")
        return
    _save_state(brr_dir, state)
    print("[brr] Connection saved")


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return "token" in state and "channel" in state


# ── Gate loop ────────────────────────────────────────────────────────


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    backoff = 1
    while True:
        try:
            _loop_once(brr_dir, inbox_dir, responses_dir)
            time.sleep(_POLL_INTERVAL)
            backoff = 1
        except Exception as e:
            print(f"[brr:slack] error: {e}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    token = state["token"]
    channel = state["channel"]
    oldest_ts = state.get("oldest_ts", str(time.time()))

    data = _slack_api(token, "conversations.history", {
        "channel": channel,
        "oldest": oldest_ts,
        "limit": 50,
    })

    messages = data.get("messages", [])
    for msg in reversed(messages):
        if msg.get("subtype"):
            continue
        text = msg.get("text", "").strip()
        if not text:
            continue
        ts = msg.get("ts", "")
        user = msg.get("user", "?")
        protocol.create_event(
            inbox_dir,
            source="slack",
            body=text,
            slack_channel=channel,
            slack_user=user,
            slack_ts=ts,
        )
        if ts > oldest_ts:
            oldest_ts = ts

    if oldest_ts != state.get("oldest_ts"):
        state["oldest_ts"] = oldest_ts
        _save_state(brr_dir, state)

    _deliver_responses(brr_dir, inbox_dir, responses_dir, token, channel)


def _deliver_responses(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    token: str,
    channel: str,
) -> None:
    for event in protocol.list_done(inbox_dir, "slack"):
        eid = event["id"]
        body = protocol.read_response(responses_dir, eid)
        if body is None:
            continue
        try:
            _slack_api(token, "chat.postMessage", {
                "channel": channel, "text": body,
            })
        except Exception as e:
            print(f"[brr:slack] delivery error for {eid}: {e}")
            continue
        resp_path = protocol.response_path(responses_dir, eid)
        protocol.cleanup(event["_path"], resp_path)
