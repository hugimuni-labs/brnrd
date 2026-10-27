"""The home thread forwarding a person letter carries the wish it says."""
import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.selfrepo import init_self, room
from brr.loom.runtime.wishes import wishes
from test_wake import cli, commit, write_thread


@pytest.fixture
def brnrd(tmp_path):
    root = tmp_path / "home"
    init_self(root, person="test")
    for name in ("work", "other"):
        write_thread(root / "self", name, name.title())
    commit(root / "self", "threads")
    clone = room(root, "s-aaaa-test")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="router", by="loom:aaaa", data={"gen": 1}))
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": "inbox", "strand": "s-aaaa-test", "gen": 1,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))
    return home, clone.parent


def run(seat, *args):
    result = cli([args[0], "--room", str(seat), *args[1:]])
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def said(home, letter):
    return [w for w in wishes(read_facts(home)).values() if any(s.letter == letter for s in w.says)]


def test_first_forward_moves_the_wish_it_stamps(brnrd):
    home, seat = brnrd
    letter = inject_letter(home.root, to="thread:inbox", body="Please build the switch")
    run(seat, "send", "--re", letter, "--to", "thread:work", "--wish", "new:Build the switch", "Yours")
    [wish] = said(home, letter)
    assert wish.thread == "work" and not wish.parent


def test_later_forward_makes_one_child_and_leaves_the_wish(brnrd):
    home, seat = brnrd
    letter = inject_letter(home.root, to="thread:inbox", body="Please build the switch")
    ident = run(seat, "wish", "new", "--line", "Build the switch", "--say", letter)
    assert wishes(read_facts(home))[ident].thread == "inbox"
    run(seat, "send", "--re", letter, "--to", "thread:work", "Yours")
    assert wishes(read_facts(home))[ident].thread == "work"
    run(seat, "send", "--re", letter, "--to", "thread:other", "Yours too")
    run(seat, "send", "--re", letter, "--to", "thread:other", "Again")
    known = wishes(read_facts(home))
    children = [w for w in known.values() if w.parent == ident]
    assert known[ident].thread == "work"
    assert [(w.thread, w.line, w.sender) for w in children] == [("other", "Build the switch", "person")]
    assert [s.letter for s in children[0].says] == [letter]


def test_a_note_home_moves_nothing(brnrd):
    home, seat = brnrd
    letter = inject_letter(home.root, to="thread:inbox", body="Please build the switch")
    ident = run(seat, "wish", "new", "--line", "Build the switch", "--say", letter)
    run(seat, "send", "--re", letter, "--to", "thread:inbox", "--note", "kept")
    assert wishes(read_facts(home))[ident].thread == "inbox"
