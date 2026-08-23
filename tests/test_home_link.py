"""Tests for ``brr.home_link`` — the one-question git durability opt-in.

No network: every ``gh`` call goes through a fake ``home_link._run_gh``,
and every "GitHub" push target is a real local ``git init --bare`` repo
reached through a monkeypatched ``home_link._clone_url`` — so pushes are
exercised with real git plumbing, offline.
"""

from __future__ import annotations

import os
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


def _strip_ambient_git_url_rewrites(monkeypatch):
    """A daemon-hosted host may carry its own ``GIT_CONFIG_COUNT`` /
    ``GIT_CONFIG_KEY_N`` / ``GIT_CONFIG_VALUE_N`` pairs (an ``insteadOf`` +
    credential rewrite for *brr's own* git operations elsewhere) that the
    hermetic fixture in ``conftest.py`` doesn't scrub — irrelevant to what
    this module's own logic does, but it would silently rewrite a
    ``github.com``-shaped fixture URL out from under a test that specifically
    exists to check what scheme that URL carries. Strip it so this test only
    ever sees the literal URL it wrote.
    """
    try:
        count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    for i in range(count):
        monkeypatch.delenv(f"GIT_CONFIG_KEY_{i}", raising=False)
        monkeypatch.delenv(f"GIT_CONFIG_VALUE_{i}", raising=False)


# ── idempotent re-run ───────────────────────────────────────────────────


def test_already_linked_and_pushed_repos_need_no_gh_and_no_further_push(tmp_path, monkeypatch):
    """A healthy, already-pushed home re-run touches no network and asks
    nothing of gh: the local upstream-tracking record a prior `git push -u`
    left behind is enough (#1422 — no `git ls-remote` probe needed either)."""
    monkeypatch.setattr(home_link, "_run_gh", _fail_if_called)

    home = tmp_path / "home"
    ctx = account.resolve_context(tmp_path / "repo", _cfg(home))
    knowledge_root = account.knowledge_path(ctx)
    knowledge_root.mkdir(parents=True, exist_ok=True)
    home_link._ensure_git_repo(knowledge_root)
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "founding"],
                    cwd=knowledge_root, check=True,
                    env=home_link.gitops.bot_identity_env(home_link._noninteractive_git_env()))

    dominion_remote = _bare_repo(tmp_path, "dominion-remote")
    knowledge_remote = _bare_repo(tmp_path, "knowledge-remote")
    for repo_path, remote in ((ctx.dominion_repo, dominion_remote), (knowledge_root, knowledge_remote)):
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo_path, check=True)
        subprocess.run(["git", "push", "-u", "origin", "HEAD:refs/heads/main"],
                        cwd=repo_path, check=True, capture_output=True)

    push_calls = []
    real_run = subprocess.run

    def spy_run(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "push"]:
            push_calls.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(home_link.subprocess, "run", spy_run)

    results = home_link.link_home(tmp_path / "repo", _cfg(home))

    assert {r.slot: r.action for r in results} == {
        "dominion": "already-linked",
        "knowledge": "already-linked",
    }
    assert all(r.pushed for r in results)
    assert {r.slot: r.remote_url for r in results} == {
        "dominion": str(dominion_remote),
        "knowledge": str(knowledge_remote),
    }
    assert push_calls == [], "an already-pushed repo must not push again"


