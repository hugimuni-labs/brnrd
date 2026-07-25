"""A worker's git must not be able to reach the shared host checkout (#703).

Both halves of the containment, driven against real checkouts rather than
mocks. The bug happened through a real ``git commit`` from a real drifted cwd,
and a mock of ``subprocess`` would have agreed with the pre-fix code as
happily as with the post-fix code — the failure mode this repo files under
"copies that agree can be wrong together".

Layout every test builds:

    <tmp>/host          the shared checkout, on `main`   ← must never be written
    <tmp>/wt/<run>      the worker's own worktree, on its own branch
    <tmp>/other         an unrelated repository, for the `-C` blindness probes
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from brr import cli, daemon, gitops
from brr.run import Run

from _helpers import init_git_repo


# ── scaffolding ─────────────────────────────────────────────────────


def _git(cwd, *args, env=None, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=check,
        capture_output=True, text=True,
    )


def _head(repo):
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _branch(repo):
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


@pytest.fixture
def trees(tmp_path):
    """A host checkout with one commit, plus a linked worker worktree."""
    host = tmp_path / "host"
    init_git_repo(host)
    (host / "README.md").write_text("host\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-m", "host: base")
    run_root = tmp_path / "wt" / "run-703-child"
    run_root.parent.mkdir(parents=True, exist_ok=True)
    _git(host, "worktree", "add", "-b", "brr/child", str(run_root), "main")
    return host, run_root


def _worker(run_root, *, env="worktree", run_id="run-703-child"):
    return Run(
        id=run_id, event_id="evt-703", body="spec", env=env,
        meta={"worker": True},
    )


def _pinned_env(task, run_root):
    """The child's environment as the daemon actually constructs it."""
    return {**os.environ, **daemon._child_git_pin(task, run_root)}


# ── half 1: the pin refuses the write ───────────────────────────────


def test_pin_names_the_worktree_not_the_shared_checkout(trees):
    host, run_root = trees
    pin = daemon._child_git_pin(_worker(run_root), run_root)
    assert set(pin) == {"GIT_DIR", "GIT_WORK_TREE"}
    assert pin["GIT_WORK_TREE"] == str(run_root)
    # The worktree's *own* administrative dir, not the shared common dir —
    # pointing GIT_DIR at the common dir would put the worker on the main
    # checkout's HEAD, which is the bug wearing a hat.
    assert pin["GIT_DIR"] == str(gitops.absolute_git_dir(run_root))
    assert pin["GIT_DIR"] != str(host / ".git")


def test_bare_commit_from_execution_root_cannot_reach_host_branch(trees):
    """The incident, replayed: `cd <host> && git add -A && git commit`.

    The pre-fix outcome was 262 insertions on the shared checkout's `main`.
    """
    host, run_root = trees
    task = _worker(run_root)
    env = _pinned_env(task, run_root)
    host_head_before = _head(host)

    # The worker writes its deliverable into the *execution root* — the exact
    # drift that opened #703 — then runs a bare add/commit there.
    (host / "deliverable.md").write_text("262 insertions\n", encoding="utf-8")
    _git(host, "add", "-A", env=env, check=False)
    committed = _git(
        host, "commit", "-m", "feat: the worker's deliverable",
        env=env, check=False,
    )

    # Either refusal or redirection is acceptable; landing on the shared
    # checkout is not. Here the worktree is clean, so git refuses — loudly,
    # non-zero, with a message a reader can act on.
    assert committed.returncode != 0
    assert "nothing to commit" in (committed.stdout + committed.stderr).lower()
    assert _head(host) == host_head_before, "the shared checkout's HEAD moved"
    assert _branch(host) == "main", "the shared checkout's branch changed"
    # And the file is still there to recover — refused, not destroyed.
    assert (host / "deliverable.md").exists()


