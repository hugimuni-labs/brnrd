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
    assert entry["name"] == ""
    assert entry["run_id"] == "t1"
    assert entry["repo_label"] == "Gurio/brr"
    assert entry["pid"] > 0

    active = presence.list_active(brr)
    assert [e["id"] for e in active] == [entry["id"]]


def test_register_carries_runner_fields(tmp_path):
    """Shell+Core threaded into presence at registration time (2026-07-13)
    so the live-runs dashboard can name which Runner a running thought is
    on, not only the closed-run ledger after it finishes."""
    brr = tmp_path / ".brr"
    entry = presence.register(
        brr, kind="daemon", run_id="t1",
        runner_name="claude-sonnet", runner_shell="claude",
        runner_core="claude-sonnet-4-6", runner_class="balanced",
    )
    assert entry["runner_name"] == "claude-sonnet"
    assert entry["runner_shell"] == "claude"
    assert entry["runner_core"] == "claude-sonnet-4-6"
    assert entry["runner_class"] == "balanced"

    active = presence.list_active(brr)
    assert active[0]["runner_shell"] == "claude"


def test_register_runner_fields_default_to_empty(tmp_path):
    """A caller that doesn't pass runner fields (older code path, or a
    plain ad-hoc session) still gets a well-formed entry, not a KeyError
    downstream when `_runner_payload` reads it."""
    brr = tmp_path / ".brr"
    entry = presence.register(brr, kind="session", run_id="t1")
    assert entry["runner_name"] == ""
    assert entry["runner_shell"] == ""
    assert entry["runner_core"] == ""
    assert entry["runner_class"] == ""


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


def test_heartbeat_refreshes_resident_authored_name(tmp_path):
    brr = tmp_path / ".brr"
    entry = presence.register(brr, kind="daemon", run_id="t1")
    assert presence.heartbeat(brr, entry["id"], name="dashboard name") is True
    assert presence.list_active(brr)[0]["name"] == "dashboard name"


def test_register_defaults_topics_to_empty_list(tmp_path):
    brr = tmp_path / ".brr"
    entry = presence.register(brr, kind="daemon", run_id="t1")
    assert entry["topics"] == []


def test_heartbeat_refreshes_resident_claimed_topics(tmp_path):
    """Same live-read discipline as `mood`/`name` (steer, 2026-08-12): a
    burning run's claimed topics ride the same heartbeat, not only the
    closeout `topics.md` capture."""
    brr = tmp_path / ".brr"
    entry = presence.register(brr, kind="daemon", run_id="t1")
    assert presence.heartbeat(brr, entry["id"], topics=["the-loom", "the-post"]) is True
    assert presence.list_active(brr)[0]["topics"] == ["the-loom", "the-post"]
    # Omitting `topics` on a later heartbeat leaves the last claim standing
    # — same "None means unchanged" rule `name`/`mood` already follow.
    assert presence.heartbeat(brr, entry["id"]) is True
    assert presence.list_active(brr)[0]["topics"] == ["the-loom", "the-post"]


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


def test_account_dirs_falls_back_to_the_one_checkout(tmp_path, monkeypatch):
    """An unresolvable account never narrows the walk below its own repo."""
    from brr import account

    brr = tmp_path / "repo" / ".brr"
    brr.mkdir(parents=True)

    def boom(*_args, **_kwargs):
        raise RuntimeError("no home here")

    monkeypatch.setattr(account, "resolve_context", boom)
    assert presence.account_dirs(brr) == [brr]


def test_account_dirs_keeps_this_checkout_even_when_unregistered(tmp_path, monkeypatch):
    """The reader's own repo is never dropped by a registry that omits it.

    A checkout can be live before the registry names it (first connect, a
    label rename mid-flight). Losing it here would swap #1727's missing
    sibling for a missing self — the same lie, one row over.
    """
    from brr import account
    from types import SimpleNamespace

    mine = tmp_path / "mine" / ".brr"
    other = tmp_path / "other" / ".brr"
    mine.mkdir(parents=True)
    other.mkdir(parents=True)
    monkeypatch.setattr(
        account,
        "resolve_context",
        lambda *_a, **_k: SimpleNamespace(
            repos={"org/other": SimpleNamespace(label="org/other", root=other.parent)}
        ),
    )
    assert presence.account_dirs(mine) == [mine, other]


