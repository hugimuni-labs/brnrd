"""#1108 — a working tree git names but does not have.

The incident this file pins: a torn-down run worktree left
``core.worktree`` in the *shared* git config. ``rev-parse --show-toplevel``
kept answering with the deleted path **at exit 0**, so the next call to use
that answer as a ``cwd=`` died on a bare ``FileNotFoundError``. The daemon
crash-looped on its own first statement 312 times across 27 minutes, and
``brnrd daemon status`` — the command an operator types to ask what is
wrong — died in the same three frames.

Every test here fails on the pre-#1108 tree: the raise is a
``FileNotFoundError`` with no cause and no repair, ``ensure_git_repo``
returns the corpse path happily, and neither heal exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import gitops
from brr import worktree as worktree_mod


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", ".")
    _git(root, "commit", "-q", "--allow-empty", "-m", "root")
    return root


def _pin(repo: Path, value: Path) -> None:
    _git(repo, "config", "core.worktree", str(value))


def _dead_run_worktree(repo: Path, run_id: str = "run-260804-1103-fk72") -> Path:
    """A brnrd run worktree path that is pinned and then torn down."""
    path = repo / ".brr" / "worktrees" / run_id
    path.mkdir(parents=True)
    _pin(repo, path)
    path.rmdir()
    return path


# ── the polite lie itself ────────────────────────────────────────────

def test_git_still_reports_the_deleted_tree_at_exit_zero(repo: Path) -> None:
    """The premise. If git ever starts failing here, this file is obsolete.

    Nothing brnrd does can be blamed for the crash without this fact: git
    is *asked* which tree this is, answers with a directory that does not
    exist, and reports success.
    """
    dead = _dead_run_worktree(repo)
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=repo,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    assert result.returncode == 0
    assert Path(result.stdout.strip()) == dead
    assert not dead.exists()


# ── diagnosis, not a traceback ───────────────────────────────────────

def test_git_wrapper_raises_a_diagnosed_error_for_a_missing_cwd(repo: Path) -> None:
    dead = _dead_run_worktree(repo)
    with pytest.raises(gitops.RepoTreeUnusable) as excinfo:
        gitops._git(dead, "rev-parse", "--git-common-dir", check=False)
    message = str(excinfo.value)
    assert str(dead) in message
    assert "does not exist" in message


def test_ensure_git_repo_refuses_the_corpse_instead_of_returning_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact call at the head of the crash: cli._repo_root -> here."""
    dead = _dead_run_worktree(repo)
    monkeypatch.chdir(repo)
    with pytest.raises(gitops.RepoTreeUnusable) as excinfo:
        gitops.ensure_git_repo()
    assert str(dead) in str(excinfo.value)


