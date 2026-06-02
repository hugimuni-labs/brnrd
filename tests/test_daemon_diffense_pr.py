"""Tests for the publish kernel's diffense PR step (``_maybe_open_pr``).

Slice 3 of Thread D: after a clean push, the daemon opens or refreshes the
change's PR with the diffense pack projected into the body. The decision
to create vs. refresh rides on whether an open PR already heads this
branch — divergence is handled upstream by the push step, never here.

These unit tests fake ``gh`` (via ``subprocess.run``) and the git remote
plumbing, so they assert the create/refresh branching and the best-effort
no-ops without a live GitHub or network.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

from brr import daemon
from brr.task import Task


def _write_pack(brr_dir: Path, task_id: str) -> Path:
    d = brr_dir / "diffense" / task_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / "pack.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1-test",
                "metadata": {},
                "reading_order": ["summary:x"],
                "cards": [
                    {
                        "id": "summary:x",
                        "kind": "summary",
                        "identity": {"label": "the change in shape"},
                        "lore": {"descriptive": "a small honest change"},
                        "provenance": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _fake_gh(calls: list, *, existing: int | None = None,
             url: str = "https://github.com/o/r/pull/7"):
    """A fake ``subprocess.run`` that records calls and impersonates gh."""

    def run(cmd, **kwargs):
        calls.append({"cmd": list(cmd), "input": kwargs.get("input")})
        head = cmd[:3]
        if head == ["gh", "pr", "list"]:
            rows = [{"number": existing}] if existing is not None else []
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")
        if head in (["gh", "pr", "create"], ["gh", "pr", "edit"], ["gh", "pr", "view"]):
            return types.SimpleNamespace(returncode=0, stdout=url + "\n", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="unexpected gh call")

    return run


def _explode(*_a, **_kw):
    raise AssertionError("subprocess.run should not be called")


def _github_remote(monkeypatch):
    monkeypatch.setattr(daemon.gitops, "remote_url", lambda *_a: "git@github.com:o/r.git")
    monkeypatch.setattr(daemon.gitops, "default_branch", lambda *_a: "main")


def test_creates_pr_when_no_open_pr(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = Task(id="task-1", event_id="evt-1", body="x", status="done")
    _write_pack(brr_dir, task.id)
    _github_remote(monkeypatch)
    calls: list = []
    monkeypatch.setattr(daemon.subprocess, "run", _fake_gh(calls, existing=None))

    url = daemon._maybe_open_pr(tmp_path, task, brr_dir, {}, "origin", "brr/feat-x")

    assert url == "https://github.com/o/r/pull/7"
    create = next(c for c in calls if c["cmd"][:3] == ["gh", "pr", "create"])
    assert "--head" in create["cmd"] and "brr/feat-x" in create["cmd"]
    assert "--base" in create["cmd"] and "main" in create["cmd"]
    # The PR body is the pack projection, with the pack embedded so it
    # travels with the PR.
    assert "## Summary" in create["input"]
    assert "diffense:pack:v1" in create["input"]
    assert not any(c["cmd"][:3] == ["gh", "pr", "edit"] for c in calls)


def test_refreshes_body_when_open_pr_exists(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = Task(id="task-2", event_id="evt-2", body="x", status="done")
    _write_pack(brr_dir, task.id)
    _github_remote(monkeypatch)
    calls: list = []
    monkeypatch.setattr(daemon.subprocess, "run", _fake_gh(calls, existing=12))

    url = daemon._maybe_open_pr(tmp_path, task, brr_dir, {}, "origin", "feat/foo")

    assert url == "https://github.com/o/r/pull/7"
    edit = next(c for c in calls if c["cmd"][:3] == ["gh", "pr", "edit"])
    assert "12" in edit["cmd"]
    assert "## Summary" in edit["input"]
    assert not any(c["cmd"][:3] == ["gh", "pr", "create"] for c in calls)


def test_create_includes_brnrd_render_link_in_managed_mode(tmp_path, monkeypatch):
    from brr.gates import cloud

    brr_dir = tmp_path / ".brr"
    task = Task(id="task-8", event_id="evt-8", body="x", status="done")
    _write_pack(brr_dir, task.id)
    _github_remote(monkeypatch)
    # Managed mode: the pack is relayed to brnrd and the link rides the body.
    monkeypatch.setattr(cloud, "is_configured", lambda _b: True)
    monkeypatch.setattr(cloud, "relay_pack", lambda _b, _p: "https://brnrd.example/r/tok")
    calls: list = []
    monkeypatch.setattr(daemon.subprocess, "run", _fake_gh(calls, existing=None))

    url = daemon._maybe_open_pr(tmp_path, task, brr_dir, {}, "origin", "brr/feat-x")

    assert url == "https://github.com/o/r/pull/7"
    create = next(c for c in calls if c["cmd"][:3] == ["gh", "pr", "create"])
    assert "https://brnrd.example/r/tok" in create["input"]
    assert "Interactive review" in create["input"]


def test_noop_when_create_pr_disabled(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = Task(id="task-3", event_id="evt-3", body="x", status="done")
    _write_pack(brr_dir, task.id)
    monkeypatch.setattr(daemon.subprocess, "run", _explode)

    url = daemon._maybe_open_pr(
        tmp_path, task, brr_dir, {"diffense.create_pr": False}, "origin", "b",
    )
    assert url is None


def test_noop_without_pack(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = Task(id="task-4", event_id="evt-4", body="x", status="done")
    monkeypatch.setattr(daemon.subprocess, "run", _explode)

    url = daemon._maybe_open_pr(tmp_path, task, brr_dir, {}, "origin", "b")
    assert url is None


def test_skips_non_github_remote(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = Task(id="task-5", event_id="evt-5", body="x", status="done")
    _write_pack(brr_dir, task.id)
    monkeypatch.setattr(daemon.gitops, "remote_url", lambda *_a: "git@gitlab.com:o/r.git")
    monkeypatch.setattr(daemon.subprocess, "run", _explode)

    url = daemon._maybe_open_pr(tmp_path, task, brr_dir, {}, "origin", "b")
    assert url is None


def test_skips_when_head_is_base(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = Task(id="task-6", event_id="evt-6", body="x", status="done")
    _write_pack(brr_dir, task.id)
    _github_remote(monkeypatch)
    monkeypatch.setattr(daemon.subprocess, "run", _explode)

    # Work landed on the default branch -> nothing to PR.
    url = daemon._maybe_open_pr(tmp_path, task, brr_dir, {}, "origin", "main")
    assert url is None


def test_best_effort_when_gh_missing(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = Task(id="task-7", event_id="evt-7", body="x", status="done")
    _write_pack(brr_dir, task.id)
    _github_remote(monkeypatch)

    def _missing(*_a, **_kw):
        raise FileNotFoundError("gh not installed")

    monkeypatch.setattr(daemon.subprocess, "run", _missing)

    # gh absent must never fail the task — the branch is published anyway.
    url = daemon._maybe_open_pr(tmp_path, task, brr_dir, {}, "origin", "brr/feat-x")
    assert url is None
