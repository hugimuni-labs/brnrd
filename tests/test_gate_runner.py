"""The local gate runner must stay a *reader* of CI, never a second copy.

Every test here exists to catch one specific way this file could quietly stop
being true: a leg list that drifts, a refusal that stops being reported, a job
whose steps are silently dropped.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_PATH = REPO_ROOT / "scripts" / "gate.py"


def _gate():
    spec = importlib.util.spec_from_file_location("gate_runner_under_test", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_reads_every_run_step_of_the_real_workflow():
    """The runner's leg list is the workflow's, not a copy that can drift."""
    gate = _gate()
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    found = gate.legs(workflow)

    expected_run_steps = sum(
        1
        for job in workflow["jobs"].values()
        for step in job.get("steps") or []
        if step.get("run")
    )
    assert sum(1 for leg in found if leg["kind"] == "run") == expected_run_steps
    # Every job is represented — a whole job silently missing is the failure
    # that would make a GREEN verdict a lie.
    assert {leg["job"] for leg in found} == set(workflow["jobs"])


def test_job_level_working_directory_is_applied():
    """`defaults.run.working-directory` decides where a step runs.

    The frontend legs only pass from `src/frontend`; running them at the repo
    root fails in a way that looks like a broken test suite.
    """
    gate = _gate()
    workflow = {
        "jobs": {
            "frontend": {
                "defaults": {"run": {"working-directory": "src/frontend"}},
                "steps": [{"name": "Test", "run": "npm test"}],
            }
        }
    }
    (leg,) = [leg for leg in gate.legs(workflow) if leg["kind"] == "run"]
    assert leg["cwd"] == "src/frontend"


def test_step_level_working_directory_overrides_the_job_default():
    gate = _gate()
    workflow = {
        "jobs": {
            "mixed": {
                "defaults": {"run": {"working-directory": "src/frontend"}},
                "steps": [{"run": "pytest", "working-directory": "."}],
            }
        }
    }
    (leg,) = [leg for leg in gate.legs(workflow) if leg["kind"] == "run"]
    assert leg["cwd"] == "."


def test_uses_steps_are_classified_not_dropped():
    """A skipped step must be *counted*, so the summary cannot overclaim."""
    gate = _gate()
    workflow = {"jobs": {"j": {"steps": [{"uses": "actions/checkout@v4"}, {"run": "true"}]}}}
    kinds = [leg["kind"] for leg in gate.legs(workflow)]
    assert kinds == ["uses", "run"]


def test_editable_install_is_refused_with_a_reason():
    gate = _gate()
    reason = gate.refusal("python -m pip install -e '.[dev]'")
    assert reason is not None
    assert "#762" in reason


def test_npm_ci_is_not_refused():
    """Skipping the install is what produced two imaginary test failures.

    `npm test` without `node_modules` reported 228 pass / 2 fail against a
    suite that is 238/238 green. A refusal here would rebuild that trap.
    """
    gate = _gate()
    assert gate.refusal("npm ci") is None


def test_the_real_workflow_has_no_unrefused_editable_install():
    """If CI ever gains a second `pip install -e`, this fails loudly.

    The refusal list is a hand-maintained set, which is exactly the shape that
    meets the member nobody listed — so pin the one property that makes the
    list safe: everything CI installs is either refused with a reason or run.
    """
    gate = _gate()
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    installs = [
        leg
        for leg in gate.legs(workflow)
        if leg["kind"] == "run" and "pip install -e" in leg["command"]
    ]
    for leg in installs:
        assert gate.refusal(leg["command"]) is not None


# ── The receipt: what makes "did the gate run" a fact instead of a memory ──


def _repo(tmp_path):
    """A git repo with one commit, one tracked edit, one untracked file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "tracked.py").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "seed"], check=True, capture_output=True
    )
    (repo / "tracked.py").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "brand-new.py").write_text("never committed\n", encoding="utf-8")
    return repo


def _fake_ci(repo: Path, command: str) -> Path:
    """A one-leg workflow in *repo*, so `main()` can be driven end to end.

    The real `ci.yml` takes minutes and gates this account; these tests are
    about *when* the runner samples the tree, not about what CI contains —
    that property has its own tests above, against the real file.
    """
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "jobs:\n"
        "  backend:\n"
        "    steps:\n"
        "      - name: the one leg\n"
        f"        run: {command}\n",
        encoding="utf-8",
    )
    return workflow


