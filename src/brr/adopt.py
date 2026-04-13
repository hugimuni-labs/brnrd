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
import subprocess
from pathlib import Path

from . import config as conf
from . import runner
from . import gitops


def init_repo(url: str | None = None) -> None:
    """Initialize a repository for brr management."""
    if url:
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        print(f"[brr] cloning {url}")
        subprocess.run(["git", "clone", url, name], check=True)
        os.chdir(name)

    repo_root = _ensure_repo()
    _setup_brr_dir(repo_root)

    runner_name = runner.detect_runner(repo_root)
    if not runner_name:
        raise SystemExit(
            "[brr] no runner found on PATH (claude, codex, gemini).\n"
            "       Install one and re-run `brr init`."
        )
    print(f"[brr] detected runner: {runner_name}")

    _run_setup(runner_name, repo_root)
    _verify(repo_root)


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
    for sub in ("inbox", "responses", "gates", "prompts"):
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
