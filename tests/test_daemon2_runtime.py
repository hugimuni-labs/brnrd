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

from brr import conversations, config as conf, protocol, runner
from brr.daemon2.runtime import Daemon2
from brr.daemon2.doors import FileDoor
from brr.daemon2.transport import GateTransport
from brr.daemon2.seat import Seat


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


def test_plain_outbox_messages_stream_before_terminal_stdout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n")
    responses = home / "dispatch" / "responses"
    binary = tmp_path / "stream-shell"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import os,time\nfrom pathlib import Path\n"
        "outbox=Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "event_id=os.environ['BRR_EVENT_ID']\n"
        f"responses=Path({str(responses)!r})\n"
        "partials=responses/(event_id+'.partials')\n"
        "for index,body in enumerate(('first interim','second interim'),1):\n"
        "    (outbox/f'{index:03d}.md').write_text(body+'\\n')\n"
        "    deadline=time.monotonic()+10\n"
        "    while len(list(partials.glob('*.md')))<index:\n"
        "        if time.monotonic()>deadline: raise SystemExit('interim not streamed')\n"
        "        time.sleep(.02)\n"
        "print('terminal answer')\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                  "Stream then answer", conversation_key="telegram:owner")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.returncode == 0 and result.answered
    assert [protocol.read_partial(path) for path in
            protocol.list_partials(responses, event.stem)] == [
                "first interim", "second interim"]
    assert protocol.read_response(responses, event.stem) == "terminal answer"
    assert [fact.kind for fact in runtime.facts.read("letters", event.stem)] == [
        "pending", "claimed", "answered"]
    assert not json.loads((result.outbox / "portal-state.json").read_text())["notices"]


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


def test_portal_capsule_with_resources_populates_quota_and_coexisting(tmp_path: Path) -> None:
    """Test that resources (quota + coexisting runs) are wired into portal-state."""
    from brr.daemon2.doors import FileDoor
    
    # Test with resources parameter passed
    resources = {
        "quota": {
            "pacing": {
                "starvation": {
                    "binding_remaining_pct": 50.0,
                    "starve_floor_pct": 2.0,
                    "refill_floor_pct": 10.0,
                    "starved": False,
                }
            }
        },
        "coexisting_runs": {
            "siblings": [{"run_id": "sibling1", "kind": "daemon"}]
        }
    }
    
    FileDoor.write_views(tmp_path, "evt-test", [], phase="running",
                         notices=[], run_id="r1", repo="org/repo", runner_name="fake",
                         resources=resources)
    
    actual = json.loads((tmp_path / "portal-state.json").read_text())
    
    # Verify resources are populated
    assert "resources" in actual
    assert actual["resources"]["quota"]["pacing"]["starvation"]["binding_remaining_pct"] == 50.0
    assert actual["resources"]["quota"]["pacing"]["starvation"]["starve_floor_pct"] == 2.0
    assert actual["resources"]["quota"]["pacing"]["starvation"]["refill_floor_pct"] == 10.0
    assert actual["resources"]["quota"]["pacing"]["starvation"]["starved"] is False
    assert len(actual["resources"]["coexisting_runs"]["siblings"]) == 1
    assert actual["resources"]["coexisting_runs"]["siblings"][0]["run_id"] == "sibling1"


def test_once_uses_recorded_quota_levels_for_hud_and_hold(tmp_path: Path) -> None:
    """The Shell sees measured pacing and the hold wall uses that reading."""
    from brr import account, daemon as legacy_daemon, presence
    from brr.run import Run, run_manifest_path

    for pct in (50.0, 1.0):
        root = tmp_path / str(int(pct))
        repo, home = root / "repo", root / "home"
        repo.mkdir(parents=True)
        (repo / "AGENTS.md").write_text("# Test\n")
        (repo / ".brr").mkdir()
        (repo / ".brr" / "config").write_text(
            f"home.path={home}\nrepo.label=org/repo\n")
        binary = root / "shell"
        _verb_shell(binary, ("hold.md", "hold: true\nresume: refill\n"))
        event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                      "Quota wall", conversation_key="owner")
        runtime = Daemon2(repo, home, runtime_dir=repo / ".brr",
                          runner_name="fake",
                          runner_config={"runner_cmd": [str(binary)]},
                          tick_seconds=0.02)
        sibling = presence.register(repo / ".brr", kind="session",
                                    run_id="sibling-run", stream="other")
        levels = {"quota": {
            "primary_remaining_percent": pct,
            "primary_resets_at": time.time() + 9000,
            "primary_window_minutes": 300.0,
            "summary": f"5h {pct:g}% left",
        }}
        with patch("brr.daemon._collect_levels",
                   return_value=(levels, frozenset({"quota"}))):
            result = runtime.once()
        assert result is not None
        portal = json.loads((result.outbox / "portal-state.json").read_text())
        quota = portal["resources"]["quota"]
        assert quota["pacing"]["binding_remaining_pct"] == pct
        assert quota["pacing"]["pace"]["window_minutes"] == 300.0
        assert quota["pacing"]["starvation"]["starved"] is (pct < 2)
        assert any(row["run_id"] == "sibling-run" for row in
                   portal["resources"]["coexisting_runs"]["siblings"])
        assert (repo / ".brr" / "presence").is_dir()
        assert sorted(path.stem for path in (repo / ".brr" / "presence").glob("*.json")) == [sibling["id"]]
        task = Run.from_file(run_manifest_path(repo / ".brr" / "runs",
                                                result.run_id))
        assert task is not None and task.meta["runner_name"] == "fake"
        run_state = account.run_dir(runtime._account_ctx, "org/repo",
                                    result.run_id) / "state.md"
        frame = run_state.read_text()
        for key in ("run_id", "event_id", "status", "stage", "repo_label",
                    "source", "started_at", "ended_at", "conversation_key",
                    "runner_name", "pid"):
            assert f"{key}:" in frame
        assert f"run_id: {result.run_id}" in frame
        assert "stage: finished" in frame and "repo_label: org/repo" in frame
        if pct < 2:
            assert runtime.seats.read("owner").state == "parked"
            assert task.status == "held"
            assert portal["resource_hold"]["quota"]["binding_remaining_pct"] == pct
            assert not any("hold refused" in n["text"] for n in portal["notices"])
        else:
            task.meta["quota_binding_pct"] = pct
            expected = legacy_daemon._resident_hold_refusal(
                task, "refill", runtime._config)
            assert any(n["text"] == expected for n in portal["notices"])


