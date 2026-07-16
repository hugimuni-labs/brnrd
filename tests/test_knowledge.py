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


# ── Capture net (#357): checkout → account knowledge → forge ─────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )


def _capture_chain(tmp_path: Path, *, checkout: bool = True) -> tuple[Path, dict, Path]:
    """repo(+checkout) → account knowledge (non-bare) → a bare local 'forge'.

    A real push chain, no network: the bare repo stands in for GitHub, so
    the test asserts on what actually landed there rather than on a mock.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    forge = tmp_path / "forge.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(forge)], check=True)

    knowledge_repo = home / "knowledge"
    page_dir = knowledge_repo / "repos" / "Gurio__brr"
    page_dir.mkdir(parents=True)
    (page_dir / "index.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=knowledge_repo, check=True)
    _git(knowledge_repo, "add", "-A")
    _git(knowledge_repo, "commit", "-q", "-m", "seed knowledge")
    _git(knowledge_repo, "remote", "add", "origin", str(forge))
    _git(knowledge_repo, "push", "-q", "-u", "origin", "main")

    if checkout:
        subprocess.run(
            ["git", "clone", "-q", str(knowledge_repo), str(repo / ".brnrd-kb")],
            check=True,
        )
        _git(repo / ".brnrd-kb", "config", "user.email", "t@t")
        _git(repo / ".brnrd-kb", "config", "user.name", "t")
    _git(knowledge_repo, "config", "user.email", "t@t")
    _git(knowledge_repo, "config", "user.name", "t")
    return repo, _account_cfg(repo, home), forge


def _forge_has(forge: Path, path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"main:{path}"],
        cwd=forge, capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def test_capture_pushes_a_checkout_write_all_the_way_to_the_forge(tmp_path):
    """The whole point: a resident writes a page, and it reaches the archive
    with nobody running a manual push dance."""
    repo, cfg, forge = _capture_chain(tmp_path)
    page = repo / ".brnrd-kb" / "repos" / "Gurio__brr" / "design-new.md"
    page.write_text("a durable thought\n", encoding="utf-8")

    captured_pages: list[str] = []
    assert knowledge.capture(
        repo, "kb: capture", cfg=cfg, captured_pages=captured_pages,
    ) is True

    assert _forge_has(forge, "repos/Gurio__brr/design-new.md")
    assert captured_pages == ["design-new.md"]
    # updateInstead: the account's *working tree* took the push, so the next
    # wake's injected kb sees the page too — not just its git objects.
    account_copy = tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "design-new.md"
    assert account_copy.read_text(encoding="utf-8") == "a durable thought\n"


def test_capture_commits_direct_writes_into_the_account_tree(tmp_path):
    """#357's second defect: ``active_kb_dir`` points residents straight at
    the account working tree, and nothing ever committed what landed there —
    a log entry sat uncommitted for a day, invisible to every later wake."""
    repo, cfg, forge = _capture_chain(tmp_path, checkout=False)
    stray = tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "log.md"
    stray.write_text("an entry nobody committed\n", encoding="utf-8")

    captured_pages: list[str] = []
    assert knowledge.capture(
        repo, "kb: capture", cfg=cfg, captured_pages=captured_pages,
    ) is True

    assert _forge_has(forge, "repos/Gurio__brr/log.md")
    assert captured_pages == ["log.md"]


def test_capture_page_manifest_excludes_reply_archives(tmp_path):
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    knowledge.archive_reply(
        repo, run_id="run-reply", body="durable answer", cfg=cfg,
    )
    page = tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "decision.md"
    page.write_text("a decision\n", encoding="utf-8")
    captured_pages: list[str] = []

    knowledge.capture(
        repo, "kb: capture", cfg=cfg, captured_pages=captured_pages,
    )

    assert captured_pages == ["decision.md"]


def test_capture_reconciles_a_stray_account_write_against_a_checkout_write(tmp_path):
    """Both trees dirty at once — the account tree's stray commit would make
    the checkout's push non-fast-forward; capture rebases instead of bouncing."""
    repo, cfg, forge = _capture_chain(tmp_path)
    (repo / ".brnrd-kb" / "repos" / "Gurio__brr" / "from-checkout.md").write_text(
        "checkout\n", encoding="utf-8",
    )
    (tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "from-account.md").write_text(
        "account\n", encoding="utf-8",
    )

    assert knowledge.capture(repo, "kb: capture", cfg=cfg) is True

    assert _forge_has(forge, "repos/Gurio__brr/from-checkout.md")
    assert _forge_has(forge, "repos/Gurio__brr/from-account.md")


