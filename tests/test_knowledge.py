"""Tests for the home/repo knowledge source chain."""

from pathlib import Path
import subprocess

from brr import account, knowledge
from brr.prompts import _build_knowledge_sources_block

from _helpers import commit_files, init_git_repo


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


def test_active_kb_dir_matches_between_a_worktree_and_its_main_checkout(
    monkeypatch, tmp_path,
):
    """#654: before the fix, `active_kb_dir` from a linked worktree fell
    back to a project home with nothing checked out (`resolve_context`
    couldn't find the worktree's own path in the account registry) — so a
    daemon-provisioned run silently searched the wrong corpus. It must
    resolve the same directory the main checkout does."""
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    main_repo = tmp_path / "main"
    init_git_repo(main_repo)
    commit_files(main_repo, {"README.md": "hi\n"})
    label_cfg = {"repo.label": "Gurio/brr"}
    ctx = account.resolve_context(
        main_repo,
        {**label_cfg, "home.kind": "account", "account.id": "acct-1"},
    )
    repo_knowledge = account.repo_knowledge_path(ctx, "Gurio/brr")
    repo_knowledge.mkdir(parents=True)

    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch", str(worktree)],
        cwd=main_repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    from_main = knowledge.active_kb_dir(main_repo, label_cfg)
    from_worktree = knowledge.active_kb_dir(worktree, label_cfg)

    assert from_worktree == repo_knowledge
    assert from_worktree == from_main


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


def test_committed_pages_in_window_credits_resident_committed_pages(tmp_path):
    """#538: a page the resident *commits* mid-run is invisible to the
    dirty-vs-HEAD capture diff; the run-start OID window still sees it —
    provided the commit carries this run's identity trailer (#565), which is
    what the knowledge repo's installed ``commit-msg`` hook stamps in
    production from ``$BRR_RUN_ID``."""
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    knowledge_repo = tmp_path / "home" / "knowledge"
    start = knowledge.head_oid(repo, cfg)
    assert start

    page = knowledge_repo / "repos" / "Gurio__brr" / "mid-run.md"
    page.write_text("committed mid-run\n", encoding="utf-8")
    other = knowledge_repo / "repos" / "Other__repo" / "not-ours.md"
    other.parent.mkdir(parents=True)
    other.write_text("another repo's page\n", encoding="utf-8")
    _git(knowledge_repo, "add", "-A")
    _git(
        knowledge_repo, "commit", "-q", "-m", "resident: mid-run kb work",
        "--trailer", "Brnrd-Run-Id: run-mine",
    )

    assert knowledge.committed_pages_in_window(
        repo, start, cfg=cfg, run_id="run-mine",
    ) == ["mid-run.md"]


def test_committed_pages_in_window_attributes_by_run_identity_not_time(tmp_path):
    """#565: stopped runs were credited, on their own dashboard nodes, with a
    concurrent sibling worker's kb commits, because the window used to union
    in everything committed in ``start..HEAD`` on the *shared* account-
    knowledge checkout with no regard for who committed it. Two runs land
    commits into the same overlapping window here; each must be credited
    only its own pages, and a commit with no trailer at all (a maintainer's
    hand commit) must go to neither — never fall back to crediting it by
    time alone."""
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    knowledge_repo = tmp_path / "home" / "knowledge"
    start = knowledge.head_oid(repo, cfg)
    assert start

    page_a = knowledge_repo / "repos" / "Gurio__brr" / "run-a.md"
    page_a.write_text("run a's page\n", encoding="utf-8")
    _git(knowledge_repo, "add", "-A")
    _git(
        knowledge_repo, "commit", "-q", "-m", "run a: kb work",
        "--trailer", "Brnrd-Run-Id: run-A",
    )

    page_b = knowledge_repo / "repos" / "Gurio__brr" / "run-b.md"
    page_b.write_text("run b's page\n", encoding="utf-8")
    _git(knowledge_repo, "add", "-A")
    _git(
        knowledge_repo, "commit", "-q", "-m", "run b: kb work",
        "--trailer", "Brnrd-Run-Id: run-B",
    )

    page_hand = knowledge_repo / "repos" / "Gurio__brr" / "hand.md"
    page_hand.write_text("a maintainer's hand commit\n", encoding="utf-8")
    _git(knowledge_repo, "add", "-A")
    _git(knowledge_repo, "commit", "-q", "-m", "maintainer: hand edit")

    assert knowledge.committed_pages_in_window(
        repo, start, cfg=cfg, run_id="run-A",
    ) == ["run-a.md"]
    assert knowledge.committed_pages_in_window(
        repo, start, cfg=cfg, run_id="run-B",
    ) == ["run-b.md"]
    # No run owns the trailer-less hand commit — not run A, not run B, and
    # not a third run id that happens to ask.
    assert knowledge.committed_pages_in_window(
        repo, start, cfg=cfg, run_id="run-C",
    ) == []


