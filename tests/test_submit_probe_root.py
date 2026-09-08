"""`submit: true` probes the strand's *own* git root for its branch.

2026-09-08, run-260908-1048-dcwv: a host-env strand (its own
``git clone --shared``) pushed its branch, opened its PR, and was refused
``submit refused: missing published branch`` three times — the daemon
rev-parsed the branch in the parent checkout, where a clone's local branch
never exists. These drive `_queue_submit_request` through real git repos.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import daemon
from brr.run import Run


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repos(tmp_path: Path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    host = tmp_path / "host"
    _git(tmp_path, "clone", "-q", str(origin), str(host))
    _git(host, "config", "user.email", "t@t")
    _git(host, "config", "user.name", "t")
    (host / "a.txt").write_text("a\n")
    _git(host, "add", "a.txt")
    _git(host, "commit", "-q", "-m", "seed")
    _git(host, "push", "-q", "origin", "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--shared", str(host), str(clone))
    _git(clone, "remote", "set-url", "origin", str(origin))
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    _git(clone, "switch", "-q", "-c", "brr/strand-work")
    (clone / "b.txt").write_text("b\n")
    _git(clone, "add", "b.txt")
    _git(clone, "commit", "-q", "-m", "strand work")
    return host, clone


@pytest.fixture(autouse=True)
def _controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _strand(clone: Path, report: Path) -> Run:
    task = Run(id="run-child", event_id="evt-child", body="", env="worktree")
    task.meta.update({
        "strand": True,
        "spawn_contract_branch": "brr/strand-work",
        "spawn_contract_report": str(report),
        "spawn_parent_run_id": "run-parent",
        "worktree_path": str(clone),
        "worktree_kind": "clone",
        "seed_ref": "main",
    })
    daemon._register_run_control("evt-child", "run-parent")
    daemon._bind_run_control("evt-child", "run-child")
    return task


def _notices(outbox: Path) -> list[str]:
    state = outbox / "portal-state.json"
    if not state.exists():
        return []
    import json
    return [n.get("text", "") for n in json.loads(state.read_text()).get("notices", [])]


def test_pushed_clone_branch_submits_even_though_the_parent_never_saw_it(
    repos, tmp_path, monkeypatch,
):
    host, clone = repos
    _git(clone, "push", "-q", "-u", "origin", "brr/strand-work")
    assert daemon.gitops.rev_parse(host, "brr/strand-work") is None, (
        "the premise: a clone's branch is not a local ref in the parent"
    )
    report = tmp_path / "report.md"
    report.write_text("# done\n")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    task = _strand(clone, report)
    created: list[dict] = []
    monkeypatch.setattr(
        daemon.protocol, "create_event",
        lambda *a, **kw: created.append(kw) or "evt-submitted",
    )
    assert daemon._queue_submit_request(task, inbox, "", outbox, host) is True
    assert created and created[0]["spawn_published_branch"] == "brr/strand-work"
    assert created[0]["spawn_commits"] == 1
    assert task.meta["submitted"] is True


def test_unpushed_clone_branch_is_still_refused(repos, tmp_path, monkeypatch):
    host, clone = repos
    report = tmp_path / "report.md"
    report.write_text("# done\n")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    task = _strand(clone, report)
    seen: list[str] = []
    monkeypatch.setattr(
        daemon, "_record_outbox_notice",
        lambda _o, text, **kw: seen.append(text),
    )
    assert daemon._queue_submit_request(task, inbox, "", outbox, host) is False
    assert seen == ["submit refused: missing published branch 'brr/strand-work'"]


def test_local_commits_past_the_remote_are_unpublished(repos, tmp_path, monkeypatch):
    host, clone = repos
    _git(clone, "push", "-q", "-u", "origin", "brr/strand-work")
    (clone / "c.txt").write_text("c\n")
    _git(clone, "add", "c.txt")
    _git(clone, "commit", "-q", "-m", "not pushed")
    report = tmp_path / "report.md"
    report.write_text("# done\n")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    task = _strand(clone, report)
    seen: list[str] = []
    monkeypatch.setattr(
        daemon, "_record_outbox_notice",
        lambda _o, text, **kw: seen.append(text),
    )
    assert daemon._queue_submit_request(task, inbox, "", outbox, host) is False
    assert seen and "missing published branch" in seen[0]
