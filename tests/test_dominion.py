"""Tests for the agent dominion bootstrap (`brr.dominion`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from brr import dominion, gitops

from _helpers import commit_files, init_git_repo


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    """A git repo with a committed ``main`` and a ``.brr/`` runtime dir."""
    repo = tmp_path / name
    init_git_repo(repo)
    commit_files(repo, {"README.md": "main\n"}, message="init main")
    (repo / ".brr").mkdir()
    return repo


def _clone(remote: Path, dest: Path, *, name: str) -> Path:
    subprocess.run(
        ["git", "clone", str(remote), str(dest)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _git(dest, "config", "user.name", name)
    _git(dest, "config", "user.email", f"{name}@example.com")
    (dest / ".brr").mkdir()
    return dest


# ── Fresh bootstrap ──────────────────────────────────────────────────


def test_fresh_bootstrap_creates_orphan_branch_and_worktree(tmp_path):
    repo = _repo(tmp_path)

    path = dominion.ensure_dominion(repo, push=False)

    assert path == repo / ".brr" / "dominion"
    assert path.is_dir()
    assert gitops.branch_exists(repo, "brr-home")
    assert gitops.branch_checkout_path(repo, "brr-home").resolve() == path.resolve()
    # Seed files are present and committed.
    assert (path / "playbook.md").exists()
    assert (path / "self-inject").exists()
    assert (path / "README.md").exists()


def test_orphan_history_is_independent_of_main(tmp_path):
    repo = _repo(tmp_path)
    dominion.ensure_dominion(repo, push=False)

    main_oid = gitops.rev_parse(repo, "main")
    home_oid = gitops.rev_parse(repo, "brr-home")
    assert main_oid and home_oid and main_oid != home_oid

    # Unrelated histories: no common ancestor.
    merge_base = subprocess.run(
        ["git", "merge-base", "main", "brr-home"],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert merge_base.returncode != 0


def test_custom_branch_name(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, branch="brr-dominion", push=False)
    assert gitops.branch_exists(repo, "brr-dominion")
    assert gitops.branch_checkout_path(repo, "brr-dominion").resolve() == path.resolve()


# ── Idempotency / returning ──────────────────────────────────────────


def test_restart_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    first = dominion.ensure_dominion(repo, push=False)
    first_oid = gitops.rev_parse(repo, "brr-home")

    second = dominion.ensure_dominion(repo, push=False)

    assert first == second
    # No re-seed, no new commit.
    assert gitops.rev_parse(repo, "brr-home") == first_oid


def test_returning_reattaches_existing_branch(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    seed_oid = gitops.rev_parse(repo, "brr-home")

    # Simulate a fresh local checkout: drop the worktree, keep the branch.
    _git(repo, "worktree", "remove", "--force", str(path))
    assert gitops.branch_checkout_path(repo, "brr-home") is None

    again = dominion.ensure_dominion(repo, push=False)

    assert again.resolve() == path.resolve()
    assert path.is_dir()
    assert (path / "playbook.md").exists()
    # Re-attached to the same branch — not re-seeded.
    assert gitops.rev_parse(repo, "brr-home") == seed_oid


# ── Forge-backed continuity ──────────────────────────────────────────


def test_returning_from_remote_fetches_and_attaches(tmp_path):
    # A bare remote seeded with main only.
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    init_git_repo(seed)
    commit_files(seed, {"README.md": "main\n"}, message="init")
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(remote)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    # Clone A creates and publishes the dominion.
    clone_a = _clone(remote, tmp_path / "a", name="A")
    dominion.ensure_dominion(clone_a, push=True)

    # Clone B (a "second machine") reconstitutes it from the remote.
    clone_b = _clone(remote, tmp_path / "b", name="B")
    path_b = dominion.ensure_dominion(clone_b, push=False)

    assert path_b.is_dir()
    assert (path_b / "playbook.md").exists()  # fetched the seeded content
    assert gitops.branch_checkout_path(clone_b, "brr-home").resolve() == path_b.resolve()


def test_fresh_bootstrap_without_remote_does_not_raise(tmp_path):
    # No remote configured: stays local, still durable across runs.
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo)  # push defaults True; no-op without remote
    assert path.is_dir()
    assert gitops.branch_exists(repo, "brr-home")