def test_committed_pages_in_window_falls_back_on_bad_or_rewritten_oid(tmp_path):
    """No stamp, an unresolvable OID, or a rewritten history each degrade to
    today's behavior (an empty window), never an error."""
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    knowledge_repo = tmp_path / "home" / "knowledge"
    page = knowledge_repo / "repos" / "Gurio__brr" / "mid-run.md"
    page.write_text("committed mid-run\n", encoding="utf-8")
    _git(knowledge_repo, "add", "-A")
    _git(
        knowledge_repo, "commit", "-q", "-m", "resident: mid-run kb work",
        "--trailer", "Brnrd-Run-Id: run-mine",
    )

    assert knowledge.committed_pages_in_window(
        repo, None, cfg=cfg, run_id="run-mine",
    ) == []
    assert knowledge.committed_pages_in_window(
        repo, "", cfg=cfg, run_id="run-mine",
    ) == []
    assert knowledge.committed_pages_in_window(
        repo, "deadbeef" * 5, cfg=cfg, run_id="run-mine",
    ) == []
    # No run_id at all also degrades to empty — never a fallback to the old
    # unfiltered-by-time behavior.
    assert knowledge.committed_pages_in_window(repo, "deadbeef" * 5, cfg=cfg) == []
    # An OID that resolves but is no ancestor of HEAD (rebase/gc rewrote
    # the window) — the ancestry guard refuses to diff across it.
    orphan = _git(
        knowledge_repo, "commit-tree", "HEAD^{tree}", "-m", "orphan",
    ).stdout.strip()
    assert knowledge.committed_pages_in_window(
        repo, orphan, cfg=cfg, run_id="run-mine",
    ) == []


def test_committed_pages_in_window_noop_without_knowledge_repo(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)

    assert knowledge.head_oid(repo, {}) is None
    assert knowledge.committed_pages_in_window(
        repo, "deadbeef" * 5, cfg={}, run_id="run-mine",
    ) == []


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


# ── The mirror reads current (#659) ──────────────────────────────────


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_capture_fast_forwards_the_checkout_mirror_after_a_direct_account_write(
    tmp_path,
):
    """#659 — capture used to ``fetch`` the checkout and stop one command
    short. The remote-tracking ref advanced; the *local* branch and working
    tree never did, so every direct account write (step 2's own comment
    calls that "today's common path") left ``.brnrd-kb/`` one commit further
    behind with ``git status`` clean and page counts equal. The only file
    that diverges is the append-heavy ``log.md``, and a whole-file rewrite
    there — which log compaction and the state-first discipline call for —
    regresses it silently."""
    repo, cfg, _forge = _capture_chain(tmp_path)
    checkout = repo / ".brnrd-kb"
    before = _head(checkout)
    log = tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "log.md"
    log.write_text(
        "## [2026-07-24] fix | written straight into the account\n",
        encoding="utf-8",
    )

    assert knowledge.capture(repo, "kb: capture", cfg=cfg) is True

    # The local branch moved — not just ``origin/main``.
    assert _head(checkout) != before
    # The one honest handle a resident could have checked, now empty.
    assert _git(
        checkout, "rev-list", "--count", "HEAD..origin/main",
    ).stdout.strip() == "0"
    # And the *file on disk* matches, byte for byte: a ref that advanced
    # without the worktree following would read just as clean.
    mirrored = checkout / "repos" / "Gurio__brr" / "log.md"
    assert mirrored.read_bytes() == log.read_bytes()
    assert _git(checkout, "status", "--porcelain").stdout == ""


