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


def test_a_delegates_post_leaves_the_bodys_letters_alone(tmp_path):
    # A delegate's tool call runs the same PostToolUse hook; its payload
    # carries agent_id. The owed letter stays unshown for the body, the held
    # stop stays held, and the delegate's web fetch still taints the strand.
    room = _room(tmp_path)
    _write(room, [_letter()])
    assert json.loads(execute("stop", room, "{}"))["decision"] == "block"
    _write(room, [_letter()])
    delegate = {"agent_id": "a92830476e38202a7", "agent_type": "general-purpose", "tool_name": "WebFetch"}
    assert execute("post", room, json.dumps(delegate)) == ""
    state = json.loads((room / "port" / "jack-state.json").read_text())
    assert state["stop_blocks"] == 1
    out = sorted(p.name for p in (room / "port" / "out").iterdir())
    assert len(out) == 2 and any(name.startswith("taint-") for name in out)
    posted = json.loads(execute("post", room, json.dumps({"tool_name": "Bash"})))
    assert "p-test/abcde" in posted["hookSpecificOutput"]["additionalContext"]


def test_opened_delivery_ignores_age_and_fuel_but_tracks_state_owed_and_removal(tmp_path):
    room = _room(tmp_path)
    row = {'thread': 'child', 'now': 'idle 7s', 'owed': 0,
           'opened-by': 'thread:ta', 'status': 'open'}

    def write(now, owed=0, opened=True):
        row.update(now=now, owed=owed)
        text = render_boundary(STRAND, 1, 'ta', [], set(), {}, opened=[row] if opened else [])
        (room / 'port/in/boundary.md').write_text(text)
        return text

    # An idle child owing nothing is folded into a count (wake-vision).
    assert 'opened: child' not in write('idle 7s')
    assert 'idle: 1 more opened here' in write('idle 7s')
    first = write('idle 7s', owed=1)
    assert 'opened: child idle 0m · owed 1' in first
    assert execute('start', room, '{}') == first
    assert execute('post', room, '{}') == ''
    assert write('idle 59s', owed=1) == first
    assert execute('post', room, '{}') == ''
    assert 'idle 1m' in write('idle 1m', owed=1)
    assert execute('post', room, '{}') == ''
    # A PR's push age ticking is not news; its CI turning is, said once and not as gone.
    row['prs'] = ['#7 draft pending 2h']
    assert 'owed 1 · #7 draft pending 2h' in write('idle 1m', owed=1)
    assert execute('post', room, '{}')
    row['prs'] = ['#7 draft pending 3h']
    write('idle 1m', owed=1)
    assert execute('post', room, '{}') == ''
    row['prs'] = ['#7 draft green 3h']
    write('idle 1m', owed=1)
    said = json.loads(execute('post', room, '{}'))['hookSpecificOutput']['additionalContext']
    assert '#7 draft green 3h' in said and 'gone:' not in said
    row['prs'] = []
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
    assert payload["reason"].endswith(text)
    assert '- p-test/abcde: brnrd runtime send --re p-test/abcde --to thread:tb "<answer>"' in payload["reason"]
    state = json.loads((room / "port" / "jack-state.json").read_text())
    assert state["stop_blocks"] == 1
    assert list((room / "port" / "out").glob("*.md"))


def test_a_stop_repeats_only_what_was_not_said(tmp_path):
    # A blocked stop used to re-send the whole boundary the body had just read.
    room = _room(tmp_path)
    _write(room, [_letter()])
    assert "ping-1" in execute("start", room, "{}")
    reason = json.loads(execute("stop", room, "{}"))["reason"]
    assert "ping-1" not in reason
    assert "owed: p-test/abcde (shown before)" in reason
    newer = Fact(kind="letter", by="strand:s-ab12-bbbbbb", id="p-test/fghij",
                 data={"id": "p-test/fghij", "to": "thread:ta", "body": "pong-2", "from": "s-ab12-bbbbbb"})
    _write(room, [_letter(), newer])
    execute("post", room, "{}")
    _write(room, [_letter(), newer])
    reason = json.loads(execute("stop", room, "{}"))["reason"]
    assert "pong-2" not in reason and "p-test/fghij" in reason


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


