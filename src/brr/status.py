"""Status and reporting helpers.

This module provides functions to assemble a project status snapshot and
generate narrative reports.  In this seed version, the functions return
placeholder strings.
"""

from __future__ import annotations

from pathlib import Path

from . import gitops


def get_status() -> str:
    """Return a concise status string for the current project.

    A real implementation will read the YAML header in `AGENTS.md` and
    current `agent_state.md`, check the Git branch and determine whether
    there are uncommitted changes.  For now, this returns a static
    message.
    """
    try:
        repo_root = gitops.ensure_git_repo()
    except RuntimeError:
        return "Not a Git repository.  Run `git init` first."
    return f"Project at {repo_root}: status not yet implemented."


def generate_report() -> str:
    """Generate a narrative report about the project.

    In the MVP, this function returns a placeholder.  A full
    implementation will call the executor with the prompt in
    `prompts/report.md` and use `agent_state.md` as context.
    """
    return "Report generation is not implemented yet."