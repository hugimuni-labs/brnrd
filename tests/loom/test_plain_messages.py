"""A person's chat reads words, never ledger ids; a reply still answers its ask.

Self threads/first-task counted machine words in a new person's messages. The
ids stay on the ledger letter and the board; the router's channel render drops
them. Each case goes through the writer a person's message really comes from,
the tick that drains it, the router's ``speak_out`` and the fake connector.
"""
import json
import re

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import collectors, snapshot
from brr.loom.runtime.config import load_config
from brr.loom.runtime.connector import ingest
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _ingest
from brr.loom.runtime.plain import for_person
from brr.loom.runtime.router import Router
from brr.loom.runtime.wishes import wishes
from test_asks_wait import child
from test_collector_asks import ran, reads
from test_wake import cli, commit
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)

CHANNEL = "channel:mine/42"
# What the first-task counter calls a machine word in a chat: ids, refs and thread addresses.
MACHINE = re.compile(r"w-[0-9a-z]{12}|s-[0-9a-f]{4}-[0-9a-z]{4,}|&\S|thread:\S|loom/\S+-[0-9a-f]{10}")


@pytest.fixture
def home(ledger):
    home, seat = ledger
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    (home.root / "self/people/ada").mkdir(parents=True)
    (home.root / "self/people/ada/channels.toml").write_text('channels = ["mine:42"]\n')
    collectors._SEEN.clear()
    yield home, seat
    collectors._SEEN.clear()


def chat(home, key):
    """The text the fake connector carried for letter ``key``, once."""
    path = home.root / "loom/channel-fake.jsonl"
    sent = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    texts = [s["text"] for s in sent if s["key"] == key]
    assert len(texts) == 1, (key, sent)
    return texts[0]


def speak(home):
    router = Router(home)
    facts = router.begin_tick(read_facts(home))
    assert router.armed()
    router.speak_out(facts)


def reply(home, key, body, native):
    """The person answers the chat message: the platform receipt binds it to ``key``."""
    append(home, Fact(kind="speech.part", by="loom:aaaa", id=f"part:{native}", data={
        "key": key, "channel": CHANNEL, "receipt": {"message_id": native}}))
    ingest(home, "mine", dict(id=f"r-{native}", kind="letter", to=CHANNEL, **{"from": "mine:42"},
                              re=native, body=body), [])


def to_chat(home, ident):
    """The one ledger letter to the chat that carries ``ident``."""
    found = [f for f in read_facts(home) if f.kind == "letter" and f.data.get("to") == CHANNEL
             and ident in f.data.get("body", "")]
    assert len(found) == 1, found
    return found[0]


def on_board(home, key):
    doc = snapshot.assemble(home, read_facts(home), load_config(home.root))
    rows = [row for row in _rows(doc) if row.get("id") == key]
    assert rows, key
    return rows[0]["head"]


def _rows(doc):
    out = []

    def walk(node):
        if isinstance(node, dict):
            if "head" in node and "id" in node:
                out.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(doc)
    return out


def test_a_body_ask_reads_as_its_question_and_a_reply_still_answers_it(home):
    home, seat = home
    _wish, mid = child(home, seat, "mid", "s-aaaa-mid")
    wish, leaf = child(home, mid, "leaf", "s-aaaa-leaf")
    command(home, seat, "settle", "mid", "--why", "split done")
    other = command(home, leaf, "ask", "Ship the parser?", "--for", wish)
    ask = command(home, leaf, "ask", "Keep going?", "--for", wish, "--rec", "yes, the tests are green")
    _ingest(home)
    letter = to_chat(home, f"answer &{ask}")
    # The ledger letter keeps its words and its link.
    assert letter.data["body"].startswith(f"ask {ask} from thread:leaf: Keep going?")
    assert ask in letter.data["links"]
    speak(home)
    text = chat(home, letter.data["id"])
    assert not MACHINE.search(text), text
    assert text.endswith("\n\nKeep going?\nI recommend: yes, the tests are green\n"
                         "Reply to this message to answer."), text
    reply(home, letter.data["id"], "yes, keep going", "42:7")
    found = wishes(read_facts(home))
    assert found[ask].state == "answered" and found[other].open
    # The board still shows the ids.
    assert on_board(home, letter.data["id"]).startswith(f"ask {ask} from thread:leaf: ")


