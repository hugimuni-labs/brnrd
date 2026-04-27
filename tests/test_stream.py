"""Tests for stream module — manifest, resolution, append-only records."""

from pathlib import Path

from brr import stream


# ── Manifest roundtrip ──────────────────────────────────────────────


def test_manifest_roundtrip(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = stream.StreamManifest(
        id="stream-rt-1",
        title="Refactor auth flow",
        status="active",
        intent="Make the login path testable.",
        summary="found the offending coupling",
        open_questions="should we keep cookie-based fallback?",
        gate_context={"source": "telegram", "telegram_chat_id": 12345},
        reply_route={
            "preferred": "input_gate",
            "selected": "input_gate",
            "allowed": ["input_gate", "git_pr"],
        },
    )
    stream.save_manifest(brr_dir, manifest)
    loaded = stream.load_manifest(brr_dir, "stream-rt-1")
    assert loaded is not None
    assert loaded.id == "stream-rt-1"
    assert loaded.title == "Refactor auth flow"
    assert loaded.intent == "Make the login path testable."
    assert loaded.summary == "found the offending coupling"
    assert loaded.open_questions == "should we keep cookie-based fallback?"
    assert loaded.gate_context["telegram_chat_id"] == 12345
    assert loaded.reply_route["allowed"] == ["input_gate", "git_pr"]
    assert loaded.created  # set on first save
    assert loaded.updated


def test_manifest_missing_returns_none(tmp_path):
    assert stream.load_manifest(tmp_path / ".brr", "stream-nope") is None


# ── Gate thread keys ────────────────────────────────────────────────


def test_gate_thread_key_telegram():
    key = stream.gate_thread_key({
        "source": "telegram",
        "telegram_chat_id": 99,
        "telegram_topic_id": 7,
    })
    assert key == "telegram:99:7"


def test_gate_thread_key_telegram_no_topic():
    key = stream.gate_thread_key({
        "source": "telegram",
        "telegram_chat_id": 99,
        "telegram_topic_id": "",
    })
    assert key == "telegram:99:"


def test_gate_thread_key_slack():
    key = stream.gate_thread_key({
        "source": "slack",
        "slack_channel": "C1",
        "slack_thread_ts": "1.2",
    })
    assert key == "slack:C1:1.2"


def test_gate_thread_key_git():
    key = stream.gate_thread_key({"source": "git", "git_file": "tasks/foo.md"})
    assert key == "git:tasks/foo.md"


def test_gate_thread_key_no_anchor():
    assert stream.gate_thread_key({"source": "telegram"}) is None


# ── Resolution ──────────────────────────────────────────────────────


def test_resolve_creates_new_stream(tmp_path):
    brr_dir = tmp_path / ".brr"
    event = {
        "id": "evt-1",
        "source": "telegram",
        "telegram_chat_id": 42,
        "telegram_topic_id": 1,
        "body": "first message in this thread",
    }
    res = stream.resolve_for_event(brr_dir, event)
    assert res.created is True
    assert res.reason == "fallback"
    assert res.thread_key == "telegram:42:1"
    manifest = stream.load_manifest(brr_dir, res.stream_id)
    assert manifest is not None
    assert manifest.gate_context["telegram_chat_id"] == 42
    assert manifest.title.startswith("first message")


def test_resolve_followup_in_same_thread_reuses_stream(tmp_path):
    brr_dir = tmp_path / ".brr"
    base = {
        "id": "evt-1",
        "source": "telegram",
        "telegram_chat_id": 42,
        "telegram_topic_id": 1,
        "body": "kick off",
    }
    res1 = stream.resolve_for_event(brr_dir, base)
    res2 = stream.resolve_for_event(brr_dir, {**base, "id": "evt-2", "body": "follow-up"})
    assert res1.stream_id == res2.stream_id
    assert res2.created is False
    assert res2.reason == "thread"


def test_resolve_explicit_stream_id_wins(tmp_path):
    brr_dir = tmp_path / ".brr"
    initial = {
        "id": "evt-1",
        "source": "telegram",
        "telegram_chat_id": 42,
        "telegram_topic_id": 1,
        "body": "go",
    }
    res = stream.resolve_for_event(brr_dir, initial)

    other_thread = {
        "id": "evt-2",
        "source": "telegram",
        "telegram_chat_id": 99,
        "telegram_topic_id": 2,
        "body": "ride along",
        "stream_id": res.stream_id,
    }
    explicit = stream.resolve_for_event(brr_dir, other_thread)
    assert explicit.stream_id == res.stream_id
    assert explicit.reason == "explicit"


def test_resolve_explicit_unknown_creates(tmp_path):
    brr_dir = tmp_path / ".brr"
    res = stream.resolve_for_event(brr_dir, {
        "id": "evt-1",
        "source": "telegram",
        "telegram_chat_id": 1,
        "body": "hi",
        "stream_id": "stream-explicit-zzz",
    })
    assert res.stream_id == "stream-explicit-zzz"
    assert res.created is True
    assert res.reason == "explicit"
    assert (stream.stream_dir(brr_dir, "stream-explicit-zzz")).exists()


def test_resolve_via_related_task(tmp_path):
    brr_dir = tmp_path / ".brr"
    res = stream.resolve_for_event(brr_dir, {
        "id": "evt-1", "source": "git", "git_file": "tasks/a.md", "body": "x",
    })
    follow = stream.resolve_for_event(
        brr_dir,
        {"id": "evt-2", "source": "git", "body": "next"},
        related_task_stream=res.stream_id,
    )
    assert follow.stream_id == res.stream_id
    assert follow.reason == "task"


# ── Append-only records ─────────────────────────────────────────────


def test_append_event_task_artifact(tmp_path):
    brr_dir = tmp_path / ".brr"
    res = stream.resolve_for_event(brr_dir, {
        "id": "evt-1", "source": "telegram",
        "telegram_chat_id": 1, "body": "ping",
    })
    sid = res.stream_id
    stream.append_event(brr_dir, sid, {"id": "evt-2", "source": "telegram", "body": "pong"})
    stream.append_task(
        brr_dir, sid, task_id="task-1", event_id="evt-1",
        branch="auto", env="worktree", status="done",
        base_branch="main", branch_name="brr/task-1",
    )
    stream.append_artifact(
        brr_dir, sid, kind="response", path="/tmp/out.md",
        task_id="task-1", label="response:evt-1",
    )

    events = stream.read_events(brr_dir, sid)
    tasks = stream.read_tasks(brr_dir, sid)
    artifacts = stream.read_artifacts(brr_dir, sid)
    assert len(events) == 1
    assert events[0]["event_id"] == "evt-2"
    assert tasks[0]["task_id"] == "task-1"
    assert tasks[0]["branch_name"] == "brr/task-1"
    assert artifacts[0]["kind"] == "response"


# ── Reply route normalization ───────────────────────────────────────


def test_normalize_reply_route_defaults_input_gate():
    out = stream.normalize_reply_route(None)
    assert out["preferred"] == "input_gate"
    assert out["selected"] == "input_gate"
    assert "input_gate" in out["allowed"]


def test_normalize_reply_route_accepts_allowed_request():
    base = {"preferred": "input_gate", "allowed": ["input_gate", "git_pr"]}
    out = stream.normalize_reply_route({"preferred": "git_pr"}, stream_route=base)
    assert out["selected"] == "git_pr"


def test_normalize_reply_route_rejects_disallowed_request():
    base = {"preferred": "input_gate", "allowed": ["input_gate"]}
    out = stream.normalize_reply_route({"preferred": "git_pr"}, stream_route=base)
    assert out["selected"] == "input_gate"
