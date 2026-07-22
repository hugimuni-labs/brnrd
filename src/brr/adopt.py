"""Repository adoption — ``brnrd init``.

Sets up the ``.brr/`` runtime directory, detects a runner, and delegates
the repository contract to the runner itself. The runner receives
``setup.md`` plus the host-agnostic adopter template
(``templates/constitution.md``, *not* brr's own playbook) and tailors it
to the repo. brnrd then writes a shell bridge (``CLAUDE.md``) for every
detected shell that needs one and verifies the contract is structurally sound
and reachable from each — see ``constitution`` for both mechanics.

The adopter's knowledge shape (committed ``kb/`` vs account home) is asked,
not defaulted, and asked before the contract is authored.

This module is intentionally thin — the intelligence lives in the
prompt files and ``constitution``, not here.
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
from . import constitution
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
            "[brnrd] no runner found on PATH (claude, codex).\n"
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

    # D2: the adopter's knowledge shape is *asked*, not defaulted — and it is
    # asked *before* the contract is written, so setup authors the shape the
    # user actually chose instead of committing repo-`kb/` and discovering the
    # mismatch only when home-linking is offered afterwards.
    knowledge_shape = _resolve_knowledge_shape(interactive)

    _run_setup(runner_name, repo_root, knowledge_shape=knowledge_shape)

    # L2: every *detected* shell gets a bridge to the contract, not only the
    # configured runner — the drop-in audience switches tools, and a bare
    # A Claude session in an adopted repo is otherwise never told
    # AGENTS.md exists.
    shells = _detect_shells()
    written = constitution.write_bridges(repo_root, shells)
    if written:
        print(f"[brnrd] shell bridges written: {', '.join(sorted(written))}")

    _verify(repo_root, knowledge_shape=knowledge_shape, shells=shells)

    if interactive and sys.stdin.isatty():
        _narrate_home_repos(repo_root)
        _offer_home_link(repo_root)


# shell -> the CLI binary that reveals it on PATH. Bridges are written for
# whichever of these are present, independent of the runner brnrd itself
# uses. Cursor and Codex read ``AGENTS.md`` natively (no bridge file), but
# detecting them still drives the reachability report.
_SHELL_BINARIES: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "cursor": "cursor-agent",
}


def _detect_shells() -> list[str]:
    """Shells whose CLI is on PATH, in a stable order."""
    return [s for s, binary in _SHELL_BINARIES.items() if shutil.which(binary)]


def _resolve_knowledge_shape(interactive: bool) -> str:
    """Resolve the adopter's kb architecture — ``"repo"`` or ``"home"``.

    Asked in an interactive TTY session (D2); a non-interactive install can't
    ask, so it takes the portable committed-``kb/`` shape as its
    backward-compatible default. Either way the answer is no longer *implied*
    by which artifacts init happens to hard-require.
    """
    if interactive and sys.stdin.isatty():
        choice = _pick_option(
            "Where should this repo's knowledge base live?",
            [
                "repo — a committed kb/ directory, portable and git-native",
                "home — private brnrd account knowledge (needs a connected account)",
            ],
            "repo — a committed kb/ directory, portable and git-native",
        )
        return "home" if choice.startswith("home") else "repo"
    return "repo"


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


def _narrate_home_repos(repo_root: Path) -> None:
    """Name the two repos init just brought into being, and where.

    Runs *before* — and independently of — the home-link question, so the
    user hears the facts even when linking is declined or ``gh`` is
    missing (design-repo-birth-ceremony.md: the ceremony decorates seams
    the user is already standing at; it never becomes a consent gate).
    Best-effort: narration must never fail init.
    """
    from . import account

    try:
        ctx = account.resolve_context(repo_root, conf.load_config(repo_root))
        knowledge_root = account.knowledge_path(ctx)
    except Exception:  # noqa: BLE001 — narration is decoration, never a failure
        return

    print()
    print("[brnrd] two repos now hold what this resident is — both yours:")
    print(f"  memory    → {ctx.dominion_repo}")
    print("              the dominion: the agent's working memory; it commits")
    print("              here after every thought")
    suffix = "" if knowledge_root.exists() else "  (created on first use)"
    print(f"  knowledge → {knowledge_root}{suffix}")
    print("              the pages your projects teach it")
    print("  Plain git repos on this machine; each carries a README deed —")
    print("  what it is, who writes it, where it lives, and how to leave.")


def _offer_home_link(repo_root: Path) -> None:
    """Ask the single git-durability question, then wire both home repos.

    Unification, not a second setup flow: one question covers the
    dominion (memory) and knowledge repos in one shot — see
    ``home_link.link_home``. Skipped entirely, with no question asked,
    when ``gh`` isn't on PATH: init must never depend on ``gh`` for its
    own success, and asking a question whose only answer is "can't" is
    the exact user-fatigue this brief warned against.
    """
    from . import home_link

    if not home_link.gh_available():
        return

    print()
    if not _confirm(
        "Back up the agent's memory and knowledge base to private GitHub repos?",
        default=True,
    ):
        return

    try:
        results = home_link.link_home(repo_root, conf.load_config(repo_root))
    except home_link.HomeLinkError as exc:
        print(f"[brnrd] git durability setup skipped: {exc}")
        return

    for result in results:
        state = "pushed" if result.pushed else "already up to date"
        print(f"[brnrd] {result.slot}: {result.action} → {result.remote_url} ({state})")


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
        from . import retention

        conf.write_config(repo_root, {
            "runner": "auto",
            "environment": "auto",
            "response_retries": 1,
            "dominion.enabled": True,
            "dominion.branch": dominion.DEFAULT_BRANCH,
            "dominion.inject_budget_bytes": dominion.DEFAULT_INJECT_BUDGET_BYTES,
            "schedule.enabled": True,
            # Retention windows (#501): fresh installs get finite windows;
            # a pre-existing config is never touched, so existing installs
            # keep today's keep-forever behavior until they opt in.
            **retention.FRESH_INSTALL_DEFAULTS,
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


def _run_setup(
    runner_name: str, repo_root: Path, *, knowledge_shape: str = "repo"
) -> None:
    """Call the runner with the init prompt to author the contract.

    The one hard required artifact is ``AGENTS.md`` — the repository
    contract every shell rests on. The committed ``kb/`` files are **not**
    hard-required: they only apply to the committed-``kb/`` knowledge shape,
    and gating the whole install on an architecture the adopter may have
    declined (or that a connected account replaces) is the very failure this
    layer removes. Their presence is a soft check in :func:`_verify`.
    """
    prompt = prompts.build_init_prompt(repo_root, knowledge_shape=knowledge_shape)
    cfg = conf.load_config(repo_root)
    invocation = runner.RunnerInvocation(
        kind="init",
        label="setup",
        prompt=prompt,
        cwd=repo_root,
        repo_root=repo_root,
        required_artifacts=[
            runner.RunnerArtifactSpec(repo_root / "AGENTS.md", "AGENTS.md"),
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

    # Structure, not mere existence: an AGENTS.md the runner wrote but left
    # empty (or without the universal sections) passes the file-exists gate
    # yet is not a usable contract. Only checked when a real file landed —
    # a mocked runner that asserts the artifact without writing it is a test
    # fixture, not a production path.
    agents = repo_root / "AGENTS.md"
    if agents.exists():
        problems = _agents_structure_problems(agents)
        if problems:
            print(
                "[brnrd] setup failed: AGENTS.md is present but incomplete "
                f"({'; '.join(problems)})"
            )
            print("[brnrd] re-run `brnrd init` to retry")
            raise SystemExit(1)

    if result.output.strip():
        print(result.output)


# Universal section anchors an authored AGENTS.md must carry to count as a
# usable contract. Matched leniently (heading text or a block id) so a
# repo that renamed a heading slightly still passes, but an empty or
# truncated file does not.
_REQUIRED_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("## Stewardship", "id=stewardship"),
    ("## Knowledge base", "id=knowledge"),
    ("## Guardrails", "id=guardrails"),
)


def _agents_structure_problems(path: Path) -> list[str]:
    """Return human-readable reasons *path* is not a usable contract, or []."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"unreadable ({exc})"]
    problems: list[str] = []
    if len(text.strip()) < 200:
        problems.append("file is essentially empty")
    for anchors in _REQUIRED_SECTIONS:
        if not any(a in text for a in anchors):
            problems.append(f"missing the {anchors[0]!r} section")
    return problems


