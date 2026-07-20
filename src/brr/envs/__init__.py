"""Execution environment backends for daemon runs.

The public CLI stays small; environments are daemon plumbing. Each
backend owns the run scratch location (host checkout, git worktree,
docker container) and the cleanup rules around one runner invocation.

Branching is the agent's call now: every worktree starts on a fresh
``brr/<run-id>`` branch from the daemon's resolved seed ref. The
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
from ..run import Run


class UnsupportedEnvironmentError(RuntimeError):
    """Raised when a run asks for an environment with no backend."""


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
    run_branch: str | None = None
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
        task: Run,
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
        task: Run,
        runs_dir: Path,
    ) -> Run:
        ...


class HostEnv:
    name = "host"

    def prepare(
        self,
        task: Run,
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
        task: Run,
        runs_dir: Path,
    ) -> Run:
        return task


class WorktreeEnv(HostEnv):
    name = "worktree"

    def prepare(
        self,
        task: Run,
        repo_root: Path,
        cfg: dict[str, Any],
        *,
        branch_plan: branching.PublishPlan,
        response_path: Path,
        outbox_path: Path | None = None,
    ) -> RunContext:
        run_root, run_branch_name = worktree.create(
            repo_root, task.id, base_ref=branch_plan.seed_ref,
        )
        # When the event named a target branch, switch the worktree HEAD
        # there before the agent starts so it commits on the right branch
        # without any prompt instruction. The throwaway brr/<run-id>
        # placeholder stays as a local ref and is cleaned up at finalize.
        if branch_plan.target_branch:
            try:
                worktree.switch_to(run_root, branch_plan.target_branch)
            except worktree.BranchCheckedOutError as exc:
                starting_branch = run_branch_name
                notice = (
                    f"target branch {branch_plan.target_branch!r} is checked out "
                    f"at {exc.checkout_path}; starting on {run_branch_name!r} "
                    f"from {branch_plan.seed_ref!r} instead"
                )
                print(f"[brnrd] run {task.id}: {notice}")
                task.meta["branch_setup"] = "target-checked-out-elsewhere"
                task.meta["branch_setup_notice"] = notice
                task.meta["target_branch_checkout_path"] = str(exc.checkout_path)
            else:
                starting_branch = branch_plan.target_branch
        else:
            starting_branch = run_branch_name
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
            run_branch=run_branch_name,
            branch_plan=branch_plan,
            env_state={"worktree_path": str(run_root)},
        )

    def finalize(
        self,
        ctx: RunContext,
        task: Run,
        runs_dir: Path,
    ) -> Run:
        """Classify the worktree's final state into a publish outcome.

        The agent's branch is whatever ``git`` says is checked out in the
        worktree after the run. The daemon's publish step (in
        ``daemon.publish``) reads ``run.meta["publish_branch"]`` and
        ``run.meta["publish_status"]`` and decides whether to push,
        with what lease, and to which remote ref. Finalize itself never
        updates a non-run branch ref and never calls
        ``gitops.fast_forward_branch``.

        Worktree teardown is outcome-aware: a clean success with no
        uncommitted files tears the worktree down (the branch ref +
        traces are the durable artefact). A ``detached`` outcome or any
        untracked/unstaged files in the worktree keep it alive so the
        operator can inspect what the agent left behind.
        """
        worktree_path = Path(ctx.env_state.get("worktree_path") or ctx.cwd)
        run_branch = ctx.run_branch or worktree.run_branch_name(task.id)
        initial_branch = ctx.branch_name or run_branch

        if task.status != "done":
            task.save(runs_dir)
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
        task.save(runs_dir)

        if outcome.keep_worktree:
            print(
                f"[brnrd] run {task.id}: keeping worktree at {worktree_path} "
                f"({outcome.keep_reason})"
            )
            return task

        if worktree.has_uncommitted_changes(worktree_path):
            print(
                f"[brnrd] run {task.id}: keeping worktree at {worktree_path} "
                "(uncommitted changes left behind)"
            )
            return task

        worktree.remove(
            ctx.repo_root, task.id,
            branch=run_branch,
            delete_branch=outcome.delete_run_branch,
            force=True,
        )
        if outcome.delete_unused_initial:
            self._delete_unused_initial_branch(ctx.repo_root, run_branch)
        elif outcome.publish_branch and run_branch != outcome.publish_branch:
            # run_branch is a throwaway placeholder (brr/<run-id>) that
            # was switched away from before the agent ran; it won't be
            # pushed, so clean it up now.
            self._delete_unused_initial_branch(ctx.repo_root, run_branch)
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
          ``status=nothing``, no publish branch, delete the run branch
          along with the worktree.
        - final branch has commits, agent stayed on the starting branch:
          ``status=ready``, publish the starting branch. Worktree is torn
          down unless it has uncommitted leftovers.
        - final branch has commits, agent switched branches:
          ``status=ready``, publish the new branch. Worktree is torn
          down unless it has uncommitted leftovers; the unused
          ``brr/<run-id>`` placeholder is best-effort deleted.
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

        if not worktree.has_commits_beyond(worktree_path, plan.seed_ref):
            return _FinalizeOutcome(
                status="nothing",
                publish_branch=None,
                delete_run_branch=final_branch == initial_branch,
                delete_unused_initial=final_branch != initial_branch,
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
        """Best-effort delete of the throwaway ``brr/<run-id>`` placeholder.

        Called when the agent ended on a different branch, so the run
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
                print(f"[brnrd] warning: could not delete {branch}: {detail}")


