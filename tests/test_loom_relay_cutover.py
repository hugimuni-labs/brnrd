"""Loom step 5 slice 3: one poller per relay cursor, the wiring, the give-ups."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from brr.daemon2.facts import Fact
from brr.gates import cloud, relay_lock
from brr.loom.runtime import loom as loom_mod, speak
from brr.loom.runtime.channels import relay
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts

from test_loom_relay import FakeClient, event, home, kinds, speak as spoke  # noqa: F401


class _StopLoop(BaseException):
    pass


def _cloud_state(tmp_path, since=0):
    brr_dir = tmp_path / ".brr"
    cloud._save_state(brr_dir, {"brnrd_url": "http://brnrd", "token": "bd_x",
                                "repo_id": "proj_x", "since": since})
    return brr_dir, brr_dir / "inbox", brr_dir / "responses"


def _quiet_cloud(monkeypatch):
    monkeypatch.setattr(cloud, "_register", lambda *_a, **_k: None)
    monkeypatch.setattr(cloud, "_try_refresh_publishing_credential", lambda *_a, **_k: None)
    monkeypatch.setattr(cloud, "_dashboard_publish_loop", lambda *_a, **_k: None)


# ── the lock ────────────────────────────────────────────────────────────


def test_second_holder_refuses_until_the_first_releases(tmp_path):
    first = relay_lock.RelayLock(tmp_path, "daemon")
    second = relay_lock.RelayLock(tmp_path, "loom")
    assert first.try_acquire()
    first.record(7)
    assert not second.try_acquire()
    assert second.holder() == f"daemon pid {os.getpid()}"
    first.release()
    assert second.try_acquire()
    # The handed-over cursor survives the change of holder.
    assert second.cursor() == 7
    assert second.read()["kind"] == "loom"


def test_a_dead_wanter_does_not_block_the_daemon(tmp_path):
    lock = relay_lock.RelayLock(tmp_path, "daemon")
    path = relay_lock.want_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 2 ** 22 + 12345, "kind": "loom"}))
    assert lock.wanted_by_other() is None
    path.write_text(json.dumps({"pid": os.getppid(), "kind": "loom"}))
    assert lock.wanted_by_other()["kind"] == "loom"


# ── the daemon's side ───────────────────────────────────────────────────


def test_cloud_gate_yields_to_a_loom_holder_and_only_delivers(tmp_path, monkeypatch):
    brr_dir, inbox_dir, responses_dir = _cloud_state(tmp_path)
    _quiet_cloud(monkeypatch)
    loom_lock = relay_lock.RelayLock(cloud._state_dir(brr_dir), "loom")
    assert loom_lock.try_acquire()
    polls, delivered = [], []
    monkeypatch.setattr(cloud, "_loop_once", lambda *_a: polls.append(1))
    monkeypatch.setattr(cloud, "_deliver_responses", lambda *_a: delivered.append(1))

    def sleep(_s):
        if len(delivered) >= 2:
            raise _StopLoop
    monkeypatch.setattr(cloud.time, "sleep", sleep)

    with pytest.raises(_StopLoop):
        cloud.run_loop(brr_dir, inbox_dir, responses_dir)

    assert polls == []
    assert len(delivered) == 2
    health = cloud.runtime.load_health(brr_dir, "cloud")
    assert health["last_error"] == f"relay held by loom pid {os.getpid()}: not polling"


def test_cloud_gate_takes_the_loom_cursor_and_hands_its_own_back(tmp_path, monkeypatch):
    brr_dir, inbox_dir, responses_dir = _cloud_state(tmp_path, since=3)
    _quiet_cloud(monkeypatch)
    state_dir = cloud._state_dir(brr_dir)
    previous = relay_lock.RelayLock(state_dir, "loom")
    assert previous.try_acquire()
    previous.record(40)
    previous.release()
    seen = []

    def loop_once(*_a):
        state = cloud._load_state(brr_dir)
        seen.append(state["since"])
        state["since"] = 44
        cloud._save_state(brr_dir, state)
        # Released between polls, so a loom can take it.
        raise _StopLoop
    monkeypatch.setattr(cloud, "_loop_once", loop_once)

    with pytest.raises(_StopLoop):
        cloud.run_loop(brr_dir, inbox_dir, responses_dir)
    assert seen == [40]
    later = relay_lock.RelayLock(state_dir, "loom")
    assert later.try_acquire()  # the daemon released it on the way out


def test_cloud_gate_records_its_cursor_after_each_poll(tmp_path, monkeypatch):
    brr_dir, inbox_dir, responses_dir = _cloud_state(tmp_path, since=5)
    _quiet_cloud(monkeypatch)
    calls = []

    def loop_once(*_a):
        calls.append(1)
        if len(calls) > 1:
            raise _StopLoop
        state = cloud._load_state(brr_dir)
        state["since"] = 9
        cloud._save_state(brr_dir, state)
    monkeypatch.setattr(cloud, "_loop_once", loop_once)

    with pytest.raises(_StopLoop):
        cloud.run_loop(brr_dir, inbox_dir, responses_dir)
    assert relay_lock.RelayLock(cloud._state_dir(brr_dir), "x").cursor() == 9


# ── the loom's side ─────────────────────────────────────────────────────


def test_config_reads_flat_relay_keys(tmp_path):
    (tmp_path / "loom").mkdir()
    (tmp_path / "loom" / "config.toml").write_text(
        'relay = true\nrelay_state = "~/acct"\n[channels.relay]\nenabled = true\n'
    )
    config = load_config(tmp_path)
    assert config.relay is True
    assert config.relay_state == os.path.expanduser("~/acct")
    assert load_config(tmp_path / "nowhere").relay is False


def test_poll_forever_starts_from_the_handed_cursor_and_records_its_own(home, tmp_path):
    state_dir = tmp_path / "acct"
    daemon = relay_lock.RelayLock(state_dir, "daemon")
    assert daemon.try_acquire()
    daemon.record(12)
    daemon.release()
    stop = threading.Event()
    client = FakeClient([event("ev_a")], cursor=13)
    pull = client.pull

    def pull_then_stop(cursor):
        stop.set()
        return pull(cursor)
    client.pull = pull_then_stop
    lock = relay_lock.RelayLock(state_dir, "loom")
    relay.poll_forever(home, client, lock, stop)

    assert client.polls == [12]
    assert relay.read_cursor(home) == 13
    assert [f.id for f in kinds(home, "letter")] == ["letter:relay:ev_a"]
    after = relay_lock.RelayLock(state_dir, "daemon")
    assert after.cursor() == 13
    assert after.wanted_by_other() is None  # the want went with the loom
    assert after.try_acquire()


def test_poll_forever_waits_while_the_daemon_polls(home, tmp_path):
    state_dir = tmp_path / "acct"
    daemon = relay_lock.RelayLock(state_dir, "daemon")
    assert daemon.try_acquire()
    stop = threading.Event()
    client = FakeClient([], cursor=0)
    client.pull = lambda cursor: (stop.set(), {"events": [], "cursor": 0})[1]
    lines = []
    worker = threading.Thread(target=relay.poll_forever, args=(
        home, client, relay_lock.RelayLock(state_dir, "loom"), stop,
    ), kwargs={"log": lines.append})
    worker.start()
    deadline = time.monotonic() + 5
    while not lines and time.monotonic() < deadline:
        time.sleep(0.01)
    assert lines and lines[0].startswith("relay: waiting for daemon pid")
    daemon.release()
    worker.join(timeout=5)
    assert not worker.is_alive()


def test_daemon_yields_to_a_live_loom_want(tmp_path, monkeypatch):
    state_dir = tmp_path / "acct"
    path = relay_lock.want_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getppid(), "kind": "loom"}))
    daemon = relay_lock.RelayLock(state_dir, "daemon")
    assert cloud._relay_yield(daemon) == f"loom pid {os.getppid()}"
    assert not daemon.held


def test_arm_relay_registers_the_effect_and_polls(home, tmp_path, monkeypatch):
    state_dir = tmp_path / "acct"
    (home.root / "loom").mkdir(parents=True, exist_ok=True)
    (home.root / "loom" / "config.toml").write_text(
        f'relay = true\nrelay_state = "{state_dir}"\n'
    )
    pulled = threading.Event()

    class Client(FakeClient):
        def __init__(self, _state_dir):
            super().__init__([event("ev_w")], cursor=1)

        def pull(self, cursor):
            pulled.set()
            return super().pull(cursor)
    monkeypatch.setattr(relay, "RelayClient", Client)
    stop = threading.Event()
    thread = loom_mod._arm_relay(home, load_config(home.root), stop)
    try:
        assert "relay" in speak.EFFECTS
        assert pulled.wait(5)
    finally:
        stop.set()
        loom_mod._disarm_relay(thread)
    assert "relay" not in speak.EFFECTS
    assert "letter:relay:ev_w" in {f.id for f in kinds(home, "letter")}


def test_arm_relay_without_state_dir_is_an_attention_row(home):
    (home.root / "loom").mkdir(parents=True, exist_ok=True)
    (home.root / "loom" / "config.toml").write_text("relay = true\n")
    assert loom_mod._arm_relay(home, load_config(home.root), threading.Event()) is None
    assert "attention:relay-config" in {f.id for f in kinds(home, "attention")}


# ── before live: the attachment give-up ─────────────────────────────────


def test_a_missing_attachment_holds_the_cursor_three_pulls_then_lands(home):
    client = FakeClient([event("ev_f", attachments=[{"name": "a.jpg"}, {"name": "b.jpg"}])],
                        cursor=8)
    calls = []

    def download(ident, index, dest):
        calls.append(index)
        dest.write_bytes(b"ok")
        return index == 0
    client.download_attachment = download

    for attempt in (1, 2):
        with pytest.raises(RuntimeError, match=f"attempt {attempt} of 3"):
            relay.pull_once(home, client, 0)
        assert relay.read_cursor(home) == 0
        assert kinds(home, "source") == []

    assert relay.pull_once(home, client, 0) == 8
    (source,) = kinds(home, "source")
    assert source.data["blobs_missing"] == [1]
    assert len(source.data["blobs"]) == 1
    assert "attention:blobs-missing:ev_f" in {f.id for f in kinds(home, "attention")}
    assert not json.loads((home.root / "loom" / "relay-failures.json").read_text())
    # A replay after giving up is still one source, one letter.
    relay.pull_once(home, client, 0)
    assert len(kinds(home, "source")) == 1 and len(kinds(home, "letter")) == 1


# ── the molt analog: a bare line after the speaker released ────────────


def test_bare_message_after_the_speaker_released_wakes_that_thread(home):
    spoke(home, "s-first", "first")
    append(home, Fact(kind="released", by="loom:aaaa", id="released:first:1",
                      data={"thread": "first", "strand": "s-first", "gen": 1,
                            "why": "molt", "install": "aaaa"}))
    relay.pull_once(home, FakeClient([event("ev_late")]), 0)
    (letter,) = [f for f in kinds(home, "letter") if f.id == "letter:relay:ev_late"]
    assert letter.data["to"] == "thread:first"