def test_capture_is_a_noop_on_a_clean_chain(tmp_path):
    repo, cfg, _forge = _capture_chain(tmp_path)

    assert knowledge.capture(repo, "kb: capture", cfg=cfg) is False


def test_capture_marks_needs_sync_when_the_forge_diverged(tmp_path):
    """A rejected push is never swallowed: the marker is the resident's cue
    to reconcile by hand, exactly as the dominion's capture net does."""
    repo, cfg, forge = _capture_chain(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(forge), str(other)], check=True)
    (other / "elsewhere.md").write_text("from another machine\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "diverge")
    _git(other, "push", "-q", "origin", "main")

    (repo / ".brnrd-kb" / "repos" / "Gurio__brr" / "mine.md").write_text("x\n", encoding="utf-8")
    knowledge.capture(repo, "kb: capture", cfg=cfg)

    brr_dir = repo / ".brr"
    assert "diverged" in (knowledge.needs_sync(brr_dir) or "")
    assert not _forge_has(forge, "repos/Gurio__brr/mine.md")

    # And the marker clears once the resident reconciles and capture retries.
    _git(tmp_path / "home" / "knowledge", "pull", "-q", "--rebase", "origin", "main")
    knowledge.capture(repo, "kb: capture", cfg=cfg)
    assert knowledge.needs_sync(brr_dir) is None
    assert _forge_has(forge, "repos/Gurio__brr/mine.md")


# ── Reply archive ────────────────────────────────────────────────────


def test_archive_reply_writes_outside_the_kb_page_tree(tmp_path):
    """The run's answer of record is archived — but as an archive, not a kb
    page: the graph stats, the preflight scan and wake injection all walk the
    page tree, and a year of replies in there would drown it."""
    repo, cfg, _forge = _capture_chain(tmp_path)

    rel = knowledge.archive_reply(
        repo, run_id="run-260712-1609-vnhc", body="the answer\n",
        meta={"event": "evt-1", "source": "telegram"}, cfg=cfg,
    )

    assert rel == "replies/Gurio__brr/run-260712-1609-vnhc.md"
    page_tree = knowledge.active_kb_dir(repo, cfg)
    archived = tmp_path / "home" / "knowledge" / rel
    assert archived.is_file()
    assert page_tree not in archived.parents
    body = archived.read_text(encoding="utf-8")
    assert body.startswith("---\nrun: run-260712-1609-vnhc\n")
    assert "event: evt-1" in body
    assert body.rstrip().endswith("the answer")


def test_archive_reply_skips_an_empty_terminal_reply(tmp_path):
    """A run that shipped its substance through interim outbox messages
    closes with an empty stdout — there is no answer of record to archive."""
    repo, cfg, _forge = _capture_chain(tmp_path)

    assert knowledge.archive_reply(repo, run_id="run-x", body="   \n", cfg=cfg) is None


def test_archived_reply_is_linkable_once_captured(tmp_path):
    """The relic's link. Same rule as kb pages: only after it's on the forge."""
    repo, cfg = _knowledge_remote_chain(tmp_path, forge=True)
    rel = knowledge.archive_reply(repo, run_id="run-1", body="answer", cfg=cfg)

    # Written but not yet pushed — no link, rather than a plausible 404.
    assert knowledge.knowledge_file_url(repo, rel, cfg) is None

    kb_repo = tmp_path / "home" / "knowledge"
    _commit(kb_repo, "archive reply")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=kb_repo, check=True,
    )

    assert knowledge.knowledge_file_url(repo, rel, cfg) == (
        "https://github.com/hugimuni-labs/brnrd-knowledge/blob/main/"
        "replies/Gurio__brr/run-1.md"
    )
