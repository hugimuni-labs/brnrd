"""The release version guard: one tag, two registries, one number."""

import importlib.util
import json
from pathlib import Path

# Loaded by path on purpose: the repo's `packaging/` directory is not an import
# package, and `packaging` is already taken by an installed distribution.
_SPEC = importlib.util.spec_from_file_location(
    "brnrd_check_release_version",
    Path(__file__).parents[1] / "packaging" / "check_release_version.py",
)
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)


def test_the_two_manifests_declare_the_same_version():
    assert checker.manifest_mismatch() is None


def test_manifest_drift_is_reported_with_both_sides(tmp_path, monkeypatch):
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"version": "9.9.9"}))
    monkeypatch.setattr(checker, "NPM_PACKAGE", package)

    problem = checker.manifest_mismatch()

    assert problem is not None
    assert checker.python_version() in problem
    assert "9.9.9" in problem


def test_a_tag_naming_another_version_is_a_mismatch():
    declared = checker.python_version()

    assert checker.tag_mismatch(f"v{declared}") is None
    assert checker.tag_mismatch(declared) is None
    assert checker.tag_mismatch("v99.0.0") is not None


def test_main_fails_on_a_wrong_tag_and_passes_on_the_right_one():
    declared = checker.python_version()

    assert checker.main(["check", f"v{declared}"]) == 0
    assert checker.main(["check", "v0.0.0"]) == 1
    assert checker.main(["check"]) == 0
