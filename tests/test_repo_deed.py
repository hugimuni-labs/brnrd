"""Tests for the repo-birth deed (#551) — the README seeded at every
home/knowledge repo birth, the named founding commit that replaced
``"brnrd: seed"``, and the link/init ceremony seams.

Same offline posture as ``test_home_link.py``: fake ``_run_gh``, real git
plumbing against local bare "remotes".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from brr import account, home_link, knowledge, repo_deed


def _cfg(home: Path) -> dict:
    return {"home.kind": "project", "home.path": str(home)}


def _bare_repo(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(path)], check=True)
    return path


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False,
    ).stdout


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ── the deed text itself ─────────────────────────────────────────────────


def test_deed_is_self_contained_and_states_the_four_facts():
    """What this is · who writes/reads · where it lives + cloud bounds ·
    how to leave — for both slots, with no reliance on hosted docs."""
    for slot in ("dominion", "knowledge"):
        text = repo_deed.deed_text(slot)
        flat = " ".join(text.split())  # wrap-safe substring matching
        assert "you own it" in flat
        assert "Who writes here, who reads it" in flat
        assert "private" in flat
        assert "brnrd's GitHub App never owns or holds it" in flat
        # bounded-mirror wording aligned with SECURITY.md
        assert "bounded render cache" in flat
        assert "14 days" in flat
        assert "SECURITY.md" in flat
        # exit is a git command, not a support ticket
        assert "How to leave" in flat
        assert "git clone" in flat
        assert "no support ticket" in flat
    assert repo_deed.deed_text("dominion") != repo_deed.deed_text("knowledge")


def test_founding_commit_message_names_what_was_founded():
    assert "dominion" in repo_deed.founding_commit_message("dominion")
    assert "knowledge" in repo_deed.founding_commit_message("knowledge")
    for slot in ("dominion", "knowledge"):
        assert "seed" not in repo_deed.founding_commit_message(slot)


# ── write/ensure mechanics ───────────────────────────────────────────────


def test_write_deed_never_overwrites_an_existing_readme(tmp_path):
    (tmp_path / "README.md").write_text("mine\n", encoding="utf-8")
    assert repo_deed.write_deed(tmp_path, "dominion") is False
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "mine\n"


def test_ensure_deed_on_unborn_head_makes_the_founding_commit(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    assert repo_deed.ensure_deed(tmp_path, "knowledge") is True
    log = _git_out(tmp_path, "log", "--format=%s")
    assert log.strip() == repo_deed.founding_commit_message("knowledge")


def test_ensure_deed_on_repo_with_history_commits_the_deed_separately(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "note.md").write_text("prior life\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "capture"], cwd=tmp_path, check=True)

    assert repo_deed.ensure_deed(tmp_path, "dominion") is True
    subjects = _git_out(tmp_path, "log", "--format=%s").splitlines()
    assert subjects[0] == "brnrd: add the deed README to this dominion repo"
    # deed is committed clean — nothing left stranded in the worktree
    assert not _git_out(tmp_path, "status", "--porcelain").strip()


# ── birth site: account.resolve_context (the silent XDG birth) ───────────


def test_fresh_home_birth_writes_and_founds_the_deed(tmp_path):
    home = tmp_path / "home"
    account.resolve_context(tmp_path / "repo", _cfg(home))

    deed = home / "README.md"
    assert deed.exists()
    assert "working memory" in deed.read_text(encoding="utf-8")
    log = _git_out(home, "log", "--format=%s")
    assert repo_deed.founding_commit_message("dominion") in log


def test_owner_deleting_the_deed_is_respected_on_later_resolves(tmp_path):
    home = tmp_path / "home"
    account.resolve_context(tmp_path / "repo", _cfg(home))
    (home / "README.md").unlink()

    account.resolve_context(tmp_path / "repo", _cfg(home))
    assert not (home / "README.md").exists()


# ── birth site: knowledge.ensure_checkout ────────────────────────────────


def test_home_knowledge_birth_commits_the_deed_and_the_checkout_carries_it(tmp_path):
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, check=True)

    checkout = knowledge.ensure_checkout(repo_root, _cfg(home))

    home_knowledge = home / "knowledge"
    assert (home_knowledge / "README.md").exists()
    # committed, not stranded — this repo receives updateInstead pushes
    assert not _git_out(home_knowledge, "status", "--porcelain").strip()
    assert repo_deed.founding_commit_message("knowledge") in _git_out(
        home_knowledge, "log", "--format=%s",
    )
    assert (checkout / "README.md").exists()


# ── link seam: the deed lands on the remote, foundingly named ────────────


def _fake_created_link(tmp_path, monkeypatch):
    dominion_remote = _bare_repo(tmp_path, "brnrd-home")
    knowledge_remote = _bare_repo(tmp_path, "brnrd-knowledge")
    remotes = {"brnrd-home": dominion_remote, "brnrd-knowledge": knowledge_remote}
    monkeypatch.setattr(home_link, "_clone_url", lambda owner, name: str(remotes[name]))

    def fake_run_gh(args):
        if args[:2] == ["auth", "status"]:
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            return _cp(1, stderr="not found")
        if args[:2] == ["repo", "create"]:
            name = args[2].split("/", 1)[1]
            return _cp(0, stdout=f"https://github.com/acme/{name}\n")
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)
    return dominion_remote, knowledge_remote


def test_link_pushes_a_deed_readme_to_every_created_repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    dominion_remote, knowledge_remote = _fake_created_link(tmp_path, monkeypatch)

    results = home_link.link_home(tmp_path / "repo", _cfg(home), owner="acme")

    assert {r.slot: r.action for r in results} == {
        "dominion": "created", "knowledge": "created",
    }
    for remote in (dominion_remote, knowledge_remote):
        files = _git_out(remote, "ls-tree", "--name-only", "main")
        assert "README.md" in files.splitlines()
    # the founding commits are named — no anonymous "brnrd: seed" anywhere
    for remote, slot in ((dominion_remote, "dominion"), (knowledge_remote, "knowledge")):
        log = _git_out(remote, "log", "--format=%s", "main")
        assert repo_deed.founding_commit_message(slot) in log
        assert "brnrd: seed" not in log


def test_link_pushes_the_deed_even_when_the_dominion_already_has_history(
    tmp_path, monkeypatch,
):
    """A pre-deed dominion (capture-net commits, no README) being linked
    still gets its deed committed and pushed — not stranded untracked."""
    home = tmp_path / "home"
    ctx = account.resolve_context(tmp_path / "repo", _cfg(home))
    # simulate an old home: no deed, but real history
    (home / "README.md").unlink()
    (home / "notes.md").write_text("old thought\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=ctx.dominion_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "capture"], cwd=ctx.dominion_repo, check=True)

    dominion_remote, _ = _fake_created_link(tmp_path, monkeypatch)
    home_link.link_home(tmp_path / "repo", _cfg(home), owner="acme")

    files = _git_out(dominion_remote, "ls-tree", "--name-only", "main")
    assert "README.md" in files.splitlines()


def test_link_never_replaces_an_owner_authored_readme(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ctx = account.resolve_context(tmp_path / "repo", _cfg(home))
    (home / "README.md").write_text("# my own words\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=ctx.dominion_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "mine"], cwd=ctx.dominion_repo, check=True)

    dominion_remote, _ = _fake_created_link(tmp_path, monkeypatch)
    home_link.link_home(tmp_path / "repo", _cfg(home), owner="acme")

    blob = _git_out(dominion_remote, "show", "main:README.md")
    assert blob == "# my own words\n"


# ── ceremony surfaces ────────────────────────────────────────────────────


def test_link_ceremony_states_owner_names_privacy_and_rename(capsys):
    from brr.cli import _print_link_ceremony

    _print_link_ceremony("acme", "brnrd-home", "brnrd-knowledge")
    out = capsys.readouterr().out
    assert "acme/brnrd-home" in out
    assert "acme/brnrd-knowledge" in out
    assert "private" in out
    assert "brnrd's App owns nothing" in out
    assert "--dominion-name" in out and "--knowledge-name" in out
    assert "README deed" in out


def test_init_narration_names_both_repos_without_linking(tmp_path, capsys, monkeypatch):
    from brr import adopt

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, check=True)
    home = tmp_path / "home"
    monkeypatch.setenv("BRNRD_HOME", str(home))

    adopt._narrate_home_repos(repo_root)
    out = capsys.readouterr().out
    assert str(home) in out
    assert "memory" in out and "knowledge" in out
    assert "README deed" in out
