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


def test_conversation_key_github_issue():
    event = {
        "source": "github",
        "github_repo": "acme/widget",
        "github_issue_number": 42,
    }
    assert conversations.conversation_key_for_event(event) == "github:acme/widget:42"


def test_conversation_key_github_issue_number_as_string():
    event = {
        "source": "github",
        "github_repo": "acme/widget",
        "github_issue_number": "7",
    }
    assert conversations.conversation_key_for_event(event) == "github:acme/widget:7"


def test_conversation_key_github_missing_anchor_returns_none():
    assert conversations.conversation_key_for_event({"source": "github"}) is None
    assert conversations.conversation_key_for_event(
        {"source": "github", "github_repo": "acme/r"},
    ) is None
    assert conversations.conversation_key_for_event(
        {"source": "github", "github_issue_number": 1},
    ) is None


def test_conversation_key_cloud_threads_by_origin_chat():
    event = {
        "source": "cloud",
        "cloud_platform": "telegram",
        "cloud_chat_id": 555,
        "cloud_topic_id": 9,
    }
    assert conversations.conversation_key_for_event(event) == "cloud:telegram:555:9"


def test_conversation_key_cloud_without_routing_falls_back_to_default():
    # A drained event with no origin routing still gets a stable key.
    assert (
        conversations.conversation_key_for_event({"source": "cloud"}) == "cloud:default"
    )


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


# ── directory name encoding ─────────────────────────────────────────


def test_safe_dir_name_encodes_colons():
    assert conversations.safe_dir_name("telegram:42:7") == "telegram__42__7"


def test_safe_dir_name_strips_unsafe_chars():
    assert conversations.safe_dir_name("github:o/r:1 with space") == (
        "github__o_r__1_with_space"
    )


def test_key_from_dir_name_inverts_safe_dir_name():
    encoded = conversations.safe_dir_name("telegram:42:")
    assert conversations.key_from_dir_name(encoded) == "telegram:42:"


def test_conversation_path_is_directory(tmp_path):
    path = conversations.conversation_path(tmp_path, "telegram:42:7")
    assert path == tmp_path / "conversations" / "telegram__42__7"


def test_event_log_path_routes_per_event(tmp_path):
    path = conversations.event_log_path(tmp_path, "telegram:42:7", "evt-1")
    assert path == (
        tmp_path / "conversations" / "telegram__42__7" / "evt-1.jsonl"
    )


# ── append/read ──────────────────────────────────────────────────────


