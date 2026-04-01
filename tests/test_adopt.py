"""Tests for adopt module."""

import subprocess

from brr import adopt


def test_write_templates(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("brr.executor.run_adopt_prompt", lambda *a, **kw: None)
    adopt.init_repo()
    assert (repo / "AGENTS.md").exists()
    assert (repo / ".brr.local" / "state.md").exists()
    # Second call should not overwrite
    content = (repo / "AGENTS.md").read_text()
    adopt.init_repo()
    assert (repo / "AGENTS.md").read_text() == content


def test_template_has_valid_yaml_header(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("brr.executor.run_adopt_prompt", lambda *a, **kw: None)
    adopt.init_repo()
    text = (repo / "AGENTS.md").read_text()
    assert text.startswith("---\n")
    assert "\n---\n" in text[4:]
