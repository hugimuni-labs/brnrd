"""Repository adoption — ``brr init``.

Sets up the ``.brr/`` runtime directory, detects a runner, and
delegates AGENTS.md + kb/ creation to the runner itself.  The runner
receives setup.md + agents-template.md as a prompt and decides what
work (if any) is needed based on the repo's current state.

This module is intentionally thin — the intelligence lives in the
prompt files, not here.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from . import config as conf
from . import runner
from . import gitops


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
        print(f"\n[brr] no input — using default: {default}")
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
    print(f"  [brr] unrecognised — using default: {default}")
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
        print(f"[brr] cloning {url}")
        subprocess.run(["git", "clone", url, name], check=True)
        os.chdir(name)

    repo_root = _ensure_repo()
    _setup_brr_dir(repo_root)

    available = runner.detect_all_runners(repo_root)
    if not available:
        raise SystemExit(
            "[brr] no runner found on PATH (claude, codex, gemini).\n"
            "       Install one and re-run `brr init`."
        )

    if interactive and sys.stdin.isatty():
        runner_name, cfg_overrides = _interactive_configure(available)
    else:
        runner_name = available[0]
        cfg_overrides = {}

    print(f"[brr] runner: {runner_name}")

    if cfg_overrides:
        cfg = conf.load_config(repo_root)
        cfg.update(cfg_overrides)
        conf.write_config(repo_root, cfg)

    _run_setup(runner_name, repo_root)
    _verify(repo_root)


def _interactive_configure(available: list[str]) -> tuple[str, dict]:
    """Ask the user a few setup questions. Returns (runner, config_overrides)."""
    print("[brr] interactive setup")
    cfg: dict = {}

    # runner
    if len(available) == 1:
        runner_name = available[0]
        print(f"\n  runner: {runner_name} (only one found)")
    else:
        runner_name = _pick_option("Which runner?", available, available[0])

    cfg["runner"] = runner_name

    # auto-approve
    auto = _confirm("Allow the runner to run without approval prompts?", default=True)
    cfg["auto_approve"] = auto

    print()
    return runner_name, cfg


def _ensure_repo() -> Path:
    """Ensure we're in a git repo, initializing one if needed."""
    try:
        return gitops.ensure_git_repo()
    except (RuntimeError, SystemExit):
        print("[brr] not a git repo — running git init")
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
        "tasks",       # persisted Task files (task-*.md)
        "traces",      # runner invocation traces (prompt/stdout/stderr/meta)
        "reviews",     # review artifacts produced by agents
        "worktrees",   # git worktrees for task-isolated execution
    ):
        (brr / sub).mkdir(parents=True, exist_ok=True)

    config_path = brr / "config"
    if not config_path.exists():
        conf.write_config(repo_root, {
            "runner": "auto",
            "auto_approve": True,
            "response_retries": 1,
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

    print("[brr] .brr/ directory ready")


def _run_setup(runner_name: str, repo_root: Path) -> None:
    """Call the runner with the init prompt to create AGENTS.md + kb/."""
    prompt = runner.build_init_prompt(repo_root)
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

    print("[brr] running setup...")
    result = runner.invoke_runner(runner_name, invocation, cfg=cfg)
    try:
        result.raise_for_error()
    except RuntimeError as e:
        print(f"[brr] setup failed: {e}")
        print("[brr] re-run `brr init` to retry")
        raise SystemExit(1)
    if not result.validation_ok:
        missing = ", ".join(artifact.label for artifact in result.missing_artifacts)
        print(f"[brr] setup failed: missing required output(s): {missing}")
        print("[brr] re-run `brr init` to retry")
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
            print(f"[brr] ✓ {label}")
        else:
            print(f"[brr] ✗ {label} missing — the runner may not have created it")
            ok = False

    if ok:
        print("[brr] init complete")
    else:
        print("[brr] init incomplete — re-run `brr init` to retry")
