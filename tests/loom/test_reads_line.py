"""How a person reads rides under each of their letters, at the wake and at every
boundary, from the page on the self's main (brnrd#2776). It informs; nothing is refused."""
import json
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.connector import ingest
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _boundary, _prepare, _reply_context
from brr.loom.runtime.port import parse_boundary
from brr.loom.runtime.project import sender_threads
from brr.loom.runtime.reads import CAP, line_of
from brr.loom.runtime.selfrepo import git

from _step import mail, write_thread

STRAND = "s-aaaa-reads"
READS = "reads: Ada has slight dyslexia and ADHD"


@pytest.fixture
def home(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "inbox", "Brnrd", "quit-now")
    for person, chat in (("ada", "42"), ("bob", "43")):
        (root / "self/people" / person).mkdir(parents=True)
        (root / "self/people" / person / "channels.toml").write_text(f'channels = ["fake:{chat}"]\n')
    home = Home(root, install="aaaa")
    append(home, Fact(kind="lease", by="loom:aaaa", id="lease:inbox:1", data={
        "thread": "inbox", "strand": STRAND, "gen": 1}))
    return home


def page(home, text, *, land=True):
    path = home.root / "self/people/ada/page.md"
    path.write_text(text)
    if land:
        git(home.root / "self", "add", "people")
        git(home.root / "self", "commit", "-qm", "ada's page")


def letter(home, ident, chat, body):
    ingest(home, "fake", dict(id=ident, kind="letter", to=f"channel:fake/{chat}", body=body,
                             **{"from": f"fake:{chat}"}), [])




def woken(home):
    facts = read_facts(home)
    room = _prepare(home, STRAND, "inbox", 1, mail(home.root, facts, "inbox"),
                    sender_threads(facts), facts, "hand")
    return room, (room / "port/wake.md").read_text(), (room / "port/in/boundary.md").read_text()


def sections(wake):
    return {section.split(" from ")[0]: section for section in wake.split("### ")[1:]}


def bodies(boundary):
    return {f.id: f.body for f in parse_boundary(boundary).letters}


def test_the_line_rides_under_her_letters_only_at_the_wake_and_the_boundary(home):
    page(home, f"# ada\n\n{READS}\n\n## who\n")
    letter(home, "a1", "42", "Is it merged?")
    letter(home, "b1", "43", "Hello from bob")
    room, wake, boundary = woken(home)
    occasion = sections(wake)
    assert f"Is it merged?\n{READS}\n" in occasion["letter:fake:a1"]
    assert READS not in occasion["letter:fake:b1"]
    shown = bodies(boundary)
    assert READS in shown["letter:fake:a1"] and READS not in shown["letter:fake:b1"]
    assert boundary.count(READS) == 1 and wake.count(READS) == 1
    # A thread letter is no person's: the one producer both renderers read gives it nothing.
    strand = Fact(kind="letter", by="strand:s-aaaa-other", id="letter:t1", data={
        "id": "s-aaaa-other/t1", "to": "thread:inbox", "from": "s-aaaa-other", "body": "a thread letter"})
    assert READS not in _reply_context(read_facts(home), "inbox", [strand], room).get("s-aaaa-other/t1", "")


def test_a_letter_that_lands_later_carries_it_and_the_jack_delivers_it(home):
    page(home, f"# ada\n{READS}\n")
    room, _wake, _boundary_text = woken(home)
    execute("post", room, "{}")
    letter(home, "a2", "42", "One more thing")
    _boundary(home, read_facts(home), STRAND, 1, SimpleNamespace(thread="inbox", fuel="", why=""))
    assert READS in bodies((room / "port/in/boundary.md").read_text())["letter:fake:a2"]
    delivered = json.loads(execute("post", room, "{}"))["hookSpecificOutput"]["additionalContext"]
    assert f"One more thing\n  {READS}" in delivered


def test_no_page_no_line_and_a_line_below_the_head_is_page_prose(home):
    letter(home, "a1", "42", "Hello")
    _room, wake, boundary = woken(home)
    assert "reads:" not in wake and "reads:" not in boundary
    page(home, "# ada\n" + "\n" * 10 + READS + "\n")
    _room, wake, boundary = woken(home)
    assert "reads:" not in wake and "reads:" not in boundary


def test_a_page_in_the_work_tree_only_is_not_read(home):
    page(home, f"# ada\n{READS}\n", land=False)
    letter(home, "a1", "42", "Hello")
    _room, wake, _boundary_text = woken(home)
    assert READS not in wake


def test_over_the_cap_the_first_bytes_show_and_say_it_was_cut(home):
    long = "reads: " + "Ada reads short lines, one question at a time, and no repeats. " * 3
    assert len(long.encode()) > CAP
    page(home, f"# ada\n{long}\n")
    letter(home, "a1", "42", "Hello")
    _room, wake, _boundary_text = woken(home)
    shown = next(line for line in wake.splitlines() if line.startswith("reads:"))
    assert shown.startswith(long.encode()[:CAP].decode().rstrip())
    assert shown.endswith(f"[cut at {CAP} bytes; the page holds the rest]")


def test_line_of_reads_the_head_only():
    assert line_of(f"# ada\n{READS}\n") == READS
    assert line_of("# ada\nreads:\n") == ""
    # A cut never splits a character.
    cut = line_of("reads: " + "é" * 60)
    assert cut.endswith("the page holds the rest]") and "\ufffd" not in cut
