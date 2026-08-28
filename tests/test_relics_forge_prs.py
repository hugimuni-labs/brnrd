"""Produce that observes itself rather than being remembered.

`.pr` holds *one* number, because one run opening one PR was the shape when
it was written. A run that opens several had to call `brnrd relic pr <n>` for
each — so a produce manifest depended on the resident remembering, and was
therefore wrong on exactly the runs where remembering is hardest, with
nothing on any surface saying which kind of run you were looking at.

Measured 2026-08-28: a run opened fourteen PRs and its manifest was complete
only because the verb was typed thirteen times.

The join under test: **a PR is this run's when its head branch's tip commit
is one of the commits this run made.**
"""

import json
import subprocess

import pytest

from brr import relics


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    return root


def _branch_with_commit(root, name, text):
    _git(root, "switch", "-q", "-c", name, "main")
    (root / f"{name.replace('/', '_')}.txt").write_text(text)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", f"work on {name}")
    return _git(root, "rev-parse", "--short", "HEAD").stdout.strip()


def _forge_state(brr_dir, rows):
    brr_dir.mkdir(parents=True, exist_ok=True)
    (brr_dir / "forge-pr-state.json").write_text(json.dumps({"error": None, "prs": rows}))


def test_every_pr_off_this_runs_branches_is_derived(repo, tmp_path):
    brr = tmp_path / ".brr"
    outbox = brr / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    mine = _branch_with_commit(repo, "brr/one", "one")
    other = _branch_with_commit(repo, "brr/two", "two")
    _forge_state(
        brr,
        [
            {"number": 11, "branch": "brr/one", "url": "https://x/pull/11"},
            {"number": 12, "branch": "brr/two", "url": "https://x/pull/12"},
        ],
    )
    got = relics._prs_from_forge_state(outbox, {mine, other}, lambda n: None, repo)
    assert [r["number"] for r in got] == [11, 12]
    assert got[0]["url"] == "https://x/pull/11"


def test_a_pr_off_a_branch_this_run_never_wrote_is_not_its_produce(repo, tmp_path):
    brr = tmp_path / ".brr"
    outbox = brr / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    mine = _branch_with_commit(repo, "brr/mine", "mine")
    _branch_with_commit(repo, "brr/theirs", "theirs")
    _forge_state(
        brr,
        [
            {"number": 11, "branch": "brr/mine"},
            {"number": 99, "branch": "brr/theirs"},
        ],
    )
    got = relics._prs_from_forge_state(outbox, {mine}, lambda n: None, repo)
    assert [r["number"] for r in got] == [11], "the join cannot over-claim"


def test_a_branch_the_checkout_does_not_have_is_skipped(repo, tmp_path):
    brr = tmp_path / ".brr"
    outbox = brr / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    mine = _branch_with_commit(repo, "brr/mine", "mine")
    _forge_state(brr, [{"number": 7, "branch": "brr/never-here"}])
    assert relics._prs_from_forge_state(outbox, {mine}, lambda n: None, repo) == []


def test_no_poll_on_disk_says_nothing_rather_than_no_prs(repo, tmp_path):
    # An absent forge poll is not evidence of zero PRs, and must not be
    # rendered as one — a surface that narrows renders as if it hadn't.
    outbox = tmp_path / ".brr" / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    assert relics._prs_from_forge_state(outbox, {"abc1234"}, lambda n: None, repo) == []


def test_no_commits_this_run_derives_no_prs(repo, tmp_path):
    # Nothing to join against. A run that wrote no commits cannot have
    # opened a PR off its own work, and guessing from branch names would be
    # exactly the shape-matching this join replaces.
    brr = tmp_path / ".brr"
    outbox = brr / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    _branch_with_commit(repo, "brr/mine", "mine")
    _forge_state(brr, [{"number": 11, "branch": "brr/mine"}])
    assert relics._prs_from_forge_state(outbox, set(), lambda n: None, repo) == []


def test_a_torn_or_hostile_poll_file_is_survivable(repo, tmp_path):
    brr = tmp_path / ".brr"
    outbox = brr / "outbox" / "evt-1"
    outbox.mkdir(parents=True)
    brr.mkdir(parents=True, exist_ok=True)
    (brr / "forge-pr-state.json").write_text("{not json")
    assert relics._prs_from_forge_state(outbox, {"abc1234"}, lambda n: None, repo) == []
    (brr / "forge-pr-state.json").write_text(json.dumps({"prs": "nope"}))
    assert relics._prs_from_forge_state(outbox, {"abc1234"}, lambda n: None, repo) == []
    (brr / "forge-pr-state.json").write_text(
        json.dumps({"prs": [{"number": "eleven", "branch": "brr/x"}, {"branch": ""}, 5]})
    )
    assert relics._prs_from_forge_state(outbox, {"abc1234"}, lambda n: None, repo) == []


def test_the_derived_row_dedupes_against_a_hand_reported_one(repo, tmp_path):
    # `brnrd relic pr` stays as the escape hatch. Reporting a PR the forge
    # also sees must not render two rows — the failure `dedupe` was written
    # for (run-260721-0922-pfqd).
    merged = relics.dedupe(
        [
            {"kind": "pr", "number": 11, "url": "https://x/pull/11"},
            {"kind": "pr", "number": 11, "action": "opened"},
        ]
    )
    assert len(merged) == 1
    assert merged[0]["url"] == "https://x/pull/11"
    assert merged[0]["action"] == "opened", "a resident annotation survives the merge"