@dataclass
class _FinalizeOutcome:
    """Classification produced by ``WorktreeEnv._resolve_outcome``.

    ``status`` mirrors ``run.meta["publish_status"]``: one of
    ``ready``, ``nothing``, or ``detached``. ``conflict`` is owned by
    the daemon's publish step — finalize never produces it because the
    env layer no longer touches non-run refs.
    """

    status: str
    publish_branch: str | None
    keep_worktree: bool = False
    keep_reason: str = ""
    delete_run_branch: bool = False
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
    # directly; Docker runs can additionally receive a resolved fallback
    # credential (see _resolve_docker_github_token).
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


def _resolve_docker_github_token(brr_dir: Path) -> str | None:
    """Resolve the GitHub token a Docker runner should publish with.

    An explicit ``GH_TOKEN`` is the publishing-identity override and therefore
    wins over the local GitHub gate's stored ingress token.  Without it, the
    gate token remains the most specific credential, followed by the legacy
    ``GITHUB_TOKEN`` env and the host gh account.
    """
    token = os.environ.get("GH_TOKEN")
    if token and token.strip():
        return token.strip()
    # The container gets a copy of this token for the whole run and cannot
    # come back for a newer one — top it up before handing it over. Silent
    # and best-effort; see runner._ensure_publishing_token_fresh.
    try:
        from ..gates import cloud

        cloud.ensure_publishing_credential_fresh(brr_dir)
    except Exception:
        pass
    token = os.environ.get("BRNRD_MANAGED_GITHUB_TOKEN")
    if token and token.strip():
        return token.strip()
    state_path = brr_dir / "gates" / "github.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            stored = state.get("token")
            if isinstance(stored, str) and stored.strip():
                return stored.strip()
        except Exception:
            pass
    token = os.environ.get("GITHUB_TOKEN")
    if token and token.strip():
        return token.strip()
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


def _docker_passthrough_env_args(
    cfg: dict[str, Any],
    exclude: frozenset[str] = frozenset(),
) -> list[str]:
    """Build ``-e NAME`` args for env vars that are set on the daemon."""
    seen: set[str] = set()
    args: list[str] = []
    for name in (*_DOCKER_DEFAULT_PASSTHROUGH_ENV, *_docker_extra_env_keys(cfg)):
        if not name or name in seen or name in exclude:
            continue
        seen.add(name)
        if name == "GITHUB_TOKEN" and (
            os.environ.get("GH_TOKEN") or os.environ.get("BRNRD_MANAGED_GITHUB_TOKEN")
        ):
            continue
        if os.environ.get(name):
            args.extend(["-e", name])
    return args