def test_hold_with_measured_binding_quota_under_floor_parks_seat(tmp_path: Path) -> None:
    """Test that hold: with resume: refill parks the seat when binding quota is under floor."""
    from brr.daemon2.runtime import Daemon2
    
    # Create a Daemon2 instance
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text(
        f"home.path={tmp_path / 'home'}\nrepo.label=local/hold-test\n")
    daemon2 = Daemon2(repo, tmp_path / "home")
    
    # Create minimal state for a running seat
    state = {
        "runner_name": "test-runner",
        "run_id": "test-run-123",
        "event": {"id": "evt-test", "body": "test"},
        "conversation": "test-conv",
        "is_child": False,
        "runner_meta": {"model": None},
        "levels": {},  # This will result in None binding_pct
        "branch": "test-branch",
        "outbox": tmp_path / "outbox",
        "claims": {},
        "claim_lock": threading.Lock(),
        "notices": [],
        "answered": False,
        "await": None,
        "control_run": None,
    }
    
    # Create outbox and seat structures
    outbox_dir = tmp_path / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize seat
    seat = Seat(daemon2.seats, state["conversation"])
    seat.start(0, run_id=state["run_id"])
    state["seat"] = seat
    
    # Set a binding percentage under the floor to trigger the hold
    config = conf.load_config(repo)
    floor = float(config.get("seat.starve_floor_pct", 2))
    state["quota_binding_pct"] = floor - 1.0  # Just under the floor
    
    # Create a hold file
    hold_file = outbox_dir / "hold.md"
    hold_file.write_text("hold: true\nresume: refill\n")
    
    # Process the hold
    daemon2._handle_outbox(state, hold_file)
    
    # Check that the seat was parked
    record = seat.read()
    assert record.state == "parked"
    
    # Check that the reason contains quota_starved
    assert "quota_starved" in record.why


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


