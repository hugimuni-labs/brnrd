"""brr daemon — poll Telegram, dispatch tasks, report results."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

from . import telegram
from . import runners
from . import gitops
from . import config as conf


def _load_registry() -> dict:
    """Load the repo registry from .brr.local/runtime.json."""
    path = Path.cwd() / ".brr.local" / "runtime.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_registry(data: dict) -> None:
    d = Path.cwd() / ".brr.local"
    d.mkdir(exist_ok=True)
    (d / "runtime.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def start() -> None:
    """Start the daemon for the current repo."""
    repo_root = gitops.ensure_git_repo()
    creds = telegram._load_creds()

    if "token" not in creds or "chat_id" not in creds:
        print("[brr] Telegram not configured.  Run `brr auth telegram` and `brr connect telegram` first.")
        return

    token = creds["token"]
    chat_id = creds["chat_id"]
    topic_id = creds.get("topic_id")

    # Verify executor is available
    try:
        executor = runners.resolve_executor(repo_root)
    except RuntimeError as e:
        print(f"[brr] {e}")
        return

    print(f"[brr] daemon started for {repo_root}")
    print(f"[brr] executor: {executor}")
    print(f"[brr] listening on chat {chat_id}" + (f" topic {topic_id}" if topic_id else ""))
    print("[brr] press Ctrl+C to stop")

    # Save PID
    registry = _load_registry()
    registry["pid"] = os.getpid()
    registry["repo"] = str(repo_root)
    _save_registry(registry)

    offset = 0
    running = True

    def _stop(sig, frame):
        nonlocal running
        running = False
        print("\n[brr] shutting down")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        try:
            updates = telegram.get_updates(token, offset=offset, timeout=30)
        except (RuntimeError, OSError) as e:
            print(f"[brr] poll error: {e}")
            continue

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})

            # Filter: only respond to messages in our chat (and topic if set)
            if msg.get("chat", {}).get("id") != chat_id:
                continue
            if topic_id and msg.get("message_thread_id") != topic_id:
                continue

            text = msg.get("text", "").strip()
            if not text:
                continue

            user = msg.get("from", {}).get("first_name", "?")
            print(f"[brr] {user}: {text}")

            # Handle built-in commands
            if text.lower() in ("/status", "status", "status?"):
                _handle_status(token, chat_id, topic_id, repo_root)
                continue

            # Run as task
            _handle_task(token, chat_id, topic_id, repo_root, executor, text)


def _handle_status(token: str, chat_id: int, topic_id: int | None, repo_root: Path) -> None:
    """Send current status from agent_state.md."""
    from . import status as status_mod
    summary = status_mod.get_status()
    try:
        telegram.send_message(token, chat_id, summary, topic_id)
    except RuntimeError as e:
        print(f"[brr] failed to send status: {e}")


def _handle_task(
    token: str, chat_id: int, topic_id: int | None,
    repo_root: Path, executor: str, instruction: str
) -> None:
    """Run a task and send the result back."""
    try:
        telegram.send_message(token, chat_id, f"running: {instruction}", topic_id)
    except RuntimeError:
        pass

    try:
        output = runners.run_task(instruction)
        # Truncate for Telegram's 4096 char limit
        if len(output) > 3900:
            output = output[:3900] + "\n\n[truncated]"
        telegram.send_message(token, chat_id, output, topic_id)
    except Exception as e:
        try:
            telegram.send_message(token, chat_id, f"failed: {e}", topic_id)
        except RuntimeError:
            pass
        print(f"[brr] task failed: {e}")