def _drive(gate, monkeypatch, repo: Path, outbox: Path, argv: list[str]) -> int:
    monkeypatch.setattr(gate, "REPO_ROOT", repo)
    monkeypatch.setattr(gate, "WORKFLOW", repo / ".github" / "workflows" / "ci.yml")
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    return gate.main(argv)


def test_the_referent_rules_are_the_shipped_ones_not_a_second_copy():
    """There is one implementation of the receipt's referents, and this file
    names it rather than reimplementing it.

    This test used to assert something weaker: that `scripts/gate.py`'s own
    `untracked_digest` *agreed with* `brr.hooks`'. Agreement was the best a
    two-copy shape could offer, and #917 is what it cost — one defect in the
    sampling order, present in both copies, needing the same fix twice. So
    the copy is gone and the property pinned here is the stronger one: same
    object, not merely same output.

    The worktree hazard gets its own assertion. A bare `import brr` from a
    worktree resolves to the *host's* installed copy (AGENTS.md -> Build and
    run), which would have this script gating one tree by another tree's
    rules; `gate.py` puts `REPO_ROOT/src` at the front of `sys.path` to stop
    that, and this is what notices if the line is ever removed.
    """
    import tempfile

    from brr import gate_receipt, hooks

    gate = _gate()
    assert gate.untracked_digest is gate_receipt.untracked_digest
    assert gate.tree_referents is gate_receipt.tree_referents
    assert gate.RECEIPT_NAME == gate_receipt.RECEIPT_NAME == hooks.GATE_RECEIPT_NAME
    assert (
        Path(gate.gate_receipt.__file__).resolve()
        == (REPO_ROOT / "src" / "brr" / "gate_receipt.py").resolve()
    )

    # The rule itself still driven against a real repository, because "one
    # implementation" says nothing about whether that implementation works.
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo(Path(tmp))
        digest = hooks._untracked_digest(repo)
        assert digest != ""  # the repo really does have an untracked file

        # And it is *content*-sensitive, not merely name-sensitive — the gap
        # `status --porcelain` alone leaves open.
        (repo / "brand-new.py").write_text("edited after the gate ran\n", encoding="utf-8")
        assert hooks._untracked_digest(repo) != digest


def test_no_receipt_is_written_outside_a_run(tmp_path, monkeypatch):
    """A hand invocation in the operator's shell has no guard watching it."""
    gate = _gate()
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    assert gate.receipt_path() is None
    assert gate.write_receipt("GREEN", []) is None


def test_receipt_lands_in_the_outbox_and_names_the_verdict(tmp_path, monkeypatch):
    """Under a run it is written beside the other control dotfiles — never
    delivered to chat (the drain skips dotfiles), read by the closeout guard.

    The file is a map keyed per tree (#820); `gate.REPO_ROOT` (unpatched here,
    so this repo's own checkout) is looked up like any reader would.
    """
    from brr import gate_receipt

    gate = _gate()
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(tmp_path))
    path = gate.write_receipt("RED", [("backend: Run tests", "FAIL rc=1", 12.34)])
    assert path == tmp_path / gate.RECEIPT_NAME
    entry = gate_receipt.read_receipt(tmp_path, gate.REPO_ROOT)
    # RED is recorded, not suppressed: the obligation a reader checks is that
    # the gate *ran* on this tree, never that it was green.
    assert entry["verdict"] == "RED"
    assert entry["legs"][0]["verdict"] == "FAIL rc=1"
    assert set(entry) >= {"head", "status", "diff_digest", "untracked_digest"}


# ── #917: the receipt has to be about the tree the *legs* saw ─────────────


