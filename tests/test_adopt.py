"""Tests for adopt module."""

import subprocess

from brr import adopt


def _mock_runner(monkeypatch, output=""):
    """Mock runner detection and execution to avoid calling real CLIs."""
    monkeypatch.setattr("brr.runner.detect_runner", lambda *a, **kw: "mock-runner")
    monkeypatch.setattr("brr.runner.run_executor", lambda *a, **kw: output)


def test_creates_brr_dir(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)
    _mock_runner(monkeypatch)

    adopt.init_repo()

    brr = repo / ".brr"
    assert brr.exists()
    assert (brr / "inbox").exists()
    assert (brr / "responses").exists()
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
