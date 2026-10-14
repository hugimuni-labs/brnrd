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
        lines.append(f'forge_token_file = "{token_file}"')
    if gh is not None:
        lines.append(f'gh = "{gh}"')
    (home / "loom" / "config.toml").write_text("\n".join(lines) + "\n")
    room = home / "rooms" / "s1"
    (room / "port").mkdir(parents=True)
    return room


def test_home_token_wins_over_the_machines(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "machine-token")
    monkeypatch.setenv("GITHUB_TOKEN", "machine-token-2")
    env = _env(_room(tmp_path, "home-token"))
    assert env["GH_TOKEN"] == "home-token"
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
