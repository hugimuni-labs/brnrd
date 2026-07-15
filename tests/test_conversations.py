"""Tests for the conversation log: keys, append/read, listing."""

import json

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


def test_correspondent_key_telegram_user_id_matches_cloud_relay():
    native = {
        "source": "telegram",
        "telegram_user_id": 42,
        "telegram_username": "AdaL",
        "telegram_user": "Ada",
    }
    cloud = {
        "source": "cloud",
        "cloud_platform": "telegram",
        "cloud_user_id": 42,
        "cloud_username": "AdaL",
        "cloud_user": "Ada",
    }
    assert conversations.correspondent_key_for_event(native) == (
        "telegram:user-id:42"
    )
    assert conversations.correspondent_key_for_event(cloud) == (
        "telegram:user-id:42"
    )


def test_correspondent_key_uses_stable_handles():
    assert conversations.correspondent_key_for_event(
        {"source": "github", "github_author": "OctoCat"},
    ) == "github:login:octocat"
    assert conversations.correspondent_key_for_event(
        {"source": "slack", "slack_user": "U123"},
    ) == "slack:user:u123"


def test_origin_message_key_matches_native_and_cloud_telegram():
    native = {
        "source": "telegram",
        "telegram_chat_id": 10,
        "telegram_topic_id": 3,
        "telegram_message_id": 99,
    }
    cloud = {
        "source": "cloud",
        "cloud_platform": "telegram",
        "cloud_chat_id": 10,
        "cloud_topic_id": 3,
        "cloud_message_id": 99,
    }
    assert conversations.origin_message_key_for_event(native) == (
        "telegram:10:3:99"
    )
    assert conversations.origin_message_key_for_event(cloud) == (
        "telegram:10:3:99"
    )


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


def test_find_event_by_origin_message_unbounded_still_matches(tmp_path):
    """max_age_seconds=None preserves the old behaviour: any prior match."""
    origin = _append_tg_event(tmp_path, "evt-old", 99)
    hit = conversations.find_event_by_origin_message(tmp_path, origin)
    assert hit is not None
    assert hit["event_id"] == "evt-old"


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


def test_read_recent_for_correspondent_merges_sibling_channels(tmp_path):
    native = {
        "id": "evt-native",
        "source": "telegram",
        "body": "from native",
        "telegram_chat_id": 10,
        "telegram_user_id": 42,
    }
    cloud = {
        "id": "evt-cloud",
        "source": "cloud",
        "body": "from cloud",
        "cloud_platform": "telegram",
        "cloud_chat_id": 10,
        "cloud_user_id": 42,
    }
    native_key = conversations.conversation_key_for_event(native)
    cloud_key = conversations.conversation_key_for_event(cloud)
    assert native_key == "telegram:10:"
    assert cloud_key == "cloud:telegram:10:"

    conversations.append_event(tmp_path, native_key, native)
    conversations.append_event(tmp_path, cloud_key, cloud)

    records = conversations.read_recent_for_correspondent(
        tmp_path, cloud_key, "telegram:user-id:42", limit=5,
    )
    bodies = [r.get("body") for r in records]
    assert bodies == ["from native", "from cloud"]
    assert [r.get("conversation_key") for r in records] == [native_key, cloud_key]


def test_build_communication_snapshot_groups_related_threads(tmp_path):
    native = {
        "id": "evt-native",
        "source": "telegram",
        "body": "from native",
        "telegram_chat_id": 10,
        "telegram_user_id": 42,
    }
    cloud = {
        "id": "evt-cloud",
        "source": "cloud",
        "body": "from cloud",
        "cloud_platform": "telegram",
        "cloud_chat_id": 10,
        "cloud_user_id": 42,
    }
    native_key = conversations.conversation_key_for_event(native)
    cloud_key = conversations.conversation_key_for_event(cloud)
    conversations.append_event(tmp_path, native_key, native)
    conversations.append_artifact(
        tmp_path,
        native_key,
        kind="response",
        path="/tmp/evt-native.md",
        event_id="evt-native",
        body="agent reply",
    )
    conversations.append_event(tmp_path, cloud_key, cloud)

    snapshot = conversations.build_communication_snapshot(
        tmp_path,
        cloud_key,
        "telegram:user-id:42",
        event_id="evt-cloud",
        run_id="run-cloud",
        recent_limit=5,
    )

    assert snapshot["current_thread"] == cloud_key
    assert snapshot["correspondent_key"] == "telegram:user-id:42"
    assert {
        t["conversation_key"] for t in snapshot["related_threads"]
    } == {native_key, cloud_key}
    assert [r.get("body") for r in snapshot["recent_turns"]] == [
        "from native",
        "agent reply",
    ]