def _brr_checkout_src(repo_root: Path) -> Path | None:
    """Return ``repo_root/src`` when the bind-mounted tree is a brr checkout.

    Runner images bake ``pip install /opt/brr`` at build time. When the
    mounted repo *is* brr (dogfooding), that install is always behind the
    live checkout — callers inject this path via ``PYTHONPATH`` so ``brr``
    CLI invocations inside the container prefer the mounted source.
    """
    package = repo_root / "src" / "brr"
    if (package / "__init__.py").is_file() and (package / "cli.py").is_file():
        return repo_root / "src"
    return None


def _docker_brr_source_env_args(repo_root: Path) -> list[str]:
    src = _brr_checkout_src(repo_root)
    if src is None:
        return []
    return ["-e", f"PYTHONPATH={src}"]


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
    repository``, CVE-2022-24765), which breaks every branch run — the
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
                "echo \"password=${GH_TOKEN:-$GITHUB_TOKEN}\"; }; f",
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
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    return None


def _docker_container_name(run_id: str, label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{run_id}-{label}").strip(".-_")
    if not slug or not slug[0].isalnum():
        slug = f"run-{slug}"
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
        task: Run,
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
            "run_id": task.id,
            "docker_image": image,
            "docker_network": _docker_cfg(cfg, "network", "bridge"),
            "docker_mount": str(repo_root),
            "docker_containers": [],
        })
        task.meta["docker_image"] = image

        self._resolve_publish_token(ctx, repo_root)

        return ctx

    def _resolve_publish_token(self, ctx: RunContext, repo_root: Path) -> None:
        # Resolve a GitHub token for every docker run, not only the
        # github-source ones. The container has no path to the host's
        # keyring or to the user's gh stored accounts, so without an
        # injected ``GITHUB_TOKEN`` the agent's ``gh`` CLI is dead and
        # ``git push`` over HTTPS to github.com falls back to anonymous
        # — which silently breaks any run that needs to look up sibling
        # PRs, read upstream issues, or open a fresh PR from a worktree
        # branch even when the run wasn't strictly triggered by the
        # github gate. Resolution prefers an explicit publishing GH_TOKEN,
        # then stored gate state, legacy GITHUB_TOKEN, and ``gh auth token``;
        # absent all four the field stays unset and the container runs with no
        # GitHub auth, same as before.  An explicit GH_TOKEN is checked first:
        # it selects the runner's publishing identity without changing the
        # local gate credential that owns ingress.
        token = _resolve_docker_github_token(repo_root / ".brr")
        if token:
            ctx.env_state["github_token"] = token
            if os.environ.get("GH_TOKEN") or os.environ.get("BRNRD_MANAGED_GITHUB_TOKEN"):
                ctx.env_state["github_token_env"] = "GH_TOKEN"

    def _network_args(self, ctx: RunContext, cfg: dict[str, Any]) -> list[str]:
        network = str(
            ctx.env_state.get("docker_network")
            or _docker_cfg(cfg, "network", "bridge")
        )
        return ["--network", network]

    def _passthrough_args(self, ctx: RunContext, cfg: dict[str, Any]) -> list[str]:
        return _docker_passthrough_env_args(cfg)

    def _cred_mount_args(self, ctx: RunContext, cfg: dict[str, Any]) -> list[str]:
        return _docker_credential_mount_args(cfg)

    def _extra_env_args(self, ctx: RunContext, cfg: dict[str, Any]) -> list[str]:
        """Additional ``-e`` args a subclass wants in the runner container."""
        return []

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
        container_name = _docker_container_name(
            str(ctx.env_state.get("run_id", "") or "run"),
            invocation.label,
        )
        containers = ctx.env_state.setdefault("docker_containers", [])
        if isinstance(containers, list):
            containers.append(container_name)
        ctx.env_state["docker_container"] = container_name

        inner_cmd = runner._build_cmd(
            invocation.selected_runner or runner_name,
            invocation.prompt,
            cfg,
        )
        command = [
            "docker", "run",
            "--name", container_name,
            *self._network_args(ctx, cfg),
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
            *self._passthrough_args(ctx, cfg),
            *_docker_brr_source_env_args(ctx.repo_root),
            *self._cred_mount_args(ctx, cfg),
            *self._extra_env_args(ctx, cfg),
            *[
                arg
                for key, value in sorted(invocation.env.items())
                for arg in ("-e", f"{key}={value}")
            ],
            # Inject the GitHub gate token when available so ``gh`` CLI
            # and HTTPS git operations are authenticated inside the
            # container regardless of whether the system keyring is
            # reachable (it isn't inside Docker).
            *(
                [
                    "-e",
                    f"{ctx.env_state.get('github_token_env', 'GITHUB_TOKEN')}="
                    f"{ctx.env_state['github_token']}",
                ]
                if ctx.env_state.get("github_token")
                and not (
                    ctx.env_state.get("github_token_env", "GITHUB_TOKEN") == "GITHUB_TOKEN"
                    and os.environ.get("GITHUB_TOKEN")
                )
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

        stdout, observed_core = runner._process_runner_stdout(
            runner_name, stdout, invocation.env
        )
        from .. import runner_select
        mismatch = runner_select.core_mismatch(
            invocation.expected_core, observed_core,
        )
        if mismatch:
            attestation_error = (
                "Core attestation failed: requested "
                f"{invocation.expected_core!r}, Shell observed {observed_core!r}"
            )
            stderr = (stderr.rstrip() + "\n" if stderr.strip() else "") + attestation_error

        if (
            invocation.response_path and returncode == 0 and mismatch is not True
            and stdout and stdout.strip()
        ):
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
            observed_core=observed_core,
            core_mismatch=mismatch,
        )
        if trace:
            result.trace_dir = runner._write_trace(result)
        else:
            result.artifacts = _artifact_records(invocation.required_artifacts)
        return result

    def finalize(
        self,
        ctx: RunContext,
        task: Run,
        runs_dir: Path,
    ) -> Run:
        task = super().finalize(ctx, task, runs_dir)
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
                task.save(runs_dir)
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
                    "[brnrd] warning: failed to remove docker container "
                    f"{container}: {detail}"
                )
        return task