def test_stop_from_schedule_seat_reaches_child_under_item_ask(tmp_path: Path) -> None:
    """#2224: a schedule-woken seat (no ask of its own) stops a strand it
    spawned under an ``item:`` ask. The stop lands under the child's ask,
    so the supervisor sees it, and the completion event is emitted."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    inbox = home / "dispatch" / "inbox"
    protocol.create_event(inbox, "schedule", "goal pulse", conversation_key="c")
    protocol.create_event(
        inbox, "spawn", "work", conversation_key="c", ask_id="w-7",
        parent_run_id="run-parent", spawn_edge="edge-1",
        child_run_id="run-child", branch="brr/child", report=str(tmp_path / "report.md"))
    binary = tmp_path / "parent-shell"
    _verb_shell(binary, ("stop.md", "---\nstop: edge-1\nreason: integrated\n---\n"))
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02, worktree_env=False)
    runtime.supervisor.register("w-7", "c", "run-parent", "edge-1", "run-child")
    runtime._run_id = lambda: "run-parent"
    result = runtime.once(role="resident")
    assert result is not None
    assert runtime.supervisor.children("w-7")["edge-1"].status == "stopped"
    completed = [event for event in runtime.door.pending()
                 if event["source"] == "spawn_completed"]
    assert len(completed) == 1 and completed[0]["spawn_stopped"]


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


# Full gate runs several subprocess-heavy tests in parallel; prompt assembly
# can take longer there than a fake Shell's own execution.
def _wait_processing(runtime: Daemon2, path: Path, timeout: float = 30) -> None:
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
    # Scales sized for a loaded CI runner: a 0.3 s TTL lost its lease to
    # scheduler stalls there and the Shell was killed (-15). The rival claim
    # still lands past one full TTL, so only a renewal can block it.
    _sleep_shell(binary, seconds=4)
    event = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                  "Slow task", conversation_key="c")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02, lease_ttl_seconds=1.2)
    output = []
    thread = threading.Thread(target=lambda: output.append(runtime.once()))
    thread.start()
    try:
        _wait_processing(runtime, event)
        time.sleep(2.0)
        assert runtime.letters.claim(event.stem, "rival", 1.2,
                                     now=time.time()) is None
        thread.join(timeout=30)
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
        # The letter turns `processing` before seat.start stamps the run id;
        # reading it in that window returned "" under CI load.
        deadline = time.monotonic() + 30
        while not runtime.seats.read("c").run_id and time.monotonic() < deadline:
            time.sleep(0.02)
        run_id = runtime.seats.read("c").run_id
        assert run_id
        while runner.live_pid_for_label(run_id) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert runner.live_pid_for_label(run_id) is not None
        runtime.leases.clock = lambda: time.time() + 10
        thread.join(timeout=30)
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
    # The spawn letter is still pending. A resident seat sends `to:`;
    # a role-any once() would claim the spawn, and a clone that cannot
    # be placed no longer falls through onto the host checkout.
    result2 = runtime2.once(role="resident")
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

    def until(predicate, timeout: float = 30) -> None:
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
            process.wait(timeout=30)


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


def test_concurrent_gate_delivery_paths_send_one_terminal(tmp_path: Path) -> None:
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
    entered = threading.Event()
    release = threading.Event()
    sent: list[str] = []

    def deliver(_event, body):
        sent.append(body)
        entered.set()
        assert release.wait(30)
        return {"message_id": 77}

    def path():
        gate_runtime.deliver_stream(runtime.door.inbox, runtime.door.responses,
                                    "telegram", deliver)

    first = threading.Thread(target=path)
    second = threading.Thread(target=path)
    try:
        first.start()
        assert entered.wait(15)
        second.start()
        second.join(timeout=15)
        concurrent_path_finished = not second.is_alive()
    finally:
        release.set()
        first.join(timeout=15)
        if second.ident is not None:
            second.join(timeout=15)
        transport.remove(runtime.door.inbox, runtime.door.responses)
        gate_runtime._delivery_retry.clear()
    assert concurrent_path_finished
    assert not first.is_alive() and not second.is_alive()
    assert sent == ["reply"]
    assert protocol._read_event(event_path)["status"] == "delivered"
    facts = runtime.facts.read(
        "sends", "transport:telegram:" + event_path.stem + ":terminal")
    assert sum(f.kind == "sent" for f in facts) == 1


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
    tick = runtime.controls.tick

    def tick_with_card(state):
        card = state["outbox"] / ".card"
        if not card.exists():
            card.write_text("## Now\nworking\n", encoding="utf-8")
        return tick(state)

    runtime.controls.tick = tick_with_card
    sent: list[tuple[str, dict]] = []

    def fake_api(_token, method, _params=None, **_kwargs):
        if method == "getUpdates":
            return {"result": [{"update_id": 1, "message": {
                "message_id": 501, "chat": {"id": 123},
                "from": {"id": 41, "first_name": "Ada"},
                "text": "recorded inbound"}}]}
        sent.append((method, _params or {}))
        return {"ok": True, "result": {"message_id": 77}}

    monkeypatch.setattr(telegram, "_api_call", fake_api)

    def one_gate_turn(gate_brr, inbox, responses):
        telegram._loop_once(gate_brr, inbox, responses)
        deadline = time.monotonic() + 30
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
    deadline = time.monotonic() + 30
    try:
        terminal = lambda: [params for method, params in sent
                            if method == "sendMessage"
                            and params.get("text") == "hello from fake Shell"]
        while not terminal() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(terminal()) == 1
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
        thread.join(timeout=30)
        assert not thread.is_alive()
        # A run card may also use sendMessage; count the terminal reply by
        # its payload so that a card cannot masquerade as a duplicate reply.
        assert len([params for method, params in sent
                    if method == "sendMessage"
                    and params.get("text") == "hello from fake Shell"]) == 1
        assert all(method != "sendMessage" or
                   params.get("text") == "hello from fake Shell" or
                   params.get("parse_mode") == "HTML"
                   for method, params in sent)
        assert sum(method == "sendMessage" and params.get("parse_mode") == "HTML"
                   for method, params in sent) == 1


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
    # once() scanned before this entry existed; serve must start a new scan
    # even when the earlier tick's throttle has not expired.
    runtime._next_schedule_check = time.monotonic() + 60
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


def test_due_schedule_reaches_inbox_while_shell_is_running(tmp_path: Path,
                                                         monkeypatch) -> None:
    from types import SimpleNamespace
    from brr import dominion

    repo, home, dom = tmp_path / "repo", tmp_path / "home", tmp_path / "dominion"
    repo.mkdir()
    dom.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n")
    monkeypatch.setattr(dominion, "resident_dominion_candidates",
                        lambda *_args, **_kwargs: [SimpleNamespace(path=dom)])
    binary = tmp_path / "shell"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,time\nfrom pathlib import Path\n"
        "out=Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "deadline=time.monotonic()+4\n"
        "while time.monotonic()<deadline:\n"
        "    inbox=json.loads((out/'inbox.json').read_text())\n"
        "    if any(e.get('source')=='schedule' for e in inbox.get('events',[])):\n"
        "        (out/'.saw-schedule.marker').write_text('yes')\n"
        "        break\n"
        "    time.sleep(.05)\n"
        "time.sleep(.1)\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    runtime = Daemon2(repo, home, runtime_dir=repo / ".brr",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    protocol.create_event(runtime.door.inbox, "telegram", "wait for schedule",
                          conversation_key="schedule:followup")
    due = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2))
    (dom / "schedule.md").write_text(
        f"## Followup\nat: {due}\ncheck the work\n", encoding="utf-8")
    result = runtime.once()
    assert result is not None
    assert (result.outbox / ".saw-schedule.marker").read_text() == "yes"
    assert any(e.get("source") == "schedule" for e in
               json.loads((result.outbox / "inbox.json").read_text())["events"])


def test_control_card_and_menu_generations_use_retained_mirrors(tmp_path: Path) -> None:
    from brr import menus, run_progress
    from brr.run import Run, run_manifest_path

    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = Daemon2(repo, tmp_path / "home", runtime_dir=repo / ".brr")
    event_path = protocol.create_event(runtime.door.inbox, "telegram", "task",
                                       conversation_key="telegram:owner")
    event = runtime.door.get(event_path.stem)
    outbox = repo / ".brr" / "outbox" / event_path.stem
    outbox.mkdir(parents=True)
    (outbox / ".card").write_text("## Now\nWorking\n")
    (outbox / ".name").write_text("A short name\n")
    (outbox / ".mood").write_text("thinking\n")
    (outbox / ".room").write_text("quiet thread\n")
    (outbox / ".topic").write_text("null\n")
    (outbox / ".topics").write_text("topics: the-clockwork\n")
    (outbox / ".promises.jsonl").write_text('{"what":"commit","count":1}\n')
    (outbox / ".relics.jsonl").write_text('{"kind":"summary","text":"done"}\n')
    (outbox / ".pr").write_text("https://github.com/org/repo/pull/42\n")
    menu = {"menu_id": "choice-1", "thread": "telegram:owner",
            "options": [{"handle": "yes", "label": "Yes"}]}
    (outbox / "menu.json").write_text(json.dumps(menu))
    state = {"event": event, "conversation": "telegram:owner",
             "run_id": "run-control", "outbox": outbox, "is_child": False,
             "notices": []}
    controls = runtime.controls.tick(state)
    assert "## Ledger" in (outbox / ".card").read_text()
    assert controls["name"] == "A short name"
    assert controls["mood"] == "thinking"
    assert controls["room"] == "quiet thread"
    assert controls["topics"] == ["the-clockwork"]
    assert controls["pr"] == "42"
    assert len(controls["promises"]) == len(controls["relics"]) == 1
    assert Run.from_file(run_manifest_path(repo / ".brr" / "runs",
                                                "run-control")) is not None
    assert menus.load_live_menu(repo / ".brr", "telegram:owner")["menu_id"] == "choice-1"
    view = run_progress.project_run(repo / ".brr", "telegram:owner", "run-control")
    assert view is not None and "Working" in (view.agent_card_text or "")
    runtime.controls.tick(state)
    assert state["notices"] == []
    menu["options"][0]["label"] = "Changed"
    (outbox / "menu.json").write_text(json.dumps(menu))
    runtime.controls.tick(state)
    assert any("already used for different content" in row["text"]
               for row in state["notices"])
    runtime.controls.finish(state, 0)
    view = run_progress.project_run(repo / ".brr", "telegram:owner", "run-control")
    assert view is not None and view.state == "succeeded"


def test_await_recall_while_awaiting_rearms_instead_of_refusing(tmp_path: Path) -> None:
    """A lease that returned `pending` is re-called; the seat is still awaiting."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "recall-shell"
    binary.write_text(r"""#!/usr/bin/env python3
import json, os, time
from pathlib import Path
outbox = Path(os.environ["BRR_OUTBOX_DIR"])
portal = Path(os.environ["BRR_PORTAL_STATE"])
event_id = os.environ["BRR_EVENT_ID"]
def stage(name, body):
    (outbox / (name + ".tmp")).write_text(body)
    (outbox / (name + ".tmp")).rename(outbox / name)
def wait_for(pred, what):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = json.loads(portal.read_text())
        if pred(state):
            return state
        time.sleep(0.05)
    raise SystemExit(what)
stage("wait1.md", "---\nawait: true\ntimeout: none\n---\n")
first = wait_for(lambda s: s.get("await", {}).get("armed"), "first never armed")
stage("wait2.md", "---\nawait: true\ntimeout: 1s\n---\n")
gen = first["await"]["generation"]
wait_for(lambda s: s.get("await", {}).get("generation") not in (None, gen)
         or s.get("notices"), "re-call never drained")
wait_for(lambda s: s.get("await", {}).get("resolved") or s.get("notices"),
         "re-armed wait never resolved")
stage("reply.md", "---\nevent: " + event_id + "\n---\nheld twice\n")
""")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    event_path = protocol.create_event(
        home / "dispatch" / "inbox", "telegram", "Wait, re-call, reply",
        conversation_key="telegram:owner", trust_tier="owner")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.returncode == 0 and result.answered
    capsule = json.loads((result.outbox / "portal-state.json").read_text())
    assert not [n for n in capsule["notices"] if "running seat" in n.get("text", "")]
    assert capsule["await"]["resolved"] is True
    assert capsule["await"]["outcome"] == "timeout"
    assert runtime.letters.state(event_path.stem).state == "answered"


