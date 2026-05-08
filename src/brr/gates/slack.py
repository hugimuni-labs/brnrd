"""Slack gate — polls channel history, delivers responses.

Uses stdlib urllib only (zero deps).  Credentials and runtime state
live in ``.brr/gates/slack.json``.

Required setup:
- Create a Slack app with ``channels:history``, ``channels:read``,
  ``chat:write`` scopes.
- Run ``brr setup slack`` to save the bot token and choose the channel.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from .. import protocol, run_progress
from ..task import Task

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


def _progress_state_path(brr_dir: Path) -> Path:
    return brr_dir / "gates" / "slack_progress.json"


def _load_progress_state(brr_dir: Path) -> dict:
    path = _progress_state_path(brr_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_progress_state(brr_dir: Path, state: dict) -> None:
    path = _progress_state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _progress_key(task_id: str) -> str:
    return task_id


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


def bind(brr_dir: Path) -> None:
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
            "channel": channel, "text": "brr bound.",
        })
        print("[brr] Test message sent.")
    except Exception as e:
        print(f"[brr] Failed: {e}")
        return
    _save_state(brr_dir, state)
    print("[brr] Binding saved")


def setup(brr_dir: Path) -> None:
    """Configure Slack credentials and channel in one interactive flow."""
    auth(brr_dir)
    if "token" in _load_state(brr_dir):
        bind(brr_dir)


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


# ── Live progress card ──────────────────────────────────────────────


_RENDERABLE_PACKETS = {
    "task_created",
    "env_prepared",
    "container_started",
    "container_preserved",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "finalizing",
    "push_started",
    "push_done",
    "done",
    "failed",
    "conflict",
}


def render_update(brr_dir: Path, packet: Any) -> None:
    """Send/edit a Slack progress card for *packet*.

    On ``task_created`` we post a thread reply in the originating
    channel/thread and store the resulting ``ts`` so later packets can
    update the same message via ``chat.update``. Failures are swallowed
    — the daemon must keep running even if Slack is misconfigured.
    """
    ptype = getattr(packet, "type", None)
    if ptype not in _RENDERABLE_PACKETS:
        return

    state = _load_state(brr_dir)
    token = state.get("token")
    if not token:
        return

    conv_key = getattr(packet, "conversation_key", "") or ""
    task_id = run_progress.task_id_from_packet(packet)
    if not conv_key or not task_id:
        return

    task = Task.from_file(brr_dir / "tasks" / f"{task_id}.md")
    if task is None or task.source != "slack":
        return
    channel = task.meta.get("slack_channel") or state.get("channel")
    if not channel:
        return
    thread_ts = task.meta.get("slack_thread_ts") or task.meta.get("slack_ts")

    view = run_progress.project_task(brr_dir, conv_key, task_id)
    if view is None:
        return
    text = run_progress.render_text(view, compact=True)

    progress_state = _load_progress_state(brr_dir)
    key = _progress_key(task_id)
    entry = progress_state.get(key)

    if entry and entry.get("last_text") == text:
        # Identical to the last rendered message — skip the round-trip.
        entry["last_render"] = ptype
        progress_state[key] = entry
        _save_progress_state(brr_dir, progress_state)
        return

    try:
        if entry and entry.get("ts"):
            try:
                _slack_api(token, "chat.update", {
                    "channel": channel,
                    "ts": entry["ts"],
                    "text": text,
                })
                entry["last_render"] = ptype
                entry["last_text"] = text
                progress_state[key] = entry
                _save_progress_state(brr_dir, progress_state)
                return
            except Exception:
                # The message is genuinely gone (deleted, expired, etc.).
                # Fall through to post a replacement.
                pass
        params: dict = {"channel": channel, "text": text}
        if thread_ts:
            params["thread_ts"] = thread_ts
        resp = _slack_api(token, "chat.postMessage", params)
        ts = resp.get("ts")
        if not ts:
            return
        progress_state[key] = {
            "channel": channel,
            "thread_ts": thread_ts,
            "ts": ts,
            "last_render": ptype,
            "last_text": text,
        }
        _save_progress_state(brr_dir, progress_state)
    except Exception:
        return
