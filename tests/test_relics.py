"""Tests for :mod:`brr.relics` — run relics collection (#200/#317)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from brr import relics, worktree

from _helpers import commit_files, init_git_repo


# ── append / read_reported ───────────────────────────────────────────


# ── derive_auto ──────────────────────────────────────────────────────


def test_derive_auto_lists_commits_branch_and_pr(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
                    cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-b", "brr/work"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="add b")
    commit_files(repo, {"c.txt": "3"}, message="add c")

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".pr").write_text("319\n", encoding="utf-8")

    # #1192: the branch relic's URL is only minted once the branch is
    # confirmed on the remote (`git ls-remote --heads`) — this test's local
    # checkout never actually pushed to the fake `origin` above, so stand
    # in for a push that genuinely landed rather than exercising the real
    # network. The "not confirmed" arm is
    # ``test_derive_auto_omits_branch_url_when_not_confirmed_on_remote``.
    monkeypatch.setattr(relics.gitops, "remote_branch_exists", lambda *a, **k: True)

    out = relics.derive_auto(repo, branch="brr/work", seed_ref="main", outbox_dir=outbox)
    kinds = [r["kind"] for r in out]
    assert kinds == ["commit", "commit", "branch", "pr"]
    commits = [r for r in out if r["kind"] == "commit"]
    assert [c["subject"] for c in commits] == ["add c", "add b"]
    for c in commits:
        assert c["url"].startswith("https://github.com/Gurio/brr/commit/")
    branch = out[2]
    assert branch["name"] == "brr/work"
    assert branch["url"] == "https://github.com/Gurio/brr/tree/brr/work"
    pr = out[3]
    assert pr == {
        "kind": "pr", "number": 319,
        "url": "https://github.com/Gurio/brr/pull/319",
    }


def test_derive_auto_omits_branch_url_when_not_confirmed_on_remote(
    tmp_path: Path, monkeypatch,
):
    """#1192: a branch relic must not carry a URL nobody checked.

    A remote is configured (so a URL *could* be templated) and the branch
    has commits beyond seed (so the old code would have minted one) — but
    `git ls-remote` says the branch isn't there, standing in for a push
    that failed or hasn't landed yet. The relic still names the branch —
    something happened, worth a mention — but the URL stays absent rather
    than pointing at a page that 404s. Same fixture shape as the confirmed
    case in `test_derive_auto_lists_commits_branch_and_pr`, opposite
    verdict from the ls-remote check.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
                    cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-b", "brr/work"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="add b")

    monkeypatch.setattr(relics.gitops, "remote_branch_exists", lambda *a, **k: False)

    out = relics.derive_auto(repo, branch="brr/work", seed_ref="main", outbox_dir=None)
    branch = next(r for r in out if r["kind"] == "branch")
    assert branch == {"kind": "branch", "name": "brr/work", "url": None}


# ── live_summary / collect / counts_by_kind ──────────────────────────


# ── collect / counts_by_kind ─────────────────────────────────────────


# ── dedupe ───────────────────────────────────────────────────────────


# ── scope_roots: the repo_root vs. the run's own tree (#1776) ──────────


# ── run-identity filtering of the shared-checkout window (#575) ────────


# ── seed resolution in a create_clone strand's own tree (#1788) ────────


def test_commits_since_seed_seed_ref_alone_recovers_via_origin_fallback(tmp_path: Path):
    """A ``create_clone`` strand (#746) is a ``git clone --shared`` whose
    local heads are only whatever branch the host had checked out at clone
    time — every other branch, including the run's seed, is only a
    ``refs/remotes/origin/*`` name there. Before the fix, ``git merge-base
    main <branch>`` failed outright ("Not a valid object name main") and
    the comparison never ran at all. One leg of the fix (``origin/<seed>``
    for an unqualified name) recovers this case even with no recorded
    ``seed_oid`` — the ordinary shape, since a clone still carries
    ``origin/main`` as a remote-tracking ref."""
    host = tmp_path / "host"
    init_git_repo(host)
    commit_files(host, {"a.txt": "1"}, message="seed")
    # The host was on an unrelated branch at clone time — what leaves the
    # clone with no local `main` at all.
    subprocess.run(["git", "checkout", "-b", "unrelated"], cwd=host, check=True)

    clone, branch = worktree.create_clone(host, "run-1788a", base_ref="main")
    assert subprocess.run(
        ["git", "branch", "--list", "main"],
        cwd=clone, capture_output=True, text=True,
    ).stdout.strip() == ""
    commit_files(clone, {"b.txt": "2"}, message="the strand's work")

    commits, note = relics._commits_since_seed(clone, branch, "main")
    assert note is None
    assert [s for _, s, _, _ in commits] == ["the strand's work"]


