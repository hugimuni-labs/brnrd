"""Repository adoption and normalisation logic.

This module contains the functions called by `brr init` and `brr regenerate`.
It scans the repository, runs a summarisation prompt via the configured
executor and writes the resulting files.  The implementation here is
minimal and intended as a scaffold; most logic will reside in prompts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import gitops
from . import runners


def init_repo() -> None:
    """Initialise the current repository for brr management.

    This function performs a minimal set of actions: it ensures we are in a
    Git repository, invokes the executor in adoption mode to get a
    structured summary and then writes a new `AGENTS.md` and
    `agent_state.md` if they do not already exist.  A full implementation
    will preserve existing files and show diffs before writing.
    """
    repo_root = gitops.ensure_git_repo()
    print(f"[brr] Initialising repository at {repo_root}")

    # Determine default executor (hardcoded to codex for now)
    executor = runners.get_default_runner()

    # Invoke adoption prompt
    adoption_spec = runners.run_adoption_prompt(executor)
    # For now, adoption_spec may be None if not implemented
    if adoption_spec is None:
        print("[brr] Adoption prompt not implemented; writing minimal files.")
        # Write minimal files if they don't exist
        write_minimal_files(repo_root)
        return
    # TODO: parse adoption_spec and write files accordingly


def write_minimal_files(repo_root: Path) -> None:
    """Write a minimal AGENTS.md and agent_state.md if absent."""
    agents_file = repo_root / "AGENTS.md"
    state_file = repo_root / "agent_state.md"
    if not agents_file.exists():
        agents_file.write_text(
            "---\n"
            "brr:\n"
            "  version: 1\n"
            "  mode: paused\n"
            "  default_executor: auto\n"
            "  commands:\n"
            '    verify: ""\n'
            '    status: ""\n'
            "  task_sources: []\n"
            "  state_file: agent_state.md\n"
            "  commit_policy: commit-at-end-if-material\n"
            "---\n\n"
            "# Project\n\n"
            "Describe your project here.  This section tells the AI what the\n"
            "repository does, how to build it, how to run it and any other\n"
            "important context.  Keep it clear and concise.\n\n"
        )
        print(f"[brr] Wrote {agents_file}")
    if not state_file.exists():
        state_file.write_text(
            "# Agent State\n\n"
            "## Current Focus\n\n"
            "Not set.\n\n"
            "## Conversation Topics\n\n"
            "Recent threads with the user, compacted. Oldest first.\n\n"
            "## Decisions\n\n"
            "\n"
            "## Discoveries\n\n"
            "\n"
            "## Next Steps\n\n"
            "\n"
            "## Open Questions\n\n"
            "\n"
        )
        print(f"[brr] Wrote {state_file}")