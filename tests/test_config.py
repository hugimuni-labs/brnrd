"""Tests for config module — flat key=value parser."""

from brr.config import load_config, write_config


def test_load_missing(tmp_path):
    assert load_config(tmp_path) == {}


def test_roundtrip(tmp_path):
    write_config(tmp_path, {"runner": "codex", "auto_approve": True, "retries": 2})
    cfg = load_config(tmp_path)
    assert cfg["runner"] == "codex"
    assert cfg["auto_approve"] is True
    assert cfg["retries"] == 2


def test_comments_and_blanks(tmp_path):
    (tmp_path / ".brr").mkdir()
    (tmp_path / ".brr" / "config").write_text(
        "# comment\n\nrunner=claude\n  spaces = ignored  \n"
    )
    cfg = load_config(tmp_path)
    assert cfg["runner"] == "claude"
    assert cfg["spaces"] == "ignored"
