"""Repository adoption — create AGENTS.md with universal conventions.

``brr init`` produces the AGENTS.md instruction file that any AI tool
can read.  It detects the executor, incorporates existing instruction
files (CLAUDE.md, GEMINI.md, etc.), writes the YAML config and
skeleton body, then optionally runs the executor to enrich it with
actual project details.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import config as conf
from . import executor
from . import gitops

_KNOWN_INSTRUCTION_FILES = ["CLAUDE.md", "GEMINI.md", "CODEX.md"]

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_setup_prompt() -> str:
    path = _PROMPTS_DIR / "setup.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Read this repository and fill in the AGENTS.md placeholder sections."

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

**Branching.** Create a feature branch for code changes; commit with
a descriptive message when done.  For read-only tasks (review, verify,
research), report results without branching or committing.

**State file.** After each task, update `.brr.local/state.md`:
- Rewrite **Current Focus** to reflect where things stand.
- Add to **Conversation Topics** (one line: what was asked, what was
  done).  Keep the last ~10 entries; drop older ones.
- Update **Decisions**, **Discoveries**, **Next Steps**, **Open
  Questions** as needed.  Remove stale items — do not accumulate.

**Long output.** If your response would exceed a few hundred lines,
write it to a file or create a gist (`gh gist create`) and reference
the link.  The chat connector has a message size limit.

**Task types.** Adapt your approach to what is being asked:
- *Implement / fix* — branch, code, test, commit.
- *Review / verify / check* — read, analyse, report.  No branch.
- *Research / plan* — investigate, write findings to a file or gist.
- *Release / deploy* — follow the project's release process exactly.

## Guardrails

- **Dead ends.** Two failed attempts at the same approach → stop and
  report what you tried rather than retrying.
- **Scope drift.** If work expands beyond the original task, pause
  and note what you found.  Do not silently take on unbounded scope.
- **Proportionality.** Match effort to task size.  A one-line fix
  does not need a multi-file refactor.  A question does not need a
  prototype.
- **State tracking.** Always update the state file, even on failure
  or partial progress.  The next run depends on it.

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
        executor.run_task(_load_setup_prompt())
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