def test_already_linked_but_never_pushed_retries_the_push(tmp_path, monkeypatch):
    """The sticky bug (#1422): origin wired (e.g. by a prior run whose push
    failed) but nothing ever reached the remote — must retry, not report
    `already-linked, pushed=False` forever."""
    home = tmp_path / "home"
    ctx = account.resolve_context(tmp_path / "repo", _cfg(home))
    knowledge_root = account.knowledge_path(ctx)
    knowledge_root.mkdir(parents=True, exist_ok=True)
    home_link._ensure_git_repo(knowledge_root)
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "founding"],
                    cwd=knowledge_root, check=True,
                    env=home_link.gitops.bot_identity_env(home_link._noninteractive_git_env()))

    dominion_remote = _bare_repo(tmp_path, "dominion-remote")
    knowledge_remote = _bare_repo(tmp_path, "knowledge-remote")
    subprocess.run(["git", "remote", "add", "origin", str(dominion_remote)],
                    cwd=ctx.dominion_repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(knowledge_remote)],
                    cwd=knowledge_root, check=True)

    def fake_run_gh(args):
        if args[:2] in (["auth", "status"], ["auth", "setup-git"]):
            return _cp(0)
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    results = home_link.link_home(tmp_path / "repo", _cfg(home))

    assert {r.slot: r.action for r in results} == {
        "dominion": "already-linked",
        "knowledge": "already-linked",
    }
    assert all(r.pushed for r in results), "never-pushed-but-wired repos must be pushed on this run"
    for remote in (dominion_remote, knowledge_remote):
        log = subprocess.run(
            ["git", "log", "--oneline", "main"], cwd=remote,
            capture_output=True, text=True, check=True,
        )
        assert log.stdout.strip(), f"{remote} should have actually received the retried push"
    assert account.gitops.has_pushed_upstream(ctx.dominion_repo) is True
    assert account.gitops.has_pushed_upstream(knowledge_root) is True


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
        if args[:2] == ["auth", "setup-git"]:
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
        if args[:2] == ["auth", "setup-git"]:
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

    def fake_run_gh(args):
        if args[:2] in (["auth", "status"], ["auth", "setup-git"]):
            return _cp(0)
        name = args[2].split("/", 1)[1]
        return _cp(0, stdout=f'{{"url": "https://github.com/acme/{name}", '
                             f'"visibility": "PRIVATE"}}\n')

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

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

    # ── the retry (#1422): fix what made the push unreachable, run again
    # through the real caller (no `_link_one` involved this run — both
    # slots now hit the `existing_remote` branch) — the second run must
    # actually push, not report `already-linked, pushed=False` forever.
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(bogus_knowledge_target)], check=True,
    )

    second_results = home_link.link_home(repo_root, _cfg(home))

    assert {r.slot: r.action for r in second_results} == {
        "dominion": "already-linked",
        "knowledge": "already-linked",
    }
    assert all(r.pushed for r in second_results), "the second run must report the retried push"
    for remote in (dominion_remote, bogus_knowledge_target):
        log = subprocess.run(
            ["git", "log", "--oneline", "main"], cwd=remote,
            capture_output=True, text=True, check=True,
        )
        assert log.stdout.strip()


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


# ── identity detection — init states it, without an account ──────────────
#
# `detect_identity()` is the caller `init` was missing: same resolution as
# `resolve_owner`, but never raises. Callers that only want to *state* the
# identity (never require it) get a plain `str | None` back.


def test_detect_identity_returns_the_resolved_login(monkeypatch):
    monkeypatch.setattr(home_link, "gh_available", lambda: True)
    monkeypatch.setattr(
        home_link, "_run_gh", lambda args: _cp(0, stdout="octocat\n")
        if args == ["api", "user", "-q", ".login"] else _fail_if_called(),
    )
    assert home_link.detect_identity() == "octocat"


def test_detect_identity_none_when_gh_is_not_on_path(monkeypatch):
    monkeypatch.setattr(home_link, "gh_available", lambda: False)
    monkeypatch.setattr(home_link, "_run_gh", _fail_if_called)
    assert home_link.detect_identity() is None


def test_detect_identity_none_when_gh_is_unauthenticated(monkeypatch):
    monkeypatch.setattr(home_link, "gh_available", lambda: True)
    monkeypatch.setattr(home_link, "_run_gh", lambda args: _cp(1, stderr="not logged in"))
    assert home_link.detect_identity() is None


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


# ── #1241: never let git prompt, and prefer the transport that already
# works ───────────────────────────────────────────────────────────────


def test_noninteractive_git_env_disables_every_prompt_path(monkeypatch):
    """Unit-level: the env every git subprocess in this module gets.

    An unset ``GIT_TERMINAL_PROMPT`` makes git write a credential prompt
    straight to the real ``/dev/tty`` — invisible to ``capture_output=True``
    — and hang for a human who isn't watching (the #1241 trace). This
    checks the fix directly: the HTTPS side (``GIT_TERMINAL_PROMPT`` /
    ``GIT_ASKPASS``) and the SSH side (``GIT_SSH_COMMAND``'s
    ``BatchMode=yes``) are both closed, and the discovery-override pin a
    strand run inherits (#703) does not leak into a call that names its
    own repo via ``cwd=``.
    """
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setenv("GIT_DIR", "/somewhere/pinned.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere/pinned")

    env = home_link._noninteractive_git_env()

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"]  # a real, resolvable no-op command
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env


