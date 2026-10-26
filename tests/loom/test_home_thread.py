"""The home thread is inbox; a home that still has only brnrd keeps working."""

import shutil

import pytest

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


@pytest.mark.parametrize("threads, expected", [
    (("brnrd",), "brnrd"),
    (("inbox",), "inbox"),
    (("brnrd", "inbox"), "inbox"),
    (("work",), "inbox"),
])
def test_resolver(tmp_path, threads, expected):
    assert make(tmp_path, *threads).main_thread() == expected


def test_default_lands_on_brnrd_in_a_legacy_home(tmp_path):
    home = make(tmp_path, "brnrd", "work")
    assert stranger_message(home).data["to"] == "thread:brnrd"


def test_default_lands_on_inbox_when_it_exists(tmp_path):
    home = make(tmp_path, "inbox", "work")
    assert stranger_message(home).data["to"] == "thread:inbox"


def test_old_brnrd_letter_routes_to_inbox(tmp_path):
    home = make(tmp_path, "inbox")
    append(home, Fact(kind="letter", by="person:ada", id="p-ada/old", data={
        "id": "p-ada/old", "from": "p-ada", "to": "thread:brnrd", "body": "still says brnrd"}))
    facts = read_facts(home)
    main = main_snapshot(home)
    fate = letter_owners(facts, main)["p-ada/old"]
    assert fate.thread == "inbox" and fate.action == "start"
    assert [f.id for f in letters_for(facts, main, "inbox")] == ["p-ada/old"]


def test_brnrd_letter_stays_on_brnrd_while_brnrd_exists(tmp_path):
    home = make(tmp_path, "inbox", "brnrd")
    append(home, Fact(kind="letter", by="person:ada", id="p-ada/old", data={
        "id": "p-ada/old", "from": "p-ada", "to": "thread:brnrd", "body": "x"}))
    assert letter_owners(read_facts(home), main_snapshot(home))["p-ada/old"].thread == "brnrd"
