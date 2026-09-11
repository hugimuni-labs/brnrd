"""One resident, one dominion — move 1 of design-one-resident-per-machine.md.

Pins the resolution switch (``<home>/dominion`` leads once it exists, the
per-repo dominion until then), the schedule reading the one dominion whatever
repo the daemon started in, the global kb (``knowledge/global`` with
``_cross-repo`` as its alias) as the home root's own kb, and
``brnrd dominion consolidate`` (dry run moves nothing; apply moves, commits,
and is idempotent; a dirty repo refuses).
"""

from __future__ import annotations

import time

from brr import account, daemon, dominion, gitops, knowledge, protocol
from brr import dominion_consolidate as consolidate

from _helpers import commit_files, init_git_repo


def _account_home(tmp_path, *, label="Gurio/brr"):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"README.md": "main\n"}, message="init main")
    (repo / ".brr").mkdir()
    home = tmp_path / "home"
    cfg = {
        "home.path": str(home),
        "home.kind": "account",
        "account.id": "acct-1",
        "repo.label": label,
    }
    ctx = account.resolve_context(repo, cfg)
    return repo, home, cfg, ctx


def _commit_all(root, message="fixture"):
    gitops._git(root, "add", "-A", check=True)
    gitops._git(root, "commit", "-q", "--allow-empty", "-m", message, check=True)


def _count(root):
    return int(gitops._git(root, "rev-list", "--count", "HEAD", check=True).stdout.strip())


def _status(root):
    return gitops._git(root, "status", "--porcelain", check=True).stdout.strip()


# ── 1. the one dominion resolves first, once it exists ────────────────


def test_per_repo_dominion_leads_until_the_one_dominion_exists(tmp_path):
    repo, home, cfg, ctx = _account_home(tmp_path)
    repo_dom = account.repo_dominion_path(ctx, "Gurio/brr")
    dominion.seed_account_dominion(repo_dom)

    pre = dominion.resident_dominion_candidates(
        repo, cfg, include_legacy=False, account_context=ctx,
    )
    assert pre[0].path == repo_dom

    home_dom = account.home_dominion_path(ctx)
    assert home_dom == home / "dominion"
    home_dom.mkdir()

    post = dominion.resident_dominion_candidates(
        repo, cfg, include_legacy=False, account_context=ctx,
    )
    # The post-consolidation pin: the one dominion first, same capture repo,
    # the per-repo path kept behind it as the fallback.
    assert post[0].path == home_dom
    assert post[0].capture_root == ctx.dominion_repo
    assert post[1].path == repo_dom


# ── 2. the schedule reads the one dominion, whatever repo started ────


def test_schedule_fires_from_the_one_dominion_in_a_second_repo(tmp_path):
    _repo, home, _cfg, ctx = _account_home(tmp_path)
    other = tmp_path / "other"
    init_git_repo(other)
    commit_files(other, {"README.md": "other\n"}, message="init other")
    brr_dir = other / ".brr"
    brr_dir.mkdir()
    inbox = brr_dir / "inbox"
    other_cfg = {
        "home.path": str(home), "home.kind": "account", "account.id": "acct-1",
        "repo.label": "Gurio/other",
    }
    other_ctx = account.resolve_context(other, other_cfg)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    other_dom = account.repo_dominion_path(other_ctx, "Gurio/other")
    other_dom.mkdir(parents=True)
    (other_dom / "schedule.md").write_text(
        f"## Stale Local\nat: {past}\nthe per-repo schedule\n", encoding="utf-8",
    )
    home_dom = account.home_dominion_path(other_ctx)
    home_dom.mkdir()
    (home_dom / "schedule.md").write_text(
        f"## One Schedule\nat: {past}\nthe one schedule\n", encoding="utf-8",
    )

    daemon._fire_due_schedules(
        other, brr_dir, inbox, other_cfg, account_context=other_ctx,
    )

    pending = protocol.list_pending(inbox)
    assert [ev["schedule_id"] for ev in pending] == ["one-schedule"]
    # The firing names its target repo as a location: the account default,
    # not the repo the daemon happened to start in.
    assert pending[0]["repo_label"] == ctx.default_repo.label == "Gurio/brr"