def test_a_file_written_while_the_legs_run_is_recorded_as_a_moved_tree(
    tmp_path, monkeypatch
):
    """The defect, driven: a leg loop that takes four minutes, a file written
    during it, and a receipt that used to certify that file as gated because
    both samples were taken after the last leg finished.

    `run-260731-1303-6mcr` is the real instance — two `deploy/` files created
    while the backend leg was running, both named in that run's receipt,
    neither ever gated. Markdown that time; the mechanism cannot tell markdown
    from a source file.
    """
    from brr import gate_receipt

    gate = _gate()
    repo = _repo(tmp_path)
    _fake_ci(repo, "printf 'no leg ever saw this\\n' > written-mid-gate.py")
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    assert _drive(gate, monkeypatch, repo, outbox, []) == 0
    payload = json.loads((outbox / gate.RECEIPT_NAME).read_text(encoding="utf-8"))
    payload = payload[gate_receipt.tree_key(repo)]

    assert payload["tree_moved_during_gate"] is True
    # The pair, not a boolean — which is what lets a reader name the file.
    assert payload["gated_from"]["status"] != payload["status"]
    assert "written-mid-gate.py" not in payload["gated_from"]["status"]
    assert "written-mid-gate.py" in payload["status"]
    assert "status" in payload["moved_referents"]
    assert gate_receipt.moved_paths(payload) == ["written-mid-gate.py"]
    assert gate_receipt.moved_sentence(payload) == (
        "written-mid-gate.py changed while the gate was running, so no leg "
        "ever saw it"
    )


def test_a_still_tree_records_that_it_held_still(tmp_path, monkeypatch):
    """The honest path, and the reason the state is not a bare boolean-or-
    absent: `false` here means *a writer checked and the tree held*, which is
    a different claim from a receipt too old to have looked."""
    from brr import gate_receipt

    gate = _gate()
    repo = _repo(tmp_path)
    # Not `true` — YAML coerces that to a bool and the leg stops being a
    # string long before it reaches a shell.
    _fake_ci(repo, "exit 0")
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    assert _drive(gate, monkeypatch, repo, outbox, []) == 0
    payload = json.loads((outbox / gate.RECEIPT_NAME).read_text(encoding="utf-8"))
    payload = payload[gate_receipt.tree_key(repo)]

    assert payload["tree_moved_during_gate"] is False
    assert "moved_referents" not in payload
    assert payload["gated_from"]["status"] == payload["status"]
    assert payload["verdict"] == "GREEN"


def test_list_stays_a_no_op_that_writes_and_samples_nothing(tmp_path, monkeypatch):
    """"Before the first leg" means before the leg *loop*, not before argument
    parsing. `--list` returns above the captures, so it neither runs anything
    nor leaves a receipt claiming it did."""
    gate = _gate()
    repo = _repo(tmp_path)
    _fake_ci(repo, "printf 'x\\n' > must-not-exist.py")
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    assert _drive(gate, monkeypatch, repo, outbox, ["--list"]) == 0
    assert not (outbox / gate.RECEIPT_NAME).exists()
    assert not (repo / "must-not-exist.py").exists()


def test_a_red_leg_that_also_moved_the_tree_records_both(tmp_path, monkeypatch):
    """Two independent facts, and neither suppresses the other: the verdict is
    about what the legs returned, the stillness record is about what the tree
    did underneath them."""
    from brr import gate_receipt

    gate = _gate()
    repo = _repo(tmp_path)
    _fake_ci(repo, "printf 'x\\n' > written-mid-gate.py; exit 1")
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    assert _drive(gate, monkeypatch, repo, outbox, []) == 1
    payload = json.loads((outbox / gate.RECEIPT_NAME).read_text(encoding="utf-8"))
    payload = payload[gate_receipt.tree_key(repo)]
    assert payload["verdict"] == "RED"
    assert payload["tree_moved_during_gate"] is True


