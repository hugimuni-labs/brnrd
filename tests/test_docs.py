"""Tests for bundled docs module and `brr docs` CLI."""

from __future__ import annotations

import pytest

from brr import docs
from brr.cli import main


def test_list_topics_includes_bundled():
    topics = docs.list_topics()
    assert "active-task" in topics
    assert "execution-map" in topics
    assert "brr-internals" in topics


def test_read_topic_bundled_returns_content():
    text = docs.read_topic("execution-map")
    assert text is not None
    assert "Execution Map" in text


def test_read_topic_unknown_returns_none():
    assert docs.read_topic("does-not-exist") is None


def test_read_topic_rejects_traversal():
    assert docs.read_topic("../pyproject") is None
    assert docs.read_topic(".hidden") is None
    assert docs.read_topic("") is None


def test_read_topic_override_wins(tmp_path):
    overrides = tmp_path / ".brr" / "docs"
    overrides.mkdir(parents=True)
    (overrides / "execution-map.md").write_text("# custom override")

    text = docs.read_topic("execution-map", repo_root=tmp_path)
    assert text == "# custom override"


def test_list_topics_includes_override_additions(tmp_path):
    overrides = tmp_path / ".brr" / "docs"
    overrides.mkdir(parents=True)
    (overrides / "repo-specific.md").write_text("# repo specific")

    topics = docs.list_topics(repo_root=tmp_path)
    assert "repo-specific" in topics
    assert "execution-map" in topics  # bundled still listed


def test_format_listing_marks_overrides(tmp_path):
    overrides = tmp_path / ".brr" / "docs"
    overrides.mkdir(parents=True)
    (overrides / "execution-map.md").write_text("# custom")

    listing = docs.format_listing(repo_root=tmp_path)
    assert "execution-map" in listing
    assert "(overridden)" in listing


def test_read_topic_uses_shared_runtime_override_for_worktree(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    overrides = repo / ".brr" / "docs"
    overrides.mkdir(parents=True)
    (overrides / "execution-map.md").write_text("# worktree override", encoding="utf-8")
    worktree = repo / ".brr" / "worktrees" / "task-1"
    subprocess.run(
        ["git", "worktree", "add", "-b", "brr/task-1", str(worktree), "HEAD"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )

    try:
        text = docs.read_topic("execution-map", repo_root=worktree)
        assert text == "# worktree override"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, check=True)
        subprocess.run(["git", "branch", "-D", "brr/task-1"], cwd=repo, check=True, stdout=subprocess.PIPE)


def test_cli_docs_list_outside_repo(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    main(["docs"])
    out = capsys.readouterr().out
    assert "execution-map" in out
    assert "brr-internals" in out


def test_cli_docs_show_topic(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    main(["docs", "brr-internals"])
    out = capsys.readouterr().out
    assert "brr Internals" in out


def test_cli_docs_unknown_topic_errors(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["docs", "nonexistent-topic"])
    assert "unknown doc topic" in str(exc.value)
