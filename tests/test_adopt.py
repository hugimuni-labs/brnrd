"""Tests for adopt module."""

from pathlib import Path

from brr import adopt


def test_write_minimal_files(tmp_path, monkeypatch):
    # Create temporary git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "file").write_text("irrelevant")
    # initialise git
    import subprocess
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    # change into repo
    monkeypatch.chdir(repo)
    # call write_minimal_files
    adopt.write_minimal_files(repo)
    assert (repo / "AGENTS.md").exists()
    assert (repo / "agent_state.md").exists()