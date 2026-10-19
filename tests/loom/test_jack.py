"""The jack speaks the hook protocol and fails open."""

import json
import threading
import time
from pathlib import Path

from brr.daemon2.facts import Fact

import os
import runpy
import sys
import pytest
from brr.loom.runtime.adapters import resolve
from brr.loom.runtime.home import Home
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
        kind="letter", by="strand:s-ab12-bbbbbb", id="p-test/abcde",
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


def test_opened_delivery_ignores_age_and_fuel_but_tracks_state_owed_and_removal(tmp_path):
    room = _room(tmp_path)
    row = {'thread': 'child', 'now': 'idle 7s', 'owed': 0,
           'opened-by': 'thread:ta', 'status': 'open'}

    def write(now, owed=0, opened=True):
        row.update(now=now, owed=owed)
        text = render_boundary(STRAND, 1, 'ta', [], set(), {}, opened=[row] if opened else [])
        (room / 'port/in/boundary.md').write_text(text)
        return text

    first = write('idle 7s')
    assert 'opened: child idle 0m · owed 0' in first
    assert execute('start', room, '{}') == first
    assert execute('post', room, '{}') == ''
    assert write('idle 59s') == first
    assert execute('post', room, '{}') == ''
    assert 'idle 1m' in write('idle 1m')
    assert execute('post', room, '{}') == ''
    holding = write('holding 84%')
    assert 'opened: child holding · owed 0' in holding
    assert execute('post', room, '{}')
    assert write('holding 83%') == holding
    assert execute('post', room, '{}') == ''
    write('holding 83%', owed=1)
    assert execute('post', room, '{}')
    write('holding 83%', opened=False)
    assert execute('post', room, '{}')
    assert execute('post', room, '{}') == ''
    assert list((room / 'port/out').glob('*.md')) == []


def test_stop_blocks_while_letters_are_owed(tmp_path):
    room = _room(tmp_path)
    text = _write(room, [_letter()])
    payload = json.loads(execute("stop", room, "{}"))
    assert payload["decision"] == "block"
    assert payload["reason"] == text
    state = json.loads((room / "port" / "jack-state.json").read_text())
    assert state["stop_blocks"] == 1
    assert list((room / "port" / "out").glob("*.md"))


def test_three_blocks_then_the_stop_is_allowed(tmp_path, monkeypatch):
    # The hand jack retains the old bounded behavior; hooks hold instead.
    monkeypatch.setenv("BRNRD_BODY_JACK", "hand")
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


def test_molted_body_can_stop_while_letters_are_owed(tmp_path):
    room = _room(tmp_path)
    _write(room, [_letter()])
    (room / "port" / "jack-state.json").write_text('{"molted": true}\n')
    assert execute("stop", room, "{}") == ""
    assert list((room / "port" / "out").glob("*.md")) == []


def test_corrupt_boundary_fails_open(tmp_path):
    room = _room(tmp_path)
    (room / "port" / "in" / "boundary.md").write_text("{{{\n")
    code, out = run("stop", room, "")
    assert (code, out) == (0, "")
    assert "Traceback" in (room / "port" / "jack-errors.log").read_text()