def test_the_diagnosis_names_the_cause_and_the_repair(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dead_run_worktree(repo)
    monkeypatch.chdir(repo)
    with pytest.raises(gitops.RepoTreeUnusable) as excinfo:
        gitops.ensure_git_repo()
    message = str(excinfo.value)
    assert "core.worktree" in message
    assert "git config --unset core.worktree" in message


def test_the_diagnosis_says_unclear_when_no_pin_explains_it(tmp_path: Path) -> None:
    """A remedy is part of a diagnostic's truth claim (#792).

    Offering ``--unset core.worktree`` for a tree that vanished for some
    other reason would be a confident lie about both the cause and the fix,
    so the classifier must reach the sentence rather than one hard-coded
    branch reaching every case (#786).
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    text = gitops.diagnose_unusable_tree(tmp_path / "gone", asked_from=plain)
    assert "unclear" in text
    assert "git config --unset core.worktree" not in text


# ── the exception class is load-bearing ──────────────────────────────

def test_unusable_tree_is_an_oserror_and_not_a_runtimeerror() -> None:
    """Not a taxonomy preference — two live call sites depend on it.

    ``OSError``: the failure it replaces is one, so every existing
    ``except OSError`` around a git call (``gitops._is_working_tree``, for
    one) keeps its behaviour, only better informed.

    **Not** ``RuntimeError``: ``cli._maybe_brr_dir`` swallows that class to
    mean *no brnrd repository here*. Under it, the crash would become a
    shrug — brnrd reporting "nothing installed" while the real answer is a
    repointed config. That is the silent-narrowing failure the whole guard
    exists to end, reintroduced one ``except`` clause at a time.
    """
    assert issubclass(gitops.RepoTreeUnusable, OSError)
    assert not issubclass(gitops.RepoTreeUnusable, RuntimeError)


def test_maybe_brr_dir_does_not_swallow_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brr import cli

    _dead_run_worktree(repo)
    monkeypatch.chdir(repo)
    with pytest.raises(gitops.RepoTreeUnusable):
        cli._maybe_brr_dir()


def test_cli_main_renders_the_diagnosis_instead_of_a_traceback(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    from brr import cli

    _dead_run_worktree(repo)
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["daemon", "status"])
    message = str(excinfo.value.code)
    assert message.startswith("[brnrd] ")
    assert "core.worktree" in message
    assert "Traceback" not in message


# ── the two repairs ──────────────────────────────────────────────────

def test_heal_unsets_a_pin_naming_a_dead_brnrd_worktree(repo: Path) -> None:
    _dead_run_worktree(repo)
    assert gitops.heal_stale_brnrd_worktree_pin(repo) is True
    assert gitops._config_value(repo, "core.worktree") == ""


def test_heal_leaves_a_pin_that_still_names_a_real_directory(repo: Path) -> None:
    """Only *provably* dead values. A live repoint may be somebody's intent."""
    live = repo / ".brr" / "worktrees" / "run-live"
    live.mkdir(parents=True)
    _pin(repo, live)
    assert gitops.heal_stale_brnrd_worktree_pin(repo) is False
    assert gitops._config_value(repo, "core.worktree") == str(live)


def test_heal_leaves_a_dead_pin_outside_brnrds_own_worktree_root(
    repo: Path, tmp_path: Path,
) -> None:
    """brnrd repairs its own garbage and nobody else's."""
    foreign = tmp_path / "somewhere" / "else"
    _pin(repo, foreign)
    assert gitops.heal_stale_brnrd_worktree_pin(repo) is False
    assert gitops._config_value(repo, "core.worktree") == str(foreign)


def test_the_pin_is_readable_even_when_git_refuses_every_command(
    repo: Path,
) -> None:
    """The deep-broken pin: git will not even read its own config.

    Two shapes, and only one of them is the incident. When the pinned
    path's *parent* still exists, ``rev-parse`` answers with the corpse at
    exit 0 (#1108). When the parent is gone too, git refuses **everything**
    in the repository with ``fatal: Invalid path``, rc 128 — ``git config
    --get`` included, and ``-f <the config file>`` does not help.

    This is the case that caught the first version of this guard: the
    classifier read no pin, concluded no pin existed, and told the operator
    to go look somewhere else. The read has to be immune to the thing it is
    diagnosing.
    """
    deep = repo / "gone" / "deeper" / "still"
    _pin(repo, deep)
    plain = subprocess.run(
        ["git", "config", "--get", "core.worktree"], cwd=repo,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    assert plain.returncode != 0, "premise: git refuses the ordinary read here"
    assert gitops._config_value(repo, "core.worktree") == str(deep)


def test_the_diagnosis_reports_a_pin_it_could_not_read_the_easy_way(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same case, through the sentence a human actually reads."""
    deep = repo / "gone" / "deeper" / "still"
    _pin(repo, deep)
    monkeypatch.chdir(repo)
    text = gitops.diagnose_unusable_tree(deep, asked_from=repo)
    assert "core.worktree" in text
    assert "unclear" not in text


def test_worktree_removal_clears_a_pin_naming_what_it_just_removed(
    repo: Path,
) -> None:
    """Closing the source, not only the symptom.

    Structural rather than a guess about the writer, which #746 never
    identified: a pin naming a directory this call has just deleted is
    garbage whoever wrote it.
    """
    dead = repo / ".brr" / "worktrees" / "run-torn-down"
    dead.mkdir(parents=True)
    _pin(repo, dead)
    dead.rmdir()
    assert worktree_mod.clear_stale_worktree_pin(repo, dead) is True
    assert gitops._config_value(repo, "core.worktree") == ""


def test_worktree_removal_leaves_a_pin_naming_something_else(repo: Path) -> None:
    other = repo / ".brr" / "worktrees" / "run-other"
    other.mkdir(parents=True)
    _pin(repo, other)
    removed = repo / ".brr" / "worktrees" / "run-removed"
    assert worktree_mod.clear_stale_worktree_pin(repo, removed) is False
    assert gitops._config_value(repo, "core.worktree") == str(other)
