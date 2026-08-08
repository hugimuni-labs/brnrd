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
