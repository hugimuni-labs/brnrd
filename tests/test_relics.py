"""Tests for :mod:`brr.relics` — run relics collection (#200/#317)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from brr import relics

from _helpers import commit_files, init_git_repo


# ── append / read_reported ───────────────────────────────────────────


def test_append_and_read_round_trip(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    relics.append(outbox, "summary", text="Closed #200 and #317.")
    relics.append(outbox, "issue", number=317, action="closed", url="https://x/317")
    got = relics.read_reported(outbox)
    assert got == [
        {"kind": "summary", "text": "Closed #200 and #317."},
        {"kind": "issue", "number": 317, "action": "closed", "url": "https://x/317"},
    ]


def test_read_reported_missing_file_is_empty(tmp_path: Path):
    assert relics.read_reported(tmp_path / "no-outbox") == []


def test_read_reported_none_outbox_is_empty():
    assert relics.read_reported(None) == []


def test_read_reported_skips_malformed_lines(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / relics.CONTROL_NAME).write_text(
        "not json\n"
        '{"kind": "issue", "number": 1}\n'
        "\n"
        '{"missing_kind": true}\n'
        '{"kind": "kb", "path": "kb/x.md"}\n',
        encoding="utf-8",
    )
    got = relics.read_reported(outbox)
    assert got == [
        {"kind": "issue", "number": 1},
        {"kind": "kb", "path": "kb/x.md"},
    ]


def test_append_drops_oversized_record(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    relics.append(outbox, "comment", note="x" * (relics._MAX_LINE_BYTES + 100))
    assert relics.read_reported(outbox) == []


def test_read_reported_caps_at_max_records(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    lines = "\n".join(
        f'{{"kind": "comment", "n": {i}}}' for i in range(relics._MAX_RECORDS + 20)
    )
    (outbox / relics.CONTROL_NAME).write_text(lines + "\n", encoding="utf-8")
    assert len(relics.read_reported(outbox)) == relics._MAX_RECORDS


def test_read_reported_aliases_kb_page_kind(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    relics.append(outbox, "kb_page", path="design-loom-viewport.md")

    assert relics.read_reported(outbox) == [
        {"kind": "kb", "path": "design-loom-viewport.md"},
    ]


def test_read_pr_control_accepts_only_explicit_pr_forms(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    path = outbox / ".pr"
    for text in (
        "274", "#274", "https://github.com/Gurio/brr/pull/274",
        "https://codeberg.org/Gurio/brr/pulls/274",
    ):
        path.write_text(text, encoding="utf-8")
        assert relics._read_pr_control(outbox) == "274"
    for text in ("ea35206", "prefix 274", "not-a-url/pull/274", "https://x/pulls/274"):
        path.write_text(text, encoding="utf-8")
        assert relics._read_pr_control(outbox) is None


# ── derive_auto ──────────────────────────────────────────────────────


def test_derive_auto_lists_commits_branch_and_pr(tmp_path: Path):
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


def test_derive_auto_without_branch_or_pr_is_empty(tmp_path: Path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    assert relics.derive_auto(repo, branch=None, seed_ref=None, outbox_dir=None) == []


def test_derive_auto_hides_branch_when_it_has_no_commit_beyond_seed(tmp_path: Path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "brr/noop"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
        cwd=repo, check=True,
    )

    assert relics.derive_auto(
        repo, branch="brr/noop", seed_ref="main", outbox_dir=None,
    ) == []


def test_derive_auto_none_repo_root_is_empty():
    assert relics.derive_auto(None, branch="x", seed_ref=None, outbox_dir=None) == []


def test_derive_auto_no_remote_still_lists_commits_without_urls(tmp_path: Path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "brr/work"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="add b")
    out = relics.derive_auto(repo, branch="brr/work", seed_ref="main", outbox_dir=None)
    assert out == [
        {"kind": "commit", "sha": out[0]["sha"], "subject": "add b", "url": None},
        {"kind": "branch", "name": "brr/work", "url": None},
    ]


# ── live_summary / collect / counts_by_kind ──────────────────────────


def test_live_summary_compiles_auto_and_reported_produce(tmp_path: Path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "checkout", "-b", "brr/work"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="add b")
    commit_files(repo, {"c.txt": "3"}, message="add c")
    latest = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".pr").write_text("451\n", encoding="utf-8")
    relics.append(outbox, "issue", number=317, action="closed")
    relics.append(outbox, "kb", path="design-run-relics.md")
    relics.append(outbox, "summary", text="not a counted relic")

    summary = relics.live_summary(
        repo, branch="brr/work", seed_ref="main", outbox_dir=outbox,
    )
    records = summary.pop("records")
    assert summary == {
        "known": True,
        "counts": {
            "commit": 2, "branch": 1, "pr": 1, "issue": 1, "kb": 1,
        },
        "latest_commit": latest,
        "branch": "brr/work",
        "pr": 451,
    }
    # The manifest itself rides alongside the counts: the node's frame renders
    # it, and the resident's closeout briefing reads it to write a receipt from
    # what the run actually made rather than from memory.
    assert {record["kind"] for record in records} == {
        "commit", "branch", "pr", "issue", "kb", "summary",
    }
    assert any(record.get("sha") == latest for record in records)


def test_live_summary_never_raises(tmp_path: Path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(relics, "derive_auto", broken)
    assert relics.live_summary(
        tmp_path, branch="brr/work", seed_ref="main", outbox_dir=None,
    ) == {"known": False}


# ── collect / counts_by_kind ─────────────────────────────────────────


def test_collect_orders_summary_first_then_auto_then_reported(tmp_path: Path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "brr/work"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="add b")

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    relics.append(outbox, "issue", number=200, action="commented")
    relics.append(outbox, "summary", text="Did the thing.")
    relics.append(outbox, "kb", path="kb/design-run-relics.md")

    out = relics.collect(repo, branch="brr/work", seed_ref="main", outbox_dir=outbox)
    assert out[0] == {"kind": "summary", "text": "Did the thing."}
    kinds = [r["kind"] for r in out]
    assert kinds == ["summary", "commit", "branch", "issue", "kb"]


def test_collect_at_most_one_summary(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    relics.append(outbox, "summary", text="first")
    relics.append(outbox, "summary", text="second")
    out = relics.collect(None, branch=None, seed_ref=None, outbox_dir=outbox)
    assert out == [{"kind": "summary", "text": "first"}]


def test_collect_drops_numberless_pr_relic(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    relics.append(outbox, "pr")
    relics.append(outbox, "pr", number=42)

    assert relics.collect(
        None, branch=None, seed_ref=None, outbox_dir=outbox,
    ) == [{"kind": "pr", "number": 42}]


def test_collect_adds_resolved_url_to_kb_relic(tmp_path: Path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    relics.append(outbox, "kb", path="design-managed-delivery.md")
    expected = "https://example.test/blob/main/design-managed-delivery.md"
    monkeypatch.setattr(relics.knowledge, "kb_page_url", lambda *_: expected)

    out = relics.collect(tmp_path, branch=None, seed_ref=None, outbox_dir=outbox)

    assert out == [{
        "kind": "kb",
        "path": "design-managed-delivery.md",
        "url": expected,
    }]


def test_collect_drops_unverified_reported_kb_url(tmp_path: Path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    relics.append(
        outbox, "kb", path="new.md",
        url="https://example.test/blob/main/new.md",
    )
    monkeypatch.setattr(relics.knowledge, "kb_page_url", lambda *_: None)

    out = relics.collect(tmp_path, branch=None, seed_ref=None, outbox_dir=outbox)

    assert out == [{"kind": "kb", "path": "new.md"}]


# ── dedupe ───────────────────────────────────────────────────────────


def test_collect_dedupes_auto_and_reported_pr(tmp_path: Path):
    """Regression: run-260721-0922-pfqd rendered PR #532 twice — the auto
    row (from ``.pr``, with URL) and the resident-reported row (no URL)."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
        cwd=repo, check=True,
    )

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".pr").write_text("532\n", encoding="utf-8")
    relics.append(outbox, "pr", number=532, action="opened")

    out = relics.collect(repo, branch=None, seed_ref=None, outbox_dir=outbox)
    prs = [r for r in out if r["kind"] == "pr"]
    assert prs == [{
        "kind": "pr", "number": 532, "action": "opened",
        "url": "https://github.com/Gurio/brr/pull/532",
    }]


def test_dedupe_prefers_url_bearing_row_and_merges_fields():
    out = relics.dedupe([
        {"kind": "pr", "number": 532, "action": "opened"},
        {"kind": "pr", "number": 532, "url": "https://x/pull/532"},
    ])
    assert out == [{
        "kind": "pr", "number": 532, "action": "opened",
        "url": "https://x/pull/532",
    }]
    # First occurrence keeps its position even when the later row wins fields.
    out = relics.dedupe([
        {"kind": "issue", "number": 7},
        {"kind": "commit", "sha": "abc1234"},
        {"kind": "issue", "number": 7, "url": "https://x/issues/7"},
    ])
    assert [r["kind"] for r in out] == ["issue", "commit"]
    assert out[0]["url"] == "https://x/issues/7"


def test_dedupe_keeps_distinct_identities_apart():
    records = [
        {"kind": "pr", "number": 1},
        {"kind": "pr", "number": 2},
        {"kind": "issue", "number": 1},
        {"kind": "kb", "path": "a.md"},
        {"kind": "kb", "path": "b.md"},
        {"kind": "file", "path": "a.md"},
    ]
    assert relics.dedupe(records) == records


def test_dedupe_never_merges_identityless_kinds():
    records = [
        {"kind": "comment", "on": "issue #5"},
        {"kind": "comment", "on": "issue #5"},
        {"kind": "summary", "text": "x"},
        {"kind": "summary", "text": "x"},
        {"kind": "pr"},  # numberless: no identity, passes through
        {"kind": "pr"},
    ]
    assert relics.dedupe(records) == records


