"""Tests for the ``stop:`` dispatch verb and the keyed runner-proc registry.

Wyrd §3 (``kb/design-wyrd.md``), the stop-verb slice: a run may stop its own
concurrent dispatchees — enforced by the daemon (an ownership check plus a
process kill), never by prompt discipline. The runner's per-invocation
process registry replaces the old single module-global handle, which could
kill a *sibling* run's subprocess once concurrent spawns existed.
"""

from __future__ import annotations

import subprocess
import threading
import time
import types

import pytest

from brr import daemon, protocol, run_progress, runner, updates


@pytest.fixture(autouse=True)
def _clean_registries():
    with daemon._spawn_controls_lock:
        daemon._spawn_controls.clear()
    with runner._proc_lock:
        runner._active_procs.clear()
    yield
    with daemon._spawn_controls_lock:
        daemon._spawn_controls.clear()
    with runner._proc_lock:
        stale = list(runner._active_procs.values())
        runner._active_procs.clear()
    for proc in stale:
        if proc.poll() is None:
            proc.kill()


# ── runner: per-invocation process registry ─────────────────────────


class TestProcRegistry:
    def test_kill_matching_kills_only_the_matching_prefix(self):
        a = subprocess.Popen(["sleep", "30"])
        b = subprocess.Popen(["sleep", "30"])
        try:
            runner._register_active_proc("evt-A-attempt-1", a)
            runner._register_active_proc("evt-B-attempt-1", b)

            assert runner.kill_matching("evt-A-") is True
            a.wait(timeout=5)
            assert a.poll() is not None
            assert b.poll() is None

            # Shutdown semantics: kill everything still live.
            assert runner.kill_active() is True
            b.wait(timeout=5)
        finally:
            for proc in (a, b):
                if proc.poll() is None:
                    proc.kill()

    def test_kill_matching_empty_prefix_is_a_noop(self):
        assert runner.kill_matching("") is False

    def test_kill_matching_unknown_prefix_is_a_noop(self):
        assert runner.kill_matching("evt-nothing-") is False

    def test_kill_active_with_no_procs_is_a_noop(self):
        assert runner.kill_active() is False


# ── daemon: the stop: drain verb ────────────────────────────────────


def _drain_stop(tmp_path, monkeypatch, files, *, task_id="run-parent"):
    brr_dir = tmp_path / ".brr"
    responses = brr_dir / "responses"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox = brr_dir / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    for name, body in files:
        (outbox / name).write_text(body)
    emitted = []
    monkeypatch.setattr(daemon.updates, "emit",
                        lambda brr, pkt: emitted.append(pkt))
    emit = daemon._WorkerEmit(
        brr_dir=brr_dir, conversation_key="", event_id="evt-1")
    task = types.SimpleNamespace(id=task_id, conversation_key="cloud:1:")
    stats: dict[str, int] = {}
    n = daemon._drain_outbox(
        emit, task, responses, "evt-1", outbox, inbox, stats=stats)
    return n, inbox, outbox, emitted, stats


