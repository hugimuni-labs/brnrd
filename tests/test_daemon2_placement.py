"""Placement adapter: allocate/release/land wrapping worktree.create_clone."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from brr import protocol
from brr.daemon2.placement import Allocation, PlacementError, allocate, publish, release
from brr.daemon2.runtime import Daemon2


def _git_env() -> dict[str, str]:
    """Stripped env without GIT_DIR/GIT_WORK_TREE so test ops stay in tmp."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update({"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"})
    return env


def _make_git_repo(path: Path) -> Path:
    """Init a minimal git repo at *path* with one commit."""
    env = _git_env()
    subprocess.run(["git", "init", "-b", "main", str(path)],
                   env=env, capture_output=True, check=True)
    (path / "AGENTS.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."],
                   env=env, capture_output=True, check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"],
                   env=env, capture_output=True, check=True)
    return path


def test_allocate_creates_clone_and_release_removes_it(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    alloc = allocate(repo, "run-test-0001")
    assert isinstance(alloc, Allocation)
    assert alloc.path.exists()
    assert alloc.branch.startswith("brr/")
    assert alloc.run_id == "run-test-0001"
    git_dir = alloc.path / ".git"
    assert git_dir.is_dir(), "clone must have its own .git directory"
    env = alloc.env()
    assert env["GIT_DIR"] == str(git_dir)
    assert env["GIT_WORK_TREE"] == str(alloc.path)
    release(alloc)
    assert not alloc.path.exists()


def test_release_is_idempotent(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    alloc = allocate(repo, "run-test-0002")
    release(alloc)
    release(alloc)  # second call must not raise


def test_allocate_fails_on_non_git_directory(tmp_path: Path) -> None:
    plain = tmp_path / "notgit"
    plain.mkdir()
    (plain / "AGENTS.md").write_text("# x\n")
    with pytest.raises(PlacementError, match="failed to allocate"):
        allocate(plain, "run-test-0003")


def test_git_env_pins_to_clone_not_host(tmp_path: Path) -> None:
    """Operations using alloc.env() target the clone, not the host checkout."""
    repo = _make_git_repo(tmp_path / "repo")
    alloc = allocate(repo, "run-test-0004")
    try:
        env = {**_git_env(), **alloc.env()}
        # Write and commit a file inside the clone.
        (alloc.path / "strand-work.txt").write_text("done\n", encoding="utf-8")
        subprocess.run(["git", "add", "strand-work.txt"],
                       env=env, cwd=str(alloc.path), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "strand work"],
                       env=env, cwd=str(alloc.path), capture_output=True, check=True)
        # The host repo must not have the strand commit.
        host_log = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline"],
            env=_git_env(), capture_output=True, text=True, check=True)
        assert "strand work" not in host_log.stdout
        # The clone has it.
        clone_log = subprocess.run(
            ["git", "log", "--oneline"],
            env=env, cwd=str(alloc.path), capture_output=True, text=True, check=True)
        assert "strand work" in clone_log.stdout
    finally:
        release(alloc)


def test_publish_lands_actual_branch_and_pushes_before_release(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], env=_git_env(),
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)],
                   env=_git_env(), capture_output=True, check=True)
    alloc = allocate(repo, "run-publish")
    env = {**_git_env(), **alloc.env()}
    subprocess.run(["git", "switch", "-c", "brr/the-child"], env=env,
                   cwd=alloc.path, capture_output=True, check=True)
    (alloc.path / "work.txt").write_text("kept\n")
    subprocess.run(["git", "add", "work.txt"], env=env, cwd=alloc.path,
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "child work"], env=env, cwd=alloc.path,
                   capture_output=True, check=True)
    result = publish(repo, alloc)
    assert (result.branch, result.landed, result.pushed, result.released) == (
        "brr/the-child", True, True, True)
    assert not alloc.path.exists()
    remote_ref = subprocess.run(["git", "--git-dir", str(remote), "rev-parse",
                                 "refs/heads/brr/the-child"], env=_git_env(),
                                capture_output=True, text=True, check=True).stdout.strip()
    host_ref = subprocess.run(["git", "-C", str(repo), "rev-parse",
                               "refs/heads/brr/the-child"], env=_git_env(),
                              capture_output=True, text=True, check=True).stdout.strip()
    assert remote_ref == host_ref


def test_publish_keeps_dirty_clone_for_salvage(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    alloc = allocate(repo, "run-dirty")
    (alloc.path / "unsaved.txt").write_text("partial work\n")
    result = publish(repo, alloc)
    assert result.landed and not result.released
    assert "uncommitted" in result.detail
    assert (alloc.path / "unsaved.txt").read_text() == "partial work\n"
    release(alloc)


def test_runtime_publishes_child_branch_before_clone_cleanup(tmp_path: Path,
                                                              monkeypatch) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], env=_git_env(),
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)],
                   env=_git_env(), capture_output=True, check=True)
    home = tmp_path / "home"
    protocol.create_event(home / "dispatch" / "inbox", "spawn", "work",
                          conversation_key="c", ask_id="ask-1",
                          parent_run_id="run-parent", spawn_edge="edge-1",
                          child_run_id="run-child", branch="brr/the-child",
                          report=str(tmp_path / "report.md"))
    binary = tmp_path / "child-shell"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import os, subprocess, time\nfrom pathlib import Path\n"
        "os.environ.update(GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='t@t.com', "
        "GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='t@t.com')\n"
        "subprocess.run(['git', 'switch', '-c', 'brr/the-child'], check=True)\n"
        "Path('child.txt').write_text('saved\\n')\n"
        "subprocess.run(['git', 'add', 'child.txt'], check=True)\n"
        "subprocess.run(['git', 'commit', '-m', 'child work'], check=True)\n"
        f"Path({str(tmp_path / 'report.md')!r}).write_text('Status: done\\n')\n"
        "outbox = Path(os.environ['BRR_OUTBOX_DIR'])\n"
        "(outbox / 'submit.md.tmp').write_text('---\\nsubmit: true\\n---\\nready\\n')\n"
        "(outbox / 'submit.md.tmp').rename(outbox / 'submit.md')\n"
        "time.sleep(0.1)\n")
    binary.chmod(0o755)
    runtime = Daemon2(repo, home, runtime_dir=tmp_path / "runtime",
                      runner_name="fake", runner_config={"runner_cmd": [str(binary)]},
                      tick_seconds=0.02)
    runtime.supervisor.register("ask-1", "c", "run-parent", "edge-1", "run-child")
    returned = runtime.supervisor.returned
    def checked_returned(*args, **kwargs):
        published = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/brr/the-child"],
            env=_git_env(), capture_output=True)
        assert published.returncode == 0, "submit reported before branch publication"
        return returned(*args, **kwargs)
    monkeypatch.setattr(runtime.supervisor, "returned", checked_returned)
    result = runtime.once(role="strand")
    assert result is not None and result.returncode == 0
    assert not (repo / ".brr" / "worktrees" / "run-child").exists()
    remote_ref = subprocess.run(["git", "--git-dir", str(remote), "rev-parse",
                                 "refs/heads/brr/the-child"], env=_git_env(),
                                capture_output=True, text=True, check=True).stdout.strip()
    host_ref = subprocess.run(["git", "-C", str(repo), "rev-parse",
                               "refs/heads/brr/the-child"], env=_git_env(),
                              capture_output=True, text=True, check=True).stdout.strip()
    assert remote_ref == host_ref
    submissions = [event for event in runtime.door.pending()
                   if event["source"] == "spawn_submitted"]
    assert len(submissions) == 1
    assert submissions[0]["spawn_submit_generation"] == 1
