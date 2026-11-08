"""``&refs``: a known one links, an unknown one is told and never refuses the letter."""
import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.connector import ingest
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.port import fact_from_port, parse_frontmatter
from brr.loom.runtime.refs import tokens
from test_dispositions import by_line, out_file, send
from test_wishes import ledger  # noqa: F401  (fixture)
from _step import write_thread


def notices(home, kind):
    return [f for f in read_facts(home) if f.kind == "letter" and f.id.startswith(f"loom/{kind}-")]


def test_tokens_skip_code_quotes_urls_and_shell():
    body = ("see &4529, (&w-qa0b) and &thread:beads.\n`&nope` ```\n&nope\n``` a?x=1&y=2 a && b\n"
            "> you wrote &w-quoted and &s-ab12-cd34ef\n  > &4530 nested\n")
    assert tokens(body) == ["4529", "w-qa0b", "thread:beads"]


def test_a_word_after_an_ampersand_is_no_miss_and_an_id_shape_is(ledger):
    """w-nw1m5avc70qm: a placeholder like &ref or &name is a word, not a ref that missed."""
    from brr.loom.runtime.refs import link
    home, _ = ledger
    from brr.loom.runtime.lifecycle import main_snapshot
    links, misses = link(read_facts(home), main_snapshot(home), "fill &ref and &name, see &w-zzzz9 and &s-ab12-cd34ef")
    assert links == [] and misses == ["w-zzzz9", "s-ab12-cd34ef"]


def test_a_resident_send_links_the_known_ref_and_is_told_the_unknown(ledger):
    home, seat = ledger
    known = inject_letter(home.root, to="thread:work", body="the spec")
    stem = known.rsplit("/", 1)[-1]
    sent = send(seat, f"read &{stem} and &w-nope first")
    assert sent.returncode == 0, sent.stderr
    assert "no &w-nope in the ledger" in sent.stderr
    letter = sent.stdout.strip()
    fm = parse_frontmatter(out_file(seat, letter).read_text())
    assert fm["links"] == known
    assert fact_from_port(fm, "s-aaaa-test", 1).data["links"] == [known]
    [told] = notices(home, "ref-unknown")
    assert told.data["to"] == "thread:other" and told.data["notice"] is True
    assert "&w-nope" in told.data["body"] and letter in told.data["body"]


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
    write_thread(home.root, "inbox", "Brnrd", "quit-now")
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
    assert body.data["to"] == "thread:inbox" and body.data["notice"] is True
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


def test_every_root_kind_through_its_writer_and_nothing_dangles(ledger):
    """A person letter, a loom notice, a reincarnation up to the thread's wish, an
    incident a person ratified, and a derived wish before and after its verdict."""
    from brr.loom.runtime.asks import Beads, roots
    from brr.loom.runtime.lifecycle import main_snapshot
    from brr.loom.runtime.loom import notice, notice_id
    from brr.loom.runtime.port import write_send
    from test_wake import cli, commit
    home, seat = ledger

    def walk(ident):
        return Beads(read_facts(home), main_snapshot(home)).chain(ident)

    def land(letter):
        append(home, fact_from_port(parse_frontmatter(out_file(seat, letter).read_text()), "s-aaaa-test", 1))
        return letter

    said = inject_letter(home.root, to="thread:work", body="fold the facts")
    assert walk(said) == ([], "person")
    # (a) A loom notice is its own root.
    assert notice(home, read_facts(home), "test-degraded", "work", "degraded", "nothing", thread="work")
    told = notice_id("test-degraded", "work")
    assert walk(told) == ([], "loom")
    # (b) An incident: the resident answers the notice, the person ratifies that answer.
    sent = send(seat, "--re", told, "--chatter", "repaired")
    assert sent.returncode == 0, sent.stderr
    answer = land(sent.stdout.strip())
    ratified = inject_letter(home.root, to="thread:work", body="good", re=answer)
    assert walk(ratified) == ([(answer, "re"), (told, "re")], "loom")
    # (e) Initiative: a resident's wish is unconfirmed until a person's verdict letter.
    made = send(seat, "--re", answer, "--wish", "new:Fold facts once", "I will fold them")
    assert made.returncode == 0, made.stderr
    land(made.stdout.strip())
    wish = by_line(home, "Fold facts once").id
    assert walk(wish) == ([], "unconfirmed")
    # (c) A handover climbs strand, thread, the wish the thread was opened for.
    (home.root / "self/threads/work/README.md").write_text(
        f"---\nid: work\nstatus: open\ntense: plan\nopened-for: [{wish}]\n---\n# Wish work\n")
    commit(home.root / "self", "opened for")
    reincarnate = land(write_send(seat, to="thread:work", sender="s-aaaa-test", body="handover",
                           reincarnate="s-aaaa-test/abcde"))
    up = [("s-aaaa-test", "reincarnate"), ("thread:work", "thread"), (wish, "opened-for")]
    assert walk(reincarnate) == (up, "unconfirmed")
    agreed = cli(["wish", wish, "--room", str(seat), "--accept", "word", "--ref", said])
    assert agreed.returncode == 0, agreed.stderr
    assert walk(reincarnate) == ([*up, (said, "verdict")], "person")
    # The invariant: every letter here ends at a person, a loom fact or an unconfirmed wish.
    assert set(roots(read_facts(home), main_snapshot(home))) <= {"person", "loom", "unconfirmed"}


