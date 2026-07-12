"""Repository adoption — ``brnrd init``.

Sets up the ``.brr/`` runtime directory, detects a runner, and
delegates AGENTS.md + kb/ creation to the runner itself. The runner
receives ``setup.md`` plus brr's own ``AGENTS.md`` (the model) as a
prompt and decides what work (if any) is needed based on the repo's
current state.

This module is intentionally thin — the intelligence lives in the
prompt files, not here.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config as conf
from . import dominion
from . import gitops
from . import prompts
from . import runner


_DEFAULT_DOCKER_IMAGE = "brr-runner:local"
_BUNDLED_DOCKERFILE = Path(__file__).resolve().parent / "Dockerfile"


# ── Timed input helper ──────────────────────────────────────────────


def _timed_input(prompt: str, default: str, timeout: int = 10) -> str:
    """Read a line from stdin with a timeout, returning *default* on expiry.

    Uses ``signal.SIGALRM`` (Unix-only, but brr already requires Unix).
    Falls back to a plain ``input()`` if SIGALRM is unavailable.
    """
    if not hasattr(signal, "SIGALRM"):
        return input(prompt) or default

    def _alarm(signum, frame):
        raise TimeoutError

    old = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(timeout)
    try:
        value = input(prompt)
        signal.alarm(0)
        return value.strip() or default
    except (TimeoutError, EOFError):
        signal.alarm(0)
        print(f"\n[brnrd] no input — using default: {default}")
        return default
    finally:
        signal.signal(signal.SIGALRM, old)


def _pick_option(
    label: str,
    options: list[str],
    default: str,
    timeout: int = 10,
) -> str:
    """Present numbered options and return the chosen one."""
    print(f"\n  {label}")
    for i, opt in enumerate(options, 1):
        marker = " ←" if opt == default else ""
        print(f"    {i}) {opt}{marker}")
    choice = _timed_input(
        f"  choice [default: {default}] ({timeout}s): ",
        default,
        timeout,
    )
    # accept by number or by name
    try:
        idx = int(choice)
        if 1 <= idx <= len(options):
            return options[idx - 1]
    except ValueError:
        pass
    if choice in options:
        return choice
    print(f"  [brnrd] unrecognised — using default: {default}")
    return default


def _confirm(label: str, default: bool = True, timeout: int = 10) -> bool:
    """Yes/no confirmation with timeout."""
    hint = "Y/n" if default else "y/N"
    choice = _timed_input(
        f"  {label} [{hint}] ({timeout}s): ",
        "y" if default else "n",
        timeout,
    )
    return choice.lower() in ("y", "yes", "")


# ── Init ────────────────────────────────────────────────────────────


def init_repo(url: str | None = None, *, interactive: bool = False) -> None:
    """Initialize a repository for brr management."""
    if url:
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        print(f"[brnrd] cloning {url}")
        subprocess.run(["git", "clone", url, name], check=True)
        os.chdir(name)

    repo_root = _ensure_repo()
    _setup_brr_dir(repo_root)
    _bootstrap_dominion(repo_root)

    available = runner.detect_all_runners(repo_root)
    if not available:
        raise SystemExit(
            "[brnrd] no runner found on PATH (claude, codex, gemini).\n"
            "       Install one and re-run `brnrd init`."
        )

    if interactive and sys.stdin.isatty():
        runner_name, cfg_overrides = _interactive_configure(available)
    else:
        runner_name = available[0]
        cfg_overrides = {}

    print(f"[brnrd] runner: {runner_name}")

    if cfg_overrides:
        cfg = conf.load_config(repo_root)
        cfg.update(cfg_overrides)
        conf.write_config(repo_root, cfg)

    _run_setup(runner_name, repo_root)
    _verify(repo_root)


def _interactive_configure(available: list[str]) -> tuple[str, dict]:
    """Ask the user a few setup questions. Returns (runner, config_overrides)."""
    print("[brnrd] interactive setup")
    cfg: dict = {}

    if len(available) == 1:
        runner_name = available[0]
        print(f"\n  runner: {runner_name} (only one found)")
    else:
        runner_name = _pick_option("Which runner?", available, available[0])

    cfg["runner"] = runner_name
    cfg.update(_configure_environment())

    print()
    return runner_name, cfg


def _configure_environment() -> dict:
    """Resolve the task execution environment.

    The ``environment=auto`` default (set by ``_setup_brr_dir``) silently
    falls back to worktree when docker isn't fully configured, which is
    surprising. Interactive setup makes the choice explicit so the
    config records what the user actually picked.
    """
    if shutil.which("docker") is None:
        print("\n  docker: not on PATH — using worktree environment")
        return {"environment": "worktree"}

    print()
    if not _confirm("Use Docker for task execution?", default=True):
        return {"environment": "worktree"}

    image = _timed_input(
        f"  docker image [default: {_DEFAULT_DOCKER_IMAGE}] (10s): ",
        _DEFAULT_DOCKER_IMAGE,
        timeout=10,
    )
    if image.strip().lower() in {"y", "yes", "n", "no"}:
        image = _DEFAULT_DOCKER_IMAGE
    overrides: dict = {"environment": "docker", "docker.image": image}

    if image == _DEFAULT_DOCKER_IMAGE and _BUNDLED_DOCKERFILE.exists():
        if _confirm(
            "Build the image now from brr's bundled Dockerfile?",
            default=True,
        ):
            built = _build_default_docker_image()
            if not built:
                print(
                    f"  [brnrd] image not built — brnrd will fail until "
                    f"`{_DEFAULT_DOCKER_IMAGE}` exists locally."
                )

    return overrides


def _build_default_docker_image() -> bool:
    """Build brr's bundled runner image into ``brr-runner:local``.

    Copies the current checkout's packaging tree into a temp build context
    so the Dockerfile can ``pip install /opt/brr`` from source. Never
    ``pip install brr`` from PyPI — that name is an unrelated terminal
    image renderer. Returns True iff the build succeeded.
    """
    if not _BUNDLED_DOCKERFILE.exists():
        print("  [brnrd] bundled Dockerfile not found; cannot build")
        return False

    repo_root = Path(__file__).resolve().parent.parent.parent
    pyproject = repo_root / "pyproject.toml"
    readme = repo_root / "README.md"
    src = repo_root / "src"
    if not pyproject.is_file() or not readme.is_file() or not src.is_dir():
        print("  [brnrd] checkout layout incomplete; cannot build runner image")
        return False

    print(
        f"  [brnrd] building {_DEFAULT_DOCKER_IMAGE} "
        "(this can take a few minutes)…"
    )
    with tempfile.TemporaryDirectory(prefix="brr-build-") as ctx:
        ctx_path = Path(ctx)
        shutil.copy(_BUNDLED_DOCKERFILE, ctx_path / "Dockerfile")
        shutil.copy(pyproject, ctx_path / "pyproject.toml")
        shutil.copy(readme, ctx_path / "README.md")
        shutil.copytree(src, ctx_path / "src")
        result = subprocess.run(
            ["docker", "build", "-t", _DEFAULT_DOCKER_IMAGE, str(ctx_path)],
            check=False,
        )
    if result.returncode != 0:
        print(f"  [brnrd] docker build failed (exit {result.returncode})")
        return False
    print(f"  [brnrd] image ready: {_DEFAULT_DOCKER_IMAGE}")
    return True


def _ensure_repo() -> Path:
    """Ensure we're in a git repo, initializing one if needed."""
    try:
        return gitops.ensure_git_repo()
    except (RuntimeError, SystemExit):
        print("[brnrd] not a git repo — running git init")
        subprocess.run(["git", "init"], check=True)
        return gitops.ensure_git_repo()


