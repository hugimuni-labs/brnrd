"""``daemon_install.status()`` dispatcher: exit code honesty (#1238).

A `launchd`/`systemd` "loaded" verdict only proves the job is registered
with the supervisor, not that the daemon process behind it is alive — a
`KeepAlive: {SuccessfulExit: False}` unit whose program hard-exits before
`_write_pid` (no `AGENTS.md`) reports "loaded" forever through a throttled
crash loop. These tests pin `status()` to the pidfile-backed verdict
(`daemon.read_pid`, the same file a launchd-managed and a plain foreground
daemon both write) rather than the supervisor's own bookkeeping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import daemon_install
from brr.daemon_install import macos as macos_mod


def _loaded_service(tmp_path: Path) -> macos_mod.ServiceStatus:
    return macos_mod.ServiceStatus(
        plist_path=tmp_path / "dev.brnrd.brr.plist",
        log_dir=tmp_path / "logs",
        installed=True,
        loaded=True,
        detail="",
    )


def test_status_is_nonzero_when_launchd_says_loaded_but_no_pidfile_exists(
    tmp_path, monkeypatch, capsys,
):
    # This suite may itself run on a Linux box with a real `brr.service`
    # installed — `status()` checks `linux.supported()` before `_is_macos()`,
    # so force the macOS branch explicitly rather than letting it fall
    # through to a real `systemctl status` against whatever happens to be
    # running on the host.
    monkeypatch.setattr(daemon_install.linux, "supported", lambda: False)
    monkeypatch.setattr(daemon_install, "_is_macos", lambda: True)
    monkeypatch.setattr(macos_mod, "status", lambda: _loaded_service(tmp_path))

    err_log = tmp_path / "logs" / "brr.err.log"
    err_log.parent.mkdir(parents=True)
    err_log.write_text("[brnrd] run `brnrd init` first\n", encoding="utf-8")
    monkeypatch.setattr(
        macos_mod,
        "log_paths",
        lambda **_kw: (tmp_path / "logs" / "brr.out.log", err_log),
    )

    brr_dir = tmp_path / "repo" / ".brr"
    brr_dir.mkdir(parents=True)  # no daemon.pid ⇒ the process never wrote one

    code = daemon_install.status(direct_brr_dir=brr_dir)

    assert code != 0
    out = capsys.readouterr().out
    assert "run `brnrd init` first" in out


def test_status_is_zero_when_launchd_loaded_and_pidfile_confirms_alive(
    tmp_path, monkeypatch,
):
    import os

    monkeypatch.setattr(daemon_install.linux, "supported", lambda: False)
    monkeypatch.setattr(daemon_install, "_is_macos", lambda: True)
    monkeypatch.setattr(macos_mod, "status", lambda: _loaded_service(tmp_path))

    brr_dir = tmp_path / "repo" / ".brr"
    brr_dir.mkdir(parents=True)
    (brr_dir / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")

    code = daemon_install.status(direct_brr_dir=brr_dir)

    assert code == 0


def test_status_finds_the_daemon_living_in_the_repo_the_plist_names(
    tmp_path, monkeypatch, capsys,
):
    """The second failure this branch confuses with a crash loop.

    One machine has one `dev.brnrd.brr`, pinned at install time to the repo
    it was installed from, and it writes its pidfile under *that* repo's
    `.brr/`. Connect a second repo and the pin moves — after which a status
    read from the first repo found no pidfile and announced a crash loop for
    a daemon that was serving it at that moment (2026-08-29, measured live
    on the maintainer's machine: `launchd: loaded` and `daemon process: not
    running`, two lines apart, both "true").
    """
    monkeypatch.setattr(daemon_install.linux, "supported", lambda: False)
    monkeypatch.setattr(daemon_install, "_is_macos", lambda: True)

    # The daemon's real home: another checkout, with a live pidfile.
    import os

    elsewhere = tmp_path / "other-repo"
    (elsewhere / ".brr").mkdir(parents=True)
    (elsewhere / ".brr" / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")

    service = macos_mod.ServiceStatus(
        plist_path=tmp_path / "dev.brnrd.brr.plist",
        log_dir=tmp_path / "logs",
        installed=True,
        loaded=True,
        detail="",
        workdir=elsewhere,
    )
    monkeypatch.setattr(macos_mod, "status", lambda: service)

    # This checkout: no pidfile at all.
    here = tmp_path / "this-repo" / ".brr"
    here.mkdir(parents=True)

    code = daemon_install.status(direct_brr_dir=here)

    out = capsys.readouterr().out
    assert code == 0
    assert f"daemon running (pid {os.getpid()})" in out
    assert str(elsewhere) in out
    assert "brnrd daemon install" in out
    # The crash-loop story is *wrong* here and must not be told anyway.
    assert "the daemon process is not running" not in out


def test_status_still_reports_a_crash_loop_when_the_named_repo_is_dead_too(
    tmp_path, monkeypatch, capsys,
):
    """The workdir lookup is an extra reading, never an excuse.

    A plist that names a repo whose pidfile is empty is the #1238 case
    unchanged — the supervisor says loaded and nothing is alive anywhere.
    """
    monkeypatch.setattr(daemon_install.linux, "supported", lambda: False)
    monkeypatch.setattr(daemon_install, "_is_macos", lambda: True)

    elsewhere = tmp_path / "other-repo"
    (elsewhere / ".brr").mkdir(parents=True)  # no daemon.pid

    service = macos_mod.ServiceStatus(
        plist_path=tmp_path / "dev.brnrd.brr.plist",
        log_dir=tmp_path / "logs",
        installed=True,
        loaded=True,
        detail="",
        workdir=elsewhere,
    )
    monkeypatch.setattr(macos_mod, "status", lambda: service)
    err_log = tmp_path / "logs" / "brr.err.log"
    err_log.parent.mkdir(parents=True)
    err_log.write_text("[brnrd] run `brnrd init` first\n", encoding="utf-8")
    monkeypatch.setattr(
        macos_mod, "log_paths",
        lambda **_kw: (tmp_path / "logs" / "brr.out.log", err_log),
    )

    here = tmp_path / "this-repo" / ".brr"
    here.mkdir(parents=True)

    code = daemon_install.status(direct_brr_dir=here)

    out = capsys.readouterr().out
    assert code != 0
    assert "the daemon process is not running" in out
