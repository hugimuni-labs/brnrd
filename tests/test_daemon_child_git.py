"""A strand's git must not be able to reach the shared host checkout (#703).

Both halves of the containment, driven against real checkouts rather than
mocks. The bug happened through a real ``git commit`` from a real drifted cwd,
and a mock of ``subprocess`` would have agreed with the pre-fix code as
happily as with the post-fix code — the failure mode this repo files under
"copies that agree can be wrong together".

Layout every test builds:

    <tmp>/host          the shared checkout, on `main`   ← must never be written
    <tmp>/wt/<run>      the strand's own worktree, on its own branch
    <tmp>/other         an unrelated repository, for the `-C` blindness probes
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from brr import cli, daemon, envs, gitops, worktree
from brr.run import Run
from brr.runner import RunnerResult

from _helpers import init_git_repo, make_event, write_repo_scaffold


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
    """A host checkout with one commit, plus a linked strand worktree."""
    host = tmp_path / "host"
    init_git_repo(host)
    (host / "README.md").write_text("host\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-m", "host: base")
    run_root = tmp_path / "wt" / "run-703-child"
    run_root.parent.mkdir(parents=True, exist_ok=True)
    _git(host, "worktree", "add", "-b", "brr/child", str(run_root), "main")
    return host, run_root


def _strand(run_root, *, env="worktree", run_id="run-703-child"):
    return Run(
        id=run_id, event_id="evt-703", body="spec", env=env,
        meta={"strand": True},
    )


def _pinned_env(task, run_root):
    """The child's environment as the daemon actually constructs it."""
    return {**os.environ, **daemon._child_git_pin(task, run_root)}


# ── half 1: the pin refuses the write ───────────────────────────────


def test_pin_names_the_worktree_not_the_shared_checkout(trees):
    host, run_root = trees
    pin = daemon._child_git_pin(_strand(run_root), run_root)
    assert set(pin) == {"GIT_DIR", "GIT_WORK_TREE"}
    assert pin["GIT_WORK_TREE"] == str(run_root)
    # The worktree's *own* administrative dir, not the shared common dir —
    # pointing GIT_DIR at the common dir would put the strand on the main
    # checkout's HEAD, which is the bug wearing a hat.
    assert pin["GIT_DIR"] == str(gitops.absolute_git_dir(run_root))
    assert pin["GIT_DIR"] != str(host / ".git")


def test_bare_commit_from_execution_root_cannot_reach_host_branch(trees):
    """The incident, replayed: `cd <host> && git add -A && git commit`.

    The pre-fix outcome was 262 insertions on the shared checkout's `main`.
    """
    host, run_root = trees
    task = _strand(run_root)
    env = _pinned_env(task, run_root)
    host_head_before = _head(host)

    # The strand writes its deliverable into the *execution root* — the exact
    # drift that opened #703 — then runs a bare add/commit there.
    (host / "deliverable.md").write_text("262 insertions\n", encoding="utf-8")
    _git(host, "add", "-A", env=env, check=False)
    committed = _git(
        host, "commit", "-m", "feat: the strand's deliverable",
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


def test_bare_commit_from_execution_root_lands_on_the_strand_branch(trees):
    """The other half of "or fails loudly": when the strand *has* work.

    Same drifted cwd, but the worktree is dirty. The commit must succeed and
    land on the strand's own branch — a pin that only ever refused would make
    every drifted strand fail instead of quietly doing the right thing.
    """
    host, run_root = trees
    task = _strand(run_root)
    env = _pinned_env(task, run_root)
    host_head_before = _head(host)
    child_head_before = _head(run_root)

    (run_root / "deliverable.md").write_text("real work\n", encoding="utf-8")
    _git(host, "add", "-A", env=env)
    _git(host, "commit", "-m", "feat: the strand's deliverable", env=env)

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
    pin = daemon._child_git_pin(_strand(run_root), run_root)
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
    host_strand = _strand(run_root, env="host")
    assert daemon._child_git_pin(host_strand, run_root) == {}


def test_pin_absent_rather_than_half_built_when_git_dir_unreadable(tmp_path):
    task = _strand(tmp_path / "nope")
    assert daemon._child_git_pin(task, tmp_path / "not-a-repo") == {}


# ── the wiring: the pin must reach the env the runner is actually given ──
#
# Every test above calls `_child_git_pin` directly, which measures the pin's
# *logic* and says nothing about whether anything calls it. Deleting the
# `env.update(_child_git_pin(...))` line in `_runner_runtime` would leave all
# of them green — a guard can be perfect and unwired, and "counting call sites
# overcounts coverage" is this repo's own lesson. These two drive `_run_worker`
# end to end and read the env the runner is handed.


def _drive_run_worker(tmp_path, monkeypatch, *, strand: bool, during=None,
                      finalize=False):
    """Run `_run_worker` against a real checkout + real worktree, and return
    the environment the runner was actually invoked with.

    ``during`` is called with ``(repo_root, run_root)`` while the stub runner
    is "executing", so a test can simulate what a drifted strand does. With
    ``finalize=True`` the whole ``_run_worker_and_finalize`` path runs, which
    is what puts the stray-write check itself under test rather than just the
    function it calls.
    """
    # `_run_worker` resolves a Shell+Core profile before it builds any
    # environment, and `resolve_runner_profile` raises when neither `claude`
    # nor `codex` is on PATH. Unstubbed, that makes this helper pass on every
    # dev machine (which has a Shell installed) and fail only on the gate,
    # which has none — the tests it drives went green locally and red on CI.
    # Same stub every other `_run_worker` test in this suite already uses.
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    write_repo_scaffold(tmp_path)
    init_git_repo(tmp_path)
    (tmp_path / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "seed")
    run_root = tmp_path / ".brr" / "worktrees" / "wiring"
    run_root.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "worktree", "add", "-b", "brr/wiring", str(run_root), "main")

    extra = {"strand": True} if strand else {}
    event = make_event(tmp_path, eid="evt-wiring", **extra)
    seen: dict[str, str] = {}

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                    outbox_path=None):
            return envs.RunContext(
                name=self.name, cwd=run_root, repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                outbox_host=outbox_path, outbox_env=outbox_path,
                branch_name="brr/wiring",
                env_state={"worktree_path": str(run_root)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
            seen.update(invocation.env)
            if during is not None:
                during(tmp_path, run_root)
            Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
            Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="ok\n", stderr="", returncode=0, trace_dir=None,
                artifacts=[],
            )

        def finalize(self, ctx, task, runs_dir):
            return task

    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: StubEnv())
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **k: "PROMPT",
    )
    driver = (
        daemon._run_worker_and_finalize if finalize else daemon._run_worker
    )
    task = driver(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)
    return task, seen, run_root


