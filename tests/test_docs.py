"""Tests for bundled docs module and `brr docs` CLI."""

from __future__ import annotations

import pytest

from brr import docs
from brr.cli import main


def test_list_topics_includes_bundled():
    topics = docs.list_topics()
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