def test_claude_runner_gets_the_await_lease_bash_cap(monkeypatch) -> None:
    from types import SimpleNamespace
    from brr import await_verb
    from brr.daemon2.runtime import _await_lease_env
    monkeypatch.delenv("BASH_MAX_TIMEOUT_MS", raising=False)
    claude = SimpleNamespace(name="claude-opus", hooks="claude")
    assert _await_lease_env(claude) == {
        "BRR_RUNNER": "claude",
        "BASH_MAX_TIMEOUT_MS": str(await_verb.CLAUDE_BASH_MAX_TIMEOUT_MS)}
    assert _await_lease_env(SimpleNamespace(name="codex", hooks="codex")) == {
        "BRR_RUNNER": "codex"}
    assert _await_lease_env(SimpleNamespace(name="grok-4", hooks="grok")) == {
        "BRR_RUNNER": "grok"}
    monkeypatch.setenv("BASH_MAX_TIMEOUT_MS", "1000")
    assert _await_lease_env(claude) == {"BRR_RUNNER": "claude"}


def test_a_stamped_daemon2_claude_seat_gets_the_await_rewrite(tmp_path, monkeypatch) -> None:
    """#2194, driven through the hook: the env daemon2 hands a claude seat
    makes the pre-tool phase rewrite ``brnrd await`` to the lease cap."""
    from types import SimpleNamespace
    from brr import await_verb, hooks
    from brr.daemon2.runtime import _await_lease_env
    monkeypatch.delenv("BASH_MAX_TIMEOUT_MS", raising=False)
    env = {**_await_lease_env(SimpleNamespace(name="claude-opus", hooks="claude")),
           "BRR_OUTBOX_DIR": str(tmp_path)}
    ctx = hooks.HookContext(env=env)
    payload = {"tool_name": "Bash",
               "tool_input": {"command": "brnrd await 2>&1 | tail -3"}}
    updated = hooks._await_lease_input(ctx, payload, env)
    assert updated is not None
    assert updated["timeout"] == int(env["BASH_MAX_TIMEOUT_MS"])
    assert updated["command"].startswith(f"export {await_verb.CALL_CAP_ENV}=")


