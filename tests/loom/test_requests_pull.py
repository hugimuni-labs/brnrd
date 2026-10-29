"""Asks are a pull (requests R4): live, moved, stale; a rank word owes a top?; a done thread."""
from datetime import datetime, timedelta, timezone

from brr.loom.runtime.jack import _ageless
from brr.loom.runtime.ledger import Fact, append, inject_letter, read_facts
from brr.loom.runtime.pull import band_line, done_line, flavour, owed_tops, top_line
from brr.loom.runtime.wishes import wishes
from test_requests_raises import TO, asks, channel, person_wish
from test_wish_tree import body  # noqa: F401  (fixture)
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)

STRAND = "s-aaaa-test"


def pr(home, kind, number):
    append(home, Fact(kind=kind, by=f"strand:{STRAND}", data={
        "thread": "work", "repo": "o/r", "number": number, "head": "abc",
        **({"url": f"https://github.com/o/r/pull/{number}"} if kind == "pr.opened" else {"merge_commit": "def"})}))


def state(home, seat):
    facts = read_facts(home)
    found = wishes(facts)
    ask, = asks(home)
    return facts, found, ask


def test_an_ask_is_live_then_moved_by_its_pr_then_stale_when_it_merges(ledger):
    home, seat = ledger
    channel(home)
    person_wish(home, seat, "Ship the parser")
    command(home, seat, "send", "--to", TO, "#12 is green. Merge #12?")
    facts, found, ask = state(home, seat)
    assert flavour(facts, ask, found, seat) == ("live", "")
    pr(home, "pr.opened", 12)
    facts, found, ask = state(home, seat)
    assert flavour(facts, ask, found, seat) == ("moved", "#12 opened")
    # Another pull request is not this ask's news.
    pr(home, "pr.merged", 13)
    pr(home, "pr.merged", 12)
    facts, found, ask = state(home, seat)
    assert flavour(facts, ask, found, seat) == ("stale", "#12 merged")


def test_a_letter_of_theirs_moves_an_ask_and_a_closed_parent_or_thread_stales_it(ledger):
    home, seat = ledger
    channel(home)
    root = person_wish(home, seat, "Ship the parser")
    command(home, seat, "send", "--to", TO, "Merge it?")
    inject_letter(home.root, to="thread:work", body="back now")
    facts, found, ask = state(home, seat)
    assert flavour(facts, ask, found, seat) == ("moved", "a letter of theirs")
    assert flavour(facts, ask, found, seat, closed={"work"}) == ("stale", "work settled")
    command(home, seat, "wish", root, "--drop", "not wanted")
    facts, found, ask = state(home, seat)
    assert flavour(facts, ask, found, seat) == ("stale", f"{root} dropped")


def test_the_band_says_each_ask_and_only_its_flavour_is_news(ledger):
    home, seat = ledger
    channel(home)
    root = person_wish(home, seat, "Ship the parser")
    command(home, seat, "send", "--to", TO, "Merge #12?")
    facts, found, ask = state(home, seat)
    now = datetime.now(timezone.utc)
    line = band_line(facts, found, "work", "inbox", seat, now=now)
    assert line.startswith(f'asks: {ask.id} live blocks {root} "Merge #12?" 0h')
    later = band_line(facts, found, "work", "inbox", seat, now=now + timedelta(days=2))
    assert later.endswith(" 2d") and _ageless(later) == _ageless(line)
    assert band_line(facts, found, "other", "inbox", seat) == ""
    # The home thread sees every thread's asks, each with where it lives.
    assert " on work " in band_line(facts, found, "inbox", "inbox", seat)
    pr(home, "pr.merged", 12)
    facts, found, _ = state(home, seat)
    assert _ageless(band_line(facts, found, "work", "inbox", seat, now=now)) != _ageless(line)


def test_a_rank_word_owes_a_top_until_a_wish_is_topped_or_the_stop_asked(ledger):
    home, seat = ledger
    root = person_wish(home, seat, "Ship the parser")
    plain = inject_letter(home.root, to="thread:work", body="looks good")
    order = inject_letter(home.root, to="thread:work", body="Do the lexer first, then the rest")
    owed = owed_tops(read_facts(home), "work", STRAND)
    word = order.rpartition(":")[2]
    assert owed == [(word, "first")] and plain.rpartition(":")[2] not in str(owed)
    assert top_line(owed).startswith(f'top? {word} says "first"')
    command(home, seat, "wish", root, "--top", order)
    assert owed_tops(read_facts(home), "work", STRAND) == []
    again = inject_letter(home.root, to="thread:work", body="This is top priority")
    append(home, Fact(kind="left.asked", by=f"strand:{STRAND}", data={
        "strand": STRAND, "gen": 1, "wishes": [], "letters": [again.rpartition(":")[2]]}))
    assert owed_tops(read_facts(home), "work", STRAND) == []


def test_a_thread_is_done_when_every_request_on_it_is_shut(ledger):
    home, seat = ledger
    root = person_wish(home, seat, "Ship the parser")
    assert done_line(wishes(read_facts(home)), "work", "inbox") == ""
    command(home, seat, "wish", root, "--drop", "not wanted")
    assert done_line(wishes(read_facts(home)), "work", "inbox").startswith("done: no open request on work")
    assert done_line(wishes(read_facts(home)), "other", "inbox") == ""


def test_the_stop_holds_once_on_a_rank_word_and_the_band_is_news_on_change(body):
    from test_wish_tree import render, stop
    import json
    from brr.loom.runtime import jack
    from brr.loom.runtime.loom import _ingest
    home, seat = body
    channel(home)
    root = person_wish(home, seat, "Ship the parser")
    command(home, seat, "wish", root, "--left", "parser")
    order = inject_letter(home.root, to="thread:work", body="Lexer first please")
    word = order.rpartition(":")[2]
    assert f'top? {word} says "first"' in render(home)
    refused = json.loads(stop(seat))
    assert refused["decision"] == "block" and "an order word" in refused["reason"]
    # Stopped again with nothing done: let through, asked once as a fact through the port.
    stop(seat)
    _ingest(home)
    assert [f.data.get("letters") for f in read_facts(home) if f.kind == "left.asked"] == [[word]]
    assert not [line for line in render(home).splitlines() if line.startswith("top? ")]
    # The band is said at a post when a flavour changes, and not when only its age does.
    command(home, seat, "send", "--to", TO, "Merge #12?")
    # Every letter settled, so the band is the only thing a post could say.
    import re
    for ident in re.search(r"owed (.*) -->", render(home))[1].split():
        command(home, seat, "send", "--re", ident, "--to", "thread:work", "--chatter", "--note", "read")
    _ingest(home)
    render(home)
    jack.execute("post", seat, "{}")
    assert "asks: " not in jack.execute("post", seat, "{}")
    pr(home, "pr.merged", 12)
    render(home)
    assert "stale (#12 merged)" in jack.execute("post", seat, "{}")
