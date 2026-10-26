"""Tests for the conversation log: keys, append/read, listing."""

import json

from brr import conversations


# ── conversation_key_for_event ──────────────────────────────────────


# ── find_event_by_origin_message: the windowed exact-duplicate scan ──────


def _append_tg_event(tmp_path, eid, message_id):
    event = {
        "id": eid,
        "source": "telegram",
        "body": f"msg {message_id}",
        "telegram_chat_id": 10,
        "telegram_message_id": message_id,
    }
    key = conversations.conversation_key_for_event(event)
    conversations.append_event(tmp_path, key, event)
    return conversations.origin_message_key_for_event(event)


def _record_epoch(tmp_path, origin_key):
    for key in conversations.list_conversations(tmp_path):
        for rec in conversations.read_records(tmp_path, key):
            if rec.get("origin_message_key") == origin_key:
                return conversations._ts_epoch(rec)
    raise AssertionError("no record for origin key")


def test_find_event_by_origin_message_windows_out_a_stale_collision(tmp_path):
    """A prior record older than the window is a coincidental id collision,
    not a re-delivery — it must not match and squash the new message."""
    origin = _append_tg_event(tmp_path, "evt-monthold", 99)
    rec_epoch = _record_epoch(tmp_path, origin)
    # "Now" is two hours after the prior record; window is one hour.
    hit = conversations.find_event_by_origin_message(
        tmp_path, origin, max_age_seconds=3600, now_epoch=rec_epoch + 7200,
    )
    assert hit is None


def test_find_event_by_origin_message_matches_a_recent_redelivery(tmp_path):
    """A genuine re-delivery arrives within the window and still dedups."""
    origin = _append_tg_event(tmp_path, "evt-first", 99)
    rec_epoch = _record_epoch(tmp_path, origin)
    hit = conversations.find_event_by_origin_message(
        tmp_path, origin, max_age_seconds=3600, now_epoch=rec_epoch + 30,
    )
    assert hit is not None
    assert hit["event_id"] == "evt-first"


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


# ── append/read ──────────────────────────────────────────────────────


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


def test_read_records_missing_returns_empty(tmp_path):
    assert conversations.read_records(tmp_path, "no") == []


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


# ── unread_pile (#1914) ──────────────────────────────────────────────


# ── Cross-thread weave dedup (#338) ──────────────────────────────────


def test_snapshot_collapses_exact_schedule_repeats_but_keeps_newest_and_history(
    tmp_path,
):
    key = "schedule:director-tick"
    for i in range(5):
        conversations.append_event(
            tmp_path,
            key,
            {
                "id": f"evt-tick-{i}",
                "source": "schedule",
                "schedule_id": "director-tick",
                "body": "run the director tick",
            },
        )
    conversations.append_event(
        tmp_path,
        key,
        {
            "id": "evt-tick-edited",
            "source": "schedule",
            "schedule_id": "director-tick",
            "body": "run the director tick with the edited contract",
        },
    )
    conversations.append_event(
        tmp_path,
        key,
        {"id": "evt-human", "source": "telegram", "body": "run the director tick"},
    )
    conversations.append_event(
        tmp_path,
        key,
        {
            "id": "evt-current",
            "source": "schedule",
            "schedule_id": "director-tick",
            "body": "run the director tick",
        },
    )

    snapshot = conversations.build_communication_snapshot(
        tmp_path,
        key,
        event_id="evt-current",
        recent_limit=4,
    )
    selected = snapshot["recent_turns"]
    bodies = [record["body"] for record in selected]

    assert bodies.count("run the director tick") == 2
    assert bodies[-3:] == [
        "run the director tick",
        "run the director tick with the edited contract",
        "run the director tick",
    ]
    assert selected[0]["schedule_repeat_summary"] is True
    assert selected[0]["schedule_repeat_count"] == 4
    assert selected[1]["event_id"] == "evt-tick-4"
    assert selected[-1]["event_id"] == "evt-human"
    assert all(record.get("event_id") != "evt-current" for record in selected)

    records = conversations.read_records(tmp_path, key)
    schedule_record = next(
        record for record in records if record.get("event_id") == "evt-tick-0"
    )
    assert schedule_record["schedule_id"] == "director-tick"


