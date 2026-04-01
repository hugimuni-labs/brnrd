"""Integration tests — verify brr init produces correct files for various repo shapes."""

import subprocess
from pathlib import Path

from brr import adopt
from brr.config import parse_frontmatter


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def _run_init(path: Path, monkeypatch) -> None:
    monkeypatch.chdir(path)
    monkeypatch.setattr("brr.executor.run_adopt_prompt", lambda *a, **kw: None)
    adopt.init_repo()


def _check_agents_md(path: Path) -> dict:
    """Assert AGENTS.md exists and has valid frontmatter, return brr config."""
    agents = path / "AGENTS.md"
    assert agents.exists(), "AGENTS.md not created"
    text = agents.read_text()
    assert text.startswith("---\n"), "Missing frontmatter delimiter"
    fm = parse_frontmatter(text)
    assert "brr" in fm, "No brr key in frontmatter"
    brr = fm["brr"]
    assert "version" in brr
    assert "mode" in brr
    assert "default_executor" in brr
    assert "state_file" in brr
    return brr


def _check_state_md(path: Path, brr_config: dict) -> None:
    """Assert state file exists and has required sections."""
    state_rel = brr_config.get("state_file", ".brr.local/state.md")
    state = path / state_rel
    assert state.exists(), f"State file {state_rel} not created"
    text = state.read_text()
    for section in ["Current Focus", "Conversation Topics", "Decisions", "Next Steps"]:
        assert section in text, f"Missing section: {section}"


class TestEmptyRepo:
    def test_creates_both_files(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        _run_init(tmp_path, monkeypatch)
        brr = _check_agents_md(tmp_path)
        _check_state_md(tmp_path, brr)

    def test_default_executor_is_auto(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        _run_init(tmp_path, monkeypatch)
        brr = _check_agents_md(tmp_path)
        assert brr["default_executor"] == "auto"

    def test_default_state_is_local(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        _run_init(tmp_path, monkeypatch)
        brr = _check_agents_md(tmp_path)
        assert brr["state_file"] == ".brr.local/state.md"

    def test_template_has_sections(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        _run_init(tmp_path, monkeypatch)
        text = (tmp_path / "AGENTS.md").read_text()
        for section in ["# Project", "## Build and run", "## Code guidelines", "## Constraints"]:
            assert section in text, f"Template missing {section}"


class TestRepoWithExistingAgentsMd:
    def test_does_not_overwrite(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# Custom content\n")
        _run_init(tmp_path, monkeypatch)
        assert agents.read_text() == "# Custom content\n"


class TestRepoWithMakefile:
    def test_creates_files(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        (tmp_path / "Makefile").write_text("test:\n\tpytest\nbuild:\n\tpython setup.py build\n")
        _run_init(tmp_path, monkeypatch)
        brr = _check_agents_md(tmp_path)
        _check_state_md(tmp_path, brr)


class TestRepoWithPackageJson:
    def test_creates_files(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        (tmp_path / "package.json").write_text('{"scripts":{"test":"jest","build":"tsc"}}')
        _run_init(tmp_path, monkeypatch)
        brr = _check_agents_md(tmp_path)
        _check_state_md(tmp_path, brr)


class TestRepoWithExistingClaudeMd:
    def test_creates_agents_md_alongside(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Claude instructions\n")
        _run_init(tmp_path, monkeypatch)
        brr = _check_agents_md(tmp_path)
        _check_state_md(tmp_path, brr)
        assert (tmp_path / "CLAUDE.md").read_text() == "# Claude instructions\n"


class TestStateFileConfig:
    def test_custom_state_file(self, tmp_path, monkeypatch):
        _git_init(tmp_path)
        (tmp_path / "AGENTS.md").write_text(
            "---\nbrr:\n  version: 1\n  mode: paused\n"
            "  default_executor: auto\n"
            "  state_file: custom/state.md\n---\n"
        )
        _run_init(tmp_path, monkeypatch)
        assert not (tmp_path / ".brr.local" / "state.md").exists()