def test_commits_since_seed_resolves_via_recorded_seed_oid(tmp_path: Path):
    """The deterministic leg: an oid always resolves in a clone (the
    object store is shared via ``--shared``) regardless of what the local
    branch namespace or the ``origin`` remote happen to carry — unlike the
    ``origin/<seed>`` fallback, which depends on the remote still naming
    the same ref. Proven here by stripping the remote entirely."""
    host = tmp_path / "host"
    init_git_repo(host)
    seed_oid = commit_files(host, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "unrelated"], cwd=host, check=True)

    clone, branch = worktree.create_clone(host, "run-1788b", base_ref="main")
    commit_files(clone, {"b.txt": "2"}, message="the strand's work")
    subprocess.run(["git", "remote", "remove", "origin"], cwd=clone, check=True)

    # Without the recorded oid, nothing resolves: no local `main`, no
    # `origin/main` either now that the remote is gone.
    commits, note = relics._commits_since_seed(clone, branch, "main")
    assert commits == []
    assert note == "seed unresolvable at 'main'"

    # With it, the comparison succeeds regardless.
    commits, note = relics._commits_since_seed(
        clone, branch, "main", seed_oid=seed_oid,
    )
    assert note is None
    assert [s for _, s, _, _ in commits] == ["the strand's work"]


def test_derive_auto_folds_an_unresolvable_seed_into_a_note_relic(tmp_path: Path):
    """The visible-degrade half of #1788: when no candidate resolves, the
    manifest must not read identically to "this run made nothing" — a
    ``note`` relic says so instead of a silently empty list."""
    host = tmp_path / "host"
    init_git_repo(host)
    seed_oid = commit_files(host, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "unrelated"], cwd=host, check=True)
    clone, branch = worktree.create_clone(host, "run-1788c", base_ref="main")
    commit_files(clone, {"b.txt": "2"}, message="the strand's work")
    subprocess.run(["git", "remote", "remove", "origin"], cwd=clone, check=True)

    records = relics.collect(clone, branch=branch, seed_ref="main", outbox_dir=None)
    assert [r for r in records if r["kind"] == "commit"] == []
    notes = [r for r in records if r["kind"] == "note"]
    assert notes == [{"kind": "note", "text": "seed unresolvable at 'main'"}]

    # The fix, end to end through the same entrypoint the bolt's produce
    # check and the ledger row call.
    records = relics.collect(
        clone, branch=branch, seed_ref="main", outbox_dir=None,
        seed_oid=relics.seed_oid_of({"seed_oid": seed_oid}),
    )
    assert [r["subject"] for r in records if r["kind"] == "commit"] == [
        "the strand's work",
    ]
    assert not any(r["kind"] == "note" for r in records)


# ── merge relics: merges performed are their own block (2026-07-21) ──


