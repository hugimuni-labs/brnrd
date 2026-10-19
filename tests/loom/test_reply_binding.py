"""A native quoted reply keeps one binding through routing and body delivery."""

import json
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.connector import ingest, route_bare
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _boundary, _prepare
from brr.loom.runtime.port import parse_boundary
from brr.loom.runtime.project import fold, sender_threads
from brr.loom.runtime.router import Router, _header

from _step import mail, write_thread

CHANNEL = "channel:mine/42"
FIRST = "s-aaaa-first/answer"
READER = "s-aaaa-reader"


@pytest.fixture
def home(tmp_path):
    home = Home(tmp_path / "home", install="aaaa")
    for thread in ("inbox", "first", "second"):
        write_thread(home.root, thread, thread.title(), "quit-now")
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake", "other=fake"]\n')
    person = home.root / "self/people/ada"
    person.mkdir(parents=True)
    (person / "channels.toml").write_text('channels = ["mine:42"]\n')
    append(home, Fact(kind="router", by="loom:aaaa", id="router:1", data={"gen": 1}))
    return home


def draft(home, thread, *, sent=True):
    strand = f"s-aaaa-{thread}"
    key = strand + "/answer"
    append(home, Fact(kind="lease", by="loom:aaaa", id=f"lease:{thread}", data={
        "strand": strand, "thread": thread, "gen": 1, "router_gen": 1}))
    append(home, Fact(kind="letter", by=f"strand:{strand}", id=key, data={
        "id": key, "from": strand, "to": CHANNEL, "gen": 1, "router_gen": 1,
        "body": f"The {thread} answer\nMore detail"}))
    if sent:
        append(home, Fact(kind="speech", by="loom:aaaa", id=f"speech:{key}", data={
            "key": key, "state": "sent", "router_gen": 1}))
    return key


def receipt(home, native, key=FIRST, *, channel=CHANNEL, by="loom:aaaa"):
    append(home, Fact(kind="speech.part", by=by, id=f"part:{native}", data={
        "key": key, "channel": channel, "receipt": {"message_id": native}}))


def prepare(home, facts, thread):
    append(home, Fact(kind="lease", by="loom:aaaa", id=f"lease:{READER}", data={
        "strand": READER, "thread": thread, "gen": 2, "router_gen": 1}))
    facts = read_facts(home)
    room = _prepare(home, READER, thread, 2, mail(home.root, facts, thread),
                    sender_threads(facts), facts, "hand")
    return room, facts


@pytest.mark.parametrize("mode", ["known", "unknown", "other-channel", "forged", "orphan", "unquoted"])
def test_source_reply_binding_reaches_wake_and_boundary(home, mode):
    draft(home, "first")
    draft(home, "second")
    receipt(home, "42:part1")
    native = "42:part2"
    if mode == "known":
        receipt(home, native)
    elif mode == "other-channel":
        receipt(home, native, channel="channel:other/42")
    elif mode == "forged":
        receipt(home, native, by="strand:s-forged")
    elif mode == "orphan":
        receipt(home, native, key="s-missing/no-letter")
    if mode == "unquoted":
        native = None
    fields = dict(id="incoming", kind="letter", to=CHANNEL, body="My follow-up", **{"from": "mine:42"})
    if native is not None:
        fields["re"] = native
    ingest(home, "mine", fields, [])
    facts = read_facts(home)
    source = next(f for f in facts if f.id == "source:mine:incoming")
    letter = next(f for f in facts if f.id == "letter:mine:incoming")
    thread = "first" if mode == "known" else "second"
    assert route_bare(facts, "mine/42") == "thread:second"
    assert letter.data["to"] == f"thread:{thread}"
    assert letter.data.get("re") == (FIRST if mode == "known" else None)
    assert source.data.get("re") == native
    expected = (f"replying to your {FIRST}: The first answer" if mode == "known" else
                f"replying to an earlier message ({native})" if native else "")
    room, facts = prepare(home, facts, thread)
    wake = (room / "port/wake.md").read_text()
    boundary = (room / "port/in/boundary.md").read_text()
    for text in (wake, boundary):
        assert "My follow-up" in text
        if expected:
            assert text.count(expected) == 1
            assert text.index(expected) > text.index("My follow-up")
        else:
            assert "replying to" not in text
    # The same context survives compact live redelivery and the jack parser.
    append(home, Fact(kind="shown", by=f"strand:{READER}", id="shown:reply", data={
        "strand": READER, "gen": 2, "ids": [letter.id]}))
    _boundary(home, read_facts(home), READER, 2,
              SimpleNamespace(thread=thread, fuel="", why=""))
    text = (room / "port/in/boundary.md").read_text()
    parsed, = parse_boundary(text).letters
    assert parsed.compact
    delivered = execute("post", room, "{}")
    if expected:
        assert expected in parsed.body and expected in delivered
    else:
        assert "replying to" not in delivered


