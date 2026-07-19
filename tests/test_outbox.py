"""Tests for the agent outbox + daemon mid-flight drain (slice 4b).

The producer half of the multi-response protocol: the resident drops
interim replies in ``.brr/outbox/<eid>/``, the daemon drains them to the
response partials queue, and the live card / conversation log reflect
the check-in. See ``kb/design-multi-response.md``.
"""

from __future__ import annotations

import json
import types

from brr import conversations, daemon, hooks, protocol, run_context, run_progress, updates
from brr.envs import RunContext
from brr.run import Run


def _emit(brr_dir, key, ptype, **payload):
    updates.emit(brr_dir, updates.UpdatePacket(
        type=ptype, conversation_key=key, payload=payload))


def test_hooks_installed_packet_is_persisted(tmp_path):
    brr_dir = tmp_path / ".brr"

    updates.emit(brr_dir, updates.UpdatePacket(
        type="hooks_installed",
        conversation_key="telegram:1:",
        event_id="evt-1",
        payload={"run_id": "run-a", "flavour": "codex"},
    ))

    record = conversations.read_records(brr_dir, "telegram:1:")[-1]
    assert record["kind"] == "update"
    assert record["type"] == "hooks_installed"
    assert record["run_id"] == "run-a"
    assert record["flavour"] == "codex"


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

    def test_skips_daemon_live_control_files(self, tmp_path, monkeypatch):
        n, responses, outbox, _ = self._drain(
            tmp_path, monkeypatch,
            [
                ("inbox.json", '{"events": []}\n'),
                ("portal-state.json", '{"attention": {}}\n'),
                ("real.md", "hi\n"),
            ],
        )
        assert n == 1
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["hi"]
        assert (outbox / "inbox.json").exists()
        assert (outbox / "portal-state.json").exists()

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
        receipt = (outbox / hooks.FORGE_HANDOFF_NAME).read_text(encoding="utf-8")
        assert ev["id"] in receipt
        assert "brr/feat-x" in receipt

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

    def test_cross_event_records_dialogue_on_target_conversation(
        self, tmp_path, monkeypatch,
    ):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        protocol.create_event(
            inbox,
            source="telegram",
            body="quick q",
            telegram_chat_id=222,
        )
        evB = protocol.list_pending(inbox)[0]
        bid = evB["id"]
        (outbox / "reply.md").write_text(
            f"---\nevent: {bid}\n---\nthread-specific answer\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="telegram:111:", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")

        daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        target_records = conversations.read_records(brr_dir, "telegram:222:")
        assert [r.get("kind") for r in target_records] == ["event", "artifact"]
        assert target_records[0]["event_id"] == bid
        assert target_records[1]["event_id"] == bid
        assert target_records[1]["body"] == "thread-specific answer"
        current_records = conversations.read_records(brr_dir, "telegram:111:")
        assert current_records == []

    def test_cross_event_routes_without_opening_fence(self, tmp_path, monkeypatch):
        # The live failure: the resident wrote `event: <id>` then `---`
        # with no opening fence. The strict parser left the selector in the
        # body and delivered to the lead event (wrong quote). The tolerant
        # parse must route it to the target and strip the selector.
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        protocol.create_event(inbox, source="telegram", body="quick q")
        bid = protocol.list_pending(inbox)[0]["id"]
        (outbox / "reply.md").write_text(
            f"event: {bid}\n---\nhere's the answer\n")
        emitted = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        # Routed to B's queue with the selector stripped — not leaked.
        assert [protocol.read_partial(p)
                for p in protocol.list_partials(responses, bid)] == ["here's the answer"]
        assert protocol.list_partials(responses, "evt-A") == []
        assert emitted[0].payload.get("target_event") == bid

    def test_gate_addressed_without_opening_fence(self, tmp_path, monkeypatch):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "ping.md").write_text(
            "gate: telegram\ntelegram_chat_id: 999\n---\ndaily summary\n")
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        done = protocol.list_done(inbox, "telegram")
        assert len(done) == 1
        assert str(done[0].get("telegram_chat_id")) == "999"
        assert protocol.read_response(responses, done[0]["id"]).strip() == "daily summary"

    def test_plain_message_with_dividers_delivered_verbatim(self, tmp_path, monkeypatch):
        # A PLAN-style interim with --- dividers must reach the current
        # event's queue intact, not be parsed as misrouting frontmatter.
        n, responses, outbox, _ = self._drain(
            tmp_path, monkeypatch,
            [("plan.md", "Here is the PLAN.\n\n---\n\n1. step one\n")],
        )
        assert n == 1
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["Here is the PLAN.\n\n---\n\n1. step one"]

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


