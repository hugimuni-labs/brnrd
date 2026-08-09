"""#1244 fork 1 — the daemon boots, pairs, and polls with no ``AGENTS.md``.

Before this fork, ``daemon.start()`` hard-exited (``SystemExit``, "run
`brnrd init` first") before it ever wrote a pidfile. Correct advice, wrong
place: under a service manager with ``KeepAlive``/``Restart=on-failure``
that turned into a throttled crash loop with no pidfile ever written
(#1238 worked around the *symptom* — service status/logs, and skipping the
install from ``brnrd connect`` — without touching this root cause).

These tests drive the fixed behaviour directly: boot proceeds with an
informational print instead of an exit, and a genuinely fresh repo (no
``.brr/`` at all — only ``brnrd init``'s ``_setup_brr_dir`` used to create
it) doesn't trade the old polite exit for a raw ``FileNotFoundError`` on
the pidfile write.

Loop-wiring convention shared with ``test_daemon_burst.py`` /
``test_daemon_single_flight.py``: escape the blocking main loop by making
``protocol.list_pending`` raise ``StopIteration`` once boot has had a
chance to run.
"""

from __future__ import annotations

import pytest

from brr import daemon

from _helpers import init_git_repo


def _baseline_patches(monkeypatch, cfg=None):
    monkeypatch.setattr(daemon, "read_pid", lambda _b: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _b: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _b: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_a: [])
    monkeypatch.setattr(daemon.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _r: cfg or {})


def _stop_after_boot(monkeypatch):
    def _stop(*_a, **_k):
        raise StopIteration

    monkeypatch.setattr(daemon.protocol, "list_pending", _stop)


def test_daemon_boots_without_agents_md(tmp_path, monkeypatch, capsys):
    """No crash, no exit — the loop is reached and prints the fact instead."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    assert not (repo / "AGENTS.md").exists()

    _baseline_patches(monkeypatch)
    _stop_after_boot(monkeypatch)

    with pytest.raises(StopIteration):
        daemon.start(repo)

    out = capsys.readouterr().out
    assert "no AGENTS.md yet" in out
    assert "Booting anyway" in out


def test_daemon_boot_creates_missing_brr_dir(tmp_path, monkeypatch):
    """A repo with no ``.brr/`` at all (never `init`, never `connect`) must
    not crash on the pidfile write — this is the "even an empty project
    folder" half of the contract, and it's a real bug this fork's own
    testing found: it used to be masked by the AGENTS.md exit firing
    first, since nothing ever reached ``_write_pid`` without ``.brr/``
    already existing on every path that got this far in practice."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    assert not (repo / ".brr").exists()

    # Real _write_pid this time — the thing under test is that its parent
    # directory exists by the time it runs.
    monkeypatch.setattr(daemon, "read_pid", lambda _b: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _b: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_a: [])
    monkeypatch.setattr(daemon.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _r: {})
    _stop_after_boot(monkeypatch)

    with pytest.raises(StopIteration):
        daemon.start(repo)

    assert (repo / ".brr").is_dir()
    assert (repo / ".brr" / "daemon.pid").exists()


def test_daemon_boot_stays_quiet_once_initialized(tmp_path, monkeypatch, capsys):
    """The differential contract at the daemon-log layer too: nothing to
    say once AGENTS.md exists."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")

    _baseline_patches(monkeypatch)
    _stop_after_boot(monkeypatch)

    with pytest.raises(StopIteration):
        daemon.start(repo)

    out = capsys.readouterr().out
    assert "no AGENTS.md yet" not in out
