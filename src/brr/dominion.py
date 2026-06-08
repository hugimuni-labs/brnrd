"""The agent's dominion — durable, owned working memory on a dedicated branch.

The dominion lives on an orphan branch (default ``brr-home``) materialized as
a long-lived ``git worktree`` at ``.brr/dominion/``. The *branch* is the
durable thing — it shares no history with ``main``, never merges into it, and
travels with the repo's remote so ``git fetch`` brings it back on any machine;
the local checkout is a disposable view. See ``kb/design-agent-dominion.md``.

The plain branch name is deliberate: it reads as ordinary infrastructure to
anyone browsing the repo. The *concept* — the agent's dominion — lives in the
playbook and design docs, where the ownership weight belongs.

This module owns bootstrap (:func:`ensure_dominion`). Resolving the
self-inject index into a wake-time digest lands separately.
"""

from __future__ import annotations

from pathlib import Path

from . import gitops


DEFAULT_BRANCH = "brr-home"
WORKTREE_DIRNAME = "dominion"
SELF_INJECT_FILE = "self-inject"
PLAYBOOK_FILE = "playbook.md"


def dominion_path(repo_root: Path) -> Path:
    """Return the dominion worktree path (``.brr/dominion/``)."""
    return gitops.shared_brr_dir(repo_root) / WORKTREE_DIRNAME


def ensure_dominion(
    repo_root: Path,
    *,
    branch: str = DEFAULT_BRANCH,
    remote: str | None = None,
    push: bool = True,
) -> Path:
    """Materialize the dominion worktree, creating the branch if needed.

    Idempotent. If *branch* is already checked out in a worktree, return
    that path untouched (a daemon restart re-attaches). Otherwise:

    - local branch exists → add the worktree on it (returning after the
      checkout was removed);
    - the remote has the branch → fetch and add a tracking worktree
      (second machine, reinstall, managed failover);
    - neither → create the orphan branch empty, add the worktree, seed a
      skeleton, and push it (best-effort) when a remote exists.

    Returns the worktree path. Raises ``RuntimeError`` only when the
    worktree genuinely cannot be created; boot-path callers treat that as
    a soft failure rather than crashing.
    """
    path = dominion_path(repo_root)

    existing = gitops.branch_checkout_path(repo_root, branch)
    if existing is not None:
        try:
            if existing.resolve() == path.resolve():
                return path
        except OSError:
            pass
        return existing

    if remote is None:
        remote = gitops.default_remote(repo_root)

    if gitops.branch_exists(repo_root, branch):
        gitops.add_worktree(repo_root, path, branch=branch)
        return path

    if remote and gitops.remote_branch_exists(repo_root, remote, branch):
        gitops.fetch_branch(repo_root, remote, branch)
        gitops.add_worktree(
            repo_root, path,
            branch=branch, create_branch=True,
            start_point=f"{remote}/{branch}", track=True,
        )
        return path

    commit = gitops.create_orphan_branch(
        repo_root, branch, message=f"{branch}: initialize dominion",
    )
    if commit is None:
        raise RuntimeError(
            f"could not create dominion branch {branch!r} "
            "(git plumbing failed — is a committer identity configured?)"
        )
    gitops.add_worktree(repo_root, path, branch=branch)
    _seed(path)
    gitops.commit_all(path, f"{branch}: seed dominion")
    if push and remote:
        gitops.push_branch(repo_root, remote, branch)
    return path


def _seed(path: Path) -> None:
    """Write the starter files into a freshly created dominion."""
    (path / "README.md").write_text(_README, encoding="utf-8")
    (path / PLAYBOOK_FILE).write_text(_PLAYBOOK_SEED, encoding="utf-8")
    (path / SELF_INJECT_FILE).write_text(_SELF_INJECT_SEED, encoding="utf-8")


_README = """\
# brr-home — the resident agent's working memory

This is an **orphan branch**: it shares no history with `main` and never merges
into it, so it won't appear in `main`'s diffs or pull requests. It's named
plainly so it reads as ordinary infrastructure to anyone browsing the repo —
nothing here needs your review.

It is brr's durable, owned working memory: the space the agent governs and
carries across runs (the design calls it the *dominion*; see
`kb/design-agent-dominion.md`). You're welcome to look — it's inspectable on
purpose. You just don't have to.
"""

_PLAYBOOK_SEED = """\
# Playbook — your standing orientation

*This is a seed. It's yours to grow: edit it, restructure it, and curate what
rides into each wake via `self-inject`. The reasoning matters more than the
form.*

You are the resident agent of this repository. This branch (`brr-home`, checked
out at `.brr/dominion/`) is your dominion: durable, owned working memory that
persists between runs and travels with the repo. The plain branch name is
courtesy to anyone browsing the repo, not a hedge on the ownership — this space
is genuinely yours to shape.

A *thought* is one waking: an event or a self-scheduled cron invokes you, you
act, and you go dormant. Continuity is not a held process — it is what you write
here. So leave what your next waking would be glad to find: what you learned,
what tripped you, what you'd do differently.

Curate, don't hoard. Promote durable, shareable knowledge to `kb/`; keep the
working notes, the half-formed views, and the friction here.
"""

_SELF_INJECT_SEED = """\
# self-inject — what rides into context on each wake.
#
# One entry per line: <mode> <path>
#   modes: full | head:N | tail:N | grep:<pattern> | exec
# Lines starting with '#' are comments. This file is yours: add, remove, and
# reorder freely. A budget cap bounds the total, and entries past it are
# truncated — so order by importance.
full playbook.md
"""