def test_capture_syncs_the_mirror_when_there_is_no_forge_remote(tmp_path):
    """The sync's honest trigger is "the account branch moved", which is
    knowable without a forge. Gated on the forge push (where #659 found it),
    a repo with no forge remote returns early and never syncs the mirror at
    all — the case the old placement could not reach."""
    repo, cfg, _forge = _capture_chain(tmp_path)
    knowledge_repo = tmp_path / "home" / "knowledge"
    _git(knowledge_repo, "remote", "remove", "origin")
    checkout = repo / ".brnrd-kb"
    before = _head(checkout)
    page = knowledge_repo / "repos" / "Gurio__brr" / "offline.md"
    page.write_text("no forge on this machine\n", encoding="utf-8")

    assert knowledge.capture(repo, "kb: capture", cfg=cfg) is True

    assert _head(checkout) != before
    assert (
        checkout / "repos" / "Gurio__brr" / "offline.md"
    ).read_bytes() == page.read_bytes()


def test_capture_leaves_a_dirty_checkout_alone_and_reports_the_skip(tmp_path):
    """The one case where standing back is right — and it still has to say
    so. A silent skip reproduces the bug #659 is about: a surface with no
    way to say "I am stale".

    The ``pre-commit`` hook stands in for the production window where a
    checkout is dirty at sync time even though step 1 tried to commit it:
    the shared checkout is dirtied by a concurrent sibling run, or step 1's
    own commit is refused (the installed ``commit-msg`` close-keyword guard
    does exactly that)."""
    repo, cfg, _forge = _capture_chain(tmp_path)
    checkout = repo / ".brnrd-kb"
    hook = checkout / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    in_flight = checkout / "repos" / "Gurio__brr" / "index.md"
    in_flight.write_text("half a thought, not ready\n", encoding="utf-8")
    before = _head(checkout)
    (tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "log.md").write_text(
        "the account moved meanwhile\n", encoding="utf-8",
    )

    notes: list[str] = []
    knowledge.capture(repo, "kb: capture", cfg=cfg, mirror_notes=notes)

    # Untouched: no merge, no reset, the resident's bytes still there.
    assert _head(checkout) == before
    assert in_flight.read_text(encoding="utf-8") == "half a thought, not ready\n"
    # Observable: the run can tell the mirror was left behind, and why.
    assert notes and "uncommitted" in notes[0]
    assert str(checkout) in notes[0]


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


def test_capture_stamps_conversation_trailer_on_kb_commit(tmp_path):
    """#61 — the kb capture commit carries the Brnrd-Conversation-Id trailer."""
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    page = tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "log.md"
    page.write_text("an entry\n", encoding="utf-8")

    assert knowledge.capture(
        repo, "kb: capture", cfg=cfg, conversation_id="github:Gurio/brr:61",
    ) is True

    home_knowledge = tmp_path / "home" / "knowledge"
    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Conversation-Id,valueonly)"],
        cwd=home_knowledge, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == "github:Gurio/brr:61"


def test_capture_stamps_run_id_trailer_on_kb_commit(tmp_path):
    """#565 — the automated capture commit carries a Brnrd-Run-Id trailer,
    the identity ``committed_pages_in_window`` filters a shared window by."""
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    page = tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "log.md"
    page.write_text("an entry\n", encoding="utf-8")

    assert knowledge.capture(
        repo, "kb: capture", cfg=cfg, run_id="run-260722-9999-abcd",
    ) is True

    home_knowledge = tmp_path / "home" / "knowledge"
    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Run-Id,valueonly)"],
        cwd=home_knowledge, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == "run-260722-9999-abcd"


