"""Executor management — find and run AI tools via subprocess.

Built-in profiles are provided for ``claude``, ``codex``, and ``gemini``.
Any other executable on PATH can be used by setting ``default_executor``
in AGENTS.md.  For full command-template control, use ``executor_cmd``.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from . import config as conf

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

_active_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()


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
        if shutil.which(configured):
            return configured
        raise RuntimeError(
            f"Executor '{configured}' not found on PATH."
        )
    detected = detect_executor()
    if detected:
        return detected
    raise RuntimeError(
        "No AI executor found.  Install claude, codex, or gemini, "
        "or set default_executor in AGENTS.md."
    )


def _build_cmd(executor: str, prompt: str, cfg: dict[str, Any]) -> list[str]:
    """Build subprocess argv for a built-in or named executor."""
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


def run_executor(
    executor: str,
    prompt: str,
    cwd: Path | None = None,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Run an executor with the given prompt, return output text.

    The active subprocess is tracked so it can be killed via
    :func:`cancel_active` from another thread.
    """
    global _active_proc
    cfg = cfg or {}
    cmd = _build_cmd(executor, prompt, cfg)
    try:
        with _proc_lock:
            _active_proc = subprocess.Popen(
                cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        proc = _active_proc
        stdout, stderr = proc.communicate(timeout=600)
        returncode = proc.returncode
    except FileNotFoundError:
        raise RuntimeError(f"executable '{cmd[0]}' not found on PATH")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("executor timed out after 600s")
    finally:
        with _proc_lock:
            _active_proc = None

    if returncode != 0:
        detail = stderr.strip() or stdout.strip()
        if len(detail) > 500:
            detail = detail[:500] + " …[truncated]"
        raise RuntimeError(
            f"{cmd[0]} failed (exit {returncode}): "
            + (detail or "(no output)")
        )
    return stdout


def cancel_active() -> bool:
    """Kill the active executor subprocess. Returns True if one was running."""
    with _proc_lock:
        proc = _active_proc
    if proc is None or proc.poll() is not None:
        return False
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return True


class TaskRunner:
    """One-at-a-time task execution in a background thread.

    Any connector (Telegram, Discord, CLI watcher, etc.) can use this
    to submit tasks, cancel them, and poll for results without managing
    threads directly.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._instruction: str = ""
        self._cancelled: bool = False
        self._result: dict | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def instruction(self) -> str:
        return self._instruction

    def submit(self, instruction: str) -> bool:
        """Start a task. Returns False if already busy."""
        if self.busy:
            return False
        self._instruction = instruction
        self._result = None
        self._cancelled = False
        self._thread = threading.Thread(
            target=self._run, args=(instruction,), daemon=True,
        )
        self._thread.start()
        return True

    def cancel(self) -> bool:
        """Cancel the running task. Returns True if one was running."""
        if not self.busy:
            return False
        self._cancelled = True
        cancel_active()
        return True

    def poll_result(self) -> dict | None:
        """Non-blocking check for a completed task.

        Returns a dict with ``instruction``, ``cancelled``, and either
        ``output`` or ``error``.  Returns None if no result is ready.
        """
        if self._thread is None or self._thread.is_alive():
            return None
        result = {
            "instruction": self._instruction,
            "cancelled": self._cancelled,
            **(self._result or {}),
        }
        self._thread = None
        self._instruction = ""
        self._cancelled = False
        self._result = None
        return result

    def shutdown(self, timeout: float = 10) -> None:
        """Cancel any running task and wait for the thread to finish."""
        cancel_active()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self, instruction: str) -> None:
        try:
            output = run_task(instruction)
            self._result = {"output": output}
        except Exception as e:
            self._result = {"error": str(e)}


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
        prompt_parts.append(f"\n---\nCurrent state:\n{state_path.read_text(encoding='utf-8')}")

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
