"""send_to_self: rebase, union, a lost race, and a verbatim refusal."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from brr.loom.runtime.merge import SendError, install_pre_receive, send_to_self

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "src" / "brr" / "loom" / "runtime" / "seed"
_DISCOVERY = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_NAMESPACE",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _DISCOVERY}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_EDITOR"] = "true"
    return env


def git(repo: Path | None, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git"]
    if repo is not None:
        cmd += ["-C", os.fspath(repo)]
    cmd += list(args)
    proc = subprocess.run(cmd, env=_env(), capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise AssertionError(f"{cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def write(repo: Path, rel: str, text: str, *, exe: bool = False) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if exe:
        path.chmod(0o755)


def copy_seed(repo: Path) -> None:
    for src in SEED.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(SEED)
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if rel.as_posix() in {"core/immune", "core/immune.d/50-core-notice"} or src.stat().st_mode & 0o111:
            dest.chmod(0o755)


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def published(tmp: Path) -> Path:
    bare = tmp / "self.git"
    work = tmp / "work"
    git(None, "init", "--bare", "-b", "main", os.fspath(bare))
    git(None, "init", "-b", "main", os.fspath(work))
    copy_seed(work)
    write(work, "memory/scars/x.md", "base\n")
    write(work, "memory/scars/a.md", "a-base\n")
    write(work, "memory/itches/b.md", "b-base\n")
    write(work, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha line\n")
    commit(work, "seed")
    git(work, "remote", "add", "origin", os.fspath(bare))
    install_pre_receive(bare)
    first = git(work, "push", "origin", "HEAD:main", check=False)
    assert first.returncode == 0, first.stderr + first.stdout
    return bare


def room(bare: Path, dest: Path, name: str) -> Path:
    git(None, "clone", os.fspath(bare), os.fspath(dest))
    git(dest, "checkout", "-b", name)
    return dest


def show(bare: Path, spec: str) -> str:
    return git(bare, "show", f"main:{spec}").stdout


def rebasing(repo: Path) -> bool:
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def test_different_entry_files_both_land(tmp_path: Path) -> None:
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/a")
    right = room(bare, tmp_path / "b", "strand/b")
    write(left, "memory/scars/a.md", "a-base\nfrom-a\n")
    commit(left, "scar a")
    write(right, "memory/itches/b.md", "b-base\nfrom-b\n")
    commit(right, "itch b")
    assert send_to_self(left).status == "merged"
    landed = send_to_self(right)
    assert landed.status == "merged"
    assert "from-a" in show(bare, "memory/scars/a.md")
    assert "from-b" in show(bare, "memory/itches/b.md")


def test_union_keeps_both_appended_lines(tmp_path: Path) -> None:
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/a")
    right = room(bare, tmp_path / "b", "strand/b")
    write(left, "memory/scars/x.md", "base\nfrom-a\n")
    commit(left, "append a")
    write(right, "memory/scars/x.md", "base\nfrom-b\n")
    commit(right, "append b")
    assert send_to_self(left).status == "merged"
    landed = send_to_self(right)
    assert landed.status == "merged", landed
    text = show(bare, "memory/scars/x.md")
    assert "base" in text
    assert "from-a" in text
    assert "from-b" in text


def test_readme_conflict_resolves_on_the_next_call(tmp_path: Path) -> None:
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/a")
    right = room(bare, tmp_path / "b", "strand/b")
    write(left, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha from a\n")
    commit(left, "readme a")
    write(right, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha from b\n")
    commit(right, "readme b")
    assert send_to_self(left).status == "merged"
    stopped = send_to_self(right)
    assert stopped.status == "stopped"
    assert "threads/inbox/README.md" in stopped.files
    assert rebasing(right)
    write(right, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha resolved\n")
    git(right, "add", "threads/inbox/README.md")
    landed = send_to_self(right)
    assert landed.status == "merged", landed
    assert "alpha resolved" in show(bare, "threads/inbox/README.md")


def test_three_unresolved_stops_abort(tmp_path: Path) -> None:
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/a")
    right = room(bare, tmp_path / "b", "strand/b")
    write(left, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha from a\n")
    commit(left, "readme a")
    write(right, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha from b\n")
    commit(right, "readme b")
    assert send_to_self(left).status == "merged"
    first = send_to_self(right)
    second = send_to_self(right)
    third = send_to_self(right)
    assert first.status == "stopped"
    assert second.status == "stopped"
    assert third.status == "failed"
    assert third.tries == 3
    assert "threads/inbox/README.md" in third.files
    assert not rebasing(right)
    assert git(right, "status", "--porcelain").stdout == ""
    stops = Path(git(right, "rev-parse", "--git-path", "loom-send-stops").stdout.strip())
    if not stops.is_absolute():
        stops = right / stops
    assert not stops.exists()


def test_race_retries_and_lands(tmp_path: Path) -> None:
    bare = published(tmp_path)
    strand = room(bare, tmp_path / "room", "strand/race")
    side = room(bare, tmp_path / "side", "strand/side")
    write(strand, "memory/scars/a.md", "a-base\nrace-room\n")
    commit(strand, "room change")
    hook = Path(git(strand, "rev-parse", "--git-path", "hooks/pre-push").stdout.strip())
    if not hook.is_absolute():
        hook = strand / hook
    hook.parent.mkdir(parents=True, exist_ok=True)
    side_q = shlex.quote(os.fspath(side))
    hook.write_text(
        "#!/bin/sh\n"
        "gitdir=$(git rev-parse --absolute-git-dir)\n"
        'marker="$gitdir/race-once"\n'
        'if [ -f "$marker" ]; then\n'
        "    exit 0\n"
        "fi\n"
        'touch "$marker"\n'
        "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR\n"
        f"git -C {side_q} commit --allow-empty -m race-advance\n"
        f"git -C {side_q} push origin HEAD:main\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    landed = send_to_self(strand)
    assert landed.status == "merged", landed
    assert "race-room" in show(bare, "memory/scars/a.md")


def test_immune_refusal_is_verbatim(tmp_path: Path) -> None:
    bare = published(tmp_path)
    strand = room(bare, tmp_path / "room", "strand/refuse")
    write(
        strand,
        "core/immune.d/60-block",
        "#!/bin/sh\nprintf 'block-this-exact-line\\n' >&2\nexit 1\n",
        exe=True,
    )
    commit(strand, "a refusing check")
    refused = send_to_self(strand)
    assert refused.status == "refused"
    assert "60-block: block-this-exact-line" in refused.stderr
    assert "block-this-exact-line" in refused.stderr
    assert not refused.stderr.lower().startswith("immune refused")
    # The paraphrase we must not invent. The remote's own words stay.
    assert "politely" not in refused.stderr.lower()
    assert git(bare, "rev-parse", "main").returncode == 0


def test_concurrent_sends_land_linear(tmp_path: Path) -> None:
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/a")
    right = room(bare, tmp_path / "b", "strand/b")
    write(left, "memory/scars/a.md", "a-base\nconcurrent-a\n")
    commit(left, "a")
    write(right, "memory/itches/b.md", "b-base\nconcurrent-b\n")
    commit(right, "b")
    barrier = threading.Barrier(2)
    results: list = [None, None]
    errors: list[BaseException] = []

    def run(index: int, repo: Path) -> None:
        try:
            barrier.wait(timeout=30)
            results[index] = send_to_self(repo)
        except BaseException as exc:  # noqa: BLE001 — the assertion is the report
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(0, left)),
        threading.Thread(target=run, args=(1, right)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert all(item is not None and item.status == "merged" for item in results), results
    assert "concurrent-a" in show(bare, "memory/scars/a.md")
    assert "concurrent-b" in show(bare, "memory/itches/b.md")
    parents = git(bare, "rev-list", "--parents", "main").stdout.splitlines()
    for line in parents:
        assert len(line.split()) <= 2


def test_ten_lost_races_raise(tmp_path: Path) -> None:
    bare = published(tmp_path)
    strand = room(bare, tmp_path / "room", "strand/cap")
    side = room(bare, tmp_path / "side", "strand/side")
    write(strand, "memory/scars/a.md", "a-base\ncapped\n")
    commit(strand, "capped")
    hook = Path(git(strand, "rev-parse", "--git-path", "hooks/pre-push").stdout.strip())
    if not hook.is_absolute():
        hook = strand / hook
    side_q = shlex.quote(os.fspath(side))
    hook.write_text(
        "#!/bin/sh\n"
        "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR\n"
        f"git -C {side_q} commit --allow-empty -m advance || exit 1\n"
        f"git -C {side_q} push origin HEAD:main || exit 1\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    with pytest.raises(SendError) as caught:
        send_to_self(strand)
    assert "main moved 10 times" in str(caught.value)


def test_nonbare_self_updates_worktree_and_refuses(tmp_path: Path) -> None:
    # Step 2's home/self: non-bare, main checked out, rooms are shared clones.
    self_repo = tmp_path / "self"
    git(None, "init", "-b", "main", os.fspath(self_repo))
    copy_seed(self_repo)
    write(self_repo, "memory/scars/a.md", "a-base\n")
    write(self_repo, "memory/itches/b.md", "b-base\n")
    write(self_repo, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha line\n")
    commit(self_repo, "seed")
    hook = install_pre_receive(self_repo)
    assert hook == self_repo / ".git" / "hooks" / "pre-receive"
    assert hook.is_file()
    assert git(self_repo, "config", "--get", "receive.denyCurrentBranch").stdout.strip() == (
        "updateInstead"
    )

    def shared(dest: Path, name: str) -> Path:
        git(None, "clone", "--shared", os.fspath(self_repo), os.fspath(dest))
        git(dest, "checkout", "-b", name)
        return dest

    left = shared(tmp_path / "a", "strand/a")
    right = shared(tmp_path / "b", "strand/b")
    write(left, "memory/scars/a.md", "a-base\nfrom-a\n")
    commit(left, "scar a")
    write(right, "memory/itches/b.md", "b-base\nfrom-b\n")
    commit(right, "itch b")
    assert send_to_self(left).status == "merged"
    assert send_to_self(right).status == "merged"
    assert (self_repo / "memory/scars/a.md").read_text(encoding="utf-8") == "a-base\nfrom-a\n"
    assert (self_repo / "memory/itches/b.md").read_text(encoding="utf-8") == "b-base\nfrom-b\n"
    assert git(self_repo, "status", "--porcelain").stdout == ""
    assert git(self_repo, "rev-parse", "HEAD").stdout.strip() == git(
        self_repo, "rev-parse", "main"
    ).stdout.strip()

    bad = shared(tmp_path / "c", "strand/c")
    write(
        bad,
        "core/immune.d/60-block",
        "#!/bin/sh\nprintf 'block-this-exact-line\\n' >&2\nexit 1\n",
        exe=True,
    )
    commit(bad, "a refusing check")
    head = git(self_repo, "rev-parse", "HEAD").stdout
    refused = send_to_self(bad)
    assert refused.status == "refused", refused
    assert "60-block: block-this-exact-line" in refused.stderr
    assert git(self_repo, "rev-parse", "HEAD").stdout == head
    assert git(self_repo, "status", "--porcelain").stdout == ""
    assert not (self_repo / "core/immune.d/60-block").exists()


def test_cli_send_self(tmp_path: Path) -> None:
    bare = published(tmp_path)
    strand = room(bare, tmp_path / "room", "strand/cli")
    write(strand, "memory/scars/a.md", "a-base\nfrom-cli\n")
    commit(strand, "cli")
    env = _env()
    env["PYTHONPATH"] = os.fspath(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", "send-self", "--room", os.fspath(strand)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == git(bare, "rev-parse", "main").stdout.strip()
    assert "from-cli" in show(bare, "memory/scars/a.md")
    unknown = subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", "not-a-verb"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert unknown.returncode == 2