def test_bare_commit_from_execution_root_lands_on_the_worker_branch(trees):
    """The other half of "or fails loudly": when the worker *has* work.

    Same drifted cwd, but the worktree is dirty. The commit must succeed and
    land on the worker's own branch — a pin that only ever refused would make
    every drifted worker fail instead of quietly doing the right thing.
    """
    host, run_root = trees
    task = _worker(run_root)
    env = _pinned_env(task, run_root)
    host_head_before = _head(host)
    child_head_before = _head(run_root)

    (run_root / "deliverable.md").write_text("real work\n", encoding="utf-8")
    _git(host, "add", "-A", env=env)
    _git(host, "commit", "-m", "feat: the worker's deliverable", env=env)

    assert _head(host) == host_head_before
    assert _head(run_root) != child_head_before
    assert _branch(run_root) == "brr/child"
    assert "deliverable.md" in _git(
        run_root, "show", "--stat", "--format=", "HEAD",
    ).stdout


def test_pin_is_all_or_nothing(trees):
    """Never one variable alone.

    `GIT_WORK_TREE` alone leaves git discovering the object store from cwd;
    `GIT_DIR` alone leaves it discovering the work tree from cwd. Each
    cross-wires one tree's working files to another tree's index — a
    corruption mode the incident never had.
    """
    _host, run_root = trees
    pin = daemon._child_git_pin(_worker(run_root), run_root)
    assert ("GIT_DIR" in pin) == ("GIT_WORK_TREE" in pin)


def test_no_pin_for_a_resident_or_a_host_run(trees):
    """Scope follows the contract.

    A resident commits knowledge into `.brnrd-kb/` — a separate repository
    beside the checkout — so a pin would break its one durable write path. A
    `host` run's cwd *is* the checkout and its commits belong there.
    """
    _host, run_root = trees
    resident = Run(id="run-res", event_id="e", body="b", env="worktree", meta={})
    assert daemon._child_git_pin(resident, run_root) == {}
    host_worker = _worker(run_root, env="host")
    assert daemon._child_git_pin(host_worker, run_root) == {}


def test_pin_absent_rather_than_half_built_when_git_dir_unreadable(tmp_path):
    task = _worker(tmp_path / "nope")
    assert daemon._child_git_pin(task, tmp_path / "not-a-repo") == {}


# ── the pin's cost, and brnrd's immunity to it ──────────────────────


def test_pin_blinds_a_hand_rolled_git_to_every_other_tree(trees, tmp_path):
    """The named consequence, pinned as a fact rather than left as a worry.

    Under the pin, `git -C <other repo>` answers about the *pinned* worktree,
    exit 0, no warning. This test exists so the day someone narrows the pin,
    the blast radius is already written down.
    """
    _host, run_root = trees
    other = tmp_path / "other"
    init_git_repo(other)
    _git(other, "commit", "--allow-empty", "-m", "other: base")
    env = _pinned_env(_worker(run_root), run_root)

    blinded = _git(other, "rev-parse", "--show-toplevel", env=env)
    assert blinded.stdout.strip() == str(run_root)  # not `other`

    # The escape `prompts/worker.md` hands the worker.
    escaped_env = {
        k: v for k, v in env.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")
    }
    escaped = _git(other, "rev-parse", "--show-toplevel", env=escaped_env)
    assert escaped.stdout.strip() == str(other.resolve())


def test_brnrd_own_git_reads_survive_an_inherited_pin(trees, monkeypatch):
    """brnrd names the repository it means, so the pin must not outrank it.

    Without `gitops.explicit_repo_env`, every ref read brnrd makes from inside
    a pinned worker — including the stray-write check below — would report the
    worker's worktree while naming the host checkout. The check would then be
    verifiable only by the thing it guards.
    """
    host, run_root = trees
    _git(run_root, "commit", "--allow-empty", "-m", "child: work")
    # Both readings must be taken BEFORE the pin exists. `_git` here passes no
    # env, so once the pin is set this file's own helpers are blinded by it —
    # which is worth saying out loud, because the first draft of this test
    # compared immune `gitops` output against a blinded `_head(host)` and
    # "failed" while the code under test was correct. The hazard is not
    # hypothetical enough to paraphrase.
    host_head = _head(host)
    child_head = _head(run_root)
    assert host_head != child_head
    for var, value in daemon._child_git_pin(_worker(run_root), run_root).items():
        monkeypatch.setenv(var, value)

    assert gitops.rev_parse(host, "HEAD") == host_head
    assert gitops.rev_parse(host, "HEAD") != child_head
    assert gitops.current_branch(host) == "main"
    # The control: an unscrubbed reader in the same process *is* blinded, so
    # this test would pass vacuously if `explicit_repo_env` were a no-op.
    assert _head(host) == child_head