def test_dedupe_matches_short_and_full_commit_sha():
    out = relics.dedupe([
        {"kind": "commit", "sha": "abc1234", "subject": "s", "url": "https://x/c"},
        {"kind": "commit", "sha": "abc1234def5678900000"},
    ])
    assert out == [{
        "kind": "commit", "sha": "abc1234", "subject": "s", "url": "https://x/c",
    }]


def test_live_summary_counts_deduped_pr_once(tmp_path: Path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
        cwd=repo, check=True,
    )

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".pr").write_text("532\n", encoding="utf-8")
    relics.append(outbox, "pr", number=532, action="opened")

    summary = relics.live_summary(
        repo, branch=None, seed_ref=None, outbox_dir=outbox,
    )
    assert summary["counts"] == {"pr": 1}
    assert summary["pr"] == 532
    prs = [r for r in summary["records"] if r["kind"] == "pr"]
    assert len(prs) == 1 and prs[0]["url"]


def test_counts_by_kind_excludes_summary():
    relic_list = [
        {"kind": "summary", "text": "x"},
        {"kind": "commit", "sha": "a"},
        {"kind": "commit", "sha": "b"},
        {"kind": "pr", "number": 1},
        {"kind": "issue", "number": 2},
    ]
    assert relics.counts_by_kind(relic_list) == {"commit": 2, "pr": 1, "issue": 1}


def test_icon_known_and_unknown_kind():
    assert relics.icon("commit") == "🔨"
    assert relics.icon("something-new") == "•"


def test_collection_scope_passes_through_worktree_pins(tmp_path: Path):
    branch, seed = relics.collection_scope(
        {"branch_name": "brr/work", "seed_ref": "main"}, tmp_path,
    )
    assert (branch, seed) == ("brr/work", "main")