def test_strand_runs_while_its_parent_seat_is_still_alive(tmp_path: Path) -> None:
    """The parent waits for its child's submit before replying: a serial loop deadlocks."""
    from brr.daemon2.runtime import strand_worker_argv, strand_worker_count
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    inbox = home / "dispatch" / "inbox"
    report_path = str(tmp_path / "reports" / "child.md")
    branch = "brr/child-concurrent"
    parent_shell = tmp_path / "parent-shell"
    parent_shell.write_text(f"""#!/usr/bin/env python3
import os, time
from pathlib import Path
outbox = Path(os.environ["BRR_OUTBOX_DIR"])
event_id = os.environ["BRR_EVENT_ID"]
inbox = Path({str(inbox)!r})
def stage(name, body):
    (outbox / (name + ".tmp")).write_text(body)
    (outbox / (name + ".tmp")).rename(outbox / name)
stage("spawn.md", "---\\nspawn: true\\nbranch: {branch}\\nreport: {report_path}\\n---\\nchild work\\n")
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    if any("source: spawn_submitted" in p.read_text() for p in inbox.glob("*.md")):
        break
    time.sleep(0.05)
else:
    raise SystemExit("child never ran while the parent was alive")
stage("reply.md", "---\\nevent: " + event_id + "\\n---\\nchild returned while I waited\\n")
""")
    parent_shell.chmod(parent_shell.stat().st_mode | stat.S_IXUSR)
    child_shell = tmp_path / "child-shell"
    _submit_shell(child_shell, report_path, branch)
    protocol.create_event(inbox, "telegram", "Spawn and wait",
                          conversation_key="telegram:owner", trust_tier="owner",
                          ask_id="ask-main")
    parent = Daemon2(repo, home, runtime_dir=tmp_path / "runtime", runner_name="fake",
                     runner_config={"runner_cmd": [str(parent_shell)]}, tick_seconds=0.02)
    follower = Daemon2(repo, home, runtime_dir=tmp_path / "runtime", runner_name="fake",
                       runner_config={"runner_cmd": [str(child_shell)]},
                       tick_seconds=0.02, worktree_env=False)
    worker = threading.Thread(target=lambda: follower.serve(role="strand"), daemon=True)
    worker.start()
    try:
        result = parent.once(role="resident")
    finally:
        follower.stop()
        worker.join(timeout=10)
    assert result is not None and result.returncode == 0 and result.answered
    # The CLI's follower command line targets the same queues as the resident.
    argv = strand_worker_argv(repo, home, tmp_path / "runtime", inbox,
                              home / "dispatch" / "responses", python="py")
    assert argv[:6] == ["py", "-m", "brr.daemon2", "--serve", "--role", "strand"]
    assert argv[argv.index("--inbox") + 1] == str(inbox)
    assert strand_worker_count({}) == 3
    assert strand_worker_count({"daemon2.strand_workers": 0}) == 0


def test_parent_sees_and_steers_a_child_spawned_under_another_item(tmp_path: Path) -> None:
    """A seat with no ask of its own spawns with `item:`; owned_children and `to:` still find it."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    shell = tmp_path / "parent-shell"
    shell.write_text(r"""#!/usr/bin/env python3
import json, os, time
from pathlib import Path
outbox = Path(os.environ["BRR_OUTBOX_DIR"])
portal = Path(os.environ["BRR_PORTAL_STATE"])
event_id = os.environ["BRR_EVENT_ID"]
def stage(name, body):
    (outbox / (name + ".tmp")).write_text(body)
    (outbox / (name + ".tmp")).rename(outbox / name)
def wait_for(pred, what):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = json.loads(portal.read_text())
        if pred(state):
            return state
        time.sleep(0.05)
    raise SystemExit(what)
stage("spawn.md", "---\nspawn: true\nitem: w-77\nbranch: brr/x\nreport: /tmp/x.md\n---\nwork\n")
state = wait_for(lambda s: s["resources"]["coexisting_runs"].get("owned_children"),
                 "child under item: w-77 never showed as owned")
run = state["resources"]["coexisting_runs"]["owned_children"][0]["run"]
stage("steer.md", "---\nto: " + run + "\n---\nsharper\n")
time.sleep(0.5)
stage("reply.md", "---\nevent: " + event_id + "\n---\nsteered\n")
""")
    shell.chmod(shell.stat().st_mode | stat.S_IXUSR)
    protocol.create_event(home / "dispatch" / "inbox", "telegram", "Spawn under an item",
                          conversation_key="telegram:owner", trust_tier="owner")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime", runner_name="fake",
                      runner_config={"runner_cmd": [str(shell)]}, tick_seconds=0.02)
    result = runtime.once(role="resident")
    assert result is not None and result.returncode == 0 and result.answered
    capsule = json.loads((result.outbox / "portal-state.json").read_text())
    texts = [n.get("text", "") for n in capsule["notices"]]
    assert not [t for t in texts if "fact address" in t or "not a child" in t], texts
    assert runtime.supervisor.conversation_children("telegram:owner")


def test_portal_state_reads_the_door_not_raw_status(tmp_path: Path) -> None:
    """#2187 item 1: a noted sibling leaves ``portal-state.json`` in the same
    tick it leaves ``inbox.json``. The note retires the letter (a fact) and
    leaves the event file's raw ``status:`` alone, so a HUD that rescans raw
    status lists a ghost the door already retired."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    inbox = home / "dispatch" / "inbox"
    waking = protocol.create_event(inbox, "telegram", "First",
                                   conversation_key="telegram:owner", trust_tier="owner")
    sibling = protocol.create_event(inbox, "telegram", "Second",
                                    conversation_key="telegram:owner", trust_tier="owner")
    seen = tmp_path / "seen.json"
    binary = tmp_path / "note-shell"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,time\nfrom pathlib import Path\n"
        "outbox=Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "portal=Path(os.environ['BRR_PORTAL_STATE'])\n"
        "event_id=os.environ['BRR_EVENT_ID']\n"
        f"sibling={sibling.stem!r}\nseen=Path({str(seen)!r})\n"
        "def stage(name,body):\n"
        "    (outbox/(name+'.tmp')).write_text(body); (outbox/(name+'.tmp')).rename(outbox/name)\n"
        "def read(name):\n"
        "    try: return json.loads((outbox/name).read_text())\n"
        "    except Exception: return None\n"
        "deadline=time.monotonic()+10\n"
        "while time.monotonic()<deadline:\n"
        "    s=read('portal-state.json')\n"
        "    if s and s['attention']['pending_event_count']==1: break\n"
        "    time.sleep(.02)\n"
        "else: raise SystemExit('sibling never pending')\n"
        "stage('n.md','---\\nnote: '+sibling+'\\n---\\n')\n"
        "deadline=time.monotonic()+10\n"
        "while time.monotonic()<deadline:\n"
        "    i=read('inbox.json'); s=read('portal-state.json')\n"
        "    if i is not None and not i['events'] and s and s['attention']['pending_event_count']==0:\n"
        "        seen.write_text(json.dumps(s['attention'])); break\n"
        "    time.sleep(.02)\n"
        "else: seen.write_text(json.dumps(read('portal-state.json')['attention']))\n"
        "stage('r.md','---\\nevent: '+event_id+'\\n---\\ndone\\n')\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once()
    assert result is not None and result.answered
    assert runtime.letters.state(sibling.stem).state == "retired"
    attention = json.loads(seen.read_text())
    assert attention["pending_event_count"] == 0, attention
    assert waking.stem != sibling.stem