def _setup_brr_dir(repo_root: Path) -> None:
    """Create ``.brr/`` structure and update .gitignore."""
    brr = repo_root / ".brr"
    for sub in (
        "inbox",       # incoming event files
        "responses",   # per-event response files
        "gates",       # gate state (telegram.json, slack.json, …)
        "prompts",     # user overrides for bundled prompt templates
        "runs",        # per-run manifests, prompts, contexts, and history
        "traces",      # runner invocation traces (prompt/stdout/stderr/meta)
        "reviews",     # review artifacts produced by agents
        "worktrees",   # git worktrees for run-isolated execution
    ):
        (brr / sub).mkdir(parents=True, exist_ok=True)

    config_path = brr / "config"
    if not config_path.exists():
        conf.write_config(repo_root, {
            "runner": "auto",
            "environment": "auto",
            "response_retries": 1,
            "dominion.enabled": True,
            "dominion.branch": dominion.DEFAULT_BRANCH,
            "dominion.inject_budget_bytes": dominion.DEFAULT_INJECT_BUDGET_BYTES,
            "schedule.enabled": True,
            # Co-development aid (off by default): when on, every wake
            # invites the agent to inspect the shape of its own injected
            # context and raise improvements with you. See
            # kb/design-context-introspection.md.
            "introspect.enabled": False,
        })

    gi = repo_root / ".gitignore"
    marker = ".brr/"
    if gi.exists():
        text = gi.read_text(encoding="utf-8")
        if marker not in text:
            with gi.open("a", encoding="utf-8") as f:
                f.write(f"\n# brr runtime\n{marker}\n")
    else:
        gi.write_text(f"# brr runtime\n{marker}\n", encoding="utf-8")

    print("[brnrd] .brr/ directory ready")


