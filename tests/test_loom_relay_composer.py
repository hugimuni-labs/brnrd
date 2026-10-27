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


def test_unknown_thread_falls_back_to_the_home_thread(owner):
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


def verdict(ident="ev_v1", *, wish="w-board1", word="agree", login="arseni"):
    event = composer(ident, text="yes, that's it", login=login)
    event["reply_to"].update(wish=wish, verdict=word)
    return event


def wish(home, *, delivered=True):
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append
    append(home, Fact(kind="wish", by="person:arseni", id="w-board1", data={
        "wish": "w-board1", "says": [["arseni/board", 0, None]], "from": "person", "line": "A board", "thread": "first"}))
    append(home, Fact(kind="wish.result", by="strand:s-aaaa-first1", id="wish.result:b1", data={
        "wish": "w-board1", "kind": "pr", "ref": "#1", "line": "the board", "delivered": delivered}))


def said(home):
    return [f for f in kinds(home, "letter") if f.data["from"].startswith("person:")]


def strangers(home):
    return [f for f in kinds(home, "letter") if f.data["from"].startswith("stranger:")]


def owed(home, thread):
    from brr.loom.runtime.lifecycle import main_snapshot
    from brr.loom.runtime.owners import letters_for
    return letters_for(read_facts(home), main_snapshot(home), thread)


def seat(home, thread, strand):
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append
    if not kinds(home, "router"):
        append(home, Fact(kind="router", by="loom:aaaa", id="router:1",
                          data={"gen": 1, "until": 4e9, "install": "aaaa"}))
    append(home, Fact(kind="lease", by="loom:aaaa", id=f"lease:{thread}:1",
                      data={"thread": thread, "strand": strand, "gen": 1, "router_gen": 1, "install": "aaaa"}))


def show(home, strand, letters):
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append
    append(home, Fact(kind="shown", by=f"strand:{strand}", id=f"{strand}/shown",
                      data={"strand": strand, "gen": 1, "ids": [f.id for f in letters]}))


# The local board (threads/live-board/board.py) writes every letter to the inbox;
# brnrd.dev writes the thread its composer was open on. Either way the wish decides.
@pytest.mark.parametrize("given_on", ["inbox", "second"])
def test_an_agree_is_a_notice_on_the_wishs_thread_and_the_inbox_owed_by_nobody(owner, given_on):
    wish(owner)
    event = verdict()
    event["reply_to"]["thread"] = given_on
    relay.pull_once(owner, FakeClient([event]), 0)
    letter, = said(owner)
    assert letter.data["to"] == "thread:first"
    assert kinds(owner, "wish.verdict")[0].data["as"] == "accepted"
    first, = owed(owner, "first")
    inbox, = owed(owner, "inbox")
    assert owed(owner, "second") == []
    for notice in (first, inbox):
        assert notice.data["from"] == "loom" and notice.data["notice"] is True
        assert notice.data["body"].startswith('verdict: agree on w-board1 "A board" from person:arseni')
        assert "nothing owed" in notice.data["body"]
    # Shown once to a body on its thread, a notice is done; the other still waits for its reader.
    seat(owner, "first", "s-aaaa-first1")
    show(owner, "s-aaaa-first1", [first])
    assert owed(owner, "first") == [] and owed(owner, "inbox") == [inbox]


def test_a_correct_is_owed_on_the_wishs_thread_and_only_noticed_by_the_inbox(owner):
    wish(owner)
    event = verdict(word="correct")
    event["reply_to"]["thread"] = "inbox"
    relay.pull_once(owner, FakeClient([event]), 0)
    letter, = said(owner)
    assert owed(owner, "first") == [letter]
    notice, = owed(owner, "inbox")
    assert notice.data["notice"] is True and "the letter is owed on thread first" in notice.data["body"]
    seat(owner, "first", "s-aaaa-first1")
    show(owner, "s-aaaa-first1", [letter])
    seat(owner, "inbox", "s-aaaa-inbox1")
    show(owner, "s-aaaa-inbox1", [notice])
    assert owed(owner, "first") == [letter]  # shown is not answered: his correction still asks for work
    assert owed(owner, "inbox") == []


def test_a_correct_on_a_settled_thread_is_owed_by_the_inbox(owner):
    # Silence settles a thread whose wishes are only delivered; the router skips
    # closed threads, so his correction would otherwise wait on nobody.
    wish(owner)
    readme = owner.thread_dir("first") / "README.md"
    readme.write_text(readme.read_text().replace("status: open", "status: settled"))
    relay.pull_once(owner, FakeClient([verdict(word="correct")]), 0)
    notice, = owed(owner, "inbox")
    assert notice.data["from"] == "loom" and "thread first is settled" in notice.data["body"]
    seat(owner, "inbox", "s-aaaa-inbox1")
    show(owner, "s-aaaa-inbox1", [notice])
    assert owed(owner, "inbox") == [notice]  # shown is not answered: it asks for work


