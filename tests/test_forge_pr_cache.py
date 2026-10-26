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

import pytest

from brr import forge_pr_cache, forge_state, prompts, worktree
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


# ── refresh (the one network path — daemon-side only) ────────────────


# ── non-GitHub forge kind gate (#852) ─────────────────────────────────
#
# ``gh pr list --repo OWNER/REPO`` only ever resolves against github.com.
# A GitLab (or Bitbucket/Gitea/unrecognised) remote must never reach that
# subprocess at all — these drive the actual caller the defect lived in,
# ``refresh(repo_root)``, exactly as the daemon's tick calls it.


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


# ── rendering ────────────────────────────────────────────────────────


