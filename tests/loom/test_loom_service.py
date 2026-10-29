"""``brnrd loom install|start|stop|status|uninstall|logs`` through argv.

``run`` stands in for launchctl and systemctl. Nothing here talks to the
machine's service manager.
"""

from __future__ import annotations

import functools
import types
import time

import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from brr.daemon_install import linux, macos
from brr.loom.runtime import entry, service


@pytest.fixture(autouse=True)
def _installed_brr(monkeypatch, tmp_path):
    """brr as uv tool installs it, and no coding CLI answering with fuel.

    The suite imports brr from ``src``: exactly the checkout install refuses.
    Tests about that refusal undo this. No test probes a real CLI's quota.
    """
    import sysconfig

    import brr

    monkeypatch.setattr(brr, "__file__", str(Path(sysconfig.get_paths()["purelib"]) / "brr" / "__init__.py"))
    monkeypatch.setattr(service, "_fuel", lambda home, shell: None)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("BRNRD_HOME", raising=False)


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


def test_macos_install_freezes_the_home_and_abandons_the_group(tmp_path, monkeypatch, capsys):
    # The installing shell has its venv's bin on PATH; the unit reads it through current.
    monkeypatch.setenv("PATH", f"{entry.prefix() / 'bin'}:/usr/bin")
    home = tmp_path / "home"
    _body(home)
    mac = _Mac()
    code = _install_mac(tmp_path, mac, home)
    assert code == 0
    plist = macos.plist_path(home=tmp_path / "agents", label=service.LABEL)
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "dev.brnrd.loom"
    assert data["AbandonProcessGroup"] is True
    # The unit runs the shim, never this interpreter or its venv (entry.py).
    assert data["ProgramArguments"][0] == str(entry.shim())
    assert data["ProgramArguments"][1:] == ["runtime", "loom", "--home", str(home.resolve())]
    plain = plist.read_text()
    assert sys.executable not in plain and str(entry.prefix()) not in plain
    assert data["EnvironmentVariables"]["PATH"] == f"{entry.shim().parent}:{entry.current() / 'bin'}:/usr/bin"
    # The shim runs current's python on brr's runtime CLI.
    ran = subprocess.run([data["ProgramArguments"][0], "runtime", "no-such-verb"],
                         capture_output=True, text=True)
    assert ran.returncode == 2 and "usage: brnrd runtime <verb>" in ran.stderr
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
    monkeypatch.setenv("PATH", f"{entry.prefix() / 'bin'}:/usr/bin")
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
    assert "KillMode=process" in text
    assert str(home.resolve()) in text
    assert f'ExecStart="{entry.shim()}" "runtime" "loom"' in text
    assert sys.executable not in text and str(entry.prefix()) not in text
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
    # service's own `time` only: patching `service.time.sleep` patched the module every
    # caller shares, and install's gh probe (communicate(timeout=)) slept into events on CI
    clock = types.ModuleType("time")
    clock.__dict__.update(time.__dict__)
    clock.sleep = lambda seconds: events.append(("sleep", seconds))
    monkeypatch.setattr(service, "time", clock)
    # the reinstall's PATH drops the conftest's signed-out gh, and a CI runner's /usr/bin/gh is real
    from brr.loom.runtime import gates
    monkeypatch.setattr(gates, "converge", functools.partial(gates.converge, gh=lambda: False))
    monkeypatch.setenv("PATH", "/initial/bin:/usr/bin")
    main(["loom", "install", "--home", str(home)])
    assert f"pid: {os.getpid()}" in capsys.readouterr().out
    if system == "Darwin":
        path = macos.plist_path(label=service.LABEL)
        data = plistlib.loads(path.read_bytes())
        assert data["AbandonProcessGroup"] is True
        assert data["EnvironmentVariables"]["PATH"] == f"{entry.shim().parent}:/initial/bin:/usr/bin"
        assert data["KeepAlive"] == {"SuccessfulExit": False}
        program = data["ProgramArguments"]
    else:
        path = linux.unit_path(service.UNIT)
        data = path.read_text()
        assert "KillMode=process\n" in data
        assert "Restart=on-failure\n" in data
        assert "MemoryHigh=" not in data and "MemoryMax=" not in data
        assert f'Environment="PATH={entry.shim().parent}:/initial/bin:/usr/bin"' in data
        import shlex
        program = shlex.split(next(line.split("=", 1)[1] for line in data.splitlines() if line.startswith("ExecStart=")))
    assert program[1:] == ["runtime", "loom", "--home", str(home)]
    assert "--adapter" not in program
    # Run the generated foreground argv's parser; it takes its body from the
    # config, rather than relying on the old fake adapter default.
    from brr.loom.runtime import __main__ as runtime_cli, verbs_loom
    choices = []
    monkeypatch.setattr(verbs_loom, "run_loom", lambda root, **kwargs: choices.append((root, kwargs)))
    assert runtime_cli.main(program[2:]) == 0
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
        assert f'Environment="PATH={entry.shim().parent}:/new/bin:/usr/bin"' in path.read_text()
    else:
        # Reading the running service's pid comes first; the first change is bootout.
        assert [e for e in events if e[1] != "print"][0][1] == "bootout"
        assert plistlib.loads(path.read_bytes())["WorkingDirectory"] == str(other)
        assert plistlib.loads(path.read_bytes())["EnvironmentVariables"]["PATH"] == f"{entry.shim().parent}:/new/bin:/usr/bin"
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
    # The receipt still prints, and claims no pid.
    assert "\npid: -\n" in output
    assert "4242" not in output


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


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_install_points_current_at_the_virtualenv_not_its_base(tmp_path, monkeypatch, system):
    home = tmp_path / "home"
    _body(home)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(linux, "supported", lambda: True)
    box = _Linux() if system == "Linux" else _Mac()
    assert service.main(["install", "--home", str(home), "--no-start"],
                        run=box.run, agents_home=tmp_path / "user", system=system) == 0
    # Resolving the venv's link would select the base Python, without brr.
    assert entry.current().resolve() == (tmp_path / "venv").resolve()
    assert entry.current().readlink() == tmp_path / "venv"