class TestStopVerb:
    def test_stop_running_child_kills_its_process(self, tmp_path, monkeypatch):
        daemon._register_spawn_control("evt-child", "run-parent")
        daemon._bind_spawn_control_run("evt-child", "run-child")
        killed = []
        monkeypatch.setattr(
            daemon.runner, "kill_matching",
            lambda prefix: killed.append(prefix) or True)

        n, inbox, outbox, emitted, stats = _drain_stop(
            tmp_path, monkeypatch,
            [("stop.md", "---\nstop: evt-child\nreason: wrong contract\n---\n")],
        )

        assert n == 1
        assert stats.get("stop") == 1
        assert killed == ["evt-child-attempt-"]
        control = daemon._find_spawn_control("evt-child")
        assert control["stopped"] is True
        assert control["stopped_by"] == "run-parent"
        assert control["stop_reason"] == "wrong contract"
        types_seen = [p.type for p in emitted]
        assert "spawn_stop_requested" in types_seen

    def test_stop_addressed_by_child_run_id(self, tmp_path, monkeypatch):
        daemon._register_spawn_control("evt-child", "run-parent")
        daemon._bind_spawn_control_run("evt-child", "run-child")
        killed = []
        monkeypatch.setattr(
            daemon.runner, "kill_matching",
            lambda prefix: killed.append(prefix) or True)

        n, *_ = _drain_stop(
            tmp_path, monkeypatch,
            [("stop.md", "---\nstop: run-child\n---\n")],
        )

        assert n == 1
        assert killed == ["evt-child-attempt-"]
        assert daemon._stopped_spawn_control("evt-child") is not None

    def test_stop_refused_for_foreign_child(self, tmp_path, monkeypatch):
        daemon._register_spawn_control("evt-child", "run-somebody-else")

        n, inbox, outbox, emitted, stats = _drain_stop(
            tmp_path, monkeypatch,
            [("stop.md", "---\nstop: evt-child\n---\n")],
        )

        assert n == 0
        assert stats.get("stop") is None
        assert daemon._stopped_spawn_control("evt-child") is None
        notices = daemon._read_outbox_notices(outbox)
        assert any("not dispatched by this run" in n["text"] for n in notices)

    def test_stop_refused_for_unknown_target(self, tmp_path, monkeypatch):
        n, inbox, outbox, emitted, stats = _drain_stop(
            tmp_path, monkeypatch,
            [("stop.md", "---\nstop: evt-ghost\n---\n")],
        )

        assert n == 0
        notices = daemon._read_outbox_notices(outbox)
        assert any("matches no live concurrent spawn" in n["text"] for n in notices)

    def test_stop_cancels_a_not_yet_dispatched_child(self, tmp_path, monkeypatch):
        brr_dir = tmp_path / ".brr"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        path = protocol.create_event(
            inbox, "spawn", "do the thing",
            spawn_immediate=True, spawn_parent_run_id="run-parent",
        )
        spawn_eid = path.stem
        daemon._register_spawn_control(spawn_eid, "run-parent")
        killed = []
        monkeypatch.setattr(
            daemon.runner, "kill_matching",
            lambda prefix: killed.append(prefix) or True)

        n, inbox, outbox, emitted, stats = _drain_stop(
            tmp_path, monkeypatch,
            [("stop.md", f"---\nstop: {spawn_eid}\n---\n")],
        )

        assert n == 1
        assert killed == []  # nothing to kill: it never started
        # The queued event is cancelled, never dispatchable again.
        assert all(
            ev["id"] != spawn_eid for ev in protocol.list_pending(inbox))
        # The completion note is posted here (no future will ever reap it).
        completed = [
            ev for ev in protocol.list_pending(inbox)
            if ev.get("source") == "spawn_completed"
        ]
        assert len(completed) == 1
        assert "stopped before" in completed[0]["body"]
        assert completed[0].get("spawn_stopped") is True

    def test_stop_without_target_is_dropped(self, tmp_path, monkeypatch):
        # Unreachable via the drain (the branch requires a non-empty value);
        # the direct-call guard still refuses cleanly.
        outbox = tmp_path / "outbox"
        outbox.mkdir()
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=tmp_path / ".brr", conversation_key="", event_id="evt-1")
        task = types.SimpleNamespace(id="run-parent", conversation_key="")
        handled = daemon._queue_stop_request(
            emit, task, None, "evt-1", {"stop": "  "}, "", outbox)
        assert handled is False
        notices = daemon._read_outbox_notices(outbox)
        assert any("no target" in n["text"] for n in notices)


# ── daemon: heartbeat abort backstop ────────────────────────────────


class TestShouldAbort:
    def test_abort_kills_own_invocation_label(self, monkeypatch):
        release = threading.Event()

        class Backend:
            def invoke(self, ctx, name, invocation, cfg, trace):
                release.wait(timeout=10)
                return runner.RunnerResult(
                    invocation=invocation, runner_name=name,
                    command=[], stdout="", stderr="", returncode=-9,
                    trace_dir=None, artifacts=[],
                )

        killed: list[str] = []

        def fake_kill(prefix):
            killed.append(prefix)
            release.set()
            return True

        monkeypatch.setattr(daemon.runner, "kill_matching", fake_kill)
        invocation = runner.RunnerInvocation(
            kind="daemon-run", label="evt-X-attempt-1", prompt="p",
            repo_root=None,
        )
        result = daemon._invoke_with_heartbeat(
            Backend(), None, "fake", invocation,
            cfg={}, trace=False,
            on_heartbeat=lambda: None,
            interval=0.05, flush_interval=0.02,
            should_abort=lambda: True,
        )
        assert killed == ["evt-X-attempt-1"]
        assert result.returncode == -9

    def test_no_abort_callback_keeps_original_shape(self, monkeypatch):
        class Backend:
            def invoke(self, ctx, name, invocation, cfg, trace):
                return runner.RunnerResult(
                    invocation=invocation, runner_name=name,
                    command=[], stdout="ok", stderr="", returncode=0,
                    trace_dir=None, artifacts=[],
                )

        invocation = runner.RunnerInvocation(
            kind="daemon-run", label="evt-Y-attempt-1", prompt="p",
            repo_root=None,
        )
        result = daemon._invoke_with_heartbeat(
            Backend(), None, "fake", invocation,
            cfg={}, trace=False, on_heartbeat=lambda: None, interval=0.05,
        )
        assert result.returncode == 0


# ── run_progress: the stopped terminal fold ─────────────────────────