def _merge_repo(tmp_path: Path) -> Path:
    """A repo on main with one feature branch merged via merge commit."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
                    cwd=repo, check=True)
    return repo


def test_derive_auto_squash_merge_requires_github_committer(tmp_path: Path):
    repo = _merge_repo(tmp_path)
    start = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.strip()
    # Hand-written issue reference: stays an ordinary commit.
    commit_files(repo, {"b.txt": "2"}, message="fix retention race (#501)")
    # GitHub-committed squash landing: promoted to a merge relic.
    (repo / "c.txt").write_text("3", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=GitHub", "-c", "user.email=noreply@github.com",
         "commit", "-m", "cloud media ingest (#525)"],
        cwd=repo, check=True,
    )

    out = relics.derive_auto(repo, branch="main", seed_ref=start, outbox_dir=None)
    kinds = [r["kind"] for r in out]
    assert kinds == ["merge", "commit", "branch"]
    assert out[0]["pr"] == 525
    assert out[0]["url"] == "https://github.com/Gurio/brr/pull/525"
    assert out[1]["subject"] == "fix retention race (#501)"


# ── live portal counts / compact tail (#342) ─────────────────────────


def _write_portal_state(brr_dir: Path, event_id: str, produce) -> None:
    import json

    outbox = brr_dir / "outbox" / event_id
    outbox.mkdir(parents=True)
    (outbox / "portal-state.json").write_text(
        json.dumps({"version": 1, "produce": produce}), encoding="utf-8",
    )


def test_live_portal_counts_reads_produce_facet(tmp_path: Path):
    brr_dir = tmp_path / ".brr"
    _write_portal_state(
        brr_dir, "evt-1",
        {"known": True, "counts": {"commit": 2, "kb": 1, "branch": 1}},
    )
    assert relics.live_portal_counts(brr_dir, "evt-1") == {
        "commit": 2, "kb": 1, "branch": 1,
    }


def test_live_portal_counts_absent_paths_degrade_to_none(tmp_path: Path):
    brr_dir = tmp_path / ".brr"
    # No event id at all (ad-hoc session presence entry).
    assert relics.live_portal_counts(brr_dir, None) is None
    assert relics.live_portal_counts(brr_dir, "") is None
    # No capsule on disk.
    assert relics.live_portal_counts(brr_dir, "evt-missing") is None
    # Facet explicitly unknown.
    _write_portal_state(brr_dir, "evt-unknown", {"known": False})
    assert relics.live_portal_counts(brr_dir, "evt-unknown") is None
    # Torn / half-written JSON.
    outbox = brr_dir / "outbox" / "evt-torn"
    outbox.mkdir(parents=True)
    (outbox / "portal-state.json").write_text('{"produce": {', encoding="utf-8")
    assert relics.live_portal_counts(brr_dir, "evt-torn") is None


def test_live_portal_counts_known_but_empty_is_empty_dict(tmp_path: Path):
    """Known-with-no-produce is `{}` (renderers drop the line), not None —
    the distinction mirrors the facet's own known/unknown contract."""
    brr_dir = tmp_path / ".brr"
    _write_portal_state(brr_dir, "evt-idle", {"known": True, "counts": {}})
    assert relics.live_portal_counts(brr_dir, "evt-idle") == {}


def test_live_portal_counts_gates_hostile_kinds_and_values(tmp_path: Path):
    """A reported relic's `kind` is resident-authored free text that flows
    into chat markup and the publish payload — gate it on a conservative
    identifier shape, and drop non-integer or non-positive counts."""
    brr_dir = tmp_path / ".brr"
    _write_portal_state(
        brr_dir, "evt-h",
        {"known": True, "counts": {
            "<script>": 3, "commit": 2, "weird kind": 1, "issue": 0,
            "pr": "not-a-number", "comment": -4, "x" * 40: 2,
        }},
    )
    assert relics.live_portal_counts(brr_dir, "evt-h") == {"commit": 2}


def test_counts_phrase_matches_issue_example():
    # The #342 ask, verbatim: `relics: 2 commits · 1 page`.
    assert relics.counts_phrase({"commit": 2, "kb": 1}) == "2 commits · 1 page"


# ── design-the-water-line.md "The kb is the reef": named pages, not counts ──


def test_live_portal_kb_pages_reads_records_not_counts(tmp_path: Path):
    """Same capsule `live_portal_counts` reads, one field further in: the
    full manifest under `records`, filtered to `kind == "kb"` and reduced
    to path (+ url) — never the resident-facing full relic shape."""
    brr_dir = tmp_path / ".brr"
    _write_portal_state(
        brr_dir, "evt-1",
        {
            "known": True,
            "counts": {"commit": 1, "kb": 2},
            "records": [
                {"kind": "commit", "sha": "abc123", "subject": "wip"},
                # A `file` relic carries a `path` too — so this is the record
                # that actually pins the `kind == "kb"` filter. Without it the
                # commit above is filtered by the *pathless* guard instead,
                # and dropping the kind check entirely breaks nothing: the
                # reef would fill with every produced file, and every test
                # here would still pass.
                {"kind": "file", "path": "src/brr/relics.py"},
                {"kind": "kb", "path": "design-the-water-line.md", "url": "https://x/reef"},
                {"kind": "kb", "path": "design-the-crossing.md"},
            ],
        },
    )
    assert relics.live_portal_kb_pages(brr_dir, "evt-1") == [
        {"path": "design-the-water-line.md", "url": "https://x/reef"},
        {"path": "design-the-crossing.md"},
    ]