def test_cli_entrypoint_drops_an_inherited_pin(monkeypatch):
    """`brnrd hook <phase>` runs inside the pinned child; hooks read git."""
    monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere")
    cli._drop_inherited_git_pin()
    assert "GIT_DIR" not in os.environ
    assert "GIT_WORK_TREE" not in os.environ


def test_explicit_repo_env_drops_only_the_overrides(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/x/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/x")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "keep me")
    scrubbed = gitops.explicit_repo_env()
    assert "GIT_DIR" not in scrubbed and "GIT_WORK_TREE" not in scrubbed
    assert scrubbed["GIT_AUTHOR_NAME"] == "keep me"


# ── half 2: the finalize-time check ─────────────────────────────────
#
# One positive control per arm, then every near-miss the spec named. A test
# that only proves silence passes when the check is never wired at all.


def _dispatched(host, run_root, *, env="worktree", run_id="run-703-child"):
    """A run with its host baseline recorded, as `_run_worker` does."""
    task = _worker(run_root, env=env, run_id=run_id)
    daemon._record_host_baseline(task, host)
    return task


def _commit_in_host_as(host, run_id, message="feat: stray"):
    """A commit in the shared checkout stamped by the run-id commit-msg hook.

    Uses the real hook, not a hand-rolled one: the trailer only parses when it
    lands in its own paragraph, and getting that wrong is how a test can
    "prove" attribution works while the shipped hook produces nothing.
    """
    gitops.ensure_run_id_hook(host)
    (host / f"stray-{run_id}.md").write_text("work\n", encoding="utf-8")
    env = {**os.environ, "BRR_RUN_ID": run_id} if run_id else dict(os.environ)
    _git(host, "add", "-A", env=env)
    _git(host, "commit", "-m", message, env=env)


def test_positive_control_stray_commit_is_attributed(trees):
    """The incident: the run's branch never moved, the host's HEAD did, and
    the commits there carry this run's own id."""
    host, run_root = trees
    task = _dispatched(host, run_root)
    _commit_in_host_as(host, task.id)

    verdict = daemon._stray_host_write(task, host)
    assert verdict is not None, "the check did not fire on the real shape"
    assert verdict["kind"] == "stray-commit"
    assert verdict["stray_commits"] == [_head(host)]
    assert verdict["host_head_now"] == _head(host)


def test_positive_control_stranded_work_survives_the_pin(trees):
    """The mode the pin *creates*, and the reason the specced conjunction
    alone would have been aimed at the branch the fix removes: after the pin
    the host's HEAD no longer moves, so only the working tree shows it."""
    host, run_root = trees
    task = _dispatched(host, run_root)
    head_before = _head(host)

    # `git add -A` swept the clean worktree, the commit exited 1, and the
    # deliverable is left unstaged in the maintainer's tree.
    (host / "deliverable.md").write_text("262 insertions\n", encoding="utf-8")

    verdict = daemon._stray_host_write(task, host)
    assert verdict is not None, "the check did not fire on the post-fix shape"
    assert verdict["kind"] == "stranded-worktree"
    assert verdict["stranded_paths"] == ["deliverable.md"]
    assert _head(host) == head_before, "this arm must not need a ref to move"


def test_near_miss_host_moved_and_child_branch_moved_too(trees):
    """Legal: the run committed its own work; something else touched the host."""
    host, run_root = trees
    task = _dispatched(host, run_root)
    task.meta["has_new_commit"] = True
    _commit_in_host_as(host, task.id)
    assert daemon._stray_host_write(task, host) is None


def test_near_miss_host_still_and_child_did_nothing(trees):
    """Legal, and the commonest shape: a review run commits nothing."""
    host, run_root = trees
    task = _dispatched(host, run_root)
    assert daemon._stray_host_write(task, host) is None


def test_near_miss_host_still_and_child_committed(trees):
    host, run_root = trees
    task = _dispatched(host, run_root)
    task.meta["has_new_commit"] = True
    assert daemon._stray_host_write(task, host) is None


def test_a_siblings_commit_is_not_this_runs_stray(trees):
    """Attribution, not proximity — the arm that keeps the note readable.

    A sibling run legitimately committing in the shared checkout during this
    run's life reaches the specced conjunction exactly. Reporting that as a
    stray write is how a guard earns being waved through, so it degrades to
    the unattributed advisory instead of an accusation.
    """
    host, run_root = trees
    task = _dispatched(host, run_root)
    _commit_in_host_as(host, "run-some-other-sibling")

    verdict = daemon._stray_host_write(task, host)
    assert verdict is not None
    assert verdict["kind"] == "host-head-moved"
    assert "stray_commits" not in verdict


def test_a_humans_unstamped_commit_is_not_attributed(trees):
    host, run_root = trees
    task = _dispatched(host, run_root)
    _commit_in_host_as(host, "")  # no BRR_RUN_ID: a maintainer, logged in
    verdict = daemon._stray_host_write(task, host)
    assert verdict is not None and verdict["kind"] == "host-head-moved"


def test_host_env_run_is_never_checked(trees):
    """A `host` run's commits legitimately land in this very checkout."""
    host, run_root = trees
    task = _dispatched(host, run_root, env="host")
    assert "host_head_at_dispatch" not in task.meta
    _commit_in_host_as(host, task.id)
    assert daemon._stray_host_write(task, host) is None


def test_no_baseline_disables_the_check_rather_than_guessing(trees):
    host, run_root = trees
    task = _worker(run_root)  # never dispatched through _record_host_baseline
    _commit_in_host_as(host, task.id)
    assert daemon._stray_host_write(task, host) is None


def test_pre_existing_dirt_in_the_host_tree_is_not_a_finding(trees):
    """The maintainer's own work-in-progress was already there."""
    host, run_root = trees
    (host / "wip.md").write_text("mine\n", encoding="utf-8")
    task = _dispatched(host, run_root)
    assert json.loads(task.meta["host_dirty_at_dispatch"]) == ["wip.md"]
    assert daemon._stray_host_write(task, host) is None


def test_an_over_cap_baseline_says_so_instead_of_accusing_everything(
    trees, monkeypatch,
):
    """A truncated baseline is worse than none: every dropped path would read
    as new. Skip the arm, and record the skip — an inventory that bounds its
    coverage has to say how much (#721)."""
    host, run_root = trees
    monkeypatch.setattr(daemon, "_HOST_BASELINE_PATH_CAP", 1)
    for name in ("a.md", "b.md", "c.md"):
        (host / name).write_text("x\n", encoding="utf-8")

    task = _dispatched(host, run_root)
    assert "host_dirty_at_dispatch" not in task.meta
    assert task.meta["host_dirty_at_dispatch_skipped"] == "over-cap:3"
    # Arm 2 is off, so a new path is not reported...
    (host / "d.md").write_text("x\n", encoding="utf-8")
    assert daemon._stray_host_write(task, host) is None
    # ...but arm 1 still works, which is why the skip is survivable.
    _commit_in_host_as(host, task.id)
    assert daemon._stray_host_write(task, host)["kind"] == "stray-commit"


def test_strongest_signal_wins_when_both_arms_fire(trees):
    host, run_root = trees
    task = _dispatched(host, run_root)
    _commit_in_host_as(host, task.id)
    (host / "also-stranded.md").write_text("x\n", encoding="utf-8")
    verdict = daemon._stray_host_write(task, host)
    assert verdict["kind"] == "stray-commit"
    assert set(verdict["signals"]) == {"stray-commit", "stranded-worktree"}


def test_a_rename_reports_its_destination(trees):
    host, run_root = trees
    task = _dispatched(host, run_root)
    _git(host, "mv", "README.md", "READENGINE.md")
    verdict = daemon._stray_host_write(task, host)
    assert verdict["kind"] == "stranded-worktree"
    assert "READENGINE.md" in verdict["stranded_paths"]
