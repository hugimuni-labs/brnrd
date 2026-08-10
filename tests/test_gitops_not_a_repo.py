"""A non-git folder gets a plain sentence, never a traceback.

The incident: a fresh user ran ``brnrd account connect`` from a directory
that was not inside any git checkout. ``ensure_git_repo`` raised a bare
``RuntimeError`` that ``cli.main`` did not catch (it caught only the
sibling :class:`~brr.gitops.RepoTreeUnusable`), so the product's first
impression was a full ``subprocess`` / ``RuntimeError`` traceback.

The fix is the #1108 pattern extended to its sibling: a distinguishable
:class:`~brr.gitops.NotAGitRepository` (still a ``RuntimeError`` subclass,
so every ``except RuntimeError`` that degrades around "no brnrd here" keeps
working), caught in ``cli.main`` and rendered as a clean ``[brnrd] …`` line
whose message names the cwd and both ways out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import cli, gitops


def test_not_a_git_repo_raises_the_named_class(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(gitops.NotAGitRepository) as exc:
        gitops.ensure_git_repo()
    msg = str(exc.value)
    assert str(tmp_path) in msg  # names where the user actually is
    assert "git init" in msg  # path out #1
    assert "cd" in msg  # path out #2


def test_it_is_a_runtimeerror_but_not_a_repotreeunusable():
    # Subclassing RuntimeError is load-bearing: `_maybe_repo_root` and
    # adopt's repo check both `except RuntimeError` to degrade to "no brnrd
    # here", and must keep matching. RepoTreeUnusable is the *other*
    # failure (a named-but-absent tree) and must stay a disjoint class.
    assert issubclass(gitops.NotAGitRepository, RuntimeError)
    assert not issubclass(gitops.NotAGitRepository, gitops.RepoTreeUnusable)
    assert not issubclass(gitops.RepoTreeUnusable, gitops.NotAGitRepository)


def test_maybe_repo_root_still_degrades_to_none(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli._maybe_repo_root() is None


def test_cli_main_renders_a_clean_line_not_a_traceback(tmp_path, monkeypatch, capsys):
    # A command that resolves the repo root from cwd, driven from a non-git
    # folder, must exit with a single `[brnrd] …` line — no traceback.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.main(["gate-run"])
    rendered = str(exc.value)
    assert rendered.startswith("[brnrd] ")
    assert "not a git repository" in rendered.lower()
    assert "Traceback" not in rendered
