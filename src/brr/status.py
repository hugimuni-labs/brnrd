"""Status — read and display agent_state.md."""

from __future__ import annotations

from pathlib import Path

from . import gitops
from . import config as conf


def get_status() -> str:
    """Return a formatted status string from agent_state.md and git."""
    try:
        repo_root = gitops.ensure_git_repo()
    except RuntimeError:
        return "Not a Git repository."

    cfg = conf.load_config(repo_root)
    state_path = conf.state_file_path(repo_root, cfg)

    parts = [f"repo: {repo_root.name}"]
    parts.append(f"mode: {cfg.get('mode', '?')}")
    parts.append(f"executor: {cfg.get('default_executor', '?')}")

    if state_path.exists():
        parts.append("")
        parts.append(state_path.read_text(encoding="utf-8").strip())
    else:
        parts.append("\nNo state file found.")

    return "\n".join(parts)
