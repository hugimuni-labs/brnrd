"""Tests for the daemon freshness hook (`brr.sync`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import sync


# ── Fixtures ─────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")


def _commit_file(repo: Path, name: str, body: str, *, message: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


def _setup_remote_and_local(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare ``remote`` and a ``local`` clone with a tracking main."""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _init_repo(seed)
    _commit_file(seed, "README.md", "seed\n", message="seed")
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(remote)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    local = tmp_path / "local"
    subprocess.run(
        ["git", "clone", str(remote), str(local)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _git(local, "config", "user.name", "Test User")
    _git(local, "config", "user.email", "test@example.com")
    return remote, local


def _push_new_commit(
    tmp_path: Path,
    remote: Path,
    branch: str = "main",
    *,
    file: str = "README.md",
    body: str = "advanced\n",
) -> str:
    """Make a fresh commit on *branch* and push it to *remote*. Returns the OID.

    By default the commit overwrites ``README.md`` so a tests can stage
    a conflicting local change and force a non-clean ff.
    """
    pusher = tmp_path / "pusher"
    if not pusher.exists():
        subprocess.run(
            ["git", "clone", str(remote), str(pusher)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        _git(pusher, "config", "user.name", "Pusher")
        _git(pusher, "config", "user.email", "pusher@example.com")
    _git(pusher, "fetch", "origin")
    if subprocess.run(
        ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
        cwd=pusher, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).returncode != 0:
        _git(pusher, "checkout", "-b", branch, f"origin/{branch}")
    else:
        _git(pusher, "checkout", branch)
        _git(pusher, "merge", "--ff-only", f"origin/{branch}")
    _commit_file(pusher, file, body, message=f"advance {branch}")
    _git(pusher, "push", "origin", branch)
    return _git(pusher, "rev-parse", "HEAD").stdout.strip()


# ── Behavior ─────────────────────────────────────────────────────────


def test_refresh_no_remote_is_silent_noop(tmp_path):
    repo = tmp_path / "solo"
    _init_repo(repo)
    _commit_file(repo, "x", "x\n", message="solo")

    result = sync.refresh_before_task(repo, target_branches=["main"])

    assert result.fetched is False
    assert result.error is None
    assert result.ff_branches == {}
    # Skipped because no remote is configured.
    assert result.skipped == {"main": "no remote configured"}


def test_refresh_fetches_then_ff_main(tmp_path):
    remote, local = _setup_remote_and_local(tmp_path)
    new_oid = _push_new_commit(tmp_path, remote, branch="main")

    result = sync.refresh_before_task(local, target_branches=["main"])

    assert result.fetched is True
    assert result.error is None
    assert result.skipped == {}
    assert result.ff_branches == {"main": new_oid}
    head = _git(local, "rev-parse", "HEAD").stdout.strip()
    assert head == new_oid


def test_refresh_skips_when_working_tree_dirty(tmp_path):
    remote, local = _setup_remote_and_local(tmp_path)
    _push_new_commit(tmp_path, remote, branch="main")
    # README.md is the file the pusher just modified, so an unstaged
    # local edit to it makes the merge unsafe.
    (local / "README.md").write_text("local dirty edit\n", encoding="utf-8")

    result = sync.refresh_before_task(local, target_branches=["main"])

    assert result.fetched is True
    assert result.error is None
    assert result.ff_branches == {}
    assert "main" in result.skipped
    assert result.skipped["main"]


def test_refresh_skips_when_history_diverged(tmp_path):
    remote, local = _setup_remote_and_local(tmp_path)
    _push_new_commit(tmp_path, remote, branch="main")
    # Diverge local main with a fresh commit instead of pulling.
    _commit_file(local, "local-only.txt", "x\n", message="local divergence")

    result = sync.refresh_before_task(local, target_branches=["main"])

    assert result.fetched is True
    assert result.error is None
    assert result.ff_branches == {}
    assert "main" in result.skipped
    assert "fast-forward" in result.skipped["main"].lower()


def test_refresh_handles_multiple_target_branches(tmp_path):
    remote, local = _setup_remote_and_local(tmp_path)
    # Create a second branch on the remote and pull it locally so it
    # exists as a tracked local ref.
    _git(local, "checkout", "-b", "feature")
    _commit_file(local, "feature.txt", "feature\n", message="feature seed")
    _git(local, "push", "-u", "origin", "feature")
    _git(local, "checkout", "main")

    main_oid = _push_new_commit(tmp_path, remote, branch="main")
    feature_oid = _push_new_commit(tmp_path, remote, branch="feature")

    result = sync.refresh_before_task(
        local, target_branches=["main", "feature", "main"],
    )

    assert result.fetched is True
    assert result.error is None
    assert result.ff_branches == {"main": main_oid, "feature": feature_oid}


def test_refresh_skips_branch_without_remote_ref(tmp_path):
    _, local = _setup_remote_and_local(tmp_path)
    _git(local, "checkout", "-b", "purely-local")
    _commit_file(local, "local.txt", "x\n", message="purely local")

    result = sync.refresh_before_task(
        local, target_branches=["purely-local"],
    )

    assert result.fetched is True
    assert result.ff_branches == {}
    assert result.skipped["purely-local"].startswith("no remote ref")


def test_refresh_skips_missing_local_branch(tmp_path):
    _, local = _setup_remote_and_local(tmp_path)

    result = sync.refresh_before_task(
        local, target_branches=["never-existed"],
    )

    assert result.fetched is True
    assert result.skipped == {"never-existed": "branch does not exist locally"}


def test_refresh_captures_unexpected_exception_into_result(monkeypatch, tmp_path):
    _, local = _setup_remote_and_local(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(sync.gitops, "default_remote", boom)

    result = sync.refresh_before_task(local, target_branches=["main"])

    assert result.fetched is False
    assert result.ff_branches == {}
    assert result.error is not None
    assert "unexpected" in result.error


def test_refresh_records_fetch_failure(monkeypatch, tmp_path):
    _, local = _setup_remote_and_local(tmp_path)
    real_run = subprocess.run

    def maybe_fail(args, *rest, **kwargs):
        # Only intercept the actual fetch — gitops still needs a real
        # subprocess so default_remote / branch lookups work.
        if isinstance(args, list) and args[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(
                args=args, returncode=128,
                stdout="", stderr="fatal: unable to access 'origin': boom\n",
            )
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(sync.subprocess, "run", maybe_fail)

    result = sync.refresh_before_task(local, target_branches=["main"])

    assert result.fetched is False
    assert result.error is not None
    assert "boom" in result.error
    # When the fetch failed, we don't try to advance any local refs.
    assert result.ff_branches == {}


def test_refresh_disabled_via_config(tmp_path):
    _, local = _setup_remote_and_local(tmp_path)
    _push_new_commit(tmp_path, local.parent / "remote.git", branch="main")

    result = sync.refresh_before_task(
        local,
        target_branches=["main"],
        cfg={"sync.fetch_before_task": False},
    )

    assert result.fetched is False
    assert result.ff_branches == {}
    assert result.skipped == {
        "main": "fetch disabled (sync.fetch_before_task=false)",
    }


def test_refresh_ff_disabled_via_config(tmp_path):
    remote, local = _setup_remote_and_local(tmp_path)
    _push_new_commit(tmp_path, remote, branch="main")

    pre_oid = _git(local, "rev-parse", "HEAD").stdout.strip()
    result = sync.refresh_before_task(
        local,
        target_branches=["main"],
        cfg={"sync.fast_forward_default": False},
    )

    assert result.fetched is True
    assert result.ff_branches == {}
    assert result.skipped == {
        "main": "ff disabled (sync.fast_forward_default=false)",
    }
    # Local main untouched.
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == pre_oid


def test_refresh_already_up_to_date_is_quiet(tmp_path):
    _, local = _setup_remote_and_local(tmp_path)

    result = sync.refresh_before_task(local, target_branches=["main"])

    assert result.fetched is True
    assert result.error is None
    assert result.ff_branches == {}
    assert result.skipped == {}
    assert result.is_noop()


def test_render_summary_quiet_on_noop():
    assert sync.render_summary(sync.SyncResult(fetched=True)) == ""


def test_render_summary_describes_ff_and_skips():
    result = sync.SyncResult(
        fetched=True,
        ff_branches={"main": "abcdef1234"},
        skipped={"feature": "non-fast-forward"},
    )
    summary = sync.render_summary(result)
    assert "ff main -> abcdef1" in summary
    assert "skipped feature (non-fast-forward)" in summary


def test_render_summary_includes_error():
    result = sync.SyncResult(error="git fetch origin: boom")
    assert "error: git fetch origin: boom" in sync.render_summary(result)


@pytest.mark.parametrize("raw, expected", [
    (True, True),
    (False, False),
    ("true", True),
    ("False", False),
    ("0", False),
    ("1", True),
    (1, True),
    (0, False),
])
def test_bool_helper(raw, expected):
    assert sync._bool({"k": raw}, "k", default=not expected) is expected
