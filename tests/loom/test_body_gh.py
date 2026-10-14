"""A body's gh: the home's token when it has one, else the machine's login; off signs it out."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from brr.loom.runtime.loom import _env


def _room(tmp_path: Path, token: str | None, gh: str | None = None) -> Path:
    home = tmp_path / "home"
    (home / "loom").mkdir(parents=True)
    lines = ['name = "t"']
    if token is not None:
        token_file = tmp_path / "forge.token"
        token_file.write_text(token + "\n")
        lines.append(f'forge_token = "file: {token_file}"')
    if gh is not None:
        lines.append(f'gh = "{gh}"')
    (home / "loom" / "config.toml").write_text("\n".join(lines) + "\n")
    room = home / "rooms" / "s1"
    (room / "port").mkdir(parents=True)
    return room


def test_home_token_wins_over_the_machines(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "machine-token")
    monkeypatch.setenv("GITHUB_TOKEN", "machine-token-2")
    room = _room(tmp_path, "home-token")
    env = _env(room)
    assert "GH_TOKEN" not in env
    assert _call_gh(env).stdout == "home-token\n"
    (tmp_path / "forge.token").write_text("refreshed-token\n")
    assert _call_gh(env).stdout == "refreshed-token\n"
    assert "GITHUB_TOKEN" not in env


def test_no_home_token_leaves_the_machines_gh(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "machine-token")
    monkeypatch.delenv("GH_CONFIG_DIR", raising=False)
    env = _env(_room(tmp_path, None))
    assert env["GH_TOKEN"] == "machine-token"
    assert "GH_CONFIG_DIR" not in env


def test_off_signs_gh_out_even_with_a_token(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "machine-token")
    room = _room(tmp_path, "home-token", gh="off")
    env = _env(room)
    assert "GH_TOKEN" not in env
    assert Path(env["GH_CONFIG_DIR"]).is_relative_to(room)


def test_an_unknown_gh_value_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _env(_room(tmp_path, None, gh="person"))


@pytest.mark.skipif(shutil.which("gh") is None, reason="gh not installed")
def test_real_gh_is_signed_out_when_off(tmp_path, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    env = _env(_room(tmp_path, None, gh="off"))
    proc = subprocess.run(["gh", "auth", "status"], env=env, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "not logged" in (proc.stdout + proc.stderr).lower()


def _fake_gh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$GH_TOKEN"\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + ":/usr/bin:/bin")
    return gh


def _call_gh(env):
    return subprocess.run(["gh"], env=env, capture_output=True, text=True, timeout=5)


def test_no_real_gh_logs_and_does_not_make_a_shim(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "missing"))
    monkeypatch.setenv("GH_TOKEN", "machine-token")
    room = _room(tmp_path, "home-token")
    env = _env(room)
    assert "GH_TOKEN" not in env
    assert not (room / "port" / "bin" / "gh").exists()
    assert "gh not found" in (room.parent.parent / "loom" / "loom.log").read_text()


@pytest.mark.parametrize("source", ["file", "command"])
def test_failed_or_empty_source_never_calls_real_gh(tmp_path, monkeypatch, source):
    real = _fake_gh(tmp_path, monkeypatch)
    called = tmp_path / "called"
    real.write_text(f'#!/bin/sh\ntouch "{called}"\n')
    room = _room(tmp_path, "")
    if source == "command":
        (room.parent.parent / "loom" / "config.toml").write_text(
            'forge_token = "command: echo leaked-token >&2; exit 7"\n')
    proc = _call_gh(_env(room))
    assert proc.returncode == 1
    assert not called.exists()
    assert "forge_token source" in proc.stderr
    assert "leaked-token" not in proc.stderr + proc.stdout


def test_command_gh_source_uses_real_gh_without_recursion(tmp_path, monkeypatch):
    real = _fake_gh(tmp_path, monkeypatch)
    real.write_text('#!/bin/sh\nif [ "$1" = auth ]; then echo login-token; else echo "$GH_TOKEN"; fi\n')
    room = _room(tmp_path, None)
    (room.parent.parent / "loom" / "config.toml").write_text(
        'forge_token = "command: gh auth token"\n')
    assert _call_gh(_env(room)).stdout == "login-token\n"


@pytest.mark.parametrize("variable", ["GH_TOKEN", "CUSTOM_TOKEN"])
def test_env_source_is_available_to_shim(tmp_path, monkeypatch, variable):
    _fake_gh(tmp_path, monkeypatch)
    monkeypatch.setenv(variable, "source-token")
    room = _room(tmp_path, None)
    (room.parent.parent / "loom" / "config.toml").write_text(
        f'forge_token = "env: {variable}"\n')
    assert _call_gh(_env(room)).stdout == "source-token\n"
