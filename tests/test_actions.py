"""The action ledger: the file, and the three seams that write it.

Real files on ``tmp_path``. One test per state a seam writes, plus the file's
own laws — append-only, latest row per id, a missing dir is a no-op.
"""

from __future__ import annotations

import json
from pathlib import Path

from brr import actions, daemon, protocol
from brr.outbox import table
from brr.run import Run



def _lines(outbox: Path) -> list[dict]:
    text = (outbox / actions.CONTROL_NAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ── the file ─────────────────────────────────────────────────────────


# ── the outbox seam: message rows ────────────────────────────────────


def _drain(tmp_path: Path, files: dict[str, str]):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox, "telegram", "original task", status="processing",
        conversation_key="telegram:42:", chat_id="42",
    )
    event_id = path.stem
    outbox = brr_dir / "outbox" / event_id
    outbox.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (outbox / name).write_text(text, encoding="utf-8")
    task = Run(
        id="run-dispatch", event_id=event_id, body="original task",
        source="telegram", conversation_key="telegram:42:",
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task, responses, event_id, outbox, inbox, stats={},
    )
    return promoted, outbox, inbox, event_id


def test_message_attempted_then_confirmed_when_the_reply_is_delivered(tmp_path: Path):
    promoted, outbox, _inbox, event_id = _drain(tmp_path, {"reply.md": "on it\n"})

    assert promoted == 1
    rows = _lines(outbox)
    assert [r["state"] for r in rows] == ["attempted", "confirmed"]
    assert {r["verb"] for r in rows} == {"message"}
    assert rows[1]["evidence"] == f"event:{event_id}"
    assert rows[0]["why"] == "reply.md"


def test_message_failed_when_a_notice_names_the_file(tmp_path: Path):
    promoted, outbox, _inbox, _eid = _drain(
        tmp_path,
        {"reply.md": "---\nevent: evt-1234567890123-nope\n---\nhello\n"},
    )

    assert promoted == 0
    rows = _lines(outbox)
    assert [r["state"] for r in rows] == ["attempted", "failed"]
    assert "not found" in rows[1]["evidence"] or "NOT delivered" in rows[1]["evidence"]
    notices = (outbox / ".notices.jsonl").read_text(encoding="utf-8")
    assert rows[1]["evidence"] in notices


def test_message_gate_and_thread_rows_are_ledgered_too(tmp_path: Path):
    _p, outbox, _i, _e = _drain(
        tmp_path,
        {
            "gate.md": "---\ngate: nosuchgate\n---\nping\n",
            "thread.md": "---\nthread: nowhere:1\n---\nping\n",
        },
    )

    targets = {r["target"] for r in _lines(outbox)}
    assert any(t.startswith("gate:nosuchgate") for t in targets)
    assert any(t.startswith("thread:nowhere:1") for t in targets)
    # every one of them resolved one way or the other — none left dangling
    assert not actions.open_attempted(outbox)


def test_a_handler_that_raises_leaves_the_message_attempted(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "reply.md").write_text("hi\n", encoding="utf-8")
    task = Run(id="run-x", event_id="evt-x", body="b", source="telegram")
    from brr.outbox.shapes import DrainContext, OutboxFile

    f = OutboxFile(
        path=outbox / "reply.md", frontmatter={}, body="hi", run=task,
        ctx=DrainContext(
            emit=None, responses_dir=tmp_path, event_id="evt-x", outbox_dir=outbox,
            inbox_dir=None, repo_root=None, account_context=None, stats=None,
            address_sources=None,
        ),
    )

    def boom(_f):
        raise RuntimeError("the wire dropped")

    row = table.Row("event", None, boom)
    try:
        table._ledgered_message(row, f)
    except RuntimeError:
        pass

    (open_row,) = actions.open_attempted(outbox)
    assert open_row["verb"] == "message" and open_row["target"] == "event:evt-x"


def test_control_verbs_write_no_message_row(tmp_path: Path):
    other = protocol.create_event(
        tmp_path / ".brr" / "inbox", "telegram", "fyi",
        telegram_user_id="42", telegram_chat_id="42",
    )
    _p, outbox, _i, _e = _drain(
        tmp_path, {"note.md": f"---\nnote: {other.stem}\n---\nignored\n"},
    )

    assert not (outbox / actions.CONTROL_NAME).exists()


# ── the spawn seam ───────────────────────────────────────────────────


def test_spawn_requested_at_stage(tmp_path: Path):
    report = tmp_path / "child-report.md"
    promoted, outbox, inbox, event_id = _drain(
        tmp_path,
        {
            "spawn.md": (
                "---\nspawn: true\nshell: claude\nbranch: brr/child\n"
                f"report: {report}\ntitle: a child\n---\nchild task\n"
            ),
        },
    )

    assert promoted == 1
    (row,) = _lines(outbox)
    child = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("respawned_from_event") == event_id
    ][0]
    assert row["verb"] == "spawn" and row["state"] == "requested"
    assert row["target"] == child["id"] and row["why"] == "a child"


def test_spawn_observed_when_the_parent_renders_the_completion(tmp_path: Path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    parent_outbox = brr_dir / "outbox" / "evt-parent"
    parent_outbox.mkdir(parents=True)
    act_id = actions.append(
        parent_outbox, verb="spawn", target="evt-child", state="requested",
    )
    actions.transition(
        parent_outbox, act_id, "attempted", target="run-child", evidence="evt-child",
    )
    done = protocol.create_event(
        inbox, "spawn_completed", "child finished",
        spawned_by_event="evt-child", spawn_parent_run_id="run-parent",
    )

    daemon._pending_events_for_agent(inbox, "evt-parent", observer_run_id="run-parent")

    latest = actions.read(parent_outbox)[act_id]
    assert latest["state"] == "observed" and latest["evidence"] == done.stem
    assert latest["target"] == "run-child"

    # a second render is not a second observation
    daemon._pending_events_for_agent(inbox, "evt-parent", observer_run_id="run-parent")
    assert [r["state"] for r in _lines(parent_outbox)].count("observed") == 1


def test_a_completion_someone_else_rendered_is_not_our_observation(tmp_path: Path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    parent_outbox = brr_dir / "outbox" / "evt-parent"
    parent_outbox.mkdir(parents=True)
    actions.append(parent_outbox, verb="spawn", target="evt-child", state="requested")
    protocol.create_event(
        inbox, "spawn_completed", "child finished",
        spawned_by_event="evt-child", spawn_parent_run_id="run-parent",
    )

    daemon._pending_events_for_agent(inbox, "evt-parent", observer_run_id="run-other")

    assert [r["state"] for r in _lines(parent_outbox)] == ["requested"]


# ── the promises seam ────────────────────────────────────────────────


# ── `brnrd act` (build step 4) ───────────────────────────────────────


# ── the closeout stamp (build step 5) ────────────────────────────────


