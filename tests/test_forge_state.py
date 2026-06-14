"""Tests for the forge-state wake-snapshot facet (co-maintainer §5, #113)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import forge_state, forges, prompts, worktree

from _helpers import commit_files, init_git_repo


# ── parse_forge_thread ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "key, expected",
    [
        ("github:Gurio/brr:113", ("Gurio/brr", 113)),
        ("cloud:github:Gurio/brr#113:", ("Gurio/brr", 113)),
        ("cloud:github:Gurio/brr#42:topic-7", ("Gurio/brr", 42)),
        # nested-group repo path round-trips
        ("github:group/sub/repo:9", ("group/sub/repo", 9)),
        # non-forge keys yield None
        ("telegram:12345:", None),
        ("slack:C01:1700000000.1", None),
        ("cloud:telegram:999:", None),
        ("github:Gurio/brr", None),       # no number
        ("github:Gurio/brr:abc", None),   # non-numeric
        ("github:noslash:5", None),        # repo missing owner/repo shape
        ("", None),
    ],
)
def test_parse_forge_thread(key, expected):
    assert forge_state.parse_forge_thread(key) == expected


# ── forges.thread_url ────────────────────────────────────────────────


def test_thread_url_github():
    url = forges.thread_url("git@github.com:Gurio/brr.git", "Gurio/brr", 113)
    assert url == "https://github.com/Gurio/brr/issues/113"


def test_thread_url_uses_thread_repo_not_origin():
    # The repo a thread is about may differ from origin; the URL follows
    # the thread's repo while taking host/kind from the remote.
    url = forges.thread_url("git@github.com:Gurio/brr.git", "other/proj", 7)
    assert url == "https://github.com/other/proj/issues/7"


def test_thread_url_gitlab_template():
    url = forges.thread_url("git@gitlab.com:grp/proj.git", "grp/proj", 4)
    assert url == "https://gitlab.com/grp/proj/-/issues/4"


@pytest.mark.parametrize(
    "remote, repo, number",
    [
        ("git@github.com:Gurio/brr.git", "Gurio/brr", "nope"),  # bad number
        ("git@github.com:Gurio/brr.git", "noslash", 5),          # bad repo
        ("not a remote", "Gurio/brr", 5),                        # bad remote
        ("git@github.com:Gurio/brr.git", "Gurio/brr", 0),        # non-positive
    ],
)
def test_thread_url_none_cases(remote, repo, number):
    assert forges.thread_url(remote, repo, number) is None


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


def test_unpushed_commit_count_zero_after_push(tmp_path):
    repo = _repo_with_remote(tmp_path)
    assert worktree.unpushed_commit_count(repo) == 0


def test_unpushed_commit_count_counts_local_commits(tmp_path):
    repo = _repo_with_remote(tmp_path)
    commit_files(repo, {"b.txt": "two\n"}, message="local")
    commit_files(repo, {"c.txt": "three\n"}, message="local2")
    assert worktree.unpushed_commit_count(repo) == 2


def test_unpushed_commit_count_no_remote(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "x\n"})
    # No remote at all → every commit is "unpushed".
    assert worktree.unpushed_commit_count(repo) == 1


# ── build_forge_state ────────────────────────────────────────────────


def _repo_with_worktree(tmp_path: Path) -> Path:
    repo = _repo_with_remote(tmp_path)
    # A brr-managed worktree under .brr/worktrees/<task-id>.
    task_id = "task-test-1"
    wt_path, branch = worktree.create(repo, task_id)
    # Add an unpushed commit on the worktree branch.
    commit_files(wt_path, {"feature.txt": "wip\n"}, message="feature")
    return repo


def test_build_forge_state_lists_worktrees(tmp_path):
    repo = _repo_with_worktree(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[],
        current_thread="github:Gurio/brr:113",
        current_task_id="task-test-1",
    )
    assert facet is not None
    worktrees = facet["worktrees"]
    by_branch = {w["branch"]: w for w in worktrees}
    assert "brr/task-test-1" in by_branch
    wt = by_branch["brr/task-test-1"]
    assert wt["unpushed"] == 1
    assert wt["current"] is True
    # forge branch URL derived from origin remote
    assert wt["branch_url"] == "https://github.com/Gurio/brr/tree/brr/task-test-1"


def test_build_forge_state_threads_cross_reference(tmp_path):
    repo = _repo_with_remote(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[{"conversation_key": "github:Gurio/brr:99"}],
        current_thread="github:Gurio/brr:113",
        current_task_id="",
    )
    assert facet is not None
    threads = facet["threads"]
    refs = {(t["repo"], t["number"]): t for t in threads}
    assert ("Gurio/brr", 113) in refs
    assert ("Gurio/brr", 99) in refs
    assert refs[("Gurio/brr", 113)]["current"] is True
    assert refs[("Gurio/brr", 99)]["current"] is False
    assert refs[("Gurio/brr", 113)]["url"] == "https://github.com/Gurio/brr/issues/113"


def test_build_forge_state_enriches_current_from_event_meta(tmp_path):
    repo = _repo_with_remote(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[],
        current_thread="github:Gurio/brr:113",
        current_task_id="",
        current_event_meta={
            "github_kind": "pull_request",
            "branch_target": "brr/feature-x",
            "github_pr_number": "113",
            "github_html_url": "https://github.com/Gurio/brr/pull/113#issuecomment-5",
        },
    )
    thread = facet["threads"][0]
    assert thread["kind"] == "pull_request"
    assert thread["branch_target"] == "brr/feature-x"
    assert thread["pr_number"] == "113"
    # exact comment URL wins over the template-derived issue URL
    assert thread["url"] == "https://github.com/Gurio/brr/pull/113#issuecomment-5"


def test_build_forge_state_none_when_empty(tmp_path):
    # A repo with no brr worktrees and a non-forge current thread yields
    # nothing to show.
    repo = _repo_with_remote(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[{"conversation_key": "telegram:123:"}],
        current_thread="telegram:123:",
        current_task_id="",
    )
    assert facet is None


# ── prompt rendering ─────────────────────────────────────────────────


def test_format_forge_state_renders_sections():
    facet = {
        "worktrees": [
            {
                "task_id": "task-1",
                "branch": "brr/feature",
                "unpushed": 2,
                "dirty": True,
                "current": True,
                "branch_url": "https://github.com/Gurio/brr/tree/brr/feature",
            }
        ],
        "threads": [
            {
                "conversation_key": "github:Gurio/brr:113",
                "repo": "Gurio/brr",
                "number": 113,
                "current": True,
                "kind": "issue",
                "url": "https://github.com/Gurio/brr/issues/113",
            }
        ],
    }
    rendered = prompts._format_forge_state(facet)
    assert "Forge state" in rendered
    assert "brr/feature" in rendered
    assert "2 unpushed" in rendered
    assert "uncommitted changes" in rendered
    assert "Gurio/brr#113" in rendered
    assert "this thread" in rendered


def test_format_forge_state_empty():
    assert prompts._format_forge_state(None) == ""
    assert prompts._format_forge_state({}) == ""
    assert prompts._format_forge_state({"worktrees": [], "threads": []}) == ""
