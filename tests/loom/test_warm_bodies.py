"""The four harnesses hold one session between letters (letter 11739).

A finished turn waits inside Stop. A letter during that wait, and one that
is already there when the turn ends, reaches that same session once. A quiet
deadline on a harness Stop does not start a model turn.
"""

from __future__ import annotations

import json
import os
import runpy
import shlex
import sys
import tomllib
from pathlib import Path

import pytest

from brr.loom.runtime.adapters import resolve
from brr.loom.runtime.home import Home
from test_jack import _letter, _reply, _room, _write

BODIES = ("claude", "codex", "grok", "vibe")
BODY_ROOT = Path(__file__).resolve().parents[2] / "src/brr/loom/runtime/bodies"


def test_the_four_bundled_jacks_are_hooks():
    for name in BODIES:
        assert (BODY_ROOT / name / "jack").read_text().strip() == "hooks"


def _wake(tmp_path: Path, monkeypatch, jack: Path) -> None:
    wake = tmp_path / "wake.md"
    wake.write_text("the occasion\n")
    monkeypatch.setenv("BRNRD_JACK", str(jack))
    monkeypatch.setenv("BRNRD_WAIT", "1")
    monkeypatch.setenv("BRNRD_WAKE", str(wake))
    monkeypatch.setenv("BRNRD_CORE", "")
    monkeypatch.delenv("BRNRD_OCCASION", raising=False)
    monkeypatch.delenv("BRNRD_STOP_TIMEOUT", raising=False)


