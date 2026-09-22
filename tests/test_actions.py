"""The action ledger: the file, and the three seams that write it.

Real files on ``tmp_path``. One test per state a seam writes, plus the file's
own laws — append-only, latest row per id, a missing dir is a no-op.
"""

from __future__ import annotations

import json
from pathlib import Path

from brr import actions, daemon, promises, protocol, worker
from brr.outbox import table
from brr.run import Run

from _helpers import (
    StubWorktreeEnv, make_event, stub_daemon_prompt, succeed_invoke, write_repo_scaffold,
)


def _lines(outbox: Path) -> list[dict]:
    text = (outbox / actions.CONTROL_NAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ── the file ─────────────────────────────────────────────────────────


def test_append_returns_an_act_id_and_writes_one_row(tmp_path: Path):
    act_id = actions.append(
        tmp_path, verb="message", target="thread:t", state="attempted",
        why="reply.md", boundary="b1",
    )

    assert act_id.startswith("act-") and len(act_id.rsplit("-", 1)[1]) == 4
    (row,) = _lines(tmp_path)
    assert row["id"] == act_id
    assert row["state"] == "attempted" and row["verb"] == "message"
    assert row["by"] == "seat" and row["idempotent"] is False
    assert row["boundary"] == "b1" and row["target"] == "thread:t"


def test_transition_appends_a_new_row_and_never_rewrites(tmp_path: Path):
    act_id = actions.append(tmp_path, verb="pr", target="repo#1", state="attempted")
    first = (tmp_path / actions.CONTROL_NAME).read_text(encoding="utf-8")

    assert actions.transition(tmp_path, act_id, "confirmed", evidence="https://x/1")

    text = (tmp_path / actions.CONTROL_NAME).read_text(encoding="utf-8")
    assert text.startswith(first)  # what was written is untouched
    rows = _lines(tmp_path)
    assert [r["state"] for r in rows] == ["attempted", "confirmed"]
    assert {r["id"] for r in rows} == {act_id}
    assert rows[1]["verb"] == "pr" and rows[1]["target"] == "repo#1"  # snapshot


def test_read_returns_the_latest_row_per_id(tmp_path: Path):
    a = actions.append(tmp_path, verb="message", state="attempted")
    b = actions.append(tmp_path, verb="spawn", state="requested")
    actions.transition(tmp_path, a, "failed", evidence="dropped")
    actions.transition(tmp_path, b, "attempted", target="run-1")

    latest = actions.read(tmp_path)

    assert latest[a]["state"] == "failed" and latest[a]["evidence"] == "dropped"
    assert latest[b]["state"] == "attempted" and latest[b]["target"] == "run-1"
    assert len(actions.read_rows(tmp_path)) == 4


def test_open_attempted_is_the_acts_nobody_resolved(tmp_path: Path):
    a = actions.append(tmp_path, verb="message", state="attempted")
    b = actions.append(tmp_path, verb="message", state="attempted")
    actions.transition(tmp_path, b, "confirmed", evidence="e")

    assert [r["id"] for r in actions.open_attempted(tmp_path)] == [a]


def test_missing_dir_and_none_are_no_ops(tmp_path: Path):
    gone = tmp_path / "nope"

    assert actions.append(gone, verb="message") == ""
    assert actions.append(None, verb="message") == ""
    assert actions.transition(gone, "act-1", "confirmed") is False
    assert actions.transition(None, "act-1", "confirmed") is False
    assert actions.read(gone) == {} and actions.read(None) == {}
    assert actions.open_attempted(gone) == [] and not gone.exists()


def test_malformed_lines_and_unknown_states_are_skipped(tmp_path: Path):
    good = actions.append(tmp_path, verb="message", state="attempted")
    with (tmp_path / actions.CONTROL_NAME).open("a", encoding="utf-8") as fh:
        fh.write("not json\n\n" + json.dumps({"id": "act-x", "state": "wat"}) + "\n")

    assert list(actions.read(tmp_path)) == [good]
    assert actions.append(tmp_path, verb="message", state="wat") == ""


def test_a_long_notice_is_clipped_not_dropped(tmp_path: Path):
    act_id = actions.append(tmp_path, verb="message", state="attempted")
    actions.transition(tmp_path, act_id, "failed", evidence="x" * 5000)

    row = actions.read(tmp_path)[act_id]
    assert row["state"] == "failed" and len(row["evidence"]) < 1000


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


def test_spawn_attempted_when_the_daemon_admits_the_child(tmp_path: Path, monkeypatch):
    write_repo_scaffold(tmp_path)
    parent_outbox = tmp_path / ".brr" / "outbox" / "evt-parent"
    parent_outbox.mkdir(parents=True)
    act_id = actions.append(
        parent_outbox, verb="spawn", target="evt-child", state="requested",
    )
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda root, _o=None: daemon.runner.runner_profile("codex", root),
    )
    monkeypatch.setattr(daemon.runner, "fallback_runner_profile", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    stub_daemon_prompt(monkeypatch, lambda task, eid, rp, _root, **kw: "PROMPT")
    monkeypatch.setattr(
        daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=succeed_invoke()),
    )
    event = make_event(
        tmp_path, eid="evt-child", spawn_immediate=True,
        respawned_from_event="evt-parent", spawn_parent_run_id="run-parent",
    )

    prepared = worker.prepare(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    latest = actions.read(parent_outbox)[act_id]
    assert latest["state"] == "attempted"
    assert latest["target"] == prepared.task.id  # the run id, once there is one
    assert latest["evidence"] == "evt-child"
    assert [r["state"] for r in _lines(parent_outbox)] == ["requested", "attempted"]


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


def test_a_promise_is_a_requested_row_and_promises_still_read_the_same(tmp_path: Path):
    promises.append(tmp_path, "pr", count=2, ref="the rollout split", why="split it")

    rows = _lines(tmp_path)
    assert [r["state"] for r in rows] == ["requested", "requested"]
    assert {r["verb"] for r in rows} == {"pr"}
    assert rows[0]["target"] == "the rollout split" and rows[0]["why"] == "split it"
    assert len({r["id"] for r in rows}) == 2
    # what promises returns is unchanged: one blueprint row, count 2
    (record,) = promises.read(tmp_path)
    assert record == {"what": "pr", "count": 2, "ref": "the rollout split", "why": "split it"}


def test_a_released_promise_writes_no_requested_row(tmp_path: Path):
    promises.append(tmp_path, "pr", released=True, why="superseded")

    assert not (tmp_path / actions.CONTROL_NAME).exists()
    assert promises.read(tmp_path)  # the blueprint row still lands


# ── `brnrd act` (build step 4) ───────────────────────────────────────


def _act(monkeypatch, outbox: Path, *argv: str, capsys=None):
    from brr import cli

    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    rc = cli.main(["act", *argv])
    return rc


def test_act_attempt_prints_only_the_id_and_writes_an_attempted_row(tmp_path, monkeypatch, capsys):
    rc = _act(monkeypatch, tmp_path, "attempt", "post https://x.com/a", "--why", "the launch",
              "--idempotent", "--after", "act-1")
    out = capsys.readouterr().out
    assert rc == 0
    (row,) = _lines(tmp_path)
    assert out == row["id"] + "\n"
    assert row["state"] == "attempted" and row["verb"] == "post"
    assert row["target"] == "https://x.com/a" and row["why"] == "the launch"
    assert row["idempotent"] is True and row["after"] == ["act-1"] and row["by"] == "seat"


def test_act_attempt_from_a_strand_is_by_the_strand(tmp_path, monkeypatch, capsys):
    (tmp_path / "portal-state.json").write_text(
        json.dumps({"strand": {"is_strand": True}}), encoding="utf-8")
    monkeypatch.setenv("BRR_RUN_ID", "run-1-abcd")
    _act(monkeypatch, tmp_path, "attempt", "comment repo#3")
    assert _lines(tmp_path)[0]["by"] == "strand:run-1-abcd"


def test_act_confirm_and_fail_move_the_same_id(tmp_path, monkeypatch, capsys):
    _act(monkeypatch, tmp_path, "attempt", "pr repo#1")
    a = capsys.readouterr().out.strip()
    _act(monkeypatch, tmp_path, "attempt", "post url")
    b = capsys.readouterr().out.strip()
    assert _act(monkeypatch, tmp_path, "confirm", a, "--evidence", "https://pr/1") == 0
    assert _act(monkeypatch, tmp_path, "fail", b, "--why", "403") == 0
    latest = actions.read(tmp_path)
    assert latest[a]["state"] == "confirmed" and latest[a]["evidence"] == "https://pr/1"
    assert latest[a]["verb"] == "pr" and latest[b]["state"] == "failed"
    assert latest[b]["why"] == "403"


def test_act_unknown_id_and_no_outbox_exit_one_with_one_line(tmp_path, monkeypatch, capsys):
    capsys.readouterr()
    assert _act(monkeypatch, tmp_path, "confirm", "act-nope", "--evidence", "x") == 1
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1 and "act-nope" in err
    assert not (tmp_path / actions.CONTROL_NAME).exists()

    from brr import cli
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    assert cli.main(["act", "attempt", "post x"]) == 1
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_act_bare_lists_latest_row_per_id_newest_first(tmp_path, monkeypatch, capsys):
    _act(monkeypatch, tmp_path, "attempt", "post one")
    a = capsys.readouterr().out.strip()
    _act(monkeypatch, tmp_path, "attempt", "comment two")
    capsys.readouterr()
    _act(monkeypatch, tmp_path, "confirm", a, "--evidence", "sha1")
    assert _act(monkeypatch, tmp_path) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == ["confirmed · post · one · sha1", "attempted · comment · two · -"] \
        or lines == ["attempted · comment · two · -", "confirmed · post · one · sha1"]
    assert len(lines) == 2


def test_act_has_no_way_to_write_observed():
    from brr import cli

    p = cli.build_parser() if hasattr(cli, "build_parser") else None
    if p is not None:
        try:
            p.parse_args(["act", "observe", "x"])
        except SystemExit:
            return
        raise AssertionError("observe must not parse")


# ── the closeout stamp (build step 5) ────────────────────────────────


def test_closeout_stamps_open_attempted_rows_ambiguous(tmp_path: Path):
    open_id = actions.append(tmp_path, verb="post", target="u", state="attempted")
    done_id = actions.append(tmp_path, verb="pr", target="r", state="attempted")
    actions.transition(tmp_path, done_id, "confirmed", evidence="sha")
    req_id = actions.append(tmp_path, verb="commit", state="requested")

    assert daemon._stamp_unresolved_acts(tmp_path, "run-x") == 1

    latest = actions.read(tmp_path)
    assert latest[open_id]["state"] == "ambiguous"
    assert latest[open_id]["evidence"] == "run ended with the act unresolved"
    assert latest[open_id]["target"] == "u"
    assert latest[done_id]["state"] == "confirmed" and latest[req_id]["state"] == "requested"
    assert daemon._stamp_unresolved_acts(tmp_path, "run-x") == 0  # idempotent


def test_closeout_stamp_never_raises(tmp_path: Path, monkeypatch):
    assert daemon._stamp_unresolved_acts(None) == 0
    assert daemon._stamp_unresolved_acts(tmp_path / "missing") == 0
    monkeypatch.setattr(actions, "open_attempted", lambda *_: (_ for _ in ()).throw(RuntimeError("x")))
    assert daemon._stamp_unresolved_acts(tmp_path) == 0