def test_a_follow_up_that_cites_nothing_is_told_the_letter_to_cite(ledger):
    # Strand to strand, the way on was a guess: --wish asked for --re, and the
    # bare refusal named no id. It names the newest letter between the threads.
    home, seat = ledger
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": "other", "strand": "s-aaaa-othr", "gen": 1,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))
    append(home, Fact(kind="letter", by="strand:s-aaaa-othr", id="letter:s-aaaa-othr/abcde", data={
        "id": "s-aaaa-othr/abcde", "from": "s-aaaa-othr", "to": "thread:work", "body": "more findings"}))
    refused = send(seat, "another one")
    assert refused.returncode == 1 and "--re s-aaaa-othr/abcde" in refused.stderr
    assert "&<w-id>" in refused.stderr
    assert send(seat, "--re", "s-aaaa-othr/abcde", "another one").returncode == 0


def test_a_loom_notice_on_a_persons_chat_is_headed_as_the_loom():
    """w-nw1m5avc70qm: the ref-told notice posted under the resident's resting face."""
    from brr.loom.runtime.router import LOOM_HEADER, _header
    told = Fact(kind="letter", by="loom:aaaa", id="loom/ref-told-x", data={"from": "loom", "body": "x"})
    assert _header(None, told) == LOOM_HEADER and "not the resident" in LOOM_HEADER


def test_a_track_keeps_its_whole_provenance_after_the_verdict(ledger):
    """10595: the ask, the delivery, his 'Merged' and his board agree stay one chain after the agree.

    The board's agree links the wish and is the verdict's ref; the wish climbing to it made a
    two-node loop, and the ask and the delivery dropped out of every chain under the wish."""
    from brr.loom.runtime.asks import Beads, roots
    from brr.loom.runtime.board import beads_of
    from brr.loom.runtime.lifecycle import main_snapshot
    from test_wake import cli
    home, seat = ledger

    def walk(ident):
        return Beads(read_facts(home), main_snapshot(home)).chain(ident)

    ask = inject_letter(home.root, to="thread:work", body="a logo bench, please")
    made = cli(["wish", "new", "--room", str(seat), "--line", "A logo bench", "--say", ask])
    assert made.returncode == 0, made.stderr
    wish = made.stdout.strip()
    sent = send(seat, "--re", ask, f"delivered &{wish}: the bench is up")
    assert sent.returncode == 0, sent.stderr
    delivered = sent.stdout.strip()
    append(home, fact_from_port(parse_frontmatter(out_file(seat, delivered).read_text()), "s-aaaa-test", 1))
    merged = inject_letter(home.root, to="thread:work", body="Merged", re=delivered)
    before = {i: walk(i) for i in (ask, wish, delivered, merged)}
    # The board's agree: a person letter that links the wish, then the verdict citing it.
    agree = "p-test/agree"
    append(home, Fact(kind="letter", by="person:p-test", id=agree, data={
        "id": agree, "to": "thread:work", "from": "p-test", "body": f"agree &{wish}", "links": [wish]}))
    agreed = cli(["wish", wish, "--home", str(home.root), "--accept", "word", "--ref", agree])
    assert agreed.returncode == 0, agreed.stderr

    assert walk(wish) == ([(ask, "says")], "person")
    assert walk(merged) == ([(delivered, "re"), (ask, "re")], "person")
    assert walk(agree) == ([(wish, "link"), (ask, "says")], "person")
    # The verdict moved nothing above the agree: every earlier chain reads as it did before it.
    assert {i: walk(i) for i in before} == before
    # Every letter of the track sits in one tree rooted at the ask, on the beads the board walks too.
    rows = beads_of(read_facts(home), main_snapshot(home))
    for letter in (delivered, merged, agree):
        at, seen = letter, []
        while rows[at][0]:
            at = rows[at][0]
            seen.append(at)
        assert at == ask, (letter, seen)
    assert set(roots(read_facts(home), main_snapshot(home))) <= {"person", "loom", "unconfirmed"}