def test_the_pin_reaches_the_runners_environment(tmp_path, monkeypatch):
    task, seen, run_root = _drive_run_worker(tmp_path, monkeypatch, strand=True)
    assert task.meta.get("strand") is True
    assert seen.get("GIT_WORK_TREE") == str(run_root)
    assert seen.get("GIT_DIR") == str(gitops.absolute_git_dir(run_root))


def test_no_pin_reaches_a_non_strand_runs_environment(tmp_path, monkeypatch):
    _task, seen, _run_root = _drive_run_worker(tmp_path, monkeypatch, strand=False)
    assert "GIT_DIR" not in seen
    assert "GIT_WORK_TREE" not in seen


def test_the_bot_identity_reaches_a_strands_environment(tmp_path, monkeypatch):
    """#1135: a strand's own `git commit` — typed in its own shell, never
    routed through `gitops.bot_identity_env()` — must not fall through to
    whatever identity happens to be live on the host at that moment."""
    _task, seen, _run_root = _drive_run_worker(tmp_path, monkeypatch, strand=True)
    assert seen.get("GIT_AUTHOR_NAME") == gitops.BOT_NAME
    assert seen.get("GIT_AUTHOR_EMAIL") == gitops.BOT_EMAIL
    assert seen.get("GIT_COMMITTER_NAME") == gitops.BOT_NAME
    assert seen.get("GIT_COMMITTER_EMAIL") == gitops.BOT_EMAIL


def test_the_bot_identity_reaches_a_residents_environment_too(tmp_path, monkeypatch):
    """Unlike the git pin above, the identity fix is not strand-scoped: a
    resident's own themed-work commits are equally in scope (#1135)."""
    _task, seen, _run_root = _drive_run_worker(tmp_path, monkeypatch, strand=False)
    assert seen.get("GIT_AUTHOR_NAME") == gitops.BOT_NAME
    assert seen.get("GIT_AUTHOR_EMAIL") == gitops.BOT_EMAIL
    assert seen.get("GIT_COMMITTER_NAME") == gitops.BOT_NAME
    assert seen.get("GIT_COMMITTER_EMAIL") == gitops.BOT_EMAIL


# ── #1184: the same wiring, for the rooted-write guard's own env facts ───
#
# `_child_git_pin` closes the *git* half of the hazard; `BRR_HOST_ROOT` /
# `BRR_WORK_TREE` arm the `pre-tool` hook's other half
# (`hooks._rooted_write_neutral`) with the two facts a hook subprocess cannot
# otherwise derive: `BRR_HOST_ROOT` because cwd and `-C` are exactly what the
# git pin outranks, and `BRR_WORK_TREE` because `cli.main()`'s own
# `_drop_inherited_git_pin` strips the raw `GIT_WORK_TREE` this env dict
# carries before any `brnrd hook <phase>` subprocess can read it — so a
# `BRR_`-namespaced copy is the only way the value survives that scrub.
# Driven end to end for the same reason the pin's own wiring tests are: a
# guard's logic can be perfect and unwired, and this repo's own lesson is
# that counting call sites overcounts coverage.


def test_host_root_reaches_a_strands_environment(tmp_path, monkeypatch):
    task, seen, run_root = _drive_run_worker(tmp_path, monkeypatch, strand=True)
    assert task.meta.get("strand") is True
    assert seen.get("BRR_HOST_ROOT") == str(tmp_path)
    assert seen.get("BRR_WORK_TREE") == str(run_root)


def test_no_host_root_reaches_a_non_strand_runs_environment(tmp_path, monkeypatch):
    _task, seen, _run_root = _drive_run_worker(tmp_path, monkeypatch, strand=False)
    assert "BRR_HOST_ROOT" not in seen
    assert "BRR_WORK_TREE" not in seen


def test_the_host_baseline_is_recorded_by_the_dispatch_path(tmp_path, monkeypatch):
    """Half 2's dispatch-time arm, wired rather than called directly."""
    task, _seen, _run_root = _drive_run_worker(tmp_path, monkeypatch, strand=True)
    assert task.meta.get("host_head_at_dispatch")
    assert "host_dirty_at_dispatch" in task.meta


