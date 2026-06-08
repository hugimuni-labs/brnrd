"""Tests for the agent outbox + daemon mid-flight drain (slice 4b).

The producer half of the multi-response protocol: the resident drops
interim replies in ``.brr/outbox/<eid>/``, the daemon drains them to the
response partials queue, and the live card / conversation log reflect
the check-in. See ``kb/design-multi-response.md``.
"""

from __future__ import annotations

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

    def test_missing_outbox_is_noop(self, tmp_path):
        brr_dir = tmp_path / ".brr"
        emit = daemon._WorkerEmit(
            brr_dir=brr_dir, conversation_key="", event_id="evt-1")
        task = types.SimpleNamespace(id="task-1")
        responses = brr_dir / "responses"
        assert daemon._drain_outbox(emit, task, responses, "evt-1", None) == 0
        assert daemon._drain_outbox(
            emit, task, responses, "evt-1", brr_dir / "outbox" / "nope") == 0


def test_remove_outbox_is_best_effort(tmp_path):
    outbox = tmp_path / ".brr" / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    (outbox / "leftover.tmp").write_text("x")
    daemon._remove_outbox(outbox)
    assert not outbox.exists()
    # tolerates a missing dir / None
    daemon._remove_outbox(outbox)
    daemon._remove_outbox(None)


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