def test_a_correct_on_a_settled_thread_climbs_to_its_open_opener_alone(owner):
    wish(owner)
    readme = owner.thread_dir("first") / "README.md"
    readme.write_text(readme.read_text().replace("status: open", "status: settled\nopened-by: thread:second"))
    relay.pull_once(owner, FakeClient([verdict(word="correct")]), 0)
    from brr.loom.runtime.lifecycle import main_snapshot
    from brr.loom.runtime.owners import active_owners
    letter, = said(owner)
    fate, = [f for f in active_owners(read_facts(owner), main_snapshot(owner)) if f.letter.id == letter.id]
    assert (fate.action, fate.thread) == ("climb", "second")  # the router lands it there
    notice, = owed(owner, "inbox")  # the passive notice of a correct, as on any thread
    assert notice.data["notice"] is True and "owed on thread first" in notice.data["body"]


def test_a_verdict_on_an_inbox_wish_is_one_notice_there(owner):
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append
    append(owner, Fact(kind="wish", by="person:arseni", id="w-board1", data={
        "wish": "w-board1", "says": [["arseni/board", 0, None]], "from": "person", "line": "A board", "thread": "inbox"}))
    relay.pull_once(owner, FakeClient([verdict(), verdict("ev_v2", word="correct")]), 0)
    agree, correct = said(owner)
    notice, = (f for f in kinds(owner, "letter") if f.data["from"] == "loom")
    assert notice.data["body"].startswith("verdict: agree")
    assert set(f.id for f in owed(owner, "inbox")) == {notice.id, correct.id}


def test_an_owners_agree_on_a_delivered_wish_accepts_it_once(owner):
    wish(owner)
    relay.pull_once(owner, FakeClient([verdict()]), 0)
    letter, = said(owner)
    fact, = kinds(owner, "wish.verdict")
    assert (fact.id, fact.by) == (f"wish.verdict:{letter.id}", "person:arseni")
    assert fact.data == {"wish": "w-board1", "as": "accepted", "evidence": "word", "ref": letter.id}
    # The relay forgets it delivered the event: the same drop is drained again.
    (relay.directories(owner)[1] / "messages.json").unlink()
    relay.pull_once(owner, FakeClient([verdict()]), 0)
    assert len(said(owner)) == len(kinds(owner, "wish.verdict")) == 1
    assert len(kinds(owner, "letter")) == 3  # his agree and one notice each to first and inbox


def test_a_strangers_agree_or_an_undelivered_wish_accepts_nothing(home, owner):
    wish(owner, delivered=False)
    relay.pull_once(owner, FakeClient([verdict(), verdict("ev_v2", login="ada")]), 0)
    assert len(said(owner)) == 1 and len(strangers(owner)) == 1
    assert kinds(owner, "wish.verdict") == []


def test_a_strangers_agree_on_a_delivered_wish_accepts_nothing(home):
    wish(home)
    relay.pull_once(home, FakeClient([verdict()]), 0)
    letter, = kinds(home, "letter")  # a stranger's verdict is news to nobody
    assert letter.data["from"].startswith("stranger:")
    assert kinds(home, "wish.verdict") == []


def test_a_correction_is_the_letter_on_the_wishs_thread(owner):
    wish(owner)
    relay.pull_once(owner, FakeClient([verdict(word="correct")]), 0)
    source, = kinds(owner, "source")
    letter, = said(owner)
    assert (letter.data["wish"], letter.data["verdict"], letter.data["to"]) == ("w-board1", "correct", "thread:first")
    assert (source.data["wish"], source.data["verdict"]) == ("w-board1", "correct")
    assert kinds(owner, "wish.verdict") == []


@pytest.mark.parametrize("wish_id, word", [
    ("w-board1", "maybe"), ("w-../x", "agree"), ("board1", "agree"), (None, "agree"), ("w-board1", None),
    ("w-board1\nfrom: person:ada", "agree")])
def test_a_bad_wish_or_verdict_is_refused(owner, wish_id, word):
    event = composer()
    event["reply_to"].update({k: v for k, v in (("wish", wish_id), ("verdict", word)) if v is not None})
    with pytest.raises(ValueError, match="unsafe loom wish"):
        relay.pull_once(owner, FakeClient([event]), 0)
    assert read_facts(owner) == []


def test_the_local_boards_agree_dropped_for_the_inbox_lands_on_the_wishs_thread(owner):
    """threads/live-board/board.py `send`: a chat-scoped board id, `thread: inbox`, dropped in the channel's folder."""
    from brr.loom.runtime.connector import drain
    from brr.loom.runtime.port import _front
    relay.enable(owner)
    (owner.root / "self/people/arseni/channels.toml").write_text('channels = ["relay:loom:arseni", "fake:7"]\n')
    wish(owner)
    drop = owner.root / "loom/connectors/fake/state/in"
    drop.mkdir(parents=True)
    fields = {"kind": "letter", "id": "7:board-abc123defg", "to": "7", "from": "7", "thread": "inbox",
              "wish": "w-board1", "verdict": "agree"}
    (drop / "7:board-abc123defg.md").write_text(_front(fields, "agree &w-board1"))
    drain(owner, "fake", drop)
    letter, = said(owner)
    assert (letter.data["from"], letter.data["to"]) == ("person:arseni", "thread:first")
    assert kinds(owner, "wish.verdict")[0].data["as"] == "accepted"
    assert [f.data["from"] for f in owed(owner, "first")] == ["loom"]
    assert [f.data["from"] for f in owed(owner, "inbox")] == ["loom"]
