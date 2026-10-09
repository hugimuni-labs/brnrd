"""Two holes the parent's read of step 3 found: a restarted loom, a stem as --re."""

from brr.daemon2.facts import Fact
from brr.loom.runtime import ledger
from brr.loom.runtime.home import Home
from brr.loom.runtime.loom import _recover
from brr.loom.runtime.port import parse_frontmatter, render_boundary, write_send
from brr.loom.runtime.project import holder


def _home(tmp_path) -> Home:
    home = Home(tmp_path, install="aaaa")
    (home.thread_dir("t")).mkdir(parents=True)
    (home.thread_dir("t") / "README.md").write_text("# t\n")
    return home


def _lease(home: Home, strand: str) -> None:
    ledger.append(home, Fact(kind="lease", by="loom:aaaa", id="lease:t:1",
                             data={"thread": "t", "strand": strand, "gen": 1}))


def test_a_restarted_loom_releases_its_dead_holders(tmp_path):
    home = _home(tmp_path)
    _lease(home, "s-aaaa-abc123")
    _recover(home)
    assert holder(ledger.read_facts(home), "t") is None


def test_a_restart_leaves_a_fused_strand_held(tmp_path):
    home = _home(tmp_path)
    _lease(home, "s-aaaa-abc123")
    ledger.append(home, Fact(kind="attention", by="loom:aaaa",
                             id="attention:fuse:s-aaaa-abc123",
                             data={"why": "fuse", "thread": "t"}))
    _recover(home)
    assert holder(ledger.read_facts(home), "t") == ("s-aaaa-abc123", 1)


def test_a_restart_leaves_another_installs_holder_alone(tmp_path):
    home = _home(tmp_path)
    _lease(home, "s-bbbb-abc123")
    _recover(home)
    assert holder(ledger.read_facts(home), "t") == ("s-bbbb-abc123", 1)


def test_a_bare_stem_in_re_becomes_the_owed_letter_id(tmp_path):
    home = _home(tmp_path)
    room = home.room("s-aaaa-abc123")
    letter = Fact(kind="letter", by="person:p-test", id="p-test/imf8b", data={
        "id": "p-test/imf8b", "to": "thread:t", "from": "p-test", "body": "ping-1"})
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "in" / "boundary.md").write_text(
        render_boundary("s-aaaa-abc123", 1, "t", [letter], set(), {}))
    write_send(room, to="thread:t", sender="s-aaaa-abc123", body="pong", re="imf8b")
    [out] = list((room / "port" / "out").glob("*.md"))
    assert parse_frontmatter(out.read_text())["re"] == "p-test/imf8b"
