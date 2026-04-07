"""Runner — shell out to AI CLIs, one task at a time.

brr doesn't do AI work itself.  It delegates to whatever runner CLI
the user has installed (claude, codex, gemini, or any command on PATH).
Profiles are defined in ``prompts/runners.md`` — this module is
plumbing: detection, subprocess management, and the ``TaskRunner``
class for serial task execution in a background thread.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_profiles_cache: dict[str, dict[str, Any]] | None = None

_active_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()


def _read_prompt(name: str, repo_root: Path | None = None) -> str:
    """Read a prompt file, checking user overrides first."""
    if repo_root:
        override = repo_root / ".brr" / "prompts" / name
        if override.exists():
            return override.read_text(encoding="utf-8")
    bundled = _PROMPTS_DIR / name
    if bundled.exists():
        return bundled.read_text(encoding="utf-8")
    return ""


def _load_profiles(repo_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load runner profiles from prompts/runners.md."""
    global _profiles_cache
    if _profiles_cache is not None:
        return _profiles_cache
    from . import protocol
    text = _read_prompt("runners.md", repo_root)
    if text:
        _profiles_cache = protocol.parse_frontmatter(text)
    else:
        _profiles_cache = {}
    return _profiles_cache


def detect_runner(repo_root: Path | None = None) -> str | None:
    """Return the first available built-in runner CLI name, or None."""
    for name in _load_profiles(repo_root):
        if shutil.which(name):
            return name
    return None


def resolve_runner(repo_root: Path) -> str:
    """Determine which runner to use for this repo.

    Reads ``runner`` from ``.brr/config``.  ``auto`` triggers detection.
    Raises RuntimeError if nothing is found.
    """
    from . import config as conf
    cfg = conf.load_config(repo_root)
    configured = cfg.get("runner", "auto")
    if configured != "auto":
        if shutil.which(configured):
            return configured
        raise RuntimeError(f"Runner '{configured}' not found on PATH.")
    detected = detect_runner(repo_root)
    if detected:
        return detected
    raise RuntimeError(
        "No AI runner found.  Install claude, codex, or gemini, "
        "or set runner= in .brr/config."
    )


def _build_cmd(runner_name: str, prompt: str, cfg: dict[str, Any]) -> list[str]:
    """Build subprocess argv for a built-in or named runner."""
    custom = cfg.get("runner_cmd")
    if custom:
        if isinstance(custom, list):
            return [s.replace("{prompt}", prompt) for s in custom]
        return [s.replace("{prompt}", prompt) for s in str(custom).split()]

    profiles = _load_profiles()
    profile = profiles.get(runner_name)
    if profile:
        cmd = str(profile.get("cmd", runner_name)).split()
        approve = str(profile.get("approve", "")).strip()
        if cfg.get("auto_approve") and approve:
            cmd.extend(approve.split())
        cmd.append(prompt)
        return cmd

    return [runner_name, prompt]


def run_executor(
    runner_name: str,
    prompt: str,
    cwd: Path | None = None,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Run a runner subprocess with the given prompt, return stdout."""
    global _active_proc
    cfg = cfg or {}
    cmd = _build_cmd(runner_name, prompt, cfg)
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
        raise RuntimeError("runner timed out after 600s")
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


# ── Prompt construction ──────────────────────────────────────────────


def build_init_prompt(repo_root: Path) -> str:
    """Build the prompt for ``brr init`` — setup.md + agents-template.md."""
    setup = _read_prompt("setup.md", repo_root)
    template = _read_prompt("agents-template.md", repo_root)
    return f"{setup}\n\n{template}"


def build_run_prompt(task: str, repo_root: Path) -> str:
    """Build the prompt for ``brr run`` — run.md + task text."""
    preamble = _read_prompt("run.md", repo_root)
    return f"{preamble}\n\n---\nTask: {task}"


def build_daemon_prompt(
    task: str,
    event_id: str,
    response_path: str,
    repo_root: Path,
) -> str:
    """Build the prompt for daemon-originated tasks.

    Same as run prompt but with event metadata prepended to the task.
    """
    preamble = _read_prompt("run.md", repo_root)
    metadata = (
        f"Event: {event_id}\n"
        f"Write your final response to: {response_path}\n"
        f"Do not explore or modify any other files in .brr/.\n"
    )
    return f"{preamble}\n\n---\n{metadata}\nTask: {task}"


# ── Task execution ───────────────────────────────────────────────────


def run_task(instruction: str) -> str:
    """Run a user instruction via the configured runner (for ``brr run``)."""
    from . import gitops
    repo_root = gitops.ensure_git_repo()
    from . import config as conf
    cfg = conf.load_config(repo_root)
    runner_name = resolve_runner(repo_root)

    prompt = build_run_prompt(instruction, repo_root)

    print(f"[brr] running: {instruction}")
    print(f"[brr] runner: {runner_name}")
    output = run_executor(runner_name, prompt, cwd=repo_root, cfg=cfg)
    print(output)
    return output


class TaskRunner:
    """One-at-a-time task execution in a background thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._instruction: str = ""
        self._result: dict | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def finished(self) -> bool:
        return self._thread is not None and not self._thread.is_alive()

    @property
    def instruction(self) -> str:
        return self._instruction

    def submit(self, instruction: str) -> bool:
        """Start a task. Returns False if already busy."""
        if self.busy:
            return False
        self._instruction = instruction
        self._result = None
        self._thread = threading.Thread(
            target=self._run, args=(instruction,), daemon=True,
        )
        self._thread.start()
        return True

    def poll_result(self) -> dict | None:
        """Non-blocking check for a completed task."""
        if not self.finished:
            return None
        result = {
            "instruction": self._instruction,
            **(self._result or {}),
        }
        self._thread = None
        self._instruction = ""
        self._result = None
        return result

    def shutdown(self, timeout: float = 10) -> None:
        """Wait for the current task to finish."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self, instruction: str) -> None:
        try:
            output = run_task(instruction)
            self._result = {"output": output}
        except Exception as e:
            self._result = {"error": str(e)}
