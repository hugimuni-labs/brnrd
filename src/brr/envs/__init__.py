"""Execution environment backends for daemon tasks.

The public CLI stays small; environments are daemon plumbing. Each
backend owns the task scratch location (host checkout, git worktree,
docker container) and the cleanup rules around one runner invocation.

Branching is the agent's call now: every worktree starts on a fresh
``brr/<task-id>`` branch from the daemon's resolved seed ref. The
agent commits there or switches to another branch; the daemon's
publish step (in ``daemon.publish``) reads whatever branch the
worktree ends up on and publishes it. The env layer does no
landing — it only classifies the worktree's final state into a
``publish_status`` (``ready`` | ``nothing`` | ``detached``) and records
which branch to publish.
"""

from __future__ import annotations

import json
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
    # Per-event interim-response drop zone (the multi-response protocol,
    # kb/design-multi-response.md). The resident drops mid-flight reply
    # files here; the daemon promotes them to the response partials queue.
    # Host vs env mirrors response_path_* — identical under today's envs
    # (the docker mount keeps `.brr/` the same inode inside and out).
    outbox_host: Path | None = None
    outbox_env: Path | None = None
    branch_name: str | None = None
    task_branch: str | None = None
    branch_plan: branching.PublishPlan | None = None
    env_state: dict[str, Any] = field(default_factory=dict)
    # Who operates this run: "user" (the local daemon — host, worktree,
    # and user-owned docker alike) or "operator" (a run dispatched onto
    # managed compute). Launcher-stamped, never read from the repo, so a
    # committed .brr/config can't forge it. Drives ergonomics routing
    # (see kb/design-agent-ergonomics.md → "Ownership decides routing"):
    # user-owned runs honour the `ergonomics` knob; operator-owned runs
    # ignore it. Managed compute isn't built yet, so every run today is
    # user-owned by default.
    owner: str = "user"