def test_write_grouped_history_files_writes_small_thread_jsonl_untruncated(tmp_path):
    event = {
        "id": "evt-1",
        "source": "github",
        "body": "review comment",
        "github_repo": "acme/widget",
        "github_issue_number": 9,
        "github_author": "octo",
    }
    key = conversations.conversation_key_for_event(event)
    conversations.append_event(tmp_path, key, event)
    conversations.append_run(
        tmp_path, key,
        run_id="run-1", event_id="evt-1",
        env="docker", status="running",
    )

    groups = conversations.write_grouped_history_files(
        tmp_path, tmp_path / "runs" / "run-1" / "history",
        key, "github:login:octo",
    )

    assert len(groups) == 1
    group = groups[0]
    assert group["kind"] == "forge_thread"
    assert group["conversation_key"] == key
    assert group["record_count"] == 2
    assert group["total_record_count"] == 2
    assert "truncated" not in group
    assert group["store_path"] == str(conversations.conversation_path(tmp_path, key))
    records = [
        json.loads(line)
        for line in (tmp_path / group["path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert [r["kind"] for r in records] == ["event", "run"]
    assert records[0]["conversation_key"] == key
    manifest = tmp_path / "runs" / "run-1" / "history" / "manifest.json"
    assert manifest.exists()


def test_write_grouped_history_files_bounds_long_thread_to_tail(tmp_path):
    """#500: a thread with far more records than the cap only copies the
    tail into the per-run history file — the full-copy-per-wake behavior
    that grew live `.brr/runs/` to 1.7 GB across 847 run dirs (base
    conversation store on that deployment: 15 MB)."""
    key = "telegram:1:"
    over_by = 37
    total_records = conversations.HISTORY_GROUP_TAIL_LIMIT + over_by
    for i in range(total_records):
        conversations.append_event(
            tmp_path, key,
            {"id": f"evt-{i}", "source": "telegram", "body": f"msg {i}"},
        )

    groups = conversations.write_grouped_history_files(
        tmp_path, tmp_path / "runs" / "run-1" / "history", key,
    )

    assert len(groups) == 1
    group = groups[0]
    assert group["record_count"] == conversations.HISTORY_GROUP_TAIL_LIMIT
    assert group["total_record_count"] == total_records
    assert group["truncated"] is True
    assert group["store_path"] == str(conversations.conversation_path(tmp_path, key))

    records = [
        json.loads(line)
        for line in (tmp_path / group["path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == conversations.HISTORY_GROUP_TAIL_LIMIT
    # Oldest-first order preserved; the tail keeps the *latest* records.
    assert records[0]["event_id"] == f"evt-{over_by}"
    assert records[-1]["event_id"] == f"evt-{total_records - 1}"

    # The full history is never bounded or copied away: it's still all
    # there in the base conversation store this group points at.
    assert len(conversations.read_records(tmp_path, key)) == total_records


# ── specialised appenders ────────────────────────────────────────────


def test_append_event_records_full_body_and_summary(tmp_path):
    event = {
        "id": "evt-1",
        "source": "telegram",
        "body": "  first line\nsecond line\n",
    }
    conversations.append_event(tmp_path, "k", event)
    records = conversations.read_records(tmp_path, "k")
    assert records[-1]["kind"] == "event"
    assert records[-1]["event_id"] == "evt-1"
    assert records[-1]["source"] == "telegram"
    assert records[-1]["body"] == "  first line\nsecond line\n"
    assert records[-1]["summary"] == "first line second line"
    assert "ts" in records[-1]


def test_append_event_records_photo_attachment_fact(tmp_path):
    """#943 — a captionless Telegram photo must not record as pure silence.

    The event shape here is production-shaped, not invented: it is exactly
    what ``test_telegram_gate.test_loop_accepts_photo_with_no_caption``
    asserts ``protocol.list_pending`` hands back after the local gate
    downloads a bare photo — ``body: ""``, ``attachments: "photo.jpg"``
    (the single comma-joined frontmatter field ``protocol.create_event``
    writes).
    """
    event = {
        "id": "evt-1",
        "source": "telegram",
        "body": "",
        "attachments": "photo.jpg",
    }
    conversations.append_event(tmp_path, "k", event)
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["body"] == ""
    assert record["attachments"] == [{"kind": "photo", "filename": "photo.jpg"}]


def test_append_event_records_multiple_attachments_with_kind(tmp_path):
    """Multiple attachments on one event keep the index-prefixed naming
    ``protocol.create_event`` writes when more than one file lands
    (``NN-<name>``) and classify each independently: the photo convention
    (``photo.jpg``, possibly index-prefixed) reads as ``photo``; anything
    else (a Telegram image document, a GitHub inline image) reads as
    ``document``.
    """
    event = {
        "id": "evt-2",
        "source": "telegram",
        "body": "check these out",
        "attachments": "00-photo.jpg,01-report.png",
    }
    conversations.append_event(tmp_path, "k", event)
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["attachments"] == [
        {"kind": "photo", "filename": "00-photo.jpg"},
        {"kind": "document", "filename": "01-report.png"},
    ]


def test_append_event_no_attachments_key_when_event_has_none(tmp_path):
    """A plain text event (the overwhelming common case) gets no
    ``attachments`` key at all — not an empty list — so existing readers
    that only check truthiness see no behavioural change.
    """
    event = {"id": "evt-3", "source": "telegram", "body": "hello"}
    conversations.append_event(tmp_path, "k", event)
    record = conversations.read_records(tmp_path, "k")[-1]
    assert "attachments" not in record


def test_append_run_includes_env_and_branch_name(tmp_path):
    conversations.append_run(
        tmp_path, "k",
        run_id="t-1", event_id="evt-1",
        env="worktree", status="pending",
        seed_ref="main", target_branch="main",
        branch_source="event:target_branch",
        branch_name="brr/t-1",
        repo_label="Gurio/brr",
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["run_id"] == "t-1"
    assert record["env"] == "worktree"
    assert record["repo_label"] == "Gurio/brr"
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
        run_id="t-1", event_id="evt-1",
        label="response:evt-1",
        body="agent reply\nsecond line",
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["kind"] == "artifact"
    assert record["artifact_kind"] == "response"
    assert record["run_id"] == "t-1"
    assert record["event_id"] == "evt-1"
    assert record["label"] == "response:evt-1"
    assert record["body"] == "agent reply\nsecond line"
    assert record["summary"] == "agent reply second line"


def test_append_update_records_type_and_payload(tmp_path):
    conversations.append_update(
        tmp_path, "k",
        type="run_created",
        payload={"run_id": "t-1", "branch": "auto"},
        event_id="evt-1",
    )
    record = conversations.read_records(tmp_path, "k")[-1]
    assert record["kind"] == "update"
    assert record["type"] == "run_created"
    assert record["run_id"] == "t-1"
    assert record["branch"] == "auto"
    assert record["event_id"] == "evt-1"


def test_append_update_skips_heartbeat_memory(tmp_path):
    conversations.append_update(
        tmp_path, "k",
        type="heartbeat",
        payload={"run_id": "t-1", "elapsed_seconds": 30},
        event_id="evt-1",
    )

    assert conversations.read_records(tmp_path, "k") == []


# ── listing ──────────────────────────────────────────────────────────


# ── records_for_run ────────────────────────────────────────────────


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


def test_conversation_store_is_owner_only(tmp_path):
    """Chat history carries bodies + identities: 0600, and 0644 heals on append."""
    import os
    import stat

    _append_tg_event(tmp_path, "evt-perm-1", 1)
    key = next(iter(conversations.list_conversations(tmp_path)))
    files = sorted(conversations.conversation_path(tmp_path, key).glob("*.jsonl"))
    assert files
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)

    # A legacy file sitting at 0644 is repaired by the next append to it.
    legacy = files[0]
    os.chmod(legacy, 0o644)
    conversations.append_event(  # same event id -> same per-event file
        tmp_path, key, {
            "id": "evt-perm-1", "source": "telegram", "body": "again",
            "telegram_chat_id": 10, "telegram_message_id": 1,
        },
    )
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o600


