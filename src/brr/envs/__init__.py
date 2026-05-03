"""Execution environment backends for daemon tasks.

The public CLI stays small; environments are daemon plumbing.  Each
backend owns the task scratch location and cleanup rules around one
runner invocation.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Any

from .. import gitops, runner, worktree
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
    base_branch: str | None = None
    log_file: str | None = None
    env_state: dict[str, Any] = field(default_factory=dict)


class EnvBackend(Protocol):
    name: str

    def prepare(
        self,
        task: Task,
        repo_root: Path,
        cfg: dict[str, Any],
        *,
        branch_name: str | None,
        base_branch: str | None,
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
        branch_name: str | None,
        base_branch: str | None,
        response_path: Path,
        debug: bool = False,
    ) -> RunContext:
        if branch_name is not None:
            raise RuntimeError("host env can only run on branch: current")
        return RunContext(
            name=self.name,
            cwd=repo_root,
            repo_root=repo_root,
            runtime_dir=gitops.shared_brr_dir(repo_root),
            response_path_host=response_path,
            response_path_env=response_path,
            branch_name=None,
            base_branch=base_branch,
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
        branch_name: str | None,
        base_branch: str | None,
        response_path: Path,
        debug: bool = False,
    ) -> RunContext:
        if branch_name is None:
            raise RuntimeError("worktree env requires a non-current branch strategy")
        run_root = worktree.create(
            repo_root,
            task.id,
            branch_name,
            create_branch=not gitops.branch_exists(repo_root, branch_name),
        )
        task.meta["worktree_path"] = str(run_root)
        return RunContext(
            name=self.name,
            cwd=run_root,
            repo_root=repo_root,
            runtime_dir=gitops.shared_brr_dir(repo_root),
            response_path_host=response_path,
            response_path_env=response_path,
            branch_name=branch_name,
            base_branch=base_branch,
            log_file=f"kb/log-{task.id}.md",
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
        branch_name = ctx.branch_name
        if branch_name is None:
            return task

        if task.status != "done":
            task.save(tasks_dir)
            return task

        if task.branch in ("auto", "task"):
            result = gitops.merge_branch(
                ctx.repo_root,
                branch_name,
                f"merge {branch_name} for {task.id}",
            )
            if not result.success:
                print(f"[brr] task {task.id}: merge conflict on {branch_name}")
                task.update_status("conflict", tasks_dir)
                return task
            if debug:
                print(f"[brr] debug: keeping worktree for {task.id}")
            else:
                worktree.remove(
                    ctx.repo_root,
                    task.id,
                    branch=branch_name,
                    delete_branch=True,
                    force=True,
                )
            return task

        if debug:
            print(f"[brr] debug: keeping worktree for {task.id}")
        else:
            worktree.remove(ctx.repo_root, task.id, branch=branch_name, force=True)
        return task


def _docker_cfg(cfg: dict[str, Any], key: str, default: str = "") -> str:
    value = cfg.get(f"docker.{key}", cfg.get(f"docker_{key}", default))
    return str(value).strip() if value is not None else ""


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
        branch_name: str | None,
        base_branch: str | None,
        response_path: Path,
        debug: bool = False,
    ) -> RunContext:
        if shutil.which("docker") is None:
            raise RuntimeError("docker env requires the Docker CLI on PATH")
        image = _docker_cfg(cfg, "image")
        if not image:
            raise RuntimeError("docker env requires docker.image in .brr/config")

        if branch_name is None:
            ctx = RunContext(
                name=self.name,
                cwd=repo_root,
                repo_root=repo_root,
                runtime_dir=gitops.shared_brr_dir(repo_root),
                response_path_host=response_path,
                response_path_env=response_path,
                branch_name=None,
                base_branch=base_branch,
            )
        else:
            ctx = super().prepare(
                task,
                repo_root,
                cfg,
                branch_name=branch_name,
                base_branch=base_branch,
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

        inner_cmd = runner._build_cmd(
            runner_name,
            invocation.prompt,
            cfg,
            response_path=str(ctx.response_path_env),
        )
        command = [
            "docker", "run",
            "--name", container_name,
            "--network", network,
            "-v", f"{ctx.repo_root}:{ctx.repo_root}",
            "-w", str(invocation.cwd or ctx.cwd),
            image,
            *inner_cmd,
        ]

        stdout = ""
        stderr = ""
        returncode = 0
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=600,
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
            stderr = (stderr + "\n" if stderr else "") + "runner timed out after 600s"
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
    env_name = (name or "host").strip()
    backend = _BUILTINS.get(env_name)
    if backend is None:
        supported = ", ".join(sorted(_BUILTINS))
        raise UnsupportedEnvironmentError(
            f"environment backend '{env_name}' is not available yet "
            f"(supported: {supported})"
        )
    return backend()