class EnvBackend(Protocol):
    name: str

    def prepare(
        self,
        task: Task,
        repo_root: Path,
        cfg: dict[str, Any],
        *,
        branch_plan: branching.PublishPlan,
        response_path: Path,
        outbox_path: Path | None = None,
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
        branch_plan: branching.PublishPlan,
        response_path: Path,
        outbox_path: Path | None = None,
    ) -> RunContext:
        return RunContext(
            name=self.name,
            cwd=repo_root,
            repo_root=repo_root,
            runtime_dir=gitops.shared_brr_dir(repo_root),
            response_path_host=response_path,
            response_path_env=response_path,
            outbox_host=outbox_path,
            outbox_env=outbox_path,
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
        branch_plan: branching.PublishPlan,
        response_path: Path,
        outbox_path: Path | None = None,
    ) -> RunContext:
        run_root, task_branch_name = worktree.create(
            repo_root, task.id, base_ref=branch_plan.seed_ref,
        )
        # When the event named a target branch, switch the worktree HEAD
        # there before the agent starts so it commits on the right branch
        # without any prompt instruction. The throwaway brr/<task-id>
        # placeholder stays as a local ref and is cleaned up at finalize.
        if branch_plan.target_branch:
            try:
                worktree.switch_to(run_root, branch_plan.target_branch)
            except worktree.BranchCheckedOutError as exc:
                starting_branch = task_branch_name
                notice = (
                    f"target branch {branch_plan.target_branch!r} is checked out "
                    f"at {exc.checkout_path}; starting on {task_branch_name!r} "
                    f"from {branch_plan.seed_ref!r} instead"
                )
                print(f"[brr] task {task.id}: {notice}")
                task.meta["branch_setup"] = "target-checked-out-elsewhere"
                task.meta["branch_setup_notice"] = notice
                task.meta["target_branch_checkout_path"] = str(exc.checkout_path)
            else:
                starting_branch = branch_plan.target_branch
        else:
            starting_branch = task_branch_name
        task.meta["worktree_path"] = str(run_root)
        task.meta["branch_name"] = starting_branch
        task.meta.update(branch_plan.meta_items())
        return RunContext(
            name=self.name,
            cwd=run_root,
            repo_root=repo_root,
            runtime_dir=gitops.shared_brr_dir(repo_root),
            response_path_host=response_path,
            response_path_env=response_path,
            outbox_host=outbox_path,
            outbox_env=outbox_path,
            branch_name=starting_branch,
            task_branch=task_branch_name,
            branch_plan=branch_plan,
            env_state={"worktree_path": str(run_root)},
        )

    def finalize(
        self,
        ctx: RunContext,
        task: Task,
        tasks_dir: Path,
    ) -> Task:
        """Classify the worktree's final state into a publish outcome.

        The agent's branch is whatever ``git`` says is checked out in the
        worktree after the run. The daemon's publish step (in
        ``daemon.publish``) reads ``task.meta["publish_branch"]`` and
        ``task.meta["publish_status"]`` and decides whether to push,
        with what lease, and to which remote ref. Finalize itself never
        updates a non-task branch ref and never calls
        ``gitops.fast_forward_branch``.

        Worktree teardown is outcome-aware: a clean success with no
        uncommitted files tears the worktree down (the branch ref +
        traces are the durable artefact). A ``detached`` outcome or any
        untracked/unstaged files in the worktree keep it alive so the
        operator can inspect what the agent left behind.
        """
        worktree_path = Path(ctx.env_state.get("worktree_path") or ctx.cwd)
        task_branch = ctx.task_branch or worktree.task_branch_name(task.id)
        initial_branch = ctx.branch_name or task_branch

        if task.status != "done":
            task.save(tasks_dir)
            return task

        outcome = self._resolve_outcome(
            ctx,
            worktree_path=worktree_path,
            initial_branch=initial_branch,
        )

        task.meta["publish_status"] = outcome.status
        if outcome.publish_branch:
            task.meta["publish_branch"] = outcome.publish_branch
            task.meta["branch_name"] = outcome.publish_branch
        elif "publish_branch" in task.meta:
            del task.meta["publish_branch"]
        task.save(tasks_dir)

        if outcome.keep_worktree:
            print(
                f"[brr] task {task.id}: keeping worktree at {worktree_path} "
                f"({outcome.keep_reason})"
            )
            return task

        if worktree.has_uncommitted_changes(worktree_path):
            print(
                f"[brr] task {task.id}: keeping worktree at {worktree_path} "
                "(uncommitted changes left behind)"
            )
            return task

        worktree.remove(
            ctx.repo_root, task.id,
            branch=task_branch,
            delete_branch=outcome.delete_task_branch,
            force=True,
        )
        if outcome.delete_unused_initial:
            self._delete_unused_initial_branch(ctx.repo_root, task_branch)
        elif outcome.publish_branch and task_branch != outcome.publish_branch:
            # task_branch is a throwaway placeholder (brr/<task-id>) that
            # was switched away from before the agent ran; it won't be
            # pushed, so clean it up now.
            self._delete_unused_initial_branch(ctx.repo_root, task_branch)
        return task

    def _resolve_outcome(
        self,
        ctx: RunContext,
        *,
        worktree_path: Path,
        initial_branch: str,
    ) -> "_FinalizeOutcome":
        """Map the worktree's final git state to a publish outcome.

        Four cases:

        - detached HEAD: ``status=detached``, no publish branch,
          keep the worktree for inspection.
        - final branch has no commits beyond the seed ref:
          ``status=nothing``, no publish branch, delete the task branch
          along with the worktree.
        - final branch has commits, agent stayed on the starting branch:
          ``status=ready``, publish the starting branch. Worktree is torn
          down unless it has uncommitted leftovers.
        - final branch has commits, agent switched branches:
          ``status=ready``, publish the new branch. Worktree is torn
          down unless it has uncommitted leftovers; the unused
          ``brr/<task-id>`` placeholder is best-effort deleted.
        """
        plan = ctx.branch_plan
        if plan is None:
            raise RuntimeError(
                "worktree finalize has no publish plan"
            )

        final_branch = worktree.current_branch(worktree_path)

        if final_branch is None:
            return _FinalizeOutcome(
                status="detached",
                publish_branch=None,
                keep_worktree=True,
                keep_reason="detached HEAD",
            )

        if final_branch == initial_branch and not worktree.has_commits_beyond(
            worktree_path, plan.seed_ref,
        ):
            return _FinalizeOutcome(
                status="nothing",
                publish_branch=None,
                delete_task_branch=True,
            )

        if final_branch != initial_branch:
            return _FinalizeOutcome(
                status="ready",
                publish_branch=final_branch,
                delete_unused_initial=True,
            )

        return _FinalizeOutcome(
            status="ready",
            publish_branch=final_branch,
        )

    def _delete_unused_initial_branch(self, repo_root: Path, branch: str) -> None:
        """Best-effort delete of the throwaway ``brr/<task-id>`` placeholder.

        Called when the agent ended on a different branch, so the task
        branch still points at the seed commit and can be safely removed.
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


@dataclass
class _FinalizeOutcome:
    """Classification produced by ``WorktreeEnv._resolve_outcome``.

    ``status`` mirrors ``task.meta["publish_status"]``: one of
    ``ready``, ``nothing``, or ``detached``. ``conflict`` is owned by
    the daemon's publish step — finalize never produces it because the
    env layer no longer touches non-task refs.
    """

    status: str
    publish_branch: str | None
    keep_worktree: bool = False
    keep_reason: str = ""
    delete_task_branch: bool = False
    delete_unused_initial: bool = False


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
    # Forward the GitHub token when the operator sets it in the environment
    # directly; tasks triggered by the GitHub gate additionally receive it
    # injected from the gate's stored state (see _resolve_github_gate_token).
    "GITHUB_TOKEN",
    "GH_TOKEN",
)


# Per-runner credential paths under HOME, relative to ``~``. Each is
# mounted into the container's HOME at ``/brr-home/<rel>`` when present
# on the host, so the in-container CLI finds tokens at ``$HOME/.codex``,
# etc. The container runs as the host UID, so bind-mounted host paths
# keep their host ownership and the in-container user can read/write
# them.
#
# ``~/.config/gh`` is intentionally absent: on Linux the gh CLI stores
# its OAuth token in the system keyring (libsecret/gnome-keyring) and
# the on-disk ``hosts.yml`` only carries the account name. Bind-mounting
# that file into the container leaves gh with an account it can't
# authenticate (the keyring socket isn't reachable across the container
# boundary), which makes ``gh auth status`` exit non-zero and produces
# confusing reports even when the GitHub token brr injects as
# ``GITHUB_TOKEN`` works fine for real operations. The token-injection
# path below covers gh CLI auth uniformly across keyring and file
# backends, so the mount adds nothing but a footgun.
_DOCKER_DEFAULT_CRED_PATHS: tuple[str, ...] = (
    ".claude",
    ".claude.json",
    ".codex",
    ".gemini",
    ".gitconfig",
    ".ssh",
)

# HOME directory baked into the runner image, owned mode 1777 so any UID
# the daemon hands the container can use it.
_DOCKER_CONTAINER_HOME = "/brr-home"


def _docker_extra_env_keys(cfg: dict[str, Any]) -> list[str]:
    """Return user-supplied env-var names from ``docker.env=KEY1,KEY2,...``."""
    raw = _docker_cfg(cfg, "env")
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def _resolve_github_gate_token(brr_dir: Path) -> str | None:
    """Return the token stored in the GitHub gate's state file, or from env.

    Used to inject ``GITHUB_TOKEN`` into Docker containers when a task was
    triggered by the GitHub gate, so the runner's ``gh`` CLI and HTTPS git
    operations are authenticated without relying on the system keyring (which
    is unavailable inside a container).
    """
    state_path = brr_dir / "gates" / "github.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            stored = state.get("token")
            if isinstance(stored, str) and stored.strip():
                return stored.strip()
        except Exception:
            pass
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(name)
        if val:
            return val.strip()
    if shutil.which("gh") is not None:
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


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
    tokens and updated session state on the host stay current. Targets
    land under the container's HOME, which the daemon also sets via
    ``-e HOME=...`` so the CLIs find them at ``$HOME/.codex`` etc.

    Includes ``~/.ssh`` so the runner can push via SSH remotes without
    needing a separate credential setup.
    """
    if not _docker_bool(cfg, "mount_credentials", True):
        return []
    home = Path(os.path.expanduser("~"))
    args: list[str] = []
    for rel in _DOCKER_DEFAULT_CRED_PATHS:
        host = home / rel
        if host.exists():
            args.extend(["-v", f"{host}:{_DOCKER_CONTAINER_HOME}/{rel}"])
    return args


