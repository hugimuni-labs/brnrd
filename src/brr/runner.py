"""Runner — shell out to AI CLIs, one run at a time.

brr doesn't do AI work itself. It delegates to whatever runner CLI the
user has installed (claude, codex, gemini, or any command on PATH).
Profiles are project-owned data (``.brr/runners.md``), with bundled
defaults kept for first-run convenience. Prompt assembly lives in
:mod:`brr.prompts`. This module is the plumbing: runner detection,
``RunnerInvocation`` and ``RunnerResult`` types, subprocess execution,
trace persistence, and the ``TaskRunner`` class for serial execution in
a background thread.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import threading
import time
import random
import string
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_profiles_cache: dict[str, dict[str, Any]] | None = None
_profiles_cache_key: str | None = None

_active_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()


# Environment variables an agent CLI sets for *its own* session that must not
# leak into a runner subprocess brr spawns. The killer is
# ``CLAUDE_CODE_SAFE_MODE``: when a Claude session spawns a child ``claude``
# (e.g. the daemon was launched from inside Claude Code, or the runner itself
# probes the CLI), the child inherits safe mode, which *silently drops
# settings-file hooks* while logging a reassuring "managed settings-file hooks
# still run". That single inherited var is what made earlier firing tests
# conclude "Claude hooks don't fire under --print" — a false negative from a
# contaminated env (verified 2026-06-27; see kb/design-runner-back-channel.md).
# The session-identity vars are stripped too so a spawned runner starts as a
# fresh top-level session rather than a confused nested child.
_RUNNER_ENV_CONTAMINANTS: frozenset[str] = frozenset(
    {
        "CLAUDE_CODE_SAFE_MODE",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
        "CLAUDE_EFFORT",
        "AI_AGENT",
    }
)


def clean_runner_environ() -> dict[str, str]:
    """A copy of ``os.environ`` with parent-agent-session leakage removed.

    The base env every runner subprocess starts from. Stripping the
    contaminants above keeps a spawned agent CLI from inheriting the *parent*
    agent's session identity and, critically, its safe-mode flag — so hooks,
    skills, and plugins behave as they would for a normal top-level run.
    """
    return {
        k: v for k, v in os.environ.items() if k not in _RUNNER_ENV_CONTAMINANTS
    }


def kill_active() -> bool:
    """Terminate the in-flight runner subprocess, if one is running.

    Returns ``True`` when a live process was signalled. Safe to call from
    any thread — it reads the handle under ``_proc_lock`` and kills outside
    it. The daemon's heartbeat uses this to enforce an extensible budget,
    and shutdown uses it to reclaim the single-flight slot promptly instead
    of waiting out the wall-clock backstop. No-op when nothing is running.
    """
    with _proc_lock:
        proc = _active_proc
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.kill()
    except OSError:
        return False
    return True


DEFAULT_RUNNER_TIMEOUT = 3600


def runner_timeout(cfg: dict[str, Any] | None) -> int:
    """Return the runner subprocess timeout in seconds.

    Reads ``runner.timeout_seconds`` (or legacy ``runner_timeout_seconds``)
    from *cfg*; falls back to :data:`DEFAULT_RUNNER_TIMEOUT`. xhigh-reasoning
    models like gpt-5.5 routinely need 10+ minutes on a complex task, and the
    old 600s default was killing live work mid-run; 3600s is a soft ceiling
    rather than a target.
    """
    if not cfg:
        return DEFAULT_RUNNER_TIMEOUT
    raw = cfg.get("runner.timeout_seconds", cfg.get("runner_timeout_seconds"))
    if raw is None:
        return DEFAULT_RUNNER_TIMEOUT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RUNNER_TIMEOUT
    return value if value > 0 else DEFAULT_RUNNER_TIMEOUT


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
    # Wall-clock backstop for ``proc.communicate``. ``None`` falls back to
    # ``runner_timeout(cfg)``. The daemon passes a generous hard cap here
    # and enforces the real (extensible) budget from its heartbeat — see
    # ``daemon._invoke_with_heartbeat`` and ``kill_active``.
    timeout_seconds: int | None = None
    # Extra environment variables for the runner subprocess. Daemon runs
    # use this to expose live portal paths (BRR_PORTAL_STATE,
    # BRR_OUTBOX_DIR, etc.) without making the resident copy them out of
    # prose.
    env: dict[str, str] = field(default_factory=dict)

    @property
    def trace_root(self) -> Path:
        from . import gitops

        return gitops.shared_brr_dir(self.repo_root) / "traces" / _slugify(self.kind)


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
    def has_response(self) -> bool:
        """True iff the runner emitted a non-empty final reply on stdout.

        Only meaningful for invocations that ask brr to capture a
        response file (``invocation.response_path is not None``).
        """
        return bool(self.stdout and self.stdout.strip())

    @property
    def validation_ok(self) -> bool:
        if not self.ok:
            return False
        if self.missing_artifacts:
            return False
        if self.invocation.response_path and not self.has_response:
            return False
        return True

    def retry_reason(self) -> str | None:
        """Return a retryable reason, or None.

        Only clean exits are retryable: when the runner subprocess exits 0
        but didn't produce the artifacts we expected, the next attempt may
        succeed (a stochastic "ran past the deliverable" case). Hard
        failures — non-zero exit, timeout — are not retryable here; the
        daemon's give-up path surfaces them with the captured error
        instead of paying for a duplicate expensive attempt.
        """
        if not self.ok:
            return None
        if self.missing_artifacts:
            labels = ", ".join(artifact.label for artifact in self.missing_artifacts)
            return f"missing required output(s): {labels}"
        if self.invocation.response_path and not self.has_response:
            return "runner produced no response on stdout"
        return None

    def error_detail(self, *, limit: int = 500) -> str | None:
        """Truncated error text suitable for gate display, or None.

        Used by the daemon to bubble the runner's actual failure
        (stderr/stdout tail) up into the failed update packet so chat
        gates can show the operator something more useful than
        ``stage=run``.
        """
        if self.ok and self.validation_ok:
            return None
        detail = (self.stderr or self.stdout or "").strip()
        if not detail:
            return None
        if len(detail) <= limit:
            return detail
        tail = detail[-limit:]
        return f"…[truncated]{tail}" if len(detail) > limit else tail

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


def _profiles_source(repo_root: Path | None = None) -> tuple[str, str]:
    """Return ``(cache_key, frontmatter)``, preferring project-owned data."""
    from . import prompts

    if repo_root:
        from . import gitops

        try:
            brr_dir = gitops.shared_brr_dir(repo_root)
        except Exception:  # noqa: BLE001 - non-repo invocations use bundled defaults
            brr_dir = repo_root / ".brr"
        project_profiles = brr_dir / "runners.md"
        if project_profiles.exists():
            return (
                str(project_profiles.resolve()),
                project_profiles.read_text(encoding="utf-8"),
            )
        legacy_prompt_profiles = brr_dir / "prompts" / "runners.md"
        if legacy_prompt_profiles.exists():
            return (
                str(legacy_prompt_profiles.resolve()),
                legacy_prompt_profiles.read_text(encoding="utf-8"),
            )
    return ("bundled:runners.md", prompts.read_prompt("runners.md", None))


def _load_profiles(repo_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load runner profiles from project data or bundled defaults."""
    global _profiles_cache, _profiles_cache_key
    key, text = _profiles_source(repo_root)
    if _profiles_cache is not None and (
        _profiles_cache_key is None or _profiles_cache_key == key
    ):
        return _profiles_cache
    from . import protocol

    if text:
        _profiles_cache = protocol.parse_frontmatter(text)
    else:
        _profiles_cache = {}
    _profiles_cache_key = key
    return _profiles_cache