def _verify(
    repo_root: Path,
    *,
    knowledge_shape: str = "repo",
    shells: list[str] | None = None,
) -> None:
    """Report the installed contract's health — structure + reachability.

    Existence checks alone let an empty AGENTS.md and an unbridged Claude
    session pass. This verifies the contract is structurally usable, that
    each detected shell can actually *reach* it, and that the chosen
    knowledge shape's files are present (a soft note for committed-``kb/``,
    never a hard gate).
    """
    shells = shells or []
    agents = repo_root / "AGENTS.md"

    ok = True
    if agents.exists():
        problems = _agents_structure_problems(agents)
        if problems:
            print(f"[brnrd] ⚠ AGENTS.md incomplete: {'; '.join(problems)}")
            ok = False
        else:
            print("[brnrd] ✓ AGENTS.md")
    else:
        print("[brnrd] ✗ AGENTS.md missing — the runner may not have created it")
        ok = False

    if knowledge_shape == "repo":
        for label in ("kb/index.md", "kb/log.md"):
            if (repo_root / label).exists():
                print(f"[brnrd] ✓ {label}")
            else:
                print(f"[brnrd] · {label} not created (optional)")

    for shell in shells:
        reach = constitution.verify_reachability(repo_root, shell)
        if reach.reachable:
            print(f"[brnrd] ✓ {shell}: {reach.detail}")
        else:
            print(f"[brnrd] ✗ {shell}: {reach.detail}")
            ok = False

    if ok:
        print("[brnrd] init complete")
    else:
        print("[brnrd] init incomplete — re-run `brnrd init` to retry")
