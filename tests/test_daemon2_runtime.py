"""The executable slice uses real event files and a real Shell subprocess."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from pathlib import Path

from brr import conversations, protocol, runner
from brr.daemon2.runtime import Daemon2
from brr.daemon2.doors import FileDoor
from brr.daemon2.transport import GateTransport


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
    assert runtime.door.get(event_path.stem)["status"] == "done"
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


def test_pending_unaddressed_letter_gets_visible_triage_seat(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    event = protocol.create_event(home / "dispatch" / "inbox",
                                  "telegram", "Old letter without a chat id")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]})
    result = runtime.once()
    assert result is not None and result.answered
    assert runtime.door.get(event.stem)["status"] == "done"
    assert runtime.seats.read(f"triage:telegram:{event.stem}").state == "parked"
    portal = json.loads((result.outbox / "portal-state.json").read_text())
    assert any("unaddressed letter retained" in notice["text"]
               for notice in portal["notices"])


def _spawn_shell(path: Path, branch: str, report: str) -> None:
    """A fake Shell that stages a spawn: directive and replies."""
    script = f"""#!/usr/bin/env python3
import os, time
from pathlib import Path

outbox = Path(os.environ["BRR_OUTBOX_DIR"])
event_id = os.environ["BRR_EVENT_ID"]

def stage(name, body):
    tmp = outbox / (name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.rename(outbox / name)

# Stage a spawn: directive (item/ask left empty — no ask in this simple test)
stage("spawn.md", (
    "---\\n"
    "spawn: true\\n"
    f"branch: {branch}\\n"
    f"report: {report}\\n"
    "---\\n"
    "Please do the child work.\\n"
))
time.sleep(0.1)
stage("reply.md", "---\\nevent: " + event_id + "\\n---\\nParent sent spawn.\\n")
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _submit_shell(path: Path, report_path: str, branch: str) -> None:
    """A fake child Shell that creates the report file and stages submit:."""
    script = f"""#!/usr/bin/env python3
import os, time
from pathlib import Path

outbox = Path(os.environ["BRR_OUTBOX_DIR"])

def stage(name, body):
    tmp = outbox / (name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.rename(outbox / name)

# Create the report file so submit: can stat it
report = Path("{report_path}")
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text("Status: done\\nGeneration 1 child work.\\n", encoding="utf-8")

stage("submit.md", "---\\nsubmit: true\\n---\\nChild work complete.\\n")
time.sleep(0.1)
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_spawn_to_submit_end_to_end(tmp_path: Path) -> None:
    """Parent stages spawn: → child event created → child runs and submits → spawn_submitted."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")

    report_path = str(tmp_path / "reports" / "child-report.md")
    branch = "brr/child-test"

    parent_shell = tmp_path / "parent-shell"
    child_shell = tmp_path / "child-shell"
    _spawn_shell(parent_shell, branch, report_path)
    _submit_shell(child_shell, report_path, branch)

    inbox = home / "dispatch" / "inbox"
    parent_event = protocol.create_event(
        inbox, "telegram", "Spawn a child",
        conversation_key="telegram:owner", trust_tier="owner", ask_id="ask-main")

    # Runtime with no item: the parent shell stages spawn: without an item—
    # override to permit an empty ask address by using ask_id from the event.
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake",
                      runner_config={"runner_cmd": [str(parent_shell)]},
                      tick_seconds=0.02)

    parent_result = runtime.once()
    assert parent_result is not None and parent_result.answered, (
        f"parent not answered; returncode={parent_result and parent_result.returncode}")

    # A child spawn: event should now be pending.
    pending = runtime.door.pending()
    child_events = [e for e in pending if e.get("source") == "spawn"]
    assert child_events, "no child spawn event created after parent staged spawn:"
    child_event = child_events[0]
    assert child_event.get("branch") == branch
    assert child_event.get("report") == report_path

    # Run the child strand.
    child_runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                            runner_name="fake",
                            runner_config={"runner_cmd": [str(child_shell)]},
                            tick_seconds=0.02, worktree_env=False)
    child_result = child_runtime.once(role="strand")
    assert child_result is not None and child_result.answered, (
        f"child not answered; returncode={child_result and child_result.returncode}")

    # spawn_submitted event should exist in the inbox.
    all_events = list(inbox.glob("*.md"))
    submitted = [e for e in all_events
                 if protocol._read_event(e).get("source") == "spawn_submitted"]
    assert submitted, "no spawn_submitted event after child submit:"

    # Supervisor should record the return.
    ask_id = child_event.get("ask_id") or ""
    children = runtime.supervisor.children(ask_id)
    assert children, f"supervisor has no children for ask={ask_id!r}"
    child_rec = next(iter(children.values()))
    assert child_rec.status == "returned"
    assert child_rec.branch == branch
    assert child_rec.report == report_path