def test_live_portal_kb_pages_absent_paths_degrade_to_none(tmp_path: Path):
    brr_dir = tmp_path / ".brr"
    assert relics.live_portal_kb_pages(brr_dir, None) is None
    assert relics.live_portal_kb_pages(brr_dir, "evt-missing") is None
    _write_portal_state(brr_dir, "evt-unknown", {"known": False})
    assert relics.live_portal_kb_pages(brr_dir, "evt-unknown") is None
    # A facet known before `records` shipped (only `counts`) degrades the
    # same way as no capsule at all — absent stays absent, never a guess.
    _write_portal_state(brr_dir, "evt-old", {"known": True, "counts": {"kb": 1}})
    assert relics.live_portal_kb_pages(brr_dir, "evt-old") is None


def test_live_portal_kb_pages_known_but_empty_is_empty_list(tmp_path: Path):
    brr_dir = tmp_path / ".brr"
    _write_portal_state(brr_dir, "evt-idle", {"known": True, "counts": {}, "records": []})
    assert relics.live_portal_kb_pages(brr_dir, "evt-idle") == []


def test_live_portal_kb_pages_drops_pathless_records_and_bounds_the_list(tmp_path: Path):
    records = [{"kind": "kb", "path": ""}]
    records += [{"kind": "kb", "path": f"page-{i}.md"} for i in range(20)]
    brr_dir = tmp_path / ".brr"
    _write_portal_state(brr_dir, "evt-h", {"known": True, "counts": {}, "records": records})
    pages = relics.live_portal_kb_pages(brr_dir, "evt-h")
    assert pages is not None
    assert len(pages) == relics._LIVE_KB_PAGES_MAX
    assert pages[0] == {"path": "page-0.md"}


def test_counts_phrase_singular_plural_and_order():
    phrase = relics.counts_phrase(
        {"kb": 2, "commit": 1, "pr": 1, "issue": 3, "merge": 1},
    )
    # Produce-first order mirrors _TAIL_NOUNS, not dict order.
    assert phrase == "1 commit · 1 merge · 1 PR · 3 issues · 2 pages"


def test_counts_phrase_excludes_branch_and_summary_but_keeps_unknown():
    phrase = relics.counts_phrase(
        {"commit": 2, "branch": 1, "summary": 1, "artifact": 2},
    )
    assert phrase == "2 commits · 2 artifact"


def test_counts_phrase_empty_and_none_are_empty():
    assert relics.counts_phrase(None) == ""
    assert relics.counts_phrase({}) == ""
    assert relics.counts_phrase({"branch": 1}) == ""


# ── the issue split: created vs completed (#686) ─────────────────────
#
# The grammar always carried `action`; `counts_by_kind` flattened it away
# before any renderer saw it, so a run that filed three issues and a run
# that closed three rendered identically as "3 issues". These pin the split
# and — the actual defect — that an *unrecorded* half never renders as an
# affirmative zero.


def test_issue_actions_splits_created_and_completed():
    actions = relics.issue_actions([
        {"kind": "issue", "number": 1, "action": "opened"},
        {"kind": "issue", "number": 2, "action": "opened"},
        {"kind": "issue", "number": 3, "action": "closed"},
        {"kind": "commit", "sha": "abc1234"},
        {"kind": "pr", "number": 9},
    ])
    assert actions == relics.IssueActions(created=2, completed=1)


def test_issue_actions_tolerates_synonyms_and_casing():
    actions = relics.issue_actions([
        {"kind": "issue", "number": 1, "action": "Created"},
        {"kind": "issue", "number": 2, "action": " FILED "},
        {"kind": "issue", "number": 3, "action": "resolved"},
        {"kind": "issue", "number": 4, "action": "completed"},
    ])
    assert actions == relics.IssueActions(created=2, completed=2)


