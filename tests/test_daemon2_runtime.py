"""The executable slice uses real event files and a real Shell subprocess."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from unittest.mock import patch
from pathlib import Path

from brr import protocol
from brr.daemon2.runtime import Daemon2
from brr.daemon2.doors import FileDoor


def _fake_shell(path: Path, *, await_first: bool = False) -> None:
    script = r"""#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path

outbox = Path(os.environ["BRR_OUTBOX_DIR"])
event_id = os.environ["BRR_EVENT_ID"]
portal = Path(os.environ["BRR_PORTAL_STATE"])
def stage(name, body):
    tmp = outbox / (name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.rename(outbox / name)

if AWAIT_FIRST:
    stage("wait.md", "---\nawait: true\ntimeout: 1s\n---\n")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = json.loads(portal.read_text(encoding="utf-8"))
        if state.get("await", {}).get("resolved"):
            break
        time.sleep(0.05)
    else:
        raise SystemExit("await never resolved")
stage("reply.md", "---\nevent: " + event_id + "\n---\nhello from fake Shell\n")
""".replace("AWAIT_FIRST", "True" if await_first else "False")
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_once_invokes_real_shell_and_drains_reply(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    inbox = home / "dispatch" / "inbox"
    event_path = protocol.create_event(
        inbox, "telegram", "Say hello", conversation_key="telegram:owner",
        trust_tier="owner", repo_label="org/a")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]})
    result = runtime.once()
    assert result is not None and result.returncode == 0 and result.answered
    assert protocol.read_response(home / "dispatch" / "responses",
                                  event_path.stem) == "hello from fake Shell"
    assert protocol._read_event(event_path)["status"] == "done"
    assert runtime.letters.state(event_path.stem).state == "answered"
    assert any(f.kind == "sent" for f in runtime.facts.read(
        "sends", "reply:" + event_path.stem))
    capsule = json.loads((result.outbox / "portal-state.json").read_text())
    assert capsule["inbound"]["current_event"] == event_path.stem
    assert (result.outbox / "inbox.json").exists()
    assert (tmp_path / "runtime" / "runs" / result.run_id / "context.md").exists()


def test_await_arms_and_resolves_while_shell_stays_alive(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary, await_first=True)
    event_path = protocol.create_event(
        home / "dispatch" / "inbox", "telegram", "Wait then reply",
        conversation_key="telegram:owner", trust_tier="owner")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.returncode == 0 and result.answered
    capsule = json.loads((result.outbox / "portal-state.json").read_text())
    assert capsule["await"]["resolved"] is True
    assert capsule["await"]["outcome"] == "timeout"
    assert runtime.letters.state(event_path.stem).state == "answered"


def test_module_entrypoint_runs_the_same_wire(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                  "CLI dispatch", conversation_key="chat:one")
    env = os.environ.copy()
    source = Path(__file__).parents[1] / "src"
    env["PYTHONPATH"] = str(source)
    proc = subprocess.run(
        [sys.executable, "-m", "brr.daemon2", "--once",
         "--repo", str(repo), "--home", str(home),
         "--runtime-dir", str(tmp_path / "runtime"),
         "--runner", "fake", "--runner-cmd", str(binary)],
        env=env, capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(proc.stdout)
    assert result["event_id"] == event.stem
    assert result["answered"] is True


def test_portal_capsule_matches_redacted_live_shape_and_notice_wire(tmp_path: Path) -> None:
    reference = json.loads(
        (Path(__file__).parent / "fixtures" /
         "daemon2_portal_state_redacted.json").read_text(encoding="utf-8"))
    notice = {"at": "2026-10-03T00:00:00Z", "kind": "refused",
              "text": "event refused: target absent", "lifetime": "run",
              "run": "r1", "verb": "event"}
    FileDoor.write_views(tmp_path, "evt-test", [], phase="running",
                         notices=[notice], run_id="r1",
                         repo="org/repo", runner_name="fake")
    actual = json.loads((tmp_path / "portal-state.json").read_text())
    assert set(actual) == set(reference)
    for key, value in reference.items():
        if isinstance(value, dict):
            assert set(actual[key]) == set(value), key
    assert set(actual["notices"][0]) == set(reference["notices"][0])
    assert actual["notices"][0]["text"] == notice["text"]
    assert actual["inbound"]["current_event"] == "evt-test"
    assert actual["run"]["id"] == "r1"


def test_runner_resolved_from_each_letter_before_dispatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = Daemon2(repo, tmp_path / "home")
    with patch("brr.daemon2.runtime.runner.resolve_runner_profile") as resolve:
        resolve.return_value.name = "codex-gpt-6-sol"
        assert runtime._runner_for({
            "runner": "claude-sonnet",
            "dashboard_wake_request_profile": "codex-gpt-6-sol",
            "core": "gpt-6-sol",
        }).name == "codex-gpt-6-sol"
        assert resolve.call_args.args[0] == repo
        assert resolve.call_args.args[1] == {
            "runner": "codex-gpt-6-sol", "core": "gpt-6-sol"}
