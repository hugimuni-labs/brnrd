"""Tests for the conversation log: keys, append/read, listing."""

import json

import pytest

from brr import conversations


# ── conversation_key_for_event ──────────────────────────────────────


def test_conversation_key_telegram_with_topic():
    event = {
        "source": "telegram",
        "telegram_chat_id": 12345,
        "telegram_topic_id": 7,
    }
    assert conversations.conversation_key_for_event(event) == "telegram:12345:7"


def test_conversation_key_telegram_without_topic():
    event = {"source": "telegram", "telegram_chat_id": 99}
    assert conversations.conversation_key_for_event(event) == "telegram:99:"


def test_conversation_key_telegram_missing_chat_returns_none():
    assert conversations.conversation_key_for_event({"source": "telegram"}) is None


def test_conversation_key_slack():
    event = {
        "source": "slack",
        "slack_channel": "C123",
        "slack_thread_ts": "1700000000.123",
    }
    assert conversations.conversation_key_for_event(event) == "slack:C123:1700000000.123"


def test_conversation_key_slack_falls_back_to_ts():
    event = {"source": "slack", "slack_channel": "C123", "slack_ts": "9.0"}
    assert conversations.conversation_key_for_event(event) == "slack:C123:9.0"


def test_conversation_key_git():
    event = {"source": "git", "git_file": "events/foo.md"}
    assert conversations.conversation_key_for_event(event) == "git:events/foo.md"


def test_conversation_key_explicit_wins():
    event = {
        "source": "telegram",
        "telegram_chat_id": 1,
        "conversation_key": "explicit:override",
    }
    assert conversations.conversation_key_for_event(event) == "explicit:override"


def test_conversation_key_unknown_source_uses_default_key():
    event = {"source": "cli"}
    assert conversations.conversation_key_for_event(event) == "cli:default"


def test_conversation_key_no_source_returns_none():
    assert conversations.conversation_key_for_event({}) is None


# ── filename encoding ───────────────────────────────────────────────


def test_safe_filename_encodes_colons():
    assert conversations.safe_filename("telegram:42:7") == "telegram__42__7.ndjson"


def test_safe_filename_strips_unsafe_chars():
    assert conversations.safe_filename("git:path with space") == "git__path_with_space.ndjson"


def test_key_from_filename_inverts_safe_filename():
    encoded = conversations.safe_filename("telegram:42:")
    assert conversations.key_from_filename(encoded) == "telegram:42:"


# ── append/read ──────────────────────────────────────────────────────


def test_append_record_creates_path_and_stamps_ts(tmp_path):
    conversations.append_record(tmp_path, "k:1", {"kind": "test"})
    path = conversations.conversation_path(tmp_path, "k:1")
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["kind"] == "test"
    assert "ts" in record


def test_append_record_appends_in_order(tmp_path):
    for i in range(3):
        conversations.append_record(tmp_path, "k", {"kind": "n", "i": i})
    records = conversations.read_records(tmp_path, "k")
    assert [r["i"] for r in records] == [0, 1, 2]


def test_read_records_missing_returns_empty(tmp_path):
    assert conversations.read_records(tmp_path, "no") == []


def test_read_recent_tail(tmp_path):
    for i in range(15):
        conversations.append_record(tmp_path, "k", {"kind": "n", "i": i})
    recent = conversations.read_recent(tmp_path, "k", limit=5)
    assert [r["i"] for r in recent] == [10, 11, 12, 13, 14]


def test_read_recent_limit_zero_returns_all(tmp_path):
    for i in range(3):
        conversations.append_record(tmp_path, "k", {"kind": "n", "i": i})
    assert len(conversations.read_recent(tmp_path, "k", limit=0)) == 3


# ── specialised appenders ────────────────────────────────────────────


def test_append_event_records_summary(tmp_path):
    event = {
        "id": "evt-1",
        "source": "telegram",
        "body": "first line\nsecond line",
    }
    conversations.append_event(tmp_path, "k", event)
    records = conversations.read_records(tmp_path, "k")
    assert records[-1] == pytest.approx({
        "kind": "event",
        "event_id": "evt-1",
        "source": "telegram",
        "summary": "first line",
        "ts": records[-1]["ts"],
    }, rel=0)


def test_append_task_includes_env_and_branch_name(tmp_path):
    conversations.append_task(
        tmp_path, "k",
        task_id="t-1", event_id="evt-1",
        env="worktree", status="pending",
        seed_ref="main", auto_land_branch="main",
        branch_source="event:target_branch",
        branch_name="brr/t-1",
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["task_id"] == "t-1"
    assert record["env"] == "worktree"
    assert record["seed_ref"] == "main"
    assert record["auto_land_branch"] == "main"
    assert record["branch_source"] == "event:target_branch"
    assert record["branch_name"] == "brr/t-1"
    assert "branch" not in record
    assert "base_branch" not in record


def test_append_artifact_records_kind_and_path(tmp_path):
    conversations.append_artifact(
        tmp_path, "k",
        kind="response", path="/abs/x.md",
        task_id="t-1", label="response:evt-1",
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["kind"] == "artifact"
    assert record["artifact_kind"] == "response"
    assert record["task_id"] == "t-1"
    assert record["label"] == "response:evt-1"


def test_append_update_records_type_and_payload(tmp_path):
    conversations.append_update(
        tmp_path, "k",
        type="task_created",
        payload={"task_id": "t-1", "branch": "auto"},
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["kind"] == "update"
    assert record["type"] == "task_created"
    assert record["task_id"] == "t-1"
    assert record["branch"] == "auto"


# ── listing ──────────────────────────────────────────────────────────


def test_list_conversations_empty(tmp_path):
    assert conversations.list_conversations(tmp_path) == []


def test_list_conversations_returns_decoded_keys(tmp_path):
    conversations.append_record(tmp_path, "telegram:1:", {"kind": "n"})
    conversations.append_record(tmp_path, "slack:C:1.0", {"kind": "n"})
    keys = conversations.list_conversations(tmp_path)
    assert "telegram:1:" in keys
    assert "slack:C:1.0" in keys


# ── records_for_task ────────────────────────────────────────────────


def test_records_for_task_filters_by_task_id(tmp_path):
    conversations.append_record(tmp_path, "k", {"kind": "task", "task_id": "t-1"})
    conversations.append_record(tmp_path, "k", {"kind": "update", "task_id": "t-2"})
    conversations.append_record(tmp_path, "k", {"kind": "update", "task_id": "t-1", "type": "done"})
    matches = conversations.records_for_task(tmp_path, "k", "t-1")
    assert len(matches) == 2
    assert all(r["task_id"] == "t-1" for r in matches)