def test_capture_installs_a_commit_msg_hook_that_stamps_brr_run_id(tmp_path, monkeypatch):
    """#565 — residents commit kb pages directly, mid-run, with a bare
    ``git commit`` no Python code ever sees. The one code-only interception
    point (no prompt file may teach a resident to type ``--trailer`` by
    hand) is a ``commit-msg`` hook capture installs into the knowledge repo,
    which turns ``$BRR_RUN_ID`` into the same trailer the automated commit
    gets."""
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    home_knowledge = tmp_path / "home" / "knowledge"

    # Capture (even a no-op one) must have installed the hook already.
    assert knowledge.capture(repo, "kb: capture", cfg=cfg) is False
    hook_path = home_knowledge / ".git" / "hooks" / "commit-msg"
    assert hook_path.is_file()
    assert hook_path.stat().st_mode & 0o111

    page = home_knowledge / "repos" / "Gurio__brr" / "hand-written.md"
    page.write_text("a resident wrote this mid-run\n", encoding="utf-8")
    monkeypatch.setenv("BRR_RUN_ID", "run-hooked")
    _git(home_knowledge, "add", "-A")
    _git(home_knowledge, "commit", "-q", "-m", "resident: mid-run kb work")

    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Run-Id,valueonly)"],
        cwd=home_knowledge, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == "run-hooked"


def test_commit_msg_hook_leaves_a_hand_commit_untouched_without_the_env(
    tmp_path, monkeypatch,
):
    """No ``$BRR_RUN_ID`` in the shell (a maintainer, logged in directly) ⇒
    the hook is a no-op — credited to no run, never a guess (#565).

    Explicitly ``delenv`` rather than trusting a bare environment: this
    suite can itself run inside a brnrd-dispatched worker, whose own shell
    carries a real ``BRR_RUN_ID`` — exactly the ambient value this test
    must rule out to mean anything.
    """
    monkeypatch.delenv("BRR_RUN_ID", raising=False)
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    home_knowledge = tmp_path / "home" / "knowledge"
    knowledge.capture(repo, "kb: capture", cfg=cfg)

    page = home_knowledge / "repos" / "Gurio__brr" / "hand-written.md"
    page.write_text("a maintainer wrote this\n", encoding="utf-8")
    _git(home_knowledge, "add", "-A")
    _git(home_knowledge, "commit", "-q", "-m", "maintainer: hand edit")

    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Run-Id,valueonly)"],
        cwd=home_knowledge, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == ""


def test_repo_docs_block_skips_vendored_trees(tmp_path):
    """A docs site's `node_modules/` must not displace its authored pages.

    Live shape, 2026-07-23: this project's `docs/` is an Astro site, so 505
    of its 518 markdown files are dependency READMEs. `_source_excerpt`
    lists the first 20 sorted paths, and `node_modules/` sorts ahead of
    `src/` — so the wake's "repo docs" block was `README.md` plus nineteen
    Astro dependency READMEs and "... 530 more", with not one authored page
    in it.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    docs = repo / "docs"
    (docs / "src" / "content").mkdir(parents=True)
    (docs / "README.md").write_text("authored readme", encoding="utf-8")
    (docs / "src" / "content" / "guide.md").write_text("authored", encoding="utf-8")
    vendored = docs / "node_modules" / "@astrojs" / "starlight"
    vendored.mkdir(parents=True)
    for name in ("README.md", "CHANGELOG.md"):
        (vendored / name).write_text("vendored", encoding="utf-8")
    built = docs / "dist"
    built.mkdir()
    (built / "index.md").write_text("built", encoding="utf-8")
    hidden = docs / ".astro"
    hidden.mkdir()
    (hidden / "types.md").write_text("generated", encoding="utf-8")

    block = knowledge.render_injection(repo, {})

    assert "src/content/guide.md" in block
    assert "README.md" in block
    assert "node_modules" not in block
    assert "dist/index.md" not in block
    assert ".astro" not in block


def test_search_does_not_descend_into_vendored_trees(tmp_path):
    """`brnrd kb <query>` has a 20-hit cap — vendored files must not eat it."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    docs = repo / "docs"
    vendored = docs / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "CHANGELOG.md").write_text("needle vendored\n" * 40, encoding="utf-8")
    (docs / "guide.md").write_text("needle authored", encoding="utf-8")

    hits = knowledge.search(repo, "needle", {})

    assert [h.path.name for h in hits] == ["guide.md"]