def test_issue_actions_actionless_record_lands_in_neither_bucket():
    """Every hand-written issue relic predating `brnrd relic issue` carries no
    `action`. It must not become a wrong fact in either direction — it stays
    counted as an issue the run touched (counts_by_kind) and unattributed
    here."""
    records = [
        {"kind": "issue", "number": 317},
        {"kind": "issue", "number": 566, "action": ""},
        {"kind": "issue", "number": 686, "action": "commented"},
    ]
    assert relics.issue_actions(records) == relics.IssueActions(
        created=0, completed=0, unattributed=3,
    )
    # The relic itself is not lost — only the action claim is declined.
    assert relics.counts_by_kind(records) == {"issue": 3}


def test_issues_phrase_renders_both_facts_never_a_ratio():
    phrase = relics.issues_phrase(relics.IssueActions(created=3, completed=2))
    assert phrase == "3 created · 2 completed"
    assert "/" not in phrase and "%" not in phrase


def test_issues_phrase_distinguishes_unrecorded_from_zero():
    """The #686 defect, pinned: brnrd cannot observe an issue close, so an
    empty bucket is *unrecorded*, never an attested zero. A run that filed
    three and recorded no closes must not read as one that closed none."""
    assert relics.issues_phrase(
        relics.IssueActions(created=3),
    ) == "3 created · completed unrecorded"
    assert relics.issues_phrase(
        relics.IssueActions(completed=2),
    ) == "created unrecorded · 2 completed"
    assert "0 completed" not in relics.issues_phrase(
        relics.IssueActions(created=3),
    )


def test_issues_phrase_is_silent_without_a_recorded_action():
    """counts_phrase's contract — nothing to say ⇒ no line — holds here, so
    an idle card gains no noise and the unrecorded flag only ever fires
    beside a real count, where the missing half is the finding."""
    assert relics.issues_phrase(None) == ""
    assert relics.issues_phrase(relics.IssueActions()) == ""
    # Actionless legacy records alone are not an action to report.
    assert relics.issues_phrase(relics.IssueActions(unattributed=2)) == ""


def test_live_portal_issue_actions_reads_the_same_capsule(tmp_path: Path):
    brr_dir = tmp_path / ".brr"
    _write_portal_state(
        brr_dir, "evt-i",
        {
            "known": True, "counts": {"issue": 3},
            "issue_actions": {"created": 2, "completed": 1, "unattributed": 0},
        },
    )
    assert relics.live_portal_issue_actions(brr_dir, "evt-i") == (
        relics.IssueActions(created=2, completed=1)
    )


def test_live_portal_issue_actions_absent_paths_degrade_to_none(tmp_path: Path):
    """`None` is *unknown*, never IssueActions() — an older daemon's capsule
    carries no split, and a renderer must not read that as "zero of each"."""
    brr_dir = tmp_path / ".brr"
    assert relics.live_portal_issue_actions(brr_dir, None) is None
    assert relics.live_portal_issue_actions(brr_dir, "evt-missing") is None
    _write_portal_state(brr_dir, "evt-unknown", {"known": False})
    assert relics.live_portal_issue_actions(brr_dir, "evt-unknown") is None
    # A capsule from before the split existed.
    _write_portal_state(brr_dir, "evt-old", {"known": True, "counts": {"issue": 2}})
    assert relics.live_portal_issue_actions(brr_dir, "evt-old") is None
    # Hostile / non-integer values are refused rather than coerced.
    _write_portal_state(
        brr_dir, "evt-bad",
        {"known": True, "issue_actions": {"created": "lots", "completed": 1}},
    )
    assert relics.live_portal_issue_actions(brr_dir, "evt-bad") is None


# ── forge link derivation for reported relics ────────────────────────
#
# Regression (maintainer, 2026-07-22, on run-260722-1634-7t6x): the run
# node's Produce block rendered "🎫 issue #566 (opened)" as bare text.
# Only derive_auto minted URLs, so every self-reported thread relic
# reached the renderer link-less.