def _docker_user_args() -> list[str]:
    """Return ``-u UID:GID`` for the host user, when available.

    Running the container under the host's UID is what stops bind-mounted
    writes (notably ``.git/objects/``) from being created as root. We
    intentionally use ``os.getuid`` rather than parsing ``$USER`` so the
    daemon's actual effective UID is what reaches docker. The check
    falls back gracefully on platforms without ``os.getuid`` (Windows).
    """
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return []
    return ["-u", f"{getuid()}:{getgid()}"]


def _docker_git_config_env_args(github_token_available: bool = False) -> list[str]:
    """Inject git config inside the container via ``GIT_CONFIG_*`` env vars.

    The repo is bind-mounted at the same absolute path it has on the
    host. The host directory is owned by the user running the daemon,
    while the container runs as root by default. Without this, git
    refuses to operate (``fatal: detected dubious ownership in
    repository``, CVE-2022-24765), which breaks every branch task — the
    agent can't even ``git status``.

    Using git's env-var config (``GIT_CONFIG_COUNT/KEY/VALUE``, supported
    since git 2.31) avoids requiring every image to bake the same line
    into ``/etc/gitconfig`` and works for user-built images too.

    When a GitHub token is available, also rewrite common GitHub SSH
    remote forms to HTTPS and provide a token-backed credential helper.
    ``GITHUB_TOKEN`` alone does not help ``git push git@github.com:...``;
    the rewrite lets PR/rebase tasks push from Docker even when no SSH
    agent is mounted.
    """
    pairs: list[tuple[str, str]] = [
        ("safe.directory", "*"),
    ]
    if github_token_available:
        pairs.extend([
            ("url.https://github.com/.insteadOf", "git@github.com:"),
            ("url.https://github.com/.insteadOf", "ssh://git@github.com/"),
            (
                "credential.helper",
                "!f() { test \"$1\" = get || exit 0; "
                "echo username=x-access-token; "
                "echo \"password=${GITHUB_TOKEN:-$GH_TOKEN}\"; }; f",
            ),
        ])

    args = ["-e", f"GIT_CONFIG_COUNT={len(pairs)}"]
    for idx, (key, value) in enumerate(pairs):
        args.extend([
            "-e", f"GIT_CONFIG_KEY_{idx}={key}",
            "-e", f"GIT_CONFIG_VALUE_{idx}={value}",
        ])
    return args


