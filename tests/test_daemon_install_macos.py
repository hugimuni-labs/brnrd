"""Tests for macOS LaunchAgent daemon installation helpers."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

import pytest

from brr.daemon_install import macos


def _ok(cmd, **_kwargs):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_render_plist_matches_launchagent_shape(tmp_path):
    text = macos.render_plist(
        "/usr/local/bin/brnrd", home=tmp_path,
        path_env="/usr/local/bin:/usr/bin",
        workdir="/Users/ada/src/proj",
    )
    data = plistlib.loads(text.encode("utf-8"))

    assert data["Label"] == "dev.brnrd.brr"
    assert data["ProgramArguments"] == [
        "/usr/local/bin/brnrd",
        "daemon",
        "up",
        "--foreground",
    ]
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["WorkingDirectory"] == "/Users/ada/src/proj"
    assert data["StandardOutPath"] == str(tmp_path / "Library" / "Logs" / "brr" / "brr.out.log")
    assert data["StandardErrorPath"] == str(tmp_path / "Library" / "Logs" / "brr" / "brr.err.log")
    assert data["EnvironmentVariables"] == {
        "BRR_INSTALL_MANAGED": "1",
        "PATH": "/usr/local/bin:/usr/bin",
    }


def test_render_plist_freezes_installing_path_by_default(tmp_path, monkeypatch):
    """launchd's default PATH cannot resolve the runner Shells; the agent
    freezes the installing shell's PATH (re-running install refreshes it)."""
    monkeypatch.setenv("PATH", "/Users/ada/.local/bin:/opt/homebrew/bin:/usr/bin")
    text = macos.render_plist(
        "/opt/homebrew/bin/brnrd", home=tmp_path, workdir="/Users/ada/src/proj",
    )
    data = plistlib.loads(text.encode("utf-8"))
    assert data["EnvironmentVariables"]["PATH"] == (
        "/Users/ada/.local/bin:/opt/homebrew/bin:/usr/bin"
    )


def test_render_plist_resolves_workdir_from_installing_repo(tmp_path, monkeypatch):
    """launchd starts agents from ``/``; the plist must pin the repo the
    install ran from or ``daemon up`` crash-loops on "Not a Git repository"."""
    monkeypatch.setattr(
        macos, "resolve_workdir", lambda: Path("/Users/ada/src/resolved"),
    )
    text = macos.render_plist("/opt/homebrew/bin/brnrd", home=tmp_path)
    data = plistlib.loads(text.encode("utf-8"))
    assert data["WorkingDirectory"] == "/Users/ada/src/resolved"


def test_resolve_workdir_refuses_non_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="must run from inside the project"):
        macos.resolve_workdir()


def test_install_writes_plist_and_launchctl_commands(tmp_path):
    calls = []
    home = tmp_path / "home"

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _ok(cmd)

    result = macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=home,
        workdir="/Users/ada/src/proj",
        run=fake_run,
    )

    assert result.started is True
    assert result.plist_path == home / "Library" / "LaunchAgents" / "dev.brnrd.brr.plist"
    assert result.plist_path.exists()
    assert result.log_dir == home / "Library" / "Logs" / "brr"
    assert result.log_dir.exists()

    data = plistlib.loads(result.plist_path.read_bytes())
    assert data["ProgramArguments"] == [
        "/opt/homebrew/bin/brnrd",
        "daemon",
        "up",
        "--foreground",
    ]
    assert data["WorkingDirectory"] == "/Users/ada/src/proj"

    service = f"gui/{os.getuid()}/dev.brnrd.brr"
    assert calls == [
        (
            ["launchctl", "bootout", service],
            {"check": False, "capture_output": True, "text": True},
        ),
        (
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(result.plist_path)],
            {"check": False, "capture_output": True, "text": True},
        ),
        (
            ["launchctl", "kickstart", service],
            {"check": False, "capture_output": True, "text": True},
        ),
    ]


def test_install_no_start_skips_launchctl(tmp_path):
    calls = []

    result = macos.install(
        no_start=True,
        brr_path="/usr/local/bin/brnrd",
        home=tmp_path / "home",
        workdir="/Users/ada/src/proj",
        run=lambda cmd, **kwargs: calls.append((cmd, kwargs)) or _ok(cmd),
    )

    assert result.started is False
    assert result.plist_path.exists()
    assert calls == []


def test_uninstall_boots_out_and_removes_plist(tmp_path):
    calls = []
    home = tmp_path / "home"
    path = macos.plist_path(home=home)
    path.parent.mkdir(parents=True)
    path.write_text("plist", encoding="utf-8")

    result = macos.uninstall(
        home=home,
        run=lambda cmd, **kwargs: calls.append((cmd, kwargs)) or _ok(cmd),
    )

    assert result.removed is True
    assert not path.exists()
    assert calls[0][0] == ["launchctl", "bootout", f"gui/{os.getuid()}/dev.brnrd.brr"]


def test_logs_tails_launchagent_stdout_and_stderr(tmp_path):
    calls = []
    home = tmp_path / "home"

    macos.logs(
        home=home,
        lines=42,
        run=lambda cmd, **kwargs: calls.append((cmd, kwargs)) or _ok(cmd),
    )

    out_log, err_log = macos.log_paths(home=home)
    assert out_log.exists()
    assert err_log.exists()
    assert calls == [
        (
            ["tail", "-n", "42", "-F", str(out_log), str(err_log)],
            {"check": False},
        )
    ]
