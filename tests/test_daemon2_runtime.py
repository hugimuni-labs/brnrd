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
                            tick_seconds=0.02)
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
        f"stage('to.md', '---\\nto: {edge}\\n---\\nHere is the steer.\\n')\n"
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
    with _patch("brr.daemon2.runtime.message_store.stage", side_effect=_fake_stage):
        result = runtime.once()
    assert result is not None and result.answered
    gates = [s for s in staged if s["gate"] == "telegram"]
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