def test_push_subprocess_actually_carries_the_noninteractive_env(tmp_path, monkeypatch):
    """Through ``link_home``'s own path: the real ``git push`` call this
    module makes — not just some git call somewhere — must carry the env
    that keeps it from ever reaching for a tty. A regression here is
    exactly how #1241 happened: the fix living in a helper nobody calls
    from the actual push site."""
    captured_envs = []
    real_run = subprocess.run

    def spy_run(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "push"]:
            captured_envs.append(kwargs.get("env"))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(home_link.subprocess, "run", spy_run)

    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    dominion_remote = _bare_repo(tmp_path, "brnrd-home")
    knowledge_remote = _bare_repo(tmp_path, "brnrd-knowledge")

    def clone_url(owner, name):
        return str(dominion_remote if name == "brnrd-home" else knowledge_remote)

    monkeypatch.setattr(home_link, "_clone_url", clone_url)

    def fake_run_gh(args):
        if args[:2] in (["auth", "status"], ["auth", "setup-git"]):
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            return _cp(1, stderr="not found")
        if args[:2] == ["repo", "create"]:
            name = args[2].split("/", 1)[1]
            return _cp(0, stdout=f"https://github.com/acme/{name}\n")
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    results = home_link.link_home(repo_root, _cfg(home), owner="acme")

    assert all(r.pushed for r in results)
    assert len(captured_envs) == 2, "both repos should have pushed"
    for env in captured_envs:
        assert env is not None, "git push ran with the ambient env — a tty prompt could reach it"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env.get("GIT_ASKPASS")


def test_explicit_ssh_override_uses_ssh_home_remote_and_skips_gh_setup_git(tmp_path, monkeypatch):
    """#1241: when the project's own origin is SSH, mint the home remote as
    SSH too — the trace's machine had a working SSH identity while HTTPS
    died. gh is still needed for repo metadata (view/create — that's a
    separate API, not a git transport), but `gh auth setup-git` exists only
    to fix up the HTTPS git transport, so the SSH path must never call it.
    """
    _strip_ambient_git_url_rewrites(monkeypatch)
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_root)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:acme/upstream.git"],
        cwd=repo_root, check=True, env=gitops.explicit_repo_env(),
    )

    dominion_remote = _bare_repo(tmp_path, "brnrd-home")
    knowledge_remote = _bare_repo(tmp_path, "brnrd-knowledge")
    ssh_targets = {"brnrd-home": dominion_remote, "brnrd-knowledge": knowledge_remote}
    monkeypatch.setattr(home_link, "_clone_url_ssh", lambda owner, name: str(ssh_targets[name]))
    monkeypatch.setattr(home_link, "_clone_url", _fail_if_called)

    calls = []

    def fake_run_gh(args):
        calls.append(list(args))
        if args[:2] == ["auth", "status"]:
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            return _cp(1, stderr="not found")
        if args[:2] == ["repo", "create"]:
            name = args[2].split("/", 1)[1]
            return _cp(0, stdout=f"https://github.com/acme/{name}\n")
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    results = home_link.link_home(repo_root, _cfg(home), owner="acme", ssh=True)

    assert all(r.pushed for r in results)
    assert all(c[:2] != ["auth", "setup-git"] for c in calls), (
        "the SSH path must never touch gh auth setup-git — that's an HTTPS-only fixup"
    )
    for remote in (dominion_remote, knowledge_remote):
        log = subprocess.run(
            ["git", "log", "--oneline", "main"], cwd=remote,
            capture_output=True, text=True, check=True, env=gitops.explicit_repo_env(),
        )
        assert log.stdout.strip()


