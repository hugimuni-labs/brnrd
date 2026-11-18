"""Connector attachments must reach both the waking and the live body."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.attachment import path_of
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
    assert rows[0].startswith(f"attachment: photo (image/jpeg · {blob.data['size']} bytes) · ")
    path = Path(path_of(rows[0]))
    assert path.is_absolute()
    assert path == home.root / "blobs" / (blob.data["sha"] + ".jpg")
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
    assert "attachment: photo (image/jpeg" in delivered
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
    assert "attachment: photo (image/png" in occasions["letter:fake:other"]
    boundary = parse_boundary((room / "port/in/boundary.md").read_text())
    bodies = {f.id: f.body for f in boundary.letters}
    assert "attachment: photo (image/jpeg" in bodies[letter.id]
    assert "attachment: photo (image/png" in bodies["letter:fake:other"]


@pytest.mark.parametrize("jack", ["hand", "hold"])
def test_loom_part_points_at_wishes(home, jack):
    facts = read_facts(home)
    room = _prepare(home, STRAND, "inbox", 1, [], sender_threads(facts), facts, jack)
    part = (room / "port/loom.md").read_text()
    # How to reply, leave and end rides in the core's briefing, once, not in the loom part.
    from brr.loom.runtime.waits import GRAMMAR
    core = (room / "port/core.md").read_text()
    assert "What is left on a wish lives on it: `wish <w-id> --left" in core
    assert "Stamp what someone wants with `send --wish`" in core
    assert "--result pr:<url> --line" in core and "--deliver" in core
    assert "Stamp what someone wants" not in part and "--left" not in part
    assert f'--read "waiting: <what>" --wait <{GRAMMAR}>`' in core
    assert core.count("Stopping is resting, not ending") == 1 and "A sleep or poll loop is never the wait" in core
    assert core.index("## memory/stance.md") < core.index("## loom briefing")
    assert "Stopping is resting" not in part and "poll loop" not in part


@pytest.mark.parametrize("doors", [False, True])
def test_the_briefing_names_the_door_sentence_only_while_sensors_are_on(home, doors):
    from brr.loom.runtime import audit
    writer = audit.Writer(home, doors=doors)
    try:
        facts = read_facts(home)
        room = _prepare(home, STRAND, "inbox", 1, [], sender_threads(facts), facts, "hand")
    finally:
        writer.close()
    core = (room / "port/core.md").read_text()
    briefing = core[core.index("## loom briefing"):]
    assert ("BRNRD_SAY=" in briefing) is doors
    assert core.count("BRNRD_SAY") == (1 if doors else 0)


def _jpeg(width, height):
    """A JPEG's header as a camera writes it: SOI, an APP0, then the SOF0 that holds the size."""
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03" + b"\x01\x22\x00" * 3
    return b"\xff\xd8" + app0 + sof0 + b"\x00" * 64 + b"\xff\xd9"


def test_an_octet_stream_photo_reads_as_a_photo_above_the_conversation(home, tmp_path):
    """His 11349: the relay typed a 960x1280 JPEG octet-stream, the line sat under
    the conversation tail, and the body answered the caption without the page."""
    from brr.loom.runtime.port import PortError, write_send
    ingest(home, "fake", dict(id="before", kind="letter", to="channel:fake/42", body="earlier words"), [])
    facts = read_facts(home)
    append(home, Fact(kind="letter", by=f"strand:{STRAND}", id="letter:ours", data={
        "id": f"{STRAND}/ours", "from": f"strand:{STRAND}", "to": "channel:fake/42", "body": "our answer"}))
    blob = tmp_path / "0"  # no extension: mimetypes says nothing, the connector octet-stream
    blob.write_bytes(_jpeg(960, 1280))
    ingest(home, "fake", dict(id="page", kind="letter", to="channel:fake/42",
                             body="the page I wrote this morning"), [blob])
    facts = read_facts(home)
    stored = next(f for f in facts if f.kind == "blob")
    assert stored.data["mime"] == "application/octet-stream"
    letter = next(f for f in facts if f.id == "letter:fake:page")
    room = _prepare(home, STRAND, "inbox", 1, mail(home.root, facts, "inbox"),
                    sender_threads(facts), facts, "hand")
    wake = (room / "port/wake.md").read_text()
    boundary = (room / "port/in/boundary.md").read_text()
    for text in (wake, boundary):
        section = text[text.index(letter.id):]
        row = next(line.strip() for line in section.splitlines() if line.strip().startswith("attachment:"))
        assert row.startswith("attachment: photo (image/jpeg · 960x1280 · ")
        assert row.endswith("part of this letter: open it before answering")
        path = Path(path_of(row))
        assert path.suffix == ".jpg" and path.read_bytes() == blob.read_bytes()
        assert section.index("attachment:") < section.index("not a reply; the conversation before it")
    ident = next(v.id for v in parse_boundary(boundary).letters if "the page I wrote" in v.body)
    with pytest.raises(PortError, match="never opened: .*\\.jpg"):
        write_send(room, to="channel:fake/42", sender=STRAND, body="nice caption", re=ident, chatter=[])
    # Said once: the second send goes as written.
    sent = write_send(room, to="channel:fake/42", sender=STRAND, body="nice caption", re=ident, chatter=[])
    assert sent.startswith(STRAND + "/") and any((room / "port/out").iterdir())


def test_a_reply_after_opening_the_file_is_not_refused(home, tmp_path):
    from brr.loom.runtime import tooltrace
    from brr.loom.runtime.attachment import unopened
    blob = tmp_path / "0"
    blob.write_bytes(_jpeg(4, 3))
    ingest(home, "fake", dict(id="page", kind="letter", to="channel:fake/42", body="look"), [blob])
    facts = read_facts(home)
    room = _prepare(home, STRAND, "inbox", 1, mail(home.root, facts, "inbox"),
                    sender_threads(facts), facts, "hand")
    ident = next(v.id for v in parse_boundary((room / "port/in/boundary.md").read_text()).letters)
    [path] = unopened(room, ident)
    import json
    for phase in ("pre", "post"):
        tooltrace.note(room, json.dumps({"tool_name": "Read", "tool_use_id": "t1",
                                         "tool_input": {"file_path": path}, "tool_response": "ok"}), phase)
    assert unopened(room, ident) == []
