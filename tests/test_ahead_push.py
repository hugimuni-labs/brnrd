"""Tests for the ahead-of-upstream push net (#1780).

Until this existed, the account home (dominion) repo and the knowledge
repo only ever reached their remotes at a thought's own closeout
(``daemon._capture_dominion`` / ``daemon._capture_knowledge``). This net
runs on the daemon's own scan tick instead, independent of any particular
thought: a repo that is ahead of its upstream gets pushed, best-effort,
rate-limited per repo by ``capture.push_interval_seconds``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
import subprocess
import threading
import time

from brr import account, daemon, dominion, gitops

from _helpers import commit_files, init_git_repo


def _account_cfg(repo: Path, home: Path, **extra) -> dict:
    cfg = {
        "repo.label": "Gurio/brr",
        "home.kind": "account",
        "home.path": str(home),
        "account.id": "acct-1",
    }
    cfg.update(extra)
    return cfg


def _bare_remote(path: Path) -> Path:
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True,
    )
    return path


def _account_context(tmp_path: Path, **cfg_extra) -> tuple[account.AccountContext, dict, Path]:
    """A resolved account context whose dominion repo already exists on disk.

    ``account.resolve_context`` git-inits and seeds ``home_root`` (the
    dominion repo) the first time it is called — the same birth path a real
    daemon boot takes — so the caller gets a real repo to push, not a mock.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"README.md": "repo\n"}, message="init repo")
    home = tmp_path / "home"
    cfg = _account_cfg(repo, home, **cfg_extra)
    ctx = account.resolve_context(repo, cfg)
    return ctx, cfg, repo


def _wait_for_ahead_push_threads(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = [
            t for t in threading.enumerate()
            if t.name.startswith("brr-ahead-push-") and t.is_alive()
        ]
        if not live:
            return
        time.sleep(0.02)
    raise AssertionError("ahead-push thread(s) did not finish in time")


def _spy_push_branch(monkeypatch) -> list:
    calls: list = []
    original = gitops.push_branch

    def _wrapped(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(gitops, "push_branch", _wrapped)
    return calls


def _init_knowledge_repo(ctx: account.AccountContext) -> Path:
    knowledge_repo = account.knowledge_path(ctx)
    init_git_repo(knowledge_repo)
    commit_files(knowledge_repo, {"index.md": "seed\n"}, message="seed knowledge")
    return knowledge_repo


def test_push_ahead_dominion_when_ahead_by_one(tmp_path, monkeypatch):
    calls = _spy_push_branch(monkeypatch)
    ctx, cfg, repo = _account_context(tmp_path)
    remote = _bare_remote(tmp_path / "dominion-remote.git")
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=ctx.dominion_repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"],
        cwd=ctx.dominion_repo, check=True,
    )
    commit_files(ctx.dominion_repo, {"notes/x.md": "one commit ahead\n"}, message="ahead by one")
    brr_dir = gitops.shared_brr_dir(repo)

    dispatched = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg)
    assert dispatched == ["account home"]
    _wait_for_ahead_push_threads()

    assert len(calls) == 1
    assert gitops.ahead_count(ctx.dominion_repo, "@{upstream}", "HEAD") == 0
    remote_head = subprocess.run(
        ["git", "rev-parse", "main"], cwd=remote,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    local_head = gitops.rev_parse(ctx.dominion_repo, "HEAD")
    assert remote_head == local_head


def test_push_ahead_knowledge_when_ahead_by_one(tmp_path, monkeypatch):
    calls = _spy_push_branch(monkeypatch)
    ctx, cfg, repo = _account_context(tmp_path)
    knowledge_repo = _init_knowledge_repo(ctx)
    remote = _bare_remote(tmp_path / "knowledge-remote.git")
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)], cwd=knowledge_repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=knowledge_repo, check=True,
    )
    commit_files(knowledge_repo, {"log.md": "a durable entry\n"}, message="ahead by one")
    brr_dir = gitops.shared_brr_dir(repo)

    dispatched = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg)
    # The account home dir always exists once `_account_context` resolves
    # it, so it is always a candidate target too — it simply has no remote
    # here, so its own thread finds nothing to push (asserted via `calls`).
    assert set(dispatched) == {"account home", "knowledge"}
    _wait_for_ahead_push_threads()

    assert len(calls) == 1
    assert calls[0][0][0] == knowledge_repo
    assert gitops.ahead_count(knowledge_repo, "@{upstream}", "HEAD") == 0


def test_push_ahead_pushes_both_repos_in_one_tick(tmp_path, monkeypatch):
    calls = _spy_push_branch(monkeypatch)
    ctx, cfg, repo = _account_context(tmp_path)
    dom_remote = _bare_remote(tmp_path / "dominion-remote.git")
    subprocess.run(
        ["git", "remote", "add", "origin", str(dom_remote)],
        cwd=ctx.dominion_repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=ctx.dominion_repo, check=True,
    )
    commit_files(ctx.dominion_repo, {"notes/x.md": "ahead\n"}, message="dominion ahead")

    knowledge_repo = _init_knowledge_repo(ctx)
    kn_remote = _bare_remote(tmp_path / "knowledge-remote.git")
    subprocess.run(
        ["git", "remote", "add", "origin", str(kn_remote)], cwd=knowledge_repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=knowledge_repo, check=True,
    )
    commit_files(knowledge_repo, {"log.md": "ahead\n"}, message="knowledge ahead")
    brr_dir = gitops.shared_brr_dir(repo)

    dispatched = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg)
    assert set(dispatched) == {"account home", "knowledge"}
    _wait_for_ahead_push_threads()

    assert len(calls) == 2
    assert gitops.ahead_count(ctx.dominion_repo, "@{upstream}", "HEAD") == 0
    assert gitops.ahead_count(knowledge_repo, "@{upstream}", "HEAD") == 0


