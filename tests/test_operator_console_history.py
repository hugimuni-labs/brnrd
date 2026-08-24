from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from brr import presence
from brr.operator_console import history
from brr.operator_console.history_tui import _attention, _awaiting, _body


def _write_run(brr: Path, run_id: str, *, status: str = "done", event_id: str | None = None) -> Path:
    event_id = event_id or f"evt-{run_id}"
    run_dir = brr / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.md").write_text(
        "---\n"
        f"id: {run_id}\n"
        f"event_id: {event_id}\n"
        "env: worktree\n"
        f"status: {status}\n"
        "source: cloud\n"
        "conversation_key: cloud:telegram:42:\n"
        "runner_name: codex-full\n"
        "runner_shell: codex\n"
        "runner_core: gpt-test\n"
        "---\n"
        "inspect history\n",
        encoding="utf-8",
    )
    (run_dir / "prompt.md").write_text(f"wake for {run_id}", encoding="utf-8")
    (run_dir / "boundaries.jsonl").write_text(
        json.dumps({
            "phase": "PostToolUse",
            "at": "2026-08-23T17:30:00Z",
            "act": "orient",
            "tools": ["Bash"],
            "detail": "git status --short",
            "out_bytes": 12,
            "inject": "",
        }) + "\n",
        encoding="utf-8",
    )
    return run_dir


def _set_status(run_dir: Path, status: str) -> None:
    text = (run_dir / "run.md").read_text(encoding="utf-8")
    text = text.replace("status: running", f"status: {status}")
    (run_dir / "run.md").write_text(text, encoding="utf-8")


def test_completed_run_stays_selected_after_presence_disappears(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    run_id = "run-260823-1511-uvaf"
    run_dir = _write_run(brr, run_id, status="running")

    entry = presence.register(
        brr,
        kind="daemon",
        run_id=run_id,
        repo_label="hugimuni-labs/brnrd",
        stream="cloud:telegram:42:",
        pid=os.getpid(),
        runner_name="codex-full",
        runner_shell="codex",
        runner_core="gpt-test",
    )
    live_snapshot = history.collect_snapshot(repo, brr_dir=brr)
    assert live_snapshot.selected_run_id == run_id
    assert live_snapshot.selected is not None
    assert not live_snapshot.selected.kind.startswith("history")

    _set_status(run_dir, "done")
    presence.deregister(brr, entry["id"])

    postmortem = history.collect_snapshot(repo, brr_dir=brr, selected_run_id=run_id)
    assert postmortem.selected_run_id == run_id
    assert postmortem.selected is not None
    assert postmortem.selected.kind == "history"
    assert postmortem.selected.prompt == f"wake for {run_id}"
    assert postmortem.selected.boundaries[0].detail == "git status --short"
    assert postmortem.selected.boot["wake"]["present"] is True
    assert postmortem.selected.manifest["status"] == "done"


def test_live_resident_remains_default_over_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    old_id = "run-260823-1511-oldx"
    live_id = "run-260823-1600-live"
    _write_run(brr, old_id, status="done")
    _write_run(brr, live_id, status="running")
    presence.register(
        brr,
        kind="daemon",
        run_id=live_id,
        repo_label="hugimuni-labs/brnrd",
        stream="cloud:telegram:42:",
        pid=os.getpid(),
        runner_name="codex-full",
        runner_shell="codex",
        runner_core="gpt-test",
    )

    snapshot = history.collect_snapshot(repo, brr_dir=brr)
    assert snapshot.selected_run_id == live_id
    assert snapshot.runs[0].run_id == live_id
    assert any(run.run_id == old_id and run.kind == "history" for run in snapshot.runs)

    explicit = history.collect_snapshot(repo, brr_dir=brr, selected_run_id=old_id)
    assert explicit.selected_run_id == old_id
    assert explicit.selected is not None
    assert explicit.selected.kind == "history"
    assert explicit.selected.prompt == f"wake for {old_id}"


def test_home_only_run_supplies_body_and_receipted_thread(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    home_runs = tmp_path / "home" / "runs"
    ctx = SimpleNamespace(runs_dir=home_runs)
    repo_label = "hugimuni-labs/brnrd"
    run_id = "run-260823-1400-home"
    node = home_runs / "hugimuni-labs__brnrd" / run_id
    messages = node / "messages"
    messages.mkdir(parents=True)
    (node / "state.md").write_text(
        "---\n"
        f"run_id: {run_id}\n"
        "status: done\n"
        "started_at: 2026-08-23T14:00:00Z\n"
        "ended_at: 2026-08-23T14:12:00Z\n"
        "source: cloud\n"
        "runner_name: codex-full\n"
        "runner_shell: codex\n"
        "runner_core: gpt-test\n"
        "event_id: evt-home\n"
        "conversation_key: cloud:telegram:42:\n"
        "---\n",
        encoding="utf-8",
    )
    (node / "body.md").write_text(
        "## NOW\nConverged.\n\n## COURSE\n- [x] inspect\n", encoding="utf-8"
    )
    (messages / "000001-terminal.md").write_text(
        "---\n"
        "direction: out\n"
        "status: delivered\n"
        "created_at: 2026-08-23T14:11:00+00:00\n"
        "delivered_at: 2026-08-23T14:12:00+00:00\n"
        "kind: terminal\n"
        "---\n\nDone from the durable message store.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(history, "_resolve_existing_home", lambda _repo: (ctx, repo_label))

    snapshot = history.collect_snapshot(repo, brr_dir=brr, selected_run_id=run_id)
    run = snapshot.selected
    assert run is not None
    assert run.kind == "history"
    assert "Converged" in run.card
    assert run.manifest["_body_source"] == "home body.md"
    assert run.manifest["_storage"] == "home-node"
    assert run.thread[-1]["body"] == "Done from the durable message store."
    assert run.prompt == ""

    attention = _attention(run)
    assert "HISTORY · done" in attention
    assert "awake" not in attention
    assert "home body.md" in _body(run)
    assert _awaiting(run) is None


def test_history_resolution_never_creates_a_home(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    missing_home = tmp_path / "must-not-be-created"
    seen: list[bool] = []
    fake_ctx = SimpleNamespace(home_root=missing_home, dominion_repo=missing_home)

    monkeypatch.setattr(history.conf, "load_config", lambda _repo: {})
    monkeypatch.setattr(history.account, "repo_label", lambda _repo, _cfg: "org/repo")
    monkeypatch.setattr(
        history.account,
        "resolve_context",
        lambda _repo, _cfg, *, create=True: (seen.append(create) or fake_ctx),
    )
    monkeypatch.setattr(history.account, "context_home_root", lambda ctx: ctx.home_root)

    assert history._resolve_existing_home(repo) is None
    assert seen == [False]
    assert not missing_home.exists()
