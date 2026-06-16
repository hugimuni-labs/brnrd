"""Tests for adopt module."""

import subprocess

import pytest

from brr import adopt
from brr.runner import RunnerResult


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
                "tasks", "traces", "reviews", "worktrees"):
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
