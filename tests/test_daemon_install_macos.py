"""Tests for macOS LaunchAgent daemon installation helpers."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from brr.daemon_install import macos


def _ok(cmd, **_kwargs):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_render_plist_matches_launchagent_shape(tmp_path):
    text = macos.render_plist("/usr/local/bin/brnrd", home=tmp_path)
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
    assert data["StandardOutPath"] == str(tmp_path / "Library" / "Logs" / "brr" / "brr.out.log")
    assert data["StandardErrorPath"] == str(tmp_path / "Library" / "Logs" / "brr" / "brr.err.log")
    assert data["EnvironmentVariables"] == {"BRR_INSTALL_MANAGED": "1"}
    assert "WorkingDirectory" not in data


def test_install_writes_plist_registry_and_launchctl_commands(tmp_path):
    calls = []
    home = tmp_path / "home"
    config_home = tmp_path / "config"

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _ok(cmd)

    result = macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=home,
        config_home=config_home,
        run=fake_run,
    )

    assert result.started is True
    assert result.plist_path == home / "Library" / "LaunchAgents" / "dev.brnrd.brr.plist"
    assert result.plist_path.exists()
    assert (config_home / "brr" / "projects.toml").exists()
    assert result.log_dir == home / "Library" / "Logs" / "brr"
    assert result.log_dir.exists()

    data = plistlib.loads(result.plist_path.read_bytes())
    assert data["ProgramArguments"] == [
        "/opt/homebrew/bin/brnrd",
        "daemon",
        "up",
        "--foreground",
    ]
    assert "WorkingDirectory" not in data

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
        config_home=tmp_path / "config",
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


def test_enabled_projects_reads_registry_when_tomllib_is_available(tmp_path):
    if macos.tomllib is None:
        return
    config_home = tmp_path / "config"
    path = macos.project_registry_path(config_home=config_home)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
[[projects]]
path = "/tmp/enabled"
enabled = true

[[projects]]
path = "/tmp/default-enabled"

[[projects]]
path = "/tmp/disabled"
enabled = false
""".strip(),
        encoding="utf-8",
    )

    assert macos.enabled_projects(config_home=config_home) == [
        Path("/tmp/enabled"),
        Path("/tmp/default-enabled"),
    ]
