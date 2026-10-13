"""``brnrd loom install|start|stop|status|uninstall|logs`` through argv.

``run`` stands in for launchctl and systemctl. Nothing here talks to the
machine's service manager.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from brr.daemon_install import linux, macos
from brr.loom.runtime import service


def _body(home: Path, value: str | None = "grok/grok-4") -> None:
    path = home / "loom" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if value is None:
        return
    path.write_text(f'body = "{value}"\n', encoding="utf-8")


def _ok(cmd, stdout="", **_kwargs):
    return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")


class _Mac:
    def __init__(self, *, alive: bool = True, pid: int = 4242):
        self.calls: list[list[str]] = []
        self.slept: list[float] = []
        self.alive = alive
        self.pid = pid
        self.job_up = False

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def pid_alive(self, pid: int) -> bool:
        return self.alive and pid == self.pid

    def run(self, cmd, **_kwargs):
        recorded = list(cmd)
        self.calls.append(recorded)
        if len(recorded) >= 2 and recorded[1] == "kickstart":
            self.job_up = True
        if len(recorded) >= 2 and recorded[1] == "print":
            if not self.job_up:
                return _gone(cmd)
            return _ok(cmd, stdout=f"state = running\npid = {self.pid}\n")
        return _ok(cmd)


def _gone(cmd):
    return subprocess.CompletedProcess(cmd, 113, stdout="", stderr="Could not find service")


class _Linux:
    def __init__(self, *, alive: bool = True, pid: int = 4242):
        self.calls: list[list[str]] = []
        self.slept: list[float] = []
        self.trace: list[tuple] = []
        self.alive = alive
        self.pid = pid
        self.dead_after_active = False

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.trace.append(("sleep", seconds))

    def pid_alive(self, pid: int) -> bool:
        if self.dead_after_active:
            return False
        return self.alive and pid == self.pid

    def run(self, cmd, **_kwargs):
        recorded = list(cmd)
        self.calls.append(recorded)
        self.trace.append(("run", recorded))
        if "is-active" in recorded:
            return _ok(cmd, stdout="active\n")
        if "show" in recorded:
            return _ok(cmd, stdout=f"ActiveState=active\nMainPID={self.pid}\n")
        return _ok(cmd)


def _install_mac(tmp_path: Path, mac: _Mac, home: Path, *, extra: list[str] | None = None):
    return service.main(
        ["install", "--home", str(home), *(extra or [])],
        run=mac.run,
        agents_home=tmp_path / "agents",
        sleep=mac.sleep,
        pid_alive=mac.pid_alive,
        poll_timeout=1.25,
        system="Darwin",
    )


def test_install_refuses_when_body_is_unset_and_calls_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    mac = _Mac()
    with pytest.raises(SystemExit, match="body") as caught:
        _install_mac(tmp_path, mac, home)
    text = str(caught.value)
    assert "body" in text
    assert "config.toml" in text
    assert mac.calls == []


def test_install_refuses_a_body_that_is_not_shell_core(tmp_path):
    home = tmp_path / "home"
    _body(home, "nope")
    mac = _Mac()
    with pytest.raises(SystemExit, match="body"):
        _install_mac(tmp_path, mac, home)
    assert mac.calls == []


def test_macos_install_freezes_the_home_and_abandons_the_group(tmp_path, capsys):
    home = tmp_path / "home"
    _body(home)
    mac = _Mac()
    code = _install_mac(tmp_path, mac, home)
    assert code == 0
    plist = macos.plist_path(home=tmp_path / "agents", label=service.LABEL)
    data = plistlib.loads(plist.read_bytes())
    interpreter = str(Path(sys.executable).resolve())
    assert data["Label"] == "dev.brnrd.loom"
    assert data["AbandonProcessGroup"] is True
    assert data["ProgramArguments"][0] == interpreter
    assert Path(data["ProgramArguments"][0]).is_absolute()
    assert data["ProgramArguments"][1:] == [
        "-m", "brr.loom.runtime", "loom", "--home", str(home.resolve()),
    ]
    assert "--adapter" not in data["ProgramArguments"]
    assert data["WorkingDirectory"] == str(home.resolve())
    assert "brnrd-loom" in data["StandardOutPath"]
    assert mac.slept == [1.25]
    assert "pid: 4242" in capsys.readouterr().out
    # The pid print is only meaningful after the poll sleep, and the
    # confirming `launchctl print` is the one after kickstart.
    printed = [call for call in mac.calls if call[1:2] == ["print"]]
    assert len(printed) >= 2
    assert any(call[1:2] == ["kickstart"] for call in mac.calls)


def test_macos_pid_is_withheld_when_the_pid_is_already_dead(tmp_path, capsys):
    home = tmp_path / "home"
    _body(home)
    mac = _Mac(alive=False)
    code = _install_mac(tmp_path, mac, home)
    assert code == 1
    assert mac.slept == [1.25]
    out = capsys.readouterr().out
    assert "4242" not in out
    assert "not running after the poll interval" in out


def test_macos_verbs_after_install(tmp_path, capsys):
    home = tmp_path / "home"
    _body(home)
    (home / "loom" / "loom.log").write_text(
        "2026-10-13T14:00:00+00:00 start s-1 thread work gen 1\n",
        encoding="utf-8",
    )
    mac = _Mac()
    agents = tmp_path / "agents"
    assert _install_mac(tmp_path, mac, home, extra=["--no-start"]) == 0
    assert mac.calls == []
    mac.calls.clear()
    mac.slept.clear()
    assert service.main(
        ["start"], run=mac.run, agents_home=agents, sleep=mac.sleep,
        pid_alive=mac.pid_alive, poll_timeout=1.25, system="Darwin",
    ) == 0
    assert mac.slept == [1.25]
    assert any(call[1:2] == ["kickstart"] for call in mac.calls)
    assert "pid: 4242" in capsys.readouterr().out

    mac.calls.clear()
    assert service.main(
        ["status"], run=mac.run, agents_home=agents, sleep=mac.sleep,
        pid_alive=mac.pid_alive, system="Darwin",
    ) == 0
    status = capsys.readouterr().out
    assert "state: running" in status
    assert "pid: 4242" in status
    assert f"home: {home.resolve()}" in status
    assert "body: grok/grok-4" in status
    assert "start s-1 thread work gen 1" in status
    assert "log:" in status

    mac.calls.clear()
    assert service.main(
        ["logs", "--no-follow", "-n", "12"],
        run=mac.run, agents_home=agents, system="Darwin",
    ) == 0
    tail = mac.calls[-1]
    assert tail[:3] == ["tail", "-n", "12"]
    assert "-F" not in tail
    assert any("brnrd-loom" in part and part.endswith("brr.out.log") for part in tail)

    mac.calls.clear()
    assert service.main(
        ["stop"], run=mac.run, agents_home=agents, system="Darwin",
    ) == 0
    assert any(call[1:2] == ["bootout"] for call in mac.calls)
    assert service.LABEL in mac.calls[0][-1]

    mac.calls.clear()
    assert service.main(
        ["uninstall"], run=mac.run, agents_home=agents, system="Darwin",
    ) == 0
    assert any(call[1:2] == ["bootout"] for call in mac.calls)
    assert not macos.plist_path(home=agents, label=service.LABEL).exists()


def test_linux_unit_kills_only_the_process_and_names_the_home(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    _body(home)
    config = tmp_path / "config"
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setattr(linux, "supported", lambda: True)
    monkeypatch.setattr(linux, "linger_enabled", lambda _user: True)
    box = _Linux()
    code = service.main(
        ["install", "--home", str(home)],
        run=box.run,
        sleep=box.sleep,
        pid_alive=box.pid_alive,
        poll_timeout=1.25,
        system="Linux",
    )
    assert code == 0
    assert box.slept == [1.25]
    text = linux.unit_path(service.UNIT).read_text(encoding="utf-8")
    interpreter = str(Path(sys.executable).resolve())
    assert "KillMode=process" in text
    assert str(home.resolve()) in text
    assert interpreter in text
    assert "--home" in text
    assert "--adapter" not in text
    assert f"{state}/brnrd-loom/out.log" in text
    assert service.UNIT in [part for call in box.calls for part in call]
    show_at = next(i for i, item in enumerate(box.trace) if item[0] == "run" and "show" in item[1])
    slept_at = next(i for i, item in enumerate(box.trace) if item == ("sleep", 1.25))
    assert slept_at < show_at
    assert "pid: 4242" in capsys.readouterr().out

    box.dead_after_active = True
    box.slept.clear()
    code = service.main(
        ["start"],
        run=box.run,
        sleep=box.sleep,
        pid_alive=box.pid_alive,
        poll_timeout=1.25,
        system="Linux",
    )
    assert code == 1
    assert 1.25 in box.slept
    assert "4242" not in capsys.readouterr().out

    box.dead_after_active = False
    assert service.main(
        ["status"], run=box.run, pid_alive=box.pid_alive, system="Linux",
    ) == 0
    status = capsys.readouterr().out
    assert "state: active" in status
    assert "pid: 4242" in status
    assert f"home: {home.resolve()}" in status
    assert "body: grok/grok-4" in status

    box.calls.clear()
    assert service.main(
        ["logs", "-n", "4", "--no-follow"], run=box.run, system="Linux",
    ) == 0
    assert box.calls[-1][:3] == ["tail", "-n", "4"]
    assert service.main(["stop"], run=box.run, system="Linux") == 0
    assert ["systemctl", "--user", "stop", service.UNIT] in box.calls
    assert service.main(["uninstall"], run=box.run, system="Linux") == 0
    assert not linux.unit_path(service.UNIT).exists()


def test_cli_install_reaches_the_service(monkeypatch, tmp_path):
    seen: dict = {}

    def fake(argv, **_kwargs):
        seen["argv"] = list(argv)
        return 0

    monkeypatch.setattr("brr.loom.runtime.service.main", fake)
    from brr.cli import main

    main(["loom", "install", "--home", str(tmp_path)])
    assert seen["argv"] == ["install", "--home", str(tmp_path)]


def test_verb_without_adapter_refuses_an_unset_body(tmp_path):
    from brr.loom.runtime.verbs_loom import _loom

    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(SystemExit, match="body"):
        _loom(["--home", str(home)])


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_cli_service_lifecycle_at_subprocess_boundary(tmp_path, monkeypatch, capsys, system):
    """Actual CLI argv writes files and reaches only the loom's manager target."""
    import os
    from brr.cli import main

    home = tmp_path / "home with spaces"
    _body(home)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(service, "_platform", lambda _system: system)
    monkeypatch.setattr(linux, "supported", lambda: system == "Linux")
    events = []
    running = False

    def manager(command, **kwargs):
        nonlocal running
        events.append(tuple(command))
        assert command[0] in {"systemctl", "launchctl", "tail"}
        assert "dev.brnrd.brr" not in " ".join(command)
        assert linux.SERVICE_UNIT not in command
        if command[1] == "bootout" or "stop" in command:
            running = False
        if command[1] == "kickstart" or "restart" in command or "start" in command:
            running = True
        if command[1] == "print":
            return _ok(command, f"state = running\npid = {os.getpid()}\n") if running else _gone(command)
        if "is-active" in command:
            return _ok(command, "active\n")
        if "show" in command:
            return _ok(command, f"ActiveState={'active' if running else 'inactive'}\nMainPID={os.getpid() if running else 0}\n")
        return _ok(command)

    monkeypatch.setattr(subprocess, "run", manager)
    monkeypatch.setattr(service.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setenv("PATH", "/initial/bin:/usr/bin")
    main(["loom", "install", "--home", str(home)])
    assert f"pid: {os.getpid()}" in capsys.readouterr().out
    if system == "Darwin":
        path = macos.plist_path(label=service.LABEL)
        data = plistlib.loads(path.read_bytes())
        assert data["AbandonProcessGroup"] is True
        assert data["EnvironmentVariables"]["PATH"] == "/initial/bin:/usr/bin"
        assert data["KeepAlive"] == {"SuccessfulExit": False}
        program = data["ProgramArguments"]
    else:
        path = linux.unit_path(service.UNIT)
        data = path.read_text()
        assert "KillMode=process\n" in data
        assert "Restart=on-failure\n" in data
        assert "MemoryHigh=" not in data and "MemoryMax=" not in data
        assert 'Environment="PATH=/initial/bin:/usr/bin"' in data
        import shlex
        program = shlex.split(next(line.split("=", 1)[1] for line in data.splitlines() if line.startswith("ExecStart=")))
    assert program[1:] == ["-m", "brr.loom.runtime", "loom", "--home", str(home)]
    assert "--adapter" not in program
    # Run the generated foreground argv's parser; it takes its body from the
    # config, rather than relying on the old fake adapter default.
    from brr.loom.runtime import __main__ as runtime_cli, verbs_loom
    choices = []
    monkeypatch.setattr(verbs_loom, "run_loom", lambda root, **kwargs: choices.append((root, kwargs)))
    assert runtime_cli.main(program[3:]) == 0
    assert choices[0][1]["adapter"] == "grok"
    assert choices[0][1]["core"] == "grok-4"

    # Reinstall refreshes the home and PATH and actually restarts the job.
    other = tmp_path / "other-home"
    _body(other, "codex/gpt-6")
    monkeypatch.setenv("PATH", "/new/bin:/usr/bin")
    events.clear()
    main(["loom", "install", "--home", str(other)])
    if system == "Linux":
        assert ("systemctl", "--user", "restart", service.UNIT) in events
        assert f"WorkingDirectory={other}" in path.read_text()
        assert 'Environment="PATH=/new/bin:/usr/bin"' in path.read_text()
    else:
        assert events[0][1] == "bootout"
        assert plistlib.loads(path.read_bytes())["WorkingDirectory"] == str(other)
        assert plistlib.loads(path.read_bytes())["EnvironmentVariables"]["PATH"] == "/new/bin:/usr/bin"
    capsys.readouterr()
    main(["loom", "status"])
    output = capsys.readouterr().out
    assert f"home: {other}" in output
    assert "body: codex/gpt-6" in output
    main(["loom", "stop"])
    assert not running
    main(["loom", "start"])
    assert running
    main(["loom", "logs", "--no-follow"])
    assert events[-1][0] == "tail"
    main(["loom", "uninstall"])
    assert not path.exists()


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
@pytest.mark.parametrize("choice", [None, "", "not-a-body", "/core", "shell/"])
def test_unconfigured_install_does_not_write_a_service(tmp_path, monkeypatch, system, choice):
    home = tmp_path / "home"
    _body(home, choice)
    calls = []
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    with pytest.raises(SystemExit, match="body"):
        service.main(["install", "--home", str(home)], system=system, run=lambda *a, **k: calls.append(a))
    assert calls == []
    assert not linux.unit_path(service.UNIT).exists()
    assert not macos.plist_path(label=service.LABEL).exists()


def test_macos_bootstrap_failure_is_failure(tmp_path, capsys):
    home = tmp_path / "home"
    _body(home)
    box = _Mac()

    def fail_bootstrap(command, **kwargs):
        if command[1] == "bootstrap":
            return subprocess.CompletedProcess(command, 1, "", "permission denied")
        return box.run(command, **kwargs)

    code = service.main(["install", "--home", str(home)], run=fail_bootstrap,
                        agents_home=tmp_path / "user", sleep=box.sleep, system="Darwin")
    assert code == 1
    output = capsys.readouterr().out
    assert "permission denied" in output
    assert "next login" not in output
    assert "pid:" not in output


@pytest.mark.parametrize("verb", ["stop", "start", "status", "uninstall"])
def test_service_verbs_reject_extra_arguments(verb):
    calls = []
    with pytest.raises(SystemExit) as raised:
        service.main([verb, "--mistyped"], run=lambda *a, **k: calls.append(a))
    assert raised.value.code == 2
    assert calls == []


def test_macos_stop_reports_bootout_failure():
    assert service.main(["stop"], system="Darwin", run=lambda cmd, **_: subprocess.CompletedProcess(cmd, 5, "", "EIO")) == 5


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_status_shows_last_nonempty_log_line_with_age(tmp_path, monkeypatch, capsys, system):
    from datetime import datetime, timezone
    home = tmp_path / "home"
    _body(home)
    (home / "loom" / "loom.log").write_text(
        "2026-10-13T12:00:00+00:00 old entry\n"
        "2026-10-13T14:00:00+00:00 latest entry\n\n",
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 13, 14, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(linux, "supported", lambda: True)
    box = _Linux() if system == "Linux" else _Mac()
    kwargs = dict(system=system, run=box.run, sleep=box.sleep,
                  pid_alive=box.pid_alive, agents_home=tmp_path / "user")
    assert service.main(["install", "--home", str(home), "--no-start"], **kwargs) == 0
    if system == "Darwin":
        box.job_up = True
    capsys.readouterr()
    assert service.main(["status"], **kwargs) == 0
    output = capsys.readouterr().out
    assert "log: 2026-10-13T14:00:00+00:00 latest entry (2m)" in output
    assert "old entry" not in output


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_start_does_not_trust_failed_manager_pid_readback(tmp_path, monkeypatch, capsys, system):
    home = tmp_path / "home"
    _body(home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(linux, "supported", lambda: True)
    box = _Linux() if system == "Linux" else _Mac()
    kwargs = dict(system=system, run=box.run, sleep=box.sleep,
                  pid_alive=box.pid_alive, agents_home=tmp_path / "user")
    assert service.main(["install", "--home", str(home), "--no-start"], **kwargs) == 0
    capsys.readouterr()

    def failed_read(command, **kw):
        result = box.run(command, **kw)
        if "show" in command or command[1] == "print":
            result.returncode = 1
        return result

    kwargs["run"] = failed_read
    assert service.main(["start"], **kwargs) == 1
    assert "pid: 4242" not in capsys.readouterr().out
