"""Tests for config module — flat key=value parser."""

from brr import config
from brr.config import load_config, write_config


def test_load_missing(tmp_path):
    assert load_config(tmp_path) == {}


def test_roundtrip(tmp_path):
    write_config(tmp_path, {"runner": "codex", "enabled": True, "retries": 2})
    cfg = load_config(tmp_path)
    assert cfg["runner"] == "codex"
    assert cfg["enabled"] is True
    assert cfg["retries"] == 2


def test_comments_and_blanks(tmp_path):
    (tmp_path / ".brr").mkdir()
    (tmp_path / ".brr" / "config").write_text(
        "# comment\n\nrunner=claude\n  spaces = ignored  \n"
    )
    cfg = load_config(tmp_path)
    assert cfg["runner"] == "claude"
    assert cfg["spaces"] == "ignored"


def test_daemon_config_wins_but_repo_facts_stay_repo_owned(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(
        config, "security_config_path", lambda _root, _cfg=None: home / "security.config"
    )
    write_config(tmp_path, {"dispatch.burst_window_seconds": 2, "repo.label": "acme/app"})
    config.write_daemon_config(tmp_path, {"dispatch.burst_window_seconds": 9})

    assert load_config(tmp_path) == {
        "dispatch.burst_window_seconds": 9,
        "repo.label": "acme/app",
    }
    assert (home / "daemon.config").stat().st_mode & 0o777 == 0o600


def test_legacy_runner_pin_is_read_then_migrated_and_ignored(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(
        config, "security_config_path", lambda _root, _cfg=None: home / "security.config"
    )
    write_config(tmp_path, {"shell": "codex", "core": "mini"})
    assert load_config(tmp_path)["shell"] == "codex"

    logs = config.migrate_legacy_daemon_config(tmp_path)

    assert load_config(tmp_path)["runner.default"] == "codex-gpt-5.6-luna"
    assert "shell" not in load_config(tmp_path)
    assert any("shell=/core=" in line and "migrated" in line for line in logs)
    assert any("codex-mini resolved to codex-gpt-5.6-luna" in line for line in logs)


def test_config_table_names_the_winning_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(
        config, "security_config_path", lambda _root, _cfg=None: home / "security.config"
    )
    write_config(tmp_path, {"repo.label": "acme/app"})
    config.write_daemon_config(tmp_path, {"runner.default": "codex"})

    rows = {row["key"]: row for row in config.load_config_table(tmp_path)}
    assert rows["runner.default"]["source"].endswith("daemon.config")
    assert rows["repo.label"]["source"].endswith(".brr/config")
