"""loom-readme: fake body lands both sides, an unparseable result stops."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from brr.loom.runtime.merge import install_pre_receive, send_to_self
from brr.loom.runtime.merge_driver import install_merge_driver, merge

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "src" / "brr" / "loom" / "runtime" / "seed"
_DISCOVERY = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX",
    "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}
README = "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha line\n"


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _DISCOVERY}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_EDITOR"] = "true"
    env.setdefault("LOOM_MERGE_BODY", "fake")
    return env


def git(repo: Path | None, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *([] if repo is None else ["-C", os.fspath(repo)]), *args]
    proc = subprocess.run(cmd, env=_env(), capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise AssertionError(f"{cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def copy_seed(repo: Path) -> None:
    import shutil
    for src in SEED.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(SEED)
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if src.stat().st_mode & 0o111:
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
    write(work, "threads/inbox/README.md", README)
    commit(work, "seed")
    git(work, "remote", "add", "origin", os.fspath(bare))
    install_pre_receive(bare)
    first = git(work, "push", "origin", "HEAD:main", check=False)
    assert first.returncode == 0, first.stderr + first.stdout
    return bare


def room(bare: Path, dest: Path, name: str) -> Path:
    git(None, "clone", os.fspath(bare), os.fspath(dest))
    git(dest, "checkout", "-b", name)
    install_merge_driver(dest)
    return dest


def test_unparseable_output_exits_1_and_does_not_write(tmp_path: Path) -> None:
    ours = tmp_path / "ours"
    theirs = tmp_path / "theirs"
    ours.write_text("not a readme\n", encoding="utf-8")
    theirs.write_text(README, encoding="utf-8")
    assert merge("", os.fspath(ours), os.fspath(theirs), "threads/inbox/README.md") == 1
    assert ours.read_text(encoding="utf-8") == "not a readme\n"


def test_both_readme_edits_land(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_MERGE_BODY", "fake")
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/a")
    right = room(bare, tmp_path / "b", "strand/b")
    write(left, "threads/inbox/README.md", README.replace("alpha line", "alpha from a"))
    commit(left, "readme a")
    write(right, "threads/inbox/README.md", README.replace("alpha line", "alpha from b"))
    commit(right, "readme b")
    assert send_to_self(left).status == "merged"
    landed = send_to_self(right)
    assert landed.status == "merged", landed
    text = git(bare, "show", "main:threads/inbox/README.md").stdout
    assert "alpha from a" in text
    assert "alpha from b" in text
    tip = git(bare, "log", "-1", "--format=%B", "main").stdout
    assert "Loom-Label: taint=0; audience=self" in tip


def test_an_unparseable_readme_stops_the_rebase(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_MERGE_BODY", "fake")
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/a")
    right = room(bare, tmp_path / "b", "strand/b")
    write(left, "memory/stance.md", "# Stance\n\nfrom-a\n")
    commit(left, "stance a")
    write(right, "memory/stance.md", "# Stance\n\nfrom-b\n")
    commit(right, "stance b")
    assert send_to_self(left).status == "merged"
    stopped = send_to_self(right)
    assert stopped.status == "stopped", stopped
    assert "memory/stance.md" in stopped.files


def test_the_merged_commit_carries_the_join(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_MERGE_BODY", "fake")
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/stained")
    right = room(bare, tmp_path / "b", "strand/clean")
    write(left, "people/ada/agreement.md", "Ada may read the log. {#read}\n")
    write(left, "memory/scars/x.md", "base\nstained\n")
    commit(left, "stain")
    (left / "port").mkdir(exist_ok=True)
    (left / "port" / "jack-errors.log").write_text("traceback\n", encoding="utf-8")
    opened = send_to_self(left, widening="people/ada/agreement.md#read")
    assert opened.status == "merged", opened
    write(right, "memory/scars/a.md", "from-clean\n")
    commit(right, "clean follows")
    landed = send_to_self(right, widening="people/ada/agreement.md#read")
    assert landed.status == "merged", landed
    text = git(bare, "log", "-1", "--format=%B", "main").stdout
    assert "Loom-Strand: clean" in text
    assert "Loom-Label: taint=1; audience=self" in text


@pytest.mark.skipif(os.environ.get("LOOM_DEMO") != "1", reason="set LOOM_DEMO=1 to run haiku once")
def test_haiku_merges_two_readme_edits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_MERGE_BODY", "claude-haiku")
    bare = published(tmp_path)
    left = room(bare, tmp_path / "a", "strand/a")
    right = room(bare, tmp_path / "b", "strand/b")
    write(left, "threads/inbox/README.md", README.replace("alpha line", "alpha from a"))
    commit(left, "readme a")
    write(right, "threads/inbox/README.md", README.replace("alpha line", "alpha from b"))
    commit(right, "readme b")
    assert send_to_self(left).status == "merged"
    landed = send_to_self(right)
    assert landed.status == "merged", landed
    text = git(bare, "show", "main:threads/inbox/README.md").stdout
    Path("/tmp/loom-4b-demo-readme.md").write_text(text, encoding="utf-8")
    assert "alpha from a" in text
    assert "alpha from b" in text
    assert text.startswith("---\n")
