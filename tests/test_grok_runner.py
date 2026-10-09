"""Grok Build's JSON reply, prompt file, and Claude-compatible hook seam."""
import json
import os
import sys

import pytest

from brr import hooks, runner, runner_cores, runner_select, grok_runner, grok_status

_SESSION = "018f6b3c-7c3a-7d2e-8f01-aabbccddeeff"


def test_reply_is_the_text_field_only():
    payload = {"text": "done", "sessionId": "s", "usage": {"output_tokens": 1}}
    assert grok_runner.final_reply(json.dumps(payload)) == ("done\n", None)
    assert grok_runner.final_reply('{"text":"done\\n"}') == ("done\n", None)


@pytest.mark.parametrize("stdout", ["", "not json", "[]", '{"text":"  "}',
                                    '{"type":"error","message":"no session"}'])
def test_no_reply_is_failure(stdout):
    reply, error = grok_runner.final_reply(stdout)
    assert reply == "" and error


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("model", [None, "grok-4.7"])
def test_profile_invocation_uses_a_prompt_file(tmp_path, monkeypatch, failure, model):
    binary = tmp_path / "grok"
    binary.write_text("#!" + sys.executable + "\n" + r'''
import os, sys
from pathlib import Path
args = sys.argv[1:]
model = os.environ.get("EXPECTED_GROK_MODEL") or ""
if model:
    assert args[:2] == ["-m", model]
    args = args[2:]
else:
    assert args[:2] != ["-m", model]
assert args[0] == "--prompt-file"
prompt = Path(args[1]).read_text()
assert prompt == "the wake"
assert "the wake" not in args
assert "--output-format" in args and "json" in args
assert "--permission-mode" in args and "bypassPermissions" in args
assert "--trust" in args and "--no-auto-update" in args
rules = args[args.index("--rules") + 1]
assert "resident" in rules
if os.environ.get("GROK_TEST_FAILURE"):
    print("authentication failed", file=sys.stderr)
    sys.exit(7)
print('{"text":"done","sessionId":"s"}')
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
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("GROK_TEST_FAILURE", raising=False)
    monkeypatch.delenv("GROK_ACTIVE_MODEL", raising=False)
    monkeypatch.delenv("EXPECTED_GROK_MODEL", raising=False)
    cmd = "brnrd runners _grok"
    if model:
        cmd = runner_cores._cmd_with_model("grok", cmd, model)
    profile = runner_select.runner_from_profile("grok", {
        "cmd": cmd, "binary": "grok", "provider": "xai",
        "class": "balanced", "cost_rank": 32, "hooks": "grok",
    })
    env = {}
    if model:
        env["EXPECTED_GROK_MODEL"] = model
    if failure:
        env["GROK_TEST_FAILURE"] = "1"
    response = tmp_path / "response.md"
    result = runner.invoke_runner(profile, runner.RunnerInvocation(
        kind="test", label="grok", prompt="the wake", cwd=tmp_path,
        repo_root=tmp_path, env=env, response_path=str(response), timeout_seconds=15,
    ))
    assert "the wake" not in result.command
    assert result.returncode == (7 if failure else 0), result.stderr
    assert response.exists() is not failure
    if not failure:
        assert response.read_text() == "done\n"
    else:
        assert "authentication failed" in result.stderr


def _envelope(**extra):
    payload = {
        "text": "done",
        "sessionId": _SESSION,
        "usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 4,
            "cache_creation_input_tokens": 0,
            "output_tokens": 3,
        },
        "modelUsage": {"grok-4.7": {"inputTokens": 10, "outputTokens": 3}},
        "total_cost_usd": 0.012689,
        "total_cost_usd_ticks": 126890500,
    }
    payload.update(extra)
    return json.dumps(payload)


def test_envelope_keeps_tokens_cost_and_model_and_drops_a_partial_bill():
    levels = grok_status.parse_result(json.loads(_envelope()))
    assert levels["tokens"] == {
        "input_tokens": 10,
        "cache_read_input_tokens": 4,
        "cache_creation_input_tokens": 0,
        "output_tokens": 3,
    }
    assert levels["spend"]["total_cost_usd"] == 0.012689
    assert levels["spend"]["total_cost_usd_ticks"] == 126890500
    assert "quota" not in levels and "context_window" not in levels
    assert grok_status.resolved_model_id(levels) == "grok-4.7"
    del_missing = json.loads(_envelope())
    del del_missing["total_cost_usd"]
    assert "spend" not in grok_status.parse_result(del_missing)
    partial = grok_status.parse_result(json.loads(_envelope(cost_is_partial=True)))
    assert "spend" not in partial and partial["tokens"]["output_tokens"] == 3
    incomplete = grok_status.parse_result(json.loads(_envelope(usage_is_incomplete=True)))
    assert "tokens" not in incomplete and "spend" not in incomplete
    assert grok_status.session_id(_envelope()) == _SESSION
    assert grok_status.session_id('{"text":"x","sessionId":"abc123"}') is None
    assert grok_status.supported("grok-4.7")
    assert not grok_status.supported("claude")
    assert not grok_status.supported("grokking")


def test_invocation_reads_the_envelope_and_resumes_by_uuid(tmp_path, monkeypatch):
    binary = tmp_path / "grok"
    binary.write_text("#!" + sys.executable + "\n" + r'''
import os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:2] == ["--resume", os.environ["EXPECTED_RESUME"]]
args = args[2:]
assert args[0] == "--prompt-file"
assert Path(args[1]).read_text() == "the wake"
assert "GROK_RESUME_SESSION" not in os.environ
print(os.environ["GROK_ENVELOPE"])
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
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    outbox = tmp_path / "outbox"
    response = tmp_path / "response.md"
    result = runner.invoke_runner(
        runner_select.runner_from_profile("grok-4.7", {
            "cmd": "brnrd runners _grok", "binary": "grok", "hooks": "grok",
        }),
        runner.RunnerInvocation(
            kind="test", label="grok", prompt="the wake", cwd=tmp_path,
            repo_root=tmp_path, response_path=str(response), timeout_seconds=15,
            resume_native_session_id=_SESSION,
            expected_core="grok-4.7",
            env={
                "EXPECTED_RESUME": _SESSION,
                "GROK_ENVELOPE": _envelope(),
                "BRR_OUTBOX_DIR": str(outbox),
                "BRR_RUN_ID": "run-grok",
            },
        ),
    )
    assert result.returncode == 0, result.stderr
    assert response.read_text() == "done\n"
    assert result.grok_session_id == _SESSION
    assert result.observed_core == "grok-4.7"
    assert result.core_mismatch is False
    assert _SESSION not in result.command
    assert "the wake" not in result.command
    from brr import daemon, run_ledger

    levels, slots = daemon._collect_levels("grok-4.7", outbox, refresh=False)
    assert slots == frozenset({"spend", "quota"})
    assert "quota" not in levels
    assert run_ledger.token_fields(levels)["tokens_input"] == 10
    assert run_ledger.token_fields(levels)["tokens_output"] == 3
    assert levels["spend"]["summary"].endswith("this session")
    assert levels["run_id"] == "run-grok"


