import json

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
