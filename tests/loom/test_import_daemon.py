"""The repeatable import is allowlisted, atomic on refusal, and immune-checked."""

from pathlib import Path

import pytest

from brr.loom.runtime.__main__ import main
from brr.loom.runtime.import_daemon import import_daemon
from brr.loom.runtime.selfrepo import (
    AUTHOR_EMAIL, AUTHOR_NAME, SelfError, _commit, git, init_self,
)


@pytest.fixture
def trees(tmp_path):
    home, account = tmp_path / "loom", tmp_path / "account"
    init_self(home, person="arseni")
    for name in ("notebook", "position", "playbook", "pitfalls"):
        path = account / "home" / "dominion" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\nResident memory.\n")
    path = account / "home" / "surface" / "workflow.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Our agreement\nSigned clauses.\n")
    return home, account


def head(home):
    return git(home / "self", "rev-parse", "HEAD").stdout.strip()


def test_copies_only_named_files_and_preserves_stance(trees):
    home, account = trees
    repo = home / "self"
    stance = repo / "memory" / "stance.md"
    stance.write_text("# Handwritten stance\nKeep this.\n")
    git(repo, "add", "--", "memory/stance.md")
    _commit(repo, "Handwritten stance")
    for rel in ("home/hearth/secret.md", "home/dominion/private.env",
                "home/dominion/session.cookie", "home/surface/credentials/token",
                "home/security.config", "home/dominion/extra.md",
                "home/dominion/stance.md"):
        path = account / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NEVER COPY ME\n")
    before = int(git(repo, "rev-list", "--count", "HEAD").stdout)
    result = import_daemon(home, account, "arseni")
    assert "core/identity.md" in result
    identity = Path(__file__).resolve().parents[2] / "src/brr/prompts/identity-core.md"
    assert (repo / "core/identity.md").read_bytes() == identity.read_bytes()
    for name in ("notebook", "position", "playbook", "pitfalls"):
        assert (repo / f"memory/{name}.md").read_bytes() == (
            account / f"home/dominion/{name}.md").read_bytes()
    assert (repo / "people/arseni/agreement.md").read_bytes() == (
        account / "home/surface/workflow.md").read_bytes()
    assert stance.read_text() == "# Handwritten stance\nKeep this.\n"
    assert not any(b"NEVER COPY ME" in path.read_bytes()
                   for path in repo.rglob("*") if path.is_file() and ".git" not in path.parts)
    assert int(git(repo, "rev-list", "--count", "HEAD").stdout) == before + 1
    assert git(repo, "status", "--porcelain").stdout == ""
    assert git(repo, "remote").stdout == ""
    assert git(repo, "log", "-1", "--format=%an <%ae>").stdout.strip() == f"{AUTHOR_NAME} <{AUTHOR_EMAIL}>"


def test_rerun_is_noop_then_one_commit_names_only_changed_files(trees):
    home, account = trees
    import_daemon(home, account, "arseni")
    before = head(home)
    assert "unchanged" in import_daemon(home, account, "arseni")
    assert head(home) == before
    (account / "home/dominion/notebook.md").write_text("A new note.\n")
    (account / "home/surface/workflow.md").write_text("A revised agreement.\n")
    import_daemon(home, account, "arseni")
    repo = home / "self"
    assert git(repo, "rev-parse", "HEAD^").stdout.strip() == before
    changed = git(repo, "diff", "--name-only", before, "HEAD").stdout.splitlines()
    assert changed == ["memory/notebook.md", "people/arseni/agreement.md"]
    message = git(repo, "log", "-1", "--format=%B").stdout
    assert all(name in message for name in changed)
    assert "core/identity.md" not in message


def test_cli_uses_home_author_despite_parent_git_pin(trees, monkeypatch, capsys):
    home, account = trees
    (home / "loom").mkdir()
    (home / "loom/config.toml").write_text('author = "Home Author <home@example.test>"\n')
    monkeypatch.setenv("GIT_DIR", "/no-such-parent")
    monkeypatch.setenv("GIT_WORK_TREE", "/no-such-worktree")
    assert main(["import-daemon", "--home", str(home), "--account", str(account),
                 "--person", "arseni"]) == 0
    assert "import-daemon:" in capsys.readouterr().out
    assert git(home / "self", "log", "-1", "--format=%an <%ae>|%cn <%ce>").stdout.strip() == (
        "Home Author <home@example.test>|Home Author <home@example.test>")


def test_immune_refusal_does_not_change_self(trees):
    home, account = trees
    repo = home / "self"
    guard = repo / "core/immune.d/40-identity"
    guard.write_text('#!/bin/sh\necho "identity requires review" >&2\nexit 1\n')
    guard.chmod(0o755)
    git(repo, "add", "--", "core/immune.d/40-identity")
    _commit(repo, "Guard identity")
    before = head(home)
    identity = (repo / "core/identity.md").read_bytes()
    with pytest.raises(SelfError, match="identity requires review"):
        import_daemon(home, account, "arseni")
    assert head(home) == before
    assert (repo / "core/identity.md").read_bytes() == identity
    assert git(repo, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize("rel", ["home/dominion/notebook.md", "home/dominion"])
def test_symlink_source_refused(trees, tmp_path, rel):
    home, account = trees
    path = account / rel
    moved = tmp_path / "secret"
    path.rename(moved)
    path.symlink_to(moved)
    before = head(home)
    with pytest.raises(SelfError, match="symlink refused"):
        import_daemon(home, account, "arseni")
    assert head(home) == before


def test_missing_source_is_not_a_partial_import(trees, capsys):
    home, account = trees
    (account / "home/dominion/position.md").unlink()
    before = head(home)
    assert main(["import-daemon", "--home", str(home), "--account", str(account),
                 "--person", "arseni"]) == 1
    assert "position.md" in capsys.readouterr().err
    assert head(home) == before
    assert git(home / "self", "status", "--porcelain").stdout == ""


def test_dirty_self_refused_without_staging_other_work(trees):
    home, account = trees
    (home / "self/memory/stance.md").write_text("An unfinished thought.\n")
    before = head(home)
    with pytest.raises(SelfError, match="self is dirty"):
        import_daemon(home, account, "arseni")
    assert head(home) == before
    assert git(home / "self", "diff", "--cached", "--name-only").stdout == ""


@pytest.mark.parametrize("person", ["..", "../hearth", "a/b"])
def test_person_must_be_one_segment(trees, person):
    home, account = trees
    with pytest.raises(SelfError, match="single path segment"):
        import_daemon(home, account, person)