def test_two_trees_gated_via_the_real_runner_both_survive(tmp_path, monkeypatch):
    """#820 through the real entry point, not just the writer underneath it:
    two separate `python scripts/gate.py` invocations against two different
    trees, one `BRR_OUTBOX_DIR` — this repo's own documented `host` pattern
    of gating a scratch worktree and then the checkout. The second run must
    not destroy the first run's receipt."""
    from brr import gate_receipt

    gate = _gate()
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = _repo(tmp_path / "first")
    second = _repo(tmp_path / "second")
    _fake_ci(first, "exit 0")
    _fake_ci(second, "exit 1")
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    assert _drive(gate, monkeypatch, first, outbox, []) == 0
    assert _drive(gate, monkeypatch, second, outbox, []) == 1

    data = json.loads((outbox / gate.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert data[gate_receipt.tree_key(first)]["verdict"] == "GREEN"
    assert data[gate_receipt.tree_key(second)]["verdict"] == "RED"


# ── #1195 rec 1 + rec 4: one gate at a time, and say so while queued ──────


def test_two_concurrent_gate_runs_on_one_tree_serialize(tmp_path):
    """The bug, driven for real: two genuinely separate `python
    scripts/gate.py` processes gating the *same* tree, launched together.

    Each leg spends ~0.5s inside a marked critical section. Unlocked, both
    processes' legs run at once and the section sees 2 processes present at
    once — the shape #1195 measured as "five strands, five concurrent full
    suites, CPU/IO thrashing". Locked, the second process's leg cannot start
    until the first finishes and releases, so neither leg ever observes more
    than 1 process in the section.

    Out-of-process on purpose, not `gate.main()` called twice from two
    threads of one test process: `fcntl.flock` is scoped to a real
    process's own open file description, and rec 4's "name the holder"
    ('s PID) means nothing if both sides share one PID. This test is what
    was run, unlocked, to confirm it fails before the lock landed — see
    `/tmp/brr-gate-lock-report.md` for the captured red run.
    """
    repo = _repo(tmp_path)
    markers = tmp_path / "markers"
    markers.mkdir()
    max_seen_path = tmp_path / "max_seen.txt"

    # A leg that marks its own presence, sleeps, records the highest
    # concurrent-marker count it observed (itself flock-guarded — a
    # deliberately different lock file from the one under test, so this
    # detector's own correctness does not depend on the thing it measures),
    # then clears its marker.
    leg = (
        "python3 -c \""
        "import os, time, fcntl, pathlib;"
        f"markers = pathlib.Path(r'{markers}');"
        "mine = markers / str(os.getpid());"
        "mine.write_text('1');"
        "time.sleep(0.5);"
        "seen = len(list(markers.iterdir()));"
        f"maxp = pathlib.Path(r'{max_seen_path}');"
        "f = open(maxp, 'a+');"
        "fcntl.flock(f, fcntl.LOCK_EX);"
        "f.seek(0);"
        "prev = f.read().strip();"
        "prevn = int(prev) if prev else 0;"
        "f.seek(0); f.truncate(); f.write(str(max(prevn, seen)));"
        "fcntl.flock(f, fcntl.LOCK_UN); f.close();"
        "mine.unlink()"
        "\""
    )
    _fake_ci(repo, leg)

    # Same trick `_gate()` already uses (spec_from_file_location on the
    # *real* GATE_PATH), just wrapped for a subprocess: `sys.path` gets the
    # real `REPO_ROOT/src` at import time, so `from brr import ...` resolves
    # correctly even though REPO_ROOT is monkeypatched to the fake repo
    # afterward, before `main()` runs.
    bootstrap = (
        "import importlib.util, pathlib, sys\n"
        f"spec = importlib.util.spec_from_file_location('g', r'{GATE_PATH}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        f"m.REPO_ROOT = pathlib.Path(r'{repo}')\n"
        f"m.WORKFLOW = pathlib.Path(r'{repo}') / '.github' / 'workflows' / 'ci.yml'\n"
        "sys.exit(m.main([]))\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", bootstrap],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(2)
    ]
    outs = [p.communicate(timeout=30) for p in procs]
    for p, (out, err) in zip(procs, outs):
        assert p.returncode == 0, f"stdout:\n{out}\nstderr:\n{err}"

    assert max_seen_path.exists(), "neither leg ever ran"
    assert int(max_seen_path.read_text().strip()) == 1, (
        "two gate.py runs on the same tree overlapped inside the leg — "
        "the lock did not serialize them"
    )


# ── #1195 rec 2: --changed-only skips legs the diff provably cannot touch ──

_CI_LIKE_WORKFLOW = {
    "jobs": {
        "backend": {"steps": [{"run": "python -m pytest -q"}]},
        "frontend": {
            "defaults": {"run": {"working-directory": "src/frontend"}},
            "steps": [{"run": "npm test"}],
        },
        "launcher": {
            "defaults": {"run": {"working-directory": "packaging/npm"}},
            "steps": [{"run": "npm pack --dry-run"}],
        },
    }
}


def test_jobs_to_run_skips_frontend_when_only_backend_files_changed():
    gate = _gate()
    run_jobs = gate.jobs_to_run(_CI_LIKE_WORKFLOW, ["src/brr/daemon.py"])
    assert run_jobs == {"backend"}


def test_jobs_to_run_never_skips_the_catch_all_job_on_an_all_frontend_diff():
    """The issue's own phrasing suggests this direction too ("skip the
    frontend leg... and vice versa"), but it is not safe in *this* repo and
    is deliberately not implemented: `tests/test_spa_serving.py`,
    `tests/test_brnrd_legal_pinning.py`, and `tests/test_privacy_notice.py`
    (all backend-job pytest files) read real content out of
    `src/frontend/src/routes/` and `src/frontend/src/lib/`, and
    `tests/test_npm_launcher.py` executes the real
    `packaging/npm/bin/brnrd.js`. A job with no `working-directory` of its
    own (backend) is therefore never dropped by inference, regardless of how
    exclusively the diff looks confined to another job's tree — see the
    report for the concrete evidence this was not a guess."""
    gate = _gate()
    run_jobs = gate.jobs_to_run(_CI_LIKE_WORKFLOW, ["src/frontend/src/lib/foo.ts"])
    assert run_jobs == {"backend", "frontend"}


def test_jobs_to_run_never_skips_the_catch_all_job_on_an_all_launcher_diff():
    """Same reasoning, the launcher direction: `tests/test_npm_launcher.py`
    reads `packaging/npm/uv-assets.json` and runs `packaging/npm/bin/brnrd.js`
    for real, so backend cannot be ruled out for a launcher-only diff either."""
    gate = _gate()
    run_jobs = gate.jobs_to_run(_CI_LIKE_WORKFLOW, ["packaging/npm/bin/brnrd.js"])
    assert run_jobs == {"backend", "launcher"}


def test_jobs_to_run_unions_jobs_for_a_mixed_diff():
    gate = _gate()
    run_jobs = gate.jobs_to_run(
        _CI_LIKE_WORKFLOW, ["src/brr/daemon.py", "src/frontend/src/lib/foo.ts"]
    )
    assert run_jobs == {"backend", "frontend"}


def test_jobs_to_run_never_drops_the_catch_all_job_for_an_unrecognised_path():
    """A path under neither exclusive directory (root-level, `tests/`,
    `docs/`) cannot be ruled out for the job with no `working-directory` of
    its own — that job reads from everywhere CI didn't scope elsewhere."""
    gate = _gate()
    run_jobs = gate.jobs_to_run(_CI_LIKE_WORKFLOW, ["tests/test_gate_runner.py"])
    assert run_jobs == {"backend"}


@pytest.mark.parametrize(
    "changed",
    [
        ["pyproject.toml"],
        ["scripts/gate.py"],
        [".github/workflows/ci.yml"],
        ["src/frontend/package.json"],
        ["docs/package.json"],
    ],
)
def test_jobs_to_run_forces_every_job_on_shared_config(changed):
    """A touch to config every leg's inputs depend on is not attributable to
    one job's directory — the conservative call is "cannot rule out any of
    them," not "guess which one."""
    gate = _gate()
    assert gate.jobs_to_run(_CI_LIKE_WORKFLOW, changed) is None


def test_jobs_to_run_runs_everything_on_an_empty_or_unreadable_diff():
    """No evidence to skip on is not evidence that nothing changed — the
    failure this guards is a false negative (skipping a leg that should run),
    so "unknown" must resolve the same way as "touches everything."""
    gate = _gate()
    assert gate.jobs_to_run(_CI_LIKE_WORKFLOW, []) is None


def _branch_repo(tmp_path):
    """A repo with a `main` at one commit and a feature branch one commit
    ahead, so `diff_base` has a real merge-base to find (unlike `_repo()`
    above, which never branches)."""
    repo = tmp_path / "branch_repo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "base.py").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "seed"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-qb", "feature"],
        check=True, capture_output=True,
    )
    return repo


