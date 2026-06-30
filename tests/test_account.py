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
    home = tmp_path / "brr-home"

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
