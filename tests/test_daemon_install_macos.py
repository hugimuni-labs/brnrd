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


def _gone(cmd, **_kwargs):
    """`_ok`, except `launchctl print` reports the job is not in the domain.

    That is the honest post-`bootout` shape: `print` on a departed job
    exits non-zero. A double that answers 0 to everything makes the
    settle wait spin until its timeout, which is a test artifact, not a
    behaviour worth pinning.
    """
    if cmd[1] == "print":
        return subprocess.CompletedProcess(cmd, 113, stdout="", stderr="Could not find service")
    return _ok(cmd)


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
        return _gone(cmd)

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
            ["launchctl", "print", service],
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


def test_install_retries_launchd_eio_after_bootout(tmp_path):
    calls = []
    sleeps = []

    def flaky_run(cmd, **_kwargs):
        calls.append(cmd)
        bootstrap_calls = sum(call[1] == "bootstrap" for call in calls)
        if cmd[1] == "bootstrap" and bootstrap_calls < 3:
            return subprocess.CompletedProcess(
                cmd, 5, stdout="", stderr="Bootstrap failed: 5: Input/output error"
            )
        return _gone(cmd)

    result = macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=tmp_path / "home",
        workdir=tmp_path / "proj",
        run=flaky_run,
        sleep=sleeps.append,
        poll_timeout=0,
    )

    assert [cmd[1] for cmd in calls].count("bootstrap") == 3
    # Exponential, not flat: the old 4 x 0.25s budget could not outlast a
    # bootout of a daemon with live children, which is how a real install
    # spent all four attempts inside the teardown window (2026-08-29).
    assert sleeps == [0.25, 0.5]
    assert result.error is None


def test_install_waits_for_the_job_to_leave_the_domain_before_bootstrapping(tmp_path):
    """`bootout` is a request, not a receipt.

    The EIO class this guards is a bootstrap landing while launchd is still
    tearing the old job down. Reading `launchctl print` back until it fails
    is what makes the wait a measurement instead of a guess.
    """
    calls = []
    sleeps = []
    prints = {"n": 0}

    def staged_run(cmd, **_kwargs):
        calls.append(cmd[1])
        if cmd[1] == "print":
            prints["n"] += 1
            # Still shutting down for the first two polls.
            if prints["n"] <= 2:
                return _ok(cmd)
            return subprocess.CompletedProcess(cmd, 113, stdout="", stderr="not found")
        return _ok(cmd)

    macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=tmp_path / "home",
        workdir=tmp_path / "proj",
        run=staged_run,
        sleep=sleeps.append,
        poll_timeout=0,
    )

    assert calls == ["bootout", "print", "print", "print", "bootstrap", "kickstart"]
    assert sleeps == [0.2, 0.2]


def test_install_does_not_retry_a_real_bootstrap_failure(tmp_path):
    result = macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=tmp_path / "home",
        workdir=tmp_path / "proj",
        run=lambda cmd, **_k: (
            subprocess.CompletedProcess(cmd, 78, stdout="", stderr="invalid plist")
            if cmd[1] == "bootstrap" else _gone(cmd)
        ),
        sleep=lambda _seconds: pytest.fail("a real failure must not be retried"),
    )

    assert result.error == "invalid plist"


def test_install_returns_a_bootstrap_failure_instead_of_raising(tmp_path):
    """A `SystemExit` from here jumped over `brnrd connect`'s own degradation
    branch *and* the repo setup after it, leaving a machine paired,
    serviceless, and uninitialised (2026-08-29). The failure is data now."""
    calls = []

    def always_eio(cmd, **_kwargs):
        calls.append(cmd[1])
        if cmd[1] == "bootstrap":
            return subprocess.CompletedProcess(
                cmd, 5, stdout="",
                stderr="Bootstrap failed: 5: Input/output error\nTry re-running the command as root for richer errors.",
            )
        return _gone(cmd)

    result = macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=tmp_path / "home",
        workdir=tmp_path / "proj",
        run=always_eio,
        sleep=lambda _seconds: None,
        poll_timeout=0,
    )

    assert result.error is not None
    assert result.alive is False
    assert result.started is False
    # No point kickstarting a job launchd refused to load.
    assert "kickstart" not in calls
    # The plist is still on disk — `brnrd daemon install` is re-runnable.
    assert result.plist_path.exists()


