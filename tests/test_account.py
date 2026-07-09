import json
import subprocess

from brr import account

from _helpers import write_repo_scaffold


def test_resolve_context_creates_account_home_and_registry(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)
    home = tmp_path / "account-home"

    ctx = account.resolve_context(
        repo_a,
        {
            "repo.label": "Gurio/a",
            "home.kind": "account",
            "home.path": str(home),
            "account.id": "acct-1",
            "account.repo.Gurio/b": str(repo_b),
            "account.default_repo": "Gurio/b",
        },
    )

    assert ctx.dominion_repo == home
    assert ctx.home_root == home
    assert ctx.kind == "account"
    assert ctx.account_id == "acct-1"
    assert ctx.default_repo.root == repo_b
    assert ctx.dispatch_inbox == home / "dispatch" / "inbox"
    assert ctx.responses_dir == home / "dispatch" / "responses"
    assert ctx.run_state_dir == home / "run-state"
    registry = json.loads((home / "account" / "repos.json").read_text())
    assert registry["home_kind"] == "account"
    assert registry["home_id"] == "acct-1"
    assert registry["default_repo"] == "Gurio/b"
    assert {item["label"] for item in registry["repos"]} == {"Gurio/a", "Gurio/b"}
    assert (home / ".gitignore").read_text(encoding="utf-8").splitlines() == [
        "/dispatch/inbox/",
        "/dispatch/responses/",
        "/knowledge/",
        "*.tmp",
    ]


def test_gitignore_backfills_missing_rules_on_a_pre_existing_home(tmp_path):
    """A home created before a rule existed shouldn't carry a stale .gitignore.

    ``resolve_context`` only calls ``_write_gitignore`` at first creation —
    the write itself has to be an append-what's-missing, not a one-shot
    "skip if the file exists", or every home created before ``/knowledge/``
    was added would keep a nested, un-ignored knowledge git repo forever.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    home.mkdir(parents=True)
    (home / ".gitignore").write_text(
        "/dispatch/inbox/\n/dispatch/responses/\n*.tmp\n", encoding="utf-8"
    )

    account.resolve_context(
        repo,
        {
            "home.kind": "account",
            "home.path": str(home),
            "account.id": "acct-1",
        },
    )

    lines = (home / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/knowledge/" in lines
    assert lines.count("/knowledge/") == 1


def test_default_home_is_repo_derived_project_home(monkeypatch, tmp_path):
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)

    ctx = account.resolve_context(repo)

    assert ctx.kind == "project"
    assert ctx.account_id == ""
    assert ctx.dominion_repo.parent.parent == state_home / "brnrd" / "projects"
    assert ctx.dominion_repo.name == "home"
    assert ctx.dominion_repo.parent.name.startswith("repo-")


def test_project_home_uses_path_hash_for_same_basename(monkeypatch, tmp_path):
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    repo_a = tmp_path / "a" / "repo"
    repo_b = tmp_path / "b" / "repo"
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)

    ctx_a = account.resolve_context(repo_a)
    ctx_b = account.resolve_context(repo_b)

    assert ctx_a.dominion_repo != ctx_b.dominion_repo
    assert ctx_a.dominion_repo.parent.name.startswith("repo-")
    assert ctx_b.dominion_repo.parent.name.startswith("repo-")


def test_repo_dominion_path_is_repo_tagged(tmp_path):
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        run_state_dir=tmp_path / "home" / "run-state",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.repo_dominion_path(ctx, "Gurio/brr") == (
        tmp_path / "home" / "repos" / "Gurio__brr" / "dominion"
    )


def test_event_repo_label_accepts_repo_label_metadata():
    assert account.event_repo_label({"repo_label": "Gurio/brr"}) == "Gurio/brr"


def test_run_state_blob_url_none_for_local_only_dominion(tmp_path):
    """A purely-local account dominion (no remote) yields no web URL, so callers
    fall back to a non-path label rather than leaking a host path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = account.resolve_context(
        repo, {"home.path": str(tmp_path / "home")},
    )
    doc = ctx.run_state_dir / "local" / "run.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("x", encoding="utf-8")
    assert account.run_state_blob_url(ctx, doc) is None


def test_run_state_blob_url_projects_to_forge_remote(tmp_path):
    """Once the dominion tracks a forge-hosted remote, a run-state doc gets a
    stable blob URL derived from the remote and the doc's repo-relative path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "home"
    ctx = account.resolve_context(repo, {"home.path": str(home)})
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Gurio/account.git"],
        cwd=home, check=True,
    )
    doc = ctx.run_state_dir / "Gurio__brr" / "run-260630.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("x", encoding="utf-8")
    url = account.run_state_blob_url(ctx, doc)
    assert url == (
        "https://github.com/Gurio/account/blob/main/"
        "run-state/Gurio__brr/run-260630.md"
    )


# ── CS5 — inter-run plan home ─────────────────────────────────────────


def test_resolve_context_creates_plans_directory(tmp_path):
    """CS5: resolve_context creates the plans/ directory alongside the other
    account-store directories so the resident can immediately write a plan."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "home"

    account.resolve_context(repo, {"home.path": str(home)})

    assert (home / "plans").is_dir()


def test_active_plan_path_is_repo_tagged(tmp_path):
    """CS5: the active plan path is tagged by repo label inside plans/."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        run_state_dir=tmp_path / "home" / "run-state",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.active_plan_path(ctx, "Gurio/brr") == (
        tmp_path / "home" / "plans" / "Gurio__brr" / "active.md"
    )


def test_cross_repo_plans_path(tmp_path):
    """CS5: cross-repo plans live under plans/_cross-repo/."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        run_state_dir=tmp_path / "home" / "run-state",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.cross_repo_plans_path(ctx) == (
        tmp_path / "home" / "plans" / "_cross-repo"
    )


# ── CS6 — runner policy home ──────────────────────────────────────────


def test_runner_policy_path_is_repo_tagged(tmp_path):
    """CS6: the runner policy path is tagged by repo label."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        run_state_dir=tmp_path / "home" / "run-state",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.runner_policy_path(ctx, "Gurio/brr") == (
        tmp_path / "home" / "runner-policy" / "Gurio__brr" / "policy.md"
    )


def test_account_runner_policy_path(tmp_path):
    """CS6: the account-wide runner policy lives under runner-policy/_account/."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        run_state_dir=tmp_path / "home" / "run-state",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.account_runner_policy_path(ctx) == (
        tmp_path / "home" / "runner-policy" / "_account" / "policy.md"
    )


def test_runner_policy_proposals_path(tmp_path):
    """CS6b: pending policy proposals live under runner-policy/_proposals/."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        run_state_dir=tmp_path / "home" / "run-state",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.runner_policy_proposals_path(ctx) == (
        tmp_path / "home" / "runner-policy" / "_proposals"
    )


# ── CS7 — decision ledger home ────────────────────────────────────────


def test_decisions_ledger_path(tmp_path):
    """CS7: the decision ledger lives at ledger/decisions.md."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        run_state_dir=tmp_path / "home" / "run-state",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.decisions_ledger_path(ctx) == (
        tmp_path / "home" / "ledger" / "decisions.md"
    )