def _docker_git_safe_directory_args() -> list[str]:
    """Backward-compatible wrapper for tests/importers of the old helper."""
    return _docker_git_config_env_args()


def _docker_github_token_for_git(ctx: RunContext) -> str | None:
    token = ctx.env_state.get("github_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    return None


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
        branch_plan: branching.PublishPlan,
        response_path: Path,
        outbox_path: Path | None = None,
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
            outbox_path=outbox_path,
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

        # Resolve a GitHub token for every docker task, not only the
        # github-source ones. The container has no path to the host's
        # keyring or to the user's gh stored accounts, so without an
        # injected ``GITHUB_TOKEN`` the agent's ``gh`` CLI is dead and
        # ``git push`` over HTTPS to github.com falls back to anonymous
        # — which silently breaks any task that needs to look up sibling
        # PRs, read upstream issues, or open a fresh PR from a worktree
        # branch even when the task wasn't strictly triggered by the
        # github gate. Resolution prefers stored gate state, then
        # daemon-side env vars, then ``gh auth token``; absent all
        # three the field stays unset and the container runs with no
        # GitHub auth, same as before.
        token = _resolve_github_gate_token(repo_root / ".brr")
        if token:
            ctx.env_state["github_token"] = token

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
            # Run as the host user so writes inside the bind-mounted
            # repo (``.git/objects/`` chief among them) are owned by the
            # host user, not by root. HOME points at the image's writable
            # ``/brr-home``; credential and gitconfig mounts land there
            # so the CLIs and git pick them up via ``$HOME``.
            *_docker_user_args(),
            "-e", f"HOME={_DOCKER_CONTAINER_HOME}",
            # Non-interactive git (rebase --continue, commit, etc.) must not
            # try to launch an editor that isn't in the slim runner image.
            "-e", "GIT_EDITOR=true",
            *_docker_git_config_env_args(bool(_docker_github_token_for_git(ctx))),
            *_docker_passthrough_env_args(cfg),
            *_docker_credential_mount_args(cfg),
            # Inject the GitHub gate token when available so ``gh`` CLI
            # and HTTPS git operations are authenticated inside the
            # container regardless of whether the system keyring is
            # reachable (it isn't inside Docker).
            *(
                ["-e", f"GITHUB_TOKEN={ctx.env_state['github_token']}"]
                if ctx.env_state.get("github_token")
                and not os.environ.get("GITHUB_TOKEN")
                else []
            ),
            # Bind-mount the repo at the *same absolute path* inside the
            # container (not a remapped /workspace). This is deliberate:
            #   - git worktrees store an absolute pointer back to the main
            #     .git; remapping breaks that pointer inside the container.
            #   - paths the agent emits (commit text, the response file,
            #     diffense pack locators) stay valid verbatim on the host.
            #   - it's why response_path_host == response_path_env here, and
            #     why artifacts under repo_root/.brr (response, review pack)
            #     are the same inode inside and out — no copy step needed.
            # Trade-off: this is dependency + network isolation running as
            # the host UID over a RW repo mount + mounted creds — NOT a
            # containment/credential boundary. It assumes a trusted agent.
            # `docker.isolation=clone` (design-env-interface.md) is the seam
            # for a no-shared-.git variant if that assumption ever changes.
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
    ) -> Task:
        task = super().finalize(ctx, task, tasks_dir)
        containers = ctx.env_state.get("docker_containers", [])
        if not isinstance(containers, list):
            containers = []

        # Outcome-aware container cleanup: keep on failure (so the
        # operator can ``docker logs`` / ``docker exec`` to inspect),
        # remove on clean success. Matches the worktree contract.
        if task.status != "done":
            if containers:
                task.meta["docker_containers"] = ", ".join(
                    str(c) for c in containers
                )
                task.save(tasks_dir)
            return task

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
