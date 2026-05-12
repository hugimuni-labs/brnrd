"""Execution environment backends for daemon tasks.

The public CLI stays small; environments are daemon plumbing. Each
backend owns the task scratch location (host checkout, git worktree,
docker container) and the cleanup rules around one runner invocation.

Branching is the agent's call now: every worktree starts on a fresh
``brr/<task-id>`` branch from the daemon's resolved seed ref. If the
branch plan has an auto-land target, leaving commits on the task branch
lets brr fast-forward that target. Without a target, brr preserves the
task branch for human routing. Switching to another branch also preserves
that branch as-is.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Any

from .. import branching, gitops, runner, worktree
from ..task import Task


class UnsupportedEnvironmentError(RuntimeError):
    """Raised when a task asks for an environment with no backend."""


@dataclass
class RunContext:
    name: str
    cwd: Path
    repo_root: Path
    runtime_dir: Path
    response_path_host: Path
    response_path_env: Path
    branch_name: str | None = None
    branch_plan: branching.BranchPlan | None = None
    env_state: dict[str, Any] = field(default_factory=dict)


class EnvBackend(Protocol):
    name: str

    def prepare(
        self,
        task: Task,
        repo_root: Path,
        cfg: dict[str, Any],
        *,
        branch_plan: branching.BranchPlan,
        response_path: Path,
        debug: bool = False,
    ) -> RunContext:
        ...

    def invoke(
        self,
        ctx: RunContext,
        runner_name: str,
        invocation: runner.RunnerInvocation,
        cfg: dict[str, Any],
        *,
        trace: bool = False,
    ) -> runner.RunnerResult:
        ...

    def finalize(
        self,
        ctx: RunContext,
        task: Task,
        tasks_dir: Path,
        *,
        debug: bool = False,
    ) -> Task:
        ...


class HostEnv:
    name = "host"

    def prepare(
        self,
        task: Task,
        repo_root: Path,
        cfg: dict[str, Any],
        *,
        branch_plan: branching.BranchPlan,
        response_path: Path,
        debug: bool = False,
    ) -> RunContext:
        return RunContext(
            name=self.name,
            cwd=repo_root,
            repo_root=repo_root,
            runtime_dir=gitops.shared_brr_dir(repo_root),
            response_path_host=response_path,
            response_path_env=response_path,
            branch_name=None,
            branch_plan=branch_plan,
        )

    def invoke(
        self,
        ctx: RunContext,
        runner_name: str,
        invocation: runner.RunnerInvocation,
        cfg: dict[str, Any],
        *,
        trace: bool = False,
    ) -> runner.RunnerResult:
        return runner.invoke_runner(runner_name, invocation, cfg=cfg, trace=trace)

    def finalize(
        self,
        ctx: RunContext,
        task: Task,
        tasks_dir: Path,
        *,
        debug: bool = False,
    ) -> Task:
        return task


class WorktreeEnv(HostEnv):
    name = "worktree"

    def prepare(
        self,
        task: Task,
        repo_root: Path,
        cfg: dict[str, Any],
        *,
        branch_plan: branching.BranchPlan,
        response_path: Path,
        debug: bool = False,
    ) -> RunContext:
        run_root, branch_name = worktree.create(
            repo_root, task.id, base_ref=branch_plan.seed_ref,
        )
        task.meta["worktree_path"] = str(run_root)
        task.meta["branch_name"] = branch_name
        task.meta.update(branch_plan.meta_items())
        return RunContext(
            name=self.name,
            cwd=run_root,
            repo_root=repo_root,
            runtime_dir=gitops.shared_brr_dir(repo_root),
            response_path_host=response_path,
            response_path_env=response_path,
            branch_name=branch_name,
            branch_plan=branch_plan,
            env_state={"worktree_path": str(run_root)},
        )

    def finalize(
        self,
        ctx: RunContext,
        task: Task,
        tasks_dir: Path,
        *,
        debug: bool = False,
    ) -> Task:
        worktree_path = Path(ctx.env_state.get("worktree_path") or ctx.cwd)
        initial_branch = ctx.branch_name or worktree.task_branch_name(task.id)

        if task.status != "done":
            task.save(tasks_dir)
            return task

        final_branch = worktree.current_branch(worktree_path)
        changed = self._land_or_preserve(
            ctx, task, tasks_dir,
            worktree_path=worktree_path,
            initial_branch=initial_branch,
            final_branch=final_branch,
            debug=debug,
        )
        if final_branch:
            task.meta["branch_name"] = final_branch
        if changed or final_branch:
            task.save(tasks_dir)
        return task

    def _land_or_preserve(
        self,
        ctx: RunContext,
        task: Task,
        tasks_dir: Path,
        *,
        worktree_path: Path,
        initial_branch: str,
        final_branch: str | None,
        debug: bool,
    ) -> bool:
        """Decide what to do with the agent's branch on cleanup.

        Cleanup is outcome-aware: a clean success with no uncommitted
        files tears the worktree down (the branch ref + traces are the
        durable artefact). A conflict, a detached HEAD, or any
        untracked/unstaged files in the worktree keeps it alive so the
        operator can inspect what the agent left behind. ``debug``
        keeps the worktree even on clean success.

        Returns True when task metadata changed after the earlier status
        save, so callers know to persist it.
        """
        plan = ctx.branch_plan
        if plan is None:
            raise RuntimeError(
                f"task {task.id}: worktree finalize has no branch plan"
            )

        # ── Classify the run and record final branch metadata ──────
        delete_branch = False
        delete_unused_initial = False
        keep_worktree = False
        changed_metadata = True

        if final_branch is None:
            print(f"[brr] task {task.id}: detached HEAD inside worktree, preserving")
            task.meta["preserved_branch"] = initial_branch
            task.meta["changed_branch"] = initial_branch
            keep_worktree = True
        elif final_branch != initial_branch:
            print(
                f"[brr] task {task.id}: agent landed on {final_branch}, "
                "preserving (runtime branch choice)"
            )
            task.meta["preserved_branch"] = final_branch
            task.meta["changed_branch"] = final_branch
            delete_unused_initial = True
        elif not worktree.has_commits_beyond(worktree_path, plan.seed_ref):
            # Throwaway task branch with no commits — drop both.
            delete_branch = True
            changed_metadata = False
        elif not plan.auto_land_branch:
            print(
                f"[brr] task {task.id}: preserving {initial_branch} "
                "(no auto-land target)"
            )
            task.meta["preserved_branch"] = initial_branch
            task.meta["changed_branch"] = initial_branch
        else:
            result = gitops.fast_forward_branch(
                ctx.repo_root,
                plan.auto_land_branch,
                initial_branch,
                expected_old_oid=plan.expected_old_oid,
            )
            if result.success:
                task.meta["landed_branch"] = plan.auto_land_branch
                task.meta["changed_branch"] = plan.auto_land_branch
                task.meta["landed_commit"] = result.commit
                delete_branch = True
            else:
                print(
                    f"[brr] task {task.id}: cannot fast-forward "
                    f"{plan.auto_land_branch}, preserving {initial_branch}"
                )
                task.update_status("conflict", tasks_dir)
                task.meta["preserved_branch"] = initial_branch
                task.meta["changed_branch"] = initial_branch
                if result.detail:
                    task.meta["branch_error"] = result.detail
                keep_worktree = True

        # ── Decide whether to tear down the worktree directory ─────
        if debug:
            print(f"[brr] debug: keeping worktree for {task.id}")
            return changed_metadata
        if keep_worktree:
            print(
                f"[brr] task {task.id}: keeping worktree at {worktree_path} "
                "for inspection"
            )
            return changed_metadata
        if worktree.has_uncommitted_changes(worktree_path):
            print(
                f"[brr] task {task.id}: keeping worktree at {worktree_path} "
                "(uncommitted changes left behind)"
            )
            return changed_metadata

        worktree.remove(
            ctx.repo_root, task.id,
            branch=initial_branch,
            delete_branch=delete_branch,
            force=True,
        )
        if delete_unused_initial and initial_branch != final_branch:
            self._delete_unused_initial_branch(ctx.repo_root, initial_branch)
        return changed_metadata

    def _delete_unused_initial_branch(self, repo_root: Path, branch: str) -> None:
        """Best-effort delete of the throwaway ``brr/<task-id>`` branch.

        When the agent switched off it before committing, the branch
        still points at the base commit and can be safely removed.
        Failures are non-fatal — branches are cheap.
        """
        result = subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=repo_root, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if detail:
                print(f"[brr] warning: could not delete {branch}: {detail}")


def _docker_cfg(cfg: dict[str, Any], key: str, default: str = "") -> str:
    value = cfg.get(f"docker.{key}", cfg.get(f"docker_{key}", default))
    return str(value).strip() if value is not None else ""


def _docker_bool(cfg: dict[str, Any], key: str, default: bool) -> bool:
    """Read a docker.<key> boolean, accepting native bool/int and string forms."""
    raw = cfg.get(f"docker.{key}", cfg.get(f"docker_{key}", default))
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return bool(raw)
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off", ""):
            return False
    return default


# Known runner credential env vars forwarded into the container when set on
# the daemon's environment. Subscription users (Claude Pro/Max, ChatGPT
# Plus/Pro, Gemini OAuth) won't have these — they're covered by the
# credential dir mounts below.
_DOCKER_DEFAULT_PASSTHROUGH_ENV: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


# Per-runner credential paths under HOME. Each is mounted into /root/<basename>
# when present on the host, so the in-container CLI finds tokens at the same
# location it would on the host (assuming the container runs as root, which is
# the docker env's current default).
_DOCKER_DEFAULT_CRED_PATHS: tuple[str, ...] = (
    ".claude",
    ".claude.json",
    ".codex",
    ".gemini",
)


def _docker_extra_env_keys(cfg: dict[str, Any]) -> list[str]:
    """Return user-supplied env-var names from ``docker.env=KEY1,KEY2,...``."""
    raw = _docker_cfg(cfg, "env")
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def _docker_passthrough_env_args(cfg: dict[str, Any]) -> list[str]:
    """Build ``-e NAME`` args for env vars that are set on the daemon."""
    seen: set[str] = set()
    args: list[str] = []
    for name in (*_DOCKER_DEFAULT_PASSTHROUGH_ENV, *_docker_extra_env_keys(cfg)):
        if not name or name in seen:
            continue
        seen.add(name)
        if os.environ.get(name):
            args.extend(["-e", name])
    return args


def _docker_credential_mount_args(cfg: dict[str, Any]) -> list[str]:
    """Build ``-v`` args for known runner credential paths under HOME.

    Empty when ``docker.mount_credentials=false`` or the host has none of
    the well-known credential paths. Mounts are read-write so refresh
    tokens and updated session state on the host stay current.
    """
    if not _docker_bool(cfg, "mount_credentials", True):
        return []
    home = Path(os.path.expanduser("~"))
    args: list[str] = []
    for rel in _DOCKER_DEFAULT_CRED_PATHS:
        host = home / rel
        if host.exists():
            args.extend(["-v", f"{host}:/root/{rel}"])
    return args


def _docker_git_safe_directory_args() -> list[str]:
    """Inject ``safe.directory='*'`` git config inside the container.

    The repo is bind-mounted at the same absolute path it has on the
    host. The host directory is owned by the user running the daemon,
    while the container runs as root by default. Without this, git
    refuses to operate (``fatal: detected dubious ownership in
    repository``, CVE-2022-24765), which breaks every branch task — the
    agent can't even ``git status``.

    Using git's env-var config (``GIT_CONFIG_COUNT/KEY/VALUE``, supported
    since git 2.31) avoids requiring every image to bake the same line
    into ``/etc/gitconfig`` and works for user-built images too.
    """
    return [
        "-e", "GIT_CONFIG_COUNT=1",
        "-e", "GIT_CONFIG_KEY_0=safe.directory",
        "-e", "GIT_CONFIG_VALUE_0=*",
    ]


def _docker_container_name(task_id: str, label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{task_id}-{label}").strip(".-_")
    if not slug or not slug[0].isalnum():
        slug = f"task-{slug}"
    return f"brr-{slug}"[:120]


def _artifact_records(
    specs: list[runner.RunnerArtifactSpec],
) -> list[runner.RunnerArtifactRecord]:
    return [
        runner.RunnerArtifactRecord(
            path=spec.path,
            label=spec.label or str(spec.path),
            exists=spec.path.exists(),
        )
        for spec in specs
    ]


def _subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


class DockerEnv(WorktreeEnv):
    name = "docker"

    def prepare(
        self,
        task: Task,
        repo_root: Path,
        cfg: dict[str, Any],
        *,
        branch_plan: branching.BranchPlan,
        response_path: Path,
        debug: bool = False,
    ) -> RunContext:
        if shutil.which("docker") is None:
            raise RuntimeError("docker env requires the Docker CLI on PATH")
        image = _docker_cfg(cfg, "image")
        if not image:
            raise RuntimeError("docker env requires docker.image in .brr/config")

        ctx = super().prepare(
            task,
            repo_root,
            cfg,
            branch_plan=branch_plan,
            response_path=response_path,
            debug=debug,
        )
        ctx.name = self.name

        ctx.env_state.update({
            "task_id": task.id,
            "docker_image": image,
            "docker_network": _docker_cfg(cfg, "network", "bridge"),
            "docker_mount": str(repo_root),
            "docker_containers": [],
        })
        task.meta["docker_image"] = image
        return ctx

    def invoke(
        self,
        ctx: RunContext,
        runner_name: str,
        invocation: runner.RunnerInvocation,
        cfg: dict[str, Any],
        *,
        trace: bool = False,
    ) -> runner.RunnerResult:
        image = str(ctx.env_state.get("docker_image") or _docker_cfg(cfg, "image"))
        network = str(
            ctx.env_state.get("docker_network")
            or _docker_cfg(cfg, "network", "bridge")
        )
        container_name = _docker_container_name(
            str(ctx.env_state.get("task_id", "") or "task"),
            invocation.label,
        )
        containers = ctx.env_state.setdefault("docker_containers", [])
        if isinstance(containers, list):
            containers.append(container_name)
        ctx.env_state["docker_container"] = container_name

        inner_cmd = runner._build_cmd(runner_name, invocation.prompt, cfg)
        command = [
            "docker", "run",
            "--name", container_name,
            "--network", network,
            # ``-i`` keeps the container's stdin connected to docker's
            # stdin (which we tie to /dev/null below) so codex sees an
            # immediate EOF instead of an open-but-silent pipe — without
            # this, codex 0.128+'s "Reading additional input from stdin"
            # path can block until our timeout fires.
            "-i",
            *_docker_git_safe_directory_args(),
            *_docker_passthrough_env_args(cfg),
            *_docker_credential_mount_args(cfg),
            "-v", f"{ctx.repo_root}:{ctx.repo_root}",
            "-w", str(invocation.cwd or ctx.cwd),
            image,
            *inner_cmd,
        ]

        timeout = runner.runner_timeout(cfg)
        stdout = ""
        stderr = ""
        returncode = 0
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            returncode = completed.returncode
        except FileNotFoundError:
            stderr = "executable 'docker' not found on PATH"
            returncode = 127
        except subprocess.TimeoutExpired as exc:
            stdout = _subprocess_text(exc.stdout)
            stderr = _subprocess_text(exc.stderr)
            stderr = (stderr + "\n" if stderr else "") + f"runner timed out after {timeout}s"
            returncode = 124
            try:
                subprocess.run(
                    ["docker", "kill", container_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        if invocation.response_path and returncode == 0 and stdout and stdout.strip():
            runner._write_response_file(invocation.response_path, stdout)

        result = runner.RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            trace_dir=None,
            artifacts=[],
        )
        if trace:
            result.trace_dir = runner._write_trace(result)
        else:
            result.artifacts = _artifact_records(invocation.required_artifacts)
        return result

    def finalize(
        self,
        ctx: RunContext,
        task: Task,
        tasks_dir: Path,
        *,
        debug: bool = False,
    ) -> Task:
        task = super().finalize(ctx, task, tasks_dir, debug=debug)
        containers = ctx.env_state.get("docker_containers", [])
        if not isinstance(containers, list):
            containers = []

        if containers and (task.status != "done" or debug):
            task.meta["docker_containers"] = ", ".join(str(c) for c in containers)
            task.save(tasks_dir)
            if debug:
                print(f"[brr] debug: keeping docker container(s) for {task.id}")
            return task

        if task.status == "done" and not debug:
            for container in containers:
                result = subprocess.run(
                    ["docker", "rm", "-f", str(container)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode != 0:
                    detail = result.stderr.strip() or result.stdout.strip()
                    print(
                        "[brr] warning: failed to remove docker container "
                        f"{container}: {detail}"
                    )
        return task


_BUILTINS: dict[str, type[EnvBackend]] = {
    "docker": DockerEnv,
    "host": HostEnv,
    "worktree": WorktreeEnv,
}


def get_env(name: str) -> EnvBackend:
    env_name = (name or "worktree").strip()
    backend = _BUILTINS.get(env_name)
    if backend is None:
        supported = ", ".join(sorted(_BUILTINS))
        raise UnsupportedEnvironmentError(
            f"environment backend '{env_name}' is not available yet "
            f"(supported: {supported})"
        )
    return backend()
