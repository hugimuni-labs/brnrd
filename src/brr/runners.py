"""Executor management — find and run AI tools via subprocess.

Custom executors
~~~~~~~~~~~~~~~~
Drop a file in ``.brr.local/executors/<name>`` (repo-local, gitignored)
or ``~/.config/brr/executors/<name>`` (user-global).  Pip packages can
register via the ``brr.executors`` entry-point group.

Two formats are supported:

**Executable** (shell script, binary, anything ``chmod +x``)::

    .brr.local/executors/aider

    Receives the prompt on **stdin**, writes output to **stdout**.
    Exit 0 = success.  ``BRR_AUTO_APPROVE=1`` is set in the environment
    when auto-approve is enabled.

**Python module** (``.py`` with a ``run()`` function)::

    .brr.local/executors/aider.py

    def run(prompt: str, *, cwd: str, auto_approve: bool = False) -> str:
        ...
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import config as conf

RunFn = Callable[..., str]

# ── Built-in profiles ─────────────────────────────────────────────────
#   cmd     — base argv (prompt appended as last arg)
#   approve — extra flags when auto_approve is on

_EXECUTOR_PROFILES: dict[str, dict[str, Any]] = {
    "claude": {
        "cmd": ["claude", "-p"],
        "approve": ["--dangerously-skip-permissions"],
    },
    "codex": {
        "cmd": ["codex", "exec"],
        "approve": ["--dangerously-bypass-approvals-and-sandbox"],
    },
    "gemini": {
        "cmd": ["gemini"],
        "approve": [],
    },
}

# ── Custom executor discovery ─────────────────────────────────────────

_executor_cache: dict[str, RunFn | None] = {}


def _load_module_run(path: Path) -> RunFn | None:
    """Import a .py file and return its ``run`` function, or None."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    fn = getattr(mod, "run", None)
    return fn if callable(fn) else None


def _wrap_executable(path: Path) -> RunFn:
    """Wrap an executable file as a RunFn (stdin→stdout protocol)."""
    exe = str(path)

    def _run(prompt: str, *, cwd: str, auto_approve: bool = False) -> str:
        env = os.environ.copy()
        if auto_approve:
            env["BRR_AUTO_APPROVE"] = "1"
        try:
            result = subprocess.run(
                [exe], input=prompt, cwd=cwd,
                capture_output=True, text=True, env=env, timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"executor '{path.name}' timed out after 600s")
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            if len(detail) > 500:
                detail = detail[:500] + " …[truncated]"
            raise RuntimeError(
                f"{path.name} failed (exit {result.returncode}): "
                + (detail or "(no output)")
            )
        return result.stdout

    return _run


def _find_in_dir(name: str, directory: Path) -> RunFn | None:
    """Check a single directory for an executor named *name*."""
    # Executable file (no extension) — stdin/stdout protocol
    exe = directory / name
    if exe.is_file() and os.access(exe, os.X_OK):
        return _wrap_executable(exe)
    # Python module — run() protocol
    py = directory / f"{name}.py"
    if py.is_file():
        return _load_module_run(py)
    return None


def find_custom_executor(name: str, repo_root: Path) -> RunFn | None:
    """Locate a custom executor by *name*.

    Search order per directory (repo-local, then user-global):
      1. ``<name>``    — executable file (stdin/stdout)
      2. ``<name>.py`` — Python module with ``run()``

    Finally checks the ``brr.executors`` entry-point group.
    """
    cache_key = f"{repo_root}:{name}"
    if cache_key in _executor_cache:
        return _executor_cache[cache_key]

    fn: RunFn | None = None

    for directory in (
        repo_root / ".brr.local" / "executors",
        Path.home() / ".config" / "brr" / "executors",
    ):
        fn = _find_in_dir(name, directory)
        if fn:
            break

    if fn is None:
        try:
            for ep in importlib.metadata.entry_points(group="brr.executors"):
                if ep.name == name:
                    fn = ep.load()
                    break
        except Exception:
            pass

    _executor_cache[cache_key] = fn
    return fn


# ── Detection & resolution ────────────────────────────────────────────

