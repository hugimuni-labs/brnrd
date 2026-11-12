"""Asks, the strand↔thread graph and settling on a person's word, through the real verbs."""
import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter
from brr.loom.runtime.lifecycle import read_thread
from brr.loom.runtime.selfrepo import init_self, room
from test_wake import cli, commit, write_thread

DONE = ("Work\n\n## Answer\n\nShipped.\n\n## Done\n\n"
        "- pr https://example.test/pull/1 — the fix\n"
        "a prose line under Done is not a row\n"
        "- tweet https://example.test/x — not a kind\n")


def lease(home, thread, strand, gen):
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": thread, "strand": strand, "gen": gen,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))


def shown(home, strand, *ids):
    append(home, Fact(kind="shown", by="loom:aaaa", data={"strand": strand, "gen": 1, "ids": list(ids)}))


@pytest.fixture
def loom(tmp_path, monkeypatch):
    monkeypatch.delenv("BRNRD_ROOM", raising=False)
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root / "self", "inbox", "Inbox")
    write_thread(root / "self", "work", DONE)
    commit(root / "self", "threads")
    seat = room(root, "s-aaaa-work").parent
    room(root, "s-aaaa-inbx")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="router", by="loom:aaaa", data={"gen": 1}))
    lease(home, "inbox", "s-aaaa-inbx", 1)
    lease(home, "work", "s-aaaa-work", 1)
    return home, seat


def send(home, strand, *args):
    """A body's letter, taken in from its port the way the loom takes it."""
    from brr.loom.runtime.loom import _ingest
    ident = run("send", "--room", str(home.room(strand)), *args).strip()
    _ingest(home)
    return ident


def run(*args, ok=True):
    result = cli(list(args))
    assert result.returncode == (0 if ok else 1), result.stderr
    return result.stdout if ok else result.stderr


def test_graph_renders_both_ways_with_each_edge_origin(loom):
    home, seat = loom
    asked = inject_letter(home.root, to="thread:inbox", body="please fix it")
    shown(home, "s-aaaa-inbx", asked)
    dispatch = send(home, "s-aaaa-inbx", "--to", "thread:work", "--re", asked, "--chatter", "Spec: fix it")
    shown(home, "s-aaaa-work", dispatch)
    by_thread = run("graph", "--home", str(home.root))
    assert "inbox\n  s-aaaa-inbx · ran · person\n" in by_thread
    assert "work\n  s-aaaa-work · ran · thread:inbox\n  s-aaaa-inbx · sent · person\n" in by_thread
    by_strand = run("graph", "--home", str(home.root), "--strands", "s-aaaa-inbx")
    assert by_strand == "s-aaaa-inbx\n  inbox · ran · person\n  work · sent · person\n"


def test_unshown_strand_is_the_loom_s(loom):
    home, _seat = loom
    assert "work\n  s-aaaa-work · ran · loom\n" in run("graph", "--home", str(home.root))


def test_asks_lists_buckets_over_every_thread(loom):
    home, seat = loom
    pr = inject_letter(home.root, to="thread:work", body="PR #1 is open")
    merge = run("wish", "new", "--room", str(seat), "--say", pr, "--line", "merge #1").strip()
    ask = run("ask", "merge #1? rec: yes", "--room", str(seat), "--for", merge).strip()
    text = run("asks", "--home", str(home.root))
    # The plan is work's person wishes; the ask is not one of them.
    assert "in hand\ninbox · s-aaaa-inbx · plan —\nwork · s-aaaa-work · plan 0/1\n" in text
    assert f"yours\n{ask} · heard · merge #1? rec: yes · - · waits on operator\n" in text
    assert "done\n1 · asks --all\n" in text
    rows = run("asks", "--home", str(home.root), "--all", "--thread", "work")
    assert "work · pr https://example.test/pull/1 — the fix" in rows
    assert "not a kind" not in rows and "prose" not in rows
    assert read_thread(home, "work")["done"] == [("pr", "https://example.test/pull/1", "the fix")]


