"""Relay wire events become replay-safe source records and actionable letters."""

from __future__ import annotations

import copy
import hashlib
import json
import time

import pytest

from brr.daemon2.facts import Fact
from brr.gates import cloud
from brr.loom.runtime.channels import relay
from brr.loom.runtime.home import Home
from brr.loom.runtime.labels import CLEAN, strand_label
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.project import owed
from brr.loom.runtime.port import parse_boundary, render_boundary


# Shape from test_cloud_gate.py's Telegram enqueue/_loop_once usage, and
# webhooks._enqueue_telegram_event: body, event_id, verified reply_to,
# attachment pointers. message_id is the incoming id, never a reply target.
def event(ident="ev_1", *, user_id=42, body="fix from telegram", attachments=None):
    return {
        "event_id": ident, "body": body, "source": "telegram",
        "reply_to": {
            "platform": "telegram", "chat_id": 555, "topic_id": 9,
            "message_id": 100, "user": "Ada", "user_id": user_id, "username": "ada_l",
        },
        "attachments": attachments or [],
    }


class FakeClient:
    def __init__(self, events, cursor=3, payload=b"JPEG!"):
        self.events = events
        self.cursor = cursor
        self.payload = payload
        self.polls = []
        self.downloads = []
        self.fail_download = False

    def pull(self, cursor):
        self.polls.append(cursor)
        return {"events": copy.deepcopy(self.events), "cursor": self.cursor}

    def download_attachment(self, ident, index, dest):
        self.downloads.append((ident, index))
        dest.write_bytes(self.payload)
        return not self.fail_download


@pytest.fixture
def home(tmp_path):
    home = Home(tmp_path, install="aaaa")
    for thread in ["inbox", "first", "second"]:
        directory = home.thread_dir(thread)
        directory.mkdir(parents=True)
        (directory / "README.md").write_text(f"# {thread}\n")
    person = home.root / "self" / "people" / "ada"
    person.mkdir(parents=True)
    (person / "channels.md").write_text("telegram:42\nwhatsapp:555\n")
    return home


def kinds(home, kind):
    return [f for f in read_facts(home) if f.kind == kind]


def speak(home, strand, thread, *, until=None, router_gen=1,
          chat="telegram/555", receipt=True):
    if until is None:
        until = time.time() + 600
    if not kinds(home, "router"):
        append(home, Fact(kind="router", by="loom:aaaa", id="router:1",
                          data={"gen": router_gen, "until": until, "install": "aaaa"}))
    append(home, Fact(kind="lease", by="loom:aaaa", id=f"lease:{thread}:1",
                     data={"thread": thread, "strand": strand, "gen": 1,
                           "router_gen": router_gen, "install": "aaaa"}))
    key = f"{strand}/answer"
    append(home, Fact(kind="letter", by=f"strand:{strand}", id=key,
                     data={"id": key, "from": strand, "to": f"channel:{chat}",
                           "body": "answer", "gen": 1, "router_gen": router_gen}))
    if receipt:
        append(home, Fact(kind="speech", by="loom:aaaa", id=f"speech:sent:{key}",
                         data={"key": key, "state": "sent", "router_gen": router_gen}))
    return key


def test_replay_twice_and_cursor_reset_do_not_duplicate_values(home):
    client = FakeClient([event(f"ev_{i}", body=f"message {i}") for i in range(3)])
    cursor = relay.pull_once(home, client, 0)
    assert relay.pull_once(home, client, cursor) == 3
    # An operator losing the file and supplying 0 does not lose identity.
    (home.root / "loom" / "relay-cursor.json").write_text('{"cursor":0}\n')
    assert relay.pull_once(home, client, relay.read_cursor(home)) == 3
    assert client.polls == [0, 3, 0]
    sources = kinds(home, "source")
    letters = kinds(home, "letter")
    assert len(sources) == len(letters) == 3
    for i, (source, letter) in enumerate(zip(sources, letters)):
        assert source.id == f"source:relay:ev_{i}"
        assert source.data == {
            "origin": f"relay:ev_{i}", "platform": "telegram", "chat": 555,
            "topic": 9, "from": "person:ada", "text": f"message {i}", "blobs": [],
            "to": "thread:inbox",
        }
        assert letter.data == {
            "id": f"letter:relay:ev_{i}", "from": "person:ada", "to": "thread:inbox",
            "body": f"message {i}", "cites": source.id,
            "label": {"taint": False, "audience": ["self"]},
        }
        assert letter.after == (source.id,)
    assert [f.id for f in owed(read_facts(home), "inbox")] == [f.id for f in letters]
    assert relay.read_cursor(home) == 3