def test_a_collector_ask_reads_as_its_fix_and_a_reply_still_answers_it(home):
    home, _seat = home
    ran(home, "grok")
    for _ in range(2):  # an error held for two readings, read by the tick
        reads(home, "grok", "grok billing HTTP 401; no authentication refresh attempted")
    ask, = [w for w in wishes(read_facts(home)).values() if w.line.startswith("fuel: the grok collector")]
    letter = to_chat(home, f"answer &{ask.id}")
    assert letter.data["links"] == [ask.id]
    text = chat(home, letter.data["id"])
    assert not MACHINE.search(text), text
    assert text.splitlines()[0] == "⌁ loom: an automatic notice, not the resident"
    assert text.endswith(f"\n\n{ask.line}\nReply to this message to answer."), text
    assert "`grok login`" in text  # a command stays copyable
    reply(home, letter.data["id"], "logged in again", "42:8")
    assert wishes(read_facts(home))[ask.id].state == "answered"
    assert on_board(home, letter.data["id"]).startswith(f"ask {ask.id} from thread:inbox: ")


def test_a_delivery_reads_as_its_line_and_ok_still_accepts_that_wish(home):
    home, seat = home
    page = home.root / "self/threads/work/README.md"
    page.write_text(page.read_text().replace("tense: plan\n", "tense: plan\nopened-by: person:ada\n"))
    commit(home.root / "self", "Record ada as work opener")
    ingest(home, "mine", dict(id="ask1", kind="letter", to=CHANNEL, **{"from": "mine:42"},
                              body="Please write the summary", thread="work"), [])
    said = next(f for f in read_facts(home) if f.id == "letter:mine:ask1")
    if said.data["to"] != "thread:work":
        append(home, Fact(kind="letter", by=said.by, id="letter:mine:ask1-work",
                          data=dict(said.data, id="letter:mine:ask1-work", to="thread:work")))
        said = next(f for f in read_facts(home) if f.id == "letter:mine:ask1-work")
    ident = command(home, seat, "wish", "new", "--line", "A summary of the call", "--say", said.data["id"])
    result = cli(["wish", "--room", str(seat), ident, "--result", "url:https://example.com/summary",
                  "--line", "The summary, one page", "--deliver"])
    assert result.returncode == 0, result.stderr
    _ingest(home)
    letter = to_chat(home, f"delivered &{ident}:")
    assert ident in letter.data["links"]
    speak(home)
    text = chat(home, letter.data["id"])
    assert not MACHINE.search(text), text
    # The header's proposal token loses its id too: "· delivered", never "· w-… delivered".
    head, _, rest = text.partition("\n\n")
    assert "[ work ]" in head, head
    assert rest.startswith("Delivered: A summary of the call\n"
                           "The summary, one page: https://example.com/summary\n"), rest
    # The url check's own line sits between (unverified here: no network), then the reply line.
    assert rest.endswith("\nReply ok to accept, or say what is off."), rest
    reply(home, letter.data["id"], "ok", "42:9")
    verdicts = [f for f in read_facts(home) if f.kind == "letter" and f.data.get("verdict")]
    assert [(v.data["verdict"], v.data["wish"]) for v in verdicts] == [("agree", ident)]
    assert on_board(home, letter.data["id"]).startswith(f"delivered &{ident}: ")


@pytest.mark.parametrize("before,after", [
    # A body's own words: refs, parenthesised ids and thread addresses go; the sentence closes up.
    ("Filed as &w-pfceyl02a3zh, see the inbox (s-ae66-qlablg/jbmcc).\nFrom thread:growth-pilot: done.",
     "Filed as, see the inbox.\nFrom growth-pilot: done."),
    ("- w-8jbvlj0c1e1r: the four notes\n- &w-8jbvlj0c1e1r · tidy", "- the four notes\n- tidy"),
    # The header's proposal token (faces.triggers) keeps its word, not its id.
    ("b·_·d ⌁ [ work ] · w-mp4rs7vowyb9 delivered\n\nHello", "b·_·d ⌁ [ work ] · delivered\n\nHello"),
    # Code, URLs and the person's own spacing are theirs.
    ("Run `brnrd runtime letter s-ae66-qlablg/jbmcc` · https://x.example/w-8jbvlj0c1e1r ? Oui !",
     "Run `brnrd runtime letter s-ae66-qlablg/jbmcc` · https://x.example/w-8jbvlj0c1e1r ? Oui !"),
    # "rec:" reads as a recommendation only under an ask.
    ("rec: a word of my own", "rec: a word of my own"),
    ("Plain words, 10:30, a=1&b=2 and #2884.", "Plain words, 10:30, a=1&b=2 and #2884."),
])
def test_the_render_drops_ids_and_keeps_the_sentence(before, after):
    assert for_person(before) == after