def test_build_communication_snapshot_boosts_unanswered_turns(tmp_path):
    key = "telegram:10:"
    conversations.append_event(
        tmp_path,
        key,
        {"id": "evt-old", "source": "telegram", "body": "still unanswered"},
    )
    conversations.append_event(
        tmp_path,
        key,
        {"id": "evt-new", "source": "telegram", "body": "answered"},
    )
    conversations.append_artifact(
        tmp_path,
        key,
        kind="response",
        path="/tmp/evt-new.md",
        event_id="evt-new",
        body="answer",
    )

    snapshot = conversations.build_communication_snapshot(
        tmp_path, key, recent_limit=2,
    )

    assert [r.get("body") for r in snapshot["recent_turns"]] == [
        "still unanswered",
        "answer",
    ]


def test_build_communication_snapshot_unanswered_flood_does_not_blank_recency(
    tmp_path,
):
    """A pile of stale unanswered events must not crowd out *all* of recency.

    Live bug (2026-07-07): a busy thread with a run of old unanswered events
    (attachment-only messages, replies folded into a sibling event's answer,
    ...) caused every woven "recent turns" slot to be spent on those stale
    events, even though the last several exchanges were fully answered and
    far more recent — the resident opened a wake unable to see anything
    that had "just happened". The boost for unanswered asks must stay
    capped so recency always keeps at least half the budget.
    """
    key = "telegram:10:"
    for i in range(10):
        conversations.append_event(
            tmp_path,
            key,
            {"id": f"evt-stale-{i}", "source": "telegram", "body": f"stale-{i}"},
        )
    for i in range(2):
        conversations.append_event(
            tmp_path,
            key,
            {"id": f"evt-recent-{i}", "source": "telegram", "body": f"recent-q-{i}"},
        )
        conversations.append_artifact(
            tmp_path,
            key,
            kind="response",
            path=f"/tmp/evt-recent-{i}.md",
            event_id=f"evt-recent-{i}",
            body=f"recent-a-{i}",
        )

    snapshot = conversations.build_communication_snapshot(
        tmp_path, key, recent_limit=8,
    )

    bodies = [r.get("body") for r in snapshot["recent_turns"]]
    assert len(bodies) == 8
    # The most recent, fully-answered exchange must survive — not just the
    # oldest stale unanswered pile.
    assert "recent-q-1" in bodies
    assert "recent-a-1" in bodies
    # Recency keeps at least half the budget regardless of how many stale
    # unanswered events exist.
    assert sum(1 for b in bodies if b.startswith("stale-")) <= 4


def test_build_communication_snapshot_unanswered_boost_has_age_horizon(
    tmp_path,
):
    """A fossil unanswered event stops being boosted after the horizon.

    Live observation (2026-07-11): "unanswered" is bookkeeping, not truth —
    replies folded into sibling events (or delivered before artifact
    tagging) leave events unanswered forever, and on a busy thread those
    month-old fossils were boosted into every wake's snapshot, displacing
    genuinely recent turns. Beyond ``_UNANSWERED_BOOST_HORIZON`` the boost
    must lapse; plain recency still applies.
    """
    key = "telegram:10:"
    # A month-old unanswered event — the fossil.
    conversations.append_record(
        tmp_path,
        key,
        {
            "ts": "2026-06-01T10:00:00+00:00",
            "kind": "event",
            "event_id": "evt-fossil",
            "body": "fossil ask",
        },
        event_id="evt-fossil",
    )
    # A recent unanswered event — inside the horizon, still boosted.
    conversations.append_record(
        tmp_path,
        key,
        {
            "ts": "2026-06-30T10:00:00+00:00",
            "kind": "event",
            "event_id": "evt-recent-open",
            "body": "recent open ask",
        },
        event_id="evt-recent-open",
    )
    # Enough recent answered exchanges to overflow the budget.
    for i in range(4):
        conversations.append_record(
            tmp_path,
            key,
            {
                "ts": f"2026-07-01T1{i}:00:00+00:00",
                "kind": "event",
                "event_id": f"evt-q-{i}",
                "body": f"q-{i}",
            },
            event_id=f"evt-q-{i}",
        )
        conversations.append_record(
            tmp_path,
            key,
            {
                "ts": f"2026-07-01T1{i}:30:00+00:00",
                "kind": "artifact",
                "artifact_kind": "response",
                "path": f"/tmp/evt-q-{i}.md",
                "event_id": f"evt-q-{i}",
                "body": f"a-{i}",
            },
            event_id=f"evt-q-{i}",
        )

    snapshot = conversations.build_communication_snapshot(
        tmp_path, key, recent_limit=7,
    )

    bodies = [r.get("body") for r in snapshot["recent_turns"]]
    # The within-horizon unanswered ask is boosted in; the fossil is not.
    assert "recent open ask" in bodies
    assert "fossil ask" not in bodies


