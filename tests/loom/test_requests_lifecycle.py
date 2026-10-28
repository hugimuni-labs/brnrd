"""One lifecycle for both directions: an ask closes on its answer, a delivered wish expires."""
from datetime import datetime, timedelta, timezone

from brr.daemon2.facts import Fact
from brr.loom.runtime.connector import _agree
from brr.loom.runtime.wishes import progress, wishes

NOW = datetime.now(timezone.utc)


def at(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def letter(ident, when, *, by="loom:aaaa", sender="person:arseni", re=None, links=()):
    data = {"id": ident, "from": sender, "to": "thread:work", "body": "x"}
    if re:
        data["re"] = re
    if links:
        data["links"] = list(links)
    return Fact(kind="letter", by=by, id=ident, at=when, data=data)


def lease(strand, thread, when):
    return Fact(kind="lease", by="loom:aaaa", at=when, data={"strand": strand, "thread": thread, "gen": 1})


def wish(ident, when, *, sender="person", says=(), thread="work", parent="", by="strand:s-aaaa-work"):
    data = {"wish": ident, "from": sender, "line": ident, "thread": thread,
            "says": [{"letter": s} for s in says], **({"parent": parent} if parent else {})}
    return Fact(kind="wish", by=by, id=ident if not says or when != "restamp" else ident + "-" + when,
                at=when, data=data)


def delivered(ident, when):
    return Fact(kind="wish.result", by="strand:s-aaaa-work", at=when,
                data={"wish": ident, "kind": "pr", "ref": "#1", "line": "PR #1", "delivered": True})


def base(hours_ago=10):
    return [lease("s-aaaa-work", "work", at(hours_ago + 1)), letter("L1", at(hours_ago + 1)),
            wish("w-want", at(hours_ago), says=["L1"]),
            wish("w-ask", at(hours_ago), sender="resident", parent="w-want")]


def test_an_ask_is_open_until_a_letter_of_the_person_replies_to_it():
    facts = base()
    assert wishes(facts)["w-ask"].state == "heard" and wishes(facts)["w-ask"].open
    facts.append(letter("L2", at(5), re="w-ask"))
    ask = wishes(facts)["w-ask"]
    assert (ask.state, ask.answer, ask.open) == ("answered", "L2", False)


def test_a_reply_to_the_letter_that_raised_an_ask_answers_it():
    raised = letter("R1", at(6), by="strand:s-aaaa-work", sender="s-aaaa-work", links=["w-ask"])
    facts = [*base(), raised]
    # The asking thread's own raise links the ask and is no answer.
    assert wishes(facts)["w-ask"].state == "heard"
    facts.append(letter("L3", at(5), re="R1"))
    assert wishes(facts)["w-ask"].answer == "L3"


def test_a_letter_before_the_ask_never_answers_it():
    facts = [*base(), letter("L0", at(20), re="w-ask")]
    assert wishes(facts)["w-ask"].state == "heard"


def test_a_delivered_wish_expires_after_three_days_and_counts_done():
    facts = [*base(100), delivered("w-want", at(80))]
    found = wishes(facts)
    assert found["w-want"].state == "expired" and not found["w-want"].open
    assert progress(found, "work")["done"] == 1
    fresh = wishes([*base(100), delivered("w-want", at(70))])
    assert fresh["w-want"].state == "delivered" and fresh["w-want"].open


def test_a_person_letter_stamped_after_the_delivery_keeps_it_delivered():
    restamp = Fact(kind="wish", by="strand:s-aaaa-work", id="restamp", at=at(10), data={
        "wish": "w-want", "from": "person", "line": "w-want", "thread": "work", "says": [{"letter": "L9"}]})
    facts = [*base(100), delivered("w-want", at(80)), letter("L9", at(11)), restamp]
    assert wishes(facts)["w-want"].state == "delivered"


def test_an_explicit_accept_skips_expiry():
    verdict = Fact(kind="wish.verdict", by="person:arseni", at=at(79),
                   data={"wish": "w-want", "as": "accepted", "evidence": "word", "ref": "L1"})
    assert wishes([*base(100), delivered("w-want", at(80)), verdict])["w-want"].state == "accepted"


def test_a_bare_ok_to_a_delivery_letter_is_the_agree():
    facts = [*base(100), delivered("w-want", at(80))]
    told = letter("D1", at(79), by="strand:s-aaaa-work", sender="s-aaaa-work", links=["w-want"])
    assert _agree(facts, told, " OK! ") == {"verdict": "agree", "wish": "w-want"}
    assert _agree(facts, told, "ok but the button is wrong") == {}
    other = letter("D2", at(79), by="strand:s-aaaa-work", sender="s-aaaa-work", links=["w-ask"])
    assert _agree(facts, other, "ok") == {}


def test_a_delivery_writes_the_person_a_letter_on_the_chat_they_said_it_in(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import brr.loom.runtime.port as port
    import brr.loom.runtime.project as project
    import brr.loom.runtime.verbs_loom as verbs_loom
    from brr.loom.runtime import verbs_wishes
    sent = []
    monkeypatch.setattr(verbs_wishes, "read_facts", lambda home: [])
    monkeypatch.setattr(project, "reply_channels", lambda facts, home: {"L1": "channel:mine/42"})
    monkeypatch.setattr(port, "write_send", lambda room, **kw: sent.append(kw))
    monkeypatch.setattr(verbs_loom, "_room", lambda raw: tmp_path / "s-aaaa-work")
    monkeypatch.setattr(verbs_wishes, "strand_of_room", lambda room: "s-aaaa-work")
    found = wishes(base())
    result = {"line": "PR #1", "ref": "#1"}
    verbs_wishes._tell_delivered(None, SimpleNamespace(room=None), found["w-want"], result)
    verbs_wishes._tell_delivered(None, SimpleNamespace(room=None), found["w-ask"], result)
    assert len(sent) == 1 and sent[0]["to"] == "channel:mine/42"
    assert sent[0]["body"].startswith("delivered &w-want: w-want\nPR #1: #1\nReply ok")


def test_another_thread_mentioning_an_ask_does_not_answer_it():
    facts = [*base(), lease("s-aaaa-else", "else", at(9)),
             letter("M1", at(5), by="strand:s-aaaa-else", sender="s-aaaa-else", links=["w-ask"])]
    assert wishes(facts)["w-ask"].state == "heard"
    facts.append(letter("M2", at(4), by="strand:s-aaaa-else", sender="s-aaaa-else", re="w-ask"))
    assert wishes(facts)["w-ask"].answer == "M2"
