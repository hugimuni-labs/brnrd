import json
import subprocess

from brr import account

from _helpers import write_repo_scaffold


def test_resolve_context_creates_account_dominion_and_registry(tmp_path):
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
            "account.dominion_path": str(home),
            "account.repo.Gurio/b": str(repo_b),
            "account.default_repo": "Gurio/b",
        },
    )

    assert ctx.dominion_repo == home
    assert ctx.default_repo.root == repo_b
    assert ctx.dispatch_inbox == home / "dispatch" / "inbox"
    assert ctx.responses_dir == home / "dispatch" / "responses"
    assert ctx.run_state_dir == home / "run-state"
    registry = json.loads((home / "account" / "repos.json").read_text())
    assert registry["default_repo"] == "Gurio/b"
    assert {item["label"] for item in registry["repos"]} == {"Gurio/a", "Gurio/b"}
    assert (home / ".gitignore").read_text(encoding="utf-8").splitlines() == [
        "/dispatch/inbox/",
        "/dispatch/responses/",
        "*.tmp",
    ]


def test_default_account_home_uses_brnrd_namespace(monkeypatch, tmp_path):
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)

    ctx = account.resolve_context(repo)

    assert ctx.dominion_repo == state_home / "brnrd" / "accounts" / "default" / "dominion"


def test_default_account_home_reads_existing_brr_namespace_as_legacy(
    monkeypatch, tmp_path,
):
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    legacy = state_home / "brr" / "accounts" / "default"
    (legacy / "dominion").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)

    ctx = account.resolve_context(repo, create=False)

    assert ctx.dominion_repo == legacy / "dominion"


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
        repo, {"account.dominion_path": str(tmp_path / "home")},
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
    ctx = account.resolve_context(repo, {"account.dominion_path": str(home)})
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