def test_placement_failure_retires_without_invoking_the_runner(
        tmp_path: Path, monkeypatch) -> None:
    """A clone that already exists is a deterministic failure.

    The old path logged it and started the Shell on the host checkout.
    The claim then expired and the letter was pending again, on the same
    child_run_id, with no backoff. Refuse the Shell, stamp the file off
    pending, and retire while the lease still authorizes — a second
    strand poll finds nothing to run.
    """
    from brr.daemon2.placement import PlacementError

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
    inbox = home / "dispatch" / "inbox"
    child = protocol.create_event(
        inbox, "spawn", "work", conversation_key="c", ask_id="ask-1",
        parent_run_id="run-parent", child_run_id="run-stuck",
        branch="brr/stuck", report=str(tmp_path / "report.md"))
    invoked = tmp_path / "invoked"
    binary = tmp_path / "should-not-run"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        f"from pathlib import Path\nPath({str(invoked)!r}).write_text('ran')\n",
        encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    def boom(*_args, **_kwargs):
        raise PlacementError(
            "failed to allocate worktree for run-stuck: "
            "clone already exists: .brr/worktrees/run-stuck")

    monkeypatch.setattr("brr.daemon2.runtime._placement.allocate", boom)
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    result = runtime.once(role="strand")
    assert result is not None and result.returncode == 1
    assert not invoked.exists()
    assert protocol._read_event(child)["status"] == "noted"
    assert runtime.letters.state(child.stem).state == "retired"
    assert runtime.letters.state(child.stem).retirement["why"] == "strand_exited"
    assert runtime.once(role="strand") is None
    assert all(event["id"] != child.stem for event in runtime.door.pending())


_OWNER_OTHER = "cloud:telegram:155783668:"
_SCHEDULE = "schedule:the-goal-pulse"


