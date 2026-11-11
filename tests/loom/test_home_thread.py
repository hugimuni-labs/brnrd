"""The home thread is inbox, with no brnrd alias left."""

import shutil

from brr.daemon2.facts import Fact
from brr.loom.runtime.connector import ingest
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.owners import letter_owners, letters_for

from brr.loom.runtime.selfrepo import _commit, git, init_self

from _step import write_thread


def make(tmp_path, *threads):
    home = Home(tmp_path / "home", install="aaaa")
    for thread in threads:
        write_thread(home.root, thread, thread.title(), "quit-now")
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    person = home.root / "self/people/ada"
    person.mkdir(parents=True, exist_ok=True)
    (person / "channels.toml").write_text('channels = ["mine:42"]\n')
    # init_self seeds inbox; a legacy home is one born while the home thread was brnrd.
    if "inbox" not in threads:
        shutil.rmtree(home.thread_dir("inbox"), ignore_errors=True)
        git(home.root / "self", "add", "-A")
        _commit(home.root / "self", "Legacy home.\n\nLoom-Label: taint=0; audience=self\n")
    return home


def stranger_message(home):
    """A message from a chat nobody has spoken in: only the home thread can take it."""
    ingest(home, "mine", dict(id="hello", kind="letter", to="channel:mine/42", body="hi", **{"from": "mine:42"}), [])
    return next(f for f in read_facts(home) if f.id == "letter:mine:hello")


def test_a_new_self_is_born_with_inbox(tmp_path):
    home = Home(tmp_path / "home", install="aaaa")
    init_self(home.root)
    assert home.thread_dir("inbox").joinpath("README.md").is_file()
    assert not home.thread_dir("brnrd").exists()
    assert home.main_thread() == "inbox"


def test_routable_reuses_main_tree_and_sees_the_next_commit(tmp_path, monkeypatch):
    from brr.loom.runtime import lifecycle
    home = make(tmp_path, "inbox", "work")
    calls, real = [], lifecycle.git

    def counted(*args, **kwargs):
        calls.append(args[1:])
        return real(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "git", counted)
    assert home.routable("work")
    calls.clear()
    for _ in range(10):
        assert home.routable("work")
        assert not home.routable("missing")
    assert calls == []
    # An unlanded folder is still not routable; main moving retires the tree.
    path = home.thread_dir("missing") / "README.md"
    path.parent.mkdir()
    path.write_text("# arrived\n")
    assert not home.routable("missing")
    git(home.root / "self", "add", "-A")
    _commit(home.root / "self", "Land missing.\n\nLoom-Label: taint=0; audience=self\n")
    assert home.routable("missing")
    assert len(calls) == 1 and calls[0][0] == "ls-tree"


def test_boundary_owner_inventory_is_cached_by_commit(tmp_path, monkeypatch):
    import subprocess
    from brr.loom.runtime import grants, guards
    home = make(tmp_path, "inbox")
    repo = home.root / "self"
    agreement = repo / "people/ada/agreement.md"
    agreement.write_text("the agreement\n")
    git(repo, "add", "-A")
    _commit(repo, "Agreement.\n\nLoom-Label: taint=0; audience=self\n")
    calls, real = [], guards._git

    def counted(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, 1, "", "temporarily unreadable")
        return real(*args, **kwargs)

    monkeypatch.setattr(guards, "_git", counted)
    assert grants.owners(repo) == set()
    assert grants.owners(repo) == {"person:ada"}
    grants.owners(repo).clear()  # A caller cannot mutate the cache.
    assert grants.owners(repo) == {"person:ada"}
    assert len(calls) == 2
    agreement.unlink()
    git(repo, "add", "-A")
    _commit(repo, "Withdraw agreement.\n\nLoom-Label: taint=0; audience=self\n")
    assert grants.owners(repo) == set()
    assert len(calls) == 3


def test_inbox_is_the_home_thread_even_beside_a_brnrd_thread(tmp_path):
    home = make(tmp_path, "brnrd", "work")
    assert home.main_thread() == "inbox"
    assert stranger_message(home).data["to"] == "thread:inbox"


def test_default_lands_on_inbox_when_it_exists(tmp_path):
    home = make(tmp_path, "inbox", "work")
    assert stranger_message(home).data["to"] == "thread:inbox"


def test_a_letter_to_brnrd_is_not_rerouted_to_inbox(tmp_path):
    home = make(tmp_path, "inbox")
    append(home, Fact(kind="letter", by="person:ada", id="p-ada/old", data={
        "id": "p-ada/old", "from": "p-ada", "to": "thread:brnrd", "body": "still says brnrd"}))
    facts = read_facts(home)
    main = main_snapshot(home)
    fate = letter_owners(facts, main)["p-ada/old"]
    assert fate.thread == "brnrd" and fate.action == "unroutable"
    assert letters_for(facts, main, "inbox") == []
