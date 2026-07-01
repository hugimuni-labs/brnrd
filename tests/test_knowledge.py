"""Tests for the home/repo knowledge source chain."""

import subprocess

from brr import knowledge
from brr.prompts import _build_knowledge_sources_block

from _helpers import init_git_repo


def test_prompt_injects_home_knowledge_without_repo_kb(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text(
        "# Home Knowledge\n\nResident-only note.", encoding="utf-8"
    )
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text(f"home.path={home}\n", encoding="utf-8")

    block = _build_knowledge_sources_block(repo)

    assert "Knowledge Sources" in block
    assert "Home Knowledge" in block
    assert "Resident-only note" in block


def test_knowledge_injection_orders_home_before_repo_kb(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text("home alpha", encoding="utf-8")
    (repo / "kb").mkdir()
    (repo / "kb" / "index.md").write_text("repo alpha", encoding="utf-8")

    block = knowledge.render_injection(repo, {"home.path": str(home)})

    assert block.index("home alpha") < block.index("repo alpha")


def test_search_reads_home_then_repo_sources(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text("shared needle home", encoding="utf-8")
    (repo / "kb").mkdir()
    (repo / "kb" / "index.md").write_text("shared needle repo", encoding="utf-8")

    hits = knowledge.search(repo, "needle", {"home.path": str(home)})

    assert [hit.source for hit in hits[:2]] == ["home knowledge", "repo KB"]


def test_checkout_is_gitignored_from_project_status(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"

    checkout = knowledge.ensure_checkout(repo, {"home.path": str(home)})

    assert checkout == repo / ".brnrd-kb"
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".brnrd-kb" not in status
    exclude = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert ".brnrd-kb/" in (repo / exclude).read_text(encoding="utf-8")