def test_append_record_creates_event_file_and_stamps_ts(tmp_path):
    conversations.append_record(
        tmp_path, "k:1", {"kind": "test"}, event_id="evt-a",
    )
    file_path = conversations.event_log_path(tmp_path, "k:1", "evt-a")
    assert file_path.exists()
    record = json.loads(file_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["kind"] == "test"
    assert "ts" in record


def test_append_record_appends_in_order_within_event(tmp_path):
    for i in range(3):
        conversations.append_record(
            tmp_path, "k", {"kind": "n", "i": i}, event_id="evt-a",
        )
    records = conversations.read_records(tmp_path, "k")
    assert [r["i"] for r in records] == [0, 1, 2]


def test_append_record_merges_across_event_files_by_ts(tmp_path):
    # Two pipelines (event A and event B) write into the same
    # conversation; read_records merges them sorted by ts.
    conversations.append_record(
        tmp_path, "k", {"kind": "n", "i": 0}, event_id="evt-a",
    )
    conversations.append_record(
        tmp_path, "k", {"kind": "n", "i": 1}, event_id="evt-b",
    )
    conversations.append_record(
        tmp_path, "k", {"kind": "n", "i": 2}, event_id="evt-a",
    )
    records = conversations.read_records(tmp_path, "k")
    assert [r["i"] for r in records] == [0, 1, 2]


def test_read_event_records_returns_only_one_file(tmp_path):
    conversations.append_record(
        tmp_path, "k", {"kind": "n", "i": 0}, event_id="evt-a",
    )
    conversations.append_record(
        tmp_path, "k", {"kind": "n", "i": 1}, event_id="evt-b",
    )
    a_only = conversations.read_event_records(tmp_path, "k", "evt-a")
    assert [r["i"] for r in a_only] == [0]


def test_append_record_without_event_id_falls_back_to_orphan(tmp_path):
    conversations.append_record(tmp_path, "k", {"kind": "stray", "i": 0})
    orphan_path = conversations.event_log_path(tmp_path, "k", "")
    assert orphan_path.exists()
    assert orphan_path.name == "_orphan.jsonl"


def test_read_records_missing_returns_empty(tmp_path):
    assert conversations.read_records(tmp_path, "no") == []


def test_read_recent_tail(tmp_path):
    for i in range(15):
        conversations.append_record(
            tmp_path, "k", {"kind": "n", "i": i}, event_id="evt-a",
        )
    recent = conversations.read_recent(tmp_path, "k", limit=5)
    assert [r["i"] for r in recent] == [10, 11, 12, 13, 14]


def test_read_recent_matches_read_records_tail_multi_event(tmp_path):
    key = "telegram:1:"
    for i in range(5):
        conversations.append_record(
            tmp_path, key, {"kind": "n", "i": i}, event_id="evt-a",
        )
    for i in range(5, 12):
        conversations.append_record(
            tmp_path, key, {"kind": "n", "i": i}, event_id="evt-b",
        )
    full = conversations.read_records(tmp_path, key)
    for lim in (1, 3, 7, 12, 20):
        want = full[-lim:] if lim <= len(full) else full
        got = conversations.read_recent(tmp_path, key, limit=lim)
        assert got == want


def test_read_recent_large_file_scans_from_tail(tmp_path):
    """Many lines in one jsonl: read_recent must match full merge tail."""
    key = "slack:C1:"
    path = conversations.event_log_path(tmp_path, key, "evt-big")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(400):
        ts = f"2026-01-01T00:00:00.{i:06d}Z"
        rows.append(json.dumps({"ts": ts, "i": i}, sort_keys=True))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    full = conversations.read_records(tmp_path, key)
    assert len(full) == 400
    got = conversations.read_recent(tmp_path, key, limit=15)
    assert got == full[-15:]


def test_read_recent_limit_zero_returns_all(tmp_path):
    for i in range(3):
        conversations.append_record(
            tmp_path, "k", {"kind": "n", "i": i}, event_id="evt-a",
        )
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
        seed_ref="main", target_branch="main",
        branch_source="event:target_branch",
        branch_name="brr/t-1",
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["task_id"] == "t-1"
    assert record["env"] == "worktree"
    assert record["seed_ref"] == "main"
    assert record["target_branch"] == "main"
    assert record["branch_source"] == "event:target_branch"
    assert record["branch_name"] == "brr/t-1"
    assert "branch" not in record
    assert "base_branch" not in record


def test_append_artifact_records_kind_and_path(tmp_path):
    conversations.append_artifact(
        tmp_path, "k",
        kind="response", path="/abs/x.md",
        task_id="t-1", event_id="evt-1",
        label="response:evt-1",
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["kind"] == "artifact"
    assert record["artifact_kind"] == "response"
    assert record["task_id"] == "t-1"
    assert record["event_id"] == "evt-1"
    assert record["label"] == "response:evt-1"


def test_append_update_records_type_and_payload(tmp_path):
    conversations.append_update(
        tmp_path, "k",
        type="task_created",
        payload={"task_id": "t-1", "branch": "auto"},
        event_id="evt-1",
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["kind"] == "update"
    assert record["type"] == "task_created"
    assert record["task_id"] == "t-1"
    assert record["branch"] == "auto"
    assert record["event_id"] == "evt-1"


# ── listing ──────────────────────────────────────────────────────────


def test_list_conversations_empty(tmp_path):
    assert conversations.list_conversations(tmp_path) == []


def test_list_conversations_returns_decoded_keys(tmp_path):
    conversations.append_record(
        tmp_path, "telegram:1:", {"kind": "n"}, event_id="evt-a",
    )
    conversations.append_record(
        tmp_path, "slack:C:1.0", {"kind": "n"}, event_id="evt-b",
    )
    keys = conversations.list_conversations(tmp_path)
    assert "telegram:1:" in keys
    assert "slack:C:1.0" in keys


# ── records_for_task ────────────────────────────────────────────────


def test_records_for_task_filters_by_task_id(tmp_path):
    conversations.append_record(
        tmp_path, "k", {"kind": "task", "task_id": "t-1"}, event_id="evt-a",
    )
    conversations.append_record(
        tmp_path, "k", {"kind": "update", "task_id": "t-2"}, event_id="evt-b",
    )
    conversations.append_record(
        tmp_path, "k", {"kind": "update", "task_id": "t-1", "type": "done"},
        event_id="evt-a",
    )
    matches = conversations.records_for_task(tmp_path, "k", "t-1")
    assert len(matches) == 2
    assert all(r["task_id"] == "t-1" for r in matches)


# ── Concurrency: per-event-pipeline writes don't share a file ────────


def test_concurrent_appends_for_different_events_dont_lose_records(tmp_path):
    """Two pipelines writing into the same conversation must each see
    every record they emitted — the per-event-pipeline file layout
    means concurrent writers never share a file.
    """
    import threading

    barrier = threading.Barrier(2)

    def writer(event_id: str, count: int) -> None:
        barrier.wait()
        for i in range(count):
            conversations.append_record(
                tmp_path, "k", {"kind": "n", "i": i, "src": event_id},
                event_id=event_id,
            )

    t1 = threading.Thread(target=writer, args=("evt-a", 50))
    t2 = threading.Thread(target=writer, args=("evt-b", 50))
    t1.start(); t2.start(); t1.join(); t2.join()

    a_records = conversations.read_event_records(tmp_path, "k", "evt-a")
    b_records = conversations.read_event_records(tmp_path, "k", "evt-b")
    assert len(a_records) == 50
    assert len(b_records) == 50
    # Each pipeline's file is single-writer, so iteration order is the
    # append order.
    assert [r["i"] for r in a_records] == list(range(50))
    assert [r["i"] for r in b_records] == list(range(50))
    # Merged read across both files returns every record exactly once.
    all_records = conversations.read_records(tmp_path, "k")
    assert len(all_records) == 100