def test_finalize_reports_a_stranded_deliverable_end_to_end(tmp_path, monkeypatch):
    """The whole containment, from dispatch to verdict, with no direct call.

    A strand whose cwd drifted writes its deliverable into the execution root
    and commits nothing. `_run_worker_and_finalize` must reach a verdict on
    `task.meta` — otherwise the check is a function with no caller, which is
    the shape a guard has when it was wired and then quietly unwired.
    """
    def drift(repo_root, _run_root):
        (repo_root / "stranded-deliverable.md").write_text(
            "262 insertions\n", encoding="utf-8",
        )

    task, _seen, _run_root = _drive_run_worker(
        tmp_path, monkeypatch, strand=True, during=drift, finalize=True,
    )
    assert task.meta.get("stray_host_write") == "stranded-worktree"
    detail = json.loads(task.meta["stray_host_write_detail"])
    assert "stranded-deliverable.md" in detail["stranded_paths"]


def test_finalize_stays_silent_when_the_strand_behaved(tmp_path, monkeypatch):
    """The positive control's counterpart: no verdict key at all when clean.

    Absent stays absent — never a "clean" or a False that a reader has to
    tell apart from "never checked".
    """
    def behave(_repo_root, run_root):
        (run_root / "deliverable.md").write_text("real work\n", encoding="utf-8")
        _git(run_root, "add", "-A")
        _git(run_root, "commit", "-m", "feat: work, in the right tree")

    task, _seen, _run_root = _drive_run_worker(
        tmp_path, monkeypatch, strand=True, during=behave, finalize=True,
    )
    assert "stray_host_write" not in task.meta


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
    env = _pinned_env(_strand(run_root), run_root)

    blinded = _git(other, "rev-parse", "--show-toplevel", env=env)
    assert blinded.stdout.strip() == str(run_root)  # not `other`

    # The escape `prompts/strand.md` hands the strand.
    escaped_env = {
        k: v for k, v in env.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")
    }
    escaped = _git(other, "rev-parse", "--show-toplevel", env=escaped_env)
    assert escaped.stdout.strip() == str(other.resolve())


def test_brnrd_own_git_reads_survive_an_inherited_pin(trees, monkeypatch):
    """brnrd names the repository it means, so the pin must not outrank it.

    Without `gitops.explicit_repo_env`, every ref read brnrd makes from inside
    a pinned strand — including the stray-write check below — would report the
    strand's worktree while naming the host checkout. The check would then be
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
    for var, value in daemon._child_git_pin(_strand(run_root), run_root).items():
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
    task = _strand(run_root, env=env, run_id=run_id)
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
    task = _strand(run_root)  # never dispatched through _record_host_baseline
    _commit_in_host_as(host, task.id)
    assert daemon._stray_host_write(task, host) is None


# ── #1309 item 2: host_start_oid must not silently stay unstamped ────


def test_stamp_host_start_oid_retries_a_transient_rev_parse_failure(
    trees, monkeypatch,
):
    """A momentary git hiccup at dispatch must not cost the run its baseline.

    ``gitops.rev_parse`` answers "HEAD does not resolve" and "git could not
    run right now" with the same ``None`` — a single failed attempt must not
    be read as the former.
    """
    host, _run_root = trees
    task = _strand(host, run_id="run-hso-retry")
    real_rev_parse = gitops.rev_parse
    calls = {"n": 0}

    def flaky(repo_root, ref):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # the one transient failure
        return real_rev_parse(repo_root, ref)

    monkeypatch.setattr(gitops, "rev_parse", flaky)
    daemon._stamp_host_start_oid(task, host, retries=3, delay=0)

    assert calls["n"] == 2, "must retry rather than accept the first failure"
    assert task.meta["host_start_oid"] == _head(host)


def test_stamp_host_start_oid_flags_a_persistent_failure_instead_of_silence(
    trees, monkeypatch, capsys,
):
    """Every attempt exhausted ⇒ loudly flagged, never a bare unstamped key.

    Pre-#1309 this stayed silent: the meta key was simply absent, which
    ``relics.collection_scope`` cannot tell apart from "this run legitimately
    has no host baseline" — its branchless fallback then seeds from
    ``default_branch``, and for a host run on the default branch that empty
    diff silently drops the run's own commits from its produce list.
    """
    host, _run_root = trees
    task = _strand(host, run_id="run-hso-fail")

    monkeypatch.setattr(gitops, "rev_parse", lambda *_a, **_k: None)
    daemon._stamp_host_start_oid(task, host, retries=2, delay=0)

    assert "host_start_oid" not in task.meta
    out = capsys.readouterr().out
    assert "run-hso-fail" in out
    assert "host_start_oid" in out


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


# ── the reporting surface: the finding has to reach a reader ─────────
#
# #703 exists because "nothing refused it and nothing reported it". A verdict
# that only ever lands on `task.meta` reproduces the second clause, so the
# block that writes it into the parent's own thread is pinned here. Measured:
# without these, reverting the whole notify block reds nothing.


def _spawn_child(*, stray=None, detail=None, status="done", body="spec\n"):
    meta = {
        "spawn_parent_run_id": "run-parent",
        "spawn_parent_conversation_key": "telegram:42:",
        "publish_branch": "brr/child",
        "trace_dirs": "/tmp/trace",
    }
    if stray:
        meta["stray_host_write"] = stray
        meta["stray_host_write_detail"] = json.dumps(detail or {})
    return Run(
        id="run-child", event_id="evt-child", body=body,
        source="telegram", status=status, meta=meta,
    )


def _note(tmp_path, task):
    from brr import protocol

    inbox = tmp_path / ".brr" / "inbox"
    daemon._notify_spawn_parent(inbox, task)
    return protocol.list_pending(inbox)[0]


def test_notify_indicts_an_attributed_stray_commit(tmp_path):
    note = _note(tmp_path, _spawn_child(
        stray="stray-commit",
        detail={"stray_commits": ["abc123def4567890", "0011223344556677"]},
    ))
    assert note["spawn_stray_host_write"] == "stray-commit"
    assert "status=stray-host-commit" in note["body"]
    assert "STRAY WRITE" in note["body"]
    assert "abc123def456" in note["body"]


def test_notify_reports_stranded_work(tmp_path):
    note = _note(tmp_path, _spawn_child(
        stray="stranded-worktree",
        detail={"stranded_paths": ["docs/sub-processors.md"]},
    ))
    assert note["spawn_stray_host_write"] == "stranded-worktree"
    assert "status=stray-host-worktree" in note["body"]
    assert "STRANDED WORK" in note["body"]
    assert "docs/sub-processors.md" in note["body"]


def test_notify_keeps_an_unattributed_head_move_an_advisory(tmp_path):
    """The maintainer's original conjunction, reported without accusing.

    Status must stay `done`: a sibling run or a human commit reaches this arm,
    and a guard that indicts for a non-reason is one readers learn to skip.
    """
    note = _note(tmp_path, _spawn_child(
        stray="host-head-moved",
        detail={"host_head_at_dispatch": "aaaaaaaaaaaa1111",
                "host_head_now": "bbbbbbbbbbbb2222"},
    ))
    assert "status=done" in note["body"]
    assert "advisory" in note["body"]
    assert "aaaaaaaaaaaa" in note["body"] and "bbbbbbbbbbbb" in note["body"]
    assert "STRAY WRITE" not in note["body"]


def test_notify_says_nothing_when_there_is_nothing_stray(tmp_path):
    note = _note(tmp_path, _spawn_child())
    assert "spawn_stray_host_write" not in note
    assert "STRAY WRITE" not in note["body"]
    assert "STRANDED WORK" not in note["body"]


def test_a_runner_that_never_ran_is_not_accused_of_a_stray_write(tmp_path):
    """#633's rule, preserved: no strand, nothing to have written."""
    task = _spawn_child(stray="stray-commit", detail={"stray_commits": ["a"]},
                        status="error")
    task.meta.pop("trace_dirs")  # no transcript ⇒ the Shell never gave a turn
    note = _note(tmp_path, task)
    assert "status=runner-failed" in note["body"]
    assert "STRAY WRITE" not in note["body"]
    assert "spawn_stray_host_write" not in note


