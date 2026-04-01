"""Telegram connector — the reference remote control for brr.

A connector translates between a chat interface and the ``TaskRunner``.
This module implements the Telegram Bot API (via stdlib urllib, zero
deps), interactive setup (auth/connect), and the polling daemon that
dispatches messages as tasks.  Long output overflows to GitHub gists.

To add another connector (Discord, Slack, etc.), use the same
``TaskRunner`` from ``executor.py`` with a different event loop.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

from . import executor
from . import gitops
from . import config as conf

_API = "https://api.telegram.org/bot{token}/{method}"


# ── Credentials ──────────────────────────────────────────────────────

def _local_dir() -> Path:
    d = Path.cwd() / ".brr.local"
    d.mkdir(exist_ok=True)
    return d


def _load_creds() -> dict:
    path = _local_dir() / "telegram.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_creds(data: dict) -> None:
    path = _local_dir() / "telegram.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ── Bot API ──────────────────────────────────────────────────────────

def api_call(token: str, method: str, params: dict | None = None) -> dict:
    """Call a Telegram Bot API method. Returns the parsed response."""
    url = _API.format(token=token, method=method)
    body = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram API error: {e.code} {e.read().decode()}")


def send_message(token: str, chat_id: int, text: str, topic_id: int | None = None) -> dict:
    """Send a text message to a chat, optionally in a topic."""
    params: dict = {"chat_id": chat_id, "text": text}
    if topic_id:
        params["message_thread_id"] = topic_id
    return api_call(token, "sendMessage", params)


def get_updates(token: str, offset: int = 0, timeout: int = 30) -> list[dict]:
    """Long-poll for new messages. Returns list of updates."""
    result = api_call(token, "getUpdates", {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message"],
    })
    return result.get("result", [])


# ── Interactive setup ────────────────────────────────────────────────

def auth() -> None:
    """Interactive auth: prompt for bot token, validate, save."""
    creds = _load_creds()
    token = input("Telegram bot token (from @BotFather): ").strip()
    if not token:
        print("[brr] No token provided.")
        return

    try:
        resp = api_call(token, "getMe")
        bot = resp.get("result", {})
        print(f"[brr] Authenticated as @{bot.get('username', '?')}")
    except RuntimeError as e:
        print(f"[brr] Authentication failed: {e}")
        return

    creds["token"] = token
    _save_creds(creds)
    print("[brr] Token saved to .brr.local/telegram.json")


def connect() -> None:
    """Bind current repo to a Telegram chat/topic."""
    creds = _load_creds()
    if "token" not in creds:
        print("[brr] Run `brr auth telegram` first.")
        return

    chat_id = input("Chat ID (numeric, use @RawDataBot to find it): ").strip()
    if not chat_id:
        print("[brr] No chat ID provided.")
        return

    try:
        creds["chat_id"] = int(chat_id)
    except ValueError:
        print("[brr] Chat ID must be a number.")
        return

    topic_id = input("Topic/thread ID (leave empty for no topic): ").strip()
    if topic_id:
        try:
            creds["topic_id"] = int(topic_id)
        except ValueError:
            print("[brr] Topic ID must be a number.")
            return

    try:
        send_message(creds["token"], creds["chat_id"], "brr connected.", creds.get("topic_id"))
        print("[brr] Test message sent.")
    except RuntimeError as e:
        print(f"[brr] Failed to send test message: {e}")
        return

    _save_creds(creds)
    print("[brr] Connection saved to .brr.local/telegram.json")


# ── Gist overflow ────────────────────────────────────────────────────

def _post_gist(content: str, filename: str = "result.md") -> str | None:
    """Post content to a GitHub gist via ``gh``. Returns URL or None."""
    try:
        result = subprocess.run(
            ["gh", "gist", "create", "--public", "-f", filename, "-"],
            input=content, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ── Daemon ───────────────────────────────────────────────────────────

_MAX_TG_LEN = 3900


def _load_runtime() -> dict:
    path = Path.cwd() / ".brr.local" / "runtime.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_runtime(data: dict) -> None:
    d = Path.cwd() / ".brr.local"
    d.mkdir(exist_ok=True)
    (d / "runtime.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _reply(token: str, chat_id: int, topic_id: int | None, text: str) -> None:
    """Send a reply, posting overflow to a gist when too long for Telegram."""
    if len(text) <= _MAX_TG_LEN:
        send_message(token, chat_id, text, topic_id)
        return
    url = _post_gist(text)
    if url:
        send_message(token, chat_id, f"Result: {url}", topic_id)
    else:
        send_message(token, chat_id, text[:_MAX_TG_LEN] + "\n\n[truncated]", topic_id)


def start_daemon() -> None:
    """Start the Telegram polling daemon for the current repo."""
    repo_root = gitops.ensure_git_repo()
    creds = _load_creds()

    if "token" not in creds or "chat_id" not in creds:
        print("[brr] Telegram not configured.  Run `brr auth telegram` and `brr connect telegram` first.")
        return

    token = creds["token"]
    chat_id = creds["chat_id"]
    topic_id = creds.get("topic_id")

    try:
        exec_name = executor.resolve_executor(repo_root)
    except RuntimeError as e:
        print(f"[brr] {e}")
        return

    print(f"[brr] daemon started for {repo_root}")
    print(f"[brr] executor: {exec_name}")
    print(f"[brr] listening on chat {chat_id}" + (f" topic {topic_id}" if topic_id else ""))
    print("[brr] press Ctrl+C to stop")

    runtime = _load_runtime()
    runtime["pid"] = os.getpid()
    runtime["repo"] = str(repo_root)
    _save_runtime(runtime)

    runner = executor.TaskRunner()
    offset = 0
    running = True

    def _stop(sig, frame):
        nonlocal running
        running = False
        runner.shutdown(timeout=0)
        print("\n[brr] shutting down")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        # Deliver completed task result.
        result = runner.poll_result()
        if result:
            if result["cancelled"]:
                _reply(token, chat_id, topic_id, f"cancelled: {result['instruction']}")
            elif "output" in result:
                _reply(token, chat_id, topic_id, result["output"])
            else:
                _reply(token, chat_id, topic_id, f"task failed: {result['error']}")

        # Short poll while busy so /cancel is responsive.
        poll_timeout = 2 if runner.busy else 30
        try:
            updates = get_updates(token, offset=offset, timeout=poll_timeout)
        except (RuntimeError, OSError) as e:
            print(f"[brr] poll error: {e}")
            continue

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})

            if msg.get("chat", {}).get("id") != chat_id:
                continue
            if topic_id and msg.get("message_thread_id") != topic_id:
                continue

            text = msg.get("text", "").strip()
            if not text:
                continue

            user = msg.get("from", {}).get("first_name", "?")
            print(f"[brr] {user}: {text}")

            if text.lower() in ("/cancel", "cancel"):
                if runner.cancel():
                    _reply(token, chat_id, topic_id, f"cancelling: {runner.instruction}")
                else:
                    _reply(token, chat_id, topic_id, "nothing running")
                continue

            if text.lower() in ("/status", "status", "status?"):
                from . import status as status_mod
                status_text = status_mod.get_status()
                if runner.busy:
                    status_text += f"\n\ncurrently running: {runner.instruction}"
                _reply(token, chat_id, topic_id, status_text)
                continue

            if runner.busy:
                _reply(token, chat_id, topic_id,
                       f"busy with: {runner.instruction}\nsend /cancel to abort it")
                continue

            runner.submit(text)
            try:
                send_message(token, chat_id, f"running: {text}", topic_id)
            except RuntimeError:
                pass

    runner.shutdown()