def detect_executor() -> str | None:
    """Return the first available built-in executor CLI name, or None."""
    for name in _EXECUTOR_PROFILES:
        if shutil.which(name):
            return name
    return None


def resolve_executor(repo_root: Path) -> str:
    """Determine which executor to use for this repo.

    Reads ``default_executor`` from AGENTS.md.  ``auto`` triggers detection.
    Raises RuntimeError if nothing is found.
    """
    cfg = conf.load_config(repo_root)
    configured = cfg.get("default_executor", "auto")
    if configured != "auto":
        if find_custom_executor(configured, repo_root):
            return configured
        if shutil.which(configured):
            return configured
        raise RuntimeError(
            f"Executor '{configured}' not found on PATH or in executors/."
        )
    detected = detect_executor()
    if detected:
        return detected
    raise RuntimeError(
        "No AI executor found.  Install claude, codex, or gemini, "
        "or set default_executor in AGENTS.md."
    )


# ── Execution ─────────────────────────────────────────────────────────

def _build_cmd(executor: str, prompt: str, cfg: dict[str, Any]) -> list[str]:
    """Build subprocess argv for a built-in or template executor."""
    custom = cfg.get("executor_cmd")
    if custom:
        if isinstance(custom, list):
            return [s.replace("{prompt}", prompt) for s in custom]
        return [s.replace("{prompt}", prompt) for s in str(custom).split()]

    profile = _EXECUTOR_PROFILES.get(executor)
    if profile:
        cmd = list(profile["cmd"])
        if cfg.get("auto_approve") and profile.get("approve"):
            cmd.extend(profile["approve"])
        cmd.append(prompt)
        return cmd

    return [executor, prompt]


def _subprocess_run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a command, return stdout, raise RuntimeError on failure."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        raise RuntimeError(f"executable '{cmd[0]}' not found on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"executor timed out after 600s")

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if len(detail) > 500:
            detail = detail[:500] + " …[truncated]"
        if not detail:
            detail = "(no output captured)"
        raise RuntimeError(
            f"{cmd[0]} failed (exit {result.returncode}): {detail}"
        )
    return result.stdout


def run_executor(
    executor: str,
    prompt: str,
    cwd: Path | None = None,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Run an executor with the given prompt, return output text.

    Tries a custom executor first (drop-in .py), then falls back to
    built-in profiles or a raw subprocess call.
    """
    cfg = cfg or {}
    repo_root = cwd or Path.cwd()

    custom_run = find_custom_executor(executor, repo_root)
    if custom_run:
        return custom_run(
            prompt,
            cwd=str(repo_root),
            auto_approve=bool(cfg.get("auto_approve")),
        )

    cmd = _build_cmd(executor, prompt, cfg)
    return _subprocess_run(cmd, cwd=cwd)


def run_task(instruction: str) -> str:
    """Run a user instruction via the configured executor."""
    from . import gitops
    repo_root = gitops.ensure_git_repo()
    cfg = conf.load_config(repo_root)
    executor = resolve_executor(repo_root)

    prompt_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "run.md"
    prompt_parts = []
    if prompt_path.exists():
        prompt_parts.append(prompt_path.read_text(encoding="utf-8"))

    state_path = conf.state_file_path(repo_root, cfg)
    if state_path.exists():
        prompt_parts.append(f"\n---\nCurrent agent_state.md:\n{state_path.read_text(encoding='utf-8')}")

    prompt_parts.append(f"\n---\nTask: {instruction}")
    prompt = "\n".join(prompt_parts)

    print(f"[brr] running: {instruction}")
    print(f"[brr] executor: {executor}")
    output = run_executor(executor, prompt, cwd=repo_root, cfg=cfg)
    print(output)
    return output


def run_adopt_prompt(repo_root: Path) -> str | None:
    """Run the adoption analysis prompt. Returns executor output or None."""
    try:
        cfg = conf.load_config(repo_root)
        executor = resolve_executor(repo_root)
    except RuntimeError:
        return None

    prompt_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "init_adopt.md"
    if not prompt_path.exists():
        return None

    prompt = prompt_path.read_text(encoding="utf-8")
    try:
        return run_executor(executor, prompt, cwd=repo_root, cfg=cfg)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"[brr] adoption prompt failed: {e}")
        return None
