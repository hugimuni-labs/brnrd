"""Tests for Linux systemd user-service installation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr.daemon_install import linux


def test_render_systemd_unit_matches_machine_scoped_template():
    unit = linux.render_systemd_unit(
        "/opt/venv/bin/brnrd", path_env="/opt/venv/bin:/usr/bin",
        workdir="/home/ada/src/proj",
    )

    assert "Description=brnrd daemon (machine-scoped multi-project multiplexer)" in unit
    assert "ExecStart=/opt/venv/bin/brnrd daemon up --foreground" in unit
    assert "WorkingDirectory=/home/ada/src/proj" in unit
    assert "Environment=BRR_INSTALL_MANAGED=1" in unit
    assert 'Environment="PATH=/opt/venv/bin:/usr/bin"' in unit


def test_render_systemd_unit_pins_resolved_binary_path_and_workdir(monkeypatch):
    """The user manager's PATH is thin (no venv, no ~/.local/bin, no nvm) and
    its default cwd is $HOME, not a repo: the unit must pin the binary that
    ran the install, freeze the installing shell's PATH, and freeze the
    installing repo's root — or the daemon crash-loops on
    "Not a Git repository" however correct its binary is."""
    monkeypatch.setattr(linux, "resolve_brr_bin", lambda: "/home/ada/.venv/bin/brnrd")
    monkeypatch.setattr(
        linux, "resolve_workdir", lambda: Path("/home/ada/src/proj"),
    )
    monkeypatch.setenv("PATH", "/home/ada/.venv/bin:/home/ada/.local/bin:/usr/bin")

    unit = linux.render_systemd_unit()

    assert "ExecStart=/home/ada/.venv/bin/brnrd daemon up --foreground" in unit
    assert "WorkingDirectory=/home/ada/src/proj" in unit
    assert (
        'Environment="PATH=/home/ada/.venv/bin:/home/ada/.local/bin:/usr/bin"'
        in unit
    )


def test_render_systemd_unit_escapes_percent_in_path():
    unit = linux.render_systemd_unit(
        "/opt/brnrd", path_env="/odd%dir/bin:/usr/bin",
        workdir="/home/ada/src/proj",
    )
    assert 'Environment="PATH=/odd%%dir/bin:/usr/bin"' in unit


def test_resolve_workdir_refuses_non_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="must run from inside the project"):
        linux.resolve_workdir()


def test_install_writes_unit_and_enables_without_starting(
    tmp_path, monkeypatch,
):
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command, *, check=True):
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(linux, "supported", lambda: True)
    monkeypatch.setattr(linux, "linger_enabled", lambda _user: True)
    monkeypatch.setattr(linux, "_run", fake_run)
    monkeypatch.setattr(linux, "resolve_brr_bin", lambda: "/opt/venv/bin/brnrd")
    monkeypatch.setattr(
        linux, "resolve_workdir", lambda: Path("/home/ada/src/proj"),
    )

    linux.install(no_start=True, prompt_linger=False)

    assert linux.unit_path().read_text(encoding="utf-8") == linux.render_systemd_unit()
    assert calls == [
        (["systemctl", "--user", "daemon-reload"], True),
        (["systemctl", "--user", "enable", linux.SERVICE_UNIT], True),
    ]


def test_install_verifies_the_service_survived_its_start(tmp_path, monkeypatch, capsys):
    """``systemctl start`` on a Type=simple unit returns 0 the moment the
    process forks — a service that crashes 200ms in still reports a clean
    start. Install must probe ``is-active`` a beat later and say so."""
    run_calls: list[list[str]] = []

    def fake_run(command, *, check=True):
        run_calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    def fake_capture(command):
        return subprocess.CompletedProcess(command, 3, stdout="activating\n", stderr="")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(linux, "supported", lambda: True)
    monkeypatch.setattr(linux, "linger_enabled", lambda _user: True)
    monkeypatch.setattr(linux, "_run", fake_run)
    monkeypatch.setattr(linux, "_capture", fake_capture)
    monkeypatch.setattr(linux.time, "sleep", lambda _s: None)
    monkeypatch.setattr(linux, "resolve_brr_bin", lambda: "/opt/venv/bin/brnrd")
    monkeypatch.setattr(
        linux, "resolve_workdir", lambda: Path("/home/ada/src/proj"),
    )

    linux.install(prompt_linger=False)

    assert ["systemctl", "--user", "start", linux.SERVICE_UNIT] in run_calls
    out = capsys.readouterr().out
    assert "not running (state: activating)" in out
    assert "brnrd daemon logs" in out


def test_start_service_fails_loud_when_the_unit_dies_after_start(monkeypatch):
    def fake_run(command, *, check=True):
        return subprocess.CompletedProcess(command, 0)

    def fake_capture(command):
        return subprocess.CompletedProcess(command, 3, stdout="failed\n", stderr="")

    monkeypatch.setattr(linux, "_run", fake_run)
    monkeypatch.setattr(linux, "_capture", fake_capture)
    monkeypatch.setattr(linux.time, "sleep", lambda _s: None)

    assert linux.start_service() == 1


def test_start_service_succeeds_when_the_unit_stays_active(monkeypatch):
    def fake_run(command, *, check=True):
        return subprocess.CompletedProcess(command, 0)

    def fake_capture(command):
        return subprocess.CompletedProcess(command, 0, stdout="active\n", stderr="")

    monkeypatch.setattr(linux, "_run", fake_run)
    monkeypatch.setattr(linux, "_capture", fake_capture)
    monkeypatch.setattr(linux.time, "sleep", lambda _s: None)

    assert linux.start_service() == 0


def test_install_can_enable_linger_without_prompt(tmp_path, monkeypatch):
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command, *, check=True):
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("USER", "ada")
    monkeypatch.setattr(linux, "supported", lambda: True)
    monkeypatch.setattr(linux, "linger_enabled", lambda _user: False)
    monkeypatch.setattr(linux, "_run", fake_run)
    monkeypatch.setattr(
        linux, "resolve_workdir", lambda: Path("/home/ada/src/proj"),
    )

    linux.install(no_start=True, prompt_linger=False, assume_yes_linger=True)

    assert (tmp_path / "state" / "brr" / "systemd-linger-enabled-by-brr").read_text(
        encoding="utf-8",
    ) == "ada\n"
    assert calls[0] == (["sudo", "loginctl", "enable-linger", "ada"], True)


def test_uninstall_removes_unit_and_leaves_linger_by_default(tmp_path, monkeypatch):
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command, *, check=True):
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(linux, "supported", lambda: True)
    monkeypatch.setattr(linux, "_run", fake_run)
    monkeypatch.setattr(linux, "resolve_brr_bin", lambda: "/opt/venv/bin/brnrd")
    monkeypatch.setattr(
        linux, "resolve_workdir", lambda: Path("/home/ada/src/proj"),
    )

    linux.write_unit_file()
    marker = linux.linger_marker_path()
    marker.parent.mkdir(parents=True)
    marker.write_text("ada\n", encoding="utf-8")

    linux.uninstall(prompt_linger=False)

    assert not linux.unit_path().exists()
    assert not marker.exists()
    assert calls == [
        (["systemctl", "--user", "stop", linux.SERVICE_UNIT], False),
        (["systemctl", "--user", "disable", linux.SERVICE_UNIT], False),
        (["systemctl", "--user", "daemon-reload"], False),
    ]


def test_status_and_logs_use_systemd_user_commands(monkeypatch):
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command, *, check=True):
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(linux, "_run", fake_run)

    assert linux.status() == 7
    assert linux.logs(lines=50, follow=False) == 7
    assert calls == [
        (["systemctl", "--user", "status", linux.SERVICE_UNIT, "--no-pager"], False),
        (["journalctl", "--user", "-u", "brr", "-n", "50"], False),
    ]
