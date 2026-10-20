"""The self's core rides in a body's system slot; the user turn is the occasion."""

import json
import sys
import tomllib
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import loom
from brr.loom.runtime.adapters import resolve
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.project import sender_threads

from _step import mail, write_thread

STRAND = "s-aaaa-anchor"


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "quit-now")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="lease", by="loom:aaaa", id="lease:brnrd:1", data={
        "thread": "brnrd", "strand": STRAND, "gen": 1}))
    # A shell on PATH that records the argv it was exec'd with.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex", "grok"):
        shell = bin_dir / name
        shell.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                         "open(os.environ['ARGV_OUT'], 'w').write(json.dumps(sys.argv))\n")
        shell.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin")
    return home


def prepare(home):
    facts = read_facts(home)
    return loom._prepare(home, STRAND, "brnrd", 1, mail(home.root, facts, "brnrd"),
                         sender_threads(facts), facts, "auto")


def exec_body(home, room, shell, tmp_path, monkeypatch):
    out = tmp_path / f"{shell}.argv"
    monkeypatch.setenv("ARGV_OUT", str(out))
    run = resolve(home, shell) / "run"
    proc, log = loom._spawn(room, [str(run), str(room), "m"], shell, "brnrd")
    try:
        assert proc.wait(timeout=60) == 0, (room / "port/body.log").read_text()
    finally:
        log.close()
    return json.loads(out.read_text())


def test_core_is_split_out_and_the_wake_stays_whole(home):
    room = prepare(home)
    identity = (room / "self/core/identity.md").read_text()
    stance = (room / "self/memory/stance.md").read_text()
    core = (room / "port/core.md").read_text()
    occasion = (room / "port/occasion.md").read_text()
    assert identity in core and stance in core
    assert identity not in occasion and stance not in occasion
    assert "## threads/brnrd/README.md" in occasion and "## Loom" in occasion
    # A body that reads only BRNRD_WAKE still gets the core, at the head.
    assert (room / "port/wake.md").read_text() == core + occasion


def test_claude_and_codex_carry_the_core_in_their_system_slot(home, tmp_path, monkeypatch):
    room = prepare(home)
    core = (room / "port/core.md").read_text()
    identity = (room / "self/core/identity.md").read_text()

    argv = exec_body(home, room, "claude", tmp_path, monkeypatch)
    flag = argv.index("--append-system-prompt-file")
    assert Path(argv[flag + 1]).read_text() == core
    assert "--system-prompt" not in argv and "--system-prompt-file" not in argv
    assert argv[-2] == "--" and identity not in argv[-1]
    assert "## Occasion" in argv[-1]

    argv = exec_body(home, room, "codex", tmp_path, monkeypatch)
    flag = argv.index("-c", argv.index("-c") + 1)
    assert tomllib.loads(argv[flag + 1]) == {"developer_instructions": core}
    assert argv[-2] == "--" and identity not in argv[-1]
    assert "## Occasion" in argv[-1]

    # Grok's --rules is appended to its own system prompt.
    argv = exec_body(home, room, "grok", tmp_path, monkeypatch)
    assert argv[argv.index("--rules") + 1].endswith("\n\n" + core)
    assert Path(argv[argv.index("--prompt-file") + 1]) == room / "port/occasion.md"


def test_a_recipe_that_keeps_the_core_loses_nothing(home, tmp_path, monkeypatch):
    # An older self recipe ignores LOOM_CORE and prints the core itself.
    real = loom.run_wake
    monkeypatch.setattr(loom, "run_wake", lambda *a, core=None, **k: real(*a, **k))
    room = prepare(home)
    identity = (room / "self/core/identity.md").read_text()
    assert (room / "port/core.md").read_text() == ""
    assert identity in (room / "port/wake.md").read_text()

    argv = exec_body(home, room, "claude", tmp_path, monkeypatch)
    assert "--append-system-prompt-file" not in argv
    assert identity in argv[-1]
    argv = exec_body(home, room, "codex", tmp_path, monkeypatch)
    assert not any(arg.startswith("developer_instructions=") for arg in argv)
    assert identity in argv[-1]
