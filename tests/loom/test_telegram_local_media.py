"""The local Telegram connector: every medium is a file of its letter, an album one letter.

Fixtures follow Telegram's documented Update shape (core.telegram.org/bots/api#message);
they run through the connector's own ``poll``, then ``ingest`` and the attachment lines."""

import importlib.machinery
import importlib.util
import json
import threading
from pathlib import Path

import pytest

from brr.loom.runtime import connector
from brr.loom.runtime.connector import ingest
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.port import letter_attachments

FOLDER = Path(connector.__file__).parent / "connectors/telegram"
OGG, PDF, MP4, WEBP = b"OggS\0voice", b"%PDF-1.7 notes", b"\0\0\0\x18ftypisom" + b"\0" * 8, b"RIFF\0\0\0\0WEBPVP8 "


@pytest.fixture
def tin(monkeypatch, tmp_path):
    monkeypatch.setenv("BRNRD_STATE", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    # The relay's helper is also named ``common``: load this connector's own.
    import sys
    monkeypatch.delitem(sys.modules, "common", raising=False)
    monkeypatch.syspath_prepend(str(FOLDER))
    loader = importlib.machinery.SourceFileLoader("telegram_in", str(FOLDER / "in"))
    spec = importlib.util.spec_from_loader("telegram_in", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    yield module
    sys.modules.pop("common", None)


def message(mid, chat=42, **media):
    return {"message_id": mid, "date": 1795000000, "chat": {"id": chat, "type": "private"},
            "from": {"id": 7, "is_bot": False, "first_name": "Ar"}, **media}


def run(tin, monkeypatch, drop, *batches, files=None):
    """Drive the connector's poll over these getUpdates batches, then stop."""
    stop = threading.Event()
    queue = [list(b) for b in batches]
    fetched = []

    def call(method, payload, **kw):
        assert method == "getUpdates"
        if not queue:
            stop.set()
            return []
        return queue.pop(0)
    monkeypatch.setattr(tin, "call", call)
    monkeypatch.setattr(tin, "download", lambda file_id: fetched.append(file_id) or (files or {})[file_id])
    tin.poll(drop, stop)
    return fetched


def letters(tmp_path, drop):
    """Each drop through the loom's own boundary (``addressed``, then ``ingest``)."""
    from _step import write_thread
    from brr.loom.runtime.connector import addressed
    root = tmp_path / "home"
    write_thread(root, "inbox", "Brnrd", "quit-now")
    home = Home(root, install="aaaa")
    out = {}
    for md in sorted(drop.glob("*.md")):
        text = md.read_text()
        from brr.loom.runtime.port import parse_frontmatter
        fm = parse_frontmatter(text)
        d = md.with_suffix(".d")
        paths = sorted(d.iterdir()) if d.exists() else []
        out[fm["id"]] = (fm, [p.name for p in paths])
        ingest(home, "fake", addressed("fake", fm), paths)
    return home, out


def test_every_medium_arrives_as_a_typed_file(tin, monkeypatch, tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    updates = [
        {"update_id": 1, "message": message(1, voice={"file_id": "v", "file_unique_id": "uv", "duration": 2,
                                                      "mime_type": "audio/ogg", "file_size": 10})},
        {"update_id": 2, "message": message(2, caption="the notes", document={
            "file_id": "d", "file_unique_id": "ud", "file_name": "notes.pdf", "mime_type": "application/pdf"})},
        {"update_id": 3, "message": message(3, video={"file_id": "m", "file_unique_id": "um", "width": 4,
                                                      "height": 3, "duration": 1, "mime_type": "video/mp4"})},
        {"update_id": 4, "message": message(4, video_note={"file_id": "n", "file_unique_id": "un",
                                                           "length": 240, "duration": 1})},
        {"update_id": 5, "message": message(5, sticker={"file_id": "s", "file_unique_id": "us", "type": "regular",
                                                        "width": 512, "height": 512, "is_animated": False,
                                                        "is_video": False})},
        {"update_id": 6, "message": message(6, video={"file_id": "big", "file_unique_id": "ub", "width": 4,
                                                      "height": 3, "duration": 99, "file_size": 30 * 2**20})},
    ]
    fetched = run(tin, monkeypatch, drop, updates,
                  files={"v": OGG, "d": PDF, "m": MP4, "n": MP4, "s": WEBP})
    assert "big" not in fetched  # past a bot's 20 MB getFile: never asked for
    home, out = letters(tmp_path, drop)
    assert {k: v[1] for k, v in out.items()} == {
        "42:1": ["0.ogg"], "42:2": ["0.pdf"], "42:3": ["0.mp4"], "42:4": ["0.mp4"], "42:5": ["0.webp"], "42:6": []}
    assert out["42:1"][0]["body"] == ""  # a voice with no caption is still a letter
    assert out["42:6"][0]["large"] == f"0:{30 * 2**20}"
    lines = letter_attachments(home.root, read_facts(home))
    assert lines["letter:fake:42:1"][0].startswith("attachment: voice message (audio/ogg")
    assert lines["letter:fake:42:2"][0].startswith("attachment: PDF document (application/pdf")
    assert lines["letter:fake:42:3"][0].startswith("attachment: video (video/mp4")
    assert lines["letter:fake:42:5"][0].startswith("attachment: photo (image/webp")
    assert lines["letter:fake:42:6"] == [
        "attachment 1 (30.0 MB) is past the size the connector may fetch and did not come: ask for it smaller, or as a link"]


def album(first_update, group="g1"):
    return [{"update_id": first_update + i, "message": message(
        10 + i, media_group_id=group, **({"caption": "the page"} if i == 0 else {}),
        photo=[{"file_id": f"p{i}-small", "file_unique_id": f"s{i}", "width": 90, "height": 60},
               {"file_id": f"p{i}", "file_unique_id": f"u{i}", "width": 960, "height": 1280}])}
        for i in range(3)]


def test_an_album_in_one_batch_is_one_letter_with_its_files(tin, monkeypatch, tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    run(tin, monkeypatch, drop, album(1), files={f"p{i}": b"\xff\xd8\xff" + bytes([i]) for i in range(3)})
    home, out = letters(tmp_path, drop)
    assert list(out) == ["42:10"]
    assert out["42:10"][1] == ["0.jpg", "1.jpg", "2.jpg"] and out["42:10"][0]["body"] == "the page"
    [lines] = letter_attachments(home.root, read_facts(home)).values()
    assert [line.split(":")[0] for line in lines] == ["attachment 1 of 3", "attachment 2 of 3", "attachment 3 of 3"]
    assert (Path(tin.state()) / "cursor").read_text() == "4"


def test_an_album_split_across_batches_loses_nothing(tin, monkeypatch, tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir()
    items = album(1)
    run(tin, monkeypatch, drop, items[:2], items[2:], files={f"p{i}": b"\xff\xd8\xff" + bytes([i]) for i in range(3)})
    _home, out = letters(tmp_path, drop)
    assert {k: v[1] for k, v in out.items()} == {"42:10": ["0.jpg", "1.jpg"], "42:12": ["0.jpg"]}
