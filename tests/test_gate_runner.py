"""The local gate runner must stay a *reader* of CI, never a second copy.

Every test here exists to catch one specific way this file could quietly stop
being true: a leg list that drifts, a refusal that stops being reported, a job
whose steps are silently dropped.
"""

from __future__ import annotations

import importlib.util
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
