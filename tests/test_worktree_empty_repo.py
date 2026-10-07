"""A repo with no commits gets an explanation, not git's raw error (#NNN).

``git clone`` of an empty GitHub repo leaves a checkout that says
``On branch main`` while git has no ``main`` reference at all. The first
run seeded from it used to surface ``fatal: invalid reference: main``,
which reads as a contradiction to anyone new to git.
"""

from __future__ import annotations

import pytest

from brr import worktree

from _helpers import commit_files, init_git_repo


def test_create_repo_with_no_commits(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)  # `git init -b main`, no commit

    with pytest.raises(RuntimeError) as excinfo:
        worktree.create(repo, "run-1", base_ref="main")

    message = str(excinfo.value)
    assert "no commits yet" in message
    assert "invalid reference" not in message


def test_create_repo_has_commits(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"README.md": "hi\n"})

    with pytest.raises(RuntimeError) as excinfo:
        worktree.create(repo, "run-2", base_ref="no-such-branch")

    message = str(excinfo.value)
    assert "no commits yet" not in message