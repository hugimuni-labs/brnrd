"""Tests for `brr.notes_preflight`'s pitfall-store checks.

Focus: `pitfall-cites-closed-issue` (#1298's own drift, turned into a
deterministic check — a lesson stayed in the present tense for weeks after
the ticket it cited shipped a fix). Two guards carry the whole feature and
get their own explicit-failure tests, not just a happy path:

- an absent/errored cache must never render as "closed" (unknown != closed);
- a retired entry (`## (retired YYYY-MM-DD) …`) must never fire, even when
  it still cites the ticket by number — this dominion's own live store does
  exactly that, in the retirement notice itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from brr import forge_issue_cache, forge_pr_cache, notes_preflight
from brr.pitfalls import Pitfall

from _helpers import init_git_repo
import subprocess


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/Gurio/brr.git"],
        cwd=repo, check=True,
    )
    return repo


def _seed(dom: Path, text: str) -> None:
    dom.mkdir(parents=True, exist_ok=True)
    (dom / "pitfalls.md").write_text(text, encoding="utf-8")


def _write_issue_cache(repo: Path, issues: dict) -> None:
    path = forge_issue_cache.cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": "2026-09-01T00:00:00Z", "issues": issues}),
        encoding="utf-8",
    )


def _write_pr_cache(repo: Path, prs: list) -> None:
    path = forge_pr_cache.cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": "2026-09-01T00:00:00Z", "prs": prs}),
        encoding="utf-8",
    )


# ── extract_issue_refs ────────────────────────────────────────────────


def test_extract_issue_refs_finds_bare_hash_and_urls():
    p = Pitfall(
        title="A lesson",
        triggers=["x"],
        body=(
            "Fixed by #1298. See also https://github.com/o/r/pull/9 and "
            "https://github.com/o/r/issues/42."
        ),
    )
    refs = {(r.number, r.kind) for r in notes_preflight.extract_issue_refs(p)}
    assert refs == {(1298, "ambiguous"), (9, "pr"), (42, "issue")}


def test_extract_issue_refs_ignores_the_trigger_line():
    """A bare number in `trigger:` is a matching keyword, not a citation —
    `pitfalls.parse_pitfalls` already strips that line out of `body`, and
    this store's own "Closely-spaced message fragments" entry relies on
    exactly that (`trigger: ..., #128, ...`)."""
    p = Pitfall(title="Fragments", triggers=["#128", "burst"], body="No numbers here.")
    assert notes_preflight.extract_issue_refs(p) == []


def test_extract_issue_refs_dedupes_and_prefers_the_specific_kind():
    p = Pitfall(
        title="t", triggers=[],
        body="#9 first, then confirmed at https://github.com/o/r/pull/9.",
    )
    refs = notes_preflight.extract_issue_refs(p)
    assert [(r.number, r.kind) for r in refs] == [(9, "pr")]


def test_extract_issue_refs_skips_short_runs():
    p = Pitfall(title="t", triggers=[], body="exit code #1, not a ticket.")
    assert notes_preflight.extract_issue_refs(p) == []


# ── is_retired ──────────────────────────────────────────────────────


def test_is_retired_matches_the_stores_own_convention():
    retired = Pitfall(
        title="(retired 2026-09-01) A host-env parent…", triggers=["x"], body="",
    )
    live = Pitfall(title="A host-env parent…", triggers=["x"], body="")
    assert notes_preflight.is_retired(retired) is True
    assert notes_preflight.is_retired(live) is False


# ── check_pitfall_issue_refs: the two load-bearing guards ────────────


def test_fires_on_a_closed_issue_citation(tmp_path):
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(dom, "## Host strand clone shape\ntrigger: strand\nFixed by #1298.\n")
    _write_issue_cache(repo, {"1298": {"number": 1298, "state": "CLOSED", "closed_at": "2026-08-05T00:00:00Z"}})

    findings = notes_preflight.check_pitfall_issue_refs(dom, repo)
    assert len(findings) == 1
    f = findings[0]
    assert f.type == "pitfall-cites-closed-issue"
    assert "Host strand clone shape" in f.target
    assert "#1298" in f.description
    assert "2026-08-05" in f.description
    assert "retire the entry" in f.description


def test_open_issue_citation_is_silent(tmp_path):
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(dom, "## Still live\ntrigger: x\nTracked at #1298.\n")
    _write_issue_cache(repo, {"1298": {"number": 1298, "state": "OPEN", "closed_at": None}})

    assert notes_preflight.check_pitfall_issue_refs(dom, repo) == []


def test_absent_cache_never_reads_as_closed(tmp_path):
    """The guard that matters most: no cache at all must never be treated
    as 'closed' — the exact bug this check exists to avoid reproducing one
    layer up from the failure it's meant to catch."""
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(dom, "## Cites something\ntrigger: x\nFixed by #1298.\n")
    # No forge_issue_cache.json, no forge_pr_cache.json written at all.

    assert notes_preflight.check_pitfall_issue_refs(dom, repo) == []


def test_errored_cache_never_reads_as_closed(tmp_path):
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(dom, "## Cites something\ntrigger: x\nFixed by #1298.\n")
    _write_issue_cache(repo, None)
    path = forge_issue_cache.cache_path(repo)
    path.write_text(
        json.dumps({"fetched_at": "2026-09-01T00:00:00Z", "issues": None, "error": "gh: not logged in"}),
        encoding="utf-8",
    )

    assert notes_preflight.check_pitfall_issue_refs(dom, repo) == []


def test_a_retired_entry_never_fires_even_though_it_still_cites_the_ticket(tmp_path):
    """This dominion's own live store: the retirement notice explains
    *why* it retired by quoting the closed ticket — that citation must stay
    quiet forever, or the entry that says 'don't trust this any more' would
    itself keep re-triggering the exact drift it documents."""
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(
        dom,
        "## (retired 2026-09-01) A host-env parent that commits while its "
        "strands fly loses their work\n"
        "trigger: spawn, strand, host\n"
        "Retired — #1298 is closed, the clone shape fixed it.\n",
    )
    _write_issue_cache(repo, {"1298": {"number": 1298, "state": "CLOSED", "closed_at": "2026-08-05T00:00:00Z"}})

    assert notes_preflight.check_pitfall_issue_refs(dom, repo) == []


def test_ambiguous_ref_checked_against_both_caches(tmp_path):
    """A bare `#N` with no cache hit in issues but a hit in PRs (MERGED)
    still fires — GitHub's shared numbering means a bare ref could be
    either, and this dominion's own entries cite PR numbers bare (e.g.
    '#1004 MERGED ... closes #1000')."""
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(dom, "## Shipped by a PR\ntrigger: x\nLanded in #1004.\n")
    _write_pr_cache(repo, [
        {"number": 1004, "state": "MERGED", "merged_at": "2026-08-03T00:00:00Z",
         "branch": "brr/x", "closed_at": "2026-08-03T00:00:00Z"},
    ])

    findings = notes_preflight.check_pitfall_issue_refs(dom, repo)
    assert len(findings) == 1
    assert "#1004" in findings[0].description
    assert "2026-08-03" in findings[0].description


def test_explicit_issue_url_is_not_checked_against_pr_cache(tmp_path):
    """A ref that explicitly names /issues/ must not be satisfied by a PR
    cache entry sharing the same number — the URL already disambiguated."""
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(dom, "## t\ntrigger: x\nSee https://github.com/o/r/issues/42.\n")
    _write_pr_cache(repo, [
        {"number": 42, "state": "MERGED", "merged_at": "2026-08-03T00:00:00Z", "branch": "brr/x"},
    ])
    # No issue cache at all: unknown.

    assert notes_preflight.check_pitfall_issue_refs(dom, repo) == []


def test_multiple_closed_refs_in_one_entry_render_as_one_finding(tmp_path):
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(dom, "## Two tickets\ntrigger: x\nSee #1298 and #1308.\n")
    _write_issue_cache(repo, {
        "1298": {"number": 1298, "state": "CLOSED", "closed_at": "2026-08-05T00:00:00Z"},
        "1308": {"number": 1308, "state": "CLOSED", "closed_at": "2026-08-10T00:00:00Z"},
    })

    findings = notes_preflight.check_pitfall_issue_refs(dom, repo)
    assert len(findings) == 1
    assert "#1298" in findings[0].description
    assert "#1308" in findings[0].description


def test_no_refs_at_all_is_silent(tmp_path):
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    _seed(dom, "## Plain entry\ntrigger: x\nNo tickets cited here.\n")
    _write_issue_cache(repo, {})

    assert notes_preflight.check_pitfall_issue_refs(dom, repo) == []


def test_no_pitfalls_file_is_silent(tmp_path):
    repo = _repo(tmp_path)
    dom = tmp_path / "dominion"
    dom.mkdir(parents=True, exist_ok=True)

    assert notes_preflight.check_pitfall_issue_refs(dom, repo) == []


# ── cited_issue_numbers ───────────────────────────────────────────────


def test_cited_issue_numbers_unions_issue_and_ambiguous_kinds(tmp_path):
    dom = tmp_path / "dominion"
    _seed(
        dom,
        "## A\ntrigger: x\nSee #1298.\n\n"
        "## B\ntrigger: y\nSee https://github.com/o/r/issues/42 and "
        "https://github.com/o/r/pull/9.\n",
    )
    assert notes_preflight.cited_issue_numbers(dom) == {1298, 42}
