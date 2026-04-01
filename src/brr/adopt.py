"""Repository adoption — brr init."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import config as conf
from . import executor
from . import gitops

_AGENTS_TEMPLATE = """\
---
brr:
  version: 1
  mode: paused
  default_executor: auto
  commands:
    build: ""
    test: ""
    verify: ""
  task_sources: []
  state_file: .brr.local/state.md
  commit_policy: commit-at-end-if-material
---

# Project

<!-- What this project is and does. One paragraph. -->

## Build and run

<!-- Exact commands to build, test, and run the project. -->

## Code guidelines

<!-- Style, testing, and commit conventions. -->

## Constraints

<!-- Things the agent must not do without approval. -->
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

    result = executor.run_adopt_prompt(repo_root)
    if result:
        print("[brr] adoption analysis complete")
        print(result)

    _write_if_missing(agents_file, _AGENTS_TEMPLATE)

    cfg = conf.load_config(repo_root)
    state_path = conf.state_file_path(repo_root, cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_if_missing(state_path, _STATE_TEMPLATE)


def _write_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")
    print(f"[brr] wrote {path.name}")
