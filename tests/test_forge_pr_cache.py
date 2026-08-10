"""Tests for the local PR-state cache behind the wake's Forge block.

Two invariants carry the feature:

1. **The render path never touches the network.** Prompt assembly reads the
   cache and nothing else — the block's own name promises it, so the tests
   below poison ``subprocess.run`` in every module on the render path.
2. **absent ≠ unknown ≠ none.** No cache, and a failed refresh, must both read
   as *unknown*; only a successful refresh that found nothing may read as "no
   PRs".
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from brr import forge_pr_cache, forge_state, prompts, run_context, worktree
from brr.run import Run

from _helpers import commit_files, init_git_repo


def _repo_with_remote(tmp_path: Path) -> Path:
    """A repo with a GitHub ``origin`` and a local bare push target.

    Same shape ``test_forge_state`` uses: real remote-tracking refs, no network.
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
    subprocess.run(["git", "remote", "add", "store", str(store)], cwd=repo, check=True)
    subprocess.run(
        ["git", "push", "-u", "store", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return repo


def _repo_with_gitlab_remote(tmp_path: Path) -> Path:
    """A repo whose ``origin`` is GitLab, not GitHub — the #852 defect shape.

    Same bare-store push-target trick as :func:`_repo_with_remote`, just a
    different forge behind ``origin`` so ``gh pr list --repo <label>`` would
    silently be aimed at github.com for a repo that was never there.
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
        ["git", "remote", "add", "origin", "https://gitlab.com/group/proj.git"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "remote", "add", "store", str(store)], cwd=repo, check=True)
    subprocess.run(
        ["git", "push", "-u", "store", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return repo


def _repo_with_worktree(tmp_path: Path, run_id: str = "run-pr-1") -> tuple[Path, str]:
    repo = _repo_with_remote(tmp_path)
    wt_path, branch = worktree.create(repo, run_id)
    commit_files(wt_path, {"feature.txt": "wip\n"}, message="feature")
    Run(
        id=run_id, event_id="evt-pr", body="work", status="running",
        meta={"seed_ref": "main", "has_new_commit": True},
    ).save(repo / ".brr" / "runs")
    return repo, branch


def _write_cache(repo: Path, payload: dict) -> Path:
    path = forge_pr_cache.cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _iso(offset_seconds: float = 0.0) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_seconds)
    )


def _gh_only(handler):
    """Intercept ``gh`` calls with *handler*; let git through to the real thing.

    ``forge_pr_cache.subprocess`` *is* the stdlib module, so a blanket patch
    would also swallow the ``git remote`` reads that resolve the repo label.
    """
    real_run = subprocess.run

    def dispatch(cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        if argv[:1] == ["gh"]:
            return handler(argv, **kwargs)
        return real_run(cmd, *args, **kwargs)

    return dispatch


def _pr(number: int, branch: str, state: str = "OPEN", **extra) -> dict:
    row = {
        "number": number,
        "title": f"PR {number}",
        "state": state,
        "branch": branch,
        "base": None,
        "url": f"https://github.com/Gurio/brr/pull/{number}",
        "draft": False,
        "merged_at": None,
        "closed_at": None,
    }
    row.update(extra)
    return row


# ── cache read: absent / error / stale / fresh ───────────────────────


def test_read_state_absent_is_unknown_not_empty(tmp_path):
    repo = _repo_with_remote(tmp_path)
    state = forge_pr_cache.read_state(repo)
    assert state["status"] == "absent"
    # The load-bearing distinction: unknown is *not* an empty PR list.
    assert state["prs"] is None


def test_read_state_fresh(tmp_path):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "prs": [_pr(390, "brr/x")]})
    state = forge_pr_cache.read_state(repo)
    assert state["status"] == "fresh"
    assert state["prs"][0]["number"] == 390
    assert state["age_seconds"] < 60


def test_read_state_stale_carries_age(tmp_path):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-3600), "prs": []})
    state = forge_pr_cache.read_state(repo)
    assert state["status"] == "stale"
    assert state["prs"] == []  # a real, known "no PRs"
    assert 3500 < state["age_seconds"] < 3700


def test_read_state_error_cache_is_unknown(tmp_path):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "prs": None, "error": "gh: not logged in"})
    state = forge_pr_cache.read_state(repo)
    assert state["status"] == "error"
    assert state["prs"] is None
    assert "not logged in" in state["error"]


# ── refresh (the one network path — daemon-side only) ────────────────


def test_refresh_writes_cache(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    rows = [
        {
            "number": 382, "title": "Boot score", "state": "MERGED",
            "headRefName": "brr/boot-score-slice1",
            "baseRefName": "brr/the-child-speaks-for-itself", "isDraft": False,
            "mergedAt": "2026-07-13T18:40:00Z", "closedAt": "2026-07-13T18:40:00Z",
            "url": "https://github.com/Gurio/brr/pull/382",
        },
        {
            "number": 390, "title": "Forge PR state", "state": "OPEN",
            "headRefName": "brr/forge-pr-state", "baseRefName": "main", "isDraft": True,
            "mergedAt": None, "closedAt": None,
            "url": "https://github.com/Gurio/brr/pull/390",
        },
    ]

    def fake_gh(cmd, **kwargs):
        assert cmd[:3] == ["gh", "pr", "list"]
        assert "--repo" in cmd and "Gurio/brr" in cmd
        # #1140: the base has to actually be *asked for* or nothing
        # downstream can ever know a merge landed on a feature branch.
        assert "baseRefName" in cmd[cmd.index("--json") + 1]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_pr_cache.refresh(repo)

    assert payload["repo"] == "Gurio/brr"
    assert payload["error"] is None
    on_disk = json.loads(forge_pr_cache.cache_path(repo).read_text())
    by_number = {row["number"]: row for row in on_disk["prs"]}
    assert by_number[382]["state"] == "MERGED"
    assert by_number[382]["branch"] == "brr/boot-score-slice1"
    assert by_number[382]["base"] == "brr/the-child-speaks-for-itself"
    assert by_number[390]["draft"] is True
    assert by_number[390]["base"] == "main"
    assert on_disk["fetched_at"]


def test_refresh_omits_base_when_gh_does_not_say(tmp_path, monkeypatch):
    """No ``baseRefName`` in the row (older ``gh``?) reads as unknown, not ``""``."""
    repo = _repo_with_remote(tmp_path)
    rows = [{
        "number": 1, "title": "x", "state": "OPEN",
        "headRefName": "brr/x", "isDraft": False,
        "mergedAt": None, "closedAt": None,
        "url": "https://github.com/Gurio/brr/pull/1",
    }]

    def fake_gh(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_pr_cache.refresh(repo)
    assert payload["prs"][0]["base"] is None


def test_refresh_failure_records_error_and_keeps_last_rows(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-60), "prs": [_pr(390, "brr/x")]})

    def fake_gh(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "gh: not logged in\n")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_pr_cache.refresh(repo)

    assert payload["error"] == "gh: not logged in"
    # Last good rows survive, honestly aged — a bad refresh is not "no PRs".
    assert payload["prs"][0]["number"] == 390


def test_refresh_if_stale_skips_a_fresh_cache(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "prs": []})
    def no_gh(cmd, **kwargs):
        pytest.fail("refresh_if_stale called gh on a fresh cache")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(no_gh))
    assert forge_pr_cache.refresh_if_stale(repo) is False


def test_refresh_if_stale_refreshes_an_old_cache(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-3600), "prs": []})
    calls: list[list[str]] = []

    def fake_gh(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(fake_gh))
    assert forge_pr_cache.refresh_if_stale(repo) is True
    assert calls and calls[0][:3] == ["gh", "pr", "list"]


# ── non-GitHub forge kind gate (#852) ─────────────────────────────────
#
# ``gh pr list --repo OWNER/REPO`` only ever resolves against github.com.
# A GitLab (or Bitbucket/Gitea/unrecognised) remote must never reach that
# subprocess at all — these drive the actual caller the defect lived in,
# ``refresh(repo_root)``, exactly as the daemon's tick calls it.


def test_refresh_never_shells_out_on_a_non_github_remote(tmp_path, monkeypatch):
    repo = _repo_with_gitlab_remote(tmp_path)

    def forbidden(cmd, **kwargs):
        pytest.fail(f"refresh() shelled out to gh for a non-GitHub remote: {cmd}")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(forbidden))
    payload = forge_pr_cache.refresh(repo)

    assert payload["unsupported"] is True
    assert payload["forge_kind"] == "gitlab"
    assert payload["prs"] is None
    assert payload["error"] is None  # unsupported is a verdict, not a failure

    state = forge_pr_cache.read_state(repo)
    assert state["status"] == "unsupported"
    assert state["forge_kind"] == "gitlab"
    assert state["prs"] is None


def test_refresh_drops_stale_rows_when_the_remote_turns_out_unsupported(tmp_path, monkeypatch):
    """Unlike a failed ``gh`` call, ``unsupported`` must not carry forward
    rows from a prior good fetch — the label now names a different forge
    than whatever those rows described, so nothing about them is still true
    of this repo (module docstring carve-out, parent review of #852).
    """
    repo = _repo_with_gitlab_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-60), "prs": [_pr(390, "brr/x")]})

    def forbidden(cmd, **kwargs):
        pytest.fail("gh called for a non-GitHub remote")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(forbidden))
    payload = forge_pr_cache.refresh(repo)

    assert payload["prs"] is None  # not the #390 row carried over from before


def test_refresh_if_stale_never_shells_out_for_an_unsupported_forge(tmp_path, monkeypatch):
    """Re-deriving the kind (cheap: local git + config, no subprocess) must
    never itself reach ``gh`` — only a *changed* kind may (see the next
    test). This covers the common case: nothing changed, TTL just elapsed.
    """
    repo = _repo_with_gitlab_remote(tmp_path)

    def forbidden(cmd, **kwargs):
        pytest.fail("gh called for a repo whose forge kind is still non-github")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(forbidden))
    assert forge_pr_cache.refresh(repo)["unsupported"] is True

    # Within the TTL window: no re-derivation yet, same cadence as any other
    # status.
    assert forge_pr_cache.refresh_if_stale(repo) is False

    # A tick long after the TTL re-derives (cheaply) but the kind is still
    # gitlab, so gh is still never touched.
    far_future = time.time() + forge_pr_cache.DEFAULT_TTL_SECONDS + 1
    forge_pr_cache.refresh_if_stale(repo, now=far_future)
    assert forge_pr_cache.read_state(repo)["status"] == "unsupported"


def test_refresh_if_stale_recovers_once_the_remote_becomes_github(tmp_path, monkeypatch):
    """The two facts a forge kind is computed from are user-editable — a
    ``git remote set-url`` fix must not be trapped behind a cached verdict
    forever (parent review of the initial #852 fix).
    """
    repo = _repo_with_gitlab_remote(tmp_path)

    def forbidden(cmd, **kwargs):
        pytest.fail("gh called before the remote pointed at GitHub")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(forbidden))
    forge_pr_cache.refresh(repo)
    assert forge_pr_cache.read_state(repo)["status"] == "unsupported"
    # Within the TTL window: unchanged, no re-derivation yet.
    assert forge_pr_cache.refresh_if_stale(repo) is False

    subprocess.run(
        ["git", "remote", "set-url", "origin", "https://github.com/Gurio/brr.git"],
        cwd=repo, check=True,
    )

    def fake_gh(cmd, **kwargs):
        assert "--repo" in cmd and "Gurio/brr" in cmd
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(fake_gh))
    far_future = time.time() + forge_pr_cache.DEFAULT_TTL_SECONDS + 1
    assert forge_pr_cache.refresh_if_stale(repo, now=far_future) is True
    assert forge_pr_cache.read_state(repo)["status"] == "fresh"


def test_refresh_still_queries_github_as_before(tmp_path, monkeypatch):
    """The gate must not catch the case it is meant to leave alone."""
    repo = _repo_with_remote(tmp_path)  # github.com origin

    def fake_gh(cmd, **kwargs):
        assert "--repo" in cmd and "Gurio/brr" in cmd
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_pr_cache.refresh(repo)

    assert "unsupported" not in payload
    assert payload["prs"] == []
    assert forge_pr_cache.read_state(repo)["status"] == "fresh"


def test_render_shows_unsupported_not_stale_or_unknown():
    """The renderer must learn the new state, not reuse error/stale phrasing.

    Drives the same renderer both real callers use (:func:`prompts._format_forge_state`
    / :func:`run_context._render_forge_state`) on the shape :func:`forge_pr_cache.read_state`
    now produces for a non-GitHub remote — the doctrine at forge_state.py's
    ``pr_state_note`` (absent != unknown != none) gets a fourth member here.
    """
    facet = {
        "worktrees": [
            {
                "run_id": "run-1", "branch": "feature/x",
                "unpushed": 1, "dirty": False, "current": True,
            },
        ],
        "pr_state": {"status": "unsupported", "forge_kind": "gitlab", "standalone": []},
    }
    for render in (prompts._format_forge_state, run_context._render_forge_state):
        rendered = render(facet)
        pr_line = next(line for line in rendered.splitlines() if "PR state" in line)
        assert "unsupported" in pr_line
        assert "gitlab" in pr_line
        assert "stale" not in pr_line
        assert "unknown" not in pr_line


def test_end_to_end_gitlab_remote_never_shells_out_and_renders_unsupported(
    tmp_path, monkeypatch,
):
    """The full path the daemon tick drives: refresh -> facet -> renderer.

    Neutering the gate (dropping the ``kind != "github"`` check in
    :func:`forge_pr_cache.refresh`) turns this red on the ``pytest.fail`` in
    ``forbidden`` below — confirmed by hand while building this fix.
    """
    repo = _repo_with_gitlab_remote(tmp_path)
    wt_path, _branch = worktree.create(repo, "run-pr-1")
    commit_files(wt_path, {"feature.txt": "wip\n"}, message="feature")
    Run(
        id="run-pr-1", event_id="evt-pr", body="work", status="running",
        meta={"seed_ref": "main", "has_new_commit": True},
    ).save(repo / ".brr" / "runs")

    def forbidden(cmd, **kwargs):
        pytest.fail(f"the daemon tick's refresh shelled out to gh: {cmd}")

    monkeypatch.setattr(forge_pr_cache.subprocess, "run", _gh_only(forbidden))
    forge_pr_cache.refresh(repo)

    facet = forge_state.build_forge_state(
        repo, related_threads=[], current_thread="", current_run_id="run-pr-1",
    )
    assert facet["pr_state"]["status"] == "unsupported"
    rendered = prompts._format_forge_state(facet)
    assert "unsupported" in rendered
    assert "gitlab" in rendered


# ── facet: cache folded onto the branches ────────────────────────────


def test_build_forge_state_attaches_pr_to_branch(tmp_path):
    repo, branch = _repo_with_worktree(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "prs": [_pr(390, branch)]})

    facet = forge_state.build_forge_state(
        repo, related_threads=[], current_thread="", current_run_id="run-pr-1",
    )

    entry = next(w for w in facet["worktrees"] if w["branch"] == branch)
    assert entry["pr"]["number"] == 390
    assert facet["pr_state"]["status"] == "fresh"
    assert facet["pr_state"]["standalone"] == []  # it has a worktree; rendered inline


def test_build_forge_state_branch_without_pr_stays_bare(tmp_path):
    repo, branch = _repo_with_worktree(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "prs": [_pr(391, "brr/somewhere-else")]})

    facet = forge_state.build_forge_state(
        repo, related_threads=[], current_thread="", current_run_id="run-pr-1",
    )

    entry = next(w for w in facet["worktrees"] if w["branch"] == branch)
    assert "pr" not in entry
    # An open PR whose branch has no worktree here is still worth seeing.
    assert [pr["number"] for pr in facet["pr_state"]["standalone"]] == [391]


def test_standalone_carries_recently_merged_prs(tmp_path):
    """The live shape on this host: a merged child's worktree is already pruned.

    #382 merged 3h ago, its branch long gone from the local worktree list — and
    it is *precisely* the PR a resident is still claiming "awaits the
    maintainer". It must survive into the block anyway.
    """
    repo, _branch = _repo_with_worktree(tmp_path)
    _write_cache(repo, {
        "fetched_at": _iso(),
        "prs": [
            _pr(382, "brr/boot-score-slice1", state="MERGED", merged_at=_iso(-3 * 3600)),
            _pr(100, "brr/ancient", state="MERGED", merged_at=_iso(-30 * 86400)),
        ],
    })

    facet = forge_state.build_forge_state(
        repo, related_threads=[], current_thread="", current_run_id="run-pr-1",
    )

    numbers = [pr["number"] for pr in facet["pr_state"]["standalone"]]
    assert numbers == [382]  # the ancient merge has aged out; the fresh one has not
    rendered = prompts._format_forge_state(facet)
    assert "#382 MERGED 3h ago" in rendered
    assert "#100" not in rendered


def test_build_forge_state_missing_cache_is_unknown(tmp_path):
    repo, _branch = _repo_with_worktree(tmp_path)
    facet = forge_state.build_forge_state(
        repo, related_threads=[], current_thread="", current_run_id="run-pr-1",
    )
    assert facet["pr_state"]["status"] == "absent"
    assert facet["pr_state"]["standalone"] == []


def test_build_forge_state_does_not_shell_out(tmp_path, monkeypatch):
    """The hard constraint: prompt assembly reads the cache, never the forge."""
    repo, branch = _repo_with_worktree(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "prs": [_pr(390, branch)]})

    def forbidden(cmd, **kwargs):
        pytest.fail(f"forge state shelled out to the forge: {cmd}")

    monkeypatch.setattr(subprocess, "run", _gh_only(forbidden))

    facet = forge_state.build_forge_state(
        repo, related_threads=[], current_thread="", current_run_id="run-pr-1",
    )
    rendered = prompts._format_forge_state(facet)
    assert "#390 OPEN" in rendered


# ── attention: which PRs earn a line ─────────────────────────────────


def test_recently_merged_pr_pulls_a_clean_branch_into_attention():
    # The exact failure this feature exists for: a clean, pushed branch whose
    # PR merged hours ago, which the resident may still be claiming as open.
    wt = {
        "run_id": "run-x", "branch": "brr/boot-score-slice1",
        "unpushed": 0, "dirty": False, "current": False,
        "pr": _pr(
            382, "brr/boot-score-slice1", state="MERGED",
            merged_at=_iso(-3 * 3600),
        ),
    }
    summary = forge_state.summarize_worktrees([wt])
    assert summary["attention"] == [wt]
    assert summary["omitted"] == 0


def test_long_merged_pr_stays_collapsed():
    wt = {
        "run_id": "run-y", "branch": "brr/ancient",
        "unpushed": 0, "dirty": False, "current": False,
        "pr": _pr(100, "brr/ancient", state="MERGED", merged_at=_iso(-30 * 86400)),
    }
    summary = forge_state.summarize_worktrees([wt])
    assert summary["attention"] == []
    assert summary["omitted"] == 1


# ── rendering ────────────────────────────────────────────────────────


def _facet(pr_state: dict) -> dict:
    return {
        "worktrees": [
            {
                "run_id": "run-1", "branch": "brr/forge-pr-state",
                "unpushed": 1, "dirty": False, "current": True,
                "pr": _pr(390, "brr/forge-pr-state"),
            },
            {
                "run_id": "run-2", "branch": "brr/boot-score-slice1",
                "unpushed": 0, "dirty": False, "current": False,
                "pr": _pr(
                    382, "brr/boot-score-slice1", state="MERGED",
                    merged_at=_iso(-3600),
                ),
            },
        ],
        "pr_state": pr_state,
    }


@pytest.mark.parametrize("render", [prompts._format_forge_state, run_context._render_forge_state])
def test_render_shows_pr_state_beside_branches(render):
    rendered = render(_facet({"status": "fresh", "standalone": []}))
    assert "#390 OPEN" in rendered
    assert "#382 MERGED" in rendered
    # A fresh cache needs no age caveat.
    assert "PR state" not in rendered


@pytest.mark.parametrize("render", [prompts._format_forge_state, run_context._render_forge_state])
def test_render_labels_a_stale_cache(render):
    rendered = render(
        _facet({"status": "stale", "age_seconds": 840, "standalone": []})
    )
    assert "PR state as of 14m ago (stale)" in rendered


@pytest.mark.parametrize("render", [prompts._format_forge_state, run_context._render_forge_state])
def test_render_absent_cache_reads_unknown_not_none(render):
    rendered = render(
        {
            "worktrees": [
                {"run_id": "run-1", "branch": "brr/x", "unpushed": 1,
                 "dirty": False, "current": True},
            ],
            "pr_state": {"status": "absent", "standalone": []},
        }
    )
    assert "PR state: unknown" in rendered
    assert "no PRs" not in rendered


@pytest.mark.parametrize("render", [prompts._format_forge_state, run_context._render_forge_state])
def test_render_failed_refresh_reads_unknown(render):
    rendered = render(
        _facet({"status": "error", "error": "gh: not logged in", "standalone": []})
    )
    assert "PR state: unknown (last refresh failed: gh: not logged in)" in rendered


@pytest.mark.parametrize("render", [prompts._format_forge_state, run_context._render_forge_state])
def test_render_lists_prs_without_a_worktree(render):
    rendered = render(
        _facet({"status": "fresh", "standalone": [_pr(377, "brr/orphan")]})
    )
    assert "PRs in flight or just resolved (no local worktree):" in rendered
    assert "#377 OPEN" in rendered
    assert "brr/orphan" in rendered


@pytest.mark.parametrize("render", [prompts._format_forge_state, run_context._render_forge_state])
def test_render_names_a_stacked_base_end_to_end(render):
    """#1140: a worktree's merged PR whose base isn't the facet's own
    default branch names it in the same rendered forge block a resident
    reads at boot."""
    facet = _facet({"status": "fresh", "standalone": []})
    facet["default_branch"] = "main"
    facet["worktrees"][1]["pr"]["base"] = "brr/the-child-speaks-for-itself"
    rendered = render(facet)
    assert "#382 MERGED" in rendered
    assert "brr/the-child-speaks-for-itself" in rendered
    # The other worktree's PR (#390, base unset) carries no base override —
    # the stacked branch name appears exactly once, for #382 alone.
    assert rendered.count("brr/the-child-speaks-for-itself") == 1


def test_format_pr_marks_drafts_and_ages_resolutions():
    assert forge_state.format_pr(_pr(9, "b", draft=True)) == "#9 OPEN (draft)"
    assert forge_state.format_pr(_pr(9, "b", state="CLOSED")) == "#9 CLOSED"
    aged = _pr(9, "b", state="MERGED", merged_at=_iso(-2 * 3600))
    assert forge_state.format_pr(aged) == "#9 MERGED 2h ago"
    assert forge_state.format_pr(None) == ""


def test_format_pr_terse_case_is_unchanged_by_base_branch():
    """#1140: the common case (base == default, or base/default unknown)
    must render *exactly* as before this change — a strict addition, not a
    reformat."""
    aged = _pr(1139, "brr/x", state="MERGED", merged_at=_iso(-1800))
    # No default_branch known to the caller at all.
    assert forge_state.format_pr(aged) == "#1139 MERGED 30m ago"
    # base unknown (None, the pre-#1140 / older-cache shape).
    assert (
        forge_state.format_pr(aged, default_branch="main")
        == "#1139 MERGED 30m ago"
    )
    # base known and equal to the default branch.
    on_main = _pr(1139, "brr/x", state="MERGED", merged_at=_iso(-1800), base="main")
    assert (
        forge_state.format_pr(on_main, default_branch="main")
        == "#1139 MERGED 30m ago"
    )


def test_format_pr_names_a_stacked_base():
    """#1140's own candidate spelling: only rendered when base != default."""
    stacked = _pr(
        1139, "brr/x", state="MERGED", merged_at=_iso(-1800),
        base="brr/the-child-speaks-for-itself",
    )
    assert (
        forge_state.format_pr(stacked, default_branch="main")
        == "#1139 MERGED 30m ago → brr/the-child-speaks-for-itself"
    )
    # Known base, but the caller never supplied a default branch to compare
    # against — nothing to say, so nothing is said.
    assert forge_state.format_pr(stacked) == "#1139 MERGED 30m ago"
    # Still applies to open PRs, not just merged ones.
    stacked_open = _pr(9, "b", base="brr/parent")
    assert (
        forge_state.format_pr(stacked_open, default_branch="main")
        == "#9 OPEN → brr/parent"
    )


@pytest.mark.parametrize(
    "seconds, expected",
    [(None, "unknown age"), (30, "30s"), (840, "14m"), (10800, "3h"), (200000, "2d")],
)
def test_format_age(seconds, expected):
    assert forge_state.format_age(seconds) == expected


@pytest.mark.parametrize("render", [prompts._format_forge_state, run_context._render_forge_state])
def test_render_caps_the_resolved_tail(render):
    """A busy day merges a dozen PRs; the block rides in *every* wake."""
    resolved = [
        _pr(300 + i, f"brr/x{i}", state="MERGED", merged_at=_iso(-(i + 1) * 600))
        for i in range(14)
    ]
    rendered = render(
        _facet({
            "status": "fresh",
            "standalone": [_pr(400, "brr/open")] + resolved,
        })
    )
    assert "#400 OPEN" in rendered           # the queue always renders in full
    assert "#300 MERGED" in rendered         # newest resolution (10m ago)
    assert "#313 MERGED" not in rendered     # oldest (140m ago), past the cap
    assert "4 older resolutions in the last 24h omitted" in rendered


class TestFailedRefreshCannotLookFresh:
    """A refresh that failed must never render as a current reading.

    Parent review of PR #384.  The module's own docstring states the rule
    (``absent != unknown != none``) and the first implementation broke it one
    function down: a failed ``gh`` call preserved the previous rows *and*
    stamped ``fetched_at`` with the attempt time, so hour-old PR state came
    back as ``status="fresh"``, age 0s.  An offline or logged-out ``gh`` would
    have silently frozen the Forge block at whatever it last saw — the exact
    failure-indistinguishable-from-success this cache exists to end.
    """

    def _seed(self, root, fetched_at="2026-07-13T19:00:00Z"):
        from brr import forge_pr_cache as c

        (root / ".brr").mkdir(parents=True, exist_ok=True)
        c.cache_path(root).write_text(json.dumps({
            "schema": 1,
            "fetched_at": fetched_at,
            "repo": "Gurio/brr",
            "prs": [{
                "number": 373, "title": "t", "state": "OPEN", "branch": "brr/a",
                "url": "", "draft": False, "merged_at": None, "closed_at": None,
            }],
            "error": None,
        }), encoding="utf-8")

    def test_failed_refresh_over_good_cache_reports_error_not_fresh(self, tmp_path):
        from brr import forge_pr_cache as c

        self._seed(tmp_path)
        with mock.patch.object(c.subprocess, "run", side_effect=OSError("gh: not found")):
            c.refresh(tmp_path)

        state = c.read_state(tmp_path)
        assert state["status"] == "error", "a failed refresh must never read as fresh"
        assert state["error"]
        assert state["prs"], "the last good rows are still worth showing"

    def test_fetched_at_describes_the_data_not_the_attempt(self, tmp_path):
        """The kept rows keep their true age; the failed attempt gets its own field."""
        from brr import forge_pr_cache as c

        self._seed(tmp_path, fetched_at="2026-07-13T19:00:00Z")
        with mock.patch.object(c.subprocess, "run", side_effect=OSError("boom")):
            payload = c.refresh(tmp_path)

        assert payload["fetched_at"] == "2026-07-13T19:00:00Z", (
            "carrying rows forward under a new timestamp is how stale data "
            "passes for current"
        )
        assert payload["last_attempt_at"] != payload["fetched_at"]

        state = c.read_state(tmp_path, now=c.parse_iso("2026-07-13T20:00:00Z"))
        assert state["age_seconds"] == pytest.approx(3600, abs=5)

    def test_note_names_both_the_failure_and_the_age_of_what_it_shows(self, tmp_path):
        from brr import forge_state

        note = forge_state.pr_state_note({
            "status": "error",
            "error": "gh: not found",
            "age_seconds": 4400,
            "has_rows": True,
        })
        assert "FAILED" in note
        assert "gh: not found" in note
        assert "73m" in note, "a reader cannot judge a row without its age"

    def test_error_with_no_rows_still_says_unknown(self, tmp_path):
        from brr import forge_state

        note = forge_state.pr_state_note({
            "status": "error", "error": "gh: not found", "has_rows": False,
        })
        assert "unknown" in note