# ---- `brnrd loom install` is the whole install (4597) ----------------------


class _Manager:
    """launchctl/systemctl/lsof at the subprocess boundary, for ``brnrd`` argv.

    ``service_pid`` is the pid the service manager reports as already
    running before install; ``holders`` is what lsof says has the lock open.
    """

    def __init__(self, *, service_pid: int | None = None, holders=()):
        self.calls: list[list[str]] = []
        self.service_pid = service_pid
        self.holders = list(holders)
        self.at_bootstrap: list = []
        self.on_bootstrap = lambda: None

    def run(self, command, **_kwargs):
        command = list(command)
        self.calls.append(command)
        if command[0] == "lsof":
            return _ok(command, "".join(f"{pid}\n" for pid in self.holders))
        assert command[0] in {"launchctl", "systemctl"}
        if command[1] in {"bootout"} or command[-2:-1] == ["stop"]:
            self.service_pid = None
        if command[1] == "bootstrap" or "restart" in command:
            self.at_bootstrap.append(self.on_bootstrap())
        if command[1] == "kickstart" or "restart" in command:
            self.service_pid = os.getpid()
        if command[1] == "print":
            if self.service_pid is None:
                return _gone(command)
            return _ok(command, f"state = running\npid = {self.service_pid}\n")
        if "is-active" in command:
            return _ok(command, "active\n")
        if "show" in command:
            active = self.service_pid is not None
            return _ok(command, f"ActiveState={'active' if active else 'inactive'}\nMainPID={self.service_pid or 0}\n")
        return _ok(command)

    def changes(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] != "lsof" and c[1] not in {"print", "show", "is-active"}]


import os  # noqa: E402


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A person's machine as `brnrd` sees it: home folder, state root, service manager."""
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setattr(Path, "home", lambda: user)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(linux, "supported", lambda: True)
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)
    manager = _Manager()
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: manager.run(command, **kw))

    def platform(system):
        monkeypatch.setattr(service, "_platform", lambda _system: system)
        return manager

    return platform


def _brnrd(*argv: str) -> int:
    from brr.cli import main

    try:
        main(list(argv))
    except SystemExit as exc:
        if isinstance(exc.code, str):
            raise
        return exc.code or 0
    return 0


def _account_home(tmp_path: Path, account: str = "acc_1", name: str = "loom-home", body="claude/opus") -> Path:
    home = tmp_path / "state" / "brnrd" / "accounts" / account / name
    _body(home, body)
    return home


