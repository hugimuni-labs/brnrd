"""Placement adapter: allocate/release/land wrapping worktree.create_clone."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


from brr import protocol
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
                      tick_seconds=60.0)
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


