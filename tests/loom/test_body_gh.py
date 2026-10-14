"""A body's gh speaks as the home's token or not at all, never as the person."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from brr.loom.runtime.loom import _env


def _room(tmp_path: Path, token: str | None) -> Path:
    home = tmp_path / "home"
    (home / "loom").mkdir(parents=True)
    lines = ['name = "t"']
    if token is not None:
        token_file = tmp_path / "forge.token"
        token_file.write_text(token + "\n")
        lines.append(f'forge_token_file = "{token_file}"')
    (home / "loom" / "config.toml").write_text("\n".join(lines) + "\n")
    room = home / "rooms" / "s1"
    (room / "port").mkdir(parents=True)
    return room


def test_home_token_becomes_gh_token_and_inherited_ones_go(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "person-token")
    monkeypatch.setenv("GITHUB_TOKEN", "person-token-2")
    env = _env(_room(tmp_path, "home-token"))
    assert env["GH_TOKEN"] == "home-token"
    assert "GITHUB_TOKEN" not in env


def test_no_home_token_means_signed_out_not_the_person(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "person-token")
    room = _room(tmp_path, None)
    env = _env(room)
    assert "GH_TOKEN" not in env
    gh_dir = Path(env["GH_CONFIG_DIR"])
    assert gh_dir.is_relative_to(room) and gh_dir.is_dir()


@pytest.mark.skipif(shutil.which("gh") is None, reason="gh not installed")
def test_real_gh_in_a_body_env_does_not_find_the_persons_login(tmp_path, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    env = _env(_room(tmp_path, None))
    proc = subprocess.run(["gh", "auth", "status"], env=env, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "not logged" in (proc.stdout + proc.stderr).lower()
