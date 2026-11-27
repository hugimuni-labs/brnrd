"""Open a letter, forward it, or send it back. A refusal leaves the owner."""
from brr.daemon2.facts import Fact
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.owners import letters_for
from brr.loom.runtime.selfrepo import room
from test_wake import commit
from test_wishes import command, ledger  # noqa: F401


def _lease(home, thread, strand):
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": thread, "strand": strand, "gen": 1,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))


def _owed(home, thread):
    facts = read_facts(home)
    return facts, {fact.id for fact in letters_for(facts, main_snapshot(home), thread)}


def _lines(facts):
    return [fact.data.get("body", "").splitlines()[0] for fact in facts
            if fact.kind == "letter" and fact.data.get("from") == "loom"]


def _routes(facts):
    return [fact for fact in facts if fact.kind == "letter.routed"]


def test_open_prints_the_body_and_records_the_read(ledger):
    home, seat = ledger
    ident = inject_letter(home.root, to="thread:work", body="the words")
    assert command(home, seat, "letter", ident) == "the words"
    facts, owed = _owed(home, "work")
    assert ident in owed
    reads = [fact for fact in facts if fact.kind == "letter.read"]
    assert len(reads) == 1 and reads[0].data == {"letter": ident, "strand": "s-aaaa-test"}
    assert next(fact for fact in facts if fact.id == ident).data["to"] == "thread:work"
    assert not any(fact.kind == "shown" for fact in facts)


def test_an_unread_forward_moves_the_letter(ledger):
    home, seat = ledger
    ident = inject_letter(home.root, to="thread:work", body="move me")
    command(home, seat, "send", "--re", ident, "--to", "thread:other", "--forward")
    facts = read_facts(home)
    main = main_snapshot(home)
    assert ident in {fact.id for fact in letters_for(facts, main, "other")}
    assert ident not in {fact.id for fact in letters_for(facts, main, "work")}
    route = _routes(facts)[0]
    assert route.data["how"] == "forward" and route.data["read"] is False
    assert route.data["to"] == "thread:other" and route.data["strand"] == "s-aaaa-test"
    assert f"forwarded {ident} by work, unread" in _lines(facts)
    assert not any(fact.data.get("header_for") for fact in facts)


def test_the_inbox_can_take_a_letter_another_thread_owns(ledger):
    home, seat = ledger
    inbox = room(home.root, "s-bbbb-inbox").parent
    stranger = room(home.root, "s-cccc-none").parent
    _lease(home, "inbox", "s-bbbb-inbox")
    ident = inject_letter(home.root, to="thread:work", body="theirs")
    refused = command(home, stranger, "send", "--re", ident, "--to", "thread:other",
                      "--forward", ok=False)
    assert "holds neither" in refused and _routes(read_facts(home)) == []
    command(home, inbox, "send", "--re", ident, "--to", "thread:other", "--forward")
    facts = read_facts(home)
    main = main_snapshot(home)
    assert ident in {fact.id for fact in letters_for(facts, main, "other")}
    assert ident not in {fact.id for fact in letters_for(facts, main, "work")}
    route = _routes(facts)[0]
    assert route.data["shown"] is False and route.data["read"] is False
    assert route.data["strand"] == "s-bbbb-inbox"
    lines = _lines(facts)
    assert f"taken {ident} by inbox to other" in lines
    assert f"forwarded {ident} by inbox, unread" in lines
    assert not any(fact.data.get("header_for") for fact in facts)
    # The owner's seat is not the one that moved it, and it still cannot.
    again = command(home, seat, "send", "--re", ident, "--to", "thread:work", "--forward", ok=False)
    assert "holds neither" in again and len(_routes(read_facts(home))) == 1


def test_a_shown_letter_is_recorded_when_the_inbox_takes_it(ledger):
    home, _seat = ledger
    inbox = room(home.root, "s-bbbb-inbox").parent
    _lease(home, "inbox", "s-bbbb-inbox")
    ident = inject_letter(home.root, to="thread:work", body="seen")
    append(home, Fact(kind="shown", by="loom:aaaa", id="shown:work", data={
        "strand": "s-aaaa-test", "gen": 1, "ids": [ident],
    }))
    command(home, inbox, "send", "--re", ident, "--to", "thread:other", "--forward")
    route = _routes(read_facts(home))[0]
    assert route.data["shown"] is True