def test_visible_set_includes_owner_mail_on_another_conversation(
        tmp_path: Path) -> None:
    """The resident seat owns the owner's other threads. Nothing else."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    inbox = home / "dispatch" / "inbox"
    waking = protocol.create_event(
        inbox, "schedule", "pulse", conversation_key=_SCHEDULE,
        trust_tier="owner")
    owner = protocol.create_event(
        inbox, "cloud", "from telegram", conversation_key=_OWNER_OTHER,
        trust_tier="owner", cloud_platform="telegram",
        cloud_chat_id="155783668", cloud_event_id="remote-b")
    protocol.create_event(
        inbox, "github", "a stranger", conversation_key="github:stranger",
        trust_tier="untrusted")
    protocol.create_event(
        inbox, "cloud", "a collaborator", conversation_key="cloud:slack:other",
        trust_tier="collaborator")
    protocol.create_event(
        inbox, "spawn", "child work", conversation_key="cloud:telegram:else",
        trust_tier="owner", parent_run_id="run-parent", ask_id="ask-1")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": ["true"]})
    seen = runtime._visible(_SCHEDULE, waking.stem)
    assert [event["id"] for event in seen] == [owner.stem]
    assert runtime._visible(_SCHEDULE, waking.stem, is_child=True,
                            run_id="run-child") == []


def test_owner_letter_on_another_conversation_resolves_await_and_replies_there(
        tmp_path: Path) -> None:
    """A schedule seat sees an owner telegram letter arrive, and answers it there."""
    from unittest.mock import MagicMock, patch

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
    inbox = home / "dispatch" / "inbox"
    responses = home / "dispatch" / "responses"
    waking = protocol.create_event(
        inbox, "schedule", "pulse", conversation_key=_SCHEDULE,
        trust_tier="owner")
    diag = tmp_path / "diag.txt"
    binary = tmp_path / "shell"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, time\n"
        "from pathlib import Path\n"
        f"diag = Path({str(diag)!r})\n"
        f"owner_key = { _OWNER_OTHER!r}\n"
        "outbox = Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "portal = Path(os.environ['BRR_PORTAL_STATE'])\n"
        "def stage(name, body):\n"
        "    tmp = outbox / (name + '.tmp')\n"
        "    tmp.write_text(body, encoding='utf-8')\n"
        "    tmp.rename(outbox / name)\n"
        "def read(path):\n"
        "    try:\n"
        "        return json.loads(path.read_text(encoding='utf-8'))\n"
        "    except (OSError, json.JSONDecodeError):\n"
        "        return {}\n"
        "stage('wait.md', '---\\nawait: true\\ntimeout: 20s\\n---\\n')\n"
        "deadline = time.monotonic() + 15\n"
        "target = None\n"
        "last = ''\n"
        "while time.monotonic() < deadline:\n"
        "    inbox_view = read(outbox / 'inbox.json')\n"
        "    state = read(portal)\n"
        "    events = inbox_view.get('events') or []\n"
        "    last = json.dumps({'inbox': events, 'await': state.get('await')})\n"
        "    for ev in events:\n"
        "        if ev.get('conversation_key') != owner_key or ev.get('trust_tier') != 'owner':\n"
        "            diag.write_text('unexpected ' + last, encoding='utf-8')\n"
        "            raise SystemExit('non-owner letter became visible')\n"
        "    waiting = state.get('await') or {}\n"
        "    if (len(events) == 1 and waiting.get('resolved')\n"
        "            and waiting.get('outcome') == 'event'):\n"
        "        target = events[0]['id']\n"
        "        break\n"
        "    time.sleep(0.05)\n"
        "else:\n"
        "    diag.write_text('timed out ' + last, encoding='utf-8')\n"
        "    raise SystemExit('owner letter did not reach inbox and await')\n"
        "stage('reply.md', '---\\nevent: ' + target + '\\n---\\nseen on B\\n')\n",
        encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    runtime_dir = tmp_path / "runtime"
    runtime = Daemon2(repo, home, runtime_dir=runtime_dir,
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    runtime._account_ctx = MagicMock()
    runtime._account_ctx.default_repo.label = "org/repo"
    portal = runtime_dir / "outbox" / waking.stem / "portal-state.json"
    holder: dict[str, object] = {}

    def arrive() -> None:
        try:
            # Prompt assembly runs before the Shell, so the portal appears late.
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if portal.exists():
                    try:
                        state = json.loads(portal.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        time.sleep(0.05)
                        continue
                    if (state.get("await") or {}).get("armed"):
                        break
                time.sleep(0.05)
            else:
                holder["arrive"] = "await never armed"
                return
            stranger = protocol.create_event(
                inbox, "github", "a stranger", conversation_key="github:stranger",
                trust_tier="untrusted")
            holder["stranger"] = stranger
            time.sleep(0.4)
            owner = protocol.create_event(
                inbox, "cloud", "from telegram", conversation_key=_OWNER_OTHER,
                trust_tier="owner", cloud_platform="telegram",
                cloud_chat_id="155783668", cloud_event_id="remote-b")
            holder["owner"] = owner
            holder["arrive"] = "created"
        except Exception as exc:
            holder["arrive"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=arrive, daemon=True)
    thread.start()
    staged: list[dict] = []

    def _fake_stage(_ctx, **kwargs):
        staged.append(kwargs)
        return None

    with patch("brr.daemon2.runtime.message_store.stage", side_effect=_fake_stage):
        result = runtime.once()
    thread.join(timeout=2)
    if result is None or result.returncode != 0:
        detail = diag.read_text(encoding="utf-8") if diag.exists() else ""
        files = []
        for path in inbox.glob("*.md"):
            event = protocol._read_event(path) or {}
            files.append((path.name, event.get("source"), event.get("status"),
                          event.get("conversation_key")))
        raise AssertionError(
            f"seat failed: {detail}\narrive={holder.get('arrive')}\nfiles={files}")
    owner = holder["owner"]
    stranger = holder["stranger"]
    assert protocol.read_response(responses, owner.stem) == "seen on B"
    assert protocol._read_event(owner)["status"] == "done"
    assert runtime.letters.state(owner.stem).state == "answered"
    assert conversations.conversation_key_for_event(
        protocol._read_event(owner)) == _OWNER_OTHER
    replies = [row for row in staged
               if row.get("kind") == "terminal" and row.get("target_event") == owner.stem]
    assert replies, staged
    assert replies[0]["target_thread"] == _OWNER_OTHER
    assert replies[0]["target_gate"] == "cloud"
    assert protocol._read_event(stranger)["status"] == "pending"
    assert protocol.read_response(responses, waking.stem) is None

def test_claude_seat_bundle_declares_web_research_via_daemon2_path(tmp_path: Path) -> None:
    """The bundle's `Web research:` line is rendered from the resolved Shell.

    Regression: daemon2 handed `build_daemon_prompt` only `runner_name`, so the
    Shell resolved to None and a claude seat read "not declared" although
    WebSearch/WebFetch (deferred tools) were available.
    """
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    event_path = protocol.create_event(
        home / "dispatch" / "inbox", "telegram", "Say hello",
        conversation_key="telegram:owner", trust_tier="owner", repo_label="org/a")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="claude", runner_config={"runner_cmd": [str(binary)]})
    result = runtime.once()
    assert result is not None and result.answered
    context = (tmp_path / "runtime" / "runs" / result.run_id / "context.md").read_text()
    line = next(l for l in context.splitlines() if l.startswith("- Web research:"))
    assert "not declared" not in line
    assert "WebSearch/WebFetch" in line
    assert "ToolSearch" in line and "select:WebSearch,WebFetch" in line


def _bundle_inbox_ids(prompt: str) -> list[str]:
    """Event ids rendered under ``### Inbox — other pending events``."""
    marker = "### Inbox — other pending events"
    if marker not in prompt:
        return []
    section = prompt.split(marker, 1)[1].split("\n### ", 1)[0]
    ids = []
    for line in section.splitlines():
        if not line.startswith("- "):
            continue
        token = line[2:].split(" ", 1)[0]
        if token.startswith("evt-"):
            ids.append(token)
    return ids