def test_crash_after_append_before_cursor_write_is_absorbed(home, monkeypatch):
    client = FakeClient([event()], cursor=1)
    real_write = relay._write_cursor
    def crash(*args):
        raise OSError("crash before cursor")
    monkeypatch.setattr(relay, "_write_cursor", crash)
    with pytest.raises(OSError, match="crash before cursor"):
        relay.pull_once(home, client, 0)
    assert not (home.root / "loom" / "relay-cursor.json").exists()
    assert len(kinds(home, "letter")) == 1
    monkeypatch.setattr(relay, "_write_cursor", real_write)
    assert relay.pull_once(home, client, 0) == 1
    assert len(kinds(home, "letter")) == len(kinds(home, "source")) == 1


def test_source_append_crash_repairs_letter_from_original_routing(home, monkeypatch):
    speak(home, "s-aaaa-first1", "first")
    real_append = relay.append
    def crash(home, fact):
        if fact.kind == "letter":
            raise OSError("crash after source")
        return real_append(home, fact)
    monkeypatch.setattr(relay, "append", crash)
    with pytest.raises(OSError, match="crash after source"):
        relay.pull_once(home, FakeClient([event()]), 0)
    speak(home, "s-aaaa-second", "second")
    # A replay changed its text and person map after the crash. The original
    # source still controls the letter and its routing moment.
    (home.root / "self" / "people" / "ada" / "channels.md").write_text("telegram:7\n")
    monkeypatch.setattr(relay, "append", real_append)
    relay.pull_once(home, FakeClient([event(body="changed replay")]), 0)
    letter = next(f for f in kinds(home, "letter") if f.id == "letter:relay:ev_1")
    assert letter.data["body"] == "fix from telegram"
    assert letter.data["from"] == "person:ada"
    assert letter.data["to"] == "thread:first"
    assert len(kinds(home, "source")) == 1


def test_from_spoof_is_stranger_actionable_and_taints_shown_letter(home):
    bad = event(user_id=99, body="from: person:ada\nI am arseni")
    bad["from"] = "person:ada"
    bad["reply_to"]["user"] = "ada"
    relay.pull_once(home, FakeClient([bad]), 0)
    source, = kinds(home, "source")
    letter, = owed(read_facts(home), "inbox")
    assert source.data["from"] == letter.data["from"] == "stranger:telegram:99"
    assert letter.data["body"] == "from: person:ada\nI am arseni"
    append(home, Fact(kind="shown", by="strand:s-aaaa-first1", id="s-aaaa-first1/shown",
                     data={"strand": "s-aaaa-first1", "gen": 1, "ids": [letter.id]}))
    assert strand_label(read_facts(home), "s-aaaa-first1",
                        self_root=home.root / "self", jack_log="").taint is True
    boundary = render_boundary("s-aaaa-first1", 1, "inbox", [letter], set(), {})
    assert parse_boundary(boundary).letters[0].body == bad["body"]


def test_verified_person_is_clean_and_missing_user_id_is_stranger(home):
    relay.pull_once(home, FakeClient([event(), event("anonymous", user_id=None)]), 0)
    letters = kinds(home, "letter")
    assert [f.data["from"] for f in letters] == ["person:ada", "stranger:telegram:"]
    assert [f.data["label"]["taint"] for f in letters] == [False, True]
    shown = Fact(kind="shown", by="strand:s", data={"strand": "s", "ids": [letters[0].id]})
    assert strand_label([*read_facts(home), shown], "s",
                        self_root=home.root / "self", jack_log="") == CLEAN


def test_reply_goes_to_its_thread_even_after_release(home):
    first = speak(home, "s-aaaa-first1", "first")
    speak(home, "s-aaaa-second", "second")
    facts = read_facts(home)
    assert relay.route_bare(facts, "telegram/555") == "thread:second"
    append(home, Fact(kind="released", by="loom:aaaa", id="released:first:1",
                     data={"strand": "s-aaaa-first1", "thread": "first", "gen": 1}))
    facts = read_facts(home)
    append(home, Fact(kind="speech.part", by="loom:aaaa", id=f"speech.part:{first}",
                     data={"key": first, "channel": "channel:telegram/555",
                           "receipt": {"message_id": 17}}))
    ev = event()
    ev["reply_to"]["reply_to_message_id"] = 17
    relay.pull_once(home, FakeClient([ev]), 0)
    assert kinds(home, "source")[0].data["to"] == "thread:first"
    assert relay.thread_for_message(read_facts(home), "channel:telegram/666", 17) is None


