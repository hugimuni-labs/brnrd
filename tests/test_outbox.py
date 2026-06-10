"""Tests for the agent outbox + daemon mid-flight drain (slice 4b).

The producer half of the multi-response protocol: the resident drops
interim replies in ``.brr/outbox/<eid>/``, the daemon drains them to the
response partials queue, and the live card / conversation log reflect
the check-in. See ``kb/design-multi-response.md``.
"""

from __future__ import annotations

import json
import types

from brr import conversations, daemon, protocol, run_context, run_progress, updates
from brr.envs import RunContext
from brr.task import Task


def _emit(brr_dir, key, ptype, **payload):
    updates.emit(brr_dir, updates.UpdatePacket(
        type=ptype, conversation_key=key, payload=payload))


class TestDrainOutbox:
    def _drain(self, tmp_path, monkeypatch, files):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        outbox = brr_dir / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        for name, body in files:
            (outbox / name).write_text(body)
        emitted = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")
        n = daemon._drain_outbox(emit, task, responses, "evt-1", outbox)
        return n, responses, outbox, emitted

    def test_promotes_in_order_and_removes(self, tmp_path, monkeypatch):
        n, responses, outbox, emitted = self._drain(
            tmp_path, monkeypatch,
            [("001.md", "first\n"), ("002.md", "second\n")],
        )
        assert n == 2
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["first", "second"]
        assert not (outbox / "001.md").exists()
        assert not (outbox / "002.md").exists()
        assert [p.type for p in emitted] == ["interim_response", "interim_response"]

    def test_skips_tmp_and_empty(self, tmp_path, monkeypatch):
        n, responses, outbox, _ = self._drain(
            tmp_path, monkeypatch,
            [("staging.tmp", "half written"),
             ("blank.md", "   \n"),
             ("real.md", "hi\n")],
        )
        assert n == 1
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["hi"]
        # A .tmp staging file is left for the agent to finish/rename.
        assert (outbox / "staging.tmp").exists()
        # A blank file is consumed (removed) but never promoted.
        assert not (outbox / "blank.md").exists()

    def test_skips_control_dotfiles(self, tmp_path, monkeypatch):
        n, responses, outbox, _ = self._drain(
            tmp_path, monkeypatch,
            [(".keepalive", "+30m\n"), ("real.md", "hi\n")],
        )
        assert n == 1
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["hi"]
        # The keepalive control file is left in place — the heartbeat reads
        # it; it is never delivered as a message or consumed by the drain.
        assert (outbox / ".keepalive").exists()

    def test_skips_live_inbox_control_file(self, tmp_path, monkeypatch):
        n, responses, outbox, _ = self._drain(
            tmp_path, monkeypatch,
            [("inbox.json", '{"events": []}\n'), ("real.md", "hi\n")],
        )
        assert n == 1
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["hi"]
        assert (outbox / "inbox.json").exists()

    def test_gate_addressed_message_synthesizes_done_event(self, tmp_path, monkeypatch):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        # A `status:` in the frontmatter must not resurrect a pending event.
        (outbox / "ping.md").write_text(
            "---\ngate: telegram\nstatus: pending\ntelegram_chat_id: 999\n---\n"
            "daily summary\n")
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        # A done event for the telegram gate now waits for delivery.
        done = protocol.list_done(inbox, "telegram")
        assert len(done) == 1
        ev = done[0]
        assert ev["status"] == "done"
        assert str(ev.get("telegram_chat_id")) == "999"
        # Its response carries the message body; the gate delivers that.
        assert protocol.read_response(responses, ev["id"]).strip() == "daily summary"
        # Born done: invisible to the inbox poll, so it never spawns a thought.
        assert protocol.list_pending(inbox) == []
        assert not (outbox / "ping.md").exists()

    def test_forge_gate_alias_queues_github_pull_request_event(
        self, tmp_path, monkeypatch,
    ):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "pr.md").write_text(
            "---\ngate: forge\nhead: brr/feat-x\nbase: main\n"
            "title: Review feat-x\n---\n"
            "projected body\n")
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        done = protocol.list_done(inbox, "github")
        assert len(done) == 1
        ev = done[0]
        assert ev["source"] == "github"
        assert ev["github_action"] == "pull_request"
        assert ev["head"] == "brr/feat-x"
        assert protocol.read_response(responses, ev["id"]).strip() == "projected body"
        assert protocol.list_done(inbox, "forge") == []

    def test_gate_addressed_unknown_gate_dropped(self, tmp_path, monkeypatch):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "ping.md").write_text("---\ngate: nosuchgate\n---\nhi\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        # Unconfigured/unknown gate: dropped, not queued (it'd never deliver).
        assert n == 0
        assert protocol.list_done(inbox, "nosuchgate") == []
        assert protocol.list_pending(inbox) == []
        assert not (outbox / "ping.md").exists()

    def test_missing_outbox_is_noop(self, tmp_path):
        brr_dir = tmp_path / ".brr"
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")
        responses = brr_dir / "responses"
        assert daemon._drain_outbox(emit, task, responses, "evt-1", None) == 0
        assert daemon._drain_outbox(
            emit, task, responses, "evt-1", brr_dir / "outbox" / "nope") == 0

    def test_cross_event_routes_to_target_and_marks_done(self, tmp_path, monkeypatch):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        # A second event B is waiting in the inbox.
        protocol.create_event(inbox, source="telegram", body="quick q")
        evB = protocol.list_pending(inbox)[0]
        bid = evB["id"]
        # The resident folds B in and drops a reply targeting it.
        (outbox / "reply.md").write_text(
            f"---\nevent: {bid}\n---\nhere's the answer\n")
        emitted = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        # Body went to B's queue, not the current event's.
        assert [protocol.read_partial(p)
                for p in protocol.list_partials(responses, bid)] == ["here's the answer"]
        assert protocol.list_partials(responses, "evt-A") == []
        # B is marked done so the gate delivers + cleans it up; it won't
        # wake as its own thought.
        assert [e["id"] for e in protocol.list_done(inbox, "telegram")] == [bid]
        assert protocol.list_pending(inbox) == []
        assert emitted[0].payload.get("target_event") == bid

    def test_cross_event_unknown_target_is_dropped(self, tmp_path, monkeypatch):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "reply.md").write_text("---\nevent: evt-ghost\n---\nhi\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        # No deliverable target: dropped rather than misrouted.
        assert n == 0
        assert not (outbox / "reply.md").exists()
        assert protocol.list_partials(responses, "evt-ghost") == []


def test_remove_outbox_is_best_effort(tmp_path):
    outbox = tmp_path / ".brr" / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    (outbox / "leftover.tmp").write_text("x")
    daemon._remove_outbox(outbox)
    assert not outbox.exists()
    # tolerates a missing dir / None
    daemon._remove_outbox(outbox)
    daemon._remove_outbox(None)


def test_live_inbox_file_lists_other_pending_events(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-A"
    outbox.mkdir(parents=True)
    current_path = protocol.create_event(inbox, source="github", body="current")
    current = protocol.list_pending(inbox)[0]
    protocol.set_status(current, "processing")
    protocol.create_event(
        inbox,
        source="telegram",
        body="quick question\nwith detail",
        telegram_chat_id="123",
    )
    protocol.create_event(inbox, source="slack", body="already running")
    other_processing = [
        ev for ev in protocol.list_pending(inbox)
        if ev["_path"] != current_path and ev["source"] == "slack"
    ][0]
    protocol.set_status(other_processing, "processing")

    path = daemon._write_live_inbox(outbox, inbox, current["id"])

    assert path == outbox / "inbox.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["current_event"] == current["id"]
    assert len(payload["events"]) == 1
    ev = payload["events"][0]
    assert ev["source"] == "telegram"
    assert ev["summary"] == "quick question with detail"
    assert ev["body"] == "quick question\nwith detail"
    assert ev["telegram_chat_id"] == 123
    assert "_path" not in ev


def test_interim_response_packet_updates_card(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:1:"
    conversations.append_task(
        brr_dir, key, task_id="task-1", event_id="evt-1",
        env="worktree", status="running", branch_name="brr/task-1",
    )
    _emit(brr_dir, key, "attempt_started", task_id="task-1", attempt=1)
    _emit(brr_dir, key, "run_started", task_id="task-1", branch="brr/task-1")
    _emit(brr_dir, key, "interim_response", task_id="task-1", event_id="evt-1",
          path="/x/.brr/responses/evt-1.partials/000001.md")
    _emit(brr_dir, key, "interim_response", task_id="task-1", event_id="evt-1",
          path="/x/.brr/responses/evt-1.partials/000002.md")

    view = run_progress.project_task(brr_dir, key, "task-1")
    assert view is not None
    assert view.interim_count == 2
    assert "interim" in view.detail.lower()
    # An interim reply is mid-run progress, not a terminal state.
    assert view.state == "active"


def test_cross_event_interim_card_names_the_folded_in_event(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:1:"
    conversations.append_task(
        brr_dir, key, task_id="task-A", event_id="evt-A",
        env="worktree", status="running",
    )
    _emit(brr_dir, key, "run_started", task_id="task-A")
    _emit(brr_dir, key, "interim_response", task_id="task-A", event_id="evt-A",
          target_event="evt-B", path="/x/.brr/responses/evt-B.partials/000001.md")

    view = run_progress.project_task(brr_dir, key, "task-A")
    assert view is not None
    assert "folded-in" in view.detail
    assert "evt-B" in view.detail


def test_run_context_includes_outbox_paths(tmp_path):
    task = Task(id="task-1", event_id="evt-1", body="do it", source="telegram")
    ctx = RunContext(
        name="worktree",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr/responses/evt-1.md",
        response_path_env=tmp_path / ".brr/responses/evt-1.md",
        outbox_host=tmp_path / ".brr/outbox/evt-1",
        outbox_env=tmp_path / ".brr/outbox/evt-1",
    )
    text = run_context.render_context(
        task, {"_path": "x", "source": "telegram"}, ctx)
    assert "outbox/evt-1" in text
    assert "mid-thought" in text


def test_run_context_omits_outbox_when_absent(tmp_path):
    task = Task(id="task-1", event_id="evt-1", body="do it")
    ctx = RunContext(
        name="host", cwd=tmp_path, repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr/responses/evt-1.md",
        response_path_env=tmp_path / ".brr/responses/evt-1.md",
    )
    text = run_context.render_context(task, {}, ctx)
    assert "Interim-response outbox" not in text
