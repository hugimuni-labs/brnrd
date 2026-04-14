"""Runner — shell out to AI CLIs, one task at a time.

brr doesn't do AI work itself.  It delegates to whatever runner CLI
the user has installed (claude, codex, gemini, or any command on PATH).
Profiles are defined in ``prompts/runners.md`` — this module is
plumbing: detection, subprocess management, and the ``TaskRunner``
class for serial task execution in a background thread.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
import random
import string
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_profiles_cache: dict[str, dict[str, Any]] | None = None

_active_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()


def _trace_id() -> str:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}-{rand}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return slug or "invocation"


@dataclass(frozen=True)
class RunnerArtifactSpec:
    """A file the runner invocation is expected to produce."""

    path: Path
    label: str | None = None
    copy_to_trace: bool = True


@dataclass(frozen=True)
class RunnerArtifactRecord:
    """Observed status for one expected runner artifact."""

    path: Path
    label: str
    exists: bool
    trace_copy: Path | None = None


@dataclass(frozen=True)
class RunnerInvocation:
    """Single runner invocation plus its validation contract."""

    kind: str
    label: str
    prompt: str
    repo_root: Path
    cwd: Path | None = None
    response_path: str | None = None
    required_artifacts: list[RunnerArtifactSpec] = field(default_factory=list)

    @property
    def trace_root(self) -> Path:
        return self.repo_root / ".brr" / "traces" / _slugify(self.kind)


@dataclass
class RunnerResult:
    """Runner subprocess result plus trace and artifact validation."""

    invocation: RunnerInvocation
    runner_name: str
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    trace_dir: Path | None
    artifacts: list[RunnerArtifactRecord]

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout

    @property
    def missing_artifacts(self) -> list[RunnerArtifactRecord]:
        return [artifact for artifact in self.artifacts if not artifact.exists]

    @property
    def validation_ok(self) -> bool:
        return not self.missing_artifacts

    def retry_reason(self) -> str | None:
        if not self.missing_artifacts:
            return None
        labels = ", ".join(artifact.label for artifact in self.missing_artifacts)
        return f"missing required output(s): {labels}"

    def raise_for_error(self) -> None:
        if self.ok:
            return
        detail = self.stderr.strip() or self.stdout.strip()
        if len(detail) > 500:
            detail = detail[:500] + " …[truncated]"
        raise RuntimeError(
            f"{self.command[0]} failed (exit {self.returncode}): "
            + (detail or "(no output)")
        )


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


def _build_cmd(
    runner_name: str,
    prompt: str,
    cfg: dict[str, Any],
    response_path: str | None = None,
) -> list[str]:
    """Build subprocess argv for a built-in or named runner."""
    def _replace_placeholders(parts: list[str]) -> list[str]:
        replaced = [s.replace("{prompt}", prompt) for s in parts]
        if response_path is not None:
            replaced = [s.replace("{response_path}", response_path) for s in replaced]
        return replaced

    custom = cfg.get("runner_cmd")
    if custom:
        if isinstance(custom, list):
            return _replace_placeholders(custom)
        return _replace_placeholders(str(custom).split())

    profiles = _load_profiles()
    profile = profiles.get(runner_name)
    if profile:
        cmd = str(profile.get("cmd", runner_name)).split()
        approve = str(profile.get("approve", "")).strip()
        if runner_name == "codex" and cfg.get("auto_approve"):
            cmd = [part for part in cmd if part != "--full-auto"]
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        if cfg.get("auto_approve") and approve:
            cmd.extend(approve.split())
        if runner_name == "codex" and response_path:
            cmd.extend(["--output-last-message", response_path])
        cmd.append(prompt)
        return cmd

    return [runner_name, prompt]


def _copy_artifact_to_trace(
    trace_dir: Path,
    artifact: RunnerArtifactSpec,
    index: int,
) -> Path | None:
    if not artifact.copy_to_trace or not artifact.path.exists():
        return None
    artifacts_dir = trace_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    target = artifacts_dir / f"{index:02d}-{artifact.path.name}"
    shutil.copy2(artifact.path, target)
    return target


def _write_trace(result: RunnerResult) -> Path | None:
    trace_root = result.invocation.trace_root
    try:
        trace_dir = trace_root / f"{_slugify(result.invocation.label)}-{_trace_id()}"
        trace_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    (trace_dir / "prompt.md").write_text(result.invocation.prompt, encoding="utf-8")
    (trace_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (trace_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")

    records = []
    copied_artifacts = []
    for index, artifact in enumerate(result.invocation.required_artifacts, start=1):
        trace_copy = _copy_artifact_to_trace(trace_dir, artifact, index)
        copied_artifacts.append(
            RunnerArtifactRecord(
                path=artifact.path,
                label=artifact.label or str(artifact.path),
                exists=artifact.path.exists(),
                trace_copy=trace_copy,
            )
        )

    result.artifacts = copied_artifacts
    metadata = {
        "runner": result.runner_name,
        "kind": result.invocation.kind,
        "label": result.invocation.label,
        "cwd": str(result.invocation.cwd or ""),
        "command": result.command,
        "returncode": result.returncode,
        "response_path": result.invocation.response_path,
        "validation_ok": result.validation_ok,
        "retry_reason": result.retry_reason(),
        "artifacts": [
            {
                "label": artifact.label,
                "path": str(artifact.path),
                "exists": artifact.exists,
                "trace_copy": str(artifact.trace_copy) if artifact.trace_copy else None,
            }
            for artifact in result.artifacts
        ],
    }
    (trace_dir / "meta.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return trace_dir


def invoke_runner(
    runner_name: str,
    invocation: RunnerInvocation,
    cfg: dict[str, Any] | None = None,
    *,
    trace: bool = False,
) -> RunnerResult:
    """Run a runner subprocess, validate outputs, and optionally persist a trace."""
    global _active_proc
    cfg = cfg or {}
    cmd = _build_cmd(
        runner_name,
        invocation.prompt,
        cfg,
        response_path=invocation.response_path,
    )
    stdout = ""
    stderr = ""
    returncode = 0
    try:
        with _proc_lock:
            _active_proc = subprocess.Popen(
                cmd,
                cwd=invocation.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        proc = _active_proc
        stdout, stderr = proc.communicate(timeout=600)
        returncode = proc.returncode
    except FileNotFoundError:
        stderr = f"executable '{cmd[0]}' not found on PATH"
        returncode = 127
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        stderr = (stderr + "\n" if stderr else "") + "runner timed out after 600s"
        returncode = 124
    finally:
        with _proc_lock:
            _active_proc = None

    result = RunnerResult(
        invocation=invocation,
        runner_name=runner_name,
        command=cmd,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        trace_dir=None,
        artifacts=[],
    )
    if trace:
        result.trace_dir = _write_trace(result)
    else:
        result.artifacts = [
            RunnerArtifactRecord(
                path=spec.path,
                label=spec.label or str(spec.path),
                exists=spec.path.exists(),
            )
            for spec in invocation.required_artifacts
        ]
    return result


def run_executor(
    runner_name: str,
    prompt: str,
    cwd: Path | None = None,
    cfg: dict[str, Any] | None = None,
    response_path: str | None = None,
) -> str:
    """Run a runner subprocess with the given prompt, return stdout."""
    if cwd is None:
        raise RuntimeError("run_executor requires cwd to infer repo_root for tracing")
    invocation = RunnerInvocation(
        kind="executor",
        label=runner_name,
        prompt=prompt,
        cwd=cwd,
        repo_root=cwd,
        response_path=response_path,
    )
    result = invoke_runner(runner_name, invocation, cfg=cfg)
    result.raise_for_error()
    return result.stdout


# ── Context injection ────────────────────────────────────────────────

_LOG_ENTRY_RE = re.compile(r"^## \[", re.MULTILINE)
_MAX_LOG_ENTRIES = 10


def _read_recent_log(repo_root: Path, max_entries: int = _MAX_LOG_ENTRIES) -> str:
    """Read the most recent entries from kb/log.md.

    Returns the raw markdown of the last *max_entries* entries, or
    empty string if the log doesn't exist or is empty.  This gives
    the agent conversation context from previous sessions without
    unbounded growth in the prompt.
    """
    log_path = repo_root / "kb" / "log.md"
    if not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8")
    # Split on entry headers (## [YYYY-MM-DD] ...)
    parts = _LOG_ENTRY_RE.split(text)
    if len(parts) <= 1:
        return ""
    # parts[0] is the preamble, rest are entries (without the "## [" prefix)
    entries = [f"## [{p}" for p in parts[1:]]
    recent = entries[-max_entries:]
    return "\n".join(recent).strip()


def _build_context_block(repo_root: Path) -> str:
    """Build the conversation context block for prompt injection.

    Includes recent log entries so the agent has continuity with
    previous sessions.  The log is maintained by agents (per AGENTS.md)
    so it stays proportional.
    """
    recent = _read_recent_log(repo_root)
    if not recent:
        return ""
    return (
        "## Recent Activity (from kb/log.md)\n\n"
        "This is your conversation context — what happened in previous sessions:\n\n"
        f"{recent}"
    )


def _join_prompt_parts(
    preamble: str,
    repo_root: Path,
    trailer: str,
) -> str:
    """Join a prompt preamble, optional recent context, and task-specific text."""
    parts = [preamble]
    context = _build_context_block(repo_root)
    if context:
        parts.append(context)
    parts.append(trailer)
    return "\n\n".join(parts)


# ── Prompt construction ──────────────────────────────────────────────


def build_init_prompt(repo_root: Path) -> str:
    """Build the prompt for ``brr init`` — setup.md + agents-template.md."""
    setup = _read_prompt("setup.md", repo_root)
    template = _read_prompt("agents-template.md", repo_root)
    return f"{setup}\n\n{template}"


def build_run_prompt(task: str, repo_root: Path) -> str:
    """Build the prompt for ``brr run`` — run.md + task text."""
    preamble = _read_prompt("run.md", repo_root)
    return _join_prompt_parts(preamble, repo_root, f"---\nTask: {task}")


def build_daemon_prompt(
    task: str,
    event_id: str,
    response_path: str,
    repo_root: Path,
    *,
    task_id: str | None = None,
    branch_name: str | None = None,
    runtime_dir: str | None = None,
    log_file: str | None = None,
) -> str:
    """Build the prompt for daemon-originated tasks.

    Same as run prompt but with event metadata and conversation context.
    When *log_file* is set (e.g. for worktree mode), the agent is told
    to write its log entry there instead of kb/log.md.
    """
    preamble = _read_prompt("run.md", repo_root)
    metadata = (
        f"Event: {event_id}\n"
        + (f"Task ID: {task_id}\n" if task_id else "")
        + f"Execution root: {repo_root}\n"
        + (f"Current branch: {branch_name}\n" if branch_name else "")
        + (f"Shared runtime dir: {runtime_dir}\n" if runtime_dir else "")
        + f"Your final response must be the exact content to place in: {response_path}\n"
        + "Some runners capture your final response automatically; if not, write that exact content there yourself.\n"
        + "Do not explore or modify any other files in .brr/ beyond what this task explicitly asks for.\n"
    )
    if log_file:
        metadata += f"\nWrite your log entry to {log_file} instead of kb/log.md.\n"
    return _join_prompt_parts(preamble, repo_root, f"---\n{metadata}\nTask: {task}")


def build_triage_prompt(event_body: str, event_id: str, repo_root: Path) -> str:
    """Build the prompt for the triage step — event → task conversion.

    The triage agent reads the event and decides branch strategy and
    execution environment.  Its output is parsed into a Task.
    """
    triage = _read_prompt("triage.md", repo_root)
    return _join_prompt_parts(triage, repo_root, f"---\nEvent ID: {event_id}\n\n{event_body}")


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
    result = invoke_runner(
        runner_name,
        RunnerInvocation(
            kind="run",
            label=instruction[:40],
            prompt=prompt,
            cwd=repo_root,
            repo_root=repo_root,
        ),
        cfg=cfg,
    )
    result.raise_for_error()
    output = result.output
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
