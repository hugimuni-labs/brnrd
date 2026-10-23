"""``&refs``: a known one links, an unknown one is told and never refuses the letter."""
import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.connector import ingest
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.port import fact_from_port, parse_frontmatter
from brr.loom.runtime.refs import tokens
from test_dispositions import out_file, send
from test_wishes import ledger  # noqa: F401  (fixture)
from _step import write_thread


def notices(home, kind):
    return [f for f in read_facts(home) if f.kind == "letter" and f.id.startswith(f"loom/{kind}-")]


def test_tokens_skip_code_urls_and_shell():
    body = "see &4529, (&w-qa0b) and &thread:beads.\n`&nope` ```\n&nope\n``` a?x=1&y=2 a && b"
    assert tokens(body) == ["4529", "w-qa0b", "thread:beads"]


def test_a_resident_send_links_the_known_ref_and_is_told_the_unknown(ledger):
    home, seat = ledger
    known = inject_letter(home.root, to="thread:work", body="the spec")
    stem = known.rsplit("/", 1)[-1]
    sent = send(seat, f"read &{stem} and &nope first")
    assert sent.returncode == 0, sent.stderr
    assert "no &nope in the ledger" in sent.stderr
    letter = sent.stdout.strip()
    fm = parse_frontmatter(out_file(seat, letter).read_text())
    assert fm["links"] == known
    assert fact_from_port(fm, "s-aaaa-test", 1).data["links"] == [known]
    [told] = notices(home, "ref-unknown")
    assert told.data["to"] == "thread:other" and told.data["notice"] is True
    assert "&nope" in told.data["body"] and letter in told.data["body"]


def test_a_thread_letter_that_cites_nothing_is_refused(ledger):
    home, seat = ledger
    refused = send(seat, "fyi")
    assert refused.returncode == 1 and "cites nothing" in refused.stderr
    assert not list((seat / "port/out").glob("*.md"))
    # An unknown ref is no citation either; a known thread is.
    assert send(seat, "fyi &nope").returncode == 1
    assert send(seat, "fyi, see &thread:work").returncode == 0


@pytest.fixture
def person_home(tmp_path):
    home = Home(tmp_path / "home", install="aaaa")
    write_thread(home.root, "brnrd", "Brnrd", "quit-now")
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    person = home.root / "self/people/ada"
    person.mkdir(parents=True)
    (person / "channels.toml").write_text('channels = ["mine:42"]\n')
    append(home, Fact(kind="router", by="loom:aaaa", id="router:1", data={"gen": 1}))
    return home


def test_a_person_letter_lands_with_the_known_link_and_both_sides_are_told(person_home):
    home = person_home
    ingest(home, "mine", dict(id="first", kind="letter", to="channel:mine/42", body="the spec",
                              **{"from": "mine:42"}), [])
    ingest(home, "mine", dict(id="second", kind="letter", to="channel:mine/42",
                              body="as in &first, and &w-nope too", **{"from": "mine:42"}), [])
    facts = read_facts(home)
    letter = next(f for f in facts if f.id == "letter:mine:second")
    assert letter.data["links"] == ["letter:mine:first"]
    assert letter.data["body"] == "as in &first, and &w-nope too"
    [body] = notices(home, "ref-unknown")
    assert body.data["to"] == "thread:brnrd" and body.data["notice"] is True
    [him] = notices(home, "ref-told")
    assert him.data["to"] == "channel:mine/42" and him.data["re"] == "letter:mine:second"
    assert "&w-nope" in him.data["body"]


def test_a_bead_walks_a_resident_reply_back_to_the_person_root(ledger):
    from brr.loom.runtime.asks import bead
    from brr.loom.runtime.lifecycle import main_snapshot
    from test_wake import cli
    home, seat = ledger
    first = inject_letter(home.root, to="thread:work", body="the spec")
    root = inject_letter(home.root, to="thread:work", body="build it")
    sent = send(seat, "--re", root, "--chatter", "on it, as &" + first.rsplit("/", 1)[-1])
    assert sent.returncode == 0, sent.stderr
    reply = sent.stdout.strip()
    fm = parse_frontmatter(out_file(seat, reply).read_text())
    append(home, fact_from_port(fm, "s-aaaa-test", 1))
    found = bead(read_facts(home), main_snapshot(home), reply)
    assert [a["id"] for a in found["ancestors"]] == [root]
    assert (found["root"], found["root_kind"], found["said"]) == (root, "person", root)
    assert found["strip"] == {"prev": first, "next": None} and found["links"] == [first]
    assert bead(read_facts(home), main_snapshot(home), first)["answers"] == [reply]
    shown = cli(["graph", "--room", str(seat), "--bead", reply])
    assert shown.returncode == 0 and f"root: {root}" in shown.stdout
    assert cli(["graph", "--room", str(seat), "--bead", "nope"]).returncode == 1
