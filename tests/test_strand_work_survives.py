"""A strand's commits survive finalization, and a bolt cannot lie about it (#1298).

Driven against real checkouts, never against a mock of ``subprocess``: the
whole defect is a *git* fact — how a bare ref name resolves inside a
``clone --shared`` — and a mocked git agrees with the pre-fix code exactly as
happily as with the post-fix code.

The layout every test in the first half builds is the one that lost three
strands' work on 2026-08-10:

    <tmp>/host                     the shared checkout, standing on `brr/parent`
                                   (a host-env parent works on its own branch)
    <tmp>/host/.brr/worktrees/…    the strand's own `git clone --shared`
    seed_ref                       the name `main` — which the clone does not
                                   carry as any resolvable ref

"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import branching, cut_verb, daemon, envs, gitops, worktree
from brr.run import Run

from _helpers import commit_files, init_git_repo


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True,
    )


@pytest.fixture
def host(tmp_path):
    """A host checkout with `main`, standing on a *different* branch."""
    repo = tmp_path / "host"
    init_git_repo(repo)
    commit_files(repo, {"README.md": "base\n"}, message="host: base")
    # The host-env parent's own working branch. This one line is the whole
    # precondition: while the host stands here, `main` is not the checked-out
    # branch, so a clone taken from it carries no local `main` at all.
    _git(repo, "checkout", "-b", "brr/parent")
    return repo


@pytest.fixture(autouse=True)
def _clean_run_controls():
    """The live-strand-handoff half (#1197) registers `_run_controls` rows —
    same module-global registry `test_daemon.py` clears; clear it here too
    so a row minted by one test can't leak into another's `owned_children`."""
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _strand(run_id="run-1298-child", *, status="done"):
    return Run(
        id=run_id, event_id=f"evt-{run_id}", body="spec",
        env="worktree", status=status, meta={"strand": True},
    )


def _prepare(host_repo, task, plan):
    return envs.get_env("worktree").prepare(
        task, host_repo, {},
        branch_plan=plan,
        response_path=host_repo / ".brr" / "responses" / "evt.md",
    )


# ── the git fact the whole defect rests on ──────────────────────────


def test_a_seed_name_does_not_resolve_inside_a_strand_clone(host):
    """The premise, measured rather than assumed.

    Git's rev-parse fallback for a bare name tries ``refs/remotes/<name>``.
    It never tries ``refs/remotes/origin/<name>``. So in a clone whose source
    was standing on some other branch, the seed name `main` resolves to
    nothing — and the old probe read git's 128 as "no commits".
    """
    clone, _branch = worktree.create_clone(host, "run-premise", base_ref="main")

    assert gitops.rev_parse(clone, "main") is None
    assert gitops.rev_parse(clone, "origin/main") is not None

    probe = _git(clone, "rev-list", "--count", "main..HEAD", check=False)
    assert probe.returncode != 0


# ── half 1: the probe refuses to answer what it cannot measure ───────


def test_has_commits_beyond_raises_rather_than_reporting_no_commits(host):
    clone, _branch = worktree.create_clone(host, "run-probe", base_ref="main")
    (clone / "work.txt").write_text("real work\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "child: real work")

    with pytest.raises(worktree.BaseUnresolvable):
        worktree.has_commits_beyond(clone, "main")


def test_has_commits_beyond_answers_from_the_pinned_oid(host):
    base = gitops.rev_parse(host, "main")
    clone, _branch = worktree.create_clone(host, "run-probe-oid", base_ref="main")

    assert worktree.has_commits_beyond(clone, "main", base_oid=base) is False

    (clone / "work.txt").write_text("real work\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "child: real work")

    assert worktree.has_commits_beyond(clone, "main", base_oid=base) is True


def test_a_resolvable_name_still_answers_without_an_oid(host):
    """The linked-worktree shape, unchanged: a shared refs db resolves names."""
    run_root = host / ".brr" / "worktrees" / "run-linked"
    run_root.parent.mkdir(parents=True, exist_ok=True)
    _git(host, "worktree", "add", "-b", "brr/linked", str(run_root), "main")

    assert worktree.has_commits_beyond(run_root, "main") is False
    (run_root / "work.txt").write_text("real work\n", encoding="utf-8")
    _git(run_root, "add", "-A")
    _git(run_root, "commit", "-m", "child: real work")
    assert worktree.has_commits_beyond(run_root, "main") is True


# ── half 2: the strand keeps its work, end to end ────────────────────


def test_plan_pins_the_seed_oid(host):
    plan = branching.resolve_publish_plan(host, {}, {})

    assert plan.seed_ref == "main"
    assert plan.seed_oid == gitops.rev_parse(host, "main")
    assert plan.meta_items()["seed_oid"] == plan.seed_oid


def test_a_strands_commits_are_published_not_deleted(host, tmp_path):
    """The incident, reproduced at the layer that destroyed the work.

    Pre-fix this finalizes ``publish_status: nothing`` with no publish branch,
    and ``remove_clone`` deletes the only object store the commits lived in.
    """
    plan = branching.resolve_publish_plan(host, {}, {})
    task = _strand()
    ctx = _prepare(host, task, plan)
    clone = Path(ctx.env_state["worktree_path"])

    (clone / "work.txt").write_text("the deliverable\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "child: the deliverable")
    # A strand renames its placeholder to the branch its contract declared.
    _git(clone, "branch", "-m", "brr/the-declared-branch")
    child_head = _git(clone, "rev-parse", "HEAD").stdout.strip()

    envs.get_env("worktree").finalize(ctx, task, tmp_path / "runs")

    assert task.meta["publish_status"] == "ready"
    assert task.meta["publish_branch"] == "brr/the-declared-branch"
    # And the commit is reachable from the host, not only from a directory
    # that is about to be removed.
    assert gitops.branch_head(host, "brr/the-declared-branch") == child_head


def test_a_parent_commit_during_the_strands_life_changes_nothing(host, tmp_path):
    """#1298's own stated repro, kept as a regression even though the theory
    it came from is not the mechanism.

    The parent moves the shared checkout's HEAD (and `main`) while the child
    works. Anchored to a pinned oid, the child's verdict is untouched by both.
    """
    plan = branching.resolve_publish_plan(host, {}, {})
    task = _strand(run_id="run-1298-concurrent")
    ctx = _prepare(host, task, plan)
    clone = Path(ctx.env_state["worktree_path"])

    (clone / "work.txt").write_text("the deliverable\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "child: the deliverable")

    # The parent commits on its own branch *and* moves the seed branch.
    commit_files(host, {"parent.txt": "parent work\n"}, message="parent: ships")
    _git(host, "branch", "-f", "main", "brr/parent")

    envs.get_env("worktree").finalize(ctx, task, tmp_path / "runs")

    assert task.meta["publish_status"] == "ready"
    assert task.meta["publish_branch"] == "brr/run-1298-concurrent"


def test_an_empty_strand_still_publishes_nothing(host, tmp_path):
    """The other error is not made either: a run that committed nothing is
    still classified ``nothing``, or the fix would just publish everything."""
    plan = branching.resolve_publish_plan(host, {}, {})
    task = _strand(run_id="run-1298-empty")
    ctx = _prepare(host, task, plan)

    envs.get_env("worktree").finalize(ctx, task, tmp_path / "runs")

    assert task.meta["publish_status"] == "nothing"
    assert "publish_branch" not in task.meta


def test_an_unresolvable_base_publishes_and_keeps_the_checkout(host, tmp_path):
    """With no base at all, "cannot tell" must not render as "empty"."""
    plan = branching.resolve_publish_plan(host, {}, {})
    blind = branching.PublishPlan(
        seed_ref="a-branch-that-does-not-exist",
        target_branch=None,
        source="test",
        host_context_branch=None,
        seed_oid=None,
    )
    task = _strand(run_id="run-1298-blind")
    ctx = _prepare(host, task, plan)
    clone = Path(ctx.env_state["worktree_path"])
    ctx.branch_plan = blind

    (clone / "work.txt").write_text("the deliverable\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "child: the deliverable")

    envs.get_env("worktree").finalize(ctx, task, tmp_path / "runs")

    assert task.meta["publish_status"] == "ready"
    assert clone.exists(), "an unprovable checkout must never be deleted"


# ── half 3: the salvage net has the same anchor ──────────────────────


def test_salvage_arms_a_publish_for_a_failed_strand(host, tmp_path):
    """``_capture_worktree`` is the net under the floor; before the pin it
    shared the floor's hole and returned early on git's refusal."""
    plan = branching.resolve_publish_plan(host, {}, {})
    task = _strand(run_id="run-1298-salvage", status="error")
    ctx = _prepare(host, task, plan)
    clone = Path(ctx.env_state["worktree_path"])

    (clone / "work.txt").write_text("in-flight work\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "child: in-flight work")

    daemon._capture_worktree(task, ctx, plan, {}, tmp_path / "runs")

    assert task.meta["has_new_commit"] is True
    assert task.meta["publish_branch"] == "brr/run-1298-salvage"


# ── half 4: the bolt cannot close over a destroyed strand ────────────


def _child_run(repo, run_id, parent_id, *, branch, status="done"):
    """Persist a strand run state doc under the parent's shared `.brr/runs`."""
    child = Run(
        id=run_id, event_id=f"evt-{run_id}", body="spec", env="worktree",
        status=status,
        meta={
            "strand": True,
            "spawn_parent_run_id": parent_id,
            **({"spawn_contract_branch": branch} if branch else {}),
        },
    )
    child.save(gitops.shared_brr_dir(repo) / "runs")
    return child


def _clean_cut():
    return cut_verb.CutDeclaration(produce="none", owed_none=True)


def _mismatches(parent, repo, declaration=None):
    return daemon._cut_mismatches(
        parent, declaration or _clean_cut(),
        pending_events=[], repo_root=repo, outbox_dir=None,
    )


def test_a_bolt_bounces_on_a_strand_whose_branch_exists_nowhere(host):
    parent = Run(id="run-1298-parent", event_id="evt-p", body="", env="host")
    _child_run(host, "run-1298-lost", parent.id, branch="brr/the-lost-work")

    found = _mismatches(parent, host)

    assert any("run-1298-lost" in m and "brr/the-lost-work" in m for m in found)
    assert any("unsalvaged" in m for m in found)


def test_a_bolt_accepts_when_the_branch_landed(host):
    parent = Run(id="run-1298-parent", event_id="evt-p", body="", env="host")
    _child_run(host, "run-1298-kept", parent.id, branch="brr/the-kept-work")
    _git(host, "branch", "brr/the-kept-work", "main")

    assert _mismatches(parent, host) == []


def test_a_bolt_accepts_a_strand_that_declared_no_branch(host):
    """Only a declared contract may indict (#640) — a review strand promised
    no branch and publishing nothing is its correct outcome."""
    parent = Run(id="run-1298-parent", event_id="evt-p", body="", env="host")
    _child_run(host, "run-1298-review", parent.id, branch=None)

    assert _mismatches(parent, host) == []


def test_a_bolt_ignores_a_strand_that_is_still_running(host):
    parent = Run(id="run-1298-parent", event_id="evt-p", body="", env="host")
    _child_run(
        host, "run-1298-inflight", parent.id,
        branch="brr/still-going", status="running",
    )

    assert _mismatches(parent, host) == []


def test_a_bolt_ignores_another_runs_strand(host):
    parent = Run(id="run-1298-parent", event_id="evt-p", body="", env="host")
    _child_run(host, "run-1298-sibling", "run-somebody-else", branch="brr/not-mine")

    assert _mismatches(parent, host) == []


def test_carrying_the_lost_strand_as_owed_is_an_honest_close(host):
    """The guard forces the *declaration*, it does not forbid the state — a
    parent that names the loss as owed closes cleanly."""
    parent = Run(id="run-1298-parent", event_id="evt-p", body="", env="host")
    _child_run(host, "run-1298-lost", parent.id, branch="brr/the-lost-work")

    declared = cut_verb.CutDeclaration(
        produce="none",
        owed_none=False,
        owed=(
            cut_verb.OwedRow(
                label="strand work",
                ref="brr/the-lost-work",
                why="the strand's clone was destroyed before it published",
                where="re-dispatch next session",
            ),
        ),
    )

    assert _mismatches(parent, host, declared) == []


def test_no_head_at_all_is_a_measurable_no(tmp_path):
    """The one exemption from the refusal, and why it is not a hedge.

    ``has_new_commit`` is read as a *delivery-satisfaction* signal as well as
    a publish one. Answering "cannot tell" for a tree with no HEAD would mark
    a runner that produced nothing at all as having produced a commit, and
    retire its retry — which is a different way to lose work, not a safer one.
    An absent HEAD makes "has commits beyond anything" plainly false; there is
    nothing here to protect.
    """
    empty = tmp_path / "not-a-repo"
    empty.mkdir()

    assert worktree.has_commits_beyond(empty, "main") is False


# ── half 5: a live strand needs a declared handoff, not just a finished
# one (#1197) ──────────────────────────────────────────────────────────
#
# Half 4 above closes the case where a dispatched child *finished* and its
# promised branch is nowhere to be found — `_stranded_strands`, driven off
# the disk-persisted `Run` records under the shared `.brr/runs`. This half
# is the sibling gap named in #1197: a bolt landing while a child is still
# *running* said nothing machine-checkable about whether the parent named
# the handoff or simply dropped the thread. That check reads a different,
# in-process registry — `_run_controls` / `_owned_child_controls` — the same
# one `hooks._live_child_handover_line` renders and `portal-state.json`'s
# `resources.coexisting_runs.owned_children` carries. The two mechanisms
# must not double-indict the same child: a live child is this half's
# question, a terminal one with no salvaged branch is half 4's.


def _register_live_child(event_id, parent_run_id, *, child_run_id=None):
    daemon._register_run_control(event_id, parent_run_id)
    if child_run_id:
        daemon._bind_run_control(event_id, child_run_id)


def test_a_bolt_bounces_on_a_live_strand_with_no_strands_row():
    parent = Run(id="run-1197-parent", event_id="evt-p", body="", env="host")
    _register_live_child("evt-1197-child", parent.id, child_run_id="run-1197-child")
    assert daemon._owned_child_controls(parent.id), (
        "fixture must register a live owned child or the check below "
        "would pass vacuously over an empty set"
    )

    found = daemon._cut_mismatches(
        parent, cut_verb.CutDeclaration(),
        pending_events=[], repo_root=None, outbox_dir=None,
    )

    assert any(
        "run-1197-child" in m and "undispositioned" in m for m in found
    )


def test_a_bolt_accepts_a_declared_handoff_for_a_live_strand():
    parent = Run(id="run-1197-parent", event_id="evt-p", body="", env="host")
    _register_live_child("evt-1197-child", parent.id, child_run_id="run-1197-child")

    declared = cut_verb.CutDeclaration(
        strands=(
            cut_verb.StrandDisposition(
                run="run-1197-child",
                disposition="handoff — the next wake on this thread converges it",
            ),
        ),
    )

    assert daemon._cut_mismatches(
        parent, declared, pending_events=[], repo_root=None, outbox_dir=None,
    ) == []


def test_a_bolt_accepts_a_strands_row_naming_a_reaped_or_stopped_child():
    """A child that is no longer live (converged, stopped, abandoned) is
    correctly declared as such. The disposition names the state directly:
    `converged` and `stopped` describe children that are by construction not
    live (reaped from _run_controls or flagged for stop), so declaring them
    honestly passes cleanly. The check forces the declaration, not the
    liveness state."""
    parent = Run(id="run-1197-parent", event_id="evt-p", body="", env="host")
    # No live children registered at all — this child finished and was reaped.

    declared = cut_verb.CutDeclaration(
        strands=(
            cut_verb.StrandDisposition(
                run="run-converged-child",
                disposition="converged — read whole, merged abc1234",
            ),
        ),
    )

    # Declaring a finished child as 'converged' is honest and passes cleanly.
    assert daemon._cut_mismatches(
        parent, declared, pending_events=[], repo_root=None, outbox_dir=None,
    ) == []


def test_a_finished_child_needs_no_strands_row_and_the_two_checks_dont_double_indict(
    host,
):
    """A terminal child with no salvaged branch is half 4's complaint
    (`_stranded_strands`, `owed: strand ... unsalvaged`) — never this half's
    (`strands: ... undispositioned`), because a run that already finished and
    was retired from `_run_controls` is no longer a *live* owned child at all."""
    parent = Run(id="run-1197-parent", event_id="evt-p", body="", env="host")
    _child_run(host, "run-1197-lost", parent.id, branch="brr/the-lost-work")
    # The finished child is never registered live — `_retire_run_control`
    # pops a real one at completion; simulating "never registered" is the
    # same end state without reaching into daemon internals for the pop.
    assert daemon._owned_child_controls(parent.id) == [], (
        "fixture must leave no live owned children or this test would not "
        "isolate half 4's complaint from half 5's"
    )

    found = daemon._cut_mismatches(
        parent, _clean_cut(), pending_events=[], repo_root=host, outbox_dir=None,
    )

    assert any("unsalvaged" in m for m in found)
    assert not any("undispositioned" in m for m in found)
    assert not any("not a live child" in m for m in found)


def test_a_bolt_ignores_a_stopped_child_control():
    """`stop:` marks a control ``stopped`` without popping it (#1197's other
    live-registry consumer, `_spawn_child_armed`, reads the same flag) — the
    strands check must honour it exactly as `_owned_child_controls` does."""
    parent = Run(id="run-1197-parent", event_id="evt-p", body="", env="host")
    _register_live_child("evt-1197-child", parent.id, child_run_id="run-1197-child")
    with daemon._run_controls_lock:
        daemon._run_controls["evt-1197-child"]["stopped"] = True
    assert daemon._owned_child_controls(parent.id) == []

    assert daemon._cut_mismatches(
        parent, cut_verb.CutDeclaration(),
        pending_events=[], repo_root=None, outbox_dir=None,
    ) == []
