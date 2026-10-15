"""The reply address on a boundary line, as a live haiku body needed it.

Recorded on the first live drive (2026-10-11): told to answer
``--to thread:<from-thread>`` and shown ``reply -``, the body tried
``thread:arseni``, then answered its own thread. The person would have heard
nothing; the answer became a letter the thread owed itself; the successor
acknowledged each one into another.
"""

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import ledger
from brr.loom.runtime.home import Home
from brr.loom.runtime.port import PortError, parse_boundary, render_boundary, write_send
from brr.loom.runtime.project import reply_channels

STRAND = "s-aaaa-abc123"


def _home(tmp_path) -> Home:
    home = Home(tmp_path, install="aaaa")
    home.thread_dir("t").mkdir(parents=True)
    (home.thread_dir("t") / "README.md").write_text("# t\n")
    ledger.append(home, Fact(kind="router", by="loom:aaaa", id="router:1", data={"gen": 1}))
    ledger.append(home, Fact(kind="lease", by="loom:aaaa", id="lease:t:1", data={
        "thread": "t", "strand": STRAND, "gen": 1, "install": "aaaa", "router_gen": 1}))
    (home.root / "loom").mkdir(exist_ok=True)
    (home.root / "loom/config.toml").write_text('connectors = ["relay"]\n')
    return home


def _relayed(platform: str) -> list[Fact]:
    source = Fact(kind="source", by="loom:aaaa", id="source:relay:e1", data={
        "platform": "relay", "chat": f"{platform}:42", "channel": f"channel:relay/{platform}:42", "from": "person:ada", "text": "hi", "to": "thread:t"})
    letter = Fact(kind="letter", by="loom:aaaa", id="letter:relay:e1", data={
        "id": "letter:relay:e1", "from": "person:ada", "to": "thread:t", "body": "hi",
        "cites": source.id})
    return [source, letter]


def test_a_persons_letter_names_the_chat_it_came_from(tmp_path):
    home = _home(tmp_path)
    facts = _relayed("telegram")
    text = render_boundary(STRAND, 1, "t", facts[1:], set(), {}, channels=reply_channels(facts, home))
    assert parse_boundary(text).letters[0].reply == "channel:relay/telegram:42"


def test_a_platform_send_would_refuse_is_not_offered(tmp_path):
    home = _home(tmp_path)
    (home.root / "loom/config.toml").write_text("connectors = []\n")
    assert reply_channels(_relayed("whatsapp"), home) == {}


def test_a_thread_never_names_itself_as_the_reply_address():
    handover = Fact(kind="letter", by="strand:s-aaaa-before", id="s-aaaa-before/molt1", data={
        "id": "s-aaaa-before/molt1", "from": "s-aaaa-before", "to": "thread:t", "body": "why"})
    text = render_boundary(STRAND, 1, "t", [handover], set(), {"s-aaaa-before": "t"})
    assert parse_boundary(text).letters[0].reply == "-"
    other = render_boundary(STRAND, 1, "elsewhere", [handover], set(), {"s-aaaa-before": "t"})
    assert parse_boundary(other).letters[0].reply == "thread:t"


def test_send_refuses_a_letter_to_the_senders_own_thread(tmp_path):
    home = _home(tmp_path)
    room = home.room(STRAND)
    with pytest.raises(PortError, match="your own thread"):
        write_send(room, to="thread:t", sender=STRAND, body="pong", re="p-test/abcde")
    assert not list((room / "port" / "out").glob("*.md"))
    # The two ways a thread does speak to itself still pass: a note, a molt's handover.
    write_send(room, to="thread:t", sender=STRAND, re="p-test/abcde", note="done")
    write_send(room, to="thread:t", sender=STRAND, body="why", molt=f"{STRAND}/m1")
    assert len(list((room / "port" / "out").glob("*.md"))) == 2


def test_a_climbed_letter_keeps_the_chat_it_came_from(tmp_path):
    """Live, 2026-10-12: a late telegram letter to a settled thread climbed to the
    inbox as a loom notice shown with `reply -`; the body noted the answer and the
    person heard nothing."""
    from brr.loom.runtime.loom import notice

    home = _home(tmp_path)
    facts = _relayed("telegram")
    assert notice(home, facts, "climbed", "letter:relay:e1",
                  "letter letter:relay:e1 to closed thread old climbed here (from person:ada)",
                  "hi", thread="t", about=facts[1])
    climbed = next(f for f in ledger.read_facts(home) if f.id.startswith("loom/climbed"))
    text = render_boundary(STRAND, 1, "t", [climbed], set(), {},
                           channels=reply_channels(facts + [climbed], home))
    assert parse_boundary(text).letters[0].reply == "channel:relay/telegram:42"
