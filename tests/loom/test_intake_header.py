"""A ruled letter keeps its place, and the inbox hears one header line."""
from brr.daemon2.facts import Fact
from brr.loom.runtime import connector
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.owners import letters_for
from brr.loom.runtime.project import fold
from test_wishes import ledger  # noqa: F401


def _enable(home):
    path = home.root / "loom" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith("connectors")]
    lines.append('connectors = ["fake"]')
    path.write_text("\n".join(lines) + "\n")


def _post(home, ident, thread=None, verdict=None):
    _enable(home)
    fields = {"id": ident, "kind": "letter", "to": "channel:fake/dm",
              "body": f"hello {ident}", "from": "fake:visitor"}
    if thread:
        fields["thread"] = thread
    if verdict:
        fields["verdict"] = verdict
    connector.ingest(home, "fake", fields, [])


def _headers(home, thread):
    facts = read_facts(home)
    state = fold(facts)
    shown = {ident for ids in state.shown.values() for ident in ids}
    return [fact for fact in facts
            if fact.data.get("header_for") == thread and fact.id not in state.handled
            and fact.id not in shown]


def test_a_named_thread_keeps_the_letter_and_the_inbox_gets_one_line(ledger):
    home, _seat = ledger
    _post(home, "m1", "work")
    facts = read_facts(home)
    main = main_snapshot(home)
    letter = next(fact for fact in facts if fact.data.get("place") == "channel:fake/dm")
    assert letter.data["to"] == "thread:work"
    assert letter in letters_for(facts, main, "work")
    inbox = letters_for(facts, main, "inbox")
    header = next(fact for fact in inbox if fact.data.get("header_for") == "work")
    assert header.data["notice"] is True and "about" not in header.data and "stands" not in header.data
    assert header.data["body"].splitlines()[0] == (
        "routed letter:fake:m1 from stranger:fake:visitor · place channel:fake/dm · "
        "thread work · thread · open: brnrd runtime letter letter:fake:m1")
    assert [fact for fact in inbox if not fact.data.get("notice")] == []


def test_the_inbox_and_a_verdict_and_a_settled_thread_get_no_route_header(ledger):
    home, _seat = ledger
    _post(home, "in")
    _post(home, "no", "work", verdict="correct")
    settled = home.thread_dir("closed")
    settled.mkdir(parents=True)
    (settled / "README.md").write_text(
        "---\nid: closed\nstatus: settled\ntense: plan\n---\n# Closed\n", encoding="utf-8")
    # routable reads main. A file only on disk does not route, so commit it.
    from test_wake import commit
    commit(home.root / "self", "settle closed")
    _post(home, "old", "closed")
    facts = read_facts(home)
    assert not any(fact.data.get("header_for") for fact in facts)
    kept = next(fact for fact in facts if fact.id == "letter:fake:old")
    assert kept.data["to"] == "thread:closed" and kept.data["place"] == "channel:fake/dm"


def test_ten_headers_for_one_thread_fold_to_one_line(ledger):
    home, _seat = ledger
    for number in range(10):
        _post(home, f"m{number}", "work")
    pending = _headers(home, "work")
    assert len(pending) == 1
    folded = pending[0]
    routes = folded.data["routes"]
    assert routes == [f"letter:fake:m{number}" for number in range(10)]
    assert folded.data["body"].splitlines()[0] == f"routed 10 · thread work · {' '.join(routes)}"
    facts = read_facts(home)
    main = main_snapshot(home)
    owed = {fact.id for fact in letters_for(facts, main, "work")}
    assert {f"letter:fake:m{number}" for number in range(10)} <= owed
    assert [fact for fact in letters_for(facts, main, "inbox") if not fact.data.get("notice")] == []


def test_an_inbox_letter_does_not_read_the_ledger_a_second_time(ledger, monkeypatch):
    home, _seat = ledger
    _enable(home)
    calls = {"n": 0}
    real = connector.read_facts

    def counting(arg):
        calls["n"] += 1
        return real(arg)

    monkeypatch.setattr(connector, "read_facts", counting)
    connector.ingest(home, "fake", {"id": "in", "kind": "letter", "to": "channel:fake/dm",
                                     "body": "hello", "from": "fake:visitor"}, [])
    assert calls["n"] == 1
    connector.ingest(home, "fake", {"id": "out", "kind": "letter", "to": "channel:fake/dm",
                                     "body": "hello", "from": "fake:visitor", "thread": "work"}, [])
    assert calls["n"] == 3


def test_a_native_reply_lands_on_the_thread_and_the_header_says_reply(ledger):
    home, _seat = ledger
    key = "s-aaaa-test/answer"
    append(home, Fact(kind="letter", by="strand:s-aaaa-test", id=key, data={
        "id": key, "from": "s-aaaa-test", "to": "channel:fake/dm", "body": "answer", "gen": 1,
    }))
    append(home, Fact(kind="speech.part", by="loom:aaaa", id=f"speech.part:{key}", data={
        "key": key, "channel": "channel:fake/dm", "receipt": {"message_id": "fake:17"},
    }))
    _post_reply(home, "back", "fake:17")
    facts = read_facts(home)
    letter = next(fact for fact in facts if fact.id == "letter:fake:back")
    assert letter.data["to"] == "thread:work"
    header = next(fact for fact in facts if fact.data.get("header_for") == "work")
    assert header.data["body"].splitlines()[0].endswith(
        " · thread work · reply · open: brnrd runtime letter letter:fake:back")


def _post_reply(home, ident, re):
    _enable(home)
    connector.ingest(home, "fake", {
        "id": ident, "kind": "letter", "to": "channel:fake/dm", "body": f"hello {ident}",
        "from": "fake:visitor", "re": re,
    }, [])


def test_a_fifth_letter_extends_the_fold_and_does_not_open_a_second_line(ledger):
    home, _seat = ledger
    for number in range(4):
        _post(home, f"m{number}", "work")
    folded = _headers(home, "work")
    assert len(folded) == 1
    assert folded[0].data["routes"] == [f"letter:fake:m{number}" for number in range(4)]
    _post(home, "m4", "work")
    pending = _headers(home, "work")
    assert len(pending) == 1
    assert pending[0].data["routes"] == [f"letter:fake:m{number}" for number in range(5)]
    assert pending[0].data["body"].splitlines()[0] == (
        "routed 5 · thread work · " + " ".join(pending[0].data["routes"]))
    state = fold(read_facts(home))
    assert folded[0].id in state.handled