def _bootstrap_dominion(repo_root: Path) -> None:
    """Create the agent's dominion branch + worktree at init (best-effort).

    The daemon also ensures this on every boot (idempotent), so a failure
    here — no committer identity yet, no write access to push — is a soft
    skip, not a fatal init error.
    """
    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return
    branch = str(cfg.get(
        "dominion.branch", cfg.get("dominion_branch", dominion.DEFAULT_BRANCH),
    ))
    try:
        path = dominion.ensure_dominion(repo_root, branch=branch)
        print(f"[brnrd] dominion ready: {path} (branch {branch})")
    except Exception as exc:  # noqa: BLE001
        print(f"[brnrd] dominion setup skipped: {exc}")


def _run_setup(runner_name: str, repo_root: Path) -> None:
    """Call the runner with the init prompt to create AGENTS.md + kb/."""
    prompt = prompts.build_init_prompt(repo_root)
    cfg = conf.load_config(repo_root)
    invocation = runner.RunnerInvocation(
        kind="init",
        label="setup",
        prompt=prompt,
        cwd=repo_root,
        repo_root=repo_root,
        required_artifacts=[
            runner.RunnerArtifactSpec(repo_root / "AGENTS.md", "AGENTS.md"),
            runner.RunnerArtifactSpec(repo_root / "kb" / "index.md", "kb/index.md"),
            runner.RunnerArtifactSpec(repo_root / "kb" / "log.md", "kb/log.md"),
        ],
    )

    print("[brnrd] running setup...")
    result = runner.invoke_runner(runner_name, invocation, cfg=cfg)
    try:
        result.raise_for_error()
    except RuntimeError as e:
        print(f"[brnrd] setup failed: {e}")
        print("[brnrd] re-run `brnrd init` to retry")
        raise SystemExit(1)
    if not result.validation_ok:
        missing = ", ".join(artifact.label for artifact in result.missing_artifacts)
        print(f"[brnrd] setup failed: missing required output(s): {missing}")
        print("[brnrd] re-run `brnrd init` to retry")
        raise SystemExit(1)
    if result.output.strip():
        print(result.output)


def _verify(repo_root: Path) -> None:
    """Check that the runner created the expected files."""
    agents = repo_root / "AGENTS.md"
    kb_index = repo_root / "kb" / "index.md"
    kb_log = repo_root / "kb" / "log.md"

    ok = True
    for path, label in [(agents, "AGENTS.md"), (kb_index, "kb/index.md"), (kb_log, "kb/log.md")]:
        if path.exists():
            print(f"[brnrd] ✓ {label}")
        else:
            print(f"[brnrd] ✗ {label} missing — the runner may not have created it")
            ok = False

    if ok:
        print("[brnrd] init complete")
    else:
        print("[brnrd] init incomplete — re-run `brnrd init` to retry")
