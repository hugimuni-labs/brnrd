"""Repository adoption — brr init."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import gitops
from . import runners

_AGENTS_TEMPLATE = """\
---
brr:
  version: 1
  mode: paused
  default_executor: auto
  commands:
    verify: ""
    status: ""
  task_sources: []
  state_file: agent_state.md
  commit_policy: commit-at-end-if-material
---

# Project

Describe your project here.
"""

_STATE_TEMPLATE = """\
# Agent State

## Current Focus

Not set.

## Conversation Topics

## Decisions

## Discoveries

## Next Steps

## Open Questions
"""


def init_repo(url: str | None = None) -> None:
    """Initialise a repository for brr management.

    If url is provided, clones the repo first.
    """
    if url:
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        print(f"[brr] cloning {url}")
        subprocess.run(["git", "clone", url, name], check=True)
        import os
        os.chdir(name)

    repo_root = gitops.ensure_git_repo()
    agents_file = repo_root / "AGENTS.md"

    if agents_file.exists():
        print(f"[brr] {agents_file} already exists, skipping.")
        return

    # Try adoption via executor
    result = runners.run_adopt_prompt(repo_root)
    if result:
        # TODO: parse structured output and generate tailored files
        print("[brr] adoption analysis complete")
        print(result)

    # Write template files
    _write_if_missing(agents_file, _AGENTS_TEMPLATE)
    state_file = repo_root / "agent_state.md"
    _write_if_missing(state_file, _STATE_TEMPLATE)


def _write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")
    print(f"[brr] wrote {path.name}")
