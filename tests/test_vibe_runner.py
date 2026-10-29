"""Vibe's real public-history schema and subprocess boundary."""
import os
import sys

import pytest

from brr import runner, runner_select


def message(text, role="assistant", status="completed"):
    return {"type": "message", "role": role, "generationStatus": status,
            "sessionId": "session-1", "content": [{"type": "text", "text": text}]}


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("model", [None, "mistral-medium-3.5", "glm-5-3", "local"])
def test_profile_invocation_delivers_stdin_system_prompt_and_exit(tmp_path, monkeypatch, failure, model):
    binary = tmp_path / "vibe"
    binary.write_text("#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
assert sys.argv[1:] == ['-p', '--auto-approve', '--trust', '--output', 'json']
assert os.environ.get('VIBE_ACTIVE_MODEL') == os.environ.get('EXPECTED_VIBE_MODEL')
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
    monkeypatch.delenv("VIBE_ACTIVE_MODEL", raising=False)
    monkeypatch.delenv("EXPECTED_VIBE_MODEL", raising=False)
    monkeypatch.setenv("BRR_VIBE_HOOKS", "0")  # this suite may itself run inside a daemon run
    from brr import runner_cores
    cmd = "brnrd runners _vibe"
    if model:
        cmd = runner_cores._cmd_with_model("vibe", cmd, model)
    profile = runner_select.runner_from_profile("vibe", {
        "cmd": cmd, "binary": "vibe", "provider": "mistral",
        "class": "economy", "cost_rank": 18,
    })
    env = {"VIBE_HOME": str(home)}
    if model:
        env["EXPECTED_VIBE_MODEL"] = model
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


def test_vibe_core_catalog_uses_alias_pins_without_cli_model_flag():
    import shlex
    from brr import runner_cores

    entries = runner_cores.generated_profile_entries({
        "vibe": {"cmd": "brnrd runners _vibe", "binary": "vibe"},
    }, probe=False)
    for name, alias in (("vibe-local", "local"),
                        ("vibe-glm-5-3", "glm-5-3"),
                        ("vibe-mistral-medium-3.5", "mistral-medium-3.5")):
        row = entries[name]
        assert row["model"] == alias
        assert shlex.split(row["cmd"]) == [
            "env", "VIBE_ACTIVE_MODEL=" + alias, "brnrd", "runners", "_vibe",
        ]
        assert row["cost_rank"] is None