def test_iter_docs_root_may_itself_be_a_dot_directory(tmp_path):
    """Only components *below* the root are tested — `.brnrd-kb/` still walks."""
    root = tmp_path / ".brnrd-kb"
    (root / "repos").mkdir(parents=True)
    (root / "repos" / "page.md").write_text("kept", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.md").write_text("dropped", encoding="utf-8")

    names = [p.name for p in knowledge._iter_docs(root)]

    assert names == ["page.md"]


# ── ensure_checkout refresh (#613) ───────────────────────────────────
#
# The checkout used to be returned untouched whenever its origin still
# matched — no fetch, no pull, ever. capture() pushes only when the
# checkout is ahead and pulls only after a failed push, so behind-and-
# clean drifted permanently (measured live: 28 commits / 3 days, two
# pages `brnrd kb` could not return by any spelling). These fixtures
# each *assert their own staleness first* so they cannot quietly become
# already-current input and pin nothing (the #611 failure mode).


def _seeded_checkout(tmp_path):
    """A home knowledge repo with one commit and a checkout cloned from it."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = tmp_path / "home"
    krepo = home / "knowledge"
    krepo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=krepo, check=True)
    (krepo / "index.md").write_text("v1\n", encoding="utf-8")
    _commit(krepo, "seed")
    cfg = {"home.path": str(home)}
    checkout = knowledge.ensure_checkout(repo, cfg)
    assert (checkout / "index.md").exists()
    return repo, krepo, checkout, cfg


def _advance_home(krepo, filename="late-page.md"):
    (krepo / filename).write_text("account-side content\n", encoding="utf-8")
    _commit(krepo, f"add {filename}")


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_checkout_fast_forwards_when_behind_origin(tmp_path):
    """The #613 defect itself: a clean checkout behind its origin must come
    back current, and a page that never existed checkout-side must become
    searchable rather than invisible to every spelling of `brnrd kb`."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    _advance_home(krepo)

    # Guard the fixture: genuinely behind, page genuinely absent.
    assert _head(checkout) != _head(krepo)
    assert not (checkout / "late-page.md").exists()

    result = knowledge.ensure_checkout(repo, cfg)

    assert result == checkout
    assert _head(checkout) == _head(krepo)
    assert (checkout / "late-page.md").read_text(
        encoding="utf-8"
    ) == "account-side content\n"
    behind = subprocess.run(
        ["git", "rev-list", "--count", "HEAD..origin/main"],
        cwd=checkout, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert behind == "0"


def test_checkout_refresh_tolerates_untracked_files(tmp_path):
    """Stray untracked files are a checkout's normal state; they must not
    block the fast-forward (that would re-create #613 for any checkout
    holding one) and must survive it."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    marker = checkout / "scratch-note.md"
    marker.write_text("untracked\n", encoding="utf-8")
    _advance_home(krepo)
    assert _head(checkout) != _head(krepo)

    knowledge.ensure_checkout(repo, cfg)

    assert _head(checkout) == _head(krepo)
    assert marker.read_text(encoding="utf-8") == "untracked\n"


def test_checkout_refresh_skips_dirty_tracked_files(tmp_path):
    """A modified tracked file means an in-flight edit: refresh must skip —
    return what we have — never stash, clobber, or raise."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    (checkout / "index.md").write_text("local edit in flight\n", encoding="utf-8")
    _advance_home(krepo)
    old_head = _head(checkout)
    assert old_head != _head(krepo)

    result = knowledge.ensure_checkout(repo, cfg)

    assert result == checkout
    assert _head(checkout) == old_head
    assert (checkout / "index.md").read_text(
        encoding="utf-8"
    ) == "local edit in flight\n"
    assert not (checkout / "late-page.md").exists()


def test_checkout_refresh_skips_diverged_history(tmp_path):
    """Diverged histories (checkout committed locally, account advanced
    separately) must degrade to a no-op: --ff-only refuses, nothing is
    rebased or merged — reconciliation belongs to capture(), not a read."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    (checkout / "local-page.md").write_text("checkout-side\n", encoding="utf-8")
    _commit(checkout, "local commit")
    _advance_home(krepo)
    old_head = _head(checkout)
    assert old_head != _head(krepo)

    result = knowledge.ensure_checkout(repo, cfg)

    assert result == checkout
    assert _head(checkout) == old_head
    assert (checkout / "local-page.md").exists()
    assert not (checkout / "late-page.md").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=checkout,
        capture_output=True, text=True, check=True,
    ).stdout
    assert status == ""  # no merge/rebase state left behind


def test_refresh_checkout_survives_unreachable_origin(tmp_path):
    """An origin that stopped existing must degrade to "return what we
    have", not raise. Driven at the helper level: `ensure_checkout` mkdirs
    and re-inits its own origin path before refreshing, so a *genuinely*
    unreachable origin can only reach `_refresh_checkout` directly."""
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    (source / "index.md").write_text("v1\n", encoding="utf-8")
    _commit(source, "seed")
    checkout = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(source), str(checkout)], check=True
    )
    import shutil as _shutil

    _shutil.rmtree(source)
    old_head = _head(checkout)

    knowledge._refresh_checkout(checkout)  # must not raise

    assert _head(checkout) == old_head


# ── mirror state, read fresh each wake (#667) ────────────────────────
#
# #659 shipped a per-capture skip *reason* printed to the daemon log; the
# reader who needs it reads a wake prompt instead. These pin the *state*
# reading that replaces it. Every fixture below reaches its behind-mirror by
# driving the real production path (`ensure_checkout` → `_refresh_checkout`
# fetches, then skips the fast-forward for a real reason) rather than by
# hand-rolling refs — a count from a stubbed subprocess proves nothing about
# the command, and a hand-built ref proves nothing about reachability.


def _behind_mirror(tmp_path, *, dirty: bool):
    """A checkout genuinely behind origin, produced by the real skip path.

    Advance the account repo, leave an uncommitted edit in the checkout, and
    let `ensure_checkout` run: it fetches (so `origin/main` is fresh) and then
    declines to fast-forward over an in-flight edit. That is exactly the skip
    #659's prose calls "the one that matters most", and the resulting state is
    the one the wake has to be able to describe.
    """
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    _advance_home(krepo)
    (checkout / "index.md").write_text("resident is mid-write\n", encoding="utf-8")

    knowledge.ensure_checkout(repo, cfg)  # real fetch, real ff-skip

    if not dirty:
        # The resident discarded the in-flight edit. Clean, still behind,
        # origin refs still fresh from the fetch above.
        subprocess.run(
            ["git", "checkout", "--", "index.md"], cwd=checkout, check=True
        )
    # Guard the fixture: genuinely behind, or it pins nothing (#611).
    assert _head(checkout) != _head(krepo)
    assert not (checkout / "late-page.md").exists()
    return repo, krepo, checkout, cfg


def test_mirror_state_counts_a_real_behind_mirror(tmp_path):
    """The count comes off `HEAD..origin/<branch>` in a checkout that really
    is behind — two repos on disk, a real fetch, a real skipped merge."""
    repo, krepo, checkout, cfg = _behind_mirror(tmp_path, dirty=True)

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_BEHIND
    assert state.behind == 1
    assert state.ahead == 0
    assert state.branch == "main"
    assert state.dirty is True


def test_mirror_state_reports_clean_when_the_edit_was_discarded(tmp_path):
    """Behind *and* clean is the case the mirror cannot self-report: `git
    status` says nothing is wrong and only `HEAD..origin/main` disagrees."""
    repo, krepo, checkout, cfg = _behind_mirror(tmp_path, dirty=False)

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_BEHIND
    assert state.behind == 1
    assert state.dirty is False
    # The point of the whole check, asserted rather than assumed:
    porcelain = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=checkout, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert porcelain == ""


def test_mirror_state_counts_multiple_commits(tmp_path):
    """N is a real count, not a boolean wearing an int."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    _advance_home(krepo, "page-a.md")
    _advance_home(krepo, "page-b.md")
    _advance_home(krepo, "page-c.md")
    (checkout / "index.md").write_text("mid-write\n", encoding="utf-8")
    knowledge.ensure_checkout(repo, cfg)

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_BEHIND
    assert state.behind == 3


def test_mirror_state_is_current_after_a_successful_fast_forward(tmp_path):
    """The silence case. `ensure_checkout` on a clean behind-checkout brings
    it current, and a current mirror has nothing to say (#623)."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    _advance_home(krepo)
    knowledge.ensure_checkout(repo, cfg)
    assert _head(checkout) == _head(krepo)  # guard: really did catch up

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_CURRENT
    assert state.behind == 0


def test_mirror_state_reports_divergence_separately(tmp_path):
    """Behind *and* ahead is a third next-action: `--ff-only` will refuse
    forever, so this one never resolves itself on the next capture."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    _advance_home(krepo, "account-side.md")
    (checkout / "checkout-side.md").write_text("local page\n", encoding="utf-8")
    _commit(checkout, "checkout-side work")

    knowledge.ensure_checkout(repo, cfg)  # fetches, then ff-only refuses

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_BEHIND
    assert state.behind == 1
    assert state.ahead == 1


def test_mirror_state_is_absent_not_zero_behind_without_a_checkout(tmp_path):
    """A repo with no `.brnrd-kb/` has no mirror to be stale. That must be a
    distinguishable *status*, not a falsy count that reads as healthy —
    `active_kb_dir`/`compute_graph_stats` already cost this project one
    value carrying two meanings; this is not the second."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    assert not (repo / knowledge.CHECKOUT_DIRNAME).exists()

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_ABSENT
    assert state.status != knowledge.MIRROR_CURRENT
    assert state.absent_reason  # says which absence, not just "falsy"
    # The trap this test exists to hold shut: `behind == 0` is true here and
    # is *also* true of a healthy mirror. Anything downstream branching on
    # the count alone cannot tell these apart — so nothing may.
    healthy = knowledge.MirrorState(knowledge.MIRROR_CURRENT)
    assert state.behind == healthy.behind
    assert state.status != healthy.status


def test_mirror_state_is_absent_on_detached_head(tmp_path):
    """Detached HEAD has no `origin/<branch>` to be behind — absent, not 0."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    subprocess.run(
        ["git", "checkout", "-q", "--detach", "HEAD"], cwd=checkout, check=True
    )

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_ABSENT
    assert "branch" in state.absent_reason


def test_mirror_state_is_absent_without_an_upstream_ref(tmp_path):
    """A checkout on a branch origin has never heard of: absent, not current."""
    repo, krepo, checkout, cfg = _seeded_checkout(tmp_path)
    subprocess.run(
        ["git", "checkout", "-q", "-b", "local-only"], cwd=checkout, check=True
    )

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_ABSENT
    assert "origin/local-only" in state.absent_reason


def test_mirror_state_survives_an_unreadable_checkout(tmp_path):
    """A directory that is not a git repo must degrade to absent, not raise:
    a wake prompt does not get to die because a subprocess did."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / knowledge.CHECKOUT_DIRNAME).mkdir()

    state = knowledge.mirror_state(repo)  # must not raise

    assert state.status == knowledge.MIRROR_ABSENT


def test_mirror_state_makes_no_network_call(tmp_path, monkeypatch):
    """Wake path, so local refs only. Pinned by making any fetch fatal —
    the docstring's promise is load-bearing enough to hold in a test, since
    the obvious "fix" for a stale count is to add a fetch right here."""
    repo, krepo, checkout, cfg = _behind_mirror(tmp_path, dirty=True)
    real_run = subprocess.run

    def _no_fetch(cmd, *a, **kw):
        if isinstance(cmd, (list, tuple)) and "fetch" in [str(c) for c in cmd]:
            raise AssertionError(f"mirror_state must not fetch: {cmd}")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(knowledge.subprocess, "run", _no_fetch)

    state = knowledge.mirror_state(repo)

    assert state.status == knowledge.MIRROR_BEHIND