def test_build_communication_snapshot_surfaces_prior_run_failure(tmp_path):
    key = "telegram:10:"
    # A prior run on the thread that died operationally (credit-low).
    conversations.append_event(
        tmp_path,
        key,
        {"id": "evt-old", "source": "telegram", "body": "do the thing"},
    )
    conversations.append_update(
        tmp_path,
        key,
        type="failed",
        payload={
            "run_id": "run-old",
            "event_id": "evt-old",
            "stage": "run",
            "attempts": 3,
            "exit_code": 1,
            "error": "Credit balance is too low",
        },
        event_id="evt-old",
    )
    # A fresh wake on the same thread.
    conversations.append_event(
        tmp_path,
        key,
        {"id": "evt-new", "source": "telegram", "body": "any update?"},
    )

    snapshot = conversations.build_communication_snapshot(
        tmp_path, key, event_id="evt-new", run_id="run-new", recent_limit=5,
    )

    failure = snapshot["prior_failure"]
    assert failure["reason"] == "Credit balance is too low"
    assert failure["stage"] == "run"
    assert failure["attempts"] == 3
    assert failure["exit_code"] == 1
    assert failure["event_id"] == "evt-old"
    assert "ts" in failure


def test_build_communication_snapshot_prior_failure_cleared_by_success(tmp_path):
    key = "telegram:10:"
    conversations.append_update(
        tmp_path,
        key,
        type="failed",
        payload={"event_id": "evt-old", "stage": "run", "error": "OOM"},
        event_id="evt-old",
    )
    # A later run on the thread succeeded — the stale failure must not surface.
    conversations.append_update(
        tmp_path,
        key,
        type="done",
        payload={"event_id": "evt-mid"},
        event_id="evt-mid",
    )
    conversations.append_event(
        tmp_path,
        key,
        {"id": "evt-new", "source": "telegram", "body": "hello"},
    )

    snapshot = conversations.build_communication_snapshot(
        tmp_path, key, event_id="evt-new", run_id="run-new", recent_limit=5,
    )

    assert "prior_failure" not in snapshot


def test_build_communication_snapshot_no_failure_on_clean_thread(tmp_path):
    key = "telegram:10:"
    conversations.append_event(
        tmp_path,
        key,
        {"id": "evt-old", "source": "telegram", "body": "earlier"},
    )
    conversations.append_artifact(
        tmp_path, key, kind="response", path="/tmp/evt-old.md",
        event_id="evt-old", body="all done",
    )
    conversations.append_event(
        tmp_path,
        key,
        {"id": "evt-new", "source": "telegram", "body": "again"},
    )

    snapshot = conversations.build_communication_snapshot(
        tmp_path, key, event_id="evt-new", run_id="run-new", recent_limit=5,
    )

    assert "prior_failure" not in snapshot


def test_write_grouped_history_files_writes_untruncated_thread_jsonl(tmp_path):
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
    records = [
        json.loads(line)
        for line in (tmp_path / group["path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert [r["kind"] for r in records] == ["event", "run"]
    assert records[0]["conversation_key"] == key
    manifest = tmp_path / "runs" / "run-1" / "history" / "manifest.json"
    assert manifest.exists()


def test_read_recent_prefers_dialogue_over_lifecycle_noise(tmp_path):
    conversations.append_event(
        tmp_path,
        "k",
        {"id": "evt-1", "source": "telegram", "body": "first message"},
    )
    for i in range(20):
        conversations.append_update(
            tmp_path,
            "k",
            type="attempt_started",
            payload={"run_id": "run-1", "attempt": i},
            event_id="evt-1",
        )
    conversations.append_artifact(
        tmp_path,
        "k",
        kind="response",
        path="/tmp/evt-1.md",
        event_id="evt-1",
        body="agent answer",
    )

    recent = conversations.read_recent(tmp_path, "k", limit=2)
    assert [r["kind"] for r in recent] == ["event", "artifact"]
    assert [r["body"] for r in recent] == ["first message", "agent answer"]

    raw_recent = conversations.read_recent(tmp_path, "k", limit=2, include_lifecycle=True)
    assert raw_recent[-1]["kind"] == "artifact"
    assert any(r["kind"] == "update" for r in raw_recent)


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


# ── records_for_run ────────────────────────────────────────────────


def test_records_for_run_filters_by_run_id(tmp_path):
    conversations.append_record(
        tmp_path, "k", {"kind": "run", "run_id": "t-1"}, event_id="evt-a",
    )
    conversations.append_record(
        tmp_path, "k", {"kind": "update", "run_id": "t-2"}, event_id="evt-b",
    )
    conversations.append_record(
        tmp_path, "k", {"kind": "update", "run_id": "t-1", "type": "done"},
        event_id="evt-a",
    )
    matches = conversations.records_for_run(tmp_path, "k", "t-1")
    assert len(matches) == 2
    assert all(r["run_id"] == "t-1" for r in matches)


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
