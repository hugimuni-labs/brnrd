"""Slack gate — polls channel history, delivers responses.

Credentials and runtime state live in ``.brr/gates/slack.json``.

Required setup:
- Create a Slack app with ``channels:history``, ``channels:read``,
  ``chat:write`` scopes.
- Run ``brr setup slack`` to save the bot token and choose the channel.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from .. import protocol, run_progress
from ..run import Run, run_manifest_path
from . import runtime

_POLL_INTERVAL = 5

# One Session for the gate's single loop thread: keep-alive reuses the
# connection across polls instead of dialing fresh each call. See
# ``kb/subject-daemon.md`` → gate responsiveness.
_SESSION = requests.Session()


# ── Slack API helpers ────────────────────────────────────────────────


def _slack_api(token: str, method: str, params: dict | None = None) -> dict:
    url = f"https://slack.com/api/{method}"
    response = _SESSION.post(
        url,
        json=params or {},
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Slack API error: non-object response")
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
    return data


# ── State ────────────────────────────────────────────────────────────


def _load_state(brr_dir: Path) -> dict:
    return runtime.load_state(brr_dir, "slack")


def _save_state(brr_dir: Path, state: dict) -> None:
    runtime.save_state(brr_dir, "slack", state)


def _load_progress_for_run(brr_dir: Path, run_id: str) -> dict | None:
    """Return this run's previously-rendered card state, or None."""
    return runtime.load_run_card(brr_dir, "slack", run_id)


def _save_progress_for_run(brr_dir: Path, run_id: str, entry: dict) -> None:
    """Write this run's card state file (atomic via rename)."""
    runtime.save_run_card(brr_dir, "slack", run_id, entry)


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
    runtime.run_loop(
        lambda: _loop_once(brr_dir, inbox_dir, responses_dir),
        label="slack",
        poll_interval=_POLL_INTERVAL,
    )


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
        # ``thread_ts`` is the parent message's ts when this message is
        # itself a reply inside an existing thread. Capturing it lets
        # both the progress card and the final response thread under the
        # original thread root rather than spawning a new one. When the
        # message is itself the start of a conversation, ``thread_ts``
        # is absent and ``slack_ts`` serves the same role downstream.
        thread_ts = msg.get("thread_ts") or ""
        protocol.create_event(
            inbox_dir,
            source="slack",
            body=text,
            slack_channel=channel,
            slack_user=user,
            slack_ts=ts,
            slack_thread_ts=thread_ts,
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
    def deliver(event: dict, body: str) -> None:
        event_channel = str(event.get("slack_channel") or channel)
        # Match the progress card: prefer the parent thread when the
        # source message was itself a reply, otherwise treat the source
        # message as the thread root. Without this the final response
        # ends up posted at channel level while the card lives in the
        # thread, splitting the conversation in half.
        thread_ts = str(
            event.get("slack_thread_ts") or event.get("slack_ts") or ""
        )
        params: dict = {"channel": event_channel, "text": body}
        if thread_ts:
            params["thread_ts"] = thread_ts
        _slack_api(token, "chat.postMessage", params)

    runtime.deliver_responses(inbox_dir, responses_dir, "slack", deliver)


# ── Live progress card ──────────────────────────────────────────────


_RENDERABLE_PACKETS = {
    "run_created",
    "env_prepared",
    "container_started",
    "container_preserved",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "heartbeat",
    "finalizing",
    "attending",
    "push_started",
    "push_done",
    "done",
    "failed",
    "conflict",
}


def render_update(brr_dir: Path, packet: Any) -> None:
    """Send/edit a Slack progress card for *packet*.

    On ``run_created`` we post a thread reply in the originating
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
    run_id = run_progress.run_id_from_packet(packet)
    if not conv_key or not run_id:
        return

    task = Run.from_file(run_manifest_path(brr_dir / "runs", run_id))
    if task is None or task.source != "slack":
        return
    channel = task.meta.get("slack_channel") or state.get("channel")
    if not channel:
        return
    thread_ts = task.meta.get("slack_thread_ts") or task.meta.get("slack_ts")

    view = run_progress.project_run(brr_dir, conv_key, run_id)
    if view is None:
        return
    text = run_progress.render_text(
        view, compact=True, style=run_progress.SLACK_MRKDWN_STYLE,
    )

    entry = _load_progress_for_run(brr_dir, run_id)

    if entry and entry.get("last_text") == text:
        # Identical to the last rendered message — skip the round-trip.
        entry["last_render"] = ptype
        _save_progress_for_run(brr_dir, run_id, entry)
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
                _save_progress_for_run(brr_dir, run_id, entry)
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
        _save_progress_for_run(brr_dir, run_id, {
            "channel": channel,
            "thread_ts": thread_ts,
            "ts": ts,
            "last_render": ptype,
            "last_text": text,
        })
    except Exception:
        return
