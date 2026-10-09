"""20-labels, including widening citations, through a real hooked bare repo."""

from __future__ import annotations

import os
import subprocess

import pytest
from pathlib import Path

from brr.loom.runtime.merge import install_pre_receive, send_to_self

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
    return env


def git(repo: Path | None, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *([] if repo is None else ["-C", os.fspath(repo)]), *args]
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


SEED_TRAILERS = "seed\n\nLoom-Strand: seed\nLoom-Label: taint=0; audience=self\n"


def published(tmp: Path, *, enroll: str | None = None) -> tuple[Path, Path]:
    """A hooked bare self. The seed carries its own clean label, as init_self's does."""
    bare = tmp / "self.git"
    work = tmp / "work"
    git(None, "init", "--bare", "-b", "main", os.fspath(bare))
    git(None, "init", "-b", "main", os.fspath(work))
    copy_seed(work)
    if enroll:
        listing = work / "core" / "loom" / "people-commit"
        listing.write_text(listing.read_text(encoding="utf-8") + enroll + "\n", encoding="utf-8")
    write(work, "memory/scars/x.md", "base\n")
    write(work, "threads/inbox/README.md", README)
    commit(work, SEED_TRAILERS)
    git(work, "remote", "add", "origin", os.fspath(bare))
    install_pre_receive(bare)
    first = git(work, "push", "origin", "HEAD:main", check=False)
    assert first.returncode == 0, first.stderr + first.stdout
    return bare, work


def room(bare: Path, dest: Path, name: str) -> Path:
    git(None, "clone", os.fspath(bare), os.fspath(dest))
    git(dest, "checkout", "-b", name)
    return dest


def message(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "log", "-1", "--format=%B", rev).stdout


def test_label_checks_are_executable() -> None:
    assert (SEED / "core" / "immune.d" / "20-labels").stat().st_mode & 0o111


def test_a_clean_stamped_commit_lands(tmp_path: Path) -> None:
    bare, _work = published(tmp_path)
    strand = room(bare, tmp_path / "room", "strand/clean")
    write(strand, "memory/scars/x.md", "base\nclean\n")
    commit(strand, "a clean change")
    landed = send_to_self(strand)
    assert landed.status == "merged", landed
    text = message(bare, "main")
    assert "Loom-Label: taint=0; audience=self" in text
    assert "Loom-Strand: clean" in text
    assert "clean" in git(bare, "show", "main:memory/scars/x.md").stdout


def test_taint_without_widening_names_the_strand(tmp_path: Path) -> None:
    bare, _work = published(tmp_path)
    strand = room(bare, tmp_path / "room", "strand/stained")
    write(strand, "memory/scars/x.md", "base\nstained\n")
    commit(strand, "stained\n\nLoom-Label: taint=0; audience=self\n")
    (strand / "port").mkdir(exist_ok=True)
    (strand / "port" / "jack-errors.log").write_text("traceback\n", encoding="utf-8")
    refused = send_to_self(strand)
    assert refused.status == "refused", refused
    assert "taint=1 strand stained" in refused.stderr
    assert "Loom-Label: taint=1; audience=self" in message(strand)
    assert "taint=0" not in message(strand)


def test_taint_with_a_real_clause_lands(tmp_path: Path) -> None:
    bare, _work = published(tmp_path)
    strand = room(bare, tmp_path / "room", "strand/opened")
    write(strand, "people/ada/agreement.md", "Ada may read the log. {#read}\n")
    write(strand, "memory/scars/x.md", "base\nopened\n")
    commit(strand, "open a clause")
    (strand / "port").mkdir(exist_ok=True)
    (strand / "port" / "jack-errors.log").write_text("traceback\n", encoding="utf-8")
    landed = send_to_self(strand, widening="people/ada/agreement.md#read")
    assert landed.status == "merged", landed
    text = message(bare, "main")
    assert "Loom-Label: taint=1; audience=self" in text
    assert "Widening: people/ada/agreement.md#read" in text
    assert "Loom-Strand: opened" in text


def test_a_missing_clause_is_refused(tmp_path: Path) -> None:
    bare, _work = published(tmp_path)
    strand = room(bare, tmp_path / "room", "strand/missing")
    write(strand, "people/ada/agreement.md", "No marker on this page.\n")
    write(strand, "memory/scars/x.md", "base\nmissing\n")
    commit(strand, "cite a clause that is not there")
    refused = send_to_self(strand, widening="people/ada/agreement.md#missing")
    assert refused.status == "refused", refused
    assert "widening people/ada/agreement.md#missing" in refused.stderr


def test_an_empty_people_commit_refuses_an_unlabeled_push(tmp_path: Path) -> None:
    """Fail closed: a body pushing raw, with no trailer, must not skip the labels."""
    _bare, work = published(tmp_path)
    write(work, "memory/scars/x.md", "base\nstill unlabeled\n")
    commit(work, "no trailer")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode != 0
    assert "no Loom-Label" in proc.stderr