def test_list_active_account_joins_siblings_oldest_first(tmp_path, monkeypatch):
    """A strand in a sibling repo is a coexisting run, not an absent one."""
    import os
    from types import SimpleNamespace

    from brr import account

    a = tmp_path / "a" / ".brr"
    b = tmp_path / "b" / ".brr"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    presence.register(
        a, kind="daemon", run_id="run-parent", repo_label="org/a",
        pid=os.getpid(), entry_id="p-a", now=1000.0,
    )
    presence.register(
        b, kind="daemon", run_id="run-strand", repo_label="org/b",
        pid=os.getpid(), entry_id="p-b", now=900.0,
        is_subspawn=True, parent_run_id="run-parent",
    )
    monkeypatch.setattr(
        account,
        "resolve_context",
        lambda *_a, **_k: SimpleNamespace(
            repos={
                "org/a": SimpleNamespace(label="org/a", root=a.parent),
                "org/b": SimpleNamespace(label="org/b", root=b.parent),
            }
        ),
    )

    rows = presence.list_active_account(a, now=1000.0)

    assert [row["run_id"] for row in rows] == ["run-strand", "run-parent"]
    assert rows[0]["repo_label"] == "org/b"


def test_list_active_account_counts_one_body_once(tmp_path, monkeypatch):
    """A repo reachable under two labels is still one live run."""
    import os
    from types import SimpleNamespace

    from brr import account

    brr = tmp_path / "solo" / ".brr"
    brr.mkdir(parents=True)
    presence.register(
        brr, kind="daemon", run_id="run-solo", pid=os.getpid(), entry_id="p-solo",
    )
    monkeypatch.setattr(
        account,
        "resolve_context",
        lambda *_a, **_k: SimpleNamespace(
            repos={
                "org/one": SimpleNamespace(label="org/one", root=brr.parent),
                "org/two": SimpleNamespace(label="org/two", root=brr.parent),
            }
        ),
    )

    assert [row["run_id"] for row in presence.list_active_account(brr)] == ["run-solo"]


def test_account_dirs_reads_the_registry_once_per_ttl(tmp_path, monkeypatch):
    """The resolve is ~270ms of git and config I/O, and this runs at every
    tool boundary. A quarter second per boundary for a fact that changes
    when someone runs `brnrd connect` is the resident's own latency spent
    on nothing."""
    from types import SimpleNamespace

    from brr import account

    mine = tmp_path / "mine" / ".brr"
    mine.mkdir(parents=True)
    calls = []

    def counted(*_a, **_k):
        calls.append(1)
        return SimpleNamespace(repos={})

    monkeypatch.setattr(account, "resolve_context", counted)
    presence._account_dirs_cache.clear()

    assert presence.account_dirs(mine, now=1000.0) == [mine]
    # A literal five seconds, not `TTL - 1`: a window derived from the
    # constant it is meant to pin moves with it, so `TTL = 0` would still
    # pass and the guard would be measuring nothing.
    assert presence.account_dirs(mine, now=1005.0) == [mine]
    assert len(calls) == 1, "a second read inside the window is the cost this cache exists to refuse"

    assert presence.account_dirs(mine, now=1000.0 + presence.ACCOUNT_DIRS_TTL_S + 1) == [mine]
    assert len(calls) == 2, "past the window it must read again — a connected sibling has to become visible"


def test_account_dirs_cache_is_keyed_per_checkout(tmp_path, monkeypatch):
    """Two repos on one machine must not inherit each other's answer."""
    from types import SimpleNamespace

    from brr import account

    a = tmp_path / "a" / ".brr"
    b = tmp_path / "b" / ".brr"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    monkeypatch.setattr(account, "resolve_context", lambda *_a, **_k: SimpleNamespace(repos={}))
    presence._account_dirs_cache.clear()

    assert presence.account_dirs(a, now=1000.0) == [a]
    assert presence.account_dirs(b, now=1000.0) == [b]