def test_push_ahead_by_zero_pushes_nothing(tmp_path, monkeypatch):
    calls = _spy_push_branch(monkeypatch)
    ctx, cfg, repo = _account_context(tmp_path)
    remote = _bare_remote(tmp_path / "dominion-remote.git")
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=ctx.dominion_repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=ctx.dominion_repo, check=True,
    )
    # Nothing committed since the push above — already in sync.
    brr_dir = gitops.shared_brr_dir(repo)

    dispatched = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg)
    assert dispatched == ["account home"]
    _wait_for_ahead_push_threads()

    assert calls == []


def test_push_ahead_no_remote_is_silent(tmp_path, monkeypatch):
    calls = _spy_push_branch(monkeypatch)
    ctx, cfg, repo = _account_context(tmp_path)
    # No remote configured at all on the freshly born dominion repo.
    commit_files(ctx.dominion_repo, {"notes/x.md": "no remote to reach\n"}, message="ahead")
    brr_dir = gitops.shared_brr_dir(repo)

    dispatched = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg)
    assert dispatched == ["account home"]
    _wait_for_ahead_push_threads()

    assert calls == []
    assert dominion.needs_sync(ctx.dominion_repo.parent) is None


def test_push_ahead_failure_sets_needs_sync_without_raising(tmp_path, monkeypatch):
    ctx, cfg, repo = _account_context(tmp_path)
    missing = tmp_path / "missing.git"  # never created — an unreachable local path
    subprocess.run(
        ["git", "remote", "add", "origin", str(missing)],
        cwd=ctx.dominion_repo, check=True,
    )
    commit_files(ctx.dominion_repo, {"notes/x.md": "nowhere to go\n"}, message="ahead")
    brr_dir = gitops.shared_brr_dir(repo)

    dispatched = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg)
    assert dispatched == ["account home"]
    _wait_for_ahead_push_threads()

    reason = dominion.needs_sync(ctx.dominion_repo.parent)
    assert reason is not None
    assert str(ctx.dominion_repo) in reason


def test_push_ahead_respects_the_configured_interval(tmp_path, monkeypatch):
    calls = _spy_push_branch(monkeypatch)
    ctx, cfg, repo = _account_context(tmp_path)
    remote = _bare_remote(tmp_path / "dominion-remote.git")
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=ctx.dominion_repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=ctx.dominion_repo, check=True,
    )
    commit_files(ctx.dominion_repo, {"notes/x.md": "ahead\n"}, message="ahead")
    brr_dir = gitops.shared_brr_dir(repo)

    first = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg, now=1_000.0)
    assert first == ["account home"]
    _wait_for_ahead_push_threads()
    assert len(calls) == 1

    # A fresh ahead commit lands, but the second tick is well inside the
    # default 60s interval — no second push, however much is now pending.
    commit_files(ctx.dominion_repo, {"notes/y.md": "ahead again\n"}, message="ahead again")
    second = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg, now=1_010.0)
    assert second == []
    _wait_for_ahead_push_threads()
    assert len(calls) == 1

    # Past the interval, the pending commit reaches the remote.
    third = daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg, now=1_070.0)
    assert third == ["account home"]
    _wait_for_ahead_push_threads()
    assert len(calls) == 2


def test_push_ahead_interval_is_configurable(tmp_path, monkeypatch):
    calls = _spy_push_branch(monkeypatch)
    ctx, cfg, repo = _account_context(tmp_path, **{"capture.push_interval_seconds": 5})
    remote = _bare_remote(tmp_path / "dominion-remote.git")
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=ctx.dominion_repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=ctx.dominion_repo, check=True,
    )
    commit_files(ctx.dominion_repo, {"notes/x.md": "ahead\n"}, message="ahead")
    brr_dir = gitops.shared_brr_dir(repo)

    assert daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg, now=2_000.0) == ["account home"]
    _wait_for_ahead_push_threads()
    assert len(calls) == 1

    commit_files(ctx.dominion_repo, {"notes/y.md": "ahead again\n"}, message="ahead again")
    # Past the shortened 5s interval, unlike the default-60s test above.
    assert daemon._push_ahead_repos_if_due(brr_dir, ctx, cfg, now=2_006.0) == ["account home"]
    _wait_for_ahead_push_threads()
    assert len(calls) == 2


def test_push_ahead_disabled_account_is_a_noop(tmp_path, monkeypatch):
    calls = _spy_push_branch(monkeypatch)
    ctx, cfg, repo = _account_context(tmp_path)
    remote = _bare_remote(tmp_path / "dominion-remote.git")
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=ctx.dominion_repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=ctx.dominion_repo, check=True,
    )
    commit_files(ctx.dominion_repo, {"notes/x.md": "ahead\n"}, message="ahead")
    brr_dir = gitops.shared_brr_dir(repo)
    disabled_ctx = dataclasses.replace(ctx, enabled=False)

    dispatched = daemon._push_ahead_repos_if_due(brr_dir, disabled_ctx, cfg)
    assert dispatched == []
    assert calls == []
