"""Tests for adopt module."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from brr import adopt
from brr.runner import RunnerResult


REPO = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def _no_gh_by_default(monkeypatch):
    """Keep every ``init_repo()`` call in this file hermetic and gh-free.

    ``_state_identity()`` (and ``collect_facts``'s ``gh_available`` fact)
    call ``home_link.gh_available()`` / ``resolve_owner()`` on every init —
    real work on a real machine, but a live ``gh api user`` network call
    inside a test whenever ``gh`` happens to be on the test runner's PATH
    and signed in (true of brnrd's own account-scoped credential). Tests
    that exercise the identity behavior itself override this explicitly.
    """
    monkeypatch.setattr("brr.home_link.gh_available", lambda: False)


def _mock_runner(monkeypatch, output=""):
    """Mock runner detection and execution to avoid calling real CLIs."""
    monkeypatch.setattr("brr.runner.detect_runner", lambda *a, **kw: "mock-runner")
    monkeypatch.setattr("brr.runner.detect_all_runners", lambda *a, **kw: ["mock-runner"])
    monkeypatch.setattr(
        "brr.runner.invoke_runner",
        lambda runner_name, invocation, cfg=None: RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout=output,
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[
                adopt.runner.RunnerArtifactRecord(
                    path=artifact.path,
                    label=artifact.label or str(artifact.path),
                    exists=True,
                    trace_copy=None,
                )
                for artifact in invocation.required_artifacts
            ],
        ),
    )


def test_creates_brr_dir(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)
    _mock_runner(monkeypatch)

    adopt.init_repo()

    brr = repo / ".brr"
    assert brr.exists()
    for sub in ("inbox", "responses", "gates", "prompts",
                "runs", "traces", "reviews", "worktrees"):
        assert (brr / sub).exists(), f".brr/{sub} missing"
    assert (brr / "config").exists()


def test_gitignore_updated(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)
    _mock_runner(monkeypatch)

    adopt.init_repo()
    text = (repo / ".gitignore").read_text()
    assert ".brr/" in text


def test_idempotent_gitignore(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / ".gitignore").write_text("*.pyc\n.brr/\n")
    monkeypatch.chdir(repo)
    _mock_runner(monkeypatch)

    adopt.init_repo()
    text = (repo / ".gitignore").read_text()
    assert text.count(".brr/") == 1


def test_fails_without_runner(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("brr.runner.detect_runner", lambda *a, **kw: None)
    monkeypatch.setattr("brr.runner.detect_all_runners", lambda *a, **kw: [])

    import pytest
    with pytest.raises(SystemExit):
        adopt.init_repo()


def test_git_init_if_needed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mock_runner(monkeypatch)

    adopt.init_repo()
    assert (tmp_path / ".git").exists()


def test_init_repo_defaults_skips_the_wake_even_when_it_would_be_available(
    tmp_path, monkeypatch,
):
    """``defaults=True`` (#1244 fork 2's ``brnrd account connect --defaults``
    opt-out) takes the headless path unconditionally — even when the wake
    path would otherwise run (real TTY, playbook installed). The caller
    already made this choice; re-deriving it from ``sys.stdin.isatty()``
    would silently ignore an explicit ``--defaults`` on an interactive
    terminal, which is the one case this flag exists for."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
    captured = []
    _mock_runner_writing(monkeypatch, capture=captured)

    from brr import init_wake

    monkeypatch.setattr(
        init_wake, "wake_path_available", lambda *a, **kw: (True, ""),
    )

    def _explode(*_a, **_kw):
        raise AssertionError("run_init_wake must not be called under defaults=True")

    monkeypatch.setattr(init_wake, "run_init_wake", _explode)

    adopt.init_repo(defaults=True)

    assert captured, "the headless runner path must still have run"
    assert (repo / "AGENTS.md").exists()


def test_missing_git_exits_with_install_recovery_instead_of_a_traceback(tmp_path):
    env = {
        **os.environ,
        "PATH": "",
        "PYTHONPATH": str(REPO / "src"),
    }

    result = subprocess.run(
        [sys.executable, "-m", "brr", "init"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Git is required" in output and "Install Git" in output
    assert "re-run `brnrd init`" in output
    assert "Traceback" not in output


def test_failed_remote_clone_exits_with_url_and_access_recovery(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "echo 'fatal: repository access refused' >&2\n"
        "exit 23\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(fake_bin),
        "PYTHONPATH": str(REPO / "src"),
    }

    result = subprocess.run(
        [sys.executable, "-m", "brr", "init", "https://example.invalid/private.git"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Git could not clone https://example.invalid/private.git" in output
    assert "Check that the URL exists and that you have access" in output
    assert "repository access refused" in output
    assert "Traceback" not in output


class TestConfigureEnvironment:
    """Interactive Docker question and worktree fallback."""

    def test_no_docker_falls_back_to_worktree(self, monkeypatch):
        monkeypatch.setattr(adopt.shutil, "which", lambda _name: None)
        assert adopt._configure_environment() == {"environment": "worktree"}

    def test_docker_available_user_declines(self, monkeypatch):
        monkeypatch.setattr(adopt.shutil, "which", lambda _name: "/usr/bin/docker")
        monkeypatch.setattr(adopt, "_confirm", lambda *_a, **_kw: False)

        cfg = adopt._configure_environment()
        assert cfg == {"environment": "worktree"}

    def test_docker_available_user_accepts_default_image_no_build(self, monkeypatch):
        """User opts in but declines the build — config still records docker."""
        monkeypatch.setattr(adopt.shutil, "which", lambda _name: "/usr/bin/docker")
        confirms = iter([True, False])
        monkeypatch.setattr(adopt, "_confirm", lambda *_a, **_kw: next(confirms))
        monkeypatch.setattr(
            adopt, "_timed_input",
            lambda prompt, default, timeout=10: default,
        )
        called: list = []
        monkeypatch.setattr(
            adopt, "_build_default_docker_image",
            lambda: called.append(1) or True,
        )

        cfg = adopt._configure_environment()
        assert cfg["environment"] == "docker"
        assert cfg["docker.image"] == adopt._DEFAULT_DOCKER_IMAGE
        assert called == [], "build helper must not run when user declines"

    def test_docker_available_user_brings_own_image(self, monkeypatch):
        """Custom image skips the build offer entirely."""
        monkeypatch.setattr(adopt.shutil, "which", lambda _name: "/usr/bin/docker")
        confirms = iter([True])
        monkeypatch.setattr(adopt, "_confirm", lambda *_a, **_kw: next(confirms))
        monkeypatch.setattr(
            adopt, "_timed_input",
            lambda prompt, default, timeout=10: "my/custom:tag",
        )
        called: list = []
        monkeypatch.setattr(
            adopt, "_build_default_docker_image",
            lambda: called.append(1) or True,
        )

        cfg = adopt._configure_environment()
        assert cfg["environment"] == "docker"
        assert cfg["docker.image"] == "my/custom:tag"
        assert called == [], "no build offer for user-supplied images"
        # Also: the second confirm (for the build prompt) must not be
        # consumed since we never reach it.
        with pytest.raises(StopIteration):
            next(confirms)

    def test_docker_available_user_accepts_default_image_and_builds(self, monkeypatch):
        monkeypatch.setattr(adopt.shutil, "which", lambda _name: "/usr/bin/docker")
        confirms = iter([True, True])
        monkeypatch.setattr(adopt, "_confirm", lambda *_a, **_kw: next(confirms))
        monkeypatch.setattr(
            adopt, "_timed_input",
            lambda prompt, default, timeout=10: default,
        )
        built = []
        monkeypatch.setattr(
            adopt, "_build_default_docker_image",
            lambda: built.append(1) or True,
        )

        cfg = adopt._configure_environment()
        assert cfg == {
            "environment": "docker",
            "docker.image": adopt._DEFAULT_DOCKER_IMAGE,
        }
        assert built == [1]

    def test_docker_image_yes_means_default_not_literal_tag(self, monkeypatch):
        """Typing ``y`` at the image prompt is a common mistake after Y/n."""
        monkeypatch.setattr(adopt.shutil, "which", lambda _name: "/usr/bin/docker")
        confirms = iter([True, False])
        monkeypatch.setattr(adopt, "_confirm", lambda *_a, **_kw: next(confirms))
        monkeypatch.setattr(
            adopt, "_timed_input",
            lambda prompt, default, timeout=10: "y",
        )
        monkeypatch.setattr(adopt, "_build_default_docker_image", lambda: True)

        cfg = adopt._configure_environment()
        assert cfg["docker.image"] == adopt._DEFAULT_DOCKER_IMAGE


class TestOfferHomeLink:
    """The single git-durability question at the tail of interactive init."""

    def test_no_gh_asks_nothing(self, monkeypatch, tmp_path):
        from brr import home_link

        monkeypatch.setattr(home_link, "gh_available", lambda: False)
        confirm_calls: list = []
        monkeypatch.setattr(adopt, "_confirm", lambda *a, **kw: confirm_calls.append((a, kw)) or True)

        adopt._offer_home_link(tmp_path)

        assert confirm_calls == [], "gh absent must skip the question entirely, not just decline it"

    def test_declining_the_one_question_links_nothing(self, monkeypatch, tmp_path):
        from brr import home_link

        monkeypatch.setattr(home_link, "gh_available", lambda: True)
        monkeypatch.setattr(adopt, "_confirm", lambda *a, **kw: False)
        called: list = []
        monkeypatch.setattr(home_link, "link_home", lambda *a, **kw: called.append(1))

        adopt._offer_home_link(tmp_path)

        assert called == []

    def test_accepting_asks_exactly_once_for_both_repos(self, monkeypatch, tmp_path):
        """One confirm, one link_home call — never a second per-repo question."""
        from brr import home_link

        monkeypatch.setattr(home_link, "gh_available", lambda: True)
        confirm_calls: list = []
        monkeypatch.setattr(
            adopt, "_confirm", lambda *a, **kw: confirm_calls.append((a, kw)) or True,
        )
        link_calls: list = []

        def fake_link_home(repo_root, cfg, **kwargs):
            link_calls.append((repo_root, kwargs))
            return [
                home_link.RepoLinkResult("dominion", tmp_path, "https://x/d", "created", True),
                home_link.RepoLinkResult("knowledge", tmp_path, "https://x/k", "created", True),
            ]

        monkeypatch.setattr(home_link, "link_home", fake_link_home)

        adopt._offer_home_link(tmp_path)

        assert len(confirm_calls) == 1, "must ask exactly one question, not one per repo"
        assert len(link_calls) == 1

    def test_link_failure_is_reported_not_raised(self, monkeypatch, tmp_path, capsys):
        from brr import home_link

        monkeypatch.setattr(home_link, "gh_available", lambda: True)
        monkeypatch.setattr(adopt, "_confirm", lambda *a, **kw: True)

        def boom(*a, **kw):
            raise home_link.HomeLinkError("gh is not authenticated — run `gh auth login` first")

        monkeypatch.setattr(home_link, "link_home", boom)

        adopt._offer_home_link(tmp_path)  # must not raise

        assert "gh is not authenticated" in capsys.readouterr().out


class TestBuildDefaultDockerImage:
    def test_returns_false_when_dockerfile_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adopt, "_BUNDLED_DOCKERFILE", tmp_path / "missing")
        assert adopt._build_default_docker_image() is False

    def test_runs_docker_build_with_temp_context(self, monkeypatch, tmp_path):
        bundled = tmp_path / "Dockerfile"
        bundled.write_text("FROM alpine\n", encoding="utf-8")
        monkeypatch.setattr(adopt, "_BUNDLED_DOCKERFILE", bundled)

        captured: list = []

        def _fake_run(command, **_kwargs):
            captured.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(adopt.subprocess, "run", _fake_run)

        assert adopt._build_default_docker_image() is True
        cmd = captured[0]
        assert cmd[:4] == ["docker", "build", "-t", adopt._DEFAULT_DOCKER_IMAGE]
        ctx_dir = cmd[4]
        assert ctx_dir != str(tmp_path), "context must be a temp dir, not the bundled location"

    def test_returns_false_on_build_failure(self, monkeypatch, tmp_path):
        bundled = tmp_path / "Dockerfile"
        bundled.write_text("FROM alpine\n", encoding="utf-8")
        monkeypatch.setattr(adopt, "_BUNDLED_DOCKERFILE", bundled)
        monkeypatch.setattr(
            adopt.subprocess, "run",
            lambda command, **_kw: subprocess.CompletedProcess(command, 1, "", ""),
        )
        assert adopt._build_default_docker_image() is False

    def test_context_carries_every_copy_source_including_licenses(
        self, monkeypatch, tmp_path
    ):
        """The assembled context is derived from the Dockerfile, not restated.

        Before #675 this function held its own literal list of paths, so a
        ``COPY`` source the Dockerfile gained (the root license files) never
        reached the context and the wheel built inside the image quietly
        dropped it.
        """
        repo = tmp_path / "repo"
        (repo / "src" / "brr").mkdir(parents=True)
        for rel in ("pyproject.toml", "README.md", "LICENSE", "LICENSE-OVERVIEW.md"):
            (repo / rel).write_text(rel, encoding="utf-8")
        (repo / "src" / "brr" / "LICENSE").write_text("pkg", encoding="utf-8")

        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(
            "FROM alpine\n"
            "COPY pyproject.toml README.md LICENSE LICENSE-OVERVIEW.md /opt/brr/\n"
            "COPY src /opt/brr/src\n",
            encoding="utf-8",
        )

        ctx = tmp_path / "ctx"
        ctx.mkdir()
        adopt._assemble_build_context(dockerfile, repo, ctx)

        assert (ctx / "Dockerfile").is_file()
        for rel in ("pyproject.toml", "README.md", "LICENSE", "LICENSE-OVERVIEW.md"):
            assert (ctx / rel).is_file(), rel
        assert (ctx / "src" / "brr" / "LICENSE").is_file()

    def test_missing_copy_source_is_reported_not_silently_skipped(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM alpine\nCOPY LICENSE /opt/brr/\n", encoding="utf-8")
        ctx = tmp_path / "ctx"
        ctx.mkdir()

        with pytest.raises(FileNotFoundError, match="LICENSE"):
            adopt._assemble_build_context(dockerfile, repo, ctx)


class TestDockerfileContextPaths:
    def test_last_token_is_the_destination(self):
        paths = adopt.dockerfile_context_paths("COPY a.txt b.txt /opt/brr/\n")
        assert paths == ["a.txt", "b.txt"]

    def test_backslash_continuations_are_folded(self):
        paths = adopt.dockerfile_context_paths(
            "COPY pyproject.toml \\\n    LICENSE \\\n    /opt/brr/\n"
        )
        assert paths == ["pyproject.toml", "LICENSE"]

    def test_stage_copies_and_flags_are_not_context_sources(self):
        text = (
            "# COPY commented.txt /opt/\n"
            "COPY --from=builder /out /opt/brr/out\n"
            "COPY --chown=1000:1000 src /opt/brr/src\n"
        )
        assert adopt.dockerfile_context_paths(text) == ["src"]

    def test_the_bundled_dockerfile_parses_to_real_checkout_paths(self):
        repo_root = Path(adopt.__file__).resolve().parent.parent.parent
        paths = adopt.dockerfile_context_paths(
            adopt._BUNDLED_DOCKERFILE.read_text(encoding="utf-8")
        )
        assert paths, "bundled Dockerfile must copy something into the context"
        for rel in paths:
            assert (repo_root / rel).exists(), rel


# ── L0–L2: template split, kb-not-required, shell bridges ────────────


_VALID_AGENTS = (
    "# Project\n\nA repo.\n\n## Stewardship\nBe a good steward and think first.\n\n"
    "## Knowledge base\nThe kb compounds across sessions and stays current.\n\n"
    "## Guardrails\nDo not commit secrets; stop after two failed attempts.\n"
)


def _mock_runner_writing(monkeypatch, agents_text=_VALID_AGENTS, capture=None):
    """Mock a runner that actually writes AGENTS.md, so structure and
    reachability verification run against a real file."""
    monkeypatch.setattr("brr.runner.detect_runner", lambda *a, **kw: "mock-runner")
    monkeypatch.setattr("brr.runner.detect_all_runners", lambda *a, **kw: ["mock-runner"])

    def _invoke(runner_name, invocation, cfg=None):
        if capture is not None:
            capture.append(invocation)
        if agents_text is not None:
            (invocation.repo_root / "AGENTS.md").write_text(agents_text, encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[
                adopt.runner.RunnerArtifactRecord(
                    path=a.path,
                    label=a.label or str(a.path),
                    exists=a.path.exists(),
                    trace_copy=None,
                )
                for a in invocation.required_artifacts
            ],
        )

    monkeypatch.setattr("brr.runner.invoke_runner", _invoke)


def _init_git(repo):
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)


class TestKnowledgeShapeAndArtifacts:
    def test_kb_is_not_a_hard_required_artifact(self, tmp_path, monkeypatch):
        # The abandoned architecture: init used to SystemExit(1) when kb/
        # index.md + log.md were absent. Now only AGENTS.md is required.
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
        captured = []
        _mock_runner_writing(monkeypatch, capture=captured)

        adopt.init_repo()  # must not raise even though no kb/ was created

        labels = {a.label for a in captured[0].required_artifacts}
        assert labels == {"AGENTS.md"}
        assert not (repo / "kb").exists()

    def test_empty_agents_fails_install(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
        _mock_runner_writing(monkeypatch, agents_text="# too short\n")

        with pytest.raises(SystemExit):
            adopt.init_repo()

    def test_knowledge_shape_defaults_to_repo_noninteractive(self):
        assert adopt._resolve_knowledge_shape(interactive=False) == "repo"


class TestShellBridges:
    def test_bridges_written_for_detected_shells(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: ["claude", "codex"])
        _mock_runner_writing(monkeypatch)

        adopt.init_repo()

        claude = repo / "CLAUDE.md"
        assert claude.exists()
        assert "@AGENTS.md" in claude.read_text(encoding="utf-8")
        # Codex reads AGENTS.md natively — no bridge file.
        assert not (repo / "CODEX.md").exists()
        # And the contract is reachable from every detected shell.
        for shell in ("claude", "codex"):
            assert adopt.constitution.verify_reachability(repo, shell).reachable

    def test_detect_shells_uses_path(self, monkeypatch):
        monkeypatch.setattr(
            adopt.shutil, "which",
            lambda binary: "/usr/bin/" + binary if binary in ("claude", "codex") else None,
        )
        assert adopt._detect_shells() == ["claude", "codex"]


# ── Security domain routing (#413 §7 S4) ────────────────────────────
#
# brnrd init must route config keys to their correct trust domain:
# repo-safe keys → .brr/config, security keys → security.config.
# Placing security keys in .brr/config silently drops them (load_config
# ignores them), so the operator's Docker choice is dead on arrival.


class TestSecurityDomainRouting:
    """Five driven tests — each confirmed red before the fix, green after."""

    def _setup_git_repo(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git(repo)
        return repo

    def _mock_post_configure(self, monkeypatch, repo):
        """Stub out the parts of _init_auto we don't want to exercise."""
        monkeypatch.setattr(adopt, "_run_setup", lambda *a, **kw: None)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
        monkeypatch.setattr(adopt, "_verify", lambda *a, **kw: None)
        monkeypatch.setattr(adopt.constitution, "write_bridges", lambda *a, **kw: [])
        # Suppress dominion noise.
        monkeypatch.setattr(adopt, "_bootstrap_dominion", lambda *a, **kw: None)

    def _docker_init(self, monkeypatch, repo):
        """Run the config-writing portion of an interactive Docker init."""
        monkeypatch.setattr(adopt.shutil, "which", lambda _n: "/usr/bin/docker")
        confirms = iter([True, False])  # accept Docker, decline build
        monkeypatch.setattr(adopt, "_confirm", lambda *_a, **_kw: next(confirms))
        monkeypatch.setattr(
            adopt, "_timed_input",
            lambda prompt, default, timeout=10: "test-image:v1",
        )
        adopt._setup_brr_dir(repo)
        adopt._init_auto(repo, ["mock-runner"], interactive=True)

    # ── Test 1: fresh Docker init → ignored empty, env resolves ─────

    def test_fresh_docker_init_no_ignored_keys(self, tmp_path, monkeypatch):
        """After a Docker init, load_config_report must return ignored==[].

        Red on unpatched code: _setup_brr_dir writes environment=auto and
        _init_auto writes environment=docker + docker.image — all security
        keys in .brr/config → all in ignored.
        """
        from brr import config as conf
        from brr.run import resolve_env

        repo = self._setup_git_repo(tmp_path)
        self._mock_post_configure(monkeypatch, repo)
        # Clear the path cache so this test doesn't inherit a stale None.
        conf._SECURITY_PATH_CACHE.clear()

        self._docker_init(monkeypatch, repo)

        cfg, ignored = conf.load_config_report(repo)
        assert ignored == [], f"security keys leaked into .brr/config: {ignored}"
        assert cfg.get("environment") == "docker", (
            f"environment not in merged cfg; got {cfg.get('environment')!r}"
        )
        assert cfg.get("docker.image") == "test-image:v1", (
            f"docker.image not in merged cfg; got {cfg.get('docker.image')!r}"
        )

    # ── Test 2: structural regression guard ─────────────────────────

    def test_write_config_never_receives_security_key_from_adopt(
        self, tmp_path, monkeypatch
    ):
        """Spy on write_config: any call that includes a security key must fail.

        Red on unpatched code: _setup_brr_dir and _init_auto both pass
        security keys (environment, docker.*) through write_config.
        """
        from brr import config as conf

        repo = self._setup_git_repo(tmp_path)
        self._mock_post_configure(monkeypatch, repo)
        conf._SECURITY_PATH_CACHE.clear()

        violations: list[str] = []
        real_write_config = conf.write_config

        def spy_write_config(root, cfg_dict):
            bad = [k for k in cfg_dict if conf.is_security_key(k)]
            if bad:
                violations.extend(bad)
            return real_write_config(root, cfg_dict)

        monkeypatch.setattr(conf, "write_config", spy_write_config)
        monkeypatch.setattr(adopt, "_confirm", lambda *_a, **_kw: False)  # decline Docker
        monkeypatch.setattr(adopt.shutil, "which", lambda _n: "/usr/bin/docker")

        adopt._setup_brr_dir(repo)
        adopt._init_auto(repo, ["mock-runner"], interactive=True)

        assert violations == [], (
            f"write_config received security key(s) from adopt path: {violations}"
        )

    # ── Test 3: write_security_config merges, not truncates ─────────

    def test_write_security_config_merges_existing_keys(self, tmp_path, monkeypatch):
        """Pre-existing key in security.config survives a second write.

        Red on unpatched code: write_security_config doesn't exist yet.
        """
        from brr import config as conf

        repo = self._setup_git_repo(tmp_path)
        conf._SECURITY_PATH_CACHE.clear()

        # Write an initial security key.
        conf.write_security_config(repo, {"environment": "docker"})
        # Write a different key — must not clobber the first.
        conf.write_security_config(repo, {"docker.image": "new:tag"})

        sec_path = conf.security_config_path(repo)
        raw = conf._read_flat(sec_path)
        assert raw.get("environment") == "docker", "pre-existing key was truncated"
        assert raw.get("docker.image") == "new:tag", "new key not written"

    # ── Test 4: file mode is 0600 ────────────────────────────────────

    def test_write_security_config_creates_0600(self, tmp_path, monkeypatch):
        """security.config is created with mode 0600.

        Red on unpatched code: write_security_config doesn't exist yet.
        """
        import stat
        from brr import config as conf

        repo = self._setup_git_repo(tmp_path)
        conf._SECURITY_PATH_CACHE.clear()

        written = conf.write_security_config(repo, {"environment": "worktree"})
        assert written is not None, "write_security_config returned None"
        mode = stat.S_IMODE(written.stat().st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    # ── Test 5: declining Docker → worktree resolves, no sec keys in repo cfg

    def test_fresh_worktree_init_no_security_keys_in_repo_config(
        self, tmp_path, monkeypatch
    ):
        """Declining Docker: .brr/config must have no security-shaped keys.

        Red on unpatched code: _setup_brr_dir writes environment=auto
        (a security key) into .brr/config, so ignored is never empty.
        """
        from brr import config as conf

        repo = self._setup_git_repo(tmp_path)
        self._mock_post_configure(monkeypatch, repo)
        conf._SECURITY_PATH_CACHE.clear()

        # Docker available but user declines → _configure_environment returns
        # {"environment": "worktree"}.
        monkeypatch.setattr(adopt.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(adopt, "_confirm", lambda *_a, **_kw: False)

        adopt._setup_brr_dir(repo)
        adopt._init_auto(repo, ["mock-runner"], interactive=True)

        cfg, ignored = conf.load_config_report(repo)
        assert ignored == [], (
            f"security keys in .brr/config after worktree init: {ignored}"
        )
        # environment=worktree must be in security config and readable.
        assert cfg.get("environment") == "worktree", (
            f"environment not resolved; got {cfg.get('environment')!r}"
        )


# ── The spelling the launcher earned (npx) ──────────────────────────
#
# `npx brnrd init` builds a durable venv under ~/.local/share/brnrd and
# execs the binary inside it. It never puts `brnrd` on PATH — so every
# line init prints that says "run `brnrd …`" is false for that user, and
# the first one they meet is the closing line of their first session.
# The launcher exports BRNRD_LAUNCHER=npx; these pin that init reads it.
#
# Every case is paired with its **absent** twin: a pip / uv / `npm i -g`
# install must keep printing bare `brnrd`, and that is the half a
# regression would break in silence.


class TestNpxSpelling:
    @staticmethod
    def _wake_repo(tmp_path, monkeypatch):
        """A repo where ``init_repo`` takes the *wake* path (the real screen)."""
        from brr import init_wake

        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
        monkeypatch.setattr(
            "brr.runner.detect_all_runners", lambda *a, **kw: ["mock-runner"],
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr(init_wake, "_default_reader", lambda *_a: "")

        def _invoke(runner_name, invocation, cfg=None):
            (repo / "AGENTS.md").write_text(
                "## Stewardship\n" + "x" * 200
                + "\n## Knowledge base\n\n## Guardrails\n",
                encoding="utf-8",
            )
            return RunnerResult(
                invocation=invocation,
                runner_name=runner_name,
                command=["mock"],
                stdout="",
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[],
            )

        monkeypatch.setattr("brr.runner.invoke_runner", _invoke)
        return repo

    def test_wake_closing_line_is_npx_spelled(self, tmp_path, monkeypatch, capsys):
        """The exact line the reporter was looking at when `brnrd` 404'd.

        Superseded by the channel menu (#1084 family — see TestChannelMenu):
        "next: `brnrd up`, then send it work" named no channel and is gone;
        this now pins its replacement's npx spelling specifically.
        """
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        self._wake_repo(tmp_path, monkeypatch)

        adopt.init_repo()

        out = capsys.readouterr().out
        assert "next: `npx brnrd up`, then send it work." not in out
        assert (
            "next: `npx brnrd account connect` or "
            "`npx brnrd gate setup telegram`" in out
        )

    def test_wake_closing_line_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        self._wake_repo(tmp_path, monkeypatch)

        adopt.init_repo()

        out = capsys.readouterr().out
        assert "next: `brnrd up`, then send it work." not in out
        assert (
            "next: `brnrd account connect` or `brnrd gate setup telegram`"
            in out
        )
        assert "npx" not in out

    def test_setup_retry_line_is_npx_spelled(self, tmp_path, monkeypatch, capsys):
        """The runner produced no AGENTS.md — init's own retry instruction."""
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
        _mock_runner_writing(monkeypatch, agents_text=None)

        with pytest.raises(SystemExit):
            adopt.init_repo()

        assert "re-run `npx brnrd init` to retry" in capsys.readouterr().out

    def test_setup_retry_line_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
        _mock_runner_writing(monkeypatch, agents_text=None)

        with pytest.raises(SystemExit):
            adopt.init_repo()

        out = capsys.readouterr().out
        assert "re-run `brnrd init` to retry" in out
        assert "npx" not in out

    def test_incomplete_verify_line_is_npx_spelled(
        self, tmp_path, monkeypatch, capsys,
    ):
        """``_verify``'s closing line — the other end of the same screen."""
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
        _mock_runner(monkeypatch)  # asserts the artifact without writing it

        adopt.init_repo()

        out = capsys.readouterr().out
        assert "init incomplete — re-run `npx brnrd init` to retry" in out

    def test_incomplete_verify_line_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])
        _mock_runner(monkeypatch)

        adopt.init_repo()

        out = capsys.readouterr().out
        assert "init incomplete — re-run `brnrd init` to retry" in out
        assert "npx" not in out

    def test_runner_doctor_from_init_is_npx_spelled(self, tmp_path, monkeypatch):
        """No Runner on PATH: the first-session ladder, both commands it names."""
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr("brr.runner.detect_all_runners", lambda *a, **kw: [])

        with pytest.raises(SystemExit) as excinfo:
            adopt.init_repo()

        text = str(excinfo.value)
        assert "re-run `npx brnrd init`" in text
        assert "Full profile table: npx brnrd runners list --all" in text

    def test_runner_doctor_stays_bare_for_a_path_install(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr("brr.runner.detect_all_runners", lambda *a, **kw: [])

        with pytest.raises(SystemExit) as excinfo:
            adopt.init_repo()

        text = str(excinfo.value)
        assert "re-run `brnrd init`" in text
        assert "Full profile table: brnrd runners list --all" in text
        assert "npx" not in text

    def test_missing_git_recovery_is_npx_spelled_in_a_real_process(self, tmp_path):
        """End to end: a real `python -m brr init` carrying the launcher's env."""
        env = {
            **os.environ,
            "PATH": "",
            "PYTHONPATH": str(REPO / "src"),
            "BRNRD_LAUNCHER": "npx",
        }

        result = subprocess.run(
            [sys.executable, "-m", "brr", "init"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )

        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "re-run `npx brnrd init`" in output
        assert "Traceback" not in output


# ── The identity already on the machine ──────────────────────────────
#
# "which repo" (`git remote`) has always been inferred silently; "who you
# are" was resolvable the same way (`gh auth token` / `gh api user`, the
# resolution the GitHub gate and `brnrd home link` already used) but
# nothing on the init path called it. `_state_identity()` is the front
# door. `gh` missing or signed out is a degradation, never a failure —
# these pin both halves.


class TestIdentityAtInit:
    def test_states_identity_when_gh_resolves_a_login(
        self, tmp_path, monkeypatch, capsys,
    ):
        from brr import home_link

        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        _mock_runner(monkeypatch)
        monkeypatch.setattr(home_link, "gh_available", lambda: True)
        monkeypatch.setattr(home_link, "resolve_owner", lambda explicit=None: "octocat")

        adopt.init_repo()

        assert "[brnrd] you: @octocat (from `gh`)" in capsys.readouterr().out

    def test_degrades_when_gh_is_not_on_path(self, tmp_path, monkeypatch, capsys):
        # the module-level `_no_gh_by_default` fixture already pins
        # `gh_available()` to False — this test only pins the wording.
        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        _mock_runner(monkeypatch)

        adopt.init_repo()

        out = capsys.readouterr().out
        # The rule is "degrade, never block" — and, since 2026-08-04, never
        # by negating the person. The old line read "you: not detected",
        # which is a sentence about `gh` wearing a sentence about *them*,
        # printed third in the first thing a stranger ever sees from this
        # product. Both halves are pinned: the reason is still stated, and
        # the subject of the sentence is the tool.
        assert "`gh` isn't signed in here" in out
        assert "you can link GitHub later" in out
        assert "you: not detected" not in out

    def test_degrades_when_gh_is_unauthenticated(self, tmp_path, monkeypatch, capsys):
        """`gh` is on PATH but `gh api user` fails — still no crash, no block."""
        from brr import home_link

        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        _mock_runner(monkeypatch)
        monkeypatch.setattr(home_link, "gh_available", lambda: True)

        def _raise(explicit=None):
            raise home_link.HomeLinkError("could not resolve the GitHub owner")

        monkeypatch.setattr(home_link, "resolve_owner", _raise)

        adopt.init_repo()  # must not raise

        # Same wording rule as the sibling above: state the tool, never
        # report the person as undetected.
        out = capsys.readouterr().out
        assert "`gh` isn't signed in here" in out
        assert "you: not detected" not in out

    def test_identity_is_stated_before_the_tty_branch(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Stated once in `init_repo`, ahead of the wake-vs-auto fork —
        not duplicated inside either path."""
        from brr import home_link

        TestNpxSpelling._wake_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(home_link, "gh_available", lambda: True)
        monkeypatch.setattr(home_link, "resolve_owner", lambda explicit=None: "octocat")

        adopt.init_repo()

        out = capsys.readouterr().out
        assert out.count("[brnrd] you: @octocat") == 1


# ── The closing menu: upgrades, never prerequisites ──────────────────
#
# adopt.py used to print "next: `brnrd up`, then send it work." — naming
# no channel (#1084 family). `_print_channel_menu()` replaces it: point at
# a door concretely (`account connect` / `gate setup telegram`), then list
# the rest of the upgrades framed by what they *buy*
# (design-onboarding-ladder.md). `brnrd run "<task>"` is no longer the
# marquee moment here — it's a terminal-only smoke test, demoted per the
# #1102 rework (host users drive the agent CLI directly; brnrd's value is
# the door + resident continuity).


class TestChannelMenu:
    def test_states_the_working_channel_and_upgrades(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        TestNpxSpelling._wake_repo(tmp_path, monkeypatch)

        adopt.init_repo()

        out = capsys.readouterr().out
        assert (
            "[brnrd] next: `brnrd account connect` or "
            "`brnrd gate setup telegram` — give the resident a door that "
            "reaches it without a terminal open." in out
        )
        assert "a mailbox (`brnrd account connect`)" in out
        assert "an identity that isn't you" in out
        assert "brnrd.dev" in out
        assert "more doors (`brnrd gate setup telegram`" in out
        assert "`brnrd daemon install`" in out
        # the line this replaces must be gone, not merely joined by the new one
        assert "next: `brnrd up`, then send it work." not in out

    def test_channel_menu_is_npx_spelled(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        TestNpxSpelling._wake_repo(tmp_path, monkeypatch)

        adopt.init_repo()

        out = capsys.readouterr().out
        assert "`npx brnrd account connect`" in out
        assert "`npx brnrd gate setup telegram`" in out
        assert "`npx brnrd daemon install`" in out

    def test_channel_menu_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        TestNpxSpelling._wake_repo(tmp_path, monkeypatch)

        adopt.init_repo()

        out = capsys.readouterr().out
        assert "`brnrd account connect`" in out
        assert "npx" not in out

    def test_gates_configured_line_points_at_up_before_the_menu(
        self, tmp_path, monkeypatch, capsys,
    ):
        """When the interview already wired a gate, `brnrd up` is the
        completion of a choice already made — not offered as an upgrade.
        Drives `_init_via_wake` directly with a stubbed wake module so the
        outcome (gates already configured) doesn't depend on threading the
        real runner/portal loop through a canned reply."""
        from brr import init_wake

        repo = tmp_path / "repo"
        _init_git(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr(adopt, "_detect_shells", lambda: [])

        class _FakeInitWakeMod:
            @staticmethod
            def collect_facts(*_a, **_kw):
                return {}

            @staticmethod
            def run_init_wake(*_a, **_kw):
                (repo / "AGENTS.md").write_text(
                    "## Stewardship\n" + "x" * 200
                    + "\n## Knowledge base\n\n## Guardrails\n",
                    encoding="utf-8",
                )
                return init_wake.InitWakeResult(
                    ok=True, event_id="evt", gates_configured=["telegram"],
                )

        adopt._init_via_wake(repo, ["mock-runner"], _FakeInitWakeMod)

        out = capsys.readouterr().out
        assert "gates configured: telegram" in out
        assert "`brnrd up` makes them live." in out
    def test_setup_runner_question_names_shell_and_core_and_persists_override(
        self, tmp_path, monkeypatch,
    ):
        repo = tmp_path / "repo"
        _init_git(repo)
        adopt._setup_brr_dir(repo)
        catalog = [
            {
                "name": "codex",
                "shell": "codex",
                "core": "gpt-5.6-terra",
                "class": "balanced",
            },
            {
                "name": "codex-full",
                "shell": "codex",
                "core": "gpt-5.6-sol",
                "class": "strong",
            },
        ]
        monkeypatch.setattr(
            adopt.runner, "available_runner_catalog", lambda *_a, **_kw: catalog
        )
        seen = {}

        def choose(label, options, default):
            seen.update(label=label, options=options, default=default)
            return options[1]

        monkeypatch.setattr(adopt, "_pick_option", choose)

        selected = adopt._choose_setup_runner(repo, ["codex", "codex-full"])

        assert selected == "codex-full"
        assert seen["label"] == "Default model for the resident?"
        assert seen["default"] == "codex — codex / gpt-5.6-terra (balanced)"
        assert "codex-full — codex / gpt-5.6-sol (strong)" in seen["options"]
        assert adopt.conf.load_config(repo)["runner"] == "codex-full"

    def test_every_printed_command_is_a_real_cli_verb(self):
        """The #1084 discipline: every command a fresh install reads must
        exist and do what its line says. Parses the literal commands
        `_print_channel_menu` prints against the real argparse tree."""
        from brr.cli import build_parser

        parser = build_parser()
        for argv in (
            ["run", "some task"],
            ["account", "connect"],
            ["gate", "setup", "telegram"],
            ["gate", "setup", "slack"],
            ["gate", "setup", "signal"],
            ["daemon", "install"],
            ["up"],
        ):
            parser.parse_args(argv)  # raises SystemExit on an unknown verb