def _solitary_cfg(cfg: dict[str, Any], key: str, default: str = "") -> str:
    value = cfg.get(f"solitary.{key}", cfg.get(f"solitary_{key}", default))
    return str(value).strip() if value is not None else ""


# Model-provider endpoints each Shell needs to function. This is the one
# sanctioned hole in solitary's network wall: without it no cloud runner
# completes a single turn. Entries with a leading dot allow subdomains.
# Extend per-repo with ``solitary.allow=host1,host2`` — a stale entry here
# fails *closed* (denied CONNECT, logged by the sidecar), never open.
_SOLITARY_SHELL_HOSTS: dict[str, tuple[str, ...]] = {
    "claude": (
        "api.anthropic.com",
        "console.anthropic.com",
        "statsig.anthropic.com",
        "claude.ai",
    ),
    "codex": (
        "api.openai.com",
        "auth.openai.com",
        "chatgpt.com",
        ".chatgpt.com",
    ),
    "gemini": (
        "generativelanguage.googleapis.com",
        "cloudcode-pa.googleapis.com",
        "oauth2.googleapis.com",
        "accounts.google.com",
    ),
}

# Credential paths per Shell, relative to HOME. Solitary mounts only the
# selected Shell's own state (plus ``.gitconfig`` for commit identity) —
# never ``.ssh``, never the other CLIs' tokens.
_SOLITARY_SHELL_CRED_PATHS: dict[str, tuple[str, ...]] = {
    "claude": (".claude", ".claude.json"),
    "codex": (".codex",),
    "gemini": (".gemini",),
}

_SOLITARY_PROXY_PORT = 3128


def _solitary_proxy_script() -> Path:
    from .. import data

    return Path(data.__file__).resolve().parent / "solitary_proxy.py"


