"""A wish placed on a settled thread opens it again, and the next wake says so."""
import subprocess

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.selfrepo import init_self, room
from brr.loom.runtime.wishes import wake_wishes, wishes
from test_wake import cli, commit, write_thread


def settle(root):
    readme = root / "self/threads/work/README.md"
    readme.write_text(readme.read_text().replace("status: open", "status: settled"))
    commit(root / "self", "settle work: the switch shipped")


@pytest.fixture
def brnrd(tmp_path):
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root / "self", "work", "Work")
    commit(root / "self", "threads")
    settle(root)
    clone = room(root, "s-aaaa-test")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="router", by="loom:aaaa", data={"gen": 1}))
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": "brnrd", "strand": "s-aaaa-test", "gen": 1,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))
    return home, clone.parent


def run(*args):
    result = cli(list(args))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def state(home, thread):
    """The thread's state as the threads verb reads it, and its status line on main."""
    table = run("threads", "--home", str(home.root)).splitlines()
    head = table[0].split(" · ")
    row = next(dict(zip(head, line.split(" · "))) for line in table[1:] if line.startswith(thread + " · "))
    main = subprocess.run(["git", "show", f"main:threads/{thread}/README.md"], cwd=home.root / "self",
                          capture_output=True, text=True, check=True).stdout
    status = next(line.partition(": ")[2] for line in main.splitlines() if line.startswith("status:"))
    return row["now"], status


def test_a_forward_with_a_new_wish_reopens_a_settled_thread(brnrd):
    home, seat = brnrd
    assert state(home, "work")[0].startswith("settled")
    letter = inject_letter(home.root, to="thread:brnrd", body="The switch flickers, fix it")
    run("send", "--room", str(seat), "--re", letter, "--to", "thread:work",
        "--wish", "new:The switch stops flickering", "Yours")
    now, status = state(home, "work")
    assert status == "open" and not now.startswith("settled"), now
    facts = read_facts(home)
    [wish] = [w for w in wishes(facts).values() if w.line == "The switch stops flickering"]
    assert wish.thread == "work"
    [reopened] = [f for f in facts if f.kind == "thread.reopened"]
    assert reopened.data["thread"] == "work" and reopened.data["wish"] == wish.id
    assert reopened.data["was"] == "settled" and reopened.data["why"] == "the switch shipped"
    wake = wake_wishes(facts, "work")
    assert f"reopened {reopened.at[5:16].replace('T', ' ')}: {wish.id} placed by s-aaaa-test" in wake
    assert "the switch shipped" in wake


def test_a_steer_on_a_settled_threads_wish_reopens_it(brnrd):
    home, seat = brnrd
    first = inject_letter(home.root, to="thread:brnrd", body="Build the switch")
    ident = run("wish", "new", "--room", str(seat), "--line", "Build the switch", "--say", first,
                "--thread", "work")
    run("wish", ident, "--room", str(seat), "--accept", "word")
    settle(home.root)
    assert state(home, "work")[1] == "settled"
    steer = inject_letter(home.root, to="thread:brnrd", body="Make it dimmable too")
    run("wish", ident, "--room", str(seat), "--say", steer)
    assert state(home, "work")[1] == "open"
    assert wishes(read_facts(home))[ident].open
    assert [f.data["wish"] for f in read_facts(home) if f.kind == "thread.reopened"] == [ident, ident]


def test_an_open_thread_takes_a_wish_without_a_reopen(brnrd):
    home, seat = brnrd
    first = inject_letter(home.root, to="thread:brnrd", body="One")
    run("send", "--room", str(seat), "--re", first, "--to", "thread:work", "--wish", "new:One", "Yours")
    second = inject_letter(home.root, to="thread:brnrd", body="Two")
    run("send", "--room", str(seat), "--re", second, "--to", "thread:work", "--wish", "new:Two", "Yours")
    assert len([f for f in read_facts(home) if f.kind == "thread.reopened"]) == 1


def test_a_wish_placed_from_no_room_on_a_settled_thread_is_refused(brnrd):
    home, _ = brnrd
    letter = inject_letter(home.root, to="thread:brnrd", body="Build the switch")
    result = cli(["wish", "new", "--home", str(home.root), "--line", "Build it", "--say", letter,
                  "--thread", "work"])
    assert result.returncode == 1 and "reopens it" in result.stderr
    assert state(home, "work")[1] == "settled"
    assert not [w for w in wishes(read_facts(home)).values() if w.thread == "work"]
