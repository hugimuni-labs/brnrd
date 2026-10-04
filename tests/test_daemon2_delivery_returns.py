"""Live dispatch regressions: a held body must receive messages and returns."""
from __future__ import annotations

import json
from pathlib import Path
import stat
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from brr import message_store, protocol, runner
from brr.daemon2.runtime import Daemon2
from brr.gates import cloud, runtime as gate_runtime
from test_daemon2_placement import _make_git_repo


@pytest.fixture(autouse=True)
def _no_provider_probes(monkeypatch):
    # Delivery and return contracts need no provider account or live quota.
    monkeypatch.setattr("brr.daemon._collect_levels",
                        lambda *_args, **_kwargs: ({}, frozenset()))


_PRELUDE = '''import json, os, time
from pathlib import Path
outbox = Path(os.environ["BRR_OUTBOX_DIR"])
portal = Path(os.environ["BRR_PORTAL_STATE"])
event_id = os.environ["BRR_EVENT_ID"]
def stage(name, body):
    tmp = outbox / (name + ".tmp")
    tmp.write_text(body)
    tmp.rename(outbox / name)
def wait_for(predicate, label):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise SystemExit(label)
'''


def _shell(path: Path, code: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + _PRELUDE + code)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _runtime(repo, home, binary, **kwargs):
    return Daemon2(repo, home, runtime_dir=repo / ".brr", runner_name="fake",
                   runner_config={"runner_cmd": [str(binary)]}, tick_seconds=.02,
                   **kwargs)