def test_operator_cli_reads_the_loom_asks(loom):
    import subprocess, sys
    home, _seat = loom
    result = subprocess.run([sys.executable, "-c", "import sys; from brr.cli import main; sys.exit(main())",
                             "asks", "--home", str(home.root)],
                            capture_output=True, text=True, env=cli.__globals__["_env"](
                                PYTHONPATH=str(cli.__globals__["SRC"])))
    assert result.returncode == 0, result.stderr
    assert "in hand\n" in result.stdout


def test_a_person_thread_with_nothing_in_hand_settles_without_a_word(loom):
    home, seat = loom
    run("settle", "work", "--room", str(seat), "--why", "Shipped")
    assert read_thread(home, "work")["status"] == "settled"


def test_a_person_thread_with_work_in_hand_settles_only_on_their_word(loom):
    home, seat = loom
    asked = inject_letter(home.root, to="thread:work", body="Please: fix it")
    run("wish", "new", "--room", str(seat), "--line", "Fix it", "--say", asked)
    error = run("settle", "work", "--room", str(seat), "--why", "Shipped", ok=False)
    assert "--word" in error
    other = inject_letter(home.root, to="thread:inbox", body="done")
    assert "--word" in run("settle", "work", "--room", str(seat), "--why", "Shipped",
                           "--word", other, ok=False)
    mine = send(home, "s-aaaa-inbx", "--to", "thread:work", "--re", other, "--chatter", "done")
    assert "--word" in run("settle", "work", "--room", str(seat), "--why", "Shipped",
                           "--word", mine, ok=False)
    assert read_thread(home, "work")["status"] == "open"
    said = inject_letter(home.root, to="thread:work", body="looks good, done")
    run("settle", "work", "--room", str(seat), "--why", "Shipped", "--word", said)
    assert read_thread(home, "work")["status"] == "settled"


def test_the_home_thread_settles_a_thread_whose_opener_runs_on_no_loom(loom):
    """co-maintainer-tick was opened by thread:arseni, whose `for:` is the maintainer's conversation: no body
    runs there, so its settle had no taker (2026-11-05). The home thread hears for such an opener."""
    home, seat = loom
    root = home.root / "self"
    write_thread(root, "maintainer", "Maintainer")
    path = root / "threads/maintainer/README.md"
    path.write_text(path.read_text().replace("tense: plan\n", "tense: plan\nfor: the conversation with the maintainer\n"))
    write_thread(root, "tick", "Tick")
    path = root / "threads/tick/README.md"
    path.write_text(path.read_text().replace("tense: plan\n", "tense: plan\nopened-by: thread:maintainer\n"))
    commit(root, "threads")
    error = run("settle", "tick", "--room", str(seat), "--why", "Retired", ok=False)
    assert "only thread:inbox may settle tick" in error
    run("settle", "tick", "--room", str(home.room("s-aaaa-inbx")), "--why", "Retired")
    assert read_thread(home, "tick")["status"] == "settled"


def test_an_opener_that_runs_here_still_settles_its_own_child_alone(loom):
    home, seat = loom
    root = home.root / "self"
    write_thread(root, "child", "Child")
    path = root / "threads/child/README.md"
    path.write_text(path.read_text().replace("tense: plan\n", "tense: plan\nopened-by: thread:work\n"))
    commit(root, "threads")
    error = run("settle", "child", "--room", str(home.room("s-aaaa-inbx")), "--why", "Done", ok=False)
    assert "only thread:work may settle child" in error
    run("settle", "child", "--room", str(seat), "--why", "Done")
    assert read_thread(home, "child")["status"] == "settled"


def test_asks_person_wait_uses_main_name_without_changing_opener(loom):
    home, seat = loom
    repo = home.root / "self"
    page = repo / "people/test/README.md"
    page.write_text("# Test Human Name\n")
    readme = repo / "threads/work/README.md"
    readme.write_text(readme.read_text().replace("status: open", "status: open\nopened-by: person:test"))
    commit(repo, "person opener")
    pr = inject_letter(home.root, to="thread:work", body="PR #1 is open")
    merge = run("wish", "new", "--room", str(seat), "--say", pr, "--line", "merge #1").strip()
    run("ask", "merge #1? rec: yes", "--room", str(seat), "--for", merge)
    text = run("asks", "--home", str(home.root))
    assert "waits on Test Human Name" in text
    assert read_thread(home, "work")["opened-by"] == "person:test"