# ── 3. the global kb ──────────────────────────────────────────────────


def test_home_root_resolves_the_global_kb_and_brnrd_keeps_its_own(tmp_path):
    repo, home, cfg, ctx = _account_home(tmp_path)
    kroot = account.knowledge_path(ctx)
    (kroot / "global").mkdir(parents=True)
    (kroot / "global" / "index.md").write_text("global\n", encoding="utf-8")
    (kroot / "repos" / "Gurio__brr").mkdir(parents=True)
    (kroot / "repos" / "Gurio__brr" / "index.md").write_text("brr\n", encoding="utf-8")
    # Even a per-repo bucket named after the home is not the home's kb.
    (kroot / "repos" / "Gurio__brnrd-home").mkdir(parents=True)
    home_cfg = dict(cfg, **{"repo.label": "Gurio/brnrd-home"})

    assert knowledge.active_kb_dir(home, home_cfg) == kroot / "global"
    assert knowledge._split_scope(ctx, home, "Gurio/brnrd-home") == kroot / "global"
    # brnrd's own label is unchanged: its per-repo kb.
    assert knowledge.active_kb_dir(repo, cfg) == kroot / "repos" / "Gurio__brr"


def test_a_label_with_no_kb_dir_resolves_the_global_kb(tmp_path):
    repo, _home, cfg, ctx = _account_home(tmp_path, label="Gurio/nokb")
    kroot = account.knowledge_path(ctx)
    (kroot / "global").mkdir(parents=True)

    assert knowledge.active_kb_dir(repo, cfg) == kroot / "global"
    assert knowledge._split_scope(ctx, repo, "Gurio/nokb") == kroot / "global"


def test_cross_repo_is_honoured_as_the_global_kb_alias(tmp_path):
    repo, _home, cfg, ctx = _account_home(tmp_path)
    kroot = account.knowledge_path(ctx)
    # Neither exists: a fresh home writes under the new name.
    assert account.account_knowledge_path(ctx) == kroot / "global"

    (kroot / "_cross-repo").mkdir(parents=True)
    (kroot / "_cross-repo" / "index.md").write_text("old slug\n", encoding="utf-8")
    assert account.account_knowledge_path(ctx) == kroot / "_cross-repo"
    names = {s.name: s.root for s in knowledge.sources(repo, cfg)}
    assert names["home knowledge (account)"] == kroot / "_cross-repo"

    (kroot / "global").mkdir()
    assert account.account_knowledge_path(ctx) == kroot / "global"


# ── 4. brnrd dominion consolidate ─────────────────────────────────────


def _two_layout_home(tmp_path):
    repo, home, cfg, ctx = _account_home(tmp_path)
    repo_dom = account.repo_dominion_path(ctx, "Gurio/brr")
    repo_dom.mkdir(parents=True)
    (repo_dom / "notebook.md").write_text("the notebook\n", encoding="utf-8")
    (repo_dom / "schedule.md").write_text("## Tick\nevery: 5h\ntick\n", encoding="utf-8")
    (repo_dom / "drafts").mkdir()
    (repo_dom / "drafts" / "a.md").write_text("draft\n", encoding="utf-8")
    other_dom = account.repo_dominion_path(ctx, "Gurio/site")
    other_dom.mkdir(parents=True)
    (other_dom / "README.md").write_text("site dominion\n", encoding="utf-8")
    (other_dom / "schedule.md").write_text(
        "## Site Sweep\nevery: 24h\nsweep\n", encoding="utf-8",
    )
    _commit_all(home, "fixture: two layouts")
    kroot = account.knowledge_path(ctx)
    init_git_repo(kroot)
    commit_files(kroot, {"_cross-repo/index.md": "# global\n"}, message="kb")
    ctx = account.resolve_context(repo, cfg, create=False)
    return repo, home, cfg, ctx, repo_dom, other_dom, kroot


