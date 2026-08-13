"""Tests for the agent outbox + daemon mid-flight drain (slice 4b).

The producer half of the multi-response protocol: the resident drops
interim replies in ``.brr/outbox/<eid>/``, the daemon drains them to the
response partials queue, and the live card / conversation log reflect
the check-in. See ``kb/design-multi-response.md``.
"""

from __future__ import annotations

import json
import os
import time
import types

from brr import (
    account,
    card,
    conversations,
    daemon,
    hooks,
    message_store,
    portals,
    protocol,
    run_context,
    run_progress,
    updates,
)
from brr.envs import RunContext
from brr.run import Run

from _helpers import write_repo_scaffold


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


def test_is_staging_name_matches_tmp_anywhere_in_the_suffix_chain():
    """The predicate three drains share (#590)."""
    assert portals.is_staging_name("note.md.tmp")
    assert portals.is_staging_name("note.md.tmp.1680005.8f6c42b9a1f7")
    assert portals.is_staging_name("note.tmp.md")  # any component counts
    # Real messages, including ones whose *name* merely contains "tmp".
    assert not portals.is_staging_name("note.md")
    assert not portals.is_staging_name("tmp-notes.md")
    assert not portals.is_staging_name("note-tmp.md")


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

    def test_chat_wire_blocks_a_leading_routing_selector_body(
        self, tmp_path, monkeypatch,
    ):
        """A selector stranded behind unrelated frontmatter is machine-tense,
        not a reply. Exercise the real drain-to-partial delivery caller."""
        n, responses, outbox, emitted = self._drain(
            tmp_path, monkeypatch,
            [("casualty.md", "---\nlabel: x\n---\nevent: evt-secret\n")],
        )

        assert n == 0
        assert protocol.list_partials(responses, "evt-1") == []
        assert emitted == []
        [notice] = daemon._read_outbox_notices(outbox)
        assert notice["source_file"] == "casualty.md"
        assert "directive-shaped body" in notice["text"]
        assert "NOT delivered" in notice["text"]

    def test_chat_wire_leaves_dividers_and_mid_text_directives_untouched(
        self, tmp_path, monkeypatch,
    ):
        body = "Normal prose.\n\n---\n\nQuoted example:\nevent: evt-secret\n"
        n, responses, _outbox, _emitted = self._drain(
            tmp_path, monkeypatch, [("reply.md", body)],
        )

        assert n == 1
        [partial] = protocol.list_partials(responses, "evt-1")
        assert protocol.read_partial(partial) == body.strip()

    def test_terminal_chat_body_is_guarded_but_dispatch_edge_is_not(
        self, tmp_path,
    ):
        """The shared staging seam covers terminal chat, while a strand's
        terminal return remains a non-chat dispatch-edge value."""
        repo = tmp_path / "repo"
        repo.mkdir()
        ctx = account.resolve_context(
            repo,
            {"home.path": str(tmp_path / "home"), "repo.label": "Gurio/brr"},
        )
        responses = tmp_path / "responses"
        outbox = tmp_path / "outbox"
        inbox = tmp_path / "inbox"
        outbox.mkdir()
        body = "event: evt-machine\n---\nraw directive\n"

        protocol.create_event(inbox, "telegram", "chat")
        chat_event = next(
            event for event in protocol.list_pending(inbox)
            if event["source"] == "telegram"
        )
        chat_id = chat_event["id"]
        chat_response = protocol.write_response(responses, chat_id, body)
        chat_task = Run(
            id="run-chat", event_id=chat_id, body="", source="telegram",
            meta={"repo_label": "Gurio/brr", "outbox_path": str(outbox)},
        )
        chat_path = daemon._stage_terminal_response(
            chat_task, ctx, chat_event, chat_response,
        )

        assert chat_path is not None
        [chat_row] = message_store.list_messages(chat_path.parent)
        assert chat_row["status"] == message_store.UNDELIVERABLE
        [notice] = daemon._read_outbox_notices(outbox)
        assert notice["source_file"] == f"{chat_id}.md"

        protocol.create_event(inbox, "spawn", "strand")
        strand_event = next(
            event for event in protocol.list_pending(inbox)
            if event["source"] == "spawn"
        )
        strand_id = strand_event["id"]
        strand_response = protocol.write_response(responses, strand_id, body)
        strand_task = Run(
            id="run-strand", event_id=strand_id, body="", source="spawn",
            meta={
                "repo_label": "Gurio/brr",
                "outbox_path": str(outbox),
                "spawn_parent_run_id": "run-parent",
            },
        )
        strand_path = daemon._stage_terminal_response(
            strand_task, ctx, strand_event, strand_response,
        )

        assert strand_path is not None
        [strand_row] = message_store.list_messages(strand_path.parent)
        assert strand_row["status"] == message_store.PENDING
        assert len(daemon._read_outbox_notices(outbox)) == 1

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

    def test_skips_editor_staging_names_with_a_trailing_token(
        self, tmp_path, monkeypatch,
    ):
        """#590: ``.tmp`` is not always the *last* suffix component.

        Claude's editor stages as ``<name>.tmp.<pid>.<rand>`` and renames.
        A bare ``Path.suffix == ".tmp"`` check saw ``.8f6c42b9a1f7`` and
        delivered the half-written file as a chat message — the resident's
        own rename then failed with ENOENT on a message the user already
        had. The real staging name from that incident is the fixture.
        """
        n, responses, outbox, _ = self._drain(
            tmp_path, monkeypatch,
            [("note-dispatch.md.tmp.1680005.8f6c42b9a1f7", "half written"),
             ("real.md", "hi\n")],
        )
        assert n == 1
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["hi"]
        staging = outbox / "note-dispatch.md.tmp.1680005.8f6c42b9a1f7"
        assert staging.exists(), "the rename must still have a file to rename"

    def test_staged_then_renamed_message_delivers_exactly_once(
        self, tmp_path, monkeypatch,
    ):
        """The whole point of the staging skip: commit-by-rename works."""
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        outbox = brr_dir / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        staged = outbox / "note.md.tmp.4242.cafe"
        staged.write_text("the whole message\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")

        assert daemon._drain_outbox(
            emit, task, responses, "evt-1", outbox) == 0
        staged.rename(outbox / "note.md")
        assert daemon._drain_outbox(
            emit, task, responses, "evt-1", outbox) == 1
        assert daemon._drain_outbox(
            emit, task, responses, "evt-1", outbox) == 0

        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["the whole message"]

    def test_staging_file_is_not_listed_as_a_pending_outbox_file(
        self, tmp_path,
    ):
        """The same predicate, on the resident's own portal read (#590).

        A staging file counted as an undelivered message made
        ``portal-state.json`` report work the resident had not left behind.
        """
        outbox = tmp_path / "outbox" / "evt-1"
        outbox.mkdir(parents=True)
        (outbox / "note.md.tmp.99.beef").write_text("half written")
        (outbox / "real.md").write_text("hi\n")
        (outbox / "notes.md").write_text("also real\n")

        assert sorted(daemon._outbox_message_files(outbox)) == [
            "notes.md", "real.md"]

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

    def test_incapable_gate_with_no_addressing_is_refused_not_synthesized(
        self, tmp_path, monkeypatch,
    ):
        """#1205: the drawer the courier never opens, closed at synthesis.

        ``gate: cloud`` with no ``cloud_event_id`` used to synthesize a
        `done` cloud event no delivery loop would ever visit — silent,
        structurally impossible. It must now be refused loudly instead: no
        event, a notice naming the two lanes that actually work.
        """
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "ping.md").write_text(
            "---\ngate: cloud\n---\nunaddressed ping\n")
        # cloud reads as configured/deliverable here (real capability check
        # is the thing under test, not plumbing availability).
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 0
        assert protocol.list_done(inbox, "cloud") == []
        assert list(inbox.glob("*.md")) == []
        notices = daemon._read_outbox_notices(outbox)
        assert len(notices) == 1
        text = notices[0]["text"]
        assert "NOT delivered" in text
        assert "cloud" in text
        assert "interim on the current event" in text
        assert "event: <id>" in text

    def test_incapable_gate_with_its_own_addressing_still_queues(
        self, tmp_path, monkeypatch,
    ):
        """Same incapable gate, but the message carries its own address
        (``cloud_event_id`` — what would make it a *reply*, not a fresh
        send): the capability check must not block what the gate actually
        can do."""
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "ping.md").write_text(
            "---\ngate: cloud\ncloud_event_id: cev-123\n---\naddressed reply\n")
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        [ev] = protocol.list_done(inbox, "cloud")
        assert ev["cloud_event_id"] == "cev-123"
        assert protocol.read_response(responses, ev["id"]).strip() == "addressed reply"
        assert daemon._read_outbox_notices(outbox) == []

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

    def test_forge_pr_body_with_a_close_keyword_tail_is_refused(
        self, tmp_path, monkeypatch,
    ):
        """#749's own death, replayed on the channel that had no guard (#839).

        PR #838's body carried `Closes #749 move 5 (the ticket stays open for
        moves 1-4).` GitHub matched the head of the line, discarded the clause
        written to prevent the close, and shut three unshipped moves off the
        open list. The commit-msg hook would have refused that exact line; it
        never saw it, because a PR body passes through no hook. Now the drain
        refuses it, the PR is not queued, and the run finds out via `notices`
        while it is still alive to fix the file.
        """
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "pr.md").write_text(
            "---\ngate: forge\nhead: brr/feat-x\nbase: main\n"
            "title: Review feat-x\n---\n"
            "Ships move 5 of the schedule rework.\n"
            "\n"
            "Closes #749 move 5 (the ticket stays open for moves 1-4).\n")
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 0
        assert protocol.list_done(inbox, "github") == []
        assert protocol.list_pending(inbox) == []
        # No acceptance receipt: nothing was handed off.
        assert not (outbox / hooks.FORGE_HANDOFF_NAME).exists()
        notices = daemon._read_outbox_notices(outbox)
        assert len(notices) == 1
        text = notices[0]["text"]
        assert "was NOT created" in text
        assert "Closes #749 move 5" in text
        # The diagnosis has to carry a way out, or the guard is unsatisfiable.
        assert "Part of #NNN" in text
        assert "Mask the digits" in text

    def test_forge_pr_body_with_a_bare_close_is_queued(
        self, tmp_path, monkeypatch,
    ):
        """The guard must not fire on the shape a PR legitimately wants.

        A bare `Closes #NNN.` is the intended close and the whole point of the
        handoff; a guard that refused it would be refusing the feature.
        """
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "pr.md").write_text(
            "---\ngate: forge\nhead: brr/feat-x\nbase: main\n"
            "title: Review feat-x\n---\n"
            "Ships the whole thing.\n\nCloses #839.\n")
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        assert len(protocol.list_done(inbox, "github")) == 1
        assert daemon._read_outbox_notices(outbox) == []

    def test_non_pr_gate_body_is_not_close_checked(self, tmp_path, monkeypatch):
        """A chat message is not a channel GitHub closes from.

        Widening the check to every gate would make the guard fire where it
        has no authority — the class error #839 is about, run in reverse.
        """
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "ping.md").write_text(
            "---\ngate: telegram\n---\n"
            "Closes #749 move 5 (the ticket stays open for moves 1-4).\n")
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        assert daemon._read_outbox_notices(outbox) == []

    def test_github_gate_pr_action_is_close_checked_too(
        self, tmp_path, monkeypatch,
    ):
        """`gate: forge` is an alias, not the only spelling of the PR path."""
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "pr.md").write_text(
            "---\ngate: github\ngithub_action: pull_request\n"
            "head: brr/feat-x\nbase: main\ntitle: t\n---\n"
            "Fix #533: split config and closes #534\n")
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 0
        notices = daemon._read_outbox_notices(outbox)
        assert "rides the subject after the colon" in notices[0]["text"]

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

    def test_gate_addressed_unconfigured_bare_name_names_the_configured_set(
        self, tmp_path, monkeypatch,
    ):
        """The refusal notice must state the failure that actually occurred
        (this account can't deliver here) and name what it *can* deliver —
        not a fixed string diagnosing a different mistake (#568 defect 1).
        The configured set must come from a real probe, not a hardcoded
        list, so it is monkeypatched independently of the fixture repo."""
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "ping.md").write_text("---\ngate: forge\n---\nhi\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        # Only telegram and cloud are configured on this (fake) account;
        # forge aliases to github, which is not in the set.
        monkeypatch.setattr(
            daemon, "_gate_is_configured",
            lambda _brr, name: name in {"telegram", "cloud"},
        )
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 0
        notices = daemon._read_outbox_notices(outbox)
        assert len(notices) == 1
        text = notices[0]["text"]
        assert "not deliverable on this account" in text
        assert "configured gates: telegram, cloud" in text
        assert "NOT delivered" in text
        # Must not repeat the thread-string misdiagnosis for a bare name.
        assert "thread string" not in text

    def test_gate_addressed_thread_string_gets_the_bare_name_hint(
        self, tmp_path, monkeypatch,
    ):
        """A `gate:` value that looks like a thread/conversation-key string
        (contains `:`) gets the distinct "bare name, not a thread string"
        hint — the one case the original fixed message was actually true
        for (#568 defect 1)."""
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        (outbox / "ping.md").write_text("---\ngate: telegram:12345\n---\nhi\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 0
        notices = daemon._read_outbox_notices(outbox)
        assert len(notices) == 1
        text = notices[0]["text"]
        assert "thread string" in text
        assert "not a configured gate" in text
        # Distinct from the unconfigured-bare-name notice above.
        assert "configured gates:" not in text

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
        # Same gate as the target (telegram) — the reachable, unchanged path.
        task = types.SimpleNamespace(id="task-A", source="telegram")
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
        # Same gate as the target (telegram) — the reachable, unchanged path.
        task = types.SimpleNamespace(id="task-A", source="telegram")

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
        # Same gate as the target (telegram) — the reachable, unchanged path.
        task = types.SimpleNamespace(id="task-A", source="telegram")
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

    def test_short_id_reply_resolves_cross_event(self, tmp_path, monkeypatch):
        """#906 fast-follow: the letter chrome renders a shortened id
        (``evt-…8jwi``) in the pending list, not the full nanosecond-stamped
        id. Addressing a reply with that reconstructed short form must
        resolve to the full id and route exactly as a full-id reply would.
        """
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        protocol.create_event(inbox, source="telegram", body="quick q")
        evB = protocol.list_pending(inbox)[0]
        bid = evB["id"]
        short = hooks._short_event_id(bid)
        assert short != bid  # sanity: the chrome form really is shortened
        (outbox / "reply.md").write_text(
            f"---\nevent: {short}\n---\nhere's the answer\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A", source="telegram")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        assert [protocol.read_partial(p)
                for p in protocol.list_partials(responses, bid)] == ["here's the answer"]
        assert [e["id"] for e in protocol.list_done(inbox, "telegram")] == [bid]

    def test_short_id_reply_bare_tail_resolves(self, tmp_path, monkeypatch):
        """The resolution rule also accepts the bare tail (no ``evt-``
        prefix, no ellipsis) — whatever fragment the resident reconstructs,
        as long as it is unambiguous."""
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        protocol.create_event(inbox, source="telegram", body="quick q")
        bid = protocol.list_pending(inbox)[0]["id"]
        tail = bid.rsplit("-", 1)[-1]
        (outbox / "reply.md").write_text(
            f"---\nevent: {tail}\n---\nhere's the answer\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A", source="telegram")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        assert [protocol.read_partial(p)
                for p in protocol.list_partials(responses, bid)] == ["here's the answer"]

    def test_self_reply_via_reconstructed_short_id_does_not_bounce(
        self, tmp_path, monkeypatch,
    ):
        """Reproduces the live defect the thread-of-record fast-follow names:
        a reply addressed at the run's *own* lead event, via that event's
        reconstructed short id, used to fail the exact-string match, read
        as a cross-event target to an unknown event, and bounce as
        'not pending' — even though the event was this run's own. It must
        resolve, stay in-thread (``cross`` false), and land on the current
        event's own partials queue.
        """
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox_root = brr_dir / "outbox"
        protocol.create_event(inbox, source="telegram", body="lead question")
        own = protocol.list_pending(inbox)[0]
        own_id = own["id"]
        outbox = outbox_root / own_id
        outbox.mkdir(parents=True)
        short = hooks._short_event_id(own_id)
        (outbox / "reply.md").write_text(
            f"---\nevent: {short}\n---\nhere's the self-reply\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id=own_id)
        task = types.SimpleNamespace(id="task-A", source="telegram")
        n = daemon._drain_outbox(emit, task, responses, own_id, outbox, inbox)

        assert n == 1
        assert [protocol.read_partial(p)
                for p in protocol.list_partials(responses, own_id)] == [
            "here's the self-reply"
        ]
        # It must not have been misrouted onto a synthesized "done" event —
        # the own event stays pending, this was an in-thread interim.
        assert [e["id"] for e in protocol.list_pending(inbox)] == [own_id]

    def test_ambiguous_short_id_reply_is_refused_naming_candidates(
        self, tmp_path, monkeypatch,
    ):
        """Never guess-match: when a short id's tail matches more than one
        pending event, the reply is refused (dropped, undeliverable) and the
        notice names every candidate — not delivered to either thread."""
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        protocol.create_event(inbox, source="telegram", body="first")
        protocol.create_event(inbox, source="telegram", body="second")
        evs = protocol.list_pending(inbox)
        b1, b2 = evs[0]["id"], evs[1]["id"]
        # Force a shared tail — real ids are random 4-char suffixes and
        # collisions are exceedingly rare in practice, but the router must
        # still refuse rather than guess when they do collide.
        shared_tail = "z9z9"
        (inbox / f"{b1}.md").write_text(
            (inbox / f"{b1}.md").read_text().replace(b1, f"evt-1000000000000000000-{shared_tail}")
        )
        (inbox / f"{b1}.md").rename(inbox / f"evt-1000000000000000000-{shared_tail}.md")
        (inbox / f"{b2}.md").write_text(
            (inbox / f"{b2}.md").read_text().replace(b2, f"evt-2000000000000000000-{shared_tail}")
        )
        (inbox / f"{b2}.md").rename(inbox / f"evt-2000000000000000000-{shared_tail}.md")
        (outbox / "reply.md").write_text(
            f"---\nevent: {shared_tail}\n---\nwhich one?\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A", source="telegram")
        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 0
        assert not (outbox / "reply.md").exists()
        notices = (outbox / daemon.NOTICES_FILE).read_text(encoding="utf-8")
        assert "ambiguous" in notices
        assert f"evt-…{shared_tail}" in notices
        assert protocol.list_partials(
            responses, f"evt-1000000000000000000-{shared_tail}"
        ) == []
        assert protocol.list_partials(
            responses, f"evt-2000000000000000000-{shared_tail}"
        ) == []


class TestCrossGateReplyRouting:
    """The three-branch acceptance contract for #578.

    At outbox acceptance of an ``event:``-addressed reply: the target's
    owning gate is either reachable from this run (same gate, or a
    different gate that's actually configured/running here — deliver
    exactly as before), not reachable (a real gate, just not this one and
    not configured here — redirect to this run's own gate, prefixed with
    the origin), or no gate at all (a dispatch-tree source like
    ``schedule``/``spawn`` — retire-and-drop, but as a named, recorded
    outcome, never a silent partial). Every branch must leave a real
    delivery status behind — nothing lands in ``.partials`` on a status
    of nothing-decided.
    """

    def _account_ctx(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        return account.resolve_context(
            repo, {"home.path": str(tmp_path / "home"), "repo.label": "Gurio/brr"},
        )

    def test_reachable_cross_gate_reply_is_delivered_unchanged(
        self, tmp_path, monkeypatch,
    ):
        # Different gate than the run's own, but configured/running here —
        # branch 1: no behaviour change from the pre-#578 path.
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        protocol.create_event(inbox, source="cloud", body="quick q")
        bid = protocol.list_pending(inbox)[0]["id"]
        (outbox / "reply.md").write_text(f"---\nevent: {bid}\n---\nthe answer\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A", source="telegram")

        n = daemon._drain_outbox(emit, task, responses, "evt-A", outbox, inbox)

        assert n == 1
        assert [protocol.read_partial(p)
                for p in protocol.list_partials(responses, bid)] == ["the answer"]
        assert protocol.list_partials(responses, "evt-A") == []
        assert [e["id"] for e in protocol.list_done(inbox, "cloud")] == [bid]

    def test_cross_gate_reply_not_reachable_redirects_to_own_gate(
        self, tmp_path, monkeypatch,
    ):
        # A real gate (cloud) owns the target, but it's neither this run's
        # own gate (telegram) nor configured/running here — branch 2:
        # redirect onto this run's own gate, prefixed, target still retired.
        ctx = self._account_ctx(tmp_path)
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        protocol.create_event(inbox, source="cloud", body="please check the deploy")
        bid = protocol.list_pending(inbox)[0]["id"]
        (outbox / "reply.md").write_text(f"---\nevent: {bid}\n---\nlooks fine\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: False)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="telegram:1:", event_id="evt-A")
        task = types.SimpleNamespace(
            id="task-A", source="telegram", conversation_key="telegram:1:", meta={},
        )

        n = daemon._drain_outbox(
            emit, task, responses, "evt-A", outbox, inbox,
            account_context=ctx,
        )

        # Delivered — but on this run's own event, not the foreign one.
        assert n == 1
        assert protocol.list_partials(responses, bid) == []
        [redirected] = protocol.list_partials(responses, "evt-A")
        body = protocol.read_partial(redirected)
        assert body.startswith("re: please check the deploy — originally on cloud")
        assert body.endswith("looks fine")
        # The foreign event is still retired, same as any cross reply.
        assert [e["id"] for e in protocol.list_done(inbox, "cloud")] == [bid]
        assert protocol.list_pending(inbox) == []
        notices = daemon._read_outbox_notices(outbox)
        assert any("redirected" in n["text"] for n in notices)
        # Both message-store rows exist and neither is left with no status.
        messages_dir = message_store.run_messages_dir(ctx, "Gurio/brr", "task-A")
        rows = message_store.list_messages(messages_dir)
        assert len(rows) == 2
        statuses = {row["target_event"]: row["status"] for row in rows}
        assert statuses[bid] == message_store.UNDELIVERABLE
        assert statuses["evt-A"] == message_store.PENDING

    def test_cross_target_with_no_gate_at_all_is_recorded_undeliverable(
        self, tmp_path, monkeypatch,
    ):
        # The target belongs to a dispatch-tree pseudo source (no gate owns
        # it at all, e.g. schedule) — branch 3: keep retire-and-drop, but
        # it must be a named, recorded outcome, never a silent partial.
        ctx = self._account_ctx(tmp_path)
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        protocol.create_event(inbox, source="schedule", body="tick")
        bid = protocol.list_pending(inbox)[0]["id"]
        (outbox / "reply.md").write_text(f"---\nevent: {bid}\n---\nnoted\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A", source="telegram", meta={})

        n = daemon._drain_outbox(
            emit, task, responses, "evt-A", outbox, inbox,
            account_context=ctx,
        )

        # Nothing is delivered anywhere — not to the target, not redirected.
        assert n == 0
        assert protocol.list_partials(responses, bid) == []
        assert protocol.list_partials(responses, "evt-A") == []
        assert [e["id"] for e in protocol.list_done(inbox, "schedule")] == [bid]
        notices = daemon._read_outbox_notices(outbox)
        [notice] = notices
        notice_text = " ".join(notice["text"].split())
        assert f"event {bid} retired done" in notice_text
        assert "reply text staged undeliverable" in notice_text
        assert "no gate owns schedule events" in notice_text
        assert "gate:<name>" in notice_text
        assert "not delivered" not in notice_text.lower()
        assert "originating user event" not in notice_text.lower()
        messages_dir = message_store.run_messages_dir(ctx, "Gurio/brr", "task-A")
        [row] = message_store.list_messages(messages_dir)
        assert row["status"] == message_store.UNDELIVERABLE
        assert row["status"]

    def test_own_event_notice_may_not_claim_a_retire_that_never_happens(
        self, tmp_path, monkeypatch,
    ):
        """The retire clause is only true on the branch that retires.

        ``target_source`` falls back to the *run's own* source when there is no
        cross target, so a plain outbox message from a gate-less run (schedule —
        every self-woken run) lands in the same ``not deliverable`` branch. The
        retire, however, is guarded by ``cross and target_event is not None``,
        which is False there. A notice that asserts the retire unconditionally
        therefore tells a resident its waking event is handled while it is still
        pending — an optimistic lie, which is the worse direction: the text it
        replaced claimed nothing about retirement at all.

        The assertion is the honest one: whatever the notice *claims* about the
        retire has to match what the inbox actually did.
        """
        ctx = self._account_ctx(tmp_path)
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        protocol.create_event(inbox, source="schedule", body="tick")
        eid = protocol.list_pending(inbox)[0]["id"]
        outbox = brr_dir / "outbox" / eid
        outbox.mkdir(parents=True)
        # No frontmatter: an ordinary mid-thought message to the waking thread.
        (outbox / "note.md").write_text("noted\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id=eid)
        task = types.SimpleNamespace(id="task-A", source="schedule", meta={})

        daemon._drain_outbox(
            emit, task, responses, eid, outbox, inbox, account_context=ctx,
        )

        retired = [e["id"] for e in protocol.list_done(inbox, "schedule")]
        notices = [
            " ".join(n["text"].split())
            for n in daemon._read_outbox_notices(outbox)
        ]
        claims_retire = any("retired done" in text for text in notices)
        assert claims_retire == (eid in retired), (
            f"notice claims retire={claims_retire} but inbox retired={eid in retired}; "
            f"notices={notices}"
        )

    def test_no_accepted_path_leaves_an_absent_delivery_status(
        self, tmp_path, monkeypatch,
    ):
        # Sweep the not-reachable (redirect), no-gate-at-all, and
        # unknown-target-drop branches and assert every message-store row
        # this run staged carries a real, non-empty status — the "bug
        # beneath the bug" #578 called out.
        ctx = self._account_ctx(tmp_path)
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True)
        unreachable_id = protocol.create_event(
            inbox, source="cloud", body="a").stem
        no_gate_id = protocol.create_event(
            inbox, source="schedule", body="b").stem
        (outbox / "001.md").write_text(f"---\nevent: {unreachable_id}\n---\ny\n")
        (outbox / "002.md").write_text(f"---\nevent: {no_gate_id}\n---\nz\n")
        (outbox / "003.md").write_text("---\nevent: evt-ghost\n---\nw\n")
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        # ``cloud`` is a real gate, just not reachable from this run — branch 2.
        monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: False)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="telegram:1:", event_id="evt-A")
        task = types.SimpleNamespace(
            id="task-A", source="telegram", conversation_key="telegram:1:", meta={},
        )

        daemon._drain_outbox(
            emit, task, responses, "evt-A", outbox, inbox,
            account_context=ctx,
        )

        messages_dir = message_store.run_messages_dir(ctx, "Gurio/brr", "task-A")
        rows = message_store.list_messages(messages_dir)
        assert rows, "expected at least one staged message-store row"
        for row in rows:
            assert row.get("status"), f"row with no delivery status: {row}"


class TestDrainOutboxCrossInbox:
    """#936: ``event:`` addressing across the daemon's inbox union.

    A telegram-woken run drains against ``.brr/inbox``; a cloud letter
    lives in the account dispatch inbox. Before the union, the reply was
    refused "already handled" while dispatch — reading the other drawer —
    woke the next run on the very event the resident had just answered.
    Addressing now searches the same set of inboxes dispatch scans
    (``_dispatchable_inbox_sources``), delivers into the responses dir
    paired with the target's own inbox, and the refusal notice names the
    actual cause instead of conflating two (really three) of them.
    """

    def _ctx(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        return account.resolve_context(
            repo, {"home.path": str(tmp_path / "home"), "repo.label": "Gurio/brr"},
        )

    def _drain(self, tmp_path, monkeypatch, ctx, files, event_id="evt-A"):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        outbox = brr_dir / "outbox" / event_id
        outbox.mkdir(parents=True, exist_ok=True)
        for name, text in files:
            (outbox / name).write_text(text)
        monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id=event_id)
        task = types.SimpleNamespace(
            id="task-A", source="telegram", meta={})
        n = daemon._drain_outbox(
            emit, task, responses, event_id, outbox, inbox,
            account_context=ctx,
        )
        return n, responses, inbox, outbox

    def test_cross_inbox_event_reply_delivers_and_clears(
        self, tmp_path, monkeypatch,
    ):
        """The reproducer from #936, fixed: the reply lands, and the event
        is marked handled in its actual location so it cannot wake the
        next run."""
        ctx = self._ctx(tmp_path)
        protocol.create_event(
            ctx.dispatch_inbox, source="telegram", body="two photos")
        bid = protocol.list_pending(ctx.dispatch_inbox)[0]["id"]
        n, responses, _inbox, outbox = self._drain(
            tmp_path, monkeypatch, ctx,
            [("reply.md", f"---\nevent: {bid}\n---\ngot both\n")],
        )
        assert n == 1
        # Delivered into the responses dir the dispatch inbox's own
        # delivery loop reads — not the run's own, which nobody polls
        # for this event.
        assert [protocol.read_partial(p)
                for p in protocol.list_partials(ctx.responses_dir, bid)] \
            == ["got both"]
        assert protocol.list_partials(responses, bid) == []
        # Handled in the file's actual location.
        assert protocol.list_pending(ctx.dispatch_inbox) == []
        assert [e["id"] for e in
                protocol.list_done(ctx.dispatch_inbox, "telegram")] == [bid]
        assert daemon._read_outbox_notices(outbox) == []

    def test_cross_inbox_short_id_resolves(self, tmp_path, monkeypatch):
        """Short-id resolution searches the same union (#906 → #936)."""
        ctx = self._ctx(tmp_path)
        protocol.create_event(
            ctx.dispatch_inbox, source="telegram", body="quick q")
        bid = protocol.list_pending(ctx.dispatch_inbox)[0]["id"]
        short = hooks._short_event_id(bid)
        assert short != bid
        n, _responses, _inbox, _outbox = self._drain(
            tmp_path, monkeypatch, ctx,
            [("reply.md", f"---\nevent: {short}\n---\nanswered\n")],
        )
        assert n == 1
        assert [protocol.read_partial(p)
                for p in protocol.list_partials(ctx.responses_dir, bid)] \
            == ["answered"]
        assert protocol.list_pending(ctx.dispatch_inbox) == []

    def test_refusal_distinguishes_unknown_from_not_pending(
        self, tmp_path, monkeypatch,
    ):
        """The old message conflated "already handled" with "the id is
        wrong" (and could not name the third cause at all). The two
        remaining causes are now stated apart, with the found event's
        location and actual status."""
        ctx = self._ctx(tmp_path)
        handled = protocol.create_event(
            ctx.dispatch_inbox, source="telegram", body="old", status="done",
        ).stem
        n, _responses, _inbox, outbox = self._drain(
            tmp_path, monkeypatch, ctx,
            [("001.md", "---\nevent: evt-ghost\n---\nhi\n"),
             ("002.md", f"---\nevent: {handled}\n---\nhi again\n")],
        )
        assert n == 0
        texts = [" ".join(x["text"].split())
                 for x in daemon._read_outbox_notices(outbox)]
        assert len(texts) == 2
        unknown = [t for t in texts if "evt-ghost" in t]
        assert len(unknown) == 1
        assert "not found in any inbox" in unknown[0]
        assert "not pending" not in unknown[0]
        found = [t for t in texts if handled in t]
        assert len(found) == 1
        assert "status=done (not pending)" in found[0]
        assert str(ctx.dispatch_inbox) in found[0]
        assert "not found in any inbox" not in found[0]


class TestDrainOutboxNote:
    """The ``note:`` verb — close a letter without speaking.

    The design's ``noted`` state (kb design-the-post → The letter's five
    states): today only a reply can close an event, forcing a run that
    answers a burst to choose between chat spam and permanently-queued
    events. A note retires a pending event deliberately — provenance
    stamped, nothing delivered — and refusals land in notices exactly
    like an ``event:`` reply's.
    """

    def _drain(self, tmp_path, monkeypatch, files):
        brr_dir = tmp_path / ".brr"
        responses = brr_dir / "responses"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        outbox = brr_dir / "outbox" / "evt-A"
        outbox.mkdir(parents=True, exist_ok=True)
        for name, text in files:
            (outbox / name).write_text(text)
        emitted = []
        monkeypatch.setattr(daemon.updates, "emit",
                            lambda brr, pkt: emitted.append(pkt))
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-A")
        task = types.SimpleNamespace(id="task-A", source="telegram", meta={})
        n = daemon._drain_outbox(
            emit, task, responses, "evt-A", outbox, inbox)
        return n, responses, inbox, outbox, emitted

    def test_note_retires_pending_event_with_no_outbound_message(
        self, tmp_path, monkeypatch,
    ):
        inbox = tmp_path / ".brr" / "inbox"
        protocol.create_event(inbox, source="telegram", body="thanks!")
        bid = protocol.list_pending(inbox)[0]["id"]
        n, responses, inbox, outbox, emitted = self._drain(
            tmp_path, monkeypatch,
            [("close.md", f"---\nnote: {bid}\n---\n")],
        )
        assert n == 1
        fm = protocol.parse_frontmatter(
            (inbox / f"{bid}.md").read_text(encoding="utf-8"))
        assert fm.get("status") == "noted"
        # Provenance: who closed the letter, and when.
        assert fm.get("noted_by") == "task-A"
        assert fm.get("noted_at")
        # No outbound message anywhere: no partials, and the event is not
        # ``done`` so no gate delivery loop will ever pick it up.
        assert protocol.list_partials(responses, bid) == []
        assert protocol.list_partials(responses, "evt-A") == []
        assert protocol.list_done(inbox, "telegram") == []
        assert protocol.list_active(inbox, "telegram") == []
        assert daemon._read_outbox_notices(outbox) == []
        assert not (outbox / "close.md").exists()
        assert [p.type for p in emitted] == ["event_noted"]
        assert emitted[0].payload.get("target_event") == bid

    def test_noted_event_is_not_dispatched(self, tmp_path, monkeypatch):
        inbox = tmp_path / ".brr" / "inbox"
        protocol.create_event(inbox, source="telegram", body="burst msg")
        bid = protocol.list_pending(inbox)[0]["id"]
        self._drain(
            tmp_path, monkeypatch,
            [("close.md", f"---\nnote: {bid}\n---\n")],
        )
        # ``noted`` is terminal for every pending-ness decision dispatch
        # makes — the event can never wake another run.
        assert protocol.list_pending(inbox) == []
        assert protocol.list_dispatchable(inbox) == []
        assert "noted" in protocol.TERMINAL_EVENT_STATUSES

    def test_note_short_id_resolves(self, tmp_path, monkeypatch):
        inbox = tmp_path / ".brr" / "inbox"
        protocol.create_event(inbox, source="telegram", body="ok!")
        bid = protocol.list_pending(inbox)[0]["id"]
        short = hooks._short_event_id(bid)
        n, _responses, inbox, _outbox, _emitted = self._drain(
            tmp_path, monkeypatch,
            [("close.md", f"---\nnote: {short}\n---\n")],
        )
        assert n == 1
        fm = protocol.parse_frontmatter(
            (inbox / f"{bid}.md").read_text(encoding="utf-8"))
        assert fm.get("status") == "noted"

    def test_note_unknown_id_is_refused_to_notices(
        self, tmp_path, monkeypatch,
    ):
        n, _responses, _inbox, outbox, emitted = self._drain(
            tmp_path, monkeypatch,
            [("close.md", "---\nnote: evt-ghost\n---\n")],
        )
        assert n == 0
        [notice] = daemon._read_outbox_notices(outbox)
        text = " ".join(notice["text"].split())
        assert text.startswith("note dropped:")
        assert "not found in any inbox" in text
        assert "nothing was retired" in text
        # Refused exactly like an event: reply — the file is consumed, and
        # only notices say why.
        assert not (outbox / "close.md").exists()
        assert emitted == []

    def test_note_on_non_pending_event_names_its_status(
        self, tmp_path, monkeypatch,
    ):
        inbox = tmp_path / ".brr" / "inbox"
        handled = protocol.create_event(
            inbox, source="telegram", body="old", status="done").stem
        n, _responses, _inbox, outbox, _emitted = self._drain(
            tmp_path, monkeypatch,
            [("close.md", f"---\nnote: {handled}\n---\n")],
        )
        assert n == 0
        [notice] = daemon._read_outbox_notices(outbox)
        text = " ".join(notice["text"].split())
        assert "status=done (not pending)" in text
        assert "nothing was retired" in text

    def test_note_body_text_is_ignored_but_logged(
        self, tmp_path, monkeypatch,
    ):
        inbox = tmp_path / ".brr" / "inbox"
        protocol.create_event(inbox, source="telegram", body="ping")
        bid = protocol.list_pending(inbox)[0]["id"]
        n, responses, inbox, outbox, _emitted = self._drain(
            tmp_path, monkeypatch,
            [("close.md", f"---\nnote: {bid}\n---\nsome words anyway\n")],
        )
        assert n == 1
        fm = protocol.parse_frontmatter(
            (inbox / f"{bid}.md").read_text(encoding="utf-8"))
        assert fm.get("status") == "noted"
        # The words were not delivered — anywhere — but not silently eaten.
        assert protocol.list_partials(responses, bid) == []
        [notice] = daemon._read_outbox_notices(outbox)
        assert "body text ignored" in notice["text"]

    def test_note_body_text_ignored_notice_is_advisory_kind(
        self, tmp_path, monkeypatch,
    ):
        """#1002: the file above was *accepted and acted on* (the event is
        retired ``noted``) — the notice it logs must carry ``kind:
        advisory``, not count toward ``!N`` like a real refusal/drop would.

        Asserted through the outbox drain the live defect actually used
        (``_drain_outbox`` -> ``_note_event_closed`` -> the ``note:``
        body-ignored branch at daemon.py — not by calling
        ``_record_outbox_notice`` directly), per the ticket's own measured
        example: seven of these fired in one run and drove ``!7`` for zero
        refusals.
        """
        inbox = tmp_path / ".brr" / "inbox"
        protocol.create_event(inbox, source="telegram", body="ping")
        bid = protocol.list_pending(inbox)[0]["id"]
        n, responses, inbox, outbox, _emitted = self._drain(
            tmp_path, monkeypatch,
            [("close.md", f"---\nnote: {bid}\n---\nsome words anyway\n")],
        )
        assert n == 1
        [notice] = daemon._read_outbox_notices(outbox)
        assert notice["kind"] == "advisory"


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
        """Two caps, two questions — this pins the one that reaches the wire.

        ``_CARD_CONTROL_MAX_BYTES`` bounds how much of ``.card`` the daemon is
        willing to *read*; ``card_text``'s ``max_length`` bounds what the live
        -runs PUT will *accept*. This test used to assert the read cap on the
        emitted packet, which is how a 64 KB payload could ride a 4 KB field
        with a green suite (#722): the read cap was never the publish bound and
        the projection, which was, silently declined to narrow.
        """
        big = "x" * (daemon._CARD_CONTROL_MAX_BYTES + 500)
        ok, emitted, _, _ = self._drain(tmp_path, monkeypatch, big)
        assert ok is True
        text = emitted[0].payload["text"]
        assert len(text) == card.CARD_TEXT_MAX_CHARS
        assert text.endswith("…")

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


def test_schedule_run_live_portals_read_repo_scoped_account_union(tmp_path):
    """Heartbeat callers see the same cross-drawer events dispatch can wake."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    repo_inbox = repo_a / ".brr" / "inbox"
    other_repo_inbox = repo_b / ".brr" / "inbox"
    outbox = repo_a / ".brr" / "outbox" / "evt-schedule"
    outbox.mkdir(parents=True)
    home = tmp_path / "account-home"
    account_inbox = home / "dispatch" / "inbox"
    repo_a_label = "Gurio/a"
    repo_b_label = "Gurio/b"
    account_context = account.AccountContext(
        account_id="default",
        dominion_repo=home,
        dispatch_inbox=account_inbox,
        responses_dir=home / "dispatch" / "responses",
        runs_dir=home / "runs",
        repos={
            repo_a_label: account.AccountRepo(repo_a_label, repo_a),
            repo_b_label: account.AccountRepo(repo_b_label, repo_b),
        },
        default_repo=account.AccountRepo(repo_a_label, repo_a),
    )

    protocol.create_event(
        repo_inbox,
        source="schedule",
        body="scheduled work",
        repo_label=repo_a_label,
    )
    current = protocol.list_pending(repo_inbox)[0]
    protocol.set_status(current, "processing")
    same_repo = protocol.create_event(
        account_inbox,
        source="cloud",
        body="same-repo chat",
        repo_label=repo_a_label,
    )
    other_repo = protocol.create_event(
        account_inbox,
        source="cloud",
        body="other-repo chat",
        repo_label=repo_b_label,
    )
    unlabeled = protocol.create_event(
        account_inbox,
        source="dispatch_message",
        body="account-scoped message",
    )
    other_drawer_unlabeled = protocol.create_event(
        other_repo_inbox,
        source="schedule",
        body="other repo's local event",
    )
    task = Run(
        id="run-schedule",
        event_id=current["id"],
        body="scheduled work",
        source="schedule",
        status="running",
        env="host",
        meta={},
    )

    inbox_path = daemon._write_live_inbox(
        outbox,
        repo_inbox,
        current["id"],
        account_context=account_context,
        repo_label=repo_a_label,
    )
    portal_path = daemon._write_live_portal_state(
        outbox,
        repo_inbox,
        current["id"],
        task,
        phase="running",
        refresh_levels=False,
        account_context=account_context,
        repo_label=repo_a_label,
    )

    expected_ids = {same_repo.stem, unlabeled.stem}
    excluded_ids = {other_repo.stem, other_drawer_unlabeled.stem}
    live_inbox = json.loads(inbox_path.read_text(encoding="utf-8"))
    portal = json.loads(portal_path.read_text(encoding="utf-8"))
    inbox_ids = {event["id"] for event in live_inbox["events"]}
    portal_ids = {event["id"] for event in portal["inbound"]["events"]}
    assert inbox_ids == expected_ids
    assert portal_ids == expected_ids
    assert excluded_ids.isdisjoint(inbox_ids | portal_ids)
    assert portal["attention"]["pending_event_count"] == 2


def test_pending_events_union_keeps_unregistered_run_repo_inbox(tmp_path):
    """The run drawer survives even when its repo is absent from the account."""
    run_repo = tmp_path / "run-repo"
    registered_repo = tmp_path / "registered-repo"
    run_inbox = run_repo / ".brr" / "inbox"
    registered_repo.mkdir(parents=True)
    home = tmp_path / "account-home"
    account_inbox = home / "dispatch" / "inbox"
    run_label = "Gurio/unregistered"
    registered_label = "Gurio/registered"
    account_context = account.AccountContext(
        account_id="default",
        dominion_repo=home,
        dispatch_inbox=account_inbox,
        responses_dir=home / "dispatch" / "responses",
        runs_dir=home / "runs",
        repos={
            registered_label: account.AccountRepo(
                registered_label, registered_repo,
            ),
        },
        default_repo=account.AccountRepo(registered_label, registered_repo),
    )

    current_path = protocol.create_event(
        run_inbox, source="schedule", body="current", repo_label=run_label,
    )
    current = protocol.list_pending(run_inbox)[0]
    protocol.set_status(current, "processing")
    local = protocol.create_event(
        run_inbox, source="schedule", body="local follow-up", repo_label=run_label,
    )
    account_event = protocol.create_event(
        account_inbox, source="cloud", body="account follow-up", repo_label=run_label,
    )

    events = daemon._pending_events_for_agent(
        run_inbox,
        current_path.stem,
        account_context=account_context,
        repo_label=run_label,
    )

    assert {event["id"] for event in events} == {
        local.stem,
        account_event.stem,
    }


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


def test_terminal_reply_lands_predicate():
    # #562: the one fact the Stop-hook delivery warning and the dispatch
    # path's terminal-stream suppression must agree on. Dispatch-tree
    # sources are owned by nobody — a reply addressed to one can never
    # arrive; that is a property of the protocol, not of a source name the
    # hook happens to string-match.
    assert daemon._terminal_reply_lands("telegram") is True
    assert daemon._terminal_reply_lands("slack") is True
    assert daemon._terminal_reply_lands("github") is True
    assert daemon._terminal_reply_lands("schedule") is False
    assert daemon._terminal_reply_lands("spawn") is False
    assert daemon._terminal_reply_lands("dispatch_message") is False
    # A spawning parent collects the child's terminal report along the
    # dispatch edge, so a spawn child's reply does land.
    assert daemon._terminal_reply_lands(
        "spawn", spawn_parent_run_id="run-parent") is True
    # Absent source is unknown, not impossible — never manufacture a false
    # "nobody will see this" out of a missing field.
    assert daemon._terminal_reply_lands("") is True


def test_terminal_route_names_what_carried_the_stream():
    # #743. ``_terminal_reply_lands`` answers True for a gate delivery and
    # for a dispatch-edge collection alike, which is exactly why "should the
    # terminal stdout be a delivery channel?" kept being reasoned about as
    # one question. This splits them, per run, at the moment it is decided.
    route = daemon._terminal_route

    # A gate delivered it and nothing else went out: the static dispatch is
    # the only reason this run's correspondent heard anything at all.
    assert route("telegram") == "gate-sole"
    # The run already spoke through the outbox — the closeout is additional
    # content, not the run's only voice.
    assert route("telegram", delivered_elsewhere=True) == "gate-extra"

    # A strand's report on the dispatch edge. Not a chat delivery: an
    # unambiguous return value to one parent, with nothing to duplicate and
    # no addressing to guess.
    assert route(
        "spawn", spawn_parent_run_id="run-parent") == "dispatch-edge"

    # The two shapes that already cost nothing if the static dispatch went
    # away, distinguished so they never inflate the count of what it saves.
    assert route("telegram", duplicate=True) == "duplicate"
    assert route("schedule", undeliverable=True) == "undeliverable"
    # Precedence: a duplicate is a duplicate even where a gate would have
    # taken it, and an undeliverable run that also spoke elsewhere is not a
    # gate delivery.
    assert route(
        "telegram", duplicate=True, delivered_elsewhere=True) == "duplicate"
    assert route(
        "spawn", undeliverable=True, delivered_elsewhere=True
    ) == "undeliverable"

    # An absent source lands by assumption (see the predicate above), so it
    # is neither a gate delivery nor a dispatch edge. Say so rather than
    # returning an empty string that reads as "no terminal stream".
    assert route("") == "unknown"

    # "no run left unheard": nobody owns the source, but a `notify.gate`
    # fallback carried the text anyway — distinct from both the ordinary
    # undeliverable shape and a gate that was actually addressed.
    assert route("schedule", undeliverable=False, gate_fallback=True) == "gate-fallback"
    # Duplicate still wins over everything, gate-fallback included — a
    # duplicate terminal is never re-routed anywhere, so the caller must
    # never be able to pass ``duplicate=True, gate_fallback=True`` and get
    # anything but the duplicate verdict.
    assert route(
        "schedule", duplicate=True, gate_fallback=True) == "duplicate"


# ── #1296: a stale spawn_parent_run_id is not a live dispatch edge ──────
#
# Root cause, reconstructed from run-260810-0728-x41g's own boundary
# evidence: a schedule-thread continuation woken by a ``spawn_completed``
# event inherits *that event's* ``spawn_parent_run_id`` field via
# ``Run.from_event``'s blanket meta copy — stamped there by
# ``_notify_spawn_parent`` to say who dispatched the strand that just
# completed, not who dispatched this continuation. The two only coincide
# while the schedule thread's own prior run is still alive; once it has
# finalized, ``_terminal_reply_lands``/``_terminal_route`` used to trust the
# id on presence alone and call it a landing dispatch edge forever.

def _save_run(runs_dir, run_id, status):
    Run(id=run_id, event_id=f"evt-{run_id}", body="", status=status).save(runs_dir)


def test_spawn_parent_still_collecting_checks_liveness(tmp_path):
    _save_run(tmp_path, "run-parent-live", "running")
    _save_run(tmp_path, "run-parent-dead", "done")

    # Unresolvable ⇒ unknown, not impossible — same posture as an absent
    # source in `_terminal_reply_lands`.
    assert daemon._spawn_parent_still_collecting("", tmp_path) is True
    assert daemon._spawn_parent_still_collecting("run-parent-live", None) is True
    assert daemon._spawn_parent_still_collecting("run-ghost", tmp_path) is True

    # Resolvable and still running/pending ⇒ a real dispatch edge.
    assert daemon._spawn_parent_still_collecting("run-parent-live", tmp_path) is True

    # Resolvable and already terminal (#1296's actual shape) ⇒ nobody is
    # left to collect anything.
    assert daemon._spawn_parent_still_collecting("run-parent-dead", tmp_path) is False


def test_terminal_reply_lands_checks_spawn_parent_liveness(tmp_path):
    _save_run(tmp_path, "run-parent-dead", "done")

    # Back-compat: no runs_dir given behaves exactly like before this fix —
    # presence alone lands it. Every pre-#1296 call site that doesn't pass
    # runs_dir (and every existing test pinning this predicate) must see no
    # change here.
    assert daemon._terminal_reply_lands(
        "spawn_completed", spawn_parent_run_id="run-parent-dead") is True

    # With runs_dir, a dead parent is not a dispatch edge, and
    # spawn_completed is itself owned by no gate — the reply has nowhere
    # left to land.
    assert daemon._terminal_reply_lands(
        "spawn_completed", spawn_parent_run_id="run-parent-dead",
        runs_dir=tmp_path,
    ) is False


def test_terminal_route_checks_spawn_parent_liveness(tmp_path):
    _save_run(tmp_path, "run-parent-dead", "done")
    route = daemon._terminal_route

    # Back-compat, same reasoning as the predicate above.
    assert route(
        "spawn_completed", spawn_parent_run_id="run-parent-dead") == "dispatch-edge"

    # A dead parent is not a dispatch edge once liveness is checked; falls
    # through to `unknown` here since `undeliverable`/`gate_fallback` are the
    # caller's job to compute (see the real call sites in daemon.py, which
    # now derive them from the fixed `_terminal_reply_lands`).
    assert route(
        "spawn_completed", spawn_parent_run_id="run-parent-dead",
        runs_dir=tmp_path,
    ) == "unknown"


def test_resolve_notify_gate_explicit_key_wins(tmp_path, monkeypatch):
    # An explicit key resolves outright — no need to check whether it is
    # also the *only* configured gate.
    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("telegram", "slack"),
    )
    assert daemon._resolve_notify_gate(
        {"notify.gate": "slack"}, tmp_path) == "slack"


def test_resolve_notify_gate_explicit_key_not_deliverable_resolves_to_nothing(
    tmp_path, monkeypatch,
):
    # A misconfigured/typo'd explicit key must not silently fall back to
    # inference — that would surprise an operator who deliberately named a
    # gate. It resolves like "nothing resolved" (current undeliverable
    # staging), never a crash.
    monkeypatch.setattr(
        daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram",
    )
    assert daemon._resolve_notify_gate(
        {"notify.gate": "slack"}, tmp_path) == ""


def test_resolve_notify_gate_infers_the_single_configured_user_chat_gate(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram",
    )
    assert daemon._resolve_notify_gate({}, tmp_path) == "telegram"


# ── #1205: capability, not name — an unaddressed-incapable gate never wins ─


def test_resolve_notify_gate_skips_an_incapable_sole_candidate(
    tmp_path, monkeypatch,
):
    # cloud is real-imported here (never faked): `CAN_SEND_UNADDRESSED =
    # False` lives on the module itself, and this fallback never carries
    # addressing of its own — so being the *only* configured gate must not
    # be enough. Resolves like "no candidate", never a synthesized event
    # nothing will ever deliver.
    monkeypatch.setattr(
        daemon, "_gate_can_deliver", lambda _brr, gate: gate == "cloud",
    )
    assert daemon._resolve_notify_gate({}, tmp_path) == ""


def test_resolve_notify_gate_falls_through_an_incapable_candidate(
    tmp_path, monkeypatch,
):
    # cloud + telegram configured: cloud is filtered out by capability
    # before the "how many candidates" count is even taken, so this reads
    # as single-gate inference (telegram), not "several — ambiguous".
    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("cloud", "telegram"),
    )
    assert daemon._resolve_notify_gate({}, tmp_path) == "telegram"


def test_resolve_notify_gate_explicit_incapable_key_resolves_to_nothing(
    tmp_path, monkeypatch,
):
    # An operator naming `notify.gate=cloud` explicitly still cannot make
    # an unaddressed send possible — explicit config wins the *selection*,
    # never the physics.
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, gate: True)
    assert daemon._resolve_notify_gate(
        {"notify.gate": "cloud"}, tmp_path) == ""


def test_gate_can_send_unaddressed_defaults_true_for_every_other_gate():
    # Absent `CAN_SEND_UNADDRESSED` -> capable, preserving today's behavior
    # for every gate that hasn't opted out.
    for gate in ("telegram", "slack", "github", "signal", "forge"):
        assert daemon._gate_can_send_unaddressed(gate) is True
    assert daemon._gate_can_send_unaddressed("cloud") is False


def test_resolve_notify_gate_zero_or_several_candidates_resolve_to_nothing(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, gate: False)
    assert daemon._resolve_notify_gate({}, tmp_path) == ""

    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("telegram", "slack"),
    )
    assert daemon._resolve_notify_gate({}, tmp_path) == ""


def test_resolve_notify_gate_never_infers_github(tmp_path, monkeypatch):
    # A forge PR/issue thread is not the correspondent who scheduled the
    # wake — inference must not silently pick it even when it is the only
    # gate configured. An explicit ``notify.gate=github`` still goes
    # through (that is the operator saying so), just never the guess.
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, gate: gate == "github")
    assert daemon._resolve_notify_gate({}, tmp_path) == ""
    assert daemon._resolve_notify_gate(
        {"notify.gate": "github"}, tmp_path) == "github"


# ── ambiguous candidates: the conversation-ownership tiebreak ─────────


def test_notify_gate_for_conversation_key_is_the_leading_field():
    # Every native key's first ``:``-field *is* its owning gate name — no
    # lookup table to keep in sync with conversations.gate_thread_key.
    assert daemon._notify_gate_for_conversation_key("telegram:555:0") == "telegram"
    assert daemon._notify_gate_for_conversation_key("slack:general:0") == "slack"
    assert daemon._notify_gate_for_conversation_key("cloud:telegram:1:0") == "cloud"
    # Non-gate keys resolve to a prefix that is simply never in the
    # candidate set — the caller filters it out, this makes no exception.
    assert daemon._notify_gate_for_conversation_key("schedule:director-tick") == "schedule"
    assert daemon._notify_gate_for_conversation_key("") == ""


def test_resolve_notify_gate_ambiguous_prefers_the_runs_own_conversation(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("telegram", "slack"),
    )
    assert daemon._resolve_notify_gate(
        {}, tmp_path, conversation_key="slack:general:0") == "slack"


def test_resolve_notify_gate_ambiguous_ignores_a_conversation_key_neither_owns(
    tmp_path, monkeypatch,
):
    # The run's own conversation belongs to a gate that isn't even one of
    # the ambiguous candidates (github is excluded from inference) — falls
    # through to the recent-activity tiebreak, which also finds nothing on
    # an empty account, so it stays unresolved rather than guessing.
    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("telegram", "slack"),
    )
    assert daemon._resolve_notify_gate(
        {}, tmp_path, conversation_key="github:owner/repo:42") == ""


def test_resolve_notify_gate_ambiguous_prefers_most_recently_active_thread(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("telegram", "slack"),
    )
    brr_dir = tmp_path / ".brr"
    for key, seconds_ago in (("telegram:1:0", 600), ("slack:general:0", 30)):
        conversations.append_event(
            brr_dir, key, {"id": f"evt-{key}", "source": key.split(":", 1)[0], "body": "hi"},
        )
        log_path = conversations.event_log_path(brr_dir, key, f"evt-{key}")
        stamp = time.time() - seconds_ago
        os.utime(log_path, (stamp, stamp))

    # No conversation_key of the run's own — only the repo-history signal.
    assert daemon._resolve_notify_gate({}, brr_dir, conversation_key="") == "slack"


def test_resolve_notify_gate_ambiguous_activity_ignores_non_candidate_threads(
    tmp_path, monkeypatch,
):
    # A github thread is the most recent in the repo, but github is not a
    # notify-fallback candidate — it must not win by recency either.
    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("telegram", "slack"),
    )
    brr_dir = tmp_path / ".brr"
    for key, seconds_ago in (("telegram:1:0", 600), ("github:o/r:9", 5)):
        conversations.append_event(
            brr_dir, key, {"id": f"evt-{key}", "source": key.split(":", 1)[0], "body": "hi"},
        )
        log_path = conversations.event_log_path(brr_dir, key, f"evt-{key}")
        stamp = time.time() - seconds_ago
        os.utime(log_path, (stamp, stamp))

    assert daemon._resolve_notify_gate({}, brr_dir, conversation_key="") == "telegram"


def test_portal_state_marks_schedule_event_not_replyable(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-sched"
    inbox.mkdir(parents=True)
    outbox.mkdir(parents=True)
    protocol.create_event(inbox, source="schedule", body="upkeep")
    event_id = str(protocol.list_pending(inbox)[0]["id"])
    task = Run(
        id="run-sched", event_id=event_id, body="upkeep",
        status="running", env="host", source="schedule",
    )
    path = daemon._write_live_portal_state(
        outbox, inbox, event_id, task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    # The event exists — that was exactly the trap: ``current_event`` alone
    # cannot tell a schedule wake from an addressed one.
    assert payload["inbound"]["current_event"] == event_id
    assert payload["inbound"]["current_event_replyable"] is False

    task.source = "telegram"
    path = daemon._write_live_portal_state(
        outbox, inbox, event_id, task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["inbound"]["current_event_replyable"] is True


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


def test_run_context_history_group_truncation_names_store_path(tmp_path):
    # #500: context.md is a durable artifact too — a bounded per-run
    # history copy must point at the store path here as well, not just
    # in the live daemon prompt.
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
            "history_groups": [
                {
                    "label": "telegram thread telegram:1:",
                    "path": str(tmp_path / ".brr/runs/task-1/history/gate.jsonl"),
                    "record_count": 400,
                    "total_record_count": 4321,
                    "truncated": True,
                    "store_path": str(tmp_path / ".brr/conversations/telegram__1__"),
                },
            ],
        },
    )

    assert "latest 400 of 4321 records" in text
    assert str(tmp_path / ".brr/conversations/telegram__1__") in text
    assert "untruncated" not in text


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


def test_parked_proposal_counts_as_promoted_but_not_as_delivered(
    tmp_path, monkeypatch,
):
    # #743. ``stats["current"]`` is not "a message reached a correspondent":
    # a parked ``runner_policy`` proposal increments it too. The terminal
    # route asks whether the run already put text in front of a reader, so
    # it reads ``delivered`` — which this pins as the *narrower* count.
    # Getting this wrong biases in the dangerous direction: a run whose only
    # actual delivery was the fallback net would be filed ``gate-extra``,
    # under-reporting what removing the net costs.
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {
            "repo.label": "Gurio/brr",
            "home.path": str(tmp_path / "account-home"),
        },
    )
    brr_dir = tmp_path / ".brr"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    (outbox / "001.md").write_text(
        "---\nrunner_policy: propose\n---\ncore: opus\n", encoding="utf-8")
    (outbox / "002.md").write_text("a real reply\n", encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
    emit = daemon._WorkerEmit(
        brr_dir=brr_dir, conversation_key="", event_id="evt-1")
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        emit, task, responses, "evt-1", outbox,
        account_context=ctx, stats=stats,
    )

    assert promoted == 2
    # Both files were promoted and both bumped ``current``...
    assert stats["current"] == 2
    assert stats["runner_policy"] == 1
    # ...but only the reply reached a reader.
    assert stats["delivered"] == 1


# ── strand isolation, the write side ─────────────────────────────────────
#
# Inbound is closed at one chokepoint (`_pending_events_for_agent`), and
# that chokepoint is complete. But the outbox's *address* union
# (`_outbox_address_sources`) was built from the full dispatchable inbox
# with no strand predicate, so a strand that learned a pending event id
# out of band could answer — and retire — a letter belonging to a thread
# it is structurally forbidden to read. These pin the far side of the same
# wall. The rule: a run may only address what its own inbox view shows it.


def _strand_drain_fixture(tmp_path):
    """(brr_dir, inbox, responses, outbox, own_event_id) for a strand run."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "spawn", "do the thing", status="processing")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    return brr_dir, inbox, responses, outbox, own.stem


def test_strand_reply_to_another_threads_event_is_refused(tmp_path, monkeypatch):
    """A strand may not answer someone else's mail.

    The correspondent's pending event is invisible to the strand by
    construction; replying to it by id would retire it, deliver into a
    conversation the strand cannot read, and break inbound isolation from
    the far side.
    """
    brr_dir, inbox, responses, outbox, own_id = _strand_drain_fixture(tmp_path)
    theirs = protocol.create_event(inbox, "telegram", "what's the ETA?")
    (outbox / "reply.md").write_text(
        f"---\nevent: {theirs.stem}\n---\nabout two hours\n", encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="", event_id=own_id)
    task = Run(id="run-strand", event_id=own_id, body="do the thing",
               source="spawn", meta={"strand": True})

    promoted = daemon._drain_outbox(
        emit, task, responses, own_id, outbox, inbox)

    assert promoted == 0
    # The letter is untouched — still pending, still its dispatcher's to close.
    assert theirs.stem in {ev["id"] for ev in protocol.list_pending(inbox)}
    # Nothing was delivered into the correspondent's thread.
    assert protocol.list_partials(responses, theirs.stem) == []
    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert notices[0]["kind"] == "refused"
    # The notice teaches the verb the strand actually wanted.
    assert "gate:" in notices[0]["text"]
    assert "strand-stack run" in notices[0]["text"]


def test_strand_note_of_another_threads_event_is_refused(tmp_path, monkeypatch):
    """`note:` is the same breach, quieter: it retires the letter and
    sends nothing, so nobody would ever see it happen."""
    brr_dir, inbox, responses, outbox, own_id = _strand_drain_fixture(tmp_path)
    theirs = protocol.create_event(inbox, "telegram", "still waiting")
    (outbox / "note.md").write_text(
        f"---\nnote: {theirs.stem}\n---\n", encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="", event_id=own_id)
    task = Run(id="run-strand", event_id=own_id, body="do the thing",
               source="spawn", meta={"strand": True})

    promoted = daemon._drain_outbox(
        emit, task, responses, own_id, outbox, inbox)

    assert promoted == 0
    assert theirs.stem in {ev["id"] for ev in protocol.list_pending(inbox)}
    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert notices[0]["kind"] == "refused"


def test_strand_may_answer_its_own_waking_event(tmp_path, monkeypatch):
    """The guard narrows the addressable set; it does not close it.

    A strand's own waking event is the default `event:` target (the drain
    fills it in when the frontmatter names none), and that path must stay
    open or every strand reply would be refused.
    """
    brr_dir, inbox, responses, outbox, own_id = _strand_drain_fixture(tmp_path)
    (outbox / "reply.md").write_text("done — 3 files changed\n", encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="", event_id=own_id)
    task = Run(id="run-strand", event_id=own_id, body="do the thing",
               source="spawn", meta={"strand": True})

    promoted = daemon._drain_outbox(
        emit, task, responses, own_id, outbox, inbox)

    # Nothing refused it — the whole point. (`promoted` is 0 and the body
    # stages undeliverable only because no *gate* owns a spawn-source event:
    # a strand's answer travels the dispatch edge, not a chat.)
    assert not [n for n in daemon._read_outbox_notices(outbox)
                if n["kind"] == "refused"]


def test_strand_may_answer_a_parent_steer_addressed_to_it(tmp_path, monkeypatch):
    """A `to:` steer is the strand's one inbound, and it is addressable.

    It is stamped ``spawn_message_for_event: <the strand's own event>`` —
    exactly the predicate `_pending_events_for_agent` uses to let the
    strand *see* it. Read side and write side agree, or a strand could
    read a steer it may never close.
    """
    brr_dir, inbox, responses, outbox, own_id = _strand_drain_fixture(tmp_path)
    steer = protocol.create_event(
        inbox, "spawn", "also check the codex path",
        spawn_message_for_event=own_id,
    )
    (outbox / "reply.md").write_text(
        f"---\nevent: {steer.stem}\n---\nchecked — clean\n", encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="", event_id=own_id)
    task = Run(id="run-strand", event_id=own_id, body="do the thing",
               source="spawn", meta={"strand": True})

    promoted = daemon._drain_outbox(
        emit, task, responses, own_id, outbox, inbox)

    assert steer.stem not in {ev["id"] for ev in protocol.list_pending(inbox)}
    assert not [n for n in daemon._read_outbox_notices(outbox)
                if n["kind"] == "refused"]


def test_resident_still_answers_across_the_inbox_union(tmp_path, monkeypatch):
    """The guard is strand-only: #936's cross-inbox addressing is untouched
    for a resident, which is the whole reason the union is wide."""
    brr_dir, inbox, responses, outbox, own_id = _strand_drain_fixture(tmp_path)
    theirs = protocol.create_event(inbox, "telegram", "what's the ETA?")
    (outbox / "reply.md").write_text(
        f"---\nevent: {theirs.stem}\n---\nabout two hours\n", encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="", event_id=own_id)
    task = Run(id="run-resident", event_id=own_id, body="do the thing",
               source="telegram")

    promoted = daemon._drain_outbox(
        emit, task, responses, own_id, outbox, inbox)

    assert promoted == 1
    assert daemon._read_outbox_notices(outbox) == []


def test_strand_gate_message_stays_unguarded(tmp_path, monkeypatch):
    """Outbound is open — the point of the whole change.

    A strand escalating to a human via `gate:` must pass untouched; if a
    future guard ever generalises 'a strand may not speak', this fails.
    """
    brr_dir, inbox, responses, outbox, own_id = _strand_drain_fixture(tmp_path)
    (outbox / "escalate.md").write_text(
        "---\ngate: telegram\ntelegram_chat_id: 999\n---\n"
        "blocked: the migration needs a credential I do not have\n",
        encoding="utf-8")
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda brr, gate: True)
    monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="", event_id=own_id)
    task = Run(id="run-strand", event_id=own_id, body="do the thing",
               source="spawn", meta={"strand": True})

    promoted = daemon._drain_outbox(
        emit, task, responses, own_id, outbox, inbox)

    assert promoted == 1
    done = protocol.list_done(inbox, "telegram")
    assert len(done) == 1
    assert "blocked" in protocol.read_response(responses, done[0]["id"])
    assert daemon._read_outbox_notices(outbox) == []