def test_diff_base_and_changed_paths_against_a_real_branch(tmp_path):
    gate = _gate()
    repo = _branch_repo(tmp_path)
    seed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "main"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    (repo / "src").mkdir()
    (repo / "src" / "brr.py").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "touch backend"],
        check=True, capture_output=True,
    )
    (repo / "untracked.py").write_text("new\n", encoding="utf-8")

    base = gate.diff_base(repo)
    assert base == seed

    changed = gate.changed_paths(repo, base)
    assert changed == ["src/brr.py", "untracked.py"]


def test_diff_base_falls_back_to_head_minus_one_with_no_named_branch(tmp_path):
    """A repo with no `main`/`master` at all (a detached scratch checkout, a
    default-branch name this repo doesn't use) still gets a base — the last
    commit — rather than refusing to compute one."""
    gate = _gate()
    repo = tmp_path / "no_named_branch"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "trunk"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "a.py").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "first"], check=True, capture_output=True
    )
    first = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    (repo / "a.py").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "second"], check=True, capture_output=True
    )

    assert gate.diff_base(repo) == first


def test_changed_only_end_to_end_skips_the_frontend_leg(tmp_path, monkeypatch, capfd):
    """The flag, driven through `main()`: a backend-only diff must not run
    the frontend leg, and must say so where a human (or a strand's own
    status narration) can see it."""
    gate = _gate()
    repo = tmp_path / "e2e_repo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "jobs:\n"
        "  backend:\n"
        "    steps:\n"
        "      - name: backend leg\n"
        "        run: exit 0\n"
        "  frontend:\n"
        "    defaults:\n"
        "      run:\n"
        "        working-directory: src/frontend\n"
        "    steps:\n"
        "      - name: frontend leg\n"
        "        run: touch must-not-run\n",
        encoding="utf-8",
    )
    (repo / "src" / "frontend").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "seed"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-qb", "feature"],
        check=True, capture_output=True,
    )

    (repo / "src" / "brr.py").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "backend only"],
        check=True, capture_output=True,
    )

    monkeypatch.setattr(gate, "REPO_ROOT", repo)
    monkeypatch.setattr(gate, "WORKFLOW", workflow)
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)

    rc = gate.main(["--changed-only"])
    assert rc == 0
    assert not (repo / "src" / "frontend" / "must-not-run").exists()
    err_or_out = capfd.readouterr()
    combined = err_or_out.out + err_or_out.err
    assert "skipping" in combined and "frontend" in combined