def test_consolidate_dry_run_prints_the_plan_and_moves_nothing(tmp_path):
    _repo, home, _cfg, ctx, repo_dom, other_dom, kroot = _two_layout_home(tmp_path)
    before = (_count(home), _count(kroot))

    plan = consolidate.build_plan(ctx)
    text = consolidate.render_plan(plan)

    assert plan.blockers == []
    assert "dry run — nothing moved" in text
    assert "repos/Gurio__brr/dominion/ → dominion/" in text
    assert "repos/Gurio__site/dominion/ → dominion/places/Gurio__site/" in text
    assert "knowledge/_cross-repo/ → knowledge/global/" in text
    assert "1 schedule entry in Gurio__site's schedule.md will not fire" in text
    assert "re-run with --apply" in text
    assert not (home / "dominion").exists()
    assert (repo_dom / "notebook.md").exists()
    assert not (kroot / "global").exists()
    assert (_count(home), _count(kroot)) == before


def test_consolidate_apply_moves_commits_and_is_idempotent(tmp_path):
    repo, home, cfg, ctx, repo_dom, other_dom, kroot = _two_layout_home(tmp_path)
    before = (_count(home), _count(kroot))

    done = consolidate.apply_plan(consolidate.build_plan(ctx), today="2026-09-11")

    assert len(done) == 2  # one commit in the home, one in the knowledge repo
    assert (_count(home), _count(kroot)) == (before[0] + 1, before[1] + 1)
    assert _status(home) == "" and _status(kroot) == ""
    one = home / "dominion"
    assert (one / "notebook.md").read_text(encoding="utf-8") == "the notebook\n"
    assert (one / "drafts" / "a.md").exists()
    assert (one / "places" / "Gurio__site" / "README.md").exists()
    assert "`Gurio__site/` ← `repos/Gurio__site/dominion/`" in (
        one / "places" / "README.md"
    ).read_text(encoding="utf-8")
    assert sorted(p.name for p in repo_dom.iterdir()) == ["MOVED.md"]
    assert "dominion/" in (repo_dom / "MOVED.md").read_text(encoding="utf-8")
    assert sorted(p.name for p in other_dom.iterdir()) == ["MOVED.md"]
    assert (kroot / "global" / "index.md").exists()
    assert sorted(p.name for p in (kroot / "_cross-repo").iterdir()) == ["MOVED.md"]
    # Tracked as moves, not as delete + add of unrelated files.
    log = gitops._git(home, "log", "-1", "--name-status", "-M", check=True).stdout
    assert "R100\trepos/Gurio__brr/dominion/notebook.md\tdominion/notebook.md" in log

    # The code now reads the new layout.
    first = dominion.resident_dominion_candidates(
        repo, cfg, include_legacy=False, account_context=ctx,
    )[0]
    assert first.path == one
    assert account.account_knowledge_path(ctx) == kroot / "global"

    # Idempotent: a second run finds nothing and commits nothing.
    again = consolidate.build_plan(ctx)
    assert again.empty and again.blockers == []
    assert "nothing to move" in consolidate.render_plan(again)
    assert consolidate.apply_plan(again) == []
    assert (_count(home), _count(kroot)) == (before[0] + 1, before[1] + 1)


def test_consolidate_refuses_a_dirty_repo_and_moves_nothing(tmp_path):
    _repo, home, _cfg, ctx, repo_dom, _other, kroot = _two_layout_home(tmp_path)
    (home / "stray.md").write_text("uncommitted\n", encoding="utf-8")

    plan = consolidate.build_plan(ctx)

    assert any("uncommitted change" in b for b in plan.blockers)
    assert "refused — fix these first" in consolidate.render_plan(plan, applying=True)
    try:
        consolidate.apply_plan(plan)
    except consolidate.ConsolidateError:
        pass
    else:  # pragma: no cover - the assertion is the refusal
        raise AssertionError("a dirty home must refuse")
    assert (repo_dom / "notebook.md").exists()
    assert not (home / "dominion").exists()
    assert not (kroot / "global").exists()