def test_collection_scope_host_run_measures_from_start_head(tmp_path: Path):
    """A host run has no assigned branch; the scope is the checkout's current
    branch against the run-start HEAD OID, so work merged back into the seed
    branch still books as this run's commits."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    start_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    # The usual host flow: branch, commit, merge back, end on main.
    subprocess.run(["git", "checkout", "-b", "brr/feature"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="the work")
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True)
    subprocess.run(["git", "merge", "--no-ff", "brr/feature", "-m", "merge"],
                   cwd=repo, check=True)

    branch, seed = relics.collection_scope(
        {"seed_ref": "main", "host_start_oid": start_oid}, repo,
    )
    assert branch == "main"
    assert seed == start_oid

    out = relics.derive_auto(repo, branch=branch, seed_ref=seed, outbox_dir=None)
    subjects = [r.get("subject") for r in out if r["kind"] == "commit"]
    # Both the work and the merge are commits that appeared during the run;
    # the pre-run seed commit is not.
    assert "the work" in subjects
    assert "seed" not in subjects


def test_collection_scope_detached_head_yields_no_branch(tmp_path: Path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", oid], cwd=repo, check=True,
                   capture_output=True)
    branch, seed = relics.collection_scope({"seed_ref": "main"}, repo)
    assert branch is None
    assert seed == "main"


def test_collection_scope_without_work_dir_is_meta_only():
    assert relics.collection_scope({}, None) == (None, None)


# ── merge relics: merges performed are their own block (2026-07-21) ──


def _merge_repo(tmp_path: Path) -> Path:
    """A repo on main with one feature branch merged via merge commit."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
                    cwd=repo, check=True)
    return repo


def test_derive_auto_promotes_pr_merge_commit_to_merge_relic(tmp_path: Path):
    repo = _merge_repo(tmp_path)
    start = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "checkout", "-b", "brr/feature"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="add b")
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "merge", "--no-ff", "brr/feature",
         "-m", "Merge pull request #532 from Gurio/brr/feature"],
        cwd=repo, check=True,
    )

    out = relics.derive_auto(repo, branch="main", seed_ref=start, outbox_dir=None)
    kinds = [r["kind"] for r in out]
    assert kinds == ["merge", "commit", "branch"]
    merge = out[0]
    assert merge["pr"] == 532
    assert merge["url"] == "https://github.com/Gurio/brr/pull/532"
    assert merge["subject"].startswith("Merge pull request #532")
    # The underlying feature commit still rides as an ordinary commit.
    assert out[1]["subject"] == "add b"


def test_derive_auto_promotes_branch_merge_commit_to_merge_relic(tmp_path: Path):
    repo = _merge_repo(tmp_path)
    start = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "checkout", "-b", "brr/home-as-root"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="add b")
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True)
    subprocess.run(["git", "merge", "--no-ff", "brr/home-as-root"], cwd=repo, check=True)

    out = relics.derive_auto(repo, branch="main", seed_ref=start, outbox_dir=None)
    merge = out[0]
    assert merge["kind"] == "merge"
    assert merge["branch"] == "brr/home-as-root"
    assert "pr" not in merge
    assert merge["url"].startswith("https://github.com/Gurio/brr/commit/")
    assert relics.label(merge) == "merged brr/home-as-root"


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


def test_merge_relic_never_collapses_into_pr_relic(tmp_path: Path):
    # A run that created PR #532 (.pr) and merged it locally shows both
    # blocks: the PR made and the merge performed. Separate, per the
    # maintainer's 2026-07-21 steer.
    repo = _merge_repo(tmp_path)
    start = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "checkout", "-b", "brr/feature"], cwd=repo, check=True)
    commit_files(repo, {"b.txt": "2"}, message="add b")
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "merge", "--no-ff", "brr/feature",
         "-m", "Merge pull request #532 from Gurio/brr/feature"],
        cwd=repo, check=True,
    )
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".pr").write_text("532\n", encoding="utf-8")

    out = relics.collect(repo, branch="main", seed_ref=start, outbox_dir=outbox)
    kinds = [r["kind"] for r in out]
    assert kinds.count("merge") == 1
    assert kinds.count("pr") == 1
    counts = relics.counts_by_kind(out)
    assert counts["merge"] == 1
    assert counts["pr"] == 1


def test_merge_label_and_icon():
    assert relics.label({"kind": "merge", "pr": 532}) == "merged PR #532"
    assert relics.label({"kind": "merge", "branch": "brr/x"}) == "merged brr/x"
    assert relics.label({"kind": "merge", "sha": "abc1234def",
                         "subject": ""}) == "merge abc1234"
    assert relics.icon("merge") == "⤵️"
