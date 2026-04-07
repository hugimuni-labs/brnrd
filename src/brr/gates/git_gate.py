"""Git gate — detects new files in a watch directory via git fetch.

Designed for Git-based workflows: Obsidian with git-sync, GitHub web
editor, or any tool that pushes markdown files to a tasks branch.

The gate watches a configurable directory (default: ``tasks/``) for
new files by comparing ``git fetch origin`` + ``git diff``.  Each
new file becomes an inbox event.

Delivery is a no-op — the agent's commit and the daemon's push *are*
the delivery.

State lives in ``.brr/gates/git_gate.json``.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .. import protocol

_BACKOFF_MAX = 120
_POLL_INTERVAL = 30


# ── State ────────────────────────────────────────────────────────────


def _state_path(brr_dir: Path) -> Path:
    return brr_dir / "gates" / "git_gate.json"


def _load_state(brr_dir: Path) -> dict:
    path = _state_path(brr_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_state(brr_dir: Path, state: dict) -> None:
    path = _state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# ── Git helpers ──────────────────────────────────────────────────────


def _run_git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, timeout=60,
    )
    return result.stdout.strip()


# ── Setup ────────────────────────────────────────────────────────────


def auth(brr_dir: Path) -> None:
    print("[brr:git] No auth needed for git gate.")
    print("[brr:git] Make sure the remote has a 'tasks/' directory")
    print("[brr:git] (or configure watch_dir in .brr/gates/git_gate.json).")


def connect(brr_dir: Path) -> None:
    state = _load_state(brr_dir)
    watch_dir = input("Watch directory (default: tasks/): ").strip() or "tasks/"
    state["watch_dir"] = watch_dir

    use_pull = input("Use git pull instead of fetch+diff? (y/N): ").strip().lower()
    state["use_pull"] = use_pull in ("y", "yes")

    head = _run_git("rev-parse", "HEAD")
    state["last_commit"] = head
    _save_state(brr_dir, state)
    print(f"[brr:git] Watching '{watch_dir}' from commit {head[:8]}")


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return "watch_dir" in state


# ── Gate loop ────────────────────────────────────────────────────────


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    backoff = 1
    while True:
        try:
            _loop_once(brr_dir, inbox_dir)
            time.sleep(_POLL_INTERVAL)
            backoff = 1
        except Exception as e:
            print(f"[brr:git] error: {e}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


def _loop_once(brr_dir: Path, inbox_dir: Path) -> None:
    state = _load_state(brr_dir)
    watch_dir = state.get("watch_dir", "tasks/")
    use_pull = state.get("use_pull", False)
    last_commit = state.get("last_commit", "")
    repo_root = brr_dir.parent

    if use_pull:
        _run_git("pull", "--ff-only", cwd=repo_root)
    else:
        _run_git("fetch", "origin", cwd=repo_root)

    head = _run_git("rev-parse", "HEAD" if use_pull else "FETCH_HEAD", cwd=repo_root)
    if not head or head == last_commit:
        return

    diff_output = _run_git(
        "diff", "--name-only", "--diff-filter=A",
        f"{last_commit}..{head}", "--", watch_dir,
        cwd=repo_root,
    )

    if not diff_output:
        state["last_commit"] = head
        _save_state(brr_dir, state)
        return

    for filename in diff_output.splitlines():
        filename = filename.strip()
        if not filename:
            continue
        content = _run_git("show", f"{head}:{filename}", cwd=repo_root)
        if content:
            protocol.create_event(
                inbox_dir,
                source="git",
                body=content,
                git_file=filename,
                git_commit=head[:12],
            )

    state["last_commit"] = head
    _save_state(brr_dir, state)
