"""Loom step 5 slice 2: the loom speaks to people through the relay, at most once per part."""

from __future__ import annotations

import pytest

from brr.daemon2.facts import Fact
from brr.daemon2.leases import LocalLeaseAuthority
from brr.loom.runtime import speak as speak_mod
from brr.loom.runtime.channels import relay
from brr.loom.runtime.config import loom_clock
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.port import PortError, fact_from_port, parse_boundary, render_boundary, write_send
from brr.loom.runtime.project import fold
from brr.loom.runtime.router import _answers_event, _header


@pytest.fixture
def home(tmp_path):
    h = Home(tmp_path / "home")
    h.root.mkdir(parents=True)
    person = h.root / "self" / "people" / "ada"
    person.mkdir(parents=True)
    (person / "channels.md").write_text("telegram:42\n")
    return h


@pytest.fixture
def lease(home):
    authority = LocalLeaseAuthority(home.root / "ledger" / "leases", clock=loom_clock)
    got = authority.acquire("router", home.install_id(), 600)
    assert got is not None
    return got


class FakeClient:
    def __init__(self, fail_on: set[int] | None = None):
        self.sent: list[dict] = []
        self.fail_on = fail_on or set()

    def send(self, payload):
        n = len(self.sent) + 1
        self.sent.append(payload)
        if n in self.fail_on:
            raise RuntimeError("connection reset after write")
        return {"message_id": 1000 + n}


@pytest.fixture
def client(monkeypatch):
    c = FakeClient()
    monkeypatch.setitem(speak_mod.EFFECTS, "relay", relay.make_effect(c))
    return c


def test_long_answer_splits_and_a_lost_part_is_never_resent(home, lease, monkeypatch):
    c = FakeClient(fail_on={2})
    monkeypatch.setitem(speak_mod.EFFECTS, "relay", relay.make_effect(c))
    body = "\n".join(f"line {i:04d} " + "x" * 80 for i in range(100))  # ~9,000 chars
    status, fact, unsure = speak_mod.speak(home, lease, "s-a/answer", body,
                                           channel="channel:telegram/42")
    assert len(c.sent) == 3
    assert status == "maybe-sent" and fact.data["state"] == "intended"
    assert unsure == ["s-a/answer#2"]
    assert "".join(p["body_markdown"] for p in c.sent).replace("\n", "") == body.replace("\n", "")
    assert all(len(p["body_markdown"]) <= speak_mod.LIMITS["telegram"] for p in c.sent)
    parts = {f.data["part"]: f.data["receipt"] for f in read_facts(home) if f.kind == "speech.part"}
    assert set(parts) == {"s-a/answer#1", "s-a/answer#3"}
    assert parts["s-a/answer#1"]["message_id"] == 1001
    # A rerun sends nothing: parts 1 and 3 are sent, part 2 is intended.
    speak_mod.speak(home, lease, "s-a/answer", body, channel="channel:telegram/42")
    assert len(c.sent) == 3


def test_answer_to_an_event_posts_its_event_id(home, lease, client):
    status, *_ = speak_mod.speak(home, lease, "s-a/re", "hi", channel="channel:telegram/42",
                                context={"event_id": "ev_7"})
    assert status == "sent"
    assert client.sent == [{"body_markdown": "hi", "event_id": "ev_7"}]


def test_unprompted_send_names_the_platform_never_a_chat(home, lease, client):
    speak_mod.speak(home, lease, "s-a/hey", "hey", channel="channel:telegram/42")
    assert client.sent == [{"body_markdown": "hey", "platform": "telegram"}]


def test_only_a_known_persons_direct_chat_is_speakable(home):
    assert relay.person_dm(home, "channel:telegram/42") == "person:ada"
    assert relay.person_dm(home, "channel:telegram/-100123") is None  # a group
    assert relay.person_dm(home, "channel:whatsapp/42") is None
    assert relay.person_dm(home, "channel:fake") is None


def test_header_and_event_resolution_from_the_fold(home):
    append(home, Fact(kind="letter", by="loom:aaaa", id="letter:relay:ev_9",
                      data={"id": "letter:relay:ev_9", "from": "person:ada",
                            "to": "thread:inbox", "body": "q",
                            "cites": "source:relay:ev_9"}))
    append(home, Fact(kind="router", by="loom:aaaa", id="router:1",
                      data={"gen": 1, "until": loom_clock() + 600, "install": "aaaa"}))
    append(home, Fact(kind="lease", by="loom:aaaa", id="lease:inbox:1",
                      data={"thread": "inbox", "strand": "s-aaaa-one", "gen": 1,
                            "router_gen": 1, "install": "aaaa"}))
    state = fold(read_facts(home))
    answer = Fact(kind="letter", by="strand:s-aaaa-one", id="s-aaaa-one/a",
                  data={"id": "s-aaaa-one/a", "from": "s-aaaa-one",
                        "to": "channel:telegram/42", "re": "letter:relay:ev_9"})
    assert _answers_event(state, answer) == "ev_9"
    assert _header(state, answer) == "s-aaaa-one · inbox"


