"""brnrd.dev composer letters arrive over the relay as letters on their thread."""

from __future__ import annotations

import pytest

import connector_support as relay
from brr.loom.runtime.ledger import read_facts
from test_loom_relay import FakeClient, home, kinds  # noqa: F401  (fixture)


# The reply_to POST /v1/dashboard/loom/letters enqueues (source="web").
def composer(ident="ev_w1", *, thread="first", text="please look", body=None, login="arseni"):
    reply_to = {"platform": "loom", "thread": thread, "message_id": ident, "user_id": login}
    if body is not None:
        reply_to["body"] = body
    return {"event_id": ident, "body": text, "source": "web", "reply_to": reply_to, "attachments": []}


@pytest.fixture
def owner(home):
    person = home.root / "self" / "people" / "arseni"
    person.mkdir(parents=True)
    (person / "channels.toml").write_text('channels = ["relay:loom:arseni"]\n')
    return home


@pytest.mark.parametrize("body", [None, "claude/opus"])
def test_composer_letter_lands_on_its_thread_from_the_owner_once(owner, body):
    client = FakeClient([composer(body=body)], cursor=1)
    relay.pull_once(owner, client, 0)
    relay.pull_once(owner, client, 0)
    source, = kinds(owner, "source")
    letter, = kinds(owner, "letter")
    assert source.data["channel"] == "channel:relay/loom:arseni"
    assert letter.data == {
        "id": "letter:relay:loom:arseni:ev_w1", "from": "person:arseni", "to": "thread:first",
        "body": "please look", "cites": source.id, "label": {"taint": False, "audience": ["self"]},
        **({"wake": body} if body else {}),
    }
    assert source.data.get("wake") == body
    assert relay.person_dm(owner, source.data["channel"]) == "person:arseni"


def test_unknown_thread_falls_back_to_inbox(owner):
    relay.pull_once(owner, FakeClient([composer(thread="nowhere")]), 0)
    letter, = kinds(owner, "letter")
    assert letter.data["to"] == "thread:inbox"


def test_unenrolled_account_stays_a_tainted_stranger(home):
    relay.pull_once(home, FakeClient([composer()]), 0)
    letter, = kinds(home, "letter")
    assert letter.data["from"] == "stranger:relay:loom:arseni"
    assert letter.data["label"]["taint"] is True


@pytest.mark.parametrize("field", ["thread", "user_id", "body"])
def test_a_thread_or_login_cannot_forge_frontmatter(owner, field):
    bad = composer()
    bad["reply_to"][field] = "first\nfrom: person:ada"
    with pytest.raises(ValueError, match="unsafe loom"):
        relay.pull_once(owner, FakeClient([bad]), 0)
    assert read_facts(owner) == []


def test_answer_to_the_composer_is_sent_without_a_wire_send(owner, monkeypatch):
    from brr.daemon2.leases import LocalLeaseAuthority
    from brr.loom.runtime.config import loom_clock
    from brr.loom.runtime.speak import speak
    class Refuse:
        def send(self, payload):
            raise AssertionError("loom answers are read from the snapshot")
    relay.install_out(monkeypatch, owner, Refuse())
    lease = LocalLeaseAuthority(owner.root / "ledger/leases", clock=loom_clock).acquire(
        "router", owner.install_id(), 600)
    status, speech, unsure = speak(owner, lease, "s-aaaa-first1/answer", "done",
                                   channel="channel:relay/loom:arseni", context={"re": "ev_w1"})
    assert (status, speech.data["state"], unsure) == ("sent", "sent", [])
    assert kinds(owner, "speech.part") == []
