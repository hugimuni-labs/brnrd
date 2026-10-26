"""Tests for the forge-state wake-snapshot facet (co-maintainer §5, #113)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import forge_state, forges, worktree
from brr.run import Run

from _helpers import commit_files, init_git_repo


# ── parse_forge_thread ───────────────────────────────────────────────


# ── forges.thread_url ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "remote, expected",
    [
        ("git@github.com:Gurio/brr.git", "https://github.com/Gurio/brr/pull/4"),
        ("git@gitlab.com:Gurio/brr.git", "https://gitlab.com/Gurio/brr/-/merge_requests/4"),
    ],
)
def test_pull_request_url_uses_forge_native_path(remote, expected):
    assert forges.pull_request_url(remote, "Gurio/brr", 4) == expected


# ── parse_pull_request_ref (#1461) ─────────────────────────────────────


# ── worktree.unpushed_commit_count ───────────────────────────────────


def _repo_with_remote(tmp_path: Path) -> Path:
    """A repo whose ``origin`` is a GitHub URL (for forge detection) and
    whose actual push target is a local bare ``store`` remote, so
    ``unpushed_commit_count`` sees real remote-tracking refs without a
    network.
    """
    store = tmp_path / "store.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(store)],
        check=True, stdout=subprocess.PIPE,
    )
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "one\n"})
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/Gurio/brr.git"],
        cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "store", str(store)],
        cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "store", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return repo


# ── worktree.uncommitted_file_count ──────────────────────────────────


# ── build_forge_state ────────────────────────────────────────────────


def _repo_with_worktree(tmp_path: Path) -> Path:
    repo = _repo_with_remote(tmp_path)
    # A brr-managed worktree under .brr/worktrees/<run-id>.
    run_id = "run-test-1"
    wt_path, branch = worktree.create(repo, run_id)
    # Add an unpushed commit on the worktree branch.
    commit_files(wt_path, {"feature.txt": "wip\n"}, message="feature")
    Run(
        id=run_id, event_id="evt-test", body="work", status="running",
        meta={"seed_ref": "main", "has_new_commit": True},
    ).save(repo / ".brr" / "runs")
    return repo


def test_build_forge_state_lists_worktrees(tmp_path):
    repo = _repo_with_worktree(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[],
        current_thread="github:Gurio/brr:113",
        current_run_id="run-test-1",
    )
    assert facet is not None
    worktrees = facet["worktrees"]
    by_branch = {w["branch"]: w for w in worktrees}
    assert "brr/run-test-1" in by_branch
    wt = by_branch["brr/run-test-1"]
    assert wt["unpushed"] == 1
    assert wt["current"] is True
    # forge branch URL derived from origin remote
    assert wt["branch_url"] == "https://github.com/Gurio/brr/tree/brr/run-test-1"


def test_build_forge_state_hides_commitless_placeholder_branch_url(tmp_path):
    repo = _repo_with_remote(tmp_path)
    run_id = "run-no-commit"
    worktree.create(repo, run_id)
    Run(
        id=run_id, event_id="evt-noop", body="audit", status="running",
        meta={"seed_ref": "main", "has_new_commit": False},
    ).save(repo / ".brr" / "runs")

    facet = forge_state.build_forge_state(
        repo, related_threads=[], current_thread="", current_run_id=run_id,
    )

    assert facet is not None
    entry = next(w for w in facet["worktrees"] if w["run_id"] == run_id)
    assert entry["branch"] == "brr/run-no-commit"
    assert "branch_url" not in entry


# ── prompt rendering ─────────────────────────────────────────────────


# ── render_prod_line: fresh / stale / absent (2026-07-30 task) ───────


# ── #721: worktrees of this repo outside .brr/worktrees/ ─────────────


def _pushed_repo(tmp_path: Path) -> Path:
    """A repo whose one commit is reachable from a remote.

    Without this, ``unpushed_commit_count`` counts the seed commit itself —
    a repo with no remote has everything unpushed — and every worktree in the
    fixture would be "at risk" for a reason the test did not intend.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"file.txt": "init\n"})
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return repo