def _profile_binary(name: str, profiles: dict[str, dict[str, Any]]) -> str:
    profile = profiles.get(name) or {}
    return str(profile.get("binary") or name)


def profile_hooks_flavour(
    name: str, repo_root: Path | None = None
) -> str | None:
    """Return the runner's declared hook *flavour*, or None.

    Tier 2 of the runner interface (``kb/design-runner-back-channel.md``):
    a profile opts into the hooks back channel with a ``hooks: <flavour>``
    field naming the runner family (``claude`` / ``codex`` / ``gemini``)
    whose native hook config brr generates. This reads the *declared*
    intent from the profile; whether the runner is actually hooks-capable
    is a separate runtime precheck (settings location writable, native
    config present), so a caller wires hooks only after confirming the
    flavour here *and* passing that precheck.
    """
    profile = _load_profiles(repo_root).get(name) or {}
    flavour = profile.get("hooks")
    if not flavour:
        return None
    flavour = str(flavour).strip().lower()
    return flavour or None


def _runner_available(name: str, profiles: dict[str, dict[str, Any]]) -> bool:
    return shutil.which(_profile_binary(name, profiles)) is not None


def detect_runner(repo_root: Path | None = None) -> str | None:
    """Return the first available built-in runner CLI name, or None."""
    profiles = _load_profiles(repo_root)
    for name in profiles:
        if profiles.get(name, {}).get("binary"):
            continue
        if _runner_available(name, profiles):
            return name
    return None


def detect_all_runners(repo_root: Path | None = None) -> list[str]:
    """Return all available runner CLI names found on PATH."""
    profiles = _load_profiles(repo_root)
    return [name for name in profiles if _runner_available(name, profiles)]


