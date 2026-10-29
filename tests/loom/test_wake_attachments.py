"""Connector attachments must reach both the waking and the live body."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.connector import ingest
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _boundary, _prepare
from brr.loom.runtime.port import parse_boundary
from brr.loom.runtime.project import sender_threads

from _step import mail, write_thread

STRAND = "s-aaaa-attach"


@pytest.fixture
def home(tmp_path):
    root = tmp_path / "home with spaces"
    write_thread(root, "inbox", "Brnrd", "quit-now")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="lease", by="loom:aaaa", id="lease:inbox:1", data={
        "thread": "inbox", "strand": STRAND, "gen": 1}))
    return home


def receive(home, tmp_path, *, attached=True, missing=""):
    paths = []
    if attached:
        photo = tmp_path / "0.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xe0photo payload\xff\xd9")
        paths.append(photo)
    ingest(home, "fake", dict(id="photo", kind="letter", to="channel:fake/42",
                             body="look at this\nsecond caption line", missing=missing), paths)
    facts = read_facts(home)
    letter = next(f for f in facts if f.id == "letter:fake:photo")
    return facts, letter


def assert_attachment(text, home, facts):
    rows = [line.strip() for line in text.splitlines() if line.strip().startswith("attachment:")]
    assert len(rows) == 1
    blob = next(f for f in facts if f.kind == "blob")
    assert rows[0].startswith(f"attachment: image/jpeg · {blob.data['size']} bytes · ")
    path = Path(rows[0].split(" · ", 2)[2])
    assert path.is_absolute()
    assert path == home.root / "blobs" / blob.data["sha"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == blob.data["sha"]


@pytest.mark.parametrize("attached,missing", [(True, ""), (True, "1 3"), (False, "0")])
def test_source_attachments_reach_wake_and_initial_boundary(home, tmp_path, attached, missing):
    facts, letter = receive(home, tmp_path, attached=attached, missing=missing)
    room = _prepare(home, STRAND, "inbox", 1, mail(home.root, facts, "inbox"),
                    sender_threads(facts), facts, "hand")
    wake = (room / "port/wake.md").read_text()
    boundary = (room / "port/in/boundary.md").read_text()
    assert f"### {letter.id} from " in wake
    assert "look at this\nsecond caption line" in wake
    for text in (wake, boundary):
        if attached:
            assert_attachment(text, home, facts)
        else:
            assert "attachment:" not in text
        for number in missing.split():
            assert text.count(f"attachment {number} could not be fetched") == 1
    parsed = parse_boundary(boundary)
    assert letter.id in parsed.ids
    assert "second caption line" in next(f.body for f in parsed.letters if f.id == letter.id)


@pytest.mark.parametrize("compact", [False, True])
def test_new_boundary_letter_and_compact_redelivery_keep_attachments(home, tmp_path, compact):
    facts = read_facts(home)
    room = _prepare(home, STRAND, "inbox", 1, [], sender_threads(facts), facts, "hand")
    assert "attachment:" not in (room / "port/wake.md").read_text()
    facts, letter = receive(home, tmp_path, missing="1")
    if compact:
        append(home, Fact(kind="shown", by=f"strand:{STRAND}", id="shown:photo", data={
            "strand": STRAND, "gen": 1, "ids": [letter.id]}))
        facts = read_facts(home)
    _boundary(home, facts, STRAND, 1, SimpleNamespace(thread="inbox", fuel="", why=""))
    text = (room / "port/in/boundary.md").read_text()
    parsed = next(f for f in parse_boundary(text).letters if f.id == letter.id)
    assert parsed.compact is compact
    assert ("second caption line" in parsed.body) is not compact
    assert_attachment(text, home, facts)
    assert "attachment 1 could not be fetched" in parsed.body
    # The jack must deliver it, rather than fail open on attachment continuations.
    delivered = execute("post", room, "{}")
    assert "attachment: image/jpeg" in delivered
    assert "attachment 1 could not be fetched" in delivered


def test_same_blob_in_another_source_does_not_replace_mime(home, tmp_path):
    facts, letter = receive(home, tmp_path)
    same = tmp_path / "0.png"
    same.write_bytes((tmp_path / "0.jpg").read_bytes())
    ingest(home, "fake", dict(id="other", kind="letter", to="channel:fake/42",
                             body="same bytes, different filename"), [same])
    facts = read_facts(home)
    room = _prepare(home, STRAND, "inbox", 1, mail(home.root, facts, "inbox"),
                    sender_threads(facts), facts, "hand")
    wake = (room / "port/wake.md").read_text()
    occasions = {section.split(" from ")[0]: section for section in wake.split("### ")[1:]}
    assert_attachment(occasions[letter.id], home, facts)
    assert "attachment: image/png" in occasions["letter:fake:other"]
    boundary = parse_boundary((room / "port/in/boundary.md").read_text())
    bodies = {f.id: f.body for f in boundary.letters}
    assert "attachment: image/jpeg" in bodies[letter.id]
    assert "attachment: image/png" in bodies["letter:fake:other"]


@pytest.mark.parametrize("jack", ["hand", "hold"])
def test_loom_part_points_at_wishes(home, jack):
    facts = read_facts(home)
    room = _prepare(home, STRAND, "inbox", 1, [], sender_threads(facts), facts, jack)
    part = (room / "port/loom.md").read_text()
    assert "what is left on a wish lives on it: `wish <w-id> --left" in part
    assert "the stop asks for it once a wish moved" in part
    assert "Stamp what someone wants with `send --wish`" in part
    # The rest the loom keeps rides in the core, once: a typed park, then stop; never a sleep loop.
    from brr.loom.runtime.waits import GRAMMAR
    core = (room / "port/core.md").read_text()
    assert f'--read "waiting: <what>" --wait <{GRAMMAR}>`' in core
    assert core.count("Stopping is resting, not ending") == 1 and "A sleep or poll loop is never the wait" in core
    assert core.index("## memory/stance.md") < core.index("## loom briefing")
    assert "Stopping is resting" not in part and "poll loop" not in part