class SolitaryEnv(DockerEnv):
    """The paranoid preset: docker + provider-only egress + copied creds.

    One config value (``environment=solitary``) composing the isolation
    posture SECURITY.md's hardening checklist used to spell out by hand —
    with the physics fixed: a literal ``network=none`` would brick every
    cloud runner, because the model call itself is network. Instead the
    runner joins a per-run ``--internal`` docker network whose only exit
    is a CONNECT-proxy sidecar allowlisting the run's Shell provider
    hosts (plus ``solitary.allow`` extras). TLS passes through untouched.

    What it protects against: third-party exfiltration, pushes/API calls
    from inside the run, host CLI-state poisoning (credentials are
    *copied* per run by default, so an injected agent can't edit
    ``~/.claude`` hooks into persistence), and credential theft beyond
    the selected Shell's own token. What it cannot close: content shown
    to the model provider — the conversation is a channel by design.

    Publish is unchanged: the daemon pushes from the host after
    finalize. ``github.com`` is not on the allowlist, so "no push from
    inside" holds structurally rather than by convention.

    Knobs (all optional):
    - ``solitary.network``     — ``isolated`` (default) | ``none``
      (zero egress; only for runners that need no provider API).
    - ``solitary.credentials`` — ``copy`` (default) | ``ro`` | ``none``.
    - ``solitary.allow``       — extra comma-separated hosts.
    - ``solitary.proxy_image`` — sidecar image (default: ``docker.image``;
      must carry ``python3``, which the bundled runner image does).
    """

    name = "solitary"

    def prepare(
        self,
        task: Run,
        repo_root: Path,
        cfg: dict[str, Any],
        *,
        branch_plan: branching.PublishPlan,
        response_path: Path,
        outbox_path: Path | None = None,
    ) -> RunContext:
        ctx = super().prepare(
            task,
            repo_root,
            cfg,
            branch_plan=branch_plan,
            response_path=response_path,
            outbox_path=outbox_path,
        )
        ctx.name = self.name
        mode = _solitary_cfg(cfg, "network", "isolated") or "isolated"
        if mode not in ("isolated", "none"):
            raise RuntimeError(
                f"solitary.network must be 'isolated' or 'none', got {mode!r}"
            )
        ctx.env_state["solitary_network_mode"] = mode
        task.meta["environment"] = self.name
        return ctx

    def _resolve_publish_token(self, ctx: RunContext, repo_root: Path) -> None:
        """No GitHub credential ever enters a solitary container."""

    def _invocation_shell(
        self, runner_name: str, invocation: runner.RunnerInvocation,
    ) -> str:
        selected = invocation.selected_runner
        shell = getattr(selected, "shell", "") or ""
        if shell:
            return str(shell)
        return runner_name if runner_name in _SOLITARY_SHELL_HOSTS else ""

    def _allow_hosts(self, cfg: dict[str, Any], shell: str) -> list[str]:
        hosts = list(_SOLITARY_SHELL_HOSTS.get(shell, ()))
        extra = _solitary_cfg(cfg, "allow")
        for entry in extra.split(","):
            entry = entry.strip()
            if entry and entry not in hosts:
                hosts.append(entry)
        return hosts

    def _run_docker(self, args: list[str], action: str) -> str:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, check=False,
            # Generous: ``docker run -d`` may pull the sidecar image on
            # first use; everything else here returns in seconds.
            timeout=300,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"solitary: failed to {action}: {detail}")
        return (result.stdout or "").strip()

    def _ensure_isolated_network(
        self, ctx: RunContext, cfg: dict[str, Any], shell: str,
    ) -> None:
        if ctx.env_state.get("solitary_proxy_ready"):
            return
        run_id = str(ctx.env_state.get("run_id", "") or "run")
        network = _docker_container_name(run_id, "solitary-net")
        proxy_name = _docker_container_name(run_id, "solitary-proxy")
        allow = self._allow_hosts(cfg, shell)
        if not allow:
            print(
                "[brnrd] solitary: no provider hosts known for shell "
                f"{shell!r} and no solitary.allow configured — the runner "
                "will have zero egress"
            )
        image = (
            _solitary_cfg(cfg, "proxy_image")
            or str(ctx.env_state.get("docker_image") or _docker_cfg(cfg, "image"))
        )
        script = _solitary_proxy_script()
        self._run_docker(
            ["network", "create", "--internal", network],
            f"create internal network {network}",
        )
        ctx.env_state["solitary_network"] = network
        ctx.env_state["docker_network"] = network
        try:
            self._run_docker(
                [
                    "run", "-d",
                    "--name", proxy_name,
                    "--network", network,
                    "-e", f"BRR_SOLITARY_ALLOW={','.join(allow)}",
                    "-e", f"BRR_SOLITARY_PORT={_SOLITARY_PROXY_PORT}",
                    "-v", f"{script}:/brr-solitary-proxy.py:ro",
                    image,
                    "python3", "/brr-solitary-proxy.py",
                ],
                f"start proxy sidecar {proxy_name}",
            )
            containers = ctx.env_state.setdefault("docker_containers", [])
            if isinstance(containers, list):
                containers.append(proxy_name)
            # Second leg: the bridge, so the sidecar can reach the
            # allowlisted providers. The runner container never joins it.
            self._run_docker(
                ["network", "connect", "bridge", proxy_name],
                f"connect proxy {proxy_name} to bridge",
            )
        except RuntimeError:
            # Don't orphan the half-built pieces when the sidecar can't
            # come up — the invoke fails loudly either way.
            subprocess.run(
                ["docker", "rm", "-f", proxy_name],
                capture_output=True, text=True, timeout=30, check=False,
            )
            subprocess.run(
                ["docker", "network", "rm", network],
                capture_output=True, text=True, timeout=30, check=False,
            )
            ctx.env_state.pop("solitary_network", None)
            raise
        ctx.env_state["solitary_proxy"] = proxy_name
        ctx.env_state["solitary_proxy_ready"] = True

    def _credential_stage_dir(self, ctx: RunContext) -> Path:
        # Deliberately *outside* the repo's ``.brr``: that directory rides
        # the repo bind mount into every container, so a stage under it
        # would let a concurrent sibling container read this run's copied
        # tokens. A private tmp dir (0700 via mkdtemp) is bound into only
        # this run's container and is invisible to every other one.
        import tempfile

        run_id = str(ctx.env_state.get("run_id", "") or "run")
        root = tempfile.mkdtemp(prefix=f"brr-solitary-{run_id}-")
        return Path(root) / "home"

    def _ensure_credential_copies(
        self, ctx: RunContext, cfg: dict[str, Any], shell: str,
    ) -> None:
        if "solitary_cred_stage" in ctx.env_state:
            return
        stage = self._credential_stage_dir(ctx)
        stage.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(stage, 0o700)
        except OSError:
            pass
        home = Path(os.path.expanduser("~"))
        copied: list[str] = []
        for rel in (*_SOLITARY_SHELL_CRED_PATHS.get(shell, ()), ".gitconfig"):
            src = home / rel
            dst = stage / rel
            if not src.exists():
                continue
            try:
                if src.is_dir():
                    shutil.copytree(src, dst, symlinks=True)
                else:
                    shutil.copy2(src, dst)
            except OSError as exc:
                print(
                    f"[brnrd] solitary: could not stage credential copy "
                    f"{rel}: {exc}"
                )
                continue
            copied.append(rel)
        ctx.env_state["solitary_cred_stage"] = str(stage)
        ctx.env_state["solitary_cred_paths"] = copied

    def invoke(
        self,
        ctx: RunContext,
        runner_name: str,
        invocation: runner.RunnerInvocation,
        cfg: dict[str, Any],
        *,
        trace: bool = False,
    ) -> runner.RunnerResult:
        shell = self._invocation_shell(runner_name, invocation)
        ctx.env_state["solitary_shell"] = shell
        if ctx.env_state.get("solitary_network_mode") != "none":
            self._ensure_isolated_network(ctx, cfg, shell)
        cred_mode = _solitary_cfg(cfg, "credentials", "copy") or "copy"
        if cred_mode not in ("copy", "ro", "none"):
            raise RuntimeError(
                "solitary.credentials must be 'copy', 'ro', or 'none', "
                f"got {cred_mode!r}"
            )
        ctx.env_state["solitary_cred_mode"] = cred_mode
        if cred_mode == "copy":
            self._ensure_credential_copies(ctx, cfg, shell)
        return super().invoke(ctx, runner_name, invocation, cfg, trace=trace)

    def _network_args(self, ctx: RunContext, cfg: dict[str, Any]) -> list[str]:
        if ctx.env_state.get("solitary_network_mode") == "none":
            return ["--network", "none"]
        return ["--network", str(ctx.env_state["solitary_network"])]

    def _passthrough_args(self, ctx: RunContext, cfg: dict[str, Any]) -> list[str]:
        # Model API keys still pass (API-key auth must work); the GitHub
        # publishing identity never does.
        return _docker_passthrough_env_args(
            cfg, exclude=frozenset({"GITHUB_TOKEN", "GH_TOKEN"}),
        )

    def _cred_mount_args(self, ctx: RunContext, cfg: dict[str, Any]) -> list[str]:
        mode = str(ctx.env_state.get("solitary_cred_mode", "copy"))
        if mode == "none":
            return []
        shell = str(ctx.env_state.get("solitary_shell", ""))
        rels = (*_SOLITARY_SHELL_CRED_PATHS.get(shell, ()), ".gitconfig")
        args: list[str] = []
        if mode == "copy":
            stage = Path(str(ctx.env_state.get("solitary_cred_stage", "")))
            for rel in ctx.env_state.get("solitary_cred_paths", []) or []:
                args.extend(
                    ["-v", f"{stage / rel}:{_DOCKER_CONTAINER_HOME}/{rel}"]
                )
            return args
        home = Path(os.path.expanduser("~"))
        for rel in rels:
            host = home / rel
            if host.exists():
                args.extend(
                    ["-v", f"{host}:{_DOCKER_CONTAINER_HOME}/{rel}:ro"]
                )
        return args

    def _extra_env_args(self, ctx: RunContext, cfg: dict[str, Any]) -> list[str]:
        proxy = ctx.env_state.get("solitary_proxy")
        if not proxy:
            return []
        url = f"http://{proxy}:{_SOLITARY_PROXY_PORT}"
        args: list[str] = []
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            args.extend(["-e", f"{name}={url}"])
        for name in ("NO_PROXY", "no_proxy"):
            args.extend(["-e", f"{name}=localhost,127.0.0.1"])
        return args

    def finalize(
        self,
        ctx: RunContext,
        task: Run,
        runs_dir: Path,
    ) -> Run:
        # Credential copies hold live tokens — delete them regardless of
        # outcome, before the container/network bookkeeping.
        stage_raw = ctx.env_state.get("solitary_cred_stage")
        if stage_raw:
            shutil.rmtree(
                Path(str(stage_raw)).parent, ignore_errors=True,
            )
        proxy = ctx.env_state.get("solitary_proxy")
        if task.status != "done" and proxy:
            # The runner container is preserved for inspection; stop the
            # sidecar so a failed run doesn't leave a live proxy attached
            # to the bridge, but keep it so ``docker logs`` still answers
            # "what did the run try to reach".
            subprocess.run(
                ["docker", "stop", str(proxy)],
                capture_output=True, text=True, timeout=30, check=False,
            )
        task = super().finalize(ctx, task, runs_dir)
        network = ctx.env_state.get("solitary_network")
        if network and task.status == "done":
            result = subprocess.run(
                ["docker", "network", "rm", str(network)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                print(
                    "[brnrd] warning: failed to remove solitary network "
                    f"{network}: {detail}"
                )
        return task


_BUILTINS: dict[str, type[EnvBackend]] = {
    "docker": DockerEnv,
    "host": HostEnv,
    "solitary": SolitaryEnv,
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