def resolve_runner(repo_root: Path) -> str:
    """Determine which runner to use for this repo.

    Reads ``runner`` from ``.brr/config``.  ``auto`` triggers detection.
    Raises RuntimeError if nothing is found.
    """
    from . import config as conf
    cfg = conf.load_config(repo_root)
    profiles = _load_profiles(repo_root)
    configured = cfg.get("runner", "auto")
    if configured != "auto":
        if _runner_available(configured, profiles):
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
    repo_root: Path | None = None,
) -> list[str]:
    """Build subprocess argv for a built-in or named runner.

    Each runner is invoked headless with approvals bypassed (see the
    bundled/default runner profiles) and prints its final reply on stdout. brr
    captures stdout and writes it to the invocation's response file —
    runners do not need to be told where the response file lives.
    """
    def _replace_placeholders(parts: list[str]) -> list[str]:
        return [s.replace("{prompt}", prompt) for s in parts]

    custom = cfg.get("runner_cmd")
    if custom:
        if isinstance(custom, list):
            return _replace_placeholders(custom)
        return _replace_placeholders(shlex.split(str(custom)))

    profile = _load_profiles(repo_root).get(runner_name)
    if profile:
        cmd = shlex.split(str(profile.get("cmd", runner_name)))
        cmd.append(prompt)
        return cmd

    return [runner_name, prompt]


def _write_response_file(response_path: str, stdout: str) -> None:
    """Persist the runner's stdout as the captured response file.

    The path is created relative to the host file system; brr always
    runs inside (or with a bind mount of) the repo, so the parent
    directory normally exists, but we mkdir defensively so a fresh
    ``responses/`` subtree can be created on the first run.
    """
    target = Path(response_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stdout, encoding="utf-8")


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
    """Run a runner subprocess, validate outputs, and optionally persist a trace.

    If the invocation specifies a ``response_path``, captured stdout is
    written there on success. Runners are expected to print only their
    final reply to stdout (progress streams to stderr); brr has no need
    to know per-runner output flags.
    """
    global _active_proc
    cfg = cfg or {}

    # Tier-2 streaming opt-in: a profile that declares ``stream: <flavour>``
    # runs the persistent stream-json driver (brr drives the loop and weaves
    # portal deltas / folded-in events back in at tool boundaries) instead of
    # the blocking ``--print`` Popen below. Gated to the profile path — a
    # ``runner_cmd`` override means the user pinned an exact command, so honour
    # it on the plain path rather than rewriting it for streaming. The driver
    # registers ``_active_proc`` under ``_proc_lock`` itself, so the daemon's
    # budget/shutdown ``kill_active`` still works unchanged.
    if not cfg.get("runner_cmd"):
        from . import runner_stream

        if runner_stream.stream_flavour(runner_name, invocation.repo_root):
            result = runner_stream.run_stream(runner_name, invocation, cfg)
            # Keep the trace artifact the blocking path writes — observability
            # for the (now default-on) streaming path. _write_trace is generic
            # over RunnerResult, so the driver stays trace-agnostic.
            if trace:
                result.trace_dir = _write_trace(result)
            return result

    cmd = _build_cmd(runner_name, invocation.prompt, cfg, invocation.repo_root)
    timeout = invocation.timeout_seconds or runner_timeout(cfg)
    # Always start from a cleaned base env so a parent agent session's
    # safe-mode / identity vars never leak into the runner (and silently
    # disable its hooks); layer the run's own env on top.
    proc_env = clean_runner_environ()
    if invocation.env:
        proc_env.update({str(k): str(v) for k, v in invocation.env.items()})
    stdout = ""
    stderr = ""
    returncode = 0
    try:
        with _proc_lock:
            _active_proc = subprocess.Popen(
                cmd,
                cwd=invocation.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=proc_env,
            )
        proc = _active_proc
        stdout, stderr = proc.communicate(timeout=timeout)
        returncode = proc.returncode
    except FileNotFoundError:
        stderr = f"executable '{cmd[0]}' not found on PATH"
        returncode = 127
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        stderr = (stderr + "\n" if stderr else "") + f"runner timed out after {timeout}s"
        returncode = 124
    finally:
        with _proc_lock:
            _active_proc = None

    if invocation.response_path and returncode == 0 and stdout and stdout.strip():
        _write_response_file(invocation.response_path, stdout)

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


# ── Run execution ───────────────────────────────────────────────────


def run_task(instruction: str) -> str:
    """Run a user instruction via the configured runner (for ``brr run``)."""
    from . import gitops
    repo_root = gitops.ensure_git_repo()
    from . import config as conf
    from . import prompts as _prompts

    cfg = conf.load_config(repo_root)
    runner_name = resolve_runner(repo_root)

    prompt = _prompts.build_run_prompt(instruction, repo_root)

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