def test_run_progress_folds_stopped_as_terminal(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "cloud:1:"
    for ptype, payload in (
        ("run_created", {"run_id": "run-child"}),
        ("attempt_started", {"run_id": "run-child", "attempt": 1}),
        ("stopped", {"run_id": "run-child", "stopped_by": "run-parent"}),
    ):
        updates.emit(brr_dir, updates.UpdatePacket(
            type=ptype, conversation_key=key, event_id="evt-child",
            payload=payload,
        ))

    view = run_progress.project_run(brr_dir, key, "run-child")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "stopped"
    assert view.failure_kind == "stopped"
    assert view.detail == "stopped by run-parent"


# ── daemon: the to: message verb + worker view isolation ────────────


class TestMessageVerb:
    def test_message_lands_as_child_only_event(self, tmp_path, monkeypatch):
        daemon._register_spawn_control("evt-child", "run-parent")
        daemon._bind_spawn_control_run("evt-child", "run-child")

        n, inbox, outbox, emitted, stats = _drain_stop(
            tmp_path, monkeypatch,
            [("msg.md", "---\nto: run-child\n---\nfocus on the failing test\n")],
        )

        assert n == 1
        assert stats.get("spawn_message") == 1
        msgs = [
            ev for ev in protocol.list_pending(inbox)
            if ev.get("source") == "dispatch_message"
        ]
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg["spawn_message_for_event"] == "evt-child"
        assert msg["spawn_message_from_run"] == "run-parent"
        assert msg["body"] == "focus on the failing test"
        assert "spawn_message" in [p.type for p in emitted]

        # Visibility: only the addressed child's view carries it.
        child_view = daemon._pending_events_for_agent(
            inbox, "evt-child", worker=True)
        assert [e["id"] for e in child_view] == [msg["id"]]
        resident_view = daemon._pending_events_for_agent(inbox, "evt-lead")
        assert msg["id"] not in [e["id"] for e in resident_view]

    def test_message_refused_for_foreign_child(self, tmp_path, monkeypatch):
        daemon._register_spawn_control("evt-child", "run-somebody-else")

        n, inbox, outbox, emitted, stats = _drain_stop(
            tmp_path, monkeypatch,
            [("msg.md", "---\nto: evt-child\n---\nsteer\n")],
        )

        assert n == 0
        notices = daemon._read_outbox_notices(outbox)
        assert any("not dispatched by this run" in x["text"] for x in notices)

    def test_message_refused_for_stopped_child(self, tmp_path, monkeypatch):
        daemon._register_spawn_control("evt-child", "run-parent")
        with daemon._spawn_controls_lock:
            daemon._spawn_controls["evt-child"]["stopped"] = True

        n, inbox, outbox, emitted, stats = _drain_stop(
            tmp_path, monkeypatch,
            [("msg.md", "---\nto: evt-child\n---\nsteer\n")],
        )

        assert n == 0
        notices = daemon._read_outbox_notices(outbox)
        assert any("being stopped" in x["text"] for x in notices)

    def test_message_with_empty_body_is_dropped(self, tmp_path, monkeypatch):
        daemon._register_spawn_control("evt-child", "run-parent")

        n, inbox, outbox, emitted, stats = _drain_stop(
            tmp_path, monkeypatch,
            [("msg.md", "---\nto: evt-child\n---\n\n")],
        )

        assert n == 0
        notices = daemon._read_outbox_notices(outbox)
        assert any("empty body" in x["text"] for x in notices)


class TestWorkerViewIsolation:
    def test_worker_view_hides_user_thread_events(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        protocol.create_event(inbox, "telegram", "user says hi")
        msg = protocol.create_event(
            inbox, "dispatch_message", "steer",
            spawn_message_for_event="evt-child",
            spawn_message_from_run="run-parent",
        )

        worker_view = daemon._pending_events_for_agent(
            inbox, "evt-child", worker=True)
        assert [e["id"] for e in worker_view] == [msg.stem]

        other_worker = daemon._pending_events_for_agent(
            inbox, "evt-other-child", worker=True)
        assert other_worker == []

    def test_resident_view_hides_edge_messages_keeps_user_events(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        user_ev = protocol.create_event(inbox, "telegram", "user says hi")
        protocol.create_event(
            inbox, "dispatch_message", "steer",
            spawn_message_for_event="evt-child",
            spawn_message_from_run="run-parent",
        )

        resident_view = daemon._pending_events_for_agent(inbox, "evt-lead")
        assert [e["id"] for e in resident_view] == [user_ev.stem]

    def test_retire_child_messages(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        protocol.create_event(
            inbox, "dispatch_message", "steer",
            spawn_message_for_event="evt-child",
        )
        other = protocol.create_event(
            inbox, "dispatch_message", "steer",
            spawn_message_for_event="evt-other",
        )

        daemon._retire_child_messages(inbox, "evt-child")

        remaining = [
            ev["id"] for ev in protocol.list_pending(inbox)
            if ev.get("source") == "dispatch_message"
        ]
        assert remaining == [other.stem]
