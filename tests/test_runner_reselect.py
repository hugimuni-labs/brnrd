"""The runner pin is re-read immediately before a run is spawned.

Maintainer ask, 2026-07-23: *"to avoid the recent sync issues on the
runner selector … could we double check the set model on daemon just
before spawning a run?"*

``_run_worker`` resolves the runner profile early — before the trust and
env setup, the worktree build, and the prompt assembly — and only spawns
the process several hundred lines later. That gap is a real window: an
operator who changes the pin from the dashboard or ``.brr/config`` inside
it watched the run start on the *old* profile, with nothing on any
surface saying why.

The re-check passes the **same overrides** to ``resolve_runner_profile``
rather than reading config raw, so a deliberate override (a dashboard
wake request, ``quality: escalate``) keeps winning by construction
instead of by a special case.
"""

from __future__ import annotations

import pytest

from brr import daemon, envs

from _helpers import make_event, write_repo_scaffold
from test_config_trust import _stub_worktree_env


def _profiles_in_sequence(monkeypatch, names):
    """Make ``resolve_runner_profile`` return *names* on successive calls."""
    calls = {"n": 0}

    def _resolve(root, _overrides=None):
        idx = min(calls["n"], len(names) - 1)
        calls["n"] += 1
        return daemon.runner.runner_profile(names[idx], root)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", _resolve)
    return calls


def _stub_rest(monkeypatch):
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )


def test_runner_changed_between_resolution_and_spawn_is_adopted_and_surfaced(
    tmp_path, monkeypatch, capsys,
):
    """The pin moved mid-dispatch: the run starts on the new one, and says so."""
    write_repo_scaffold(tmp_path)
    (tmp_path / ".brr" / "config").write_text("shell=codex\n", encoding="utf-8")
    event = make_event(tmp_path, eid="evt-reselect")
    _stub_worktree_env(monkeypatch, tmp_path)
    _stub_rest(monkeypatch)
    calls = _profiles_in_sequence(monkeypatch, ["codex", "claude"])

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert calls["n"] >= 2, "the pin was never re-read before the spawn"

    notices = daemon._read_outbox_notices(
        tmp_path / ".brr" / "outbox" / "evt-reselect"
    )
    assert any(
        "runner selection changed" in n["text"] and "claude" in n["text"]
        for n in notices
    ), notices

    out = capsys.readouterr().out
    assert "selected runner changed" in out
    assert "codex -> claude" in out


def test_unchanged_runner_selection_is_silent(tmp_path, monkeypatch, capsys):
    """The 99% case costs nothing and says nothing — no notice, no log line."""
    write_repo_scaffold(tmp_path)
    (tmp_path / ".brr" / "config").write_text("shell=codex\n", encoding="utf-8")
    event = make_event(tmp_path, eid="evt-stable")
    _stub_worktree_env(monkeypatch, tmp_path)
    _stub_rest(monkeypatch)
    _profiles_in_sequence(monkeypatch, ["codex"])

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    notices = daemon._read_outbox_notices(
        tmp_path / ".brr" / "outbox" / "evt-stable"
    )
    assert not any("runner selection changed" in n["text"] for n in notices)
    assert "selected runner changed" not in capsys.readouterr().out