class TestDrainAgentCard:
    """The agent-owned card composition seam (issue #114).

    The resident writes ``outbox/<eid>/.card`` with its preferred card
    narration; the daemon reads it on each heartbeat tick (and once
    more after the runner returns) and emits a ``card_composed`` packet
    when the content changes. The file is a control dotfile — the
    regular outbox drain leaves it alone (see TestDrainOutbox above).
    """

    def _drain(self, tmp_path, monkeypatch, body, state=None):
        brr_dir = tmp_path / ".brr"
        outbox = brr_dir / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        card = outbox / ".card"
        if body is not None:
            card.write_text(body, encoding="utf-8")
        emitted = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="k", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")
        st = state if state is not None else {}
        result = daemon._drain_agent_card(emit, task, "evt-1", card, st)
        return result, emitted, card, st

    def test_first_read_emits_card_composed(self, tmp_path, monkeypatch):
        ok, emitted, card, state = self._drain(
            tmp_path, monkeypatch, "scanning packet types\n",
        )
        assert ok is True
        assert len(emitted) == 1
        assert emitted[0].type == "card_composed"
        assert emitted[0].payload["text"] == "scanning packet types"
        assert emitted[0].payload["event_id"] == "evt-1"
        assert state["last"] == "scanning packet types"
        # The file stays in place — the resident owns the canonical copy.
        assert card.exists()

    def test_unchanged_content_is_noop(self, tmp_path, monkeypatch):
        ok1, emitted1, card, state = self._drain(
            tmp_path, monkeypatch, "narration\n",
        )
        assert ok1 is True
        # Second pass with the same content must not re-emit a packet.
        emitted2 = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted2.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=tmp_path / ".brr", conversation_key="k", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")
        ok2 = daemon._drain_agent_card(emit, task, "evt-1", card, state)
        assert ok2 is False
        assert emitted2 == []

    def test_rewritten_content_emits_again(self, tmp_path, monkeypatch):
        ok1, _, card, state = self._drain(
            tmp_path, monkeypatch, "first pass\n",
        )
        assert ok1 is True
        # The resident rewrites the card — a new packet must fire.
        card.write_text("second pass\n", encoding="utf-8")
        emitted = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=tmp_path / ".brr", conversation_key="k", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")
        ok2 = daemon._drain_agent_card(emit, task, "evt-1", card, state)
        assert ok2 is True
        assert len(emitted) == 1
        assert emitted[0].payload["text"] == "second pass"

    def test_deleted_card_emits_empty_withdrawal(self, tmp_path, monkeypatch):
        ok1, _, card, state = self._drain(
            tmp_path, monkeypatch, "narration\n",
        )
        assert ok1 is True
        # Resident deletes the file to retract its narration.
        card.unlink()
        emitted = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=tmp_path / ".brr", conversation_key="k", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")
        ok2 = daemon._drain_agent_card(emit, task, "evt-1", card, state)
        assert ok2 is True
        assert len(emitted) == 1
        assert emitted[0].payload["text"] == ""

    def test_missing_card_with_no_prior_state_is_noop(self, tmp_path, monkeypatch):
        ok, emitted, _, state = self._drain(tmp_path, monkeypatch, None)
        assert ok is False
        assert emitted == []
        assert "last" not in state

    def test_oversized_card_is_truncated(self, tmp_path, monkeypatch):
        big = "x" * (daemon._CARD_CONTROL_MAX_BYTES + 500)
        ok, emitted, _, _ = self._drain(tmp_path, monkeypatch, big)
        assert ok is True
        text = emitted[0].payload["text"]
        # Daemon side caps the read at _CARD_CONTROL_MAX_BYTES; the
        # renderer caps the displayed text again. We assert the daemon
        # half here.
        assert len(text) == daemon._CARD_CONTROL_MAX_BYTES

    def test_drain_outbox_leaves_card_control_file_alone(
        self, tmp_path, monkeypatch,
    ):
        """The agent card lives at ``.card`` (a dotfile). The regular
        outbox drain — which delivers real outbox messages — must not
        consume it as a chat reply."""
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        outbox = brr_dir / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        (outbox / ".card").write_text("narration\n", encoding="utf-8")
        (outbox / "real.md").write_text("real interim\n", encoding="utf-8")
        emitted = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="k", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")
        n = daemon._drain_outbox(emit, task, responses, "evt-1", outbox)

        assert n == 1
        assert (outbox / ".card").exists()
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["real interim"]


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