def test_exhausted_bootstrap_never_repeats_launchds_root_advice(tmp_path):
    """launchd signs off with "Try re-running the command as root", and root
    is the one thing that cannot help a `gui/<uid>` domain."""
    result = macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=tmp_path / "home",
        workdir=tmp_path / "proj",
        run=lambda cmd, **_k: (
            subprocess.CompletedProcess(
                cmd, 5, stdout="",
                stderr="Bootstrap failed: 5: Input/output error\nTry re-running the command as root for richer errors.",
            )
            if cmd[1] == "bootstrap" else _gone(cmd)
        ),
        sleep=lambda _seconds: None,
        poll_timeout=0,
    )

    assert "root will not help" in result.error
    assert "user-domain" in result.error
    assert "brnrd daemon install" in result.error


def test_installed_workdir_reads_the_repo_out_of_the_plist(tmp_path):
    home = tmp_path / "home"
    macos.install(
        no_start=True,
        brr_path="/opt/homebrew/bin/brnrd",
        home=home,
        workdir="/Users/ada/src/proj",
        run=_gone,
    )

    assert macos.installed_workdir(home=home) == Path("/Users/ada/src/proj")


def test_installed_workdir_is_none_without_a_plist(tmp_path):
    assert macos.installed_workdir(home=tmp_path / "nothing") is None


def test_status_carries_the_workdir_the_service_is_pinned_to(tmp_path):
    """One machine has one LaunchAgent, pinned to the repo it was installed
    from — and it writes its pidfile there. A reader standing in a different
    checkout has to be able to ask where that is."""
    home = tmp_path / "home"
    macos.install(
        no_start=True,
        brr_path="/opt/homebrew/bin/brnrd",
        home=home,
        workdir="/Users/ada/src/other",
        run=_gone,
    )

    service = macos.status(home=home, run=_ok)

    assert service.installed is True
    assert service.workdir == Path("/Users/ada/src/other")


def test_install_confirms_liveness_by_reading_the_pidfile_back(tmp_path):
    """``launchctl kickstart`` returns the moment the job forks — a job
    that hard-exits before ``_write_pid`` (no ``AGENTS.md``) still reports
    a clean kickstart there. ``install()`` must read the pidfile the
    daemon itself writes before claiming anything survived (#1238)."""
    workdir = tmp_path / "proj"
    (workdir / ".brr").mkdir(parents=True)
    (workdir / ".brr" / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")

    def fail_if_slept(_seconds):
        pytest.fail("should not sleep when the pidfile is already there")

    result = macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=tmp_path / "home",
        workdir=workdir,
        run=_gone,
        sleep=fail_if_slept,
    )

    assert result.started is True
    assert result.alive is True
    assert result.pid == os.getpid()


def test_install_reports_not_alive_when_the_pidfile_never_appears(tmp_path):
    """The crash-loop case: the job forks, hard-exits before
    ``_write_pid``, and no pidfile ever lands. ``install()`` must say so
    rather than reporting the kickstart call's own success (#1238)."""
    workdir = tmp_path / "proj"
    (workdir / ".brr").mkdir(parents=True)
    slept = []

    result = macos.install(
        brr_path="/opt/homebrew/bin/brnrd",
        home=tmp_path / "home",
        workdir=workdir,
        run=_ok,
        poll_timeout=0.5,
        sleep=slept.append,
    )

    assert result.started is True
    assert result.alive is False
    assert result.pid is None
    assert slept, "must actually poll, not fail fast on the first miss"


def test_install_no_start_never_polls_for_liveness(tmp_path):
    """Nothing was kickstarted, so there is nothing to confirm — ``alive``
    stays ``None`` rather than reporting a verdict about a job that was
    never asked to start."""
    workdir = tmp_path / "proj"
    (workdir / ".brr").mkdir(parents=True)

    result = macos.install(
        no_start=True,
        brr_path="/opt/homebrew/bin/brnrd",
        home=tmp_path / "home",
        workdir=workdir,
        run=_ok,
        sleep=lambda _s: pytest.fail("no_start must skip the liveness poll"),
    )

    assert result.started is False
    assert result.alive is None
    assert result.pid is None


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