def test_claude_adapter_pins_the_three_hooks(tmp_path, monkeypatch):
    room = tmp_path / "rooms" / STRAND
    (room / "port").mkdir(parents=True)
    (room / "port" / "wake.md").write_text("wake\n")
    captured = []
    monkeypatch.setattr(sys, "argv", ["run", str(room), "haiku"])
    monkeypatch.setenv("BRNRD_JACK", "python -m brr.loom.runtime jack --room " + str(room))
    monkeypatch.setenv("BRNRD_WAIT", "10")
    monkeypatch.setenv("BRNRD_WAKE", str(room / "port/wake.md"))
    monkeypatch.setattr(os, "execvp", lambda _cmd, argv: captured.extend(argv))
    runpy.run_path(str(resolve(Home(tmp_path), "claude") / "run"))
    argv = captured
    assert argv[:4] == ["claude", "-p", "--model", "haiku"]
    assert "--dangerously-skip-permissions" in argv
    assert argv[-1] == "wake\n"
    settings = json.loads((room / ".claude" / "settings.json").read_text())
    hooks = settings["hooks"]
    assert hooks["PostToolUse"][0]["matcher"] == ".*"
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == 70
    # The same adapter caller also accepts the timeout exported by the loom.
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "21660")
    runpy.run_path(str(resolve(Home(tmp_path), "claude") / "run"))
    settings = json.loads((room / ".claude" / "settings.json").read_text())
    assert settings["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 21660
    for event in ("post", "stop", "start"):
        blob = json.dumps(hooks)
        assert f"--event {event}" in blob


def test_stop_shows_owed_letters_before_reading_wait(tmp_path):
    room = _room(tmp_path)
    text = _write(room, [_letter()])
    (room / "port" / "wait").write_text("broken wait")
    payload = json.loads(execute("stop", room, "{}"))
    assert payload == {"decision": "block", "reason": text}
    assert list((room / "port" / "out").glob("*.md"))


def test_a_dry_body_does_not_take_a_turn_for_an_owed_letter(tmp_path, monkeypatch):
    # A turn without fuel fails; the stop hook sleeps instead of blocking.
    from brr.loom.runtime import jack, usage
    room = _room(tmp_path)
    _write(room, [_letter()])
    monkeypatch.setenv("BRNRD_BODY_SHELL", "claude")
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "91.5")
    dry = iter([True] * 3 + [False] * 1000)
    monkeypatch.setattr(usage, "dry_until", lambda home, shell: 9e9 if next(dry) else None)
    monkeypatch.setattr(usage, "reading", lambda home, shell, after=0: {"windows": []})
    monkeypatch.setattr(jack.time, "sleep", lambda s: None)
    assert jack.execute("stop", room, "{}") == ""
    dry = iter([True] * 3 + [False] * 1000)
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", str(6 * 3600 + 60))
    monkeypatch.setattr(usage, "dry_until", lambda home, shell: usage.time.time() + 60 if next(dry) else None)
    assert json.loads(jack.execute("stop", room, "{}"))["decision"] == "block"


def test_post_says_the_context_weight_once_per_band(tmp_path):
    room = _room(tmp_path)
    _write(room, [])
    transcript = tmp_path / "t.jsonl"

    def call(tokens):
        with transcript.open("a") as out:
            out.write(json.dumps({"message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": tokens}}}) + "\n")
        return execute("post", room, json.dumps({"tool_name": "Bash", "transcript_path": str(transcript)}))

    assert call(40_000) == ""
    first = json.loads(call(120_000))["hookSpecificOutput"]["additionalContext"]
    assert first.startswith("context: 120k tokens") and "booted at 40k" in first
    assert call(130_000) == ""
    assert "context: 151k" in call(151_000)


@pytest.mark.parametrize('shell,cache,override,creation,expected', [
    ('claude', None, None, {'ephemeral_1h_input_tokens': 5000}, 3300),
    ('claude', None, None, {'ephemeral_5m_input_tokens': 5000}, 7110),
    ('claude', None, None, {}, 3300),
    ('codex', None, None, {}, 7110),
    ('custom', '1200', None, {}, 900),
    ('custom', 'garbage', None, {}, 7110),
    ('custom', 'Infinity', None, {}, 7110),
    ('custom', '300', None, {}, 7110),
    ('claude', None, '42', {}, 42),
    ('codex', None, '42', {}, 42),
])
def test_stop_holds_for_the_shells_cache_or_the_hook_ceiling(
        tmp_path, monkeypatch, shell, cache, override, creation, expected):
    from brr.loom.runtime import jack, usage
    room = _room(tmp_path)
    _write(room, [])
    if shell == 'custom':
        folder = tmp_path / 'loom/bodies/custom'
        folder.mkdir(parents=True)
        (folder / 'run').touch()
        (folder / 'cache').write_text(cache)
    if override is not None:
        (room / 'port/wait').write_text(override)
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({'message': {'usage': {'cache_creation': creation}}}) + '\n')
    monkeypatch.setenv('BRNRD_BODY_SHELL', shell)
    monkeypatch.setenv('BRNRD_BODY_JACK', 'hooks')
    monkeypatch.setenv('BRNRD_STOP_TIMEOUT', '7200')
    monkeypatch.delenv('BRNRD_THREAD', raising=False)
    monkeypatch.setattr(usage, 'dry_until', lambda *args: None)
    now = [0.]
    monkeypatch.setattr(jack.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(jack.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    result = json.loads(jack.execute('stop', room, json.dumps({'transcript_path': str(transcript)})))
    assert result['decision'] == 'block'
    assert result['reason'].startswith('nothing new; holding')
    assert now[0] == pytest.approx(expected)