def test_a_return_puts_the_body_on_the_inbox(ledger):
    home, seat = ledger
    ident = inject_letter(home.root, to="thread:work", body="back to you")
    command(home, seat, "send", "--re", ident, "--off-topic")
    facts = read_facts(home)
    main = main_snapshot(home)
    assert ident in {fact.id for fact in letters_for(facts, main, "inbox")}
    assert ident not in {fact.id for fact in letters_for(facts, main, "work")}
    letter = next(fact for fact in facts if fact.id == ident)
    assert letter.data["body"] == "back to you" and letter.data["to"] == "thread:work"
    route = _routes(facts)[0]
    assert route.data["how"] == "off-topic" and route.data["to"] == "thread:inbox"
    assert f"returned {ident} off topic" in _lines(facts)


def test_a_thread_already_in_the_chain_keeps_its_owner(ledger):
    home, seat = ledger
    inbox = room(home.root, "s-bbbb-inbox").parent
    _lease(home, "inbox", "s-bbbb-inbox")
    ident = inject_letter(home.root, to="thread:work", body="stay")
    command(home, seat, "send", "--re", ident, "--to", "thread:other", "--forward")
    refused = command(home, inbox, "send", "--re", ident, "--to", "thread:work",
                      "--forward", ok=False)
    assert "citing" in refused and "new letter" in refused
    facts = read_facts(home)
    assert len(_routes(facts)) == 1
    assert ident in {fact.id for fact in letters_for(facts, main_snapshot(home), "other")}


def test_a_forward_after_a_return_waits_for_a_read(ledger):
    home, seat = ledger
    inbox = room(home.root, "s-bbbb-inbox").parent
    _lease(home, "inbox", "s-bbbb-inbox")
    ident = inject_letter(home.root, to="thread:work", body="judge this")
    early = command(home, inbox, "send", "--re", ident, "--off-topic", ok=False)
    assert "only the strand" in early and _routes(read_facts(home)) == []
    command(home, seat, "send", "--re", ident, "--off-topic")
    refused = command(home, inbox, "send", "--re", ident, "--to", "thread:other",
                      "--forward", ok=False)
    assert "brnrd runtime letter" in refused
    facts = read_facts(home)
    assert len(_routes(facts)) == 1
    assert ident in {fact.id for fact in letters_for(facts, main_snapshot(home), "inbox")}
    assert command(home, inbox, "letter", ident) == "judge this"
    command(home, inbox, "send", "--re", ident, "--to", "thread:other", "--forward")
    facts = read_facts(home)
    routes = _routes(facts)
    assert len(routes) == 2 and routes[-1].data["read"] is True and routes[-1].data["how"] == "forward"
    assert f"forwarded {ident} by inbox" in _lines(facts)
    assert f"forwarded {ident} by inbox, unread" not in _lines(facts)
    assert ident in {fact.id for fact in letters_for(facts, main_snapshot(home), "other")}
    assert ident not in {fact.id for fact in letters_for(facts, main_snapshot(home), "inbox")}


def test_a_settled_target_is_refused_and_the_owner_stays(ledger):
    home, seat = ledger
    shut = home.thread_dir("shut")
    shut.mkdir(parents=True)
    (shut / "README.md").write_text(
        "---\nid: shut\nstatus: settled\ntense: plan\n---\n# Shut\n", encoding="utf-8")
    commit(home.root / "self", "settle shut")
    ident = inject_letter(home.root, to="thread:work", body="not there")
    refused = command(home, seat, "send", "--re", ident, "--to", "thread:shut",
                      "--forward", ok=False)
    assert "not an open thread" in refused
    facts = read_facts(home)
    assert _routes(facts) == []
    assert ident in {fact.id for fact in letters_for(facts, main_snapshot(home), "work")}