def test_a_malformed_detail_blob_does_not_break_the_note(tmp_path):
    task = _spawn_child(stray="stranded-worktree")
    task.meta["stray_host_write_detail"] = "{not json"
    note = _note(tmp_path, task)
    assert "STRANDED WORK" in note["body"]


def test_the_verdict_reaches_the_run_state_doc(tmp_path):
    """The only surface a *non-spawn* worktree run has.

    A scheduled or user-addressed run has no parent thread to notify, so
    without this line the check's sole reader would be the daemon's own
    uncaptured stdout — which is how the original incident went unreported.
    """
    from brr import account

    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = account.resolve_context(repo, {"home.path": str(tmp_path / "home")})
    task = Run(
        id="run-260725-0000-aaaa", event_id="evt-x", body="b", env="worktree",
        meta={"stray_host_write": "stranded-worktree"},
    )
    doc = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="finished", cfg={},
    )
    assert doc is not None
    assert "stray_host_write: stranded-worktree" in doc.read_text(encoding="utf-8")


def test_a_rename_reports_its_destination(trees):
    host, run_root = trees
    task = _dispatched(host, run_root)
    _git(host, "mv", "README.md", "READENGINE.md")
    verdict = daemon._stray_host_write(task, host)
    assert verdict["kind"] == "stranded-worktree"
    assert "READENGINE.md" in verdict["stranded_paths"]


# ── half 3: the suite itself inherits the pin (#746) ─────────────────
#
# #703 pinned `GIT_DIR`/`GIT_WORK_TREE` into a strand's environment on
# purpose. Every subprocess that strand starts inherits them — including
# `pytest`, and *this* suite does `git init` and `git config` at ~319 call
# sites. Under the inherited pin those writes leave the tmpdir they name and
# land in the shared host checkout's common git dir: a `[user]` section and
# an `init` commit in the maintainer's live repository, and separately a
# `core.worktree` write that repointed it for fifteen minutes while every
# command exited 0.
#
# A git worktree isolates files. It does not isolate `.git/config`, which
# the main checkout and every linked worktree share. That is the whole bug.


