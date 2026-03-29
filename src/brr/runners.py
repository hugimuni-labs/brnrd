"""Executor management — find and run AI tools via subprocess."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import config as conf

# CLI names to probe, in preference order.
_KNOWN_EXECUTORS = ["claude", "codex", "gemini"]


def detect_executor() -> str | None:
    """Return the first available executor CLI name, or None."""
    for name in _KNOWN_EXECUTORS:
        if shutil.which(name):
            return name
    return None


def resolve_executor(repo_root: Path) -> str:
    """Determine which executor to use for this repo.

    Reads default_executor from AGENTS.md.  'auto' triggers detection.
    Raises RuntimeError if nothing is found.
    """
    cfg = conf.load_config(repo_root)
    configured = cfg.get("default_executor", "auto")
    if configured != "auto":
        if shutil.which(configured):
            return configured
        raise RuntimeError(f"Configured executor '{configured}' not found on PATH.")
    detected = detect_executor()
    if detected:
        return detected
    raise RuntimeError(
        "No AI executor found.  Install claude, codex, or gemini, "
        "or set default_executor in AGENTS.md."
    )


def run_executor(executor: str, prompt: str, cwd: Path | None = None) -> str:
    """Run an executor CLI with the given prompt, return stdout."""
    result = subprocess.run(
        [executor, "--print", "-p", prompt],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{executor} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def run_task(instruction: str) -> str:
    """Run a user instruction via the configured executor."""
    from . import gitops
    repo_root = gitops.ensure_git_repo()
    executor = resolve_executor(repo_root)

    # Build prompt from run.md template + state
    prompt_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "run.md"
    prompt_parts = []
    if prompt_path.exists():
        prompt_parts.append(prompt_path.read_text(encoding="utf-8"))

    state_path = conf.state_file_path(repo_root)
    if state_path.exists():
        prompt_parts.append(f"\n---\nCurrent agent_state.md:\n{state_path.read_text(encoding='utf-8')}")

    prompt_parts.append(f"\n---\nTask: {instruction}")
    prompt = "\n".join(prompt_parts)

    print(f"[brr] running: {instruction}")
    print(f"[brr] executor: {executor}")
    output = run_executor(executor, prompt, cwd=repo_root)
    print(output)
    return output


def run_adopt_prompt(repo_root: Path) -> str | None:
    """Run the adoption analysis prompt. Returns executor output or None."""
    try:
        executor = resolve_executor(repo_root)
    except RuntimeError:
        return None

    prompt_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "init_adopt.md"
    if not prompt_path.exists():
        return None

    prompt = prompt_path.read_text(encoding="utf-8")
    try:
        return run_executor(executor, prompt, cwd=repo_root)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"[brr] adoption prompt failed: {e}")
        return None
