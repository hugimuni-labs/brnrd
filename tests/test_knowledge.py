"""Tests for the home/repo knowledge source chain."""

import subprocess

from brr import account, knowledge
from brr.prompts import _build_knowledge_sources_block

from _helpers import init_git_repo


def test_prompt_injects_home_knowledge_without_repo_kb(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text(
        "# Home Knowledge\n\nResident-only note.", encoding="utf-8"
    )
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text(f"home.path={home}\n", encoding="utf-8")

    block = _build_knowledge_sources_block(repo)

    assert "Knowledge Sources" in block
    assert "Home Knowledge" in block
    assert "Resident-only note" in block


def test_knowledge_injection_orders_home_before_repo_kb(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text("home alpha", encoding="utf-8")
    (repo / "kb").mkdir()
    (repo / "kb" / "index.md").write_text("repo alpha", encoding="utf-8")

    block = knowledge.render_injection(repo, {"home.path": str(home)})

    assert block.index("home alpha") < block.index("repo alpha")


def test_search_reads_home_then_repo_sources(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text("shared needle home", encoding="utf-8")
    (repo / "kb").mkdir()
    (repo / "kb" / "index.md").write_text("shared needle repo", encoding="utf-8")

    hits = knowledge.search(repo, "needle", {"home.path": str(home)})

    assert [hit.source for hit in hits[:2]] == ["home knowledge", "repo KB"]


def test_checkout_is_gitignored_from_project_status(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"

    checkout = knowledge.ensure_checkout(repo, {"home.path": str(home)})

    assert checkout == repo / ".brnrd-kb"
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".brnrd-kb" not in status
    exclude = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert ".brnrd-kb/" in (repo / exclude).read_text(encoding="utf-8")


def _account_cfg(repo, home, **extra):
    cfg = {
        "repo.label": "Gurio/brr",
        "home.kind": "account",
        "home.path": str(home),
        "account.id": "acct-1",
    }
    cfg.update(extra)
    return cfg


def test_account_home_splits_knowledge_by_repo_and_cross_repo(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    cfg = _account_cfg(repo, home)

    ctx = account.resolve_context(repo, cfg)
    repo_knowledge = account.repo_knowledge_path(ctx, "Gurio/brr")
    cross_repo_knowledge = account.account_knowledge_path(ctx)
    repo_knowledge.mkdir(parents=True)
    cross_repo_knowledge.mkdir(parents=True)
    (repo_knowledge / "index.md").write_text("repo-scoped note", encoding="utf-8")
    (cross_repo_knowledge / "index.md").write_text("account-wide note", encoding="utf-8")

    found = knowledge.sources(repo, cfg)
    names = [s.name for s in found]

    assert names[:2] == ["home knowledge (repo)", "home knowledge (account)"]
    block = knowledge.render_injection(repo, cfg)
    # Repo-scoped knowledge is the most relevant to this wake — it leads.
    assert block.index("repo-scoped note") < block.index("account-wide note")


def test_account_only_mode_keeps_one_flat_bucket(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    cfg = _account_cfg(repo, home, **{"knowledge.split": "account-only"})

    ctx = account.resolve_context(repo, cfg)
    flat = account.knowledge_path(ctx)
    flat.mkdir(parents=True)
    (flat / "index.md").write_text("one shared bucket", encoding="utf-8")

    found = knowledge.sources(repo, cfg)

    assert [s.name for s in found[:1]] == ["home knowledge"]
    assert found[0].root == flat


def test_project_home_never_splits(tmp_path):
    """A project home has exactly one repo — nothing to split against."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text("project note", encoding="utf-8")

    found = knowledge.sources(repo, {"home.path": str(home)})

    assert [s.name for s in found[:1]] == ["home knowledge"]


def test_active_kb_dir_prefers_home_knowledge_over_repo_kb(tmp_path):
    """When both shapes exist (a repo mid-migration, say), the
    maintenance scanners should walk the home-knowledge copy — the
    same priority `sources()` already gives it for injection/search."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    cfg = _account_cfg(repo, home)
    ctx = account.resolve_context(repo, cfg)
    repo_knowledge = account.repo_knowledge_path(ctx, "Gurio/brr")
    repo_knowledge.mkdir(parents=True)
    (repo / "kb").mkdir()

    found = knowledge.active_kb_dir(repo, cfg)

    assert found == repo_knowledge


def test_active_kb_dir_falls_back_to_repo_kb(tmp_path):
    """No account context (or an account home with nothing checked out
    yet) — the legacy repo-committed kb/ is still a valid answer."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / "kb").mkdir()

    assert knowledge.active_kb_dir(repo, {}) == repo / "kb"


def test_active_kb_dir_none_when_neither_exists(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)

    assert knowledge.active_kb_dir(repo, {}) is None
