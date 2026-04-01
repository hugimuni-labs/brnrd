"""Repository adoption — create AGENTS.md with universal conventions.

``brr init`` reads prompt template files and produces the AGENTS.md
instruction file that any AI tool can read.  It detects the executor,
incorporates existing instruction files (CLAUDE.md, GEMINI.md, etc.),
then optionally runs the executor to enrich AGENTS.md with actual
project details.

Almost all content lives in ``prompts/`` — this module is plumbing.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import config as conf
from . import executor
from . import gitops

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
_KNOWN_INSTRUCTION_FILES = ["CLAUDE.md", "GEMINI.md", "CODEX.md"]
_PLACEHOLDER_MARKER = "<!-- "


def _read_prompt(name: str, fallback: str = "") -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else fallback


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
        executor.run_task(_read_prompt("setup.md", "Fill in the AGENTS.md placeholders."))
        print("[brr] AGENTS.md populated")
    except RuntimeError as e:
        print(f"[brr] enrichment failed: {e}")
        print("[brr] re-run `brr init` to retry")


def init_repo(url: str | None = None, *, executor_name: str | None = None) -> None:
    """Initialise a repository for brr management."""
    if url:
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        print(f"[brr] cloning {url}")
        subprocess.run(["git", "clone", url, name], check=True)
        os.chdir(name)

    repo_root = gitops.ensure_git_repo()
    agents_file = repo_root / "AGENTS.md"
    detected = executor_name or executor.detect_executor() or "auto"

    if agents_file.exists():
        if _needs_enrichment(agents_file) and detected != "auto":
            _enrich(detected)
        else:
            print(f"[brr] {agents_file} already configured")
        return

    template = _read_prompt("agents-template.md")
    body = template.format(executor=detected) if template else ""

    for fname in _KNOWN_INSTRUCTION_FILES:
        path = repo_root / fname
        if path.exists():
            parts = body.split("---", 2)
            frontmatter = f"---{parts[1]}---\n\n" if len(parts) >= 3 else ""
            body = frontmatter + path.read_text(encoding="utf-8")
            print(f"[brr] found {fname}, using as AGENTS.md body")
            break

    agents_file.write_text(body, encoding="utf-8")
    print(f"[brr] wrote AGENTS.md (executor: {detected})")

    cfg = conf.load_config(repo_root)
    state_path = conf.state_file_path(repo_root, cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        state_path.write_text(
            _read_prompt("state-template.md", "# Agent State\n"),
            encoding="utf-8",
        )

    if detected != "auto":
        _enrich(detected)
    else:
        print("[brr] no executor found — edit AGENTS.md manually, or install one and re-run")