def test_claude_adapter_pins_its_hooks(tmp_path, monkeypatch):
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
    assert hooks["PreToolUse"][0]["matcher"] == ".*"
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == 70
    # The same adapter caller also accepts the timeout exported by the loom.
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "21660")
    runpy.run_path(str(resolve(Home(tmp_path), "claude") / "run"))
    settings = json.loads((room / ".claude" / "settings.json").read_text())
    assert settings["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 21660
    for event in ("pre", "post", "stop", "start", "subagent-stop"):
        blob = json.dumps(hooks)
        assert f"--event {event}" in blob


def test_stop_shows_owed_letters_before_reading_wait(tmp_path):
    room = _room(tmp_path)
    text = _write(room, [_letter()])
    (room / "port" / "wait").write_text("broken wait")
    payload = json.loads(execute("stop", room, "{}"))
    assert payload["decision"] == "block" and payload["reason"].endswith(text)
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
    assert result['reason'].startswith('cache keepalive, nothing new arrived')
    assert now[0] == pytest.approx(expected)


def test_post_traces_the_call_with_its_command_masked(tmp_path):
    from brr.loom.runtime import tooltrace
    room = _room(tmp_path)
    secret = "curl -H 'Authorization: Bearer sk-live-SECRET' https://u:SECRET@x.test/a?token=SECRET"
    calls = [
        {"tool_name": "Bash", "tool_input": {"command": secret, "description": "Fetch the thing"}},
        {"tool_name": "Bash", "tool_input": {"command": "cat > f <<'EOF'\nSECRET body\nEOF"}},
        {"tool_name": "Bash", "tool_input": {"command": "echo ghx_0123456789abcdefghij0123456789abcdef"}},
        {"tool_name": "Read", "tool_input": {"file_path": "/Users/x/private/SECRET-dir/notes.md"}},
        {"tool_name": "Grep", "tool_input": {"pattern": "def execute", "path": "/a/SECRET"}},
        {"tool_name": "WebFetch", "tool_input": {"url": "https://x.test/a?token=SECRET", "prompt": "p"}},
        {"tool_name": "Bash", "tool_input": {"command": "mysql -pSECRETpw db"}},
        {"tool_name": "Bash", "tool_input": {"command": "sshpass -p SECRET2 ssh h"}},
        {"tool_name": "Bash", "tool_input": {"command": "curl -u bob:SECRET https://x"}},
        {"tool_name": "Bash", "tool_input": {"command": "sshpass -p 'SECRET 2' ssh h"}},
        {"tool_name": "Bash", "tool_input": {"command": "mysql -p'SECRET' db"}},
        {"tool_name": "Bash", "tool_input": {"command": "curl -u 'bob:SECRET x' https://x"}},
        {"tool_name": "Bash", "tool_input": {"command": "ssh -p 22 h && mkdir -p a/b && pytest -p no:randomly"}},
    ]
    for n, call in enumerate(calls):
        execute("pre", room, json.dumps(dict(call, tool_use_id=f"t{n}")))
        execute("post", room, json.dumps(dict(call, tool_use_id=f"t{n}", duration_ms=5)))
    text = (room / "port" / "tools.jsonl").read_text()
    assert "SECRET" not in text and "0123456789abcdef" not in text
    rows = tooltrace.read(room)
    assert [(r["tool"], r["arg"]) for r in rows] == [
        ("Bash", "Fetch the thing"), ("Bash", ""), ("Bash", ""), ("Read", "notes.md"),
        ("Grep", "def execute"), ("WebFetch", "x.test"),
        *[("Bash", "")] * 7,
    ]
    assert rows[0]["cmd"].startswith("curl -H 'Authorization: <redacted>")
    assert "://<redacted>@x.test" in rows[0]["cmd"]
    assert rows[1]["cmd"] == "cat > f <<'EOF' …"
    assert rows[2]["cmd"] == "echo <redacted>"
    assert [r["cmd"] for r in rows[6:]] == [
        "mysql -p<redacted> db", "sshpass -p <redacted> ssh h", "curl -u bob:<redacted> https://x",
        "sshpass -p '<redacted>' ssh h", "mysql -p'<redacted>' db", "curl -u 'bob:<redacted>' https://x",
        "ssh -p 22 h && mkdir -p a/b && pytest -p no:randomly",
    ]
    assert all(r["end"] is not None and r["ms"] == 5 for r in rows)


def test_an_open_call_is_live_with_its_line_until_its_post(tmp_path, monkeypatch):
    from brr.loom.runtime import tooltrace
    room = _room(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Old line."}]}},
        {"type": "user", "message": {"content": "x"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Running the suite now. It takes a while."}, {"type": "tool_use"}]}},
    ]))
    sleep = {"tool_name": "Bash", "tool_use_id": "s1", "transcript_path": str(transcript),
             "tool_input": {"command": "sleep 90", "description": "Wait"}}
    execute("pre", room, json.dumps(sleep))
    execute("pre", room, json.dumps(dict(sleep, tool_use_id="s2", tool_input={"command": "ls"})))
    execute("post", room, json.dumps(dict(sleep, tool_use_id="s2", tool_input={"command": "ls"})))
    now = tooltrace.live(room)
    assert [(c["cmd"], c["end"]) for c in now["open"]] == [("sleep 90", None)]
    assert now["say"] == "Running the suite now." and now["last_end"] and now["paused"] is None
    lines = (room / "port" / "tools.jsonl").read_text().splitlines()
    assert sum('"say"' in line for line in lines) == 1  # written when it changes, not per call
    execute("post", room, json.dumps(dict(sleep, hook_event_name="PostToolUseFailure")))
    assert tooltrace.live(room)["open"] == []
    assert tooltrace.read(room)[0]["failed"] is True


def test_a_refused_call_is_written_ended(tmp_path, monkeypatch):
    from brr.loom.runtime import tooltrace
    room = _room(tmp_path)
    from brr.loom.runtime import jack
    monkeypatch.setattr(jack, "_guard_warnings", lambda *a: ("guard x", True))
    out = json.loads(execute("pre", room, json.dumps(
        {"tool_name": "Bash", "tool_use_id": "r1", "tool_input": {"command": "rm -rf x"}})))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    [row] = tooltrace.read(room)
    assert row["cmd"] == "rm -rf x" and row["end"] is not None and row["failed"] is True
    assert tooltrace.live(room)["open"] == []


def test_lines_without_ids_or_starts_still_read_as_calls(tmp_path):
    from brr.loom.runtime import tooltrace
    room = _room(tmp_path)
    (room / "port" / "tools.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"at": 10.0, "tool": "Read", "arg": "old.md"},  # before starts were written
        {"at": 11.0, "end": 0, "tool": "Bash", "arg": "a"},  # grok/vibe: no id
        {"at": 12.0, "end": 0, "tool": "Bash", "arg": "b"},
        {"at": 13.0, "end": 1, "tool": "Bash", "arg": "a"},
        {"at": 20.0, "end": 1, "id": "lost", "tool": "Grep", "arg": "p", "ms": 4000},
    ]))
    rows = tooltrace.read(room)
    assert [(r["arg"], r["at"], r["end"]) for r in rows] == [
        ("old.md", 10.0, 10.0), ("a", 11.0, 13.0), ("b", 12.0, None), ("p", 16.0, 20.0)]
    assert [c["arg"] for c in tooltrace.live(room)["open"]] == ["b"]