def test_bundle_inbox_matches_visible_projection_not_raw_files(tmp_path: Path) -> None:
    """Sibling letters the facts already closed stay out of the bundle.

    Their event files still say ``pending``. The bundle used to render
    every ``door.pending()`` row, including letters this seat cannot see
    and letters the facts have answered or retired. ``inbox.json`` is
    built from ``_visible``; the bundle has to be that same list.
    """
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    inbox = home / "dispatch" / "inbox"
    conversation = "telegram:owner"
    waking = protocol.create_event(
        inbox, "telegram", "the letter this seat woke on",
        conversation_key=conversation, trust_tier="owner")
    still_open = protocol.create_event(
        inbox, "telegram", "still waiting on this thread",
        conversation_key=conversation, trust_tier="owner")
    answered = protocol.create_event(
        inbox, "telegram", "already answered",
        conversation_key=conversation, trust_tier="owner")
    retired = protocol.create_event(
        inbox, "schedule", "already retired",
        conversation_key=conversation, trust_tier="owner",
        schedule_id="pulse")
    elsewhere = protocol.create_event(
        inbox, "schedule", "another thread",
        conversation_key="schedule:other", trust_tier="collaborator",
        schedule_id="other")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]})
    for event_id, kind in ((answered.stem, "answered"), (retired.stem, "retired")):
        runtime.facts.record("letters", event_id, "pending", "test")
        runtime.facts.record("letters", event_id, kind, "test", {"why": "done"})
    result = runtime.once()
    assert result is not None and result.answered
    assert result.event_id == waking.stem
    for path in (answered, retired, elsewhere, still_open):
        assert protocol._read_event(path)["status"] == "pending"
    assert runtime.letters.state(answered.stem).state == "answered"
    assert runtime.letters.state(retired.stem).state == "retired"
    context = (tmp_path / "runtime" / "runs" / result.run_id / "context.md").read_text()
    listed = _bundle_inbox_ids(context)
    inbox_ids = [row["id"] for row in json.loads(
        (result.outbox / "inbox.json").read_text())["events"]]
    assert listed == inbox_ids
    assert still_open.stem in listed
    assert answered.stem not in listed
    assert retired.stem not in listed
    assert elsewhere.stem not in listed
    assert waking.stem not in listed


def test_recovery_checkpoint_names_previous_run_and_delivered_replies(
        tmp_path: Path) -> None:
    """A dead seat's re-dispatch states the receipts the daemon already holds."""
    from brr import daemon as legacy_daemon, message_store

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n", encoding="utf-8")
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text(
        f"home.path={home}\nrepo.label=org/repo\n", encoding="utf-8")
    binary = tmp_path / "fake-shell"
    _fake_shell(binary)
    conversation = "telegram:owner"
    event = protocol.create_event(
        home / "dispatch" / "inbox", "telegram", "pick up where you stopped",
        conversation_key=conversation, trust_tier="owner", repo_label="org/repo")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]})
    seat = Seat(runtime.seats, conversation)
    started = seat.start(0, run_id="run-dead")
    assert started.state == "running"
    assert started.run_id == "run-dead"
    label = legacy_daemon._repo_label(
        repo, protocol._read_event(event), runtime._config)
    directory = message_store.run_messages_dir(runtime._account_ctx, label, "run-dead")
    directory.mkdir(parents=True)

    def _message(name: str, *, status: str, thread: str, delivered_at: str) -> None:
        (directory / name).write_text(
            "---\n"
            "direction: out\n"
            f"status: {status}\n"
            f"target_thread: {thread}\n"
            f"delivered_at: {delivered_at}\n"
            "---\n\n"
            "body\n",
            encoding="utf-8",
        )

    _message("0001.md", status="delivered", thread=conversation,
             delivered_at="2026-10-07T11:00:00+00:00")
    _message("0002.md", status="delivered", thread=conversation,
             delivered_at="2026-10-07T12:00:00+00:00")
    _message("0003.md", status="delivered", thread="schedule:other",
             delivered_at="2026-10-07T13:00:00+00:00")
    _message("0004.md", status="pending", thread=conversation,
             delivered_at="2026-10-07T14:00:00+00:00")
    result = runtime.once()
    assert result is not None and result.answered
    context = (tmp_path / "runtime" / "runs" / result.run_id / "context.md").read_text()
    marker = "Recovery checkpoint (previous Shell stopped):\n"
    assert context.count(marker) == 1
    payload = json.loads(context.split(marker, 1)[1].splitlines()[0])
    assert payload["previous_run_id"] == "run-dead"
    assert payload["replies_delivered"] == 2
    assert payload["last_reply_at"] == "2026-10-07T12:00:00+00:00"


def test_dispatch_stamp_lands_when_the_projected_status_differs_from_the_file(
        tmp_path: Path) -> None:
    """2026-10-07 (evt-…-v2l4): after a restart, a letter still ``claimed``
    by the dead seat projects as ``processing`` over a file reading
    ``pending``. ``protocol.set_status`` keys its replace on the dict's
    status, so the dispatch stamp matched nothing, the gate (which sweeps
    raw files) never saw an active event, and the seat's interim messages
    sat undelivered. ``FileDoor.stamp`` re-reads the file first."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    path = protocol.create_event(home / "dispatch" / "inbox", "telegram",
                                 "task", conversation_key="c")
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": ["true"]})
    runtime.letters.ingest(path.stem, "pending")
    assert runtime.letters.claim(path.stem, "run-dead", 60, now=time.time())
    projected = runtime.door.get(path.stem)
    assert projected["status"] == "processing"
    assert protocol._read_event(path)["status"] == "pending"

    protocol.set_status(dict(projected), "processing")  # the old call
    assert protocol._read_event(path)["status"] == "pending"

    runtime.door.stamp(projected, "processing")
    assert protocol._read_event(path)["status"] == "processing"
    runtime.door.stamp(projected, "noted")
    assert protocol._read_event(path)["status"] == "noted"