def test_live_portal_state_file_summarizes_run_attention(tmp_path):
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
    (outbox / "draft.md").write_text("queued reply\n", encoding="utf-8")
    (outbox / ".card").write_text("working\n", encoding="utf-8")
    (outbox / ".keepalive").write_text("+30m\n", encoding="utf-8")
    task = Run(
        id="run-1",
        event_id=current["id"],
        body="work",
        status="running",
        env="host",
        meta={
            "branch_name": "brr/live-state",
            "repo_label": "Gurio/brr",
            "kb_base_url": "https://github.test/knowledge/blob/main/repos/Gurio__brr/",
        },
    )

    path = daemon._write_live_portal_state(
        outbox,
        inbox,
        current["id"],
        task,
        phase="running",
        attempt=1,
        runner_name="codex",
        quality_escalation={
            "status": "known",
            "name": "claude-opus",
            "class": "strong",
        },
        budget_seconds=3600,
        hard_cap_seconds=7200,
        keepalive_path=outbox / ".keepalive",
        card_state={"last": "working"},
        output_stats={"current": 1, "other": 2, "outbound": 3},
        start_monotonic=daemon.time.monotonic() - 1,
    )

    assert path == outbox / "portal-state.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["run"]["id"] == "run-1"
    assert payload["run"]["phase"] == "running"
    assert payload["run"]["attempt"] == 1
    assert payload["run"]["repo"] == "Gurio/brr"
    assert payload["run"]["branch"] == "brr/live-state"
    assert payload["knowledge"]["kb_base_url"].endswith("/repos/Gurio__brr/")
    assert payload["attention"] == {
        "needs_attention": True,
        "pending_event_count": 1,
        "pending_outbox_file_count": 1,
    }
    assert payload["inbound"]["events"][0]["summary"] == "quick question with detail"
    assert payload["outbound"]["replies_current"] == 1
    assert payload["outbound"]["replies_other"] == 2
    assert payload["outbound"]["outbound_messages"] == 3
    assert payload["outbound"]["pending_outbox_files"] == ["draft.md"]
    assert payload["card"]["active"] is True
    assert payload["card"]["text"] == "working"
    assert payload["card"]["stale"] is False
    assert isinstance(payload["card"]["age_seconds"], int)
    assert payload["resources"]["runner"]["quality_escalation"]["name"] == (
        "claude-opus"
    )
    assert payload["budget"]["keepalive"]["status"] == "active"
    assert payload["budget"]["elapsed_seconds"] >= 0
    assert payload["change_token"]
    assert "_path" not in payload["inbound"]["events"][0]

    first_token = payload["change_token"]
    daemon._write_live_portal_state(
        outbox,
        inbox,
        current["id"],
        task,
        phase="running",
        attempt=1,
        runner_name="codex",
        quality_escalation={
            "status": "known",
            "name": "claude-opus",
            "class": "strong",
        },
        budget_seconds=3600,
        hard_cap_seconds=7200,
        keepalive_path=outbox / ".keepalive",
        card_state={"last": "working"},
        output_stats={"current": 1, "other": 2, "outbound": 3},
        start_monotonic=daemon.time.monotonic() - 5,
    )
    payload2 = json.loads(path.read_text(encoding="utf-8"))
    assert payload2["change_token"] == first_token
    assert payload2["budget"]["elapsed_seconds"] >= payload["budget"]["elapsed_seconds"]