def _unit_program(system: str) -> list[str]:
    if system == "Darwin":
        return plistlib.loads(macos.plist_path(label=service.LABEL).read_bytes())["ProgramArguments"]
    text = linux.unit_path(service.UNIT).read_text()
    line = next(line for line in text.splitlines() if line.startswith("ExecStart="))
    import shlex
    return shlex.split(line.split("=", 1)[1])


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_cli_install_refuses_a_source_checkout_and_names_the_fix(tmp_path, machine, monkeypatch, system):
    import brr

    manager = machine(system)
    home = tmp_path / "home"
    _body(home)
    checkout = tmp_path / "Source" / "brnrd" / "src" / "brr" / "__init__.py"
    monkeypatch.setattr(brr, "__file__", str(checkout))
    with pytest.raises(SystemExit) as caught:
        _brnrd("loom", "install", "--home", str(home))
    text = str(caught.value)
    assert str(Path(sys.executable).absolute()) in text
    assert not entry.current().exists()
    assert str(checkout.parent) in text
    assert "deactivate; hash -r" in text and "--dev" in text
    assert manager.calls == []
    assert not macos.plist_path(label=service.LABEL).exists()
    assert not linux.unit_path(service.UNIT).exists()

    # --dev means it: current points at the checkout's venv, and the unit at the shim.
    assert _brnrd("loom", "install", "--home", str(home), "--dev") == 0
    assert _unit_program(system)[0] == str(entry.shim())
    assert entry.current().readlink() == entry.prefix()


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_cli_install_points_current_at_the_tool_and_prints_the_receipt(tmp_path, machine, monkeypatch, capsys, system):
    from brr import __version__

    machine(system)
    home = tmp_path / "home"
    _body(home, "claude/opus")
    tool = tmp_path / "user" / ".local" / "share" / "uv" / "tools" / "brnrd" / "bin" / "python3"
    tool.parent.mkdir(parents=True)
    tool.symlink_to(sys.executable)
    (tool.parent / "python").symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(tool))
    assert _brnrd("loom", "install", "--home", str(home)) == 0
    assert _unit_program(system)[0] == str(entry.shim())
    assert entry.current().readlink() == tool.parent.parent
    out = capsys.readouterr().out
    unit = macos.plist_path(label=service.LABEL) if system == "Darwin" else linux.unit_path(service.UNIT)
    for line in (
        f"interpreter: {tool}",
        f"brnrd: {__version__}\n",
        f"home: {home.resolve()} (flag)",
        "body: claude/opus (from config)",
        f"service: {unit}",
        f"entry: {entry.shim()}",
        "replaced: none",
        f"current: {entry.current()} -> {tool.parent.parent} (was nothing)",
        f"pid: {os.getpid()}",
    ):
        assert line in out


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_cli_install_needs_no_home_when_there_is_one(tmp_path, machine, capsys, system):
    machine(system)
    home = _account_home(tmp_path)
    assert _brnrd("loom", "install") == 0
    assert _unit_program(system)[-2:] == ["--home", str(home.resolve())]
    assert f"home: {home.resolve()} (the only one found)" in capsys.readouterr().out

    # A second home appears: the installed service's home still decides.
    _account_home(tmp_path, "acc_2")
    assert _brnrd("loom", "install") == 0
    assert f"home: {home.resolve()} (the installed service's)" in capsys.readouterr().out


def test_cli_install_lists_the_homes_when_more_than_one_could_be_meant(tmp_path, machine):
    manager = machine("Darwin")
    first = _account_home(tmp_path, "acc_1")
    second = _body(tmp_path / "user" / "brnrd") or tmp_path / "user" / "brnrd"
    with pytest.raises(SystemExit) as caught:
        _brnrd("loom", "install")
    text = str(caught.value)
    assert "--home" in text
    assert str(first.resolve()) in text and str(second.resolve()) in text
    assert manager.changes() == []
    assert not macos.plist_path(label=service.LABEL).exists()


def _lock_holder(home: Path) -> subprocess.Popen:
    """A loom outside the service: holds the loop lock until SIGTERM, as loom.run does."""
    lock = home / "loom" / "loop.lock"
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, os, signal, sys, time\n"
         "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR)\n"
         "fcntl.flock(fd, fcntl.LOCK_EX)\n"
         "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
         "print('held', flush=True)\n"
         "time.sleep(60)\n",
         str(lock)],
        stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "held"
    return child