def _add_worktree(repo: Path, path: Path, branch: str) -> None:
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(path)],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_dirty_worktree_outside_brr_reaches_attention(tmp_path):
    """#721's whole point, driven through the real producer.

    A worktree of this repo that brnrd did not mint, holding uncommitted
    work. brnrd never created it, so brnrd never preserves it; under the
    ``host`` invariant it lives in ``/tmp``, which a reboot clears. It was
    invisible to this facet, and the facet's entire job is noticing exactly
    this.
    """
    repo = _pushed_repo(tmp_path)
    worktree.create(repo, "run-clean")

    foreign = tmp_path / "tmp-like" / "brr-wt-mood"
    _add_worktree(repo, foreign, "brr/mood")
    (foreign / "unsaved.txt").write_text("work nobody can see\n", encoding="utf-8")

    facet = forge_state.build_forge_state(repo, current_run_id="run-clean")
    summary = forge_state.summarize_worktrees(facet["worktrees"])

    assert summary["external_total"] == 1
    assert summary["external_at_risk"] == 1

    external = [wt for wt in summary["attention"] if wt.get("kind") == "external"]
    assert len(external) == 1
    assert external[0]["dirty"] is True
    # No run id to name it by, so the path is the identity — and the warning.
    assert external[0]["run_id"] is None
    assert forge_state.worktree_label(external[0]) == str(foreign)


_EXTERNAL_FACET = {
    "worktrees": [
        {"run_id": "run-a", "branch": "brr/a", "kind": "brr",
         "unpushed": 0, "dirty": False, "current": True},
        {"run_id": None, "branch": "brr/mood", "kind": "external",
         "path": "/tmp/brr-wt-mood", "unpushed": 0, "dirty": True,
         "current": False},
    ],
    "threads": [],
}


# ── the App identity on the prod line (2026-07-31, the Scaleway cutover) ──
#
# The line reported `webhook secret set · bot token set` — two credentials,
# both true, neither one the credential a managed runner pushes with. The
# publishing lane had been dead for six hours behind that sentence.


# ── resolution_reason / resolved_pr_lookup (#957) ──────────────────────
#
# The live-menu strike join reads these instead of re-deriving PR age math,
# so they get their own direct coverage; test_live_menu.py pins the actual
# join through the render callers the defect used.


# ── prod vs origin/main relationship (#1039 task 2) ──────────────────
#
# All four cases driven through build_forge_state — the caller
# render_prod_line actually has in production — on a real git repo fixture,
# never by calling the private `_origin_main_relationship` helper directly.
# A local bare "origin.git" plus a real `git fetch` gives these tests a
# genuine `refs/remotes/origin/main`, exactly as the daemon's own
# `sync.refresh_before_run` would leave one, without touching a network.


# ── deploy-lane health on the prod line (2026-08-23 task) ─────────────────
#
# "N commits behind origin/main" is one predicate true for two reasons:
# normal lag (fine) vs a broken pipeline (costs a day).  The deploy-lane
# clause that now follows it must distinguish all four cases at the renderer
# level.  Tests are driven through render_prod_line with hand-crafted prod
# fixtures — the same surface the deployed code actually calls, not the
# private _classify_deploy_lane helper.


_SENTINEL = object()


_BEHIND_RELATIONSHIP = {
    "status": "behind",
    "commit": "d36489f9" * 5,
    "origin_sha": "cafebabe" * 5,
    "count": 36,
}
_MATCHES_RELATIONSHIP = {
    "status": "matches",
    "commit": "d36489f9" * 5,
    "origin_sha": "d36489f9" * 5,
}


# ── _classify_deploy_lane: unit coverage of the classifier ────────────────
#
# Driving the renderer is the primary contract; these tests guard the
# classification logic so a mismatch between the classifier and the renderer
# is caught at its source rather than only via an integration failure.


def test_the_daemon_actually_feeds_the_deploy_lane_cache():
    """The classifier nothing fed.

    `forge_workflow_cache` shipped complete, tested against fixtures, and
    correct in all four branches — and the daemon never called its refresh.
    `read_state` would have answered `absent` on every real machine forever,
    so the one line a reader ever saw would have been "deploy lane: unknown",
    for the rest of time.

    A guard is only as live as the thing that feeds it, and the fixture that
    proves the guard says nothing about whether anything does. This asserts
    the call exists beside its sibling cache in the heartbeat body — the one
    place the whole feature depends on and the one place no unit test of the
    module itself can reach.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "brr" / "daemon.py"
    body = source.read_text(encoding="utf-8")
    assert "forge_workflow_cache.refresh_if_stale_async(repo_root)" in body, (
        "the deploy-run cache is never refreshed by the daemon — read_state "
        "will answer 'absent' forever and the lane will always render unknown"
    )
    # And it must sit beside the sibling it copies, inside the heartbeat, not
    # in some startup-only path a long-lived daemon passes once.
    pr_at = body.index("forge_pr_cache.refresh_if_stale_async(repo_root)")
    wf_at = body.index("forge_workflow_cache.refresh_if_stale_async(repo_root)")
    assert 0 < wf_at - pr_at < 1200, (
        "the deploy-run refresh drifted away from the PR-cache refresh it "
        "shares a cadence contract with"
    )


# ── The daemon's own provenance (#1896) ─────────────────────────────


