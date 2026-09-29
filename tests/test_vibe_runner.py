"""Vibe's real public-history schema and subprocess boundary."""
import json
import os
import sys

import pytest

from brr import runner, runner_select, vibe_runner


def message(text, role="assistant", status="completed"):
    return {"type": "message", "role": role, "generationStatus": status,
            "sessionId": "session-1", "content": [{"type": "text", "text": text}]}


@pytest.mark.parametrize("history", [[], {}, [message("partial", status="failed")]])
def test_no_completed_reply_is_failure(history):
    reply, error = vibe_runner.final_reply(json.dumps(history))
    assert reply == ""
    assert error


def test_reply_excludes_user_and_tool_history():
    history = [message("private prompt", "user"), message("working"),
               {"type": "effect", "result": "private tool result"}, message("done")]
    assert vibe_runner.final_reply(json.dumps(history)) == ("done\n", None)
    assert vibe_runner.final_reply("not JSON")[1]


@pytest.mark.parametrize("failure", [False, True])
def test_profile_invocation_delivers_stdin_system_prompt_and_exit(tmp_path, monkeypatch, failure):
    binary = tmp_path / "vibe"
    binary.write_text("#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
assert sys.argv[1:] == ['-p', '--auto-approve', '--trust', '--output', 'json']
assert sys.stdin.read() == 'the wake'
home = Path(os.environ['VIBE_HOME'])
path = home / 'prompts' / (os.environ['VIBE_SYSTEM_PROMPT_ID'] + '.md')
assert path.read_text().strip()
if os.environ.get('VIBE_TEST_FAILURE'):
    print('authentication failed', file=sys.stderr)
    sys.exit(7)
print(json.dumps([{'type':'message', 'role':'assistant',
    'generationStatus':'completed', 'sessionId':'session-1',
    'content':[{'type':'text','text':'done'}]}]))
''')
    binary.chmod(0o755)
    launcher = tmp_path / "brnrd"
    source_root = str(__import__("pathlib").Path(runner.__file__).resolve().parent.parent)
    launcher.write_text(
        "#!" + sys.executable + "\nimport sys\n"
        + f"sys.path.insert(0, {source_root!r})\n"
        + "from brr.cli import main\nmain()\n"
    )
    launcher.chmod(0o755)
    home = tmp_path / "vibe-home"
    home.mkdir()
    sentinel = home / "config.toml"
    sentinel.write_text("# operator config\n")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("VIBE_TEST_FAILURE", raising=False)
    monkeypatch.setenv("BRR_VIBE_HOOKS", "0")  # this suite may itself run inside a daemon run
    profile = runner_select.runner_from_profile("vibe", {
        "cmd": "brnrd runners _vibe", "binary": "vibe", "provider": "mistral",
        "class": "economy", "cost_rank": 18,
    })
    env = {"VIBE_HOME": str(home)}
    if failure:
        env["VIBE_TEST_FAILURE"] = "1"
    response = tmp_path / "response.md"
    result = runner.invoke_runner(profile, runner.RunnerInvocation(
        kind="test", label="vibe", prompt="the wake", cwd=tmp_path,
        repo_root=tmp_path, env=env, response_path=str(response), timeout_seconds=15,
    ))
    assert 'the wake' not in result.command
    assert result.returncode == (7 if failure else 0), result.stderr
    assert response.exists() is not failure
    if not failure:
        assert response.read_text() == "done\n"
    else:
        assert "authentication failed" in result.stderr
    assert list((home / "prompts").glob("brnrd-*.md")) == []
    assert sentinel.read_text() == "# operator config\n"


# ── Tier 2: native hooks ────────────────────────────────────────────────
from brr import hooks as _hooks
from brr import vibe_runner as _vr


def test_vibe_hooks_toml_parses_and_routes_each_native_type_to_brnrd():
    tomllib = pytest.importorskip("tomllib")  # stdlib from 3.11; brnrd supports 3.10
    doc = tomllib.loads(_hooks.vibe_hooks_toml("brnrd"))
    got = {h["type"]: h["command"] for h in doc["hooks"]}
    assert got == {
        "pre_tool": "brnrd hook pre-tool",
        "post_tool": "brnrd hook post-tool",
        "post_agent": "brnrd hook stop",
    }
    assert all("match" not in h and h["timeout"] > 0 for h in doc["hooks"])


def test_vibe_post_tool_inject_is_additional_context_never_a_deny():
    out, rc = _hooks.render_native(
        "vibe", _hooks.PHASE_POST_TOOL,
        {"inject": "steer: stop", "block": True, "block_reason": "owed a card"},
    )
    assert rc == 0 and "decision" not in out
    assert out["hook_specific_output"]["additional_context"] == "owed a card\n\nsteer: stop"
    assert _hooks.render_native("vibe", _hooks.PHASE_POST_TOOL, {"inject": None})[0] == {}


def test_vibe_pre_tool_block_denies_and_stop_block_retries():
    neutral = {"inject": None, "block": True, "block_reason": "rooted write"}
    assert _hooks.render_native("vibe", _hooks.PHASE_PRE_TOOL, neutral)[0] == {
        "decision": "deny", "reason": "rooted write"}
    assert _hooks.render_native("vibe", _hooks.PHASE_STOP, neutral)[0]["decision"] == "deny"
    assert _hooks.render_native("vibe", _hooks.PHASE_SESSION_START, neutral)[0] == {}


def test_vibe_hook_dir_armed_only_in_a_daemon_run(monkeypatch):
    monkeypatch.setattr(_hooks, "vibe_hook_capability", lambda **_: True)
    assert _vr.hook_dir({}) is None
    assert _vr.hook_dir({"BRR_RUN_ID": "r", "BRR_VIBE_HOOKS": "0"}) is None
    env = {"BRR_RUN_ID": "r"}
    root = _vr.hook_dir(env)
    try:
        assert env["BRR_RUNNER"] == "vibe"
        assert (root / ".vibe" / "hooks.toml").read_text() == _hooks.vibe_hooks_toml()
        assert _vr.command(root)[-2:] == ["--add-dir", str(root)]
        assert "--add-dir" not in _vr.command(None)
    finally:
        import shutil
        shutil.rmtree(root)


def _vibe_pre_tool(tool_name, file_path, cwd):
    return json.dumps({
        "hook_event_name": "pre_tool", "session_id": "s", "transcript_path": "",
        "cwd": str(cwd), "tool_name": tool_name, "tool_call_id": "c",
        "tool_input": {"file_path": str(file_path), "content": "x"},
    })


def test_vibe_write_into_host_checkout_is_denied_through_run_hook(tmp_path):
    host = tmp_path / "host"
    wt = host / ".brr" / "worktrees" / "run-x"
    wt.mkdir(parents=True)
    env = {"BRR_RUNNER": "vibe", "BRR_HOST_ROOT": str(host), "BRR_WORK_TREE": str(wt)}
    for tool in ("write_file", "edit"):
        out, rc = _hooks.run_hook("pre-tool", _vibe_pre_tool(tool, host / "stray.txt", wt), env)
        assert rc == 0 and out["decision"] == "deny" and out["reason"], (tool, out)
    ok, rc = _hooks.run_hook("pre-tool", _vibe_pre_tool("write_file", wt / "ok.txt", wt), env)
    assert (ok, rc) == ({}, 0)
    other, _ = _hooks.run_hook("pre-tool", _vibe_pre_tool("read_file", host / "stray.txt", wt), env)
    assert other == {}
