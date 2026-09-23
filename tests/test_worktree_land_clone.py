"""#2093 — landing a clone's branch must not read the result through the host's
single ``FETCH_HEAD``: two clones landing in the same second overwrite it
between one run's fetch and its fast-forward, and the first branch comes out
pointing at the second clone's commit."""
from __future__ import annotations

import subprocess
from pathlib import Path

from brr import gitops, worktree


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo_with_commit(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "seed.txt").write_text("seed\n")
    _git(path, "add", "seed.txt")
    _git(path, "commit", "-q", "-m", "seed")


def _clone_with_branch(host: Path, clone: Path, branch: str) -> str:
    _git(host.parent, "clone", "-q", "--shared", str(host), str(clone))
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "t")
    _git(clone, "switch", "-q", "-c", branch)
    (clone / f"{branch.replace('/', '-')}.txt").write_text(f"{branch}\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", f"work on {branch}")
    return _git(clone, "rev-parse", "HEAD")


def test_two_clones_landing_back_to_back_each_keep_their_own_commit(tmp_path):
    host = tmp_path / "host"
    _repo_with_commit(host)
    oid_a = _clone_with_branch(host, tmp_path / "clone-a", "brr/tick")
    oid_b = _clone_with_branch(host, tmp_path / "clone-b", "brr/walls")

    assert worktree.land_clone_branch(host, tmp_path / "clone-a", "brr/tick").success
    assert worktree.land_clone_branch(host, tmp_path / "clone-b", "brr/walls").success

    assert gitops.rev_parse(host, "refs/heads/brr/tick") == oid_a
    assert gitops.rev_parse(host, "refs/heads/brr/walls") == oid_b


def test_landing_survives_fetch_head_being_overwritten_mid_landing(tmp_path, monkeypatch):
    """The race itself, made deterministic: another clone's fetch lands on
    ``FETCH_HEAD`` between this landing's fetch and its fast-forward."""
    host = tmp_path / "host"
    _repo_with_commit(host)
    oid_a = _clone_with_branch(host, tmp_path / "clone-a", "brr/tick")
    oid_b = _clone_with_branch(host, tmp_path / "clone-b", "brr/walls")

    original = worktree._git

    def racing_git(repo_root, *args, **kwargs):
        result = original(repo_root, *args, **kwargs)
        if "fetch" in args:
            # the sibling's fetch, arriving one instant later — fetch B's
            # objects for real (so they are reachable either way) and leave
            # B's oid on the one FETCH_HEAD both landings would read
            original(repo_root, "fetch", "--no-tags", str(tmp_path / "clone-b"), "brr/walls", check=False)
        return result

    monkeypatch.setattr(worktree, "_git", racing_git)

    landed = worktree.land_clone_branch(host, tmp_path / "clone-a", "brr/tick")

    assert landed.success, landed.detail
    assert (host / ".git" / "FETCH_HEAD").read_text().startswith(oid_b)
    assert gitops.rev_parse(host, "refs/heads/brr/tick") == oid_a


def test_landing_a_branch_the_clone_does_not_have_fails_by_name(tmp_path):
    host = tmp_path / "host"
    _repo_with_commit(host)
    _clone_with_branch(host, tmp_path / "clone-a", "brr/tick")

    landed = worktree.land_clone_branch(host, tmp_path / "clone-a", "brr/nope")

    assert not landed.success
    assert "brr/nope" in (landed.detail or "")