def test_child_can_resubmit_a_new_generation_without_ending(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    report = tmp_path / "report.md"
    report.write_text("Status: in progress\n")
    protocol.create_event(home / "dispatch" / "inbox", "spawn", "work",
                          conversation_key="c", ask_id="ask-1",
                          parent_run_id="run-parent", spawn_edge="edge-1",
                          child_run_id="run-child", branch="brr/child",
                          report=str(report))
    binary = tmp_path / "child-shell"
    _verb_shell(binary,
                ("submit-1.md", "---\nsubmit: true\n---\nfirst\n"),
                ("submit-2.md", "---\nsubmit: true\n---\nsecond\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02, worktree_env=False)
    runtime.supervisor.register("ask-1", "c", "run-parent", "edge-1", "run-child")
    result = runtime.once(role="strand")
    assert result is not None and result.answered
    events = [event for event in runtime.door.pending()
              if event["source"] == "spawn_submitted"]
    assert sorted(event["spawn_submit_generation"] for event in events) == [1, 2]
    assert runtime.supervisor.children("ask-1")["edge-1"].generation == 2


def test_child_allowance_ask_mints_parent_letter(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    child = protocol.create_event(
        inbox, "spawn", "work", conversation_key="c", ask_id="ask-1",
        parent_run_id="run-parent", spawn_edge="edge-1",
        child_run_id="run-child", branch="brr/child", report=str(tmp_path / "report.md"))
    binary = tmp_path / "child-shell"
    _verb_shell(binary, ("ask.md", "---\nask: allowance +50k\n---\nNeed more tests\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02, worktree_env=False)
    runtime.supervisor.register("ask-1", "c", "run-parent", "edge-1", "run-child")
    result = runtime.once(role="strand")
    assert result is not None and result.event_id == child.stem
    requests = [event for event in runtime.door.pending()
                if event["source"] == "spawn_allowance_requested"]
    assert len(requests) == 1
    assert requests[0]["spawn_parent_run_id"] == "run-parent"
    assert requests[0]["spawn_allowance_request_tokens"] == 50_000
    assert "Need more tests" in requests[0]["body"]


def test_stop_cancels_pending_child_and_preserves_submitted_produce(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    protocol.create_event(inbox, "telegram", "stop child",
                          conversation_key="c", ask_id="ask-1")
    child_event = protocol.create_event(
        inbox, "spawn", "work", conversation_key="c", ask_id="ask-1",
        parent_run_id="run-parent", spawn_edge="edge-1",
        child_run_id="run-child", branch="brr/child", report=str(tmp_path / "report.md"))
    binary = tmp_path / "parent-shell"
    _verb_shell(binary, ("stop.md", "---\nstop: edge-1\nreason: contract changed\n---\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02, worktree_env=False)
    runtime.supervisor.register("ask-1", "c", "run-parent", "edge-1", "run-child")
    runtime.supervisor.returned("ask-1", "c", "run-parent", "edge-1", "run-child",
                                report=str(tmp_path / "report.md"), branch="brr/child")
    # A resumed parent thought owns the original edge.
    runtime._run_id = lambda: "run-parent"
    result = runtime.once(role="resident")
    assert result is not None
    stopped = runtime.supervisor.children("ask-1")["edge-1"]
    assert stopped.status == "stopped"
    assert (stopped.report, stopped.branch) == (str(tmp_path / "report.md"), "brr/child")
    completed = [event for event in runtime.door.pending()
                 if event["source"] == "spawn_completed"]
    assert len(completed) == 1
    assert completed[0]["spawn_report_path"] == str(tmp_path / "report.md")
    assert completed[0]["spawn_published_branch"] == "brr/child"
    assert runtime.once(role="strand") is None
    assert runtime.door.get(child_event.stem)["status"] == "noted"


def test_stop_terminates_running_child_shell(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    child_event = protocol.create_event(
        inbox, "spawn", "work", conversation_key="c", ask_id="ask-1",
        parent_run_id="run-parent", spawn_edge="edge-1",
        child_run_id="run-child", branch="brr/child", report=str(tmp_path / "report.md"))
    child_shell = tmp_path / "child-shell"
    _sleep_shell(child_shell, seconds=30)
    child_runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                            runner_name="fake",
                            runner_config={"runner_cmd": [str(child_shell)]},
                            tick_seconds=0.02, worktree_env=False)
    child_runtime.supervisor.register("ask-1", "c", "run-parent", "edge-1", "run-child")
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(child_runtime.once(role="strand")))
    thread.start()
    try:
        _wait_processing(child_runtime, child_event)
        protocol.create_event(inbox, "telegram", "stop child",
                              conversation_key="c", ask_id="ask-1")
        parent_shell = tmp_path / "parent-shell"
        _verb_shell(parent_shell, ("stop.md", "---\nstop: run-child\n---\n"))
        parent_runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                                 runner_name="fake",
                                 runner_config={"runner_cmd": [str(parent_shell)]},
                                 tick_seconds=0.02, worktree_env=False)
        parent_runtime._run_id = lambda: "run-parent"
        parent_runtime.once(role="resident")
        thread.join(timeout=8)
        assert not thread.is_alive(), "stop did not terminate the running child"
        assert outcome and outcome[0] is not None
        assert child_runtime.door.get(child_event.stem)["status"] == "noted"
        assert child_runtime.seats.read("c#strand:run-child").state == "ended"
    finally:
        if thread.is_alive():
            child_runtime._terminate_runner("run-child")
            thread.join(timeout=5)


def _sleep_shell(path: Path, *, seconds: float = 1) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os,time\nfrom pathlib import Path\n"
        f"time.sleep({seconds})\n"
        "d=Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "e=os.environ['BRR_EVENT_ID']\n"
        "(d/'reply.md').write_text('---\\nevent: '+e+'\\n---\\nafter sleep\\n')\n",
        encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _wait_processing(runtime: Daemon2, path: Path, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runtime.door.get(path.stem)["status"] == "processing":
            return
        time.sleep(0.02)
    raise AssertionError("letter was never claimed")


def test_long_shell_renews_letter_claim_and_blocks_second_holder(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "slow-shell"
    _sleep_shell(binary)
    event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                  "Slow task", conversation_key="c")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02, lease_ttl_seconds=0.3)
    output = []
    thread = threading.Thread(target=lambda: output.append(runtime.once()))
    thread.start()
    try:
        _wait_processing(runtime, event)
        time.sleep(0.6)
        assert runtime.letters.claim(event.stem, "rival", 0.3,
                                     now=time.time()) is None
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert output[0] is not None and output[0].answered
    finally:
        if thread.is_alive():
            runner.kill_matching(runtime.seats.read("c").run_id)
            thread.join(timeout=5)


def test_lapsed_self_lease_kills_shell_and_leaves_recoverable_seat(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "slow-shell"
    _sleep_shell(binary, seconds=3)
    event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                  "Slow task", conversation_key="c")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02, lease_ttl_seconds=0.3)
    output = []
    thread = threading.Thread(target=lambda: output.append(runtime.once()))
    thread.start()
    try:
        _wait_processing(runtime, event)
        run_id = runtime.seats.read("c").run_id
        deadline = time.monotonic() + 10
        while runner.live_pid_for_label(run_id) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert runner.live_pid_for_label(run_id) is not None
        runtime.leases.clock = lambda: time.time() + 10
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert output[0] is not None and not output[0].answered
        assert output[0].returncode != 0
        assert runner.live_pid_for_label(run_id) is None
        assert runtime.seats.read("c").state == "running"
        assert runtime.door.get(event.stem)["status"] == "processing"
    finally:
        if thread.is_alive():
            runner.kill_matching(runtime.seats.read("c").run_id)
            thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Tests for individual outbox verbs staged by a fake Shell.
# ---------------------------------------------------------------------------

def _verb_shell(path: Path, *verb_files: tuple[str, str]) -> None:
    """Create a fake Shell that stages *verb_files* (name, content) then exits."""
    # Each call must be at module level, not indented inside the def block.
    stages = "\n".join(
        f"stage({name!r}, {content!r})"
        for name, content in verb_files
    )
    script = (
        "#!/usr/bin/env python3\n"
        "import os, time\nfrom pathlib import Path\n"
        "outbox = Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "def stage(name, body):\n"
        "    tmp = outbox / (name + '.tmp')\n"
        "    tmp.write_text(body, encoding='utf-8')\n"
        "    tmp.rename(outbox / name)\n"
        + stages + "\n"
        "time.sleep(0.05)\n"
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_also_retires_burst_events(tmp_path: Path) -> None:
    """also: in an event: reply marks additional events handled atomically."""
    binary = tmp_path / "shell"
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    primary = protocol.create_event(inbox, "telegram", "primary",
                                    conversation_key="c", trust_tier="owner", telegram_user_id="42")
    also1 = protocol.create_event(inbox, "telegram", "sibling 1",
                                  conversation_key="c", trust_tier="owner", telegram_user_id="42")
    also2 = protocol.create_event(inbox, "telegram", "sibling 2",
                                  conversation_key="c", trust_tier="owner", telegram_user_id="42")
    reply = (
        "---\n"
        f"event: {primary.stem}\n"
        f"also: {also1.stem}, {also2.stem}\n"
        "---\n"
        "Handled the burst.\n"
    )
    _verb_shell(binary, ("reply.md", reply))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.answered
    assert runtime.door.get(primary.stem)["status"] == "done"
    assert runtime.door.get(also1.stem)["status"] == "done"
    assert runtime.door.get(also2.stem)["status"] == "done"


def test_also_late_refusal_releases_preclaimed_sibling(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    primary = protocol.create_event(inbox, "telegram", "primary",
                                    conversation_key="c", telegram_user_id="42")
    sibling = protocol.create_event(inbox, "telegram", "sibling",
                                    conversation_key="c", telegram_user_id="42")
    binary = tmp_path / "shell"
    _verb_shell(binary, ("reply.md",
                         f"---\nevent: {primary.stem}\n"
                         f"also: {sibling.stem}, evt-1234567890123-nope\n"
                         "---\nBoth handled.\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and not result.answered
    assert protocol.read_response(home / "dispatch" / "responses", primary.stem) is None
    assert runtime.letters.state(sibling.stem).state == "pending"
    assert runtime.door.get(sibling.stem)["status"] == "pending"


def test_to_delivers_steer_to_child(tmp_path: Path) -> None:
    """to: <edge> creates a dispatch_message for the named child strand."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    report_path = str(tmp_path / "reports" / "child.md")
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)

    spawn_md = (
        "---\nspawn: true\nbranch: brr/child-steer-test\n"
        f"report: {report_path}\n---\nChild task.\n"
    )
    parent_script = (
        "#!/usr/bin/env python3\n"
        "import os, time\nfrom pathlib import Path\n"
        "outbox = Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "eid = os.environ['BRR_EVENT_ID']\n"
        "def stage(name, body):\n"
        "    tmp = outbox / (name + '.tmp')\n"
        "    tmp.write_text(body, encoding='utf-8')\n"
        "    tmp.rename(outbox / name)\n"
        f"stage('spawn.md', {spawn_md!r})\n"
        "time.sleep(0.05)\n"
        "stage('reply.md', '---\\nevent: ' + eid + '\\n---\\nSpawned.\\n')\n"
    )
    parent_shell = tmp_path / "parent-shell"
    parent_shell.write_text(parent_script, encoding="utf-8")
    parent_shell.chmod(parent_shell.stat().st_mode | stat.S_IXUSR)

    protocol.create_event(
        inbox, "telegram", "Spawn a child",
        conversation_key="c", trust_tier="owner", ask_id="ask1")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(parent_shell)]},
                      tick_seconds=0.02)
    runtime.once()

    children = runtime.supervisor.children("ask1")
    assert children, "no child registered"
    edge, child = next(iter(children.items()))
    to_script = (
        "#!/usr/bin/env python3\n"
        "import os, time\nfrom pathlib import Path\n"
        "outbox = Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "eid = os.environ['BRR_EVENT_ID']\n"
        "def stage(name, body):\n"
        "    tmp = outbox / (name + '.tmp')\n"
        "    tmp.write_text(body, encoding='utf-8')\n"
        "    tmp.rename(outbox / name)\n"
        f"stage('to.md', '---\\nto: {edge}\\n---\\nallowance: +50k\\nHere is the steer.\\n')\n"
        "time.sleep(0.05)\n"
        "stage('reply.md', '---\\nevent: ' + eid + '\\n---\\nSteer sent.\\n')\n"
    )
    to_shell = tmp_path / "to-shell"
    to_shell.write_text(to_script, encoding="utf-8")
    to_shell.chmod(to_shell.stat().st_mode | stat.S_IXUSR)

    protocol.create_event(
        inbox, "telegram", "Steer the child",
        conversation_key="c", trust_tier="owner", ask_id="ask1")
    runtime2 = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                       runner_name="fake", runner_config={"runner_cmd": [str(to_shell)]},
                       tick_seconds=0.02)
    result2 = runtime2.once()
    assert result2 is not None and result2.answered

    pending = runtime2.door.pending()
    steer_msgs = [e for e in pending
                  if e.get("source") == "dispatch_message"
                  and e.get("spawn_message_for_run") == child.run]
    assert steer_msgs, "no dispatch_message created for child by to: verb"
    assert "Here is the steer" in steer_msgs[0].get("body", "")
    assert steer_msgs[0]["allowance_tokens"] == 20_050_000
    assert runtime2._child_allowance("ask1", child.run, edge) == 20_050_000


def test_halt_ends_seat_and_replies(tmp_path: Path) -> None:
    """halt: ends the seat and uses the body as the reply."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    event = protocol.create_event(inbox, "telegram", "Please halt",
                                  conversation_key="halt-conv")
    halt_md = (
        "---\nhalt: true\nreason: Context window saturated.\n"
        "resumable: Refill quota and dispatch a fresh seat with this carry.\n"
        "---\n"
        "Body ends here.\n"
    )
    halt_script = (
        "#!/usr/bin/env python3\n"
        "import os\nfrom pathlib import Path\n"
        "outbox = Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "def stage(name, body):\n"
        "    tmp = outbox / (name + '.tmp')\n"
        "    tmp.write_text(body, encoding='utf-8')\n"
        "    tmp.rename(outbox / name)\n"
        f"stage('halt.md', {halt_md!r})\n"
    )
    binary = tmp_path / "shell"
    binary.write_text(halt_script, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.answered
    reply = protocol.read_response(home / "dispatch" / "responses", event.stem)
    assert reply is not None and "Body ends here" in reply, f"reply={reply!r}"
    assert runtime.seats.read("halt-conv").state == "ended"


def test_halt_carry_mints_fenced_successor_with_unclipped_brief(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    original = protocol.create_event(inbox, "telegram", "first",
                                     conversation_key="c", telegram_user_id="42")
    carry = "continue " + "x" * 3000
    binary = tmp_path / "halt-shell"
    _verb_shell(binary, ("halt.md", "---\nhalt: true\nreason: fresh body\n"
                         f"carry: {carry}\n---\nTaking a new body.\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    first = runtime.once()
    assert first is not None and first.answered
    successor = [event for event in runtime.door.pending()
                 if event["id"] != original.stem]
    assert len(successor) == 1
    assert successor[0]["body"] == carry
    assert successor[0]["handover_from_run"] == first.run_id
    assert runtime.seats.read("c").state == "parked"

    reply_binary = tmp_path / "reply-shell"
    _fake_shell(reply_binary)
    runtime.runner_config = {"runner_cmd": [str(reply_binary)]}
    second = runtime.once()
    assert second is not None and second.event_id == successor[0]["id"]
    assert second.answered
    assert runtime.letters.state(successor[0]["id"]).state == "answered"


def test_respawn_legacy_file_retires_through_carried_halt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    original = protocol.create_event(inbox, "telegram", "first",
                                     conversation_key="c", telegram_user_id="42")
    binary = tmp_path / "respawn-shell"
    _verb_shell(binary, ("respawn.md", "---\nrespawn: true\nshell: claude\n"
                         "---\ncarry on\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.answered
    successors = [event for event in runtime.door.pending()
                  if event["id"] != original.stem]
    assert len(successors) == 1
    assert successors[0]["body"] == "carry on"
    assert successors[0]["handover_from_run"] == result.run_id
    assert successors[0]["shell"] == "claude"
    assert runtime.seats.read("c").state == "parked"
    reply = protocol.read_response(home / "dispatch" / "responses", original.stem)
    assert reply is not None and "respawn requested" in reply


def test_halt_bounces_open_mail_once_before_ending(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    primary = protocol.create_event(inbox, "telegram", "first",
                                    conversation_key="c", telegram_user_id="42")
    sibling = protocol.create_event(inbox, "telegram", "second",
                                    conversation_key="c", telegram_user_id="42")
    halt = "---\nhalt: true\nreason: spent\nresumable: read report\n---\nBye.\n"
    binary = tmp_path / "shell"
    _verb_shell(binary, ("first.md", halt), ("second.md", halt))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.answered
    assert runtime.seats.read("c").state == "ended"
    notices = json.loads((result.outbox / "portal-state.json").read_text())["notices"]
    assert any("halt bounced" in row["text"] and sibling.stem[-4:] in row["text"]
               for row in notices)
    assert protocol.read_response(home / "dispatch" / "responses", primary.stem) == "Bye."
    assert runtime.door.get(sibling.stem)["status"] == "pending"


def test_halt_without_carry_allows_a_later_message_new_seat(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    first = protocol.create_event(inbox, "telegram", "first",
                                  conversation_key="c", telegram_user_id="42")
    binary = tmp_path / "halt-shell"
    _verb_shell(binary, ("halt.md", "---\nhalt: true\nreason: blocked\n"
                         "resumable: user sends a new request\n---\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    ended = runtime.once()
    assert ended is not None and ended.answered
    assert "blocked" in protocol.read_response(home / "dispatch" / "responses",
                                               first.stem)
    assert runtime.seats.read("c").state == "ended"

    later = protocol.create_event(inbox, "telegram", "second",
                                  conversation_key="c", telegram_user_id="42")
    reply_binary = tmp_path / "reply-shell"
    _fake_shell(reply_binary)
    runtime.runner_config = {"runner_cmd": [str(reply_binary)]}
    resumed = runtime.once()
    assert resumed is not None and resumed.event_id == later.stem
    assert resumed.answered


def test_halt_bounces_unticked_course_and_live_child(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                  "work", conversation_key="c", ask_id="ask-1",
                                  telegram_user_id="42")
    report = tmp_path / "child-report.md"
    spawn = ("---\nspawn: true\nbranch: brr/child\n"
             f"report: {report}\n---\nchild task\n")
    halt = "---\nhalt: true\nreason: spent\nresumable: read report\n---\nbye\n"
    binary = tmp_path / "shell"
    _verb_shell(binary,
                (".card", "## Plan\n- [ ] fix the parser\n"),
                ("spawn.md", spawn), ("halt.md", halt))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and not result.answered
    assert protocol.read_response(home / "dispatch" / "responses", event.stem) is None
    notices = json.loads((result.outbox / "portal-state.json").read_text())["notices"]
    bounce = [row["text"] for row in notices if "halt bounced" in row["text"]]
    assert bounce and "course:1" in bounce[0] and "strand " in bounce[0]


def test_cut_parks_seat_and_replies(tmp_path: Path) -> None:
    """cut: answers the current event and parks the seat."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    event = protocol.create_event(inbox, "telegram", "Please cut",
                                  conversation_key="cut-conv")
    cut_md = "---\ncut: true\n---\nPhase complete — commit for this stretch.\n"
    cut_script = (
        "#!/usr/bin/env python3\n"
        "import os\nfrom pathlib import Path\n"
        "outbox = Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "(outbox / '.topics').write_text('daemon\\n')\n"
        "def stage(name, body):\n"
        "    tmp = outbox / (name + '.tmp')\n"
        "    tmp.write_text(body, encoding='utf-8')\n"
        "    tmp.rename(outbox / name)\n"
        f"stage('cut.md', {cut_md!r})\n"
    )
    binary = tmp_path / "shell"
    binary.write_text(cut_script, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.answered
    reply = protocol.read_response(home / "dispatch" / "responses", event.stem)
    assert reply is not None and "Phase complete" in reply
    seat = runtime.seats.read("cut-conv")
    assert seat.state == "parked"


def test_cut_third_bounce_accepts_with_dissent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                  "cut", conversation_key="c")
    cut = "---\ncut: true\n---\nDone.\n"
    binary = tmp_path / "shell"
    _verb_shell(binary, ("cut-1.md", cut), ("cut-2.md", cut), ("cut-3.md", cut))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.answered
    response = protocol.read_response(home / "dispatch" / "responses", event.stem)
    assert "daemon: 1 check unresolved" in response
    notices = json.loads((result.outbox / "portal-state.json").read_text())["notices"]
    assert sum("cut bounced:" in row["text"] for row in notices) == 2
    accepted = [fact for fact in runtime.facts.read("seats", "c")
                if fact.kind == "cut_accepted"]
    assert len(accepted) == 1 and accepted[0].data["attempts"] == 3


def test_cut_handoff_parks_on_live_child_edge(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                  "cut", conversation_key="c", ask_id="ask-1")
    binary = tmp_path / "shell"
    _verb_shell(binary,
                (".topics", "the-clockwork\n"),
                ("cut.md", "---\ncut: true\nstrands:\n  run-child: handoff\n"
                 "---\nChild remains live.\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    runtime._run_id = lambda: "run-parent"
    runtime.supervisor.register("ask-1", "c", "run-parent", "edge-1", "run-child")
    result = runtime.once()
    assert result is not None and result.answered
    seat = runtime.seats.read("c")
    assert seat.state == "parked" and seat.why == "strands"
    assert any(p.kind == "C" and p.params["edge"] == "edge-1"
               for p in seat.wake_on)
    assert "strand:run-child" in seat.obligations
    assert runtime.door.get(event.stem)["status"] == "done"


def test_gate_and_thread_stage_via_message_store(tmp_path: Path) -> None:
    """gate: and thread: call message_store.stage when an account context exists."""
    from unittest.mock import MagicMock, patch as _patch
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    inbound = protocol.create_event(inbox, "telegram", "Gate and thread test",
                                    telegram_chat_id="owner", trust_tier="owner")
    key = conversations.conversation_key_for_event(protocol._read_event(inbound))
    gate_md = "---\ngate: telegram\n---\nHello from the gate.\n"
    thread_md = f"---\nthread: {key}\n---\nHello on thread.\n"
    gate_script = (
        "#!/usr/bin/env python3\n"
        "import os\nfrom pathlib import Path\n"
        "outbox = Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "eid = os.environ['BRR_EVENT_ID']\n"
        "def stage(name, body):\n"
        "    tmp = outbox / (name + '.tmp')\n"
        "    tmp.write_text(body, encoding='utf-8')\n"
        "    tmp.rename(outbox / name)\n"
        f"stage('gate.md', {gate_md!r})\n"
        f"stage('thread.md', {thread_md!r})\n"
        "stage('reply.md', '---\\nevent: ' + eid + '\\n---\\nDone.\\n')\n"
    )
    binary = tmp_path / "shell"
    binary.write_text(gate_script, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    mock_ctx = MagicMock()
    mock_ctx.default_repo.label = "org/repo"
    staged: list[dict] = []

    def _fake_stage(ctx, *, repo_label, run_id, body, kind,
                    target_gate="", target_thread="", **kw):
        staged.append({"gate": target_gate, "thread": target_thread,
                        "body": body, "kind": kind})
        return None

    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    runtime._account_ctx = mock_ctx
    # Allow telegram gate without real configuration (same pattern as forge tests).
    runtime._gate_available = lambda gate: gate == "telegram"
    with _patch("brr.daemon2.runtime.message_store.stage", side_effect=_fake_stage):
        result = runtime.once()
    assert result is not None and result.answered
    gates = [s for s in staged if s["gate"] == "telegram" and not s["thread"]]
    threads = [s for s in staged if s["thread"] == key]
    assert gates, "gate: did not call message_store.stage"
    assert threads, "thread: did not call message_store.stage"
    assert "Hello from the gate" in gates[0]["body"]
    assert "Hello on thread" in threads[0]["body"]


def test_thread_refuses_when_latest_correspondent_is_not_owner(tmp_path: Path) -> None:
    from unittest.mock import MagicMock, patch as _patch

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    first = protocol.create_event(inbox, "telegram", "owner request",
                                  telegram_chat_id="42", trust_tier="owner")
    key = conversations.conversation_key_for_event(protocol._read_event(first))
    protocol.create_event(inbox, "telegram", "collaborator reply",
                          telegram_chat_id="42", trust_tier="collaborator")
    binary = tmp_path / "shell"
    _verb_shell(binary, ("thread.md", f"---\nthread: {key}\n---\nDo not send.\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    runtime._account_ctx = MagicMock()
    with _patch("brr.daemon2.runtime.message_store.stage") as stage:
        result = runtime.once()
    assert result is not None and not result.answered
    stage.assert_not_called()
    notices = json.loads((result.outbox / "portal-state.json").read_text())["notices"]
    assert any("thread refused: correspondent is not an account user" in row["text"]
               for row in notices)


def test_thread_targets_closed_owner_inbound(tmp_path: Path) -> None:
    from unittest.mock import MagicMock, patch as _patch

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    closed = protocol.create_event(
        inbox, "cloud", "original owner message", status="delivered",
        trust_tier="owner", cloud_platform="telegram", cloud_chat_id="2",
        cloud_topic_id="7", cloud_event_id="remote-event")
    key = conversations.conversation_key_for_event(protocol._read_event(closed))
    protocol.create_event(inbox, "schedule", "send the result",
                          conversation_key=key)
    binary = tmp_path / "shell"
    _verb_shell(binary, ("thread.md", f"---\nthread: {key}\n---\nThe result.\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    runtime._account_ctx = MagicMock()
    with _patch("brr.daemon2.runtime.message_store.stage") as stage:
        result = runtime.once()
    assert result is not None
    assert stage.call_count == 1
    assert stage.call_args.kwargs["target_thread"] == key
    assert stage.call_args.kwargs["target_gate"] == "cloud"
    assert protocol._read_event(closed)["status"] == "delivered"


def test_forge_handoff_uses_existing_github_event_wire(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    protocol.create_event(home / "dispatch" / "inbox", "telegram", "open PR",
                          conversation_key="c", repo_label="org/repo")
    binary = tmp_path / "shell"
    _verb_shell(binary, ("pr.md", "---\ngate: forge\nhead: brr/feat-x\n"
                         "base: main\ntitle: Review feat-x\n---\nprojected body\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    runtime._gate_available = lambda gate: gate == "forge"
    result = runtime.once()
    assert result is not None
    done = protocol.list_done(home / "dispatch" / "inbox", "github")
    assert len(done) == 1
    assert done[0]["github_action"] == "pull_request"
    assert done[0]["head"] == "brr/feat-x"
    assert protocol.read_response(home / "dispatch" / "responses",
                                  done[0]["id"]) == "projected body"
    assert done[0]["id"] in (result.outbox / ".forge-handoff").read_text()


def test_serve_dispatches_two_letters_on_separate_conversations(
        tmp_path: Path) -> None:
    """serve() processes two pending letters in order under the machine lease."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    inbox = home / "dispatch" / "inbox"
    ev1 = protocol.create_event(inbox, "telegram", "First letter",
                                conversation_key="telegram:user-a",
                                trust_tier="owner")
    ev2 = protocol.create_event(inbox, "telegram", "Second letter",
                                conversation_key="telegram:user-b",
                                trust_tier="owner")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      lease_ttl_seconds=2.0)
    results = runtime.serve(stop_when_empty=True)
    assert len(results) == 2
    ids = {r.event_id for r in results}
    assert ev1.stem in ids
    assert ev2.stem in ids
    assert all(r.answered for r in results)
    assert runtime.door.get(ev1.stem)["status"] == "done"
    assert runtime.door.get(ev2.stem)["status"] == "done"


def test_serve_kill9_recovery_via_lease_expiry(tmp_path: Path) -> None:
    """After the machine/execution lease expires a new process can re-dispatch."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    inbox = home / "dispatch" / "inbox"
    event_path = protocol.create_event(inbox, "telegram", "Recover me",
                                       conversation_key="telegram:owner",
                                       trust_tier="owner")
    # Create a daemon2 instance and manually acquire the self-execution lease
    # as if a previous process had claimed it (simulating kill-9 of that process).
    runtime_a = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                        runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                        lease_ttl_seconds=0.5)
    dead_machine = "dead-host:99999"
    dead_lease = runtime_a.leases.acquire("self", dead_machine, 0.5)
    assert dead_lease is not None, "could not pre-claim execution lease"
    # Letter claim too (simulates the in-flight claim that expired)
    from brr.daemon2.letters import LetterService
    runtime_a.letters.ingest(event_path.stem, "pending",
                              metadata={"conversation": "telegram:owner"})
    dead_claim = runtime_a.letters.claim(event_path.stem, "dead-run", 0.5,
                                          now=time.time())
    assert dead_claim is not None
    # Wait for both leases to expire.
    time.sleep(1.0)
    # A new daemon should now be able to dispatch the event.
    runtime_b = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                        runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                        lease_ttl_seconds=2.0)
    result = runtime_b.once()
    assert result is not None and result.answered, (
        "new daemon could not reclaim expired lease and dispatch the letter")
    assert runtime_b.door.get(event_path.stem)["status"] == "done"


def test_two_serve_processes_share_one_self_and_take_over_after_kill9(
        tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text("daemon2.lease_ttl_seconds=0.6\n")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    command = [sys.executable, "-m", "brr.daemon2", "--serve",
               "--repo", str(repo), "--home", str(home),
               "--runtime-dir", str(tmp_path / "runtime"),
               "--runner", "fake", "--runner-cmd", str(binary)]
    processes = [subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) for _ in range(2)]
    self_path = home / "daemon2" / "leases" / "self.json"

    def until(predicate, timeout: float = 8) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.04)
        raise AssertionError("serve processes did not reach expected state")

    try:
        until(lambda: self_path.exists())
        holder = int(json.loads(self_path.read_text())["holder"].split(":")[-1])
        assert holder in {process.pid for process in processes}
        follower = next(process for process in processes if process.pid != holder)
        assert follower.poll() is None
        first = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                      "first", conversation_key="telegram:a")
        until(lambda: protocol.read_response(home / "dispatch" / "responses",
                                             first.stem) is not None)
        assert int(json.loads(self_path.read_text())["holder"].split(":")[-1]) == holder
        next(process for process in processes if process.pid == holder).kill()
        second = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                       "second", conversation_key="telegram:b")
        until(lambda: protocol.read_response(home / "dispatch" / "responses",
                                             second.stem) is not None)
        assert int(json.loads(self_path.read_text())["holder"].split(":")[-1]) == follower.pid
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            process.wait(timeout=8)


def test_gate_transport_sends_once_across_restart_and_fences_stale_gen(
        tmp_path: Path, monkeypatch) -> None:
    from brr.gates import runtime as gate_runtime, telegram

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    event_path = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                       "reply to me", conversation_key="telegram:owner",
                                       telegram_chat_id=123)
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]})
    assert runtime.once() is not None
    raw = protocol._read_event(event_path)
    assert raw is not None and raw["status"] == "done"
    holder = runtime.leases.acquire("self", "host:1", 30)
    assert holder is not None
    transport = GateTransport(runtime.leases, runtime.facts, lambda: holder)
    transport.install(runtime.door.inbox, runtime.door.responses)
    sent: list[str] = []

    def fake_api(_token, method, _params=None, **_kwargs):
        sent.append(method)
        return {"ok": True, "result": {"message_id": 77}}

    monkeypatch.setattr(telegram, "_api_call", fake_api)
    try:
        telegram._deliver_responses(repo / ".brr", runtime.door.inbox,
                                    runtime.door.responses, "secret")
        assert sent == ["sendMessage"]
        send_key = "transport:telegram:" + event_path.stem + ":terminal"
        facts = runtime.facts.read("sends", send_key)
        assert any(f.kind == "sent" and f.data["gen"] == holder.gen
                   for f in facts)

        # Reopen the compatibility carrier to simulate a crash before its
        # delivered status was persisted. The send fact still deduplicates it.
        protocol.set_status(protocol._read_event(event_path), "done")
        assert runtime.leases.release(holder)
        successor = runtime.leases.acquire("self", "host:2", 30)
        assert successor is not None and successor.gen > holder.gen
        gate_runtime._delivery_retry.clear()
        telegram._deliver_responses(repo / ".brr", runtime.door.inbox,
                                    runtime.door.responses, "secret")
        assert sent == ["sendMessage"]  # stale holder was fenced
        replacement = GateTransport(runtime.leases, runtime.facts,
                                    lambda: successor)
        replacement.install(runtime.door.inbox, runtime.door.responses)
        gate_runtime._delivery_retry.clear()
        telegram._deliver_responses(repo / ".brr", runtime.door.inbox,
                                    runtime.door.responses, "secret")
        assert sent == ["sendMessage"]  # recorded send key survives restart
        assert protocol._read_event(event_path)["status"] == "delivered"
    finally:
        gate_runtime.set_delivery_hook(runtime.door.inbox, runtime.door.responses,
                                       None)
        gate_runtime._delivery_retry.clear()


def test_gate_transport_records_undeliverable_with_generation(tmp_path: Path) -> None:
    from brr.gates import runtime as gate_runtime

    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = Daemon2(repo, tmp_path / "home")
    event_path = protocol.create_event(runtime.door.inbox, "telegram", "",
                                       status="done")
    protocol.write_response(runtime.door.responses, event_path.stem, "reply")
    holder = runtime.leases.acquire("self", "host:1", 30)
    assert holder is not None
    transport = GateTransport(runtime.leases, runtime.facts, lambda: holder)
    transport.install(runtime.door.inbox, runtime.door.responses)
    try:
        def refuse(_event, _body):
            raise gate_runtime.PermanentDeliveryError("no address")

        gate_runtime.deliver_stream(runtime.door.inbox, runtime.door.responses,
                                    "telegram", refuse)
        facts = runtime.facts.read(
            "sends", "transport:telegram:" + event_path.stem + ":terminal")
        assert any(f.kind == "undeliverable" and f.data["gen"] == holder.gen
                   and f.data["reason"] == "no address" for f in facts)
        assert protocol._read_event(event_path)["status"] == "error"
    finally:
        transport.remove(runtime.door.inbox, runtime.door.responses)


def test_recorded_telegram_inbound_routes_and_answers_through_serve(
        tmp_path: Path, monkeypatch) -> None:
    from brr.gates import telegram

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    brr_dir = repo / ".brr"
    telegram._save_state(brr_dir, {"token": "secret", "paired_user_id": 41})
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    runtime = Daemon2(repo, tmp_path / "home", runtime_dir=brr_dir,
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    sent: list[str] = []

    def fake_api(_token, method, _params=None, **_kwargs):
        if method == "getUpdates":
            return {"result": [{"update_id": 1, "message": {
                "message_id": 501, "chat": {"id": 123},
                "from": {"id": 41, "first_name": "Ada"},
                "text": "recorded inbound"}}]}
        sent.append(method)
        return {"ok": True, "result": {"message_id": 77}}

    monkeypatch.setattr(telegram, "_api_call", fake_api)

    def one_gate_turn(gate_brr, inbox, responses):
        telegram._loop_once(gate_brr, inbox, responses)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            events = list(inbox.glob("*.md"))
            if events and protocol.read_response(responses, events[0].stem):
                telegram._deliver_responses(gate_brr, inbox, responses, "secret")
                return
            time.sleep(0.02)
        raise AssertionError("daemon2 did not answer recorded gate letter")

    monkeypatch.setattr(telegram, "run_loop", one_gate_turn)
    thread = threading.Thread(target=runtime.serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not sent and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sent == ["sendMessage"]
        events = list(runtime.door.event_paths())
        assert len(events) == 1
        event = runtime.door.get(events[0].stem)
        assert event is not None and event["status"] == "done"
        assert runtime.letters.state(events[0].stem).state == "answered"
        assert runtime.router.route_or_triage(event).conversation
        assert protocol.read_response(runtime.door.response_dir(event),
                                      events[0].stem) == "hello from fake Shell"
        assert any(f.kind == "sent" for f in runtime.facts.read(
            "sends", "transport:telegram:" + events[0].stem + ":terminal"))
    finally:
        runtime.stop()
        thread.join(timeout=8)
        assert not thread.is_alive()


def test_repo_scoped_inbox_uses_its_matching_response_queue(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]})
    repo_inbox = repo / ".brr" / "inbox"
    repo_responses = repo / ".brr" / "responses"
    runtime.door.other_queues = ((repo_inbox, repo_responses, "org/repo"),)
    event_path = protocol.create_event(repo_inbox, "telegram", "from repo",
                                       conversation_key="telegram:repo")
    assert runtime.door.pending()[0]["repo_label"] == "org/repo"
    result = runtime.once()
    assert result is not None and result.answered
    assert result.response.parent == repo_responses
    assert protocol.read_response(repo_responses, event_path.stem) == "hello from fake Shell"


def test_at_schedule_fires_once_across_serve_restart(tmp_path: Path,
                                                     monkeypatch) -> None:
    from types import SimpleNamespace
    from brr import dominion

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    dom = tmp_path / "dominion"
    dom.mkdir()
    monkeypatch.setattr(dominion, "resident_dominion_candidates",
                        lambda *_args, **_kwargs: [SimpleNamespace(path=dom)])
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    home = tmp_path / "home"
    runtime = Daemon2(repo, home, runtime_dir=repo / ".brr",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]})
    # Park a conversation first, so the schedule must use the S predicate.
    protocol.create_event(runtime.door.inbox, "telegram", "first",
                          conversation_key="schedule:followup")
    assert runtime.once() is not None
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    (dom / "schedule.md").write_text(
        f"## Followup\nat: {past}\ncheck the work\n", encoding="utf-8")
    results = runtime.serve(stop_when_empty=True)
    assert len(results) == 1 and results[0].answered
    scheduled = [protocol._read_event(path) for path in runtime.door.event_paths()
                 if protocol._read_event(path)["source"] == "schedule"]
    assert len(scheduled) == 1
    assert scheduled[0]["conversation_key"] == "schedule:followup"
    assert runtime.seats.read("schedule:followup").state == "parked"

    restarted = Daemon2(repo, home, runtime_dir=repo / ".brr",
                        runner_name="fake", runner_config={"runner_cmd": [str(binary)]})
    assert restarted.serve(stop_when_empty=True) == []
    assert len([path for path in restarted.door.event_paths()
                if protocol._read_event(path)["source"] == "schedule"]) == 1
