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
    "Replace every HTML comment placeholder (<!-- ... -->) with real content.\n\n"
    "For each section:\n"
    "- **# Project** — one paragraph: what this is, what stack, what it does.\n"
    "- **## Build and run** — exact commands from Makefile, package.json, "
    "pyproject.toml, CI configs, etc. Use code blocks.\n"
    "- **## Code guidelines** — language version, formatting, test framework, "
    "commit style. Be specific to this repo.\n"
    "- **## Workflow** — leave the defaults unless the repo has an established "
    "branching or review convention you can identify.\n"
    "- **## Guardrails** — leave the defaults; they apply universally.\n"
    "- **## Constraints** — sensitive dirs, deployment commands, public API "
    "surfaces — things an agent should not change without asking.\n\n"
    "Also fill in the YAML `commands:` block (build, test, verify) with "
    "the actual commands you found.\n\n"
    "Keep the whole body under a page. Every word is read by an AI executor "
    "on every task. Do not modify the rest of the YAML frontmatter."
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

<!-- Exact commands to install, build, test, and run. -->

## Code guidelines

<!-- Language version, formatting, test framework, commit conventions. -->

## Workflow

- For code changes, create a feature branch and commit when done.
- For read-only tasks (review, research, verify), report results
  without branching.
- Update the state file after each task: rewrite Current Focus,
  add to Conversation Topics, update Decisions/Next Steps as needed.
  Remove stale items rather than accumulating.

## Guardrails

- **Dead ends.** If you have attempted the same approach twice
  without progress, stop and report what you tried.
- **Scope drift.** If work is expanding beyond the original task,
  pause and note what you found. Do not silently take on unbounded scope.
- **Proportionality.** Match effort to task size. A one-line fix does
  not need a multi-file refactor.

When in doubt, write down what you know and what you are unsure about,
and let the user decide the next move.

## Constraints

<!-- Things the agent must not do without approval. -->
"""

_PLACEHOLDER_MARKER = "<!-- "


def _needs_enrichment(agents_file: Path) -> bool:
    """True if AGENTS.md still has HTML comment placeholders."""
    text = agents_file.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    body = parts[2] if len(parts) >= 3 else text
    return _PLACEHOLDER_MARKER in body


def _enrich(detected: str) -> None:
    """Run the executor to fill in AGENTS.md."""
    print("[brr] analyzing repo...")
    try:
        executor.run_task(_SETUP_INSTRUCTION)
        print("[brr] AGENTS.md populated")
    except RuntimeError as e:
        print(f"[brr] enrichment failed: {e}")
        print("[brr] re-run `brr init` to retry")


def init_repo(url: str | None = None) -> None:
    """Initialise a repository for brr management."""
    if url:
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        print(f"[brr] cloning {url}")
        subprocess.run(["git", "clone", url, name], check=True)
        os.chdir(name)

    repo_root = gitops.ensure_git_repo()
    agents_file = repo_root / "AGENTS.md"
    detected = executor.detect_executor() or "auto"

    if agents_file.exists():
        if _needs_enrichment(agents_file) and detected != "auto":
            _enrich(detected)
        else:
            print(f"[brr] {agents_file} already configured")
        return

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

    if detected != "auto":
        _enrich(detected)
    else:
        print("[brr] no executor found — edit AGENTS.md manually, or install one and re-run")