def test_live_seat_delivers_interim_thread_gate_and_terminal_through_fence(
        tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    (repo / "AGENTS.md").write_text("# test\n")
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text(f"home.path={home}\nrepo.label=org/repo\n")
    key = "cloud:telegram:123:"
    sent_dir = tmp_path / "sent"
    sent_dir.mkdir()
    binary = _shell(tmp_path / "seat", f'''
sent = Path({str(sent_dir)!r})
stage("wait.md", "---\\nawait: true\\ntimeout: none\\n---\\n")
wait_for(lambda: json.loads(portal.read_text()).get("await", {{}}).get("armed"), "await not armed")
stage("spawn.md", "---\\nspawn: true\\nbranch: brr/message-child\\nreport: child.md\\n---\\nwait for delivery")
for name, text in [("bare", "bare message"),
                   ("thread", "---\\nthread: {key}\\n---\\nthread message"),
                   ("gate", "---\\ngate: cloud\\n---\\ngate message")]:
    stage(name + ".md", text)
    wait_for(lambda: (sent / name).exists(), name + " never delivered while awaiting")
wait_for(lambda: json.loads(portal.read_text()).get("await", {{}}).get("resolved"), "child did not return")
print("terminal message")
''')
    runtime = _runtime(repo, home, binary)
    runtime._gate_available = lambda gate: gate == "cloud"
    protocol.create_event(runtime.door.inbox, "cloud", "work", trust_tier="owner",
                          cloud_event_id="cloud-123", cloud_platform="telegram",
                          cloud_chat_id=123, conversation_key=key, repo_label="org/repo",
                          ask_id="ask-live")
    posts = []
    def request(_url, _method, endpoint, **kwargs):
        payload = kwargs["json"]
        body = payload["body_markdown"]
        posts.append((endpoint, payload))
        for name in ("bare", "thread", "gate", "terminal"):
            if body == name + " message":
                (sent_dir / name).touch()
                if name == "terminal":
                    runtime.stop()
        return {"message_id": len(posts)}
    monkeypatch.setattr(cloud, "_request", request)
    gate_stop = threading.Event()
    gate_thread = None
    def start_gates(*_args):
        nonlocal gate_thread
        def deliver():
            while not gate_stop.wait(.02):
                cloud._deliver_responses(repo / ".brr", runtime.door.inbox,
                                         runtime.door.responses,
                                         {"brnrd_url": "https://test.invalid", "token": "test"})
        gate_thread = threading.Thread(target=deliver, daemon=True)
        gate_thread.start()
    monkeypatch.setattr("brr.daemon._start_account_gates", start_gates)
    child_shell = _shell(tmp_path / "child-seat", f'''
sent = Path({str(sent_dir)!r})
wait_for(lambda: (sent / "gate").exists(), "parent never delivered")
''')
    follower = _runtime(repo, home, child_shell, worktree_env=False)
    child_worker = threading.Thread(target=lambda: follower.serve(role="strand"), daemon=True)
    worker = threading.Thread(target=lambda: runtime.serve(role="resident"), daemon=True)
    worker.start()
    child_worker.start()
    try:
        deadline = time.monotonic() + 25
        while not (sent_dir / "terminal").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (sent_dir / "terminal").exists()
        deadline = time.monotonic() + 5
        while any(m["status"] != "delivered" for path in
                  runtime._account_ctx.runs_dir.glob("*/*/messages/*.md")
                  if (m := message_store.read(path))) and time.monotonic() < deadline:
            time.sleep(.02)
        rows = list(runtime._account_ctx.runs_dir.glob("*/*/messages/*.md"))
        messages = [message_store.read(path) for path in rows]
        assert {m["body"] for m in messages} == {
            "bare message", "thread message", "gate message", "terminal message"}
        assert all(m["status"] == "delivered" for m in messages)
        assert sorted(m["kind"] for m in messages) == ["interim", "outbound", "outbound", "terminal"]
        assert [payload["body_markdown"] for _, payload in posts] == [
            "bare message", "thread message", "gate message", "terminal message"]
        assert [p["status"] for _, p in posts[:2]] == ["processing", "done"]
        assert all(p.get("event_id") == "cloud-123" for _, p in posts[:2])
        sent_facts = [f for entity in runtime.facts.entities("sends")
                      if entity.startswith("transport:") for f in runtime.facts.read("sends", entity)
                      if f.kind == "sent"]
        assert len(sent_facts) == 4
        assert all(f.data["gen"] >= 1 for f in sent_facts)
        assert next(iter(runtime.supervisor.children("ask-live").values())).status == "done"
        completed = [event for event in runtime.door.pending()
                     if event["source"] == "spawn_completed"]
        assert len(completed) == 1
        assert completed[0]["spawn_branch"] == "brr/message-child"
        assert not completed[0].get("spawn_published_branch")  # no allocated git tree
    finally:
        runtime.stop()
        follower.stop()
        worker.join(20)
        child_worker.join(20)
        gate_stop.set()
        if gate_thread:
            gate_thread.join(5)
        assert not worker.is_alive()
        assert not child_worker.is_alive()
        gate_runtime._delivery_retry.clear()


@pytest.mark.parametrize("ending", ["submit", "done", "error", "crash", "signal"])
def test_concurrent_parent_await_receives_relative_report_and_child_exit(
        tmp_path: Path, monkeypatch, ending: str) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    # These identity variables are inherited by the real child git commands.
    for name, value in {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
                        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}.items():
        monkeypatch.setenv(name, value)
    captured = tmp_path / "received.json"
    report = "reports/child.md"
    branch = "brr/delivery-child"
    expected_source = "spawn_submitted" if ending == "submit" else "spawn_completed"
    parent_shell = _shell(tmp_path / "parent", f'''
stage("wait.md", "---\\nawait: true\\ntimeout: none\\n---\\n")
wait_for(lambda: json.loads(portal.read_text()).get("await", {{}}).get("armed"), "await not armed")
stage("spawn.md", "---\\nspawn: true\\nbranch: {branch}\\nreport: {report}\\n---\\nchild work")
wait_for(lambda: json.loads(portal.read_text()).get("await", {{}}).get("resolved"), "child never woke parent")
state = json.loads(portal.read_text())
Path({str(captured)!r}).write_text(json.dumps(state))
stage("reply.md", "---\\nevent: " + event_id + "\\n---\\nchild returned")
''')
    child_shell = _shell(tmp_path / "child", f'''
import subprocess
subprocess.run(["git", "switch", "-c", {branch!r}], check=True)
report = Path({report!r})
report.parent.mkdir(parents=True)
report.write_text("child report")
subprocess.run(["git", "add", str(report)], check=True)
subprocess.run(["git", "commit", "-m", "child report"], check=True)
''' + ('stage("submit.md", "---\\nsubmit: true\\n---\\nready")\n'
       if ending == "submit" else "raise SystemExit(7)\n" if ending == "error" else
       "import signal\nos.kill(os.getpid(), signal.SIGKILL)\n" if ending == "signal" else ""))
    parent = _runtime(repo, home, parent_shell)
    follower = _runtime(repo, home, child_shell)
    if ending == "crash":
        invoke = runner.invoke_runner
        def fail_child(profile, invocation, config):
            if invocation.kind == "strand":
                raise RuntimeError("runner invocation crashed")
            return invoke(profile, invocation, config)
        monkeypatch.setattr(runner, "invoke_runner", fail_child)
    protocol.create_event(parent.door.inbox, "telegram", "spawn and await",
                          conversation_key="telegram:owner", trust_tier="owner", ask_id="ask-main")
    failures = []
    def run_child():
        try:
            follower.serve(role="strand")
        except RuntimeError as exc:
            failures.append(str(exc))
    worker = threading.Thread(target=run_child, daemon=True)
    worker.start()
    try:
        result = parent.once(role="resident")
    finally:
        follower.stop()
        worker.join(20)
    assert not worker.is_alive()
    assert result and result.returncode == 0 and result.answered
    state = json.loads(captured.read_text())
    assert state["await"]["outcome"] == "event"
    returns = [e for e in state["inbound"]["events"] if e["source"] == expected_source]
    assert len(returns) == 1
    returned = returns[0]
    assert returned["spawn_parent_run_id"] == result.run_id
    if ending == "crash":
        assert returned["spawn_published_branch"].startswith("brr/run-")
    else:
        assert returned["spawn_published_branch"] == branch
    assert returned["spawn_report_path"] == report
    child = next(iter(parent.supervisor.children("ask-main").values()))
    status = "crash" if ending == "signal" else ending
    assert child.status == ("returned" if ending == "submit" else status)
    if ending == "submit":
        assert not [e for e in follower.door.pending() if e["source"] == "spawn_completed"]
    else:
        assert returned["spawn_status"] == status
    if ending == "crash":
        assert failures == ["runner invocation crashed"]
    else:
        # The relative report was authored in the allocated clone, then landed
        # on the host branch; it never existed at the daemon's cwd.
        import subprocess
        from test_daemon2_placement import _git_env
        content = subprocess.run(["git", "-C", str(repo), "show", branch + ":" + report],
                                 env=_git_env(), check=True, capture_output=True, text=True)
        assert content.stdout == "child report"


def test_overdue_delivery_notice_survives_restart_and_finished_producer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text(f"home.path={home}\nrepo.label=org/repo\n")
    binary = _shell(tmp_path / "shell", 'print("terminal")\n')
    runtime = _runtime(repo, home, binary)
    event = protocol.create_event(runtime.door.inbox, "telegram", "work",
                                  conversation_key="telegram:owner", repo_label="org/repo")
    result = runtime.once()
    assert result is not None
    messages_dir = message_store.run_messages_dir(runtime._account_ctx, "org/repo", result.run_id)
    rows = message_store.list_messages(messages_dir)
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    old = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    protocol.update_event_meta({"_path": rows[0]["_path"]}, created_at=old)
    delivered = message_store.stage(runtime._account_ctx, repo_label="org/repo", run_id=result.run_id,
                                   body="already delivered", kind="outbound", created_at=old)
    message_store.transition(delivered, "delivered", gate="telegram")
    runtime._check_delivery()
    notice_path = result.outbox / ".notices.jsonl"
    notices = [json.loads(line) for line in notice_path.read_text().splitlines()]
    assert len(notices) == 1 and notices[0]["verb"] == "delivery"
    assert "undelivered for more than a minute" in notices[0]["text"]
    restarted = _runtime(repo, home, binary)
    restarted._check_delivery()
    assert len(notice_path.read_text().splitlines()) == 1
    message_store.stage(runtime._account_ctx, repo_label="org/repo", run_id="run-orphan",
                        body="no surviving manifest or carrier", kind="outbound", created_at=old)
    restarted._check_delivery()
    orphan_notices = repo / ".brr" / "outbox" / "delivery" / ".notices.jsonl"
    assert "run-orphan" in orphan_notices.read_text()