def test_https_origin_tries_gh_auth_setup_git_once_before_pushing(tmp_path, monkeypatch):
    """The mirror of the SSH test: an HTTPS (or absent) project origin goes
    through `_clone_url` (never `_clone_url_ssh`) and `gh auth setup-git`
    fires exactly once across both repos — not per repo, not per push."""
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"

    dominion_remote = _bare_repo(tmp_path, "brnrd-home")
    knowledge_remote = _bare_repo(tmp_path, "brnrd-knowledge")

    def clone_url(owner, name):
        return str(dominion_remote if name == "brnrd-home" else knowledge_remote)

    monkeypatch.setattr(home_link, "_clone_url", clone_url)
    monkeypatch.setattr(home_link, "_clone_url_ssh", _fail_if_called)

    setup_git_calls = []

    def fake_run_gh(args):
        if args[:2] == ["auth", "status"]:
            return _cp(0)
        if args[:2] == ["auth", "setup-git"]:
            setup_git_calls.append(list(args))
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            return _cp(1, stderr="not found")
        if args[:2] == ["repo", "create"]:
            name = args[2].split("/", 1)[1]
            return _cp(0, stdout=f"https://github.com/acme/{name}\n")
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    results = home_link.link_home(repo_root, _cfg(home), owner="acme")

    assert all(r.pushed for r in results)
    assert len(setup_git_calls) == 1


def test_push_failure_message_carries_stderr_and_a_specific_remedy(tmp_path, monkeypatch):
    """#1241's other half: 'origin is wired; re-run once fixed' alone was
    the bug. The message must name the actual captured git failure plus a
    concrete next step, not a shrug."""
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    bogus_target = tmp_path / "does-not-exist-as-a-repo"

    monkeypatch.setattr(home_link, "_clone_url", lambda owner, name: str(bogus_target))

    def fake_run_gh(args):
        if args[:2] in (["auth", "status"], ["auth", "setup-git"]):
            return _cp(0)
        if args[:2] == ["repo", "view"]:
            name = args[2].split("/", 1)[1]
            return _cp(0, stdout=f'{{"url": "https://github.com/acme/{name}", '
                                 f'"visibility": "PRIVATE"}}\n')
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(home_link, "_run_gh", fake_run_gh)

    with pytest.raises(home_link.HomeLinkError) as excinfo:
        home_link.link_home(repo_root, _cfg(home), owner="acme")

    message = str(excinfo.value)
    # the real git stderr, not the generic "git push failed" fallback
    assert "does-not-exist-as-a-repo" in message
    # a concrete remedy — gh is available in this fake, so the gh-flavoured one
    assert "gh auth setup-git" in message or "gh auth login" in message


def test_dominion_push_failure_does_not_skip_knowledge_creation(tmp_path, monkeypatch):
    """A two-repo promise must attempt both slots even when the first
    repository is wired but its initial push fails."""
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    created: list[str] = []

    monkeypatch.setattr(home_link, "_require_gh_auth", lambda: None)
    monkeypatch.setattr(home_link, "resolve_owner", lambda _owner=None: "acme")
    monkeypatch.setattr(home_link, "_try_gh_setup_git", lambda: True)
    monkeypatch.setattr(
        home_link,
        "_clone_url",
        lambda _owner, name: str(tmp_path / f"remote-{name}"),
    )

    def fake_link_one(*, slot, repo_path, owner, name, ssh, prepare_push):
        created.append(slot)
        if slot == "dominion":
            raise home_link.HomeLinkError("dominion: initial push failed")
        return home_link.RepoLinkResult(slot, repo_path, f"https://github.test/{name}", "created", True)

    monkeypatch.setattr(home_link, "_link_one", fake_link_one)

    with pytest.raises(home_link.HomeLinkError, match="dominion: initial push failed"):
        home_link.link_home(repo_root, _cfg(home))

    assert created == ["dominion", "knowledge"]


def test_current_or_symbolic_branch_survives_a_current_branch_probe_failure(
    tmp_path, monkeypatch,
):
    """#1340: this function's whole contract is "always return a string" —
    unlike ``gitops.current_branch``, which now raises on a genuine
    measurement failure. A unit test on ``current_branch`` alone can't
    show this call site specifically falls back to its own symbolic-ref
    probe instead of letting the raise escape.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    home_link._ensure_git_repo(repo)  # git init -b main, no commit yet

    def _raise(_repo_root):
        raise gitops.CurrentBranchUnresolvable("simulated")

    monkeypatch.setattr(gitops, "current_branch", _raise)

    assert home_link._current_or_symbolic_branch(repo) == "main"
