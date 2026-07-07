"""Tests for the presence registry (slice 5b).

Who's awake in the repo right now — a gitignored, lock-free registry
(each participant owns one file) that self-heals on read by pruning dead
or stale entries. See ``kb/design-agent-dominion.md`` §4.
"""

from __future__ import annotations

import subprocess

from brr import presence


def test_register_then_list(tmp_path):
    brr = tmp_path / ".brr"
    entry = presence.register(
        brr,
        kind="daemon",
        stream="telegram:1:",
        label="Investigate live-runs labels",
        run_id="t1",
        repo_label="Gurio/brr",
    )
    assert entry["id"]
    assert entry["kind"] == "daemon"
    assert entry["stream"] == "telegram:1:"
    assert entry["label"] == "Investigate live-runs labels"
    assert entry["run_id"] == "t1"
    assert entry["repo_label"] == "Gurio/brr"
    assert entry["pid"] > 0

    active = presence.list_active(brr)
    assert [e["id"] for e in active] == [entry["id"]]


def test_list_is_oldest_first(tmp_path):
    brr = tmp_path / ".brr"
    a = presence.register(brr, kind="daemon", run_id="a", now=100.0)
    b = presence.register(brr, kind="session", run_id="b", now=200.0)
    active = presence.list_active(brr, now=210.0)
    assert [e["id"] for e in active] == [a["id"], b["id"]]


def test_heartbeat_refreshes_and_keeps_alive(tmp_path):
    brr = tmp_path / ".brr"
    entry = presence.register(brr, kind="daemon", run_id="t1", now=100.0)
    # Without a heartbeat it would be stale by now=500 (cutoff 200)...
    assert presence.list_active(brr, stale_after_s=300, now=500.0) == []
    # ...but a fresh heartbeat keeps it present.
    presence.register(brr, kind="daemon", run_id="t1", entry_id=entry["id"], now=100.0)
    assert presence.heartbeat(brr, entry["id"], now=480.0) is True
    active = presence.list_active(brr, stale_after_s=300, now=500.0)
    assert [e["id"] for e in active] == [entry["id"]]


def test_heartbeat_missing_entry_is_false(tmp_path):
    brr = tmp_path / ".brr"
    assert presence.heartbeat(brr, "nope") is False


def test_deregister_removes(tmp_path):
    brr = tmp_path / ".brr"
    entry = presence.register(brr, kind="session", run_id="t1")
    presence.deregister(brr, entry["id"])
    assert presence.list_active(brr) == []
    # idempotent
    presence.deregister(brr, entry["id"])


def test_stale_entry_is_pruned_on_read(tmp_path):
    brr = tmp_path / ".brr"
    entry = presence.register(brr, kind="daemon", run_id="t1", now=1000.0)
    pruned = presence.list_active(brr, stale_after_s=300, now=2000.0)
    assert pruned == []
    # The prune deletes the file, so it doesn't linger.
    assert not (brr / presence.PRESENCE_DIRNAME / f"{entry['id']}.json").exists()


def test_dead_pid_same_host_is_pruned(tmp_path):
    brr = tmp_path / ".brr"
    # A reaped child gives a pid that is certainly dead on this host.
    proc = subprocess.Popen(["true"])
    proc.wait()
    dead = proc.pid
    entry = presence.register(brr, kind="session", run_id="t1", pid=dead)
    # Fresh heartbeat (not stale), but the process is gone → pruned.
    assert presence.list_active(brr) == []
    assert not (brr / presence.PRESENCE_DIRNAME / f"{entry['id']}.json").exists()


def test_missing_dir_is_empty(tmp_path):
    assert presence.list_active(tmp_path / ".brr") == []
