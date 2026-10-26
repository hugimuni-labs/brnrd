"""Tests for the home/repo knowledge source chain."""

from pathlib import Path
import subprocess

from brr import account, daemon, gitops, knowledge
from brr.run import Run

from _helpers import (
    commit_files,
    init_git_repo,
)


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


def test_kb_url_returns_none_rather_than_raising_when_branch_probe_fails(
    tmp_path, monkeypatch,
):
    """#1340: ``_knowledge_forge_location`` (which both URL helpers funnel
    through) has no try/except of its own around
    ``gitops.current_branch`` — the three public callers
    (``kb_base_url``, ``kb_page_url``, ``knowledge_file_url``) are
    best-effort link resolvers with an established "no link, not a crash"
    contract, so a genuine git failure must land the same way any other
    unresolvable case already does: ``None``, not an uncaught raise
    reaching a card/URL renderer three calls up.
    """
    repo, cfg = _knowledge_remote_chain(tmp_path, forge=True)

    def _raise(_repo_root):
        raise gitops.CurrentBranchUnresolvable("simulated")

    monkeypatch.setattr(gitops, "current_branch", _raise)

    assert knowledge.kb_base_url(repo, cfg) is None
    assert knowledge.kb_page_url(repo, "design-managed-delivery.md", cfg) is None


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


# ── Capture net (#357): checkout → account knowledge → forge ─────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )


def _capture_chain(
    tmp_path: Path, *, checkout: bool = True, remote: bool = True,
) -> tuple[Path, dict, Path]:
    """repo(+checkout) → account knowledge (non-bare) → a bare local 'forge'.

    A real push chain, no network: the bare repo stands in for GitHub, so
    the test asserts on what actually landed there rather than on a mock.
    ``remote=False`` leaves the account knowledge repo with no remote at
    all — the never-linked shape (#1423) — rather than wiring it to
    *forge*; the returned *forge* path exists either way but is unused by
    a caller passing ``remote=False``.
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
    if remote:
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


def test_capture_never_walks_the_hearth(tmp_path):
    """#1332 privacy pin: ``knowledge.capture`` commits/pushes only
    ``account.knowledge_path`` — the hearth is a sibling directory
    (``account.hearth_path``) that root never contains, so a hearth page
    must never reach the forge, the capture manifest, or even get read.
    Driven with a real push chain (the ``_capture_chain`` fixture), not a
    mock, so the assertion is about the actual git objects that land.
    """
    from brr import account

    repo, cfg, forge = _capture_chain(tmp_path, checkout=False)
    ctx = account.resolve_context(repo, cfg, create=True)
    hearth = account.hearth_path(ctx)
    hearth.mkdir(parents=True, exist_ok=True)
    confidence = hearth / "confidence.md"
    confidence.write_text(
        "# A secret\n\nprivate stuff that must never leave this room.\n",
        encoding="utf-8",
    )
    page = tmp_path / "home" / "knowledge" / "repos" / "Gurio__brr" / "decision.md"
    page.write_text("a decision\n", encoding="utf-8")

    captured_pages: list[str] = []
    assert knowledge.capture(
        repo, "kb: capture", cfg=cfg, captured_pages=captured_pages,
    ) is True

    assert captured_pages == ["decision.md"]
    assert not _forge_has(forge, "confidence.md")
    assert not any("hearth" in p for p in captured_pages)
    # The page itself is untouched — capture never even opened it.
    assert confidence.read_text(encoding="utf-8") == (
        "# A secret\n\nprivate stuff that must never leave this room.\n"
    )


def test_capture_knowledge_credits_the_parent_when_a_strand_sweeps_first(
    tmp_path,
):
    """#1276, driven end-to-end (two runs, one shared knowledge checkout,
    no mocking of ``knowledge.capture``/``committed_pages_in_window``): a
    parent authors a kb page and dispatches a strand before committing it.
    The strand finalizes first — its own capture net's dirty-tree sweep is
    repo-scoped, not run-scoped, so it is the one that actually commits the
    parent's uncommitted page. Before the fix that sweep commit carried the
    strand's own identity trailer, the strand claimed the page as its own
    relic, and the parent's own later window read found nothing (filtered
    by its own trailer, which the sweep never carried). After the fix the
    sweep commits under the live parent's identity instead, the strand
    claims nothing, and the parent's own finalization — still to come —
    is the one that reports the page."""
    repo, cfg, _forge = _capture_chain(tmp_path, checkout=False)
    knowledge_repo = tmp_path / "home" / "knowledge"
    start = knowledge.head_oid(repo, cfg)
    assert start

    runs_dir = daemon.gitops.shared_brr_dir(repo) / "runs"
    parent = Run(
        id="run-parent", event_id="evt-parent", body="parent work",
        status="running", meta={"kb_start_oid": start},
    )
    parent.save(runs_dir)
    child = Run(
        id="run-child", event_id="evt-child", body="child work",
        meta={"spawn_parent_run_id": "run-parent", "kb_start_oid": start},
    )
    child_outbox = tmp_path / "outbox-child"
    child_outbox.mkdir()
    parent_outbox = tmp_path / "outbox-parent"
    parent_outbox.mkdir()

    # The parent authored a page and dispatched its strand before
    # committing it — still dirty in the shared checkout.
    page = knowledge_repo / "repos" / "Gurio__brr" / "parent-authored.md"
    page.write_text(
        "written by the parent, not yet committed\n", encoding="utf-8",
    )

    # The strand finalizes first.
    daemon._capture_knowledge(repo, cfg, child, outbox_dir=child_outbox)

    # The sweep commit carries the *parent's* identity, not the strand's.
    trailer = _git(
        knowledge_repo, "log", "-1",
        "--format=%(trailers:key=Brnrd-Run-Id,valueonly)",
    ).stdout.strip()
    assert trailer == "run-parent"
    # And the strand, having swept under a borrowed identity, claims
    # nothing of its own — no double-credit once the parent reports it.
    assert daemon.relics.read_reported(child_outbox) == []

    # The parent finalizes afterwards; its own commit-window read now
    # finds the sweep commit, correctly trailer-stamped as its own.
    daemon._capture_knowledge(repo, cfg, parent, outbox_dir=parent_outbox)

    assert [
        r["path"] for r in daemon.relics.read_reported(parent_outbox)
    ] == ["parent-authored.md"]


# ── Never-linked: no remote at all, not a push failure (#1423) ───────


# ── The mirror reads current (#659) ──────────────────────────────────


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# ── Reply archive ────────────────────────────────────────────────────


# ── ensure_checkout refresh (#613) ───────────────────────────────────
#
# The checkout used to be returned untouched whenever its origin still
# matched — no fetch, no pull, ever. capture() pushes only when the
# checkout is ahead and pulls only after a failed push, so behind-and-
# clean drifted permanently (measured live: 28 commits / 3 days, two
# pages `brnrd kb` could not return by any spelling). These fixtures
# each *assert their own staleness first* so they cannot quietly become
# already-current input and pin nothing (the #611 failure mode).


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


# ── mirror state, read fresh each wake (#667) ────────────────────────
#
# #659 shipped a per-capture skip *reason* printed to the daemon log; the
# reader who needs it reads a wake prompt instead. These pin the *state*
# reading that replaces it. Every fixture below reaches its behind-mirror by
# driving the real production path (`ensure_checkout` → `_refresh_checkout`
# fetches, then skips the fast-forward for a real reason) rather than by
# hand-rolling refs — a count from a stubbed subprocess proves nothing about
# the command, and a hand-built ref proves nothing about reachability.


# ── mirror identity: which repository is this a mirror *of*? (#676) ──
#
# The counts above answer "how far behind", which presumes the checkout is a
# clone of *this account's* knowledge repo at all. It was 0-behind and read as
# `current` when it was not: on 2026-07-09 a repo's `.brnrd-kb` still pointed
# at an early decoy account slot, at 0 commits, weeks after `cloud.json`
# started carrying the real one. `ensure_checkout` re-clones on that mismatch,
# but its only caller is `cli.cmd_kb` — nothing on the wake path looks.
#
# Every fixture below reaches the mis-pointed checkout the way production
# reaches it: the account resolution moves and the checkout does not.


def _account_moves_to(repo, home):
    """Repoint this repo's account resolution; the checkout is left alone."""
    (repo / ".brr" / "config").write_text(f"home.path={home}\n", encoding="utf-8")
    return {"home.path": str(home)}


# ── one dirty reading, two readers (#676) ────────────────────────────
#
# `_refresh_checkout` decides whether to *skip* on tracked modifications;
# `mirror_state` decides what to *say* about them. They were two independently
# written copies of the same `git status`, agreeing by coincidence — so a
# change to the skip condition would have left the wake confidently reporting
# "clean" about a checkout that was skipped for being dirty.