def test_a_queued_run_names_the_holder_and_the_wait(tmp_path, monkeypatch, capfd):
    """rec 4: a queued run must say so, not sit silently.

    Holds the lock directly (standing in for a sibling's `gate.py`),
    starts a waiter through `held_gate_lock()` on a background thread, and
    checks both surfaces the spec asks for: the run's own outbox status
    file (what a strand's status narration, or its dispatcher, can read
    without scraping stderr) and the stderr line itself.
    """
    import fcntl

    gate = _gate()
    repo = _repo(tmp_path)
    monkeypatch.setattr(gate, "REPO_ROOT", repo)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))

    lock_path = gate.gate_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    gate._write_lock_status(
        gate._lock_status_path(lock_path),
        pid=999999, since="2026-08-08T00:00:00+00:00",
    )

    entered = threading.Event()

    def _waiter():
        with gate.held_gate_lock():
            entered.set()

    thread = threading.Thread(target=_waiter)
    thread.start()
    try:
        wait_file = outbox / ".gate-wait.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not wait_file.exists():
            time.sleep(0.02)
        assert wait_file.exists(), "the waiting run never wrote its own status"
        payload = json.loads(wait_file.read_text(encoding="utf-8"))
        assert payload["holder_pid"] == 999999
        assert payload["holder_since"] == "2026-08-08T00:00:00+00:00"
        assert payload["waited_seconds"] >= 0
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)
        thread.join(timeout=5)
        assert not thread.is_alive(), "waiter never acquired the lock after release"
        assert entered.is_set()

    err = capfd.readouterr().err
    assert "999999" in err
    assert "queued" in err