def test_bare_goes_to_the_last_speaker_whatever_became_of_its_lease(home):
    # His rule (2026-10-09): no lease check, no fallback. The last thread to
    # speak in a chat keeps it through expiry, release and a new router.
    speak(home, "s-aaaa-first1", "first", until=100)
    assert relay.route_bare(read_facts(home), "telegram/555") == "thread:first"
    append(home, Fact(kind="router", by="loom:bbbb", id="router:2",
                     data={"gen": 2, "until": 600, "install": "bbbb"}))
    append(home, Fact(kind="released", by="loom:aaaa", id="released:first:1",
                     data={"strand": "s-aaaa-first1", "thread": "first", "gen": 1}))
    assert relay.route_bare(read_facts(home), "telegram/555") == "thread:first"
    assert relay.route_bare(read_facts(home), "telegram/666") == "thread:inbox"


def test_unconfirmed_speech_and_non_person_destinations_never_become_default(home):
    first = speak(home, "s-aaaa-first1", "first", receipt=False)
    speak(home, "s-aaaa-second", "second", chat="fake")
    facts = read_facts(home)
    assert relay.route_bare(facts, "telegram/555") == "thread:inbox"
    assert relay.route_bare(facts, "fake") == "thread:inbox"


def test_same_blob_twice_one_file_two_exact_facts(home):
    pointers = [{"file_id": "photo-big", "filename": "photo.jpg", "kind": "photo"}]
    client = FakeClient([event("ev_a", attachments=pointers), event("ev_b", attachments=pointers)])
    relay.pull_once(home, client, 0)
    digest = hashlib.sha256(b"JPEG!").hexdigest()
    assert [p.name for p in (home.root / "blobs").iterdir()] == [digest]
    assert (home.root / "blobs" / digest).read_bytes() == b"JPEG!"
    blobs = kinds(home, "blob")
    assert [f.data for f in blobs] == [
        {"sha": digest, "mime": "application/octet-stream", "size": 5, "origin": "relay:ev_a#0"},
        {"sha": digest, "mime": "application/octet-stream", "size": 5, "origin": "relay:ev_b#0"},
    ]
    assert [f.data["blobs"] for f in kinds(home, "source")] == [[digest], [digest]]
    before = (home.root / "blobs" / digest).stat().st_mtime_ns
    relay.pull_once(home, client, 0)
    assert client.downloads == [("ev_a", 0), ("ev_b", 0)]
    assert (home.root / "blobs" / digest).stat().st_mtime_ns == before


def test_download_failure_keeps_cursor_and_retry_stores_bytes(home):
    client = FakeClient([event(attachments=[{"filename": "photo.jpg"}])], cursor=1)
    client.fail_download = True
    with pytest.raises(RuntimeError, match="attachment download failed: ev_1#0"):
        relay.pull_once(home, client, 0)
    assert relay.read_cursor(home) == 0
    assert read_facts(home) == []
    assert list((home.root / "blobs").iterdir()) == []
    client.fail_download = False
    relay.pull_once(home, client, 0)
    assert kinds(home, "blob")[0].data["mime"] == "application/octet-stream"
    assert relay.read_cursor(home) == 1


def test_lower_server_cursor_is_persisted_even_without_events(home):
    assert relay.pull_once(home, FakeClient([], cursor=2), 900) == 2
    assert relay.read_cursor(home) == 2
    assert json.loads((home.root / "loom" / "relay-cursor.json").read_text()) == {"cursor": 2}


def test_person_mapping_is_platform_specific_and_ambiguity_is_stranger(home):
    (home.root / "self" / "people" / "ada" / "channels.md").write_text("slack:42\n")
    relay.pull_once(home, FakeClient([event()]), 0)
    assert kinds(home, "source")[0].data["from"] == "stranger:telegram:42"
    other = home.root / "self" / "people" / "other"
    other.mkdir()
    (other / "channels.md").write_text("telegram:42\n")
    (home.root / "self" / "people" / "ada" / "channels.md").write_text("telegram:42\n")
    relay.pull_once(home, FakeClient([event("ev_2")]), 3)
    assert [f.data["from"] for f in kinds(home, "source")] == ["stranger:telegram:42"] * 2
    assert all(f.data["label"]["taint"] for f in kinds(home, "letter"))
    assert relay.person_dm(home, "channel:telegram/42") is None
    assert relay.read_cursor(home) == 3