real_run = subprocess.run


def test_cli_install_stops_a_loom_outside_the_service_before_starting(tmp_path, machine, capsys):
    manager = machine("Darwin")
    home = tmp_path / "home"
    _body(home)
    child = _lock_holder(home)
    try:
        manager.holders = [child.pid]
        manager.on_bootstrap = lambda: child.poll()
        assert _brnrd("loom", "install", "--home", str(home)) == 0
        # Stopped (SIGTERM, a clean exit) before the new service was loaded.
        assert manager.at_bootstrap == [0]
        assert child.wait(timeout=5) == 0
        assert not service._lock_held(home)
        out = capsys.readouterr().out
        assert f"replaced: pid {child.pid}, a loom outside the service · bodies keep running" in out
        assert f"pid: {os.getpid()}" in out
    finally:
        if child.poll() is None:
            child.kill()


def test_cli_install_from_a_prefix_with_no_python_stops_nothing(tmp_path, machine, monkeypatch):
    # Homebrew's bin has python3 but no python: the shim could not run it, so
    # install refuses before the running loom is stopped.
    manager = machine("Darwin")
    home = tmp_path / "home"
    _body(home)
    bare = tmp_path / "brew" / "bin" / "python3"
    bare.parent.mkdir(parents=True)
    bare.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(bare))
    child = _lock_holder(home)
    try:
        manager.holders = [child.pid]
        with pytest.raises(SystemExit, match="no bin/python"):
            _brnrd("loom", "install", "--home", str(home))
        assert child.poll() is None
        assert manager.changes() == []
        assert not entry.current().exists() and not entry.shim().exists()
        assert not macos.plist_path(label=service.LABEL).exists()
    finally:
        child.kill()
        child.wait()


def test_cli_install_leaves_the_services_own_loom_to_bootout(tmp_path, machine, capsys):
    """The service's loom is booted out by install, never signalled by hand."""
    manager = machine("Darwin")
    home = tmp_path / "home"
    _body(home)
    child = _lock_holder(home)
    try:
        manager.service_pid = child.pid
        manager.holders = [child.pid]
        assert _brnrd("loom", "install", "--home", str(home)) == 0
        assert child.poll() is None
        assert [c[1] for c in manager.changes()][:2] == ["bootout", "bootstrap"]
        assert f"replaced: pid {child.pid}, the service's loom · bodies keep running" in capsys.readouterr().out
    finally:
        child.kill()
        child.wait()


def test_cli_install_no_start_stops_nothing(tmp_path, machine, capsys):
    manager = machine("Darwin")
    home = tmp_path / "home"
    _body(home)
    child = _lock_holder(home)
    try:
        manager.holders = [child.pid]
        assert _brnrd("loom", "install", "--home", str(home), "--no-start") == 0
        assert child.poll() is None
        assert manager.calls == []
        assert "replaced: nothing (--no-start)" in capsys.readouterr().out
    finally:
        child.kill()
        child.wait()


def test_cli_install_picks_a_body_with_fuel_and_writes_it(tmp_path, machine, monkeypatch, capsys):
    from brr.loom.runtime.config import load_config, seed_config

    machine("Darwin")
    home = tmp_path / "home"
    seed_config(home)
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "config.toml").write_text('model = "gpt-6"\n\n[profiles.fast]\nmodel = "gpt-6-mini"\n')
    monkeypatch.setenv("CODEX_HOME", str(codex))
    asked = []
    monkeypatch.setattr(service, "_fuel", lambda h, shell: asked.append(shell) or ("week 80%" if shell == "codex" else None))
    assert _brnrd("loom", "install", "--home", str(home)) == 0
    assert asked == ["claude", "codex"]
    # Bare codex: its body reads the person's codex model at each run, so a later change there carries.
    assert _default_body(home) == "codex"
    out = capsys.readouterr().out
    assert "body: codex (picked: codex has fuel, week 80%; written to" in out
    # The rest of the seeded file is kept.
    assert "# brnrd loom config" in (home / "loom" / "config.toml").read_text()

    # Set once, it is the preference from then on: nothing is probed again.
    asked.clear()
    assert _brnrd("loom", "install", "--home", str(home)) == 0
    assert asked == []
    assert "body: codex (from preferences)" in capsys.readouterr().out