def test_live_portal_state_flags_stale_card(tmp_path):
    # 2026-07-05: a card that hasn't changed in a while is itself a signal
    # the resident should see — mirrors the pending-event framing fix from
    # the same day. ``written_monotonic`` far enough in the past (or a card
    # never written at all, falling back to ``start_monotonic``) crosses the
    # maintainer's own 240s bar.
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-A"
    outbox.mkdir(parents=True)
    current_path = protocol.create_event(inbox, source="github", body="current")
    current = protocol.list_pending(inbox)[0]
    protocol.set_status(current, "processing")
    task = Run(
        id="run-1", event_id=current["id"], body="work", status="running",
        env="host", meta={"branch_name": "brr/live-state"},
    )

    # Never written at all: age tracks the run's own elapsed time.
    path = daemon._write_live_portal_state(
        outbox, inbox, current["id"], task, phase="running",
        card_state={}, start_monotonic=daemon.time.monotonic() - 300,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["card"]["stale"] is True
    assert payload["card"]["age_seconds"] >= 240

    # Written long ago, but the run has not moved since: NOT stale. An old
    # card describing a run that hasn't changed is an accurate card, and the
    # only way to satisfy a pure timer is a cosmetic edit (2026-07-19).
    path = daemon._write_live_portal_state(
        outbox, inbox, current["id"], task, phase="running",
        card_state={
            "last": "old note",
            "written_monotonic": daemon.time.monotonic() - 300,
        },
        start_monotonic=daemon.time.monotonic() - 1,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["card"]["stale"] is False

    # Same old card, but now the run has moved (a new pending event) and the
    # movement is itself older than the threshold: stale, with the reason.
    task.meta["run_state_moved_monotonic"] = daemon.time.monotonic() - 300
    path = daemon._write_live_portal_state(
        outbox, inbox, current["id"], task, phase="running",
        card_state={
            "last": "old note",
            "written_monotonic": daemon.time.monotonic() - 400,
        },
        start_monotonic=daemon.time.monotonic() - 600,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["card"]["stale"] is True
    assert payload["card"]["state_moved_seconds"] >= 240

    # Movement the card already caught up with stays quiet, however old the
    # movement is.
    task.meta["run_state_moved_monotonic"] = daemon.time.monotonic() - 300
    path = daemon._write_live_portal_state(
        outbox, inbox, current["id"], task, phase="running",
        card_state={
            "last": "caught up",
            "written_monotonic": daemon.time.monotonic() - 10,
        },
        start_monotonic=daemon.time.monotonic() - 600,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["card"]["stale"] is False

    # Fresh write stays quiet.
    path = daemon._write_live_portal_state(
        outbox, inbox, current["id"], task, phase="running",
        card_state={
            "last": "fresh note",
            "written_monotonic": daemon.time.monotonic(),
        },
        start_monotonic=daemon.time.monotonic() - 300,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["card"]["stale"] is False


def test_interim_response_packet_updates_card(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:1:"
    conversations.append_run(
        brr_dir, key, run_id="task-1", event_id="evt-1",
        env="worktree", status="running", branch_name="brr/task-1",
    )
    _emit(brr_dir, key, "attempt_started", run_id="task-1", attempt=1)
    _emit(brr_dir, key, "run_started", run_id="task-1", branch="brr/task-1")
    _emit(brr_dir, key, "interim_response", run_id="task-1", event_id="evt-1",
          path="/x/.brr/responses/evt-1.partials/000001.md")
    _emit(brr_dir, key, "interim_response", run_id="task-1", event_id="evt-1",
          path="/x/.brr/responses/evt-1.partials/000002.md")

    view = run_progress.project_run(brr_dir, key, "task-1")
    assert view is not None
    assert view.interim_count == 2
    assert "interim" in view.detail.lower()
    # An interim reply is mid-run progress, not a terminal state.
    assert view.state == "active"


def test_cross_event_interim_card_names_the_folded_in_event(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:1:"
    conversations.append_run(
        brr_dir, key, run_id="task-A", event_id="evt-A",
        env="worktree", status="running",
    )
    _emit(brr_dir, key, "run_started", run_id="task-A")
    _emit(brr_dir, key, "interim_response", run_id="task-A", event_id="evt-A",
          target_event="evt-B", path="/x/.brr/responses/evt-B.partials/000001.md")

    view = run_progress.project_run(brr_dir, key, "task-A")
    assert view is not None
    assert "folded-in" in view.detail
    assert "evt-B" in view.detail


def test_run_context_includes_outbox_paths(tmp_path):
    task = Run(id="task-1", event_id="evt-1", body="do it", source="telegram")
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


def test_run_context_includes_communication_snapshot_and_history(tmp_path):
    task = Run(id="task-1", event_id="evt-1", body="do it", source="telegram")
    ctx = RunContext(
        name="worktree",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr/responses/evt-1.md",
        response_path_env=tmp_path / ".brr/responses/evt-1.md",
    )

    text = run_context.render_context(
        task,
        {"_path": "x", "source": "telegram"},
        ctx,
        communication_snapshot={
            "current_thread": "telegram:1:",
            "related_threads": [
                {
                    "conversation_key": "telegram:1:",
                    "source": "telegram",
                    "record_count": 2,
                    "dialogue_count": 1,
                    "latest_ts": "2026-05-05T20:00:00Z",
                },
            ],
            "history_groups": [
                {
                    "label": "telegram thread telegram:1:",
                    "path": str(tmp_path / ".brr/runs/task-1/history/gate.jsonl"),
                    "record_count": 2,
                },
            ],
            "recent_turns": [
                {
                    "ts": "2026-05-05T20:00:00Z",
                    "kind": "event",
                    "source": "telegram",
                    "body": "prior",
                },
                {
                    "ts": "2026-05-05T20:01:00Z",
                    "kind": "artifact",
                    "artifact_kind": "response",
                    "label": "response:evt-prior",
                    "body": "agent prior",
                },
            ],
        },
    )

    assert "Communication Snapshot" in text
    assert "Current thread: telegram:1:" in text
    assert "On-demand grouped history" in text
    assert "gate.jsonl" in text
    assert "prior" in text
    assert "agent prior" in text


def test_run_context_renders_prior_failure_facet(tmp_path):
    task = Run(id="task-2", event_id="evt-2", body="again", source="telegram")
    ctx = RunContext(
        name="worktree",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr/responses/evt-2.md",
        response_path_env=tmp_path / ".brr/responses/evt-2.md",
    )

    text = run_context.render_context(
        task,
        {"_path": "x", "source": "telegram"},
        ctx,
        communication_snapshot={
            "current_thread": "telegram:1:",
            "prior_failure": {
                "reason": "Credit balance is too low",
                "stage": "run",
                "attempts": 3,
                "ts": "2026-06-14T16:00:00Z",
            },
            "related_threads": [],
            "recent_turns": [],
        },
    )

    assert "Prior run on this thread failed (operational)" in text
    assert "Credit balance is too low" in text
    assert "3 attempt(s)" in text


def test_run_context_omits_outbox_when_absent(tmp_path):
    task = Run(id="task-1", event_id="evt-1", body="do it")
    ctx = RunContext(
        name="host", cwd=tmp_path, repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr/responses/evt-1.md",
        response_path_env=tmp_path / ".brr/responses/evt-1.md",
    )
    text = run_context.render_context(task, {}, ctx)
    assert "Interim-response outbox" not in text


# ── Prompt retention ─────────────────────────────────────────────────


def test_run_context_includes_prompt_file_path(tmp_path):
    """render_context lists the prompt.md path in Runtime Files.

    The file may not exist yet when context.md is written (the prompt is
    built after the context file); the path is pre-announced so the agent
    knows where to look once it exists.
    """
    task = Run(id="task-abc", event_id="evt-1", body="do it")
    ctx = RunContext(
        name="worktree",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr/responses/evt-1.md",
        response_path_env=tmp_path / ".brr/responses/evt-1.md",
    )
    text = run_context.render_context(task, {}, ctx)

    assert "prompt.md" in text
    assert "Assembled wake prompt" in text
    # Points at the correct run-dir path (not the trace dir).
    assert str(tmp_path / ".brr" / "runs" / "task-abc" / "prompt.md") in text


def test_write_prompt_file_creates_file_in_run_dir(tmp_path):
    """write_prompt_file persists the prompt alongside context.md."""
    from brr import run_context
    from brr.run import Run

    brr_dir = tmp_path / ".brr"
    task = Run(id="task-xyz", event_id="evt-1", body="fix it")
    prompt_text = "# My assembled prompt\n\nsome content"

    path = run_context.write_prompt_file(brr_dir, task, prompt_text)

    assert path is not None
    assert path == brr_dir / "runs" / "task-xyz" / "prompt.md"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == prompt_text


class TestTerminalStreamDedupe:
    """The static-dispatch dedupe (ceremony cut 2026-07-16): a terminal
    stream that exactly duplicates a reply already delivered to the waking
    thread via the outbox is dropped, never double-posted. Anything new
    still ships."""

    def _drain_current(self, tmp_path, monkeypatch, body):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        outbox = brr_dir / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        (outbox / "001.md").write_text(body)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1", meta={})
        daemon._drain_outbox(emit, task, responses, "evt-1", outbox)
        return task, responses

    def test_exact_duplicate_is_detected(self, tmp_path, monkeypatch):
        task, responses = self._drain_current(
            tmp_path, monkeypatch, "the whole reply\nsecond line\n")
        resp = responses / "evt-1.md"
        # Terminal stream = same content, differing only in surrounding
        # whitespace (the strip the outbox drain already applies).
        resp.write_text("the whole reply\nsecond line\n\n")
        assert daemon._terminal_stream_duplicates_delivered(task, resp)

    def test_new_terminal_content_still_ships(self, tmp_path, monkeypatch):
        task, responses = self._drain_current(
            tmp_path, monkeypatch, "interim: on it\n")
        resp = responses / "evt-1.md"
        resp.write_text("done — the real answer, different text\n")
        assert not daemon._terminal_stream_duplicates_delivered(task, resp)

    def test_no_delivered_partials_never_suppresses(self, tmp_path):
        task = types.SimpleNamespace(id="task-1", meta={})
        resp = tmp_path / "evt-1.md"
        resp.write_text("a reply\n")
        assert not daemon._terminal_stream_duplicates_delivered(task, resp)

    def test_cross_event_reply_does_not_arm_dedupe(self, tmp_path, monkeypatch):
        # A reply folded into a *different* event must not suppress this
        # thread's terminal stream, even with identical text.
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        outbox = brr_dir / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "evt-2.md").write_text("---\nid: evt-2\nstatus: pending\n---\nq\n")
        (outbox / "001.md").write_text("---\nevent: evt-2\n---\nsame text\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1", meta={})
        daemon._drain_outbox(emit, task, responses, "evt-1", outbox, inbox)
        resp = responses / "evt-1.md"
        resp.write_text("same text\n")
        assert not daemon._terminal_stream_duplicates_delivered(task, resp)


class TestLiveRunBodyMirror:
    """A running run's node carries its card, not an empty body section."""

    def test_card_change_mirrors_the_body_onto_the_run_node(
        self, tmp_path, monkeypatch,
    ):
        repo = tmp_path / "repo"
        (repo / ".brr").mkdir(parents=True)
        (repo / ".git").mkdir()
        ctx = daemon.account.resolve_context(
            repo,
            {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "home")},
        )
        outbox = repo / ".brr" / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        card = outbox / ".card"
        card.write_text("## Now\n\nMid-flight.\n", encoding="utf-8")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=repo / ".brr", conversation_key="k", event_id="evt-1",
        )
        task = daemon.Run(
            id="run-live", event_id="evt-1", body="work", source="telegram",
            status="running", meta={"repo_label": "Gurio/brr"},
        )
        state: dict = {}

        assert daemon._drain_agent_card(
            emit, task, "evt-1", card, state,
            account_context=ctx, repo_label="Gurio/brr",
        ) is True

        body = ctx.runs_dir / "Gurio__brr" / "run-live" / "body.md"
        assert body.read_text(encoding="utf-8") == "## Now\n\nMid-flight.\n"

        # A re-read with unchanged text is still a no-op; no rewrite storm.
        card.write_text("## Now\n\nLater.\n", encoding="utf-8")
        assert daemon._drain_agent_card(
            emit, task, "evt-1", card, state,
            account_context=ctx, repo_label="Gurio/brr",
        ) is True
        assert body.read_text(encoding="utf-8") == "## Now\n\nLater.\n"

    def test_without_an_account_context_the_drain_is_unchanged(
        self, tmp_path, monkeypatch,
    ):
        outbox = tmp_path / ".brr" / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        card = outbox / ".card"
        card.write_text("plain\n", encoding="utf-8")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=tmp_path / ".brr", conversation_key="k", event_id="evt-1",
        )
        task = types.SimpleNamespace(id="task-1")

        assert daemon._drain_agent_card(emit, task, "evt-1", card, {}) is True