def test_a_title_is_not_a_resume_target(tmp_path, monkeypatch):
    binary = tmp_path / "grok"
    binary.write_text("#!" + sys.executable + "\n" + r'''
import sys
args = sys.argv[1:]
assert "--resume" not in args
print('{"text":"done","sessionId":"not-a-uuid"}')
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
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = runner.invoke_runner(
        runner_select.runner_from_profile("grok", {
            "cmd": "brnrd runners _grok", "binary": "grok",
        }),
        runner.RunnerInvocation(
            kind="test", label="grok", prompt="the wake", cwd=tmp_path,
            repo_root=tmp_path, timeout_seconds=15,
            resume_native_session_id="yesterday's chat",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "done\n"
    assert result.grok_session_id is None


def test_shared_fallback_keeps_spend_and_drops_the_other_run_tokens(tmp_path):
    from brr import daemon

    shared = tmp_path / "shared"
    grok_status.write_snapshot(shared, grok_status.parse_result(json.loads(_envelope())))
    levels, slots = daemon._collect_levels("grok", None, shared_dir=shared, refresh=False)
    assert slots == frozenset({"spend", "quota"})
    assert "tokens" not in levels
    assert "previous Grok session" in levels["spend"]["summary"]


def test_grok_core_catalog_pins_the_model_through_the_adapter_env():
    import shlex

    entries = runner_cores.generated_profile_entries({
        "grok": {"cmd": "brnrd runners _grok", "binary": "grok", "hooks": "grok"},
    }, probe=False)
    row = entries["grok-4.7"]
    assert row["model"] == "grok-4.7"
    assert row["hooks"] == "grok"
    assert shlex.split(row["cmd"]) == [
        "env", "GROK_ACTIVE_MODEL=grok-4.7", "brnrd", "runners", "_grok",
    ]
    assert row["cost_rank"] is None


def test_grok_hook_file_uses_post_tool_use(tmp_path):
    path = hooks.install_hook_config("grok", tmp_path)
    settings = json.loads(path.read_text())
    assert path == tmp_path / ".claude" / "settings.local.json"
    assert set(settings["hooks"]) == {"PostToolUse", "Stop", "SessionStart", "PreToolUse"}
    assert "PostToolBatch" not in settings["hooks"]
    assert settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "brnrd hook post-tool"


def test_grok_pre_tool_deny_uses_claude_permission_decision():
    out, rc = hooks.render_native(
        "grok", hooks.PHASE_PRE_TOOL,
        {"inject": None, "block": True, "block_reason": "rooted write"},
    )
    assert rc == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    stopped, _ = hooks.render_native(
        "grok", hooks.PHASE_STOP,
        {"inject": None, "block": True, "block_reason": "owed a card"},
    )
    assert stopped["decision"] == "block" and stopped["reason"] == "owed a card"


def _grok_pre_tool(tool_name, file_path, cwd, *, path_key="file_path"):
    return json.dumps({
        "hook_event_name": "PreToolUse",
        "cwd": str(cwd),
        "toolName": tool_name,
        "toolInput": {path_key: str(file_path), "old_string": "a", "new_string": "b"},
    })


def test_grok_write_into_host_checkout_is_denied(tmp_path):
    host = tmp_path / "host"
    wt = host / ".brr" / "worktrees" / "run-x"
    wt.mkdir(parents=True)
    env = {"BRR_RUNNER": "grok", "BRR_HOST_ROOT": str(host), "BRR_WORK_TREE": str(wt)}
    out, rc = hooks.run_hook(
        "pre-tool", _grok_pre_tool("search_replace", host / "stray.txt", wt), env,
    )
    assert rc == 0 and out["hookSpecificOutput"]["permissionDecision"] == "deny"
    via_path, _ = hooks.run_hook(
        "pre-tool",
        _grok_pre_tool("search_replace", host / "stray.txt", wt, path_key="path"),
        env,
    )
    assert via_path["hookSpecificOutput"]["permissionDecision"] == "deny"
    ok, rc = hooks.run_hook(
        "pre-tool", _grok_pre_tool("search_replace", wt / "ok.txt", wt), env,
    )
    assert (ok, rc) == ({}, 0)