def _as(monkeypatch, name: str, email: str) -> None:
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", name)
        monkeypatch.setenv(f"GIT_{role}_EMAIL", email)


def test_an_unlabeled_stranger_is_refused_when_someone_is_enrolled(tmp_path: Path, monkeypatch) -> None:
    _bare, work = published(tmp_path, enroll="ada@example.com")
    _as(monkeypatch, "Bob", "bob@example.com")
    write(work, "memory/scars/x.md", "base\nbob\n")
    commit(work, "bob, unlabeled")
    refused = git(work, "push", "origin", "HEAD:main", check=False)
    assert refused.returncode != 0
    assert "no Loom-Label" in refused.stderr


def test_an_enrolled_committer_lands_without_a_trailer(tmp_path: Path, monkeypatch) -> None:
    _bare, work = published(tmp_path, enroll="ada@example.com")
    _as(monkeypatch, "Ada", "ada@example.com")
    write(work, "memory/scars/x.md", "base\nada\n")
    commit(work, "ada lands unlabeled")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_enrolling_yourself_in_the_same_push_is_refused(tmp_path: Path, monkeypatch) -> None:
    """An unlabeled commit must be on the old list too: a push can't enroll its own author."""
    _bare, work = published(tmp_path)
    _as(monkeypatch, "Mallory", "mallory@example.com")
    write(work, "core/loom/people-commit", "mallory@example.com\n")
    commit(work, "mallory enrolls herself")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode != 0
    assert "no Loom-Label" in proc.stderr


def test_a_label_with_no_self_in_the_audience_is_refused(tmp_path: Path) -> None:
    _bare, work = published(tmp_path)
    write(work, "memory/scars/x.md", "base\nother audience\n")
    commit(work, "narrow\n\nLoom-Label: taint=0; audience=other\n")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode != 0
    assert "audience missing self" in proc.stderr


def test_init_self_labels_the_seed_and_enrolls_the_person(tmp_path: Path) -> None:
    from brr.loom.runtime.selfrepo import AUTHOR_EMAIL, init_self
    init_self(tmp_path / "home", person="ada")
    self_dir = tmp_path / "home" / "self"
    body = git(self_dir, "log", "-1", "--format=%B").stdout
    assert "Loom-Label: taint=0; audience=self" in body
    listing = (self_dir / "core" / "loom" / "people-commit").read_text(encoding="utf-8")
    email = git(None, "config", "--global", "user.email").stdout.strip()
    assert email and email in listing.splitlines()
    assert AUTHOR_EMAIL not in listing


def test_body_identity_cannot_use_the_enrolled_person_to_push_unlabeled(tmp_path: Path, monkeypatch) -> None:
    from brr.loom.runtime.loom import _env as body_env
    from brr.loom.runtime.selfrepo import AUTHOR_EMAIL, AUTHOR_NAME

    bare, work = published(tmp_path, enroll="ada@example.com")
    _as(monkeypatch, "Ada", "ada@example.com")
    strand = room(bare, tmp_path / "room", "strand/body")
    write(strand, "memory/scars/x.md", "base\nbody\n")
    git(strand, "add", "-A")
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "body, unlabeled"],
        cwd=strand, env=body_env(strand), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    identity = git(strand, "log", "-1", "--format=%an <%ae> %cn <%ce>").stdout.strip()
    assert identity == f"{AUTHOR_NAME} <{AUTHOR_EMAIL}> {AUTHOR_NAME} <{AUTHOR_EMAIL}>"
    assert "ada@example.com" in git(work, "show", "HEAD:core/loom/people-commit").stdout
    refused = git(strand, "push", "origin", "HEAD:main", check=False)
    assert refused.returncode != 0
    assert "20-labels:" in refused.stderr
    assert "no Loom-Label" in refused.stderr


@pytest.mark.parametrize("label", ["", "taint=0; audience=self", "taint=1; audience=self"])
def test_every_widening_is_checked_even_with_a_valid_clause(tmp_path: Path, monkeypatch, label: str) -> None:
    _bare, work = published(tmp_path, enroll="ada@example.com")
    _as(monkeypatch, "Ada", "ada@example.com")
    write(work, "people/ada/agreement.md", "An allowed clause. {#allowed}\n")
    text = "mixed citations\n\nWidening: people/ada/agreement.md#allowed\nWidening: people/ada/agreement.md#missing\n"
    if label:
        text += f"Loom-Label: {label}\n"
    commit(work, text)
    refused = git(work, "push", "origin", "HEAD:main", check=False)
    assert refused.returncode != 0
    assert "20-labels:" in refused.stderr
    assert "widening people/ada/agreement.md#missing" in refused.stderr