def _inner_pytest(
    tmp_path, decoy: Path, body: str, *, extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run *body* as a pytest under `tests/conftest.py`, pinned at *decoy*.

    A subprocess and not `monkeypatch`, because the property under test is
    about a pin that exists *before the fixture runs*. Setting the variables
    inside a test body proves nothing: the autouse fixture already ran and
    already dropped them. The only faithful shape is a fresh interpreter
    whose environment carries the pin at startup, exactly as a strand's
    pytest does.

    The real `tests/conftest.py` is copied rather than imported so the inner
    run exercises the shipped fixture and would notice it being deleted.

    ``extra_env`` layers on top of the discovery pin every caller gets by
    default — the identity-half tests (#1264) use it to add a bot-shaped
    `GIT_AUTHOR_*`/`GIT_COMMITTER_*` pin alongside `GIT_DIR`/`GIT_WORK_TREE`,
    exactly as `daemon.py`'s worker env sets both together for a real strand.
    """
    import shutil
    import sys

    inner = tmp_path / "inner"
    inner.mkdir()
    shutil.copy(Path(__file__).parent / "conftest.py", inner / "conftest.py")
    (inner / "test_inner.py").write_text(body, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_DIR": str(decoy / ".git"),
        "GIT_WORK_TREE": str(decoy),
        **(extra_env or {}),
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(inner)],
        cwd=inner, env=env, capture_output=True, text=True, check=False,
    )


_INNER_WRITES_GIT = '''
import subprocess


def test_inner_git_writes_land_where_the_test_names_them(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sandbox, check=True)
    subprocess.run(
        ["git", "config", "user.email", "inner-suite@brr.invalid"],
        cwd=sandbox, check=True,
    )
    written = (sandbox / ".git" / "config").read_text(encoding="utf-8")
    assert "inner-suite@brr.invalid" in written
'''


def test_the_suite_cannot_write_into_a_repo_the_inherited_pin_names(tmp_path):
    """The load-bearing one. Adversarial, behavioural, and mutant-measured.

    A decoy repository stands in for the maintainer's checkout — built here,
    never pointed at a real one, because a test that can damage a live tree
    when it regresses is not a guard, it is the bug with a green tick.

    Two assertions, and both matter. The decoy's config is untouched: the
    #746 damage was `[user] Test/test@example.com` appearing in the shared
    config, and nothing but reading that file afterwards can prove it did
    not. And the inner run *passed*: under the pin the test's own tmpdir
    assertion fails too, so a suite that merely errored out would look
    identical to one that was correctly contained.

    Reverting the `delenv` in `tests/conftest.py` turns both red.
    """
    decoy = tmp_path / "decoy"
    init_git_repo(decoy)
    decoy_config = decoy / ".git" / "config"
    before = decoy_config.read_text(encoding="utf-8")

    result = _inner_pytest(tmp_path, decoy, _INNER_WRITES_GIT)

    after = decoy_config.read_text(encoding="utf-8")
    assert "inner-suite@brr.invalid" not in after, (
        "the inner suite's `git config` reached the decoy repository — "
        "the pin was inherited past the fixture"
    )
    assert after == before, f"decoy config mutated:\n{before!r}\n->\n{after!r}"
    assert result.returncode == 0, result.stdout + result.stderr


_INNER_READS_ENV = '''
import os

from brr import gitops


def test_inner_sees_no_discovery_override(tmp_path):
    for var in gitops.DISCOVERY_OVERRIDE_VARS:
        assert var not in os.environ, f"{var} survived the fixture"
'''


def test_the_fixture_drops_the_pin_by_the_time_a_test_body_runs(tmp_path):
    """The direct reading, under a real inherited pin.

    Cheap canary for the behavioural test above: when that one fails, this
    says whether the cause is the fixture or the git plumbing.
    """
    decoy = tmp_path / "decoy"
    init_git_repo(decoy)
    result = _inner_pytest(tmp_path, decoy, _INNER_READS_ENV)
    assert result.returncode == 0, result.stdout + result.stderr


# ── half 3b: the identity pin doesn't survive into a fixture's own commits
# (#1264) ─────────────────────────────────────────────────────────────────
#
# Same shape as half 3 above, one axis over: `GIT_AUTHOR_*`/`GIT_COMMITTER_*`
# instead of `GIT_DIR`/`GIT_WORK_TREE`. `daemon.py` pins both pairs together
# for every strand run, so a fixture that only guards the discovery pair
# still lets a strand's bot identity silently win a fixture's own
# `-c user.email=` — exactly what broke
# `test_derive_auto_squash_merge_requires_github_committer` in
# `test_relics.py` before this fix.

_INNER_WRITES_A_COMMIT = '''
import subprocess


def test_inner_commit_lands_with_the_identity_the_test_named(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sandbox, check=True)
    (sandbox / "f.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=sandbox, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Inner Suite",
         "-c", "user.email=inner-suite@brr.invalid",
         "commit", "-q", "-m", "inner commit"],
        cwd=sandbox, check=True,
    )
    author = subprocess.run(
        ["git", "log", "-1", "--format=%ae"], cwd=sandbox,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert author == "inner-suite@brr.invalid", author
'''


def test_the_suite_commits_with_the_identity_the_fixture_names_not_the_strand(
    tmp_path,
):
    """The load-bearing identity test, mirroring the discovery one above.

    A strand-shaped ambient pin (`GIT_DIR`/`GIT_WORK_TREE` *and*
    `GIT_AUTHOR_*`/`GIT_COMMITTER_*`, exactly as `daemon.py` sets both for a
    real strand run) sits in the inner interpreter's environment at
    startup. The inner suite's own `-c user.email=` must still win: if
    `_hermetic_git_env` only scrubbed the discovery pair, this reds out
    with the bot's email instead.

    Reverting the identity half of the `delenv` in `tests/conftest.py`
    turns this red.
    """
    decoy = tmp_path / "decoy"
    init_git_repo(decoy)
    result = _inner_pytest(
        tmp_path, decoy, _INNER_WRITES_A_COMMIT,
        extra_env={
            "GIT_AUTHOR_NAME": gitops.BOT_NAME,
            "GIT_AUTHOR_EMAIL": gitops.BOT_EMAIL,
            "GIT_COMMITTER_NAME": gitops.BOT_NAME,
            "GIT_COMMITTER_EMAIL": gitops.BOT_EMAIL,
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_this_very_suite_runs_without_a_discovery_override():
    """Same fact, asserted in-process — free, and it fails first."""
    for var in gitops.DISCOVERY_OVERRIDE_VARS:
        assert var not in os.environ


def test_this_very_suite_runs_without_an_identity_override():
    """The identity half of #1264, asserted in-process the same way.

    A strand's pinned `GIT_AUTHOR_*`/`GIT_COMMITTER_*` (#1135/#1251) must
    not survive into this suite's own `os.environ` any more than the
    discovery pair above does — otherwise a fixture's `-c user.email=`
    silently loses to the inherited identity (see
    `test_derive_auto_squash_merge_requires_github_committer` in
    `test_relics.py`, which is exactly that failure mode).
    """
    for var in gitops.IDENTITY_OVERRIDE_VARS:
        assert var not in os.environ


def test_the_fixture_sources_the_names_from_gitops():
    """One list of each of these variables in the project, not a fourth copy.

    `gitops.DISCOVERY_OVERRIDE_VARS`, `gitops.explicit_repo_env` and
    `cli._drop_inherited_git_pin` already state the discovery pair;
    `gitops.IDENTITY_OVERRIDE_VARS` and `gitops.bot_identity_env` state the
    identity four (#1264). #723 is the class where the copies drift apart
    and one of them keeps being right.
    """
    source = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    assert "gitops.DISCOVERY_OVERRIDE_VARS" in source
    assert '"GIT_DIR"' not in source and "'GIT_DIR'" not in source
    assert "gitops.IDENTITY_OVERRIDE_VARS" in source
    assert '"GIT_AUTHOR_EMAIL"' not in source and "'GIT_AUTHOR_EMAIL'" not in source


# ── half 4: publish refuses to ship from a repointed tree (#746) ─────
#
# `core.worktree` in the *common* git dir repoints the checkout for every
# command. In the incident the parent's `add`/`commit`/`push`/`gh pr create`
# all exited 0 on the child's content, and CI certified it. Nothing objects;
# the only reading that disagrees is `rev-parse --show-toplevel`, and only if
# something asks.


@pytest.fixture
def publishable(tmp_path):
    """A host checkout with a commit on `brr/child` and a bare origin."""
    host = tmp_path / "host"
    init_git_repo(host)
    (host / "README.md").write_text("host\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-m", "host: base")
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(host, "remote", "add", "origin", str(remote))
    _git(host, "push", "-q", "-u", "origin", "main")
    _git(host, "switch", "-q", "-c", "brr/child")
    (host / "work.md").write_text("child work\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-m", "feat: the child's work")
    return host, remote


def _publish_run(**meta):
    return Run(
        id="run-746-child", event_id="evt-746", body="spec", env="worktree",
        status="done", meta={"publish_branch": "brr/child", **meta},
    )


def _remote_branches(remote: Path) -> set[str]:
    out = _git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout
    return set(out.split())


def _repoint(host: Path, elsewhere: Path) -> None:
    """Write `core.worktree` into the shared config — the #746 mutation."""
    elsewhere.mkdir(parents=True, exist_ok=True)
    _git(host, "config", "core.worktree", str(elsewhere))


def test_publish_ships_when_the_tree_is_the_one_it_thinks_it_is(publishable):
    """Positive control. Without it the refusal could be unconditional."""
    host, remote = publishable
    task = _publish_run()
    daemon.publish(host, task)
    assert "brr/child" in _remote_branches(remote)
    assert task.meta.get("stray_host_write") is None
    assert task.meta.get("publish_status") != "conflict"


def test_publish_refuses_when_core_worktree_repoints_the_checkout(
    publishable, tmp_path, capsys,
):
    """The incident, at the seam that would have made it loud."""
    host, remote = publishable
    _repoint(host, tmp_path / "someone-elses-worktree")

    task = _publish_run()
    daemon.publish(host, task)

    assert "brr/child" not in _remote_branches(remote), (
        "published from a checkout git no longer agrees is this one"
    )
    assert task.meta["stray_host_write"] == "publish-tree-mismatch"
    assert task.meta["publish_status"] == "conflict"
    detail = json.loads(task.meta["stray_host_write_detail"])
    assert detail["seen"].endswith("someone-elses-worktree")
    assert detail["lane"] == "publish"
    assert "REFUSING publish" in capsys.readouterr().out


def test_the_refusal_reaches_the_run_state_doc(publishable, tmp_path):
    """The surface, not the return value — a non-spawn run's only reader.

    #726's post-mortem was five correct guards nobody could prove were
    wired. `publish` returns `None` on every path, so asserting the meta key
    alone would prove nothing a reader ever sees.
    """
    from brr import account

    host, _remote = publishable
    _repoint(host, tmp_path / "elsewhere")
    task = _publish_run()
    daemon.publish(host, task)

    ctx = account.resolve_context(host, {"home.path": str(tmp_path / "home")})
    doc = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="finished", cfg={},
    )
    assert doc is not None
    assert "stray_host_write: publish-tree-mismatch" in doc.read_text(encoding="utf-8")


def test_the_refusal_reaches_a_spawning_parents_thread(publishable, tmp_path):
    """The other reader: a strand's parent, in its own conversation."""
    host, _remote = publishable
    _repoint(host, tmp_path / "elsewhere")
    task = _publish_run(
        spawn_parent_run_id="run-parent",
        spawn_parent_conversation_key="telegram:42:",
    )
    task.source = "telegram"
    daemon.publish(host, task)

    note = _note(tmp_path, task)
    assert note["spawn_stray_host_write"] == "publish-tree-mismatch"
    assert "PUBLISH REFUSED" in note["body"]
    assert "core.worktree" in note["body"]
    assert "status=publish-refused" in note["body"]


def test_the_conflict_packet_puts_the_refusal_on_the_card(publishable, tmp_path):
    """The live surface a human is already watching.

    Not the `print` — that goes to the daemon's own stdout, which is where
    the original incident was reported to nobody for fifteen minutes.
    """
    from brr import run_progress

    host, _remote = publishable
    _repoint(host, tmp_path / "elsewhere")
    brr_dir = gitops.shared_brr_dir(host)
    task = _publish_run()
    task.conversation_key = "telegram:42:"
    daemon.publish(host, task)

    view = run_progress.project_conversation_latest(brr_dir, "telegram:42:")
    assert view is not None
    assert view.state == "failed"
    assert "conflict" in run_progress.render_text(view)


def test_default_branch_publish_refuses_from_a_repointed_tree(
    publishable, tmp_path, capsys,
):
    """The sibling lane, and the sharper one.

    `publish_default_branch` fast-forwards with `git merge --ff-only` when
    the default branch is checked out here — a merge *writes files into the
    working tree*, so under a repointed `core.worktree` it lands in another
    run's worktree rather than merely pushing the wrong ref.
    """
    host, _remote = publishable
    _git(host, "switch", "-q", "main")
    _repoint(host, tmp_path / "elsewhere")

    task = _publish_run()
    daemon.publish_default_branch(host, task)

    assert task.meta["stray_host_write"] == "publish-tree-mismatch"
    detail = json.loads(task.meta["stray_host_write_detail"])
    assert detail["lane"] == "default-branch publish"
    assert "REFUSING default-branch publish" in capsys.readouterr().out


def test_the_refusal_carries_the_finding_it_displaces(publishable, tmp_path):
    """A repointed tree makes the finalize check read the *other* worktree,
    so `stranded-worktree` is usually already on this key. The refusal
    outranks it — live and repairable — but does not erase it."""
    host, _remote = publishable
    _repoint(host, tmp_path / "elsewhere")
    task = _publish_run(stray_host_write="stranded-worktree")
    daemon.publish(host, task)
    detail = json.loads(task.meta["stray_host_write_detail"])
    assert detail["superseded"] == "stranded-worktree"


def test_a_tree_git_will_not_speak_for_is_not_a_confirmation(tmp_path):
    """`core.worktree` left pointing at a torn-down worktree — how the
    incident was finally noticed. Absent is not clean."""
    not_a_repo = tmp_path / "nothing"
    not_a_repo.mkdir()
    verdict = daemon._publish_tree_mismatch(not_a_repo)
    assert verdict is not None
    assert verdict["seen"] == ""


def test_an_honest_checkout_is_confirmed(publishable):
    host, _remote = publishable
    assert daemon._publish_tree_mismatch(host) is None


def test_a_linked_worktree_is_its_own_toplevel(trees):
    """Not a mismatch: the run's own worktree is legitimately its own tree,
    and a check that flagged it would fire on every worktree run."""
    _host, run_root = trees
    assert daemon._publish_tree_mismatch(run_root) is None


# ── half 5: brnrd's commits are brnrd's (#746, re-opening #475) ──────


def _idents(repo: Path, ref: str = "HEAD") -> dict[str, str]:
    out = _git(repo, "log", "-1", "--format=%an%n%ae%n%cn%n%ce", ref).stdout.split("\n")
    return {
        "author_name": out[0], "author_email": out[1],
        "committer_name": out[2], "committer_email": out[3],
    }


@pytest.fixture
def hostile_identity(monkeypatch):
    """A repo config *and* an environment both naming a human.

    Both halves are needed: `-c user.name=` beats the config and loses to
    the environment, so a test that only contaminated the config would pass
    against the weaker fix.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Arseni Lapunov")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "human@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Arseni Lapunov")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "human@example.com")


def test_a_brnrd_commit_never_inherits_an_ambient_identity(
    tmp_path, hostile_identity,
):
    """The secondary damage in #746: the strand's commit was authored *and*
    committed as the human maintainer, because identity resolution went
    through the contaminated shared config."""
    repo = tmp_path / "dominion"
    init_git_repo(repo)  # writes [user] Test User <test@example.com>
    (repo / "note.md").write_text("thought\n", encoding="utf-8")

    assert gitops.commit_all(repo, "brnrd: capture")

    idents = _idents(repo)
    assert set(idents.values()) == {gitops.BOT_NAME, gitops.BOT_EMAIL}
    assert "Arseni" not in "".join(idents.values())
    assert "Test User" not in "".join(idents.values())


def test_the_deed_founding_commit_is_brnrds(tmp_path, hostile_identity):
    """The commit that founds a *user's* repo — the one line of history
    every later reader sees first."""
    from brr import repo_deed

    repo = tmp_path / "home"
    init_git_repo(repo)
    assert repo_deed.ensure_deed(repo, "dominion")

    idents = _idents(repo)
    assert idents["author_name"] == gitops.BOT_NAME
    assert idents["committer_email"] == gitops.BOT_EMAIL


def test_an_orphan_branchs_root_commit_is_brnrds(tmp_path, hostile_identity):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / "seed.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")

    oid = gitops.create_orphan_branch(repo, "brr/dominion", message="brnrd: seed")
    assert oid

    idents = _idents(repo, "brr/dominion")
    assert set(idents.values()) == {gitops.BOT_NAME, gitops.BOT_EMAIL}


def test_the_identity_is_stated_once(tmp_path):
    """`repo_deed` used to carry its own copy of these two strings as a
    no-identity fallback. #723 is the class where such copies drift."""
    from brr import repo_deed

    assert not hasattr(repo_deed, "_FALLBACK_IDENT")
    source = Path(repo_deed.__file__).read_text(encoding="utf-8")
    assert gitops.BOT_EMAIL not in source


def test_bot_identity_env_still_scrubs_the_discovery_overrides():
    """It is `explicit_repo_env` plus identity, not instead of it — a commit
    helper that dropped the scrub would commit into the pinned worktree
    while naming another repo (#703)."""
    env = gitops.bot_identity_env({
        "GIT_DIR": "/somewhere/.git",
        "GIT_WORK_TREE": "/somewhere",
        "PATH": "/usr/bin",
    })
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["GIT_COMMITTER_NAME"] == gitops.BOT_NAME


# ── #746: a clone's own .git isolates config and stash, where a linked
# worktree never did ──────────────────────────────────────────────────
#
# The incident, twice over 2026-08-03/08-04 (#746's own comments): a
# strand's `git config user.email` write, and separately a scratch
# `git init`/`git commit`, landed in the *shared* `.git/config` — because a
# linked worktree isolates files, never config or `refs/stash`. This is the
# structural fix the issue asked for: give the child its own `.git`
# (`git clone --shared`) instead. These two tests drive the exact two
# channels the incidents used, against the real git behaviour (no mocks —
# a mock would agree with both the broken and the fixed code equally
# happily, which is exactly how the original incident hid).


def _cloned(tmp_path):
    """A host checkout with one commit, plus a #746 clone child."""
    host = tmp_path / "host"
    init_git_repo(host)
    (host / "README.md").write_text("host\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-m", "host: base")
    clone_path, branch = worktree.create_clone(host, "run-746-clone")
    return host, clone_path, branch


def test_a_clones_git_config_write_never_reaches_the_host(tmp_path):
    host, clone_path, _branch = _cloned(tmp_path)
    host_config_before = (host / ".git" / "config").read_text(encoding="utf-8")

    result = _git(clone_path, "config", "user.email", "t@t")
    assert result.returncode == 0

    # The write landed — in the clone's own config, not vanished.
    assert _git(clone_path, "config", "--get", "user.email").stdout.strip() == "t@t"
    # The host's config is byte-identical to before.
    assert (host / ".git" / "config").read_text(encoding="utf-8") == host_config_before
    assert "t@t" not in (host / ".git" / "config").read_text(encoding="utf-8")


def test_a_clones_git_stash_never_reaches_the_hosts_stash_refs(tmp_path):
    host, clone_path, _branch = _cloned(tmp_path)
    host_stash_before = _git(host, "rev-parse", "--verify", "-q", "refs/stash", check=False)
    assert host_stash_before.returncode != 0  # no stash on the host yet

    (clone_path / "README.md").write_text("clone edit\n", encoding="utf-8")
    stashed = _git(clone_path, "stash", "push", "-m", "clone work")
    assert stashed.returncode == 0
    assert "No local changes to save" not in stashed.stdout

    # The clone has its own stash entry.
    clone_stash = _git(clone_path, "rev-parse", "--verify", "-q", "refs/stash", check=False)
    assert clone_stash.returncode == 0
    # The host still has none — a linked worktree would have put it there
    # instead (refs/stash is repo-wide, not per-worktree).
    host_stash_after = _git(host, "rev-parse", "--verify", "-q", "refs/stash", check=False)
    assert host_stash_after.returncode != 0


def test_a_clone_still_resolves_the_hosts_shared_brr_dir(tmp_path):
    """The fix's own failure mode, pinned: #746 must not cost the strand its
    ability to find the shared `.brr` (outbox, `.card`, dominion access) —
    see `gitops.shared_brr_dir`'s marker-file mechanism."""
    host, clone_path, _branch = _cloned(tmp_path)
    # `create_clone` already minted `host/.brr` as `path_for`'s own parent
    # directory (the same `mkdir(parents=True, exist_ok=True)` `create` has
    # always done) — `exist_ok=True` here so the test doesn't care which.
    (host / ".brr").mkdir(exist_ok=True)
    assert gitops.shared_brr_dir(clone_path) == host / ".brr"


def test_neutered_against_a_linked_worktree_the_same_checks_go_red(tmp_path):
    """The containment tests above, re-run against the *old* shape they
    replace — proof they discriminate rather than passing vacuously.

    Not a test of production code: it drives the identical git operations
    against `worktree.create` (a linked worktree) instead of
    `worktree.create_clone`, and asserts the *pre-#746-fix* outcome — the
    write reaches the host. If this test ever goes green, the two tests
    above have stopped meaning anything.
    """
    host = tmp_path / "host"
    init_git_repo(host)
    (host / "README.md").write_text("host\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-m", "host: base")
    linked_path, _branch = worktree.create(host, "run-746-linked")

    host_config_before = (host / ".git" / "config").read_text(encoding="utf-8")
    _git(linked_path, "config", "user.email", "t@t")
    host_config_after = (host / ".git" / "config").read_text(encoding="utf-8")
    # This is the bug: a linked worktree's config write lands in the shared
    # file, so the host's config *does* change.
    assert host_config_after != host_config_before
    assert "t@t" in host_config_after