def _run(tmp_path: Path, monkeypatch, shell: str, jack: Path) -> None:
    room = tmp_path / "room"
    (room / "self/.git/info").mkdir(parents=True)
    (room / "self/.git/info/exclude").write_text("")
    (room / "self/core").mkdir(parents=True)
    (room / "self/core/identity.md").write_text("identity\n")
    _wake(tmp_path, monkeypatch, jack)
    monkeypatch.setenv("VIBE_HOME", str(tmp_path / "vibe-home"))
    captured: list[list[str]] = []
    monkeypatch.setattr(os, "execvp", lambda cmd, argv: captured.append(argv))
    if shell == "vibe":
        stub = tmp_path / "bin" / "vibe"
        stub.parent.mkdir()
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{stub.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(sys, "argv", ["run", str(room), "m"])
    try:
        runpy.run_path(str(resolve(Home(tmp_path / "home"), shell) / "run"))
    except SystemExit as exc:
        assert exc.code in (0, None)
    if shell == "codex":
        argv = captured[0]
        # The core anchor reads the second -c. Hooks stay after it.
        assert argv[argv.index("-c") + 1] == 'shell_environment_policy.inherit="all"'
        hooked = {}
        for flag, value in zip(argv, argv[1:]):
            if flag == "-c" and value.startswith("hooks."):
                name, raw = value.split("=", 1)
                hooked[name.removeprefix("hooks.")] = tomllib.loads("x=" + raw)["x"]
        assert set(hooked) == {"SessionStart", "PreToolUse", "PostToolUse", "Stop"}
        for event, rows in hooked.items():
            command = rows[0]["hooks"][0]["command"]
            assert shlex.split(command) == [str(jack), "--event",
                                             {"SessionStart": "start", "PreToolUse": "pre",
                                              "PostToolUse": "post", "Stop": "stop"}[event]]
        return
    if shell == "claude":
        hooks = json.loads((room / ".claude/settings.json").read_text())["hooks"]
        wanted = {"SessionStart": "start", "PreToolUse": "pre", "PostToolUse": "post", "Stop": "stop"}
    elif shell == "grok":
        hooks = json.loads((room / "self/.claude/settings.local.json").read_text())["hooks"]
        wanted = {"SessionStart": "start", "PreToolUse": "pre", "PostToolUse": "post", "Stop": "stop"}
    else:
        text = (room / ".vibe/hooks.toml").read_text()
        assert "hook pre-tool" in text and "hook post-tool" in text and "hook stop" in text
        assert str(resolve(Home(tmp_path / "home"), "vibe") / "run") in text
        return
    for name, event in wanted.items():
        blob = json.dumps(hooks[name])
        assert shlex.quote(str(jack)) + " --event " + event in blob


def test_vibe_stop_tells_the_jack_this_is_a_harness_stop(tmp_path):
    """Vibe's post_agent payload has no stop_hook_active. The run adds it."""
    import subprocess
    jack = tmp_path / "jack"
    seen = tmp_path / "seen"
    jack.write_text(
        "#!/bin/sh\ncat > " + shlex.quote(str(seen)) + "\nprintf '%s\\n' '{\"decision\":\"block\",\"reason\":\"x\"}'\n")
    jack.chmod(0o755)
    run = BODY_ROOT / "vibe" / "run"
    out = subprocess.run(
        [sys.executable, str(run), "hook", "stop"],
        input="{}", text=True, capture_output=True,
        env={**os.environ, "BRNRD_JACK": str(jack)}, check=True)
    assert json.loads(seen.read_text())["stop_hook_active"] is False
    assert json.loads(out.stdout)["decision"] == "deny"


@pytest.mark.parametrize("shell", BODIES)
def test_each_run_wires_its_hooks_to_the_same_jack(tmp_path, monkeypatch, shell):
    jack = tmp_path / "jack bin" / "brnrd-jack"
    jack.parent.mkdir()
    jack.write_text("#!/bin/sh\n")
    _run(tmp_path, monkeypatch, shell, jack)


def _clock(monkeypatch):
    from brr.loom.runtime import jack as jack_mod
    from brr.loom.runtime import usage
    now = [0.0]
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    monkeypatch.setattr(jack_mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jack_mod.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def _stop(room, monkeypatch, shell: str, stdin: dict, *, wait: str, timeout: str):
    from brr.loom.runtime import jack as jack_mod
    monkeypatch.setenv("BRNRD_BODY_SHELL", shell)
    monkeypatch.setenv("BRNRD_BODY_JACK", "hooks")
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", timeout)
    monkeypatch.delenv("BRNRD_THREAD", raising=False)
    (room / "port/wait").write_text(wait)
    return jack_mod.execute("stop", room, json.dumps(stdin))


@pytest.mark.parametrize("shell", BODIES)
@pytest.mark.parametrize("continued", [False, True])
def test_a_finished_turn_waits_and_a_letter_reaches_that_session_once(
        tmp_path, monkeypatch, shell, continued):
    """One execute() is one session. The letter body is in that result once."""
    from brr.loom.runtime import jack as jack_mod
    room = _room(tmp_path)
    session = f"sess-{shell}-{int(continued)}"
    body = f"quiet letter for {shell}"
    stdin = {"session_id": session, "stop_hook_active": continued}
    now = _clock(monkeypatch)
    arrived = []

    def sleep(seconds):
        if not arrived:
            _write(room, [_letter(body)])
            arrived.append(session)
        now[0] += min(seconds, 0.2)

    monkeypatch.setattr(jack_mod.time, "sleep", sleep)
    monkeypatch.setenv("BRNRD_BODY_SHELL", shell)
    monkeypatch.setenv("BRNRD_BODY_JACK", "hooks")
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "21660")
    (room / "port/wait").write_text("30\n")
    _write(room, [])
    raw = jack_mod.execute("stop", room, json.dumps(stdin))
    payload = json.loads(raw)
    assert payload["decision"] == "block"
    assert payload["reason"].count(body) == 1
    assert arrived == [session]
    # Answered, the same session's next stop does not say the letter again.
    _reply(room, "p-test/abcde")
    again = _stop(room, monkeypatch, shell, stdin, wait="0.2\n", timeout="91")
    assert again == ""
    assert body not in again


@pytest.mark.parametrize("shell", BODIES)
def test_a_letter_racing_the_turns_end_is_delivered_once(tmp_path, monkeypatch, shell):
    room = _room(tmp_path)
    body = f"raced {shell}"
    _write(room, [_letter(body)])
    _clock(monkeypatch)
    session = {"session_id": f"sess-race-{shell}", "stop_hook_active": True}
    raw = _stop(room, monkeypatch, shell, session, wait="30\n", timeout="21660")
    payload = json.loads(raw)
    assert payload["decision"] == "block"
    assert payload["reason"].count(body) == 1
    _reply(room, "p-test/abcde")
    assert _stop(room, monkeypatch, shell, session, wait="0.2\n", timeout="91") == ""


@pytest.mark.parametrize("shell", BODIES)
@pytest.mark.parametrize("continued", [False, True])
def test_a_quiet_deadline_does_not_start_a_turn(tmp_path, monkeypatch, shell, continued):
    room = _room(tmp_path)
    _write(room, [])
    now = _clock(monkeypatch)
    raw = _stop(
        room, monkeypatch, shell,
        {"session_id": f"sess-quiet-{shell}", "stop_hook_active": continued},
        wait="0.2\n", timeout="91",
    )
    assert raw == ""
    assert "decision" not in raw
    # Slept the hook out to its ceiling (timeout 91, margin 90), no block.
    assert now[0] == pytest.approx(1.0)
