"""How a body is started. Fake for the suite; claude for the demo."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from .home import atomic_write


def fake_argv(room: Path, policy: str) -> list[str]:
    return [sys.executable, "-m", "brr.loom.runtime.fakebody",
            "--room", str(room), "--policy", policy]


def _hook(room: Path, event: str, timeout: int | None) -> dict:
    command = (
        f"{shlex.quote(sys.executable)} -m brr.loom.runtime jack "
        f"--shell claude --event {event} --room {shlex.quote(str(room))}"
    )
    item: dict = {"type": "command", "command": command}
    if timeout is not None:
        item["timeout"] = timeout
    return item


def write_claude_settings(room: Path, wait: float) -> Path:
    """Stop's hook timeout is ``wait + 60``. ``.*`` matches every tool call.

    Checked against ``claude --help`` on this machine (2026-10-08): ``-p``,
    ``--model``, ``--settings`` and ``--dangerously-skip-permissions`` exist.
    """
    stop_timeout = int(wait) + 60
    settings = {
        "hooks": {
            "PostToolUse": [{"matcher": ".*", "hooks": [_hook(room, "post", 60)]}],
            "Stop": [{"hooks": [_hook(room, "stop", stop_timeout)]}],
            "SessionStart": [{"hooks": [_hook(room, "start", 60)]}],
        }
    }
    path = room / ".claude" / "settings.json"
    atomic_write(path, json.dumps(settings, indent=2) + "\n")
    return path


def claude_argv(room: Path, core: str, wait: float) -> list[str]:
    settings = write_claude_settings(room, wait)
    prompt = (room / "port" / "wake.md").read_text()
    # `--` so a step-2 README, which starts with `---`, is a prompt and not
    # an option. Checked 2026-10-08: `claude -p -- '--- hello'` runs.
    return ["claude", "-p", "--model", core, "--settings", str(settings),
            "--dangerously-skip-permissions", "--", prompt]


def wait_seconds(room: Path) -> float:
    path = room / "port" / "wait"
    if not path.is_file():
        return 3600.0
    return float(path.read_text().strip())