def test_port_accepts_relay_channels_and_refuses_unknown_kinds(tmp_path):
    room = tmp_path / "home" / "rooms" / "s-aaaa-one"
    (room / "port" / "out").mkdir(parents=True)
    for platform in ("whatsapp", "slack"):
        with pytest.raises(PortError):
            write_send(room, to=f"channel:{platform}/42", sender="s-aaaa-one", body="b")
    with pytest.raises(PortError):
        write_send(room, to="channel:email/x", sender="s-aaaa-one", body="b")
    with pytest.raises(PortError):
        write_send(room, to="channel:telegram/../../x", sender="s-aaaa-one", body="b")


def test_a_reply_binds_to_the_letter_its_message_carried(home, lease, client):
    speak_mod.speak(home, lease, "s-a/answer", "hi", channel="channel:telegram/42")
    from test_loom_relay import speak as spoke
    spoke(home, "s-a", "first", chat="telegram/42", receipt=False)
    facts = read_facts(home)
    assert relay.thread_for_message(facts, "channel:telegram/42", 1001) == "first"
    assert relay.thread_for_message(facts, "channel:telegram/42", "1001") == "first"
    # Same id, another chat or platform: binds nothing.
    assert relay.thread_for_message(facts, "channel:telegram/43", 1001) is None
    assert relay.thread_for_message(facts, "channel:whatsapp/42", 1001) is None
    assert relay.thread_for_message(facts, "channel:telegram/42", 9999) is None
    # A strand can't plant a receipt: only the loom's own speech.part counts.
    forged = Fact(kind="speech.part", by="strand:s-x", id="speech.part:forged",
                  data={"key": "s-x/evil", "channel": "channel:telegram/42",
                        "receipt": {"message_id": 7}})
    assert relay.thread_for_message([forged], "channel:telegram/42", 7) is None


def test_a_split_event_answer_responds_once_then_follows_with_messages(home, lease, client):
    body = "\n".join("y" * 90 for _ in range(90))  # > one Telegram part
    speak_mod.speak(home, lease, "s-a/long", body, channel="channel:telegram/42",
                    context={"event_id": "ev_1"})
    assert len(client.sent) >= 2
    assert client.sent[0].get("event_id") == "ev_1"
    assert all("event_id" not in p and p["platform"] == "telegram" for p in client.sent[1:])


def test_client_routes_answers_to_responses_and_the_rest_to_messages(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(relay.cloud, "_load_state_from_dir",
                        lambda d: {"brnrd_url": "https://relay", "token": "t"})
    monkeypatch.setattr(relay.cloud, "_request",
                        lambda url, method, path, **kw: calls.append((path, kw["json"])) or {})
    c = relay.RelayClient(tmp_path)
    c.send({"event_id": "ev_1", "body_markdown": "a"})
    c.send({"platform": "telegram", "body_markdown": "b"})
    assert calls == [
        ("/v1/daemons/responses", {"event_id": "ev_1", "body_markdown": "a", "status": "done"}),
        ("/v1/daemons/messages", {"platform": "telegram", "body_markdown": "b"}),
    ]


def test_response_without_message_id_has_no_binding_receipt(home, lease, monkeypatch):
    class ResponseClient:
        def send(self, payload):
            return {"event_id": payload["event_id"], "forwarded": True}
    monkeypatch.setitem(speak_mod.EFFECTS, "relay", relay.make_effect(ResponseClient()))
    status, *_ = speak_mod.speak(home, lease, "s-a/answer#2", "hi",
                               channel="channel:telegram/42", context={"event_id": "ev_1"})
    assert status == "sent"
    assert not any(f.kind == "speech.part" for f in read_facts(home))


def test_port_cannot_claim_another_strands_sender(home):
    from test_loom_relay import speak as spoke
    spoke(home, "s-writer", "first", receipt=False)
    spoke(home, "s-other", "second", receipt=False)
    letter = fact_from_port({"kind": "letter", "id": "s-writer/spoof",
                             "to": "channel:telegram/42", "from": "s-other",
                             "body": "hello"}, "s-writer", 1)
    assert letter.by == "strand:s-writer"
    assert letter.data["from"] == "s-writer"
    state = fold(read_facts(home))
    assert _header(state, letter) == "s-writer · first"
    boundary = render_boundary("s-reader", 1, "inbox", [letter], set(),
                               {"s-writer": "first", "s-other": "second"})
    shown, = parse_boundary(boundary).letters
    assert shown.sender == "s-writer" and shown.reply == "thread:first"
