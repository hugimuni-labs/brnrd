"""Execution environment backends for daemon tasks.

The public CLI stays small; environments are daemon plumbing.  Each
backend owns the task scratch location and cleanup rules around one
runner invocation.
"""

from __future__ import annotations

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


class LocalEnv:
    name = "local"

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
            raise RuntimeError("local env can only run on branch: current")
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


class WorktreeEnv(LocalEnv):
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


_BUILTINS: dict[str, type[EnvBackend]] = {
    "local": LocalEnv,
    "worktree": WorktreeEnv,
}


def get_env(name: str) -> EnvBackend:
    env_name = (name or "local").strip()
    backend = _BUILTINS.get(env_name)
    if backend is None:
        supported = ", ".join(sorted(_BUILTINS))
        raise UnsupportedEnvironmentError(
            f"environment backend '{env_name}' is not available yet "
            f"(supported: {supported})"
        )
    return backend()
