"""Tests for adopt module."""

import subprocess

from brr import adopt
from brr.runner import RunnerResult


def _mock_runner(monkeypatch, output=""):
    """Mock runner detection and execution to avoid calling real CLIs."""
    monkeypatch.setattr("brr.runner.detect_runner", lambda *a, **kw: "mock-runner")
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

    import pytest
    with pytest.raises(SystemExit):
        adopt.init_repo()


def test_git_init_if_needed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mock_runner(monkeypatch)

    adopt.init_repo()
    assert (tmp_path / ".git").exists()
