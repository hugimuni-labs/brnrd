"""Tests for ``brr.home_link`` — the one-question git durability opt-in.

No network: every ``gh`` call goes through a fake ``home_link._run_gh``,
and every "GitHub" push target is a real local ``git init --bare`` repo
reached through a monkeypatched ``home_link._clone_url`` — so pushes are
exercised with real git plumbing, offline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import account, gitops, home_link


def _cfg(home: Path) -> dict:
    return {"home.kind": "project", "home.path": str(home)}


def _bare_repo(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(path)], check=True)
    return path


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _fail_if_called(*_a, **_kw):  # pragma: no cover - assertion helper
    raise AssertionError("gh should not have been called")


# ── idempotent re-run ───────────────────────────────────────────────────


def test_already_linked_repos_need_no_gh_at_all(tmp_path, monkeypatch):
    """A fully-linked home re-run touches no network and asks nothing of gh."""
    monkeypatch.setattr(home_link, "_run_gh", _fail_if_called)

    home = tmp_path / "home"
    ctx = account.resolve_context(tmp_path / "repo", _cfg(home))
    knowledge_root = account.knowledge_path(ctx)
    knowledge_root.mkdir(parents=True, exist_ok=True)
    home_link._ensure_git_repo(knowledge_root)

    dominion_remote = _bare_repo(tmp_path, "dominion-remote")
    knowledge_remote = _bare_repo(tmp_path, "knowledge-remote")
    subprocess.run(["git", "remote", "add", "origin", str(dominion_remote)],
                    cwd=ctx.dominion_repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(knowledge_remote)],
                    cwd=knowledge_root, check=True)

    results = home_link.link_home(tmp_path / "repo", _cfg(home))

    assert {r.slot: r.action for r in results} == {
        "dominion": "already-linked",
        "knowledge": "already-linked",
    }
    assert all(not r.pushed for r in results)
    assert {r.slot: r.remote_url for r in results} == {
        "dominion": str(dominion_remote),
        "knowledge": str(knowledge_remote),
    }


# ── adopt-existing-repo path ────────────────────────────────────────────


def test_adopts_existing_github_repo_when_gh_repo_view_finds_one(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    ctx_probe = account.resolve_context(repo_root, _cfg(home), create=False)
    del ctx_probe  # just to keep the label resolution off the hot path

    dominion_remote = _bare_repo(tmp_path, "brnrd-home")
    knowledge_remote = _bare_repo(tmp_path, "brnrd-knowledge")
    remotes = {"brnrd-home": dominion_remote, "brnrd-knowledge": knowledge_remote}
    monkeypatch.setattr(home_link, "_clone_url", lambda owner, name: str(remotes[name]))

    calls = []

    def fake_run_gh(args):
        calls.append(list(args))
        if args[:2] == ["auth", "status"]:
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            name = args[2].split("/", 1)[1]
            return _cp(0, stdout=f'{{"url": "https://github.com/acme/{name}", '
                                 f'"visibility": "PRIVATE"}}\n')
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    results = home_link.link_home(repo_root, _cfg(home), owner="acme")

    assert {r.slot: r.action for r in results} == {"dominion": "adopted", "knowledge": "adopted"}
    assert all(r.pushed for r in results)
    assert {r.slot: r.remote_url for r in results} == {
        "dominion": "https://github.com/acme/brnrd-home",
        "knowledge": "https://github.com/acme/brnrd-knowledge",
    }
    # each remote actually received a push
    for remote in (dominion_remote, knowledge_remote):
        log = subprocess.run(
            ["git", "log", "--oneline", "main"], cwd=remote,
            capture_output=True, text=True, check=True,
        )
        assert log.stdout.strip()
    # owner supplied explicitly ⇒ no `gh api user` lookup
    assert all(c[:2] != ["api", "user"] for c in calls)


# ── create path ──────────────────────────────────────────────────────────


def test_creates_a_private_repo_when_none_exists(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"

    created_remote = _bare_repo(tmp_path, "brnrd-home")
    monkeypatch.setattr(home_link, "_clone_url", lambda owner, name: str(created_remote))
    # knowledge slot: point at a second bare repo too, so both links succeed
    knowledge_remote = _bare_repo(tmp_path, "brnrd-knowledge")

    def clone_url(owner, name):
        return str(created_remote if name == "brnrd-home" else knowledge_remote)

    monkeypatch.setattr(home_link, "_clone_url", clone_url)

    create_calls = []

    def fake_run_gh(args):
        if args[:2] == ["auth", "status"]:
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            return _cp(1, stderr="not found")
        if args[:2] == ["repo", "create"]:
            create_calls.append(list(args))
            name = args[2].split("/", 1)[1]
            return _cp(0, stdout=f"https://github.com/acme/{name}\n")
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    results = home_link.link_home(repo_root, _cfg(home), owner="acme")

    assert {r.slot: r.action for r in results} == {"dominion": "created", "knowledge": "created"}
    assert all(r.pushed for r in results)
    assert len(create_calls) == 2
    assert all("--private" in c for c in create_calls), "created repos must always be private"
    assert all("--public" not in c for c in create_calls)


# ── gh absent / unauthenticated ─────────────────────────────────────────


def test_missing_gh_raises_actionable_error_only_when_a_repo_needs_it(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(home_link.shutil, "which", lambda _name: None)

    with pytest.raises(home_link.HomeLinkError, match="gh .GitHub CLI. is not installed"):
        home_link.link_home(repo_root, _cfg(home))


def test_unauthenticated_gh_raises_actionable_error(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(home_link.shutil, "which", lambda _name: "/usr/bin/gh")

    def fake_run_gh(args):
        assert args == ["auth", "status"]
        return _cp(1, stderr="not logged in")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    with pytest.raises(home_link.HomeLinkError, match="not authenticated"):
        home_link.link_home(repo_root, _cfg(home))


# ── push failure: no half-wired silence ─────────────────────────────────


def test_push_failure_names_the_repo_and_leaves_origin_wired(tmp_path, monkeypatch):
    """create/adopt succeeding but the push then failing must say exactly that."""
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"

    dominion_remote = _bare_repo(tmp_path, "brnrd-home")
    bogus_knowledge_target = tmp_path / "does-not-exist-as-a-repo"

    def clone_url(owner, name):
        return str(dominion_remote if name == "brnrd-home" else bogus_knowledge_target)

    monkeypatch.setattr(home_link, "_clone_url", clone_url)
    monkeypatch.setattr(home_link, "_run_gh", lambda args: (
        _cp(0) if args[:2] == ["auth", "status"]
        else _cp(0, stdout=f'{{"url": "https://github.com/acme/'
                          f'{args[2].split("/",1)[1]}", "visibility": "PRIVATE"}}\n')
    ))

    seen = []
    with pytest.raises(home_link.HomeLinkError) as excinfo:
        home_link.link_home(repo_root, _cfg(home), owner="acme", on_result=seen.append)

    assert "knowledge" in str(excinfo.value)
    assert "push" in str(excinfo.value).lower()
    # dominion succeeded and was reported before the knowledge failure surfaced
    assert [r.slot for r in seen] == ["dominion"]
    ctx = account.resolve_context(repo_root, _cfg(home), create=False)
    assert gitops.default_remote(ctx.dominion_repo) == "origin"
    knowledge_root = account.knowledge_path(ctx)
    # origin is left wired on the failed repo too — half-wired, but said plainly
    assert gitops.default_remote(knowledge_root) == "origin"


# ── owner resolution ─────────────────────────────────────────────────────


def test_explicit_owner_skips_gh_api_user_lookup(monkeypatch):
    assert home_link.resolve_owner("explicit-owner") == "explicit-owner"


def test_resolve_owner_shells_out_to_gh_api_user(monkeypatch):
    monkeypatch.setattr(
        home_link, "_run_gh", lambda args: _cp(0, stdout="octocat\n")
        if args == ["api", "user", "-q", ".login"] else _fail_if_called(),
    )
    assert home_link.resolve_owner(None) == "octocat"


def test_resolve_owner_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(home_link, "_run_gh", lambda args: _cp(1, stderr="boom"))
    with pytest.raises(home_link.HomeLinkError, match="pass --owner"):
        home_link.resolve_owner(None)


def test_refuses_to_adopt_a_public_repo(tmp_path, monkeypatch):
    """The create path was careful (`--private`); the *adopt* path was never
    asked. Wiring origin to an existing public `brnrd-home` would push the
    agent's memory onto a public profile — the same shape as the overflow gist
    that shipped `--public` while the design page argued for data-minimization."""
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(home_link, "_clone_url", lambda owner, name: "unused")

    def fake_run_gh(args):
        if args[:2] == ["auth", "status"]:
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            name = args[2].split("/", 1)[1]
            return _cp(0, stdout=f'{{"url": "https://github.com/acme/{name}", '
                                 f'"visibility": "PUBLIC"}}\n')
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    with pytest.raises(home_link.HomeLinkError) as excinfo:
        home_link.link_home(repo_root, _cfg(home), owner="acme")

    message = str(excinfo.value)
    assert "public" in message
    assert "isn't private" in message
    # And nothing was wired: a refusal must leave no half-configured remote.
    ctx = account.resolve_context(repo_root, _cfg(home), create=False)
    assert gitops.default_remote(ctx.dominion_repo) is None


def test_unreadable_visibility_is_refused_too(tmp_path, monkeypatch):
    """Unknown is not a licence. Refusing is cheap and recoverable; a public
    push of agent memory is neither."""
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"

    def fake_run_gh(args):
        if args[:2] == ["auth", "status"]:
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            return _cp(0, stdout='{"url": "https://github.com/acme/brnrd-home"}\n')
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    with pytest.raises(home_link.HomeLinkError):
        home_link.link_home(repo_root, _cfg(home), owner="acme")
