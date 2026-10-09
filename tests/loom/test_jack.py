"""The jack speaks the hook protocol and fails open."""

import json
import threading
import time
from pathlib import Path

from brr.daemon2.facts import Fact

from brr.loom.runtime.adapters import claude_argv
from brr.loom.runtime.jack import execute, run
from brr.loom.runtime.port import parse_boundary, render_boundary

STRAND = "s-ab12-aaaaaa"


def _room(tmp_path) -> Path:
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    return room


def _letter(body="ping-1\nmore"):
    return Fact(
        kind="letter", by="person:p-test", id="p-test/abcde",
        data={"id": "p-test/abcde", "to": "thread:ta", "body": body, "from": "s-ab12-bbbbbb"},
    )


def _write(room: Path, letters, shown=None) -> str:
    text = render_boundary(
        STRAND, 1, "ta", letters, shown or set(), {"s-ab12-bbbbbb": "tb"},
    )
    (room / "port" / "in" / "boundary.md").write_text(text)
    return text


def test_boundary_round_trip_compacts_what_was_shown():
    letter = _letter()
    full = render_boundary(STRAND, 1, "ta", [letter], set(), {"s-ab12-bbbbbb": "tb"})
    parsed = parse_boundary(full)
    assert parsed.ids == ["p-test/abcde"]
    assert parsed.gen == 1
    assert parsed.letters[0].body == "ping-1\nmore"
    assert parsed.letters[0].reply == "thread:tb"
    assert parsed.letters[0].compact is False
    compact = render_boundary(
        STRAND, 1, "ta", [letter], {"p-test/abcde"}, {"s-ab12-bbbbbb": "tb"},
    )
    again = parse_boundary(compact)
    assert again.letters[0].compact is True
    assert again.letters[0].body == "ping-1"
    assert again.ids == parsed.ids


def test_post_with_nothing_owed_prints_nothing(tmp_path):
    room = _room(tmp_path)
    assert execute("post", room, "{}") == ""


def test_stop_blocks_while_letters_are_owed(tmp_path):
    room = _room(tmp_path)
    text = _write(room, [_letter()])
    payload = json.loads(execute("stop", room, "{}"))
    assert payload["decision"] == "block"
    assert payload["reason"] == text
    state = json.loads((room / "port" / "jack-state.json").read_text())
    assert state["stop_blocks"] == 1
    assert list((room / "port" / "out").glob("*.md"))


def test_three_blocks_then_the_stop_is_allowed(tmp_path):
    room = _room(tmp_path)
    _write(room, [_letter()])
    for _ in range(3):
        assert json.loads(execute("stop", room, "{}"))["decision"] == "block"
    assert execute("stop", room, "{}") == ""
    assert json.loads((room / "port" / "jack-state.json").read_text())["stop_blocks"] == 3
    posted = json.loads(execute("post", room, "{}"))
    assert posted["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert json.loads(execute("stop", room, "{}"))["decision"] == "block"


def test_stop_polls_until_a_letter_appears(tmp_path):
    room = _room(tmp_path)
    (room / "port" / "wait").write_text("2\n")

    def arrive():
        time.sleep(0.3)
        _write(room, [_letter("ping-1")])

    threading.Thread(target=arrive).start()
    started = time.monotonic()
    payload = json.loads(execute("stop", room, "{}"))
    elapsed = time.monotonic() - started
    assert payload["decision"] == "block"
    assert elapsed < 2


def test_molt_pending_allows_the_stop_while_letters_are_owed(tmp_path):
    room = _room(tmp_path)
    _write(room, [_letter()])
    (room / "port" / "molt-pending").write_text("fresh body\n")
    assert execute("stop", room, "{}") == ""
    assert list((room / "port" / "out").glob("*.md")) == []


def test_corrupt_boundary_fails_open(tmp_path):
    room = _room(tmp_path)
    (room / "port" / "in" / "boundary.md").write_text("{{{\n")
    code, out = run("stop", room, "")
    assert (code, out) == (0, "")
    assert "Traceback" in (room / "port" / "jack-errors.log").read_text()


def test_claude_adapter_pins_the_three_hooks(tmp_path):
    room = tmp_path / "rooms" / STRAND
    (room / "port").mkdir(parents=True)
    (room / "port" / "wake.md").write_text("wake\n")
    argv = claude_argv(room, "haiku", 10)
    assert argv[:4] == ["claude", "-p", "--model", "haiku"]
    assert "--dangerously-skip-permissions" in argv
    assert argv[-1] == "wake\n"
    settings = json.loads((room / ".claude" / "settings.json").read_text())
    hooks = settings["hooks"]
    assert hooks["PostToolUse"][0]["matcher"] == ".*"
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == 70
    for event in ("post", "stop", "start"):
        blob = json.dumps(hooks)
        assert f"--event {event}" in blob
        assert "--shell claude" in blob