def test_late_reply_delivers_context_at_a_live_boundary(home):
    draft(home, "first")
    receipt(home, "42:quoted")
    room, facts = prepare(home, read_facts(home), "first")
    assert "replying to" not in (room / "port/wake.md").read_text()
    ingest(home, "mine", dict(id="late", kind="letter", to=CHANNEL,
                             re="42:quoted", body="Later question"), [])
    _boundary(home, read_facts(home), READER, 2,
              SimpleNamespace(thread="first", fuel="", why=""))
    expected = f"replying to your {FIRST}: The first answer"
    parsed, = parse_boundary((room / "port/in/boundary.md").read_text()).letters
    assert not parsed.compact and expected in parsed.body
    assert expected in execute("post", room, "{}")


def test_recovered_source_uses_its_saved_quote_not_replayed_fields(home, monkeypatch):
    from brr.loom.runtime import connector
    draft(home, "first")
    receipt(home, "42:saved")
    real_append = connector.append
    def interrupt(home, fact):
        if fact.kind == "letter":
            raise RuntimeError("source saved, letter interrupted")
        return real_append(home, fact)
    fields = dict(id="recover", kind="letter", to=CHANNEL, re="42:saved", body="Question")
    monkeypatch.setattr(connector, "append", interrupt)
    with pytest.raises(RuntimeError, match="letter interrupted"):
        ingest(home, "mine", fields, [])
    monkeypatch.setattr(connector, "append", real_append)
    ingest(home, "mine", dict(fields, re="42:changed"), [])
    letter = next(f for f in read_facts(home) if f.id == "letter:mine:recover")
    assert letter.data["re"] == FIRST


def test_chat_header_is_the_face_and_thread_even_after_release(home):
    key = draft(home, "first", sent=False)
    router = Router(home)
    facts = router.begin_tick(read_facts(home))
    assert router.armed()
    facts = router.speak_out(facts)
    sent = json.loads((home.root / "loom/channel-fake.jsonl").read_text())
    assert sent["key"] == key
    assert sent["text"] == "b·_·d ⌁ [ first ]\n\nThe first answer\nMore detail"
    append(home, Fact(kind="released", by="loom:aaaa", id="released:first", data={
        "strand": "s-aaaa-first", "thread": "first", "gen": 1}))
    state = fold(read_facts(home))
    letter = next(f for f in facts if f.id == key)
    assert _header(state, letter) == "b·_·d ⌁ [ first ]"
    assert _header(state, Fact(kind="letter", by="loom:aaaa")) == "b·_·d ⌁"


@pytest.mark.parametrize("destination,expected", [(CHANNEL, "incoming"), ("channel:other/42", None),
                                                  ("channel:mine/43", None)])
def test_send_binds_native_reply_only_in_its_source_channel(home, monkeypatch, destination, expected):
    import subprocess
    from pathlib import Path
    from brr.loom.runtime import speak
    from brr.loom.runtime.loom import _ingest
    from brr.loom.runtime.port import parse_frontmatter, write_send

    (home.root / "self/people/ada/channels.toml").write_text(
        'channels = ["mine:42", "mine:43", "other:42"]\n')
    draft(home, "first")
    ingest(home, "mine", dict(id="incoming", kind="letter", to=CHANNEL,
                             body="Question", **{"from": "mine:42"}), [])
    room, _ = prepare(home, read_facts(home), "first")
    router = Router(home)
    router.begin_tick(read_facts(home))
    captured = []
    real_run = subprocess.run
    def transport(args, **kw):
        if Path(str(args[0])).name == "out":
            captured.append(parse_frontmatter(Path(args[1]).read_text()))
            return subprocess.CompletedProcess(args, 0, '{"ids": []}', '')
        return real_run(args, **kw)
    monkeypatch.setattr(speak.subprocess, "run", transport)
    key = write_send(room, to=destination, sender=READER, body="Answer", re="letter:mine:incoming")
    facts = _ingest(home, read_facts(home))
    router.speak_out(facts)
    sent = next(item for item in captured if item['id'] == key)
    assert sent.get('re') == expected
    assert any(f.kind == 'speech' and f.data.get('key') == key and f.data['state'] == 'sent'
               for f in read_facts(home))


def test_delivered_history_does_not_reauthorize_each_fact(home, monkeypatch):
    draft(home, "first")
    router = Router(home)
    facts = router.begin_tick(read_facts(home))
    # Attention facts are accepted history, as are delivered channel letters.
    facts.extend(Fact(kind="attention", by="loom:aaaa", id=f"attention:{n}",
                      data={"why": "old condition"}, hlc=(n, 0, "aaaa"))
                 for n in range(1000))
    calls = []
    authorize = router.authority.authorize
    def counted(lease):
        calls.append(lease)
        return authorize(lease)
    monkeypatch.setattr(router.authority, "authorize", counted)
    router.speak_out(facts)
    assert len(calls) == 1
    assert not (home.root / "loom/channel-fake.jsonl").exists()


def test_pending_send_rechecks_authority_after_selecting_letter(home, monkeypatch):
    draft(home, "first", sent=False)
    router = Router(home)
    facts = router.begin_tick(read_facts(home))
    checks = iter([True, False])
    monkeypatch.setattr(router.authority, "authorize", lambda lease: next(checks))
    router.speak_out(facts)
    assert router.lease is None
    assert not (home.root / "loom/channel-fake.jsonl").exists()
