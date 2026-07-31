"""The local gate runner must stay a *reader* of CI, never a second copy.

Every test here exists to catch one specific way this file could quietly stop
being true: a leg list that drifts, a refusal that stops being reported, a job
whose steps are silently dropped.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
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
    delivered to chat (the drain skips dotfiles), read by the closeout guard."""
    gate = _gate()
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(tmp_path))
    path = gate.write_receipt("RED", [("backend: Run tests", "FAIL rc=1", 12.34)])
    assert path == tmp_path / gate.RECEIPT_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    # RED is recorded, not suppressed: the obligation a reader checks is that
    # the gate *ran* on this tree, never that it was green.
    assert payload["verdict"] == "RED"
    assert payload["legs"][0]["verdict"] == "FAIL rc=1"
    assert set(payload) >= {"head", "status", "diff_digest", "untracked_digest"}


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

    assert payload["tree_moved_during_gate"] is True
    # The pair, not a boolean — which is what lets a reader name the file.
    assert payload["gated_from"]["status"] != payload["status"]
    assert "written-mid-gate.py" not in payload["gated_from"]["status"]
    assert "written-mid-gate.py" in payload["status"]
    assert "status" in payload["moved_referents"]
    assert "written-mid-gate.py" in gate_receipt.moved_summary(payload)


def test_a_still_tree_records_that_it_held_still(tmp_path, monkeypatch):
    """The honest path, and the reason the state is not a bare boolean-or-
    absent: `false` here means *a writer checked and the tree held*, which is
    a different claim from a receipt too old to have looked."""
    gate = _gate()
    repo = _repo(tmp_path)
    # Not `true` — YAML coerces that to a bool and the leg stops being a
    # string long before it reaches a shell.
    _fake_ci(repo, "exit 0")
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    assert _drive(gate, monkeypatch, repo, outbox, []) == 0
    payload = json.loads((outbox / gate.RECEIPT_NAME).read_text(encoding="utf-8"))

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
    gate = _gate()
    repo = _repo(tmp_path)
    _fake_ci(repo, "printf 'x\\n' > written-mid-gate.py; exit 1")
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    assert _drive(gate, monkeypatch, repo, outbox, []) == 1
    payload = json.loads((outbox / gate.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert payload["verdict"] == "RED"
    assert payload["tree_moved_during_gate"] is True
