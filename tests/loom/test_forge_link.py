"""A thread's forge link comes from the self's origin: the wake, the snapshot and `threads` carry it."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import snapshot
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _prepare
from brr.loom.runtime.project import sender_threads

from _step import write_thread

NEXT = "s-aaaa-000001"
SRC = str(Path(snapshot.__file__).resolve().parents[3])
URL = "https://github.com/acme/self/tree/main/threads/brnrd"


@pytest.mark.parametrize("origin, url", [
    ("git@github.com:acme/self.git", URL),
    ("https://x-access-token:ghs_secret@github.com/acme/self.git", URL),
    ("ssh://git@gitlab.com/acme/self", "https://gitlab.com/acme/self/-/tree/main/threads/brnrd"),
    (None, None),
    ("/srv/git/self.git", None),
    ("https://git.example.org/acme/self.git", None),
])
def test_the_thread_line_card_and_table_carry_the_origins_forge_link(tmp_path, origin, url):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "quit-now")
    if origin:
        subprocess.run(["git", "-C", str(root / "self"), "remote", "add", "origin", origin], check=True)
    home = Home(root, install="aaaa")
    append(home, Fact(kind="lease", by="loom:aaaa", id="lease:brnrd:1",
                      data={"thread": "brnrd", "strand": NEXT, "gen": 1}))
    facts = read_facts(home)

    room = _prepare(home, NEXT, "brnrd", 1, [], sender_threads(facts), facts, "hold")
    line = (room / "port/loom.md").read_text()
    assert line.startswith(f"you are strand {NEXT} on thread brnrd"
                           + (f" ({url}); " if url else "; "))

    card = next(t for t in snapshot.assemble(home, read_facts(home), load_config(root))["threads"]
                if t["id"] == "brnrd")
    assert card.get("url") == url

    table = subprocess.run([sys.executable, "-m", "brr", "threads", "--home", str(root)],
                           env={**os.environ, "PYTHONPATH": SRC}, capture_output=True, text=True, check=True)
    forge = [row for row in table.stdout.splitlines() if row.startswith("forge:")]
    assert forge == ([f"forge: {url.removesuffix('/brnrd')}/<thread>"] if url else [])
    assert "ghs_secret" not in line + str(card) + table.stdout
