"""Tests for the home/repo knowledge source chain."""

from pathlib import Path
import subprocess

from brr import account, knowledge
from brr.prompts import _build_knowledge_sources_block

from _helpers import init_git_repo


def _commit(repo: Path, message: str = "commit") -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", message],
        cwd=repo, check=True,
    )


def _knowledge_remote_chain(tmp_path: Path, *, forge: bool) -> tuple[Path, dict]:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    knowledge_repo = home / "knowledge"
    page = knowledge_repo / "repos" / "Gurio__brr" / "design-managed-delivery.md"
    page.parent.mkdir(parents=True)
    page.write_text("pushed page\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=knowledge_repo, check=True)
    _commit(knowledge_repo, "seed knowledge")
    if forge:
        subprocess.run(
            ["git", "remote", "add", "origin",
             "git@github.com:hugimuni-labs/brnrd-knowledge.git"],
            cwd=knowledge_repo, check=True,
        )
        # A network-free stand-in for a successful push: the resolver's
        # contract is the remote-tracking ref, not local branch existence.
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
            cwd=knowledge_repo, check=True,
        )
    subprocess.run(
        ["git", "clone", "-q", str(knowledge_repo), str(repo / ".brnrd-kb")],
        check=True,
    )
    cfg = _account_cfg(repo, home)
    return repo, cfg


def test_kb_url_resolves_two_hop_local_origin_to_forge(tmp_path):
    repo, cfg = _knowledge_remote_chain(tmp_path, forge=True)

    assert knowledge.kb_base_url(repo, cfg) == (
        "https://github.com/hugimuni-labs/brnrd-knowledge/blob/main/"
        "repos/Gurio__brr/"
    )
    assert knowledge.kb_page_url(repo, "design-managed-delivery.md", cfg) == (
        "https://github.com/hugimuni-labs/brnrd-knowledge/blob/main/"
        "repos/Gurio__brr/design-managed-delivery.md"
    )

    # A page committed only to the ultimate local repository is not linkable
    # until its forge remote-tracking ref advances to include it.
    unpushed = tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "new.md"
    unpushed.write_text("not pushed\n", encoding="utf-8")
    _commit(tmp_path / "home" / "knowledge", "local only")
    assert knowledge.kb_page_url(repo, "new.md", cfg) is None

    pushed_page = (
        tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr"
        / "design-managed-delivery.md"
    )
    pushed_page.write_text("new local content\n", encoding="utf-8")
    _commit(tmp_path / "home" / "knowledge", "unpushed page update")
    assert knowledge.kb_page_url(repo, "design-managed-delivery.md", cfg) is None


def test_kb_url_returns_none_when_remote_chain_has_no_forge(tmp_path):
    repo, cfg = _knowledge_remote_chain(tmp_path, forge=False)

    assert knowledge.kb_base_url(repo, cfg) is None
    assert knowledge.kb_page_url(repo, "design-managed-delivery.md", cfg) is None


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


def test_checkout_recloned_when_origin_is_stale(tmp_path):
    """Live bug, 2026-07-09: a checkout made before an account-resolution
    change (e.g. a cloud-gate connect starts filling in ``account_id``
    where it used to fall back to a decoy value) kept its old ``origin``
    forever — ``ensure_checkout`` only checked "does the directory exist",
    never "does it still point at the right home". Simulate that by
    pointing the existing checkout's origin at a different, empty
    knowledge repo and confirming a call with the *real* home re-clones
    rather than silently returning the stale one."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text("current home content", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=home / "knowledge", check=True)
    subprocess.run(["git", "add", "-A"], cwd=home / "knowledge", check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed"],
        cwd=home / "knowledge",
        check=True,
    )
    cfg = {"home.path": str(home)}

    # A prior wake's checkout, cloned from a since-abandoned decoy home.
    decoy_home = tmp_path / "decoy-home" / "knowledge"
    decoy_home.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(decoy_home)], check=True)
    checkout = repo / knowledge.CHECKOUT_DIRNAME
    subprocess.run(
        ["git", "clone", "-q", str(decoy_home), str(checkout)], check=True
    )
    assert (
        subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == str(decoy_home)
    )

    result = knowledge.ensure_checkout(repo, cfg)

    assert result == checkout
    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert Path(origin).resolve() == (home / "knowledge").resolve()
    assert (checkout / "index.md").read_text(encoding="utf-8") == "current home content"


def test_checkout_reused_when_origin_still_matches(tmp_path):
    """The common case: an existing, still-correct checkout is left alone
    rather than needlessly re-cloned on every call."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "index.md").write_text("v1", encoding="utf-8")
    cfg = {"home.path": str(home)}

    first = knowledge.ensure_checkout(repo, cfg)
    marker = first / ".untracked-marker"
    marker.write_text("still here", encoding="utf-8")

    second = knowledge.ensure_checkout(repo, cfg)

    assert second == first
    assert marker.exists()


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