def test_a_codex_room_reads_its_rollout(tmp_path, monkeypatch):
    from brr.loom.runtime import tooltrace
    room = _room(tmp_path)
    thread = "01a1-codex-thread"
    (room / "port" / "body.log").write_text(
        "noise\n" + json.dumps({"type": "thread.started", "thread_id": thread}) + "\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    rollout = tmp_path / "codex/sessions/2026/10/23" / f"rollout-2026-10-23T00-00-00-{thread}.jsonl"
    rollout.parent.mkdir(parents=True)

    def item(ts, payload):
        return json.dumps({"timestamp": f"2026-10-23T00:00:{ts:02d}.000Z", "type": "response_item",
                           "payload": payload}) + "\n"
    exec_input = "text(await tools.exec_command({cmd:'pytest -q tests/x --token=SECRET',max_output_tokens:9}));"
    rollout.write_text(
        item(0, {"type": "message", "role": "assistant", "phase": "commentary",
                 "content": [{"type": "output_text", "text": "Running the tests. Then publish."}]})
        + item(1, {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": exec_input})
        + item(31, {"type": "custom_tool_call_output", "call_id": "c1",
                    "output": [{"type": "input_text", "text": "Script running with cell ID 7\nWall time 30"}]})
        + item(33, {"type": "function_call", "call_id": "c2", "name": "wait", "arguments": '{"cell_id":"7"}'}))
    now = tooltrace.live(room)
    [running] = now["open"]
    assert running["cmd"] == "pytest -q tests/x --token=<redacted>" and now["say"] == "Running the tests."
    assert running["at"] == tooltrace._stamp("2026-10-23T00:00:01.000Z")
    with rollout.open("a") as handle:  # read on from where the last read stopped
        handle.write(item(50, {"type": "function_call_output", "call_id": "c2", "output": "Script completed"}))
    assert tooltrace.live(room)["open"] == []
    [done] = tooltrace.read(room)
    assert done["end"] - done["at"] == 49


def test_tool_trace_never_fails_the_hook_and_rotates(tmp_path, monkeypatch):
    from brr.loom.runtime import tooltrace
    room = _room(tmp_path)
    (room / "port" / "tools.jsonl").mkdir()  # an append that cannot happen
    assert execute("post", room, json.dumps({"tool_name": "Read", "tool_input": {"file_path": "a"}})) == ""
    (room / "port" / "tools.jsonl").rmdir()
    monkeypatch.setattr(tooltrace, "CAP", 200)
    for n in range(20):
        tooltrace.note(room, json.dumps({"tool_name": "Bash", "tool_input": {"description": f"step {n}"}}))
    assert (room / "port" / "tools.jsonl").stat().st_size <= 200 + 100
    assert (room / "port" / "tools.jsonl.1").is_file()
    args = [r["arg"] for r in tooltrace.read(room)]
    assert args[-1] == "step 19" and args == sorted(args, key=lambda a: int(a.split()[1]))
    assert len(tooltrace.read(room, limit=3)) == 3


def test_a_post_says_only_what_changed_since_the_last_boundary(tmp_path):
    """wake-vision: a live post re-sent ~7 KB (every row, every owed letter) on every call."""
    room = _room(tmp_path)
    first = _letter("ping-1\nmore")
    _write(room, [first])
    assert "ping-1\n  more" in execute("start", room, "{}")
    _write(room, [first], shown={"p-test/abcde"})
    # Owed, shown at the wake, nothing new: the post is silent, the stop still holds on it.
    assert execute("post", room, json.dumps({"tool_name": "Bash"})) == ""
    second = Fact(kind="letter", by="strand:s-ab12-bbbbbb", id="p-test/fghij",
                  data={"id": "p-test/fghij", "to": "thread:ta", "body": "pong-2\nwhole", "from": "s-ab12-bbbbbb"})
    _write(room, [first, second], shown={"p-test/abcde"})
    said = json.loads(execute("post", room, json.dumps({"tool_name": "Bash"})))["hookSpecificOutput"]["additionalContext"]
    assert "pong-2\n  whole" in said
    assert "ping-1" not in said and "status:" not in said
    assert "owed: p-test/abcde (shown before)" in said
    assert [letter.id for letter in parse_boundary(said).letters] == ["p-test/fghij"]
    _write(room, [first, second], shown={"p-test/abcde", "p-test/fghij"})
    assert execute("post", room, json.dumps({"tool_name": "Bash"})) == ""


def _reply(room: Path, re: str) -> None:
    (room / "port" / "out" / "zzzzz.md").write_text(
        f"---\nkind: letter\nid: {STRAND}/zzzzz\nto: thread:tb\nfrom: {STRAND}\nre: {re}\n---\nanswered\n")


def test_a_reply_waiting_in_port_out_counts_as_answered_at_the_stop(tmp_path, monkeypatch):
    # The loom drains port/out on its tick; a slow loom kept re-showing answered letters.
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "91")  # ceiling 1s: the hold returns at once
    room = _room(tmp_path)
    _write(room, [_letter()])
    _reply(room, "p-test/abcde")
    held = json.loads(execute("stop", room, "{}"))
    assert held["reason"].startswith("cache keepalive, nothing new arrived")
    assert "send --re p-test/abcde" not in held["reason"]


@pytest.mark.parametrize("kind", ["letter", "note"])
def test_a_reply_the_loom_drained_still_counts_while_the_boundary_lags(tmp_path, monkeypatch, kind):
    # stop-answered: once the loom drained a reply from port/out it left
    # 'answered', and the boundary listed the letter until the loom rendered again.
    from brr.loom.runtime.ledger import append
    from brr.loom.runtime.port import fact_from_port, parse_frontmatter
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "91")
    room = _room(tmp_path)
    _write(room, [_letter()])
    _reply(room, "p-test/abcde")
    if kind == "note":
        (room / "port" / "out" / "zzzzz.md").write_text(
            f"---\nkind: note\nid: {STRAND}/zzzzz\nre: p-test/abcde\nnote: an ack, nothing asked\n---\n")
    for path in (room / "port" / "out").glob("*.md"):
        append(Home(tmp_path), fact_from_port(parse_frontmatter(path.read_text()), STRAND, 1, room=room))
        path.unlink()
    held = json.loads(execute("stop", room, "{}"))
    assert held["reason"].startswith("cache keepalive, nothing new arrived")
    assert "send --re p-test/abcde" not in held["reason"]


def test_the_stop_repeats_its_whole_list_after_work_and_lets_an_idle_repeat_through(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "91")
    room = _room(tmp_path)
    _write(room, [_letter()])
    first = json.loads(execute("stop", room, "{}"))["reason"]
    assert first.startswith("the stop holds on 1 thing")
    execute("post", room, "{}")
    again = json.loads(execute("stop", room, "{}"))["reason"]
    assert "send --re p-test/abcde --to thread:tb" in again
    # Nothing done since the same list: the body read it, and is not trapped.
    assert "the stop holds" not in execute("stop", room, "{}")
