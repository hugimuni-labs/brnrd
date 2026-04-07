"""Integration tests — verify brr init produces correct structure."""

import subprocess
from pathlib import Path

from brr import adopt


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def _mock_runner_creates_files(monkeypatch):
    """Mock runner that creates AGENTS.md and kb/ like a real runner would."""
    def _fake_run(runner_name, prompt, cwd=None, cfg=None):
        if cwd:
            (cwd / "AGENTS.md").write_text("# Project\n\nTest project.\n")
            kb = cwd / "kb"
            kb.mkdir(exist_ok=True)
            (kb / "index.md").write_text("# Knowledge Base Index\n")
            (kb / "log.md").write_text("# Activity Log\n")
        return ""
    monkeypatch.setattr("brr.runner.detect_runner", lambda *a, **kw: "mock")
    monkeypatch.setattr("brr.runner.run_executor", _fake_run)


class TestEmptyRepo:
    def test_creates_structure(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        monkeypatch.chdir(tmp_path)
        _mock_runner_creates_files(monkeypatch)

        adopt.init_repo()

        assert (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / "kb" / "index.md").exists()
        assert (tmp_path / "kb" / "log.md").exists()
        assert (tmp_path / ".brr" / "config").exists()
        assert (tmp_path / ".brr" / "inbox").is_dir()
        assert (tmp_path / ".brr" / "responses").is_dir()

    def test_gitignore_has_brr(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        monkeypatch.chdir(tmp_path)
        _mock_runner_creates_files(monkeypatch)

        adopt.init_repo()
        text = (tmp_path / ".gitignore").read_text()
        assert ".brr/" in text


class TestRepoWithExistingAgentsMd:
    def test_runner_still_called(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# Custom content\n")
        monkeypatch.chdir(tmp_path)
        calls = []
        monkeypatch.setattr("brr.runner.detect_runner", lambda *a, **kw: "mock")
        monkeypatch.setattr("brr.runner.run_executor",
                            lambda *a, **kw: calls.append(1) or "")

        adopt.init_repo()
        assert len(calls) == 1


class TestNoGitRepo:
    def test_auto_git_init(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _mock_runner_creates_files(monkeypatch)
        adopt.init_repo()
        assert (tmp_path / ".git").exists()