def test_cli_install_writes_the_pick_once_and_a_hand_edit_wins(tmp_path, machine, monkeypatch, capsys):
    """Sasha's home: install appended a second body line below the template's,
    and a hand edit of the template line lost to it. The pick now lives in
    ``loom/preferences.toml``, said once, and a later install reads it back."""
    from brr.loom.runtime.config import seed_config

    machine("Darwin")
    home = tmp_path / "home"
    seed_config(home)
    template = (home / "loom" / "config.toml").read_text()
    monkeypatch.setattr(service, "_fuel", lambda h, shell: "session 91% week 76%" if shell == "claude" else None)
    # gh signed out, said here: under xdist the conftest's signed-out gh once lost to a
    # signed-in one, and converge (rightly) wrote forge_token into the template
    from brr.loom.runtime import gates
    monkeypatch.setattr(gates, "converge", functools.partial(gates.converge, gh=lambda: False))
    assert _brnrd("loom", "install", "--home", str(home)) == 0
    assert (home / "loom" / "config.toml").read_text() == template
    path = home / "loom" / "preferences.toml"
    assert path.read_text() == 'model.default = "claude"\n'

    path.write_text('# mine\nmodel.default = "claude/opus"\n')
    capsys.readouterr()
    assert _brnrd("loom", "install", "--home", str(home)) == 0
    assert "body: claude/opus (from preferences)" in capsys.readouterr().out
    assert path.read_text() == '# mine\nmodel.default = "claude/opus"\n'


def _default_body(home):
    from brr.loom.runtime.adapters import _preferences
    from brr.loom.runtime.home import Home
    return _preferences(Home(home))[1].get("model.default")


def test_cli_install_folds_a_body_set_twice_keeping_the_one_in_effect(tmp_path, machine, monkeypatch, capsys):
    from brr.loom.runtime.config import ConfigError, load_config, render_config

    machine("Darwin")
    home = tmp_path / "home"
    path = home / "loom" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(render_config().replace('# body = ""', 'body = "claude/opus"') + 'body = "claude/sonnet"\n')
    with pytest.raises(ConfigError, match="body is set twice"):
        load_config(home)
    assert _brnrd("loom", "install", "--home", str(home)) == 0
    assert load_config(home).body == "claude/sonnet"
    assert "body: claude/sonnet (from config)" in capsys.readouterr().out
    assert '# folded, an earlier duplicate the last line overrode: body = "claude/opus"' in path.read_text()


def test_cli_install_with_no_body_writes_the_bare_claude_shell(tmp_path, machine, monkeypatch, capsys):
    from brr.loom.runtime.config import load_config, seed_config

    machine("Darwin")
    home = tmp_path / "home"
    seed_config(home)
    monkeypatch.setattr(service, "_fuel", lambda h, shell: "week 80%" if shell == "claude" else None)
    assert _brnrd("loom", "install", "--home", str(home)) == 0
    assert _default_body(home) == "claude"
    assert load_config(home).body is None  # the pick is a preference, not config
    assert "body: claude (picked: claude has fuel, week 80%; written to" in capsys.readouterr().out


def test_install_takes_a_bare_shell_body(tmp_path):
    home = tmp_path / "home"
    _body(home, "claude")
    assert _install_mac(tmp_path, _Mac(), home) == 0


def test_cli_install_dry_run_prints_the_receipt_and_changes_nothing(tmp_path, machine, monkeypatch, capsys):
    manager = machine("Darwin")
    home = _account_home(tmp_path, body=None)
    (home / "loom" / "install-id").write_text("ae66\n")  # a home the loom has run in, body unset
    monkeypatch.setattr(service, "_fuel", lambda h, shell: "session 91% week 76%" if shell == "claude" else None)
    child = _lock_holder(home)
    try:
        manager.holders = [child.pid]
        assert _brnrd("loom", "install", "--dry-run") == 0
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()
    out = capsys.readouterr().out
    assert "body: claude (picked: claude has fuel, session 91% week 76%; not written: dry run)" in out
    assert f"replaced: would stop pid {child.pid}, a loom outside the service · bodies keep running" in out
    assert f"home: {home.resolve()} (the only one found)" in out
    assert "pid: - (dry run" in out
    assert manager.changes() == []
    assert not macos.plist_path(label=service.LABEL).exists()
    assert not (home / "loom" / "config.toml").exists()
