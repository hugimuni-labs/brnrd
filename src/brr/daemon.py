"""Daemon — process lifecycle and task dispatch loop.

The daemon is transport-agnostic.  It receives user messages through a
Connector, dispatches them to the TaskRunner, and delivers results back
through the same Connector.  Signal handling, PID tracking, and command
dispatch (/cancel, /status) live here — connectors only do I/O.
"""

from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import executor
from . import gitops
from . import status as status_mod


@dataclass
class Message:
    """A single inbound message from the chat transport."""
    text: str
    user: str


class Connector(Protocol):
    """Transport adapter that polls for messages and sends replies.

    Implementations handle transport-specific details (authentication,
    message filtering, length limits) internally.
    """

    def poll(self, timeout: int) -> list[Message]:
        """Block up to *timeout* seconds, return new messages."""
        ...

    def reply(self, text: str) -> None:
        """Send a reply to the connected chat."""
        ...

    def describe(self) -> str:
        """One-line description for startup logging."""
        ...


def _load_runtime() -> dict:
    path = Path.cwd() / ".brr.local" / "runtime.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_runtime(data: dict) -> None:
    d = Path.cwd() / ".brr.local"
    d.mkdir(exist_ok=True)
    (d / "runtime.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def start(connector: Connector) -> None:
    """Run the daemon loop with the given connector until interrupted."""
    repo_root = gitops.ensure_git_repo()

    try:
        exec_name = executor.resolve_executor(repo_root)
    except RuntimeError as e:
        print(f"[brr] {e}")
        return

    print(f"[brr] daemon started for {repo_root}")
    print(f"[brr] executor: {exec_name}")
    print(f"[brr] {connector.describe()}")
    print("[brr] press Ctrl+C to stop")

    runtime = _load_runtime()
    runtime["pid"] = os.getpid()
    runtime["repo"] = str(repo_root)
    _save_runtime(runtime)

    runner = executor.TaskRunner()
    running = True

    def _stop(sig, frame):
        nonlocal running
        running = False
        runner.shutdown(timeout=0)
        print("\n[brr] shutting down")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        result = runner.poll_result()
        if result:
            if result["cancelled"]:
                connector.reply(f"cancelled: {result['instruction']}")
            elif "output" in result:
                connector.reply(result["output"])
            else:
                connector.reply(f"task failed: {result['error']}")

        poll_timeout = 2 if runner.busy else 30
        try:
            messages = connector.poll(timeout=poll_timeout)
        except (RuntimeError, OSError) as e:
            print(f"[brr] poll error: {e}")
            continue

        for msg in messages:
            print(f"[brr] {msg.user}: {msg.text}")

            if msg.text.lower() in ("/cancel", "cancel"):
                if runner.cancel():
                    connector.reply(f"cancelling: {runner.instruction}")
                else:
                    connector.reply("nothing running")
                continue

            if msg.text.lower() in ("/status", "status", "status?"):
                status_text = status_mod.get_status()
                if runner.busy:
                    status_text += f"\n\ncurrently running: {runner.instruction}"
                connector.reply(status_text)
                continue

            if runner.busy:
                connector.reply(
                    f"busy with: {runner.instruction}\nsend /cancel to abort it"
                )
                continue

            runner.submit(msg.text)
            connector.reply(f"running: {msg.text}")

    runner.shutdown()
