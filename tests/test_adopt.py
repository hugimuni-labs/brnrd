"""Tests for adopt module."""

import subprocess

from brr import adopt


def _mock_no_executor(monkeypatch):
    """Make detect_executor return None so init skips the enrichment step."""
    monkeypatch.setattr("brr.executor.detect_executor", lambda: None)


def test_write_templates(tmp_path, monkeypatch):
    _mock_no_executor(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    monkeypatch.chdir(repo)
    adopt.init_repo()
    assert (repo / "AGENTS.md").exists()
    assert (repo / ".brr.local" / "state.md").exists()
    # Second call should not overwrite
    content = (repo / "AGENTS.md").read_text()
    adopt.init_repo()
    assert (repo / "AGENTS.md").read_text() == content


def test_template_has_valid_yaml_header(tmp_path, monkeypatch):
    _mock_no_executor(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    monkeypatch.chdir(repo)
    adopt.init_repo()
    text = (repo / "AGENTS.md").read_text()
    assert text.startswith("---\n")
    assert "\n---\n" in text[4:]


def test_incorporates_claude_md(tmp_path, monkeypatch):
    _mock_no_executor(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "CLAUDE.md").write_text("# My project\n\nCustom instructions here.\n")
    monkeypatch.chdir(repo)
    adopt.init_repo()
    text = (repo / "AGENTS.md").read_text()
    assert "Custom instructions here." in text
    assert text.startswith("---\n")


def test_enrichment_runs_when_executor_available(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("brr.executor.detect_executor", lambda: "codex")
    calls = []
    monkeypatch.setattr("brr.executor.run_task", lambda inst: calls.append(inst) or "")
    adopt.init_repo()
    assert len(calls) == 1
    assert "AGENTS.md" in calls[0]