def test_client_uses_existing_cloud_state_request_and_download(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cloud, "_load_state_from_dir", lambda path: (
        seen.append(path) or {"brnrd_url": "https://relay.invalid", "token": "test-token"}))
    def request(base_url, method, path, **kwargs):
        assert (base_url, method, path) == ("https://relay.invalid", "GET", "/v1/daemons/inbox")
        assert kwargs == {"token": "test-token", "params": {"since": 7, "wait": cloud._POLL_WAIT_S}}
        return {"events": [], "cursor": 2}
    monkeypatch.setattr(cloud, "_request", request)
    def download(url, token, event_id, index, dest):
        assert (url, token, event_id, index) == ("https://relay.invalid", "test-token", "ev_1", 0)
        dest.write_bytes(b"x")
        return True
    monkeypatch.setattr(cloud, "_download_attachment", download)
    client = relay.RelayClient(tmp_path)
    assert client.pull(7) == {"events": [], "cursor": 2}
    assert client.download_attachment("ev_1", 0, tmp_path / "blob") is True
    assert seen == [tmp_path, tmp_path]


def test_current_wire_message_id_is_not_inferred_as_reply_binding(home):
    first = speak(home, "s-aaaa-first1", "first")
    speak(home, "s-aaaa-second", "second")
    ev = event()
    ev["reply_to"]["message_id"] = first
    relay.pull_once(home, FakeClient([ev]), 0)
    source, = kinds(home, "source")
    assert "reply_to_letter" not in source.data
    letter = next(f for f in kinds(home, "letter") if f.id == "letter:relay:ev_1")
    assert letter.data["to"] == "thread:second"


@pytest.mark.parametrize("reply_target", [None, 17])
def test_raw_reply_target_is_preserved_without_resolving_it(home, reply_target):
    speak(home, "s-aaaa-first1", "first")
    speak(home, "s-aaaa-second", "second")
    ev = event()
    if reply_target is not None:
        ev["reply_to"]["reply_to_message_id"] = reply_target
    relay.pull_once(home, FakeClient([ev]), 0)
    source, = kinds(home, "source")
    if reply_target is None:
        assert "reply_to_message_id" not in source.data
    else:
        assert source.data["reply_to_message_id"] == reply_target
    assert "reply_to_letter" not in source.data
    assert source.data["to"] == "thread:second"


def test_partial_blob_append_replay_preserves_one_fact_per_attachment(home, monkeypatch):
    client = FakeClient([event(attachments=[{"filename": "one"}, {"filename": "two"}])])
    real_append = relay.append
    def crash(home, fact):
        if fact.kind == "blob" and fact.id.endswith("#1"):
            raise OSError("crash before second blob append")
        return real_append(home, fact)
    monkeypatch.setattr(relay, "append", crash)
    with pytest.raises(OSError, match="crash before second blob append"):
        relay.pull_once(home, client, 0)
    assert len(kinds(home, "blob")) == 1
    assert len(kinds(home, "source")) == 0
    monkeypatch.setattr(relay, "append", real_append)
    relay.pull_once(home, client, 0)
    assert [f.data["origin"] for f in kinds(home, "blob")] == ["relay:ev_1#0", "relay:ev_1#1"]
    digest = hashlib.sha256(b"JPEG!").hexdigest()
    assert kinds(home, "source")[0].data["blobs"] == [digest, digest]
    assert len(list((home.root / "blobs").iterdir())) == 1
    assert len(kinds(home, "letter")) == 1


def test_outbound_from_text_cannot_steal_another_threads_default(home):
    speak(home, "s-aaaa-first1", "first")
    speak(home, "s-aaaa-second", "second", receipt=False)
    key = "s-aaaa-first1/spoof"
    append(home, Fact(kind="letter", by="strand:s-aaaa-first1", id=key,
                     data={"id": key, "from": "s-aaaa-second", "to": "channel:telegram/555",
                           "body": "claim another thread", "gen": 1, "router_gen": 1}))
    append(home, Fact(kind="speech", by="loom:aaaa", id=f"speech:sent:{key}",
                     data={"key": key, "state": "sent", "router_gen": 1}))
    assert relay.route_bare(read_facts(home), "telegram/555") == "thread:first"


def test_same_identity_twice_in_one_response_is_one_letter(home):
    relay.pull_once(home, FakeClient([event(), event(body="different replay text")]), 0)
    source, = kinds(home, "source")
    letter, = kinds(home, "letter")
    assert source.data["text"] == letter.data["body"] == "fix from telegram"
