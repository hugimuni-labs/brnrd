"""Repository adoption — brr init."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import config as conf
from . import executor
from . import gitops

_KNOWN_INSTRUCTION_FILES = ["CLAUDE.md", "GEMINI.md", "CODEX.md"]

_SETUP_INSTRUCTION = (
    "Read this repository and fill in the AGENTS.md body sections. "
    "Replace the HTML comment placeholders with actual content: "
    "what the project does, exact build/test/run commands "
    "(from Makefile, package.json, CI configs, etc.), code style "
    "and commit conventions, and constraints the agent should respect. "
    "Keep it concise — under a page. Do not modify the YAML frontmatter."
)

_FRONTMATTER = """\
---
brr:
  version: 1
  mode: paused
  default_executor: {executor}
  auto_approve: true
  commands:
    build: ""
    test: ""
    verify: ""
  task_sources: []
  state_file: .brr.local/state.md
  commit_policy: commit-at-end-if-material
---

"""

_SKELETON_BODY = """\
# Project

<!-- What this project is and does. One paragraph. -->

## Build and run

<!-- Exact commands to build, test, and run the project. -->

## Code guidelines

<!-- Style, testing, and commit conventions. -->

## Constraints

<!-- Things the agent must not do without approval. -->
"""


def init_repo(url: str | None = None) -> None:
    """Initialise a repository for brr management."""
    if url:
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        print(f"[brr] cloning {url}")
        subprocess.run(["git", "clone", url, name], check=True)
        os.chdir(name)

    repo_root = gitops.ensure_git_repo()
    agents_file = repo_root / "AGENTS.md"

    if agents_file.exists():
        print(f"[brr] {agents_file} already exists")
        return

    detected = executor.detect_executor() or "auto"
    frontmatter = _FRONTMATTER.format(executor=detected)

    body = _SKELETON_BODY
    for name in _KNOWN_INSTRUCTION_FILES:
        path = repo_root / name
        if path.exists():
            body = path.read_text(encoding="utf-8")
            print(f"[brr] found {name}, using as AGENTS.md body")
            break

    agents_file.write_text(frontmatter + body, encoding="utf-8")
    print(f"[brr] wrote AGENTS.md (executor: {detected})")

    cfg = conf.load_config(repo_root)
    state_path = conf.state_file_path(repo_root, cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        state_path.write_text("# Agent State\n\n## Current Focus\n\nNot set.\n", encoding="utf-8")

    # Step 2: have the executor enrich AGENTS.md with repo-specific details.
    if detected != "auto":
        print("[brr] analyzing repo...")
        try:
            executor.run_task(_SETUP_INSTRUCTION)
            print("[brr] AGENTS.md populated")
        except RuntimeError as e:
            print(f"[brr] enrichment skipped: {e}")
    else:
        print("[brr] no executor found — edit AGENTS.md manually, or install one and re-run")
