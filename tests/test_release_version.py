"""The release version guard: one tag, two registries, one number."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import brr

REPO = Path(__file__).parents[1]

# Loaded by path on purpose: the repo's `packaging/` directory is not an import
# package, and `packaging` is already taken by an installed distribution.
_SPEC = importlib.util.spec_from_file_location(
    "brnrd_check_release_version",
    REPO / "packaging" / "check_release_version.py",
)
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)

# The files that carry, derive, or mirror the version — a scratch release tree
# needs exactly these, in this layout, for the guard to resolve its own ROOT.
TREE = (
    "pyproject.toml",
    "packaging/check_release_version.py",
    "packaging/npm/package.json",
    "src/brr/__init__.py",
)


def _release_tree(root: Path) -> Path:
    """Copy the real version-bearing files into ``root``, preserving layout."""
    for relative in TREE:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, destination)
    return root


def _run_guard(root: Path, *argv: str) -> subprocess.CompletedProcess:
    """Drive the real entrypoint the release workflow drives, in ``root``."""
    return subprocess.run(
        [sys.executable, str(root / "packaging" / "check_release_version.py"), *argv],
        capture_output=True,
        text=True,
    )


def _rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{path} no longer contains {old!r}"
    path.write_text(text.replace(old, new, 1))


def _pin_pyproject_version(tree: Path, version: str) -> None:
    """Give ``pyproject.toml`` its own literal version, whatever shape it is in.

    Deliberately blind to whether the tree already derives its version: the
    scenario under test is a human editing the file to cut a release, and this
    has to reproduce it on a tree that predates the fix as faithfully as on one
    that carries it. A helper that only worked post-fix would turn the
    regression test into a fixture assertion.
    """
    path = tree / "pyproject.toml"
    text = path.read_text()
    for existing in ('dynamic = ["version"]', 'version = "0.1.0"'):
        if existing in text:
            path.write_text(text.replace(existing, f'version = "{version}"', 1))
            return
    raise AssertionError(f"{path} declares its version in an unrecognised shape")


def test_the_version_agrees_across_every_surface_that_publishes_it():
    # Three surfaces, one number: what the guard reads statically, what the
    # running daemon reports (`brnrd --version`, `daemon_version`, the
    # self-update banner), and what npm ships.
    assert checker.python_version() == brr.__version__
    assert checker.npm_version() == brr.__version__
    # ...and pyproject.toml still derives PyPI's version from that same source
    # rather than restating it.
    assert checker.derivation_mismatch() is None


def test_the_backend_publishes_the_same_version_in_its_openapi_schema(tmp_path):
    """`src/brnrd/app.py` carried a fourth literal — and a published one.

    It is the ``info.version`` of the backend's OpenAPI schema, outside
    everything #674 enumerated. Driven through the real app factory, not
    asserted against the source text: the schema is what actually ships.

    The source tree is copied and ``__version__`` moved off ``0.1.0`` first.
    Asserting agreement while both numbers happen to be ``0.1.0`` proves
    nothing — a hardcoded literal passes that test right up until the release
    it is supposed to catch.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("sqlalchemy")

    src = tmp_path / "src"
    shutil.copytree(REPO / "src", src, ignore=shutil.ignore_patterns("*.egg-info", "frontend"))
    init = src / "brr" / "__init__.py"
    init.write_text(init.read_text().replace(f'"{brr.__version__}"', '"9.9.9"'))

    published = subprocess.run(
        [
            sys.executable,
            "-c",
            "from brnrd import create_app\n"
            "from brnrd.config import Settings\n"
            "app = create_app(Settings(database_url='sqlite:///:memory:',\n"
            "    public_base_url='https://brnrd.example',\n"
            "    github_oauth_client_id='x', github_oauth_client_secret='y'))\n"
            "print(app.openapi()['info']['version'])\n",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(src)},
    )

    assert published.returncode == 0, published.stderr
    assert published.stdout.strip() == "9.9.9", (
        "the backend published its own literal instead of brr.__version__"
    )


def test_a_release_that_bumps_the_manifests_but_not_the_source_is_caught(tmp_path):
    """#674, as an assertion.

    A human cutting v0.2.0 edits the two files the old guard named — the
    pyproject literal and the npm manifest — and leaves ``__version__``. That
    tree used to print ``agrees everywhere`` and exit 0, then ship a 0.2.0
    release whose ``brnrd --version`` said 0.1.0 forever.
    """
    tree = _release_tree(tmp_path / "release")
    _pin_pyproject_version(tree, "0.2.0")
    _rewrite(tree / "packaging/npm/package.json", '"version": "0.1.0"', '"version": "0.2.0"')
    assert '__version__ = "0.1.0"' in (tree / "src/brr/__init__.py").read_text()

    result = _run_guard(tree, "v0.2.0")

    assert result.returncode != 0, result.stdout
    assert "agrees" not in result.stdout
    assert "src/brr/__init__.py" in result.stderr


def test_an_npm_mirror_left_behind_is_caught(tmp_path):
    tree = _release_tree(tmp_path / "release")
    _rewrite(tree / "packaging/npm/package.json", '"version": "0.1.0"', '"version": "0.2.0"')

    result = _run_guard(tree, "v0.1.0")

    assert result.returncode != 0, result.stdout
    assert "0.1.0" in result.stderr and "0.2.0" in result.stderr


def test_a_pyproject_that_stops_deriving_its_version_is_caught(tmp_path):
    # Even with every number in agreement, a restored literal is drift waiting
    # to happen: it is a second copy nothing keeps in step with __version__.
    tree = _release_tree(tmp_path / "release")
    _pin_pyproject_version(tree, brr.__version__)

    result = _run_guard(tree, f"v{brr.__version__}")

    assert result.returncode != 0, result.stdout
    assert "pyproject.toml" in result.stderr


def test_the_shipped_tree_passes_the_workflow_invocation(tmp_path):
    tree = _release_tree(tmp_path / "release")

    result = _run_guard(tree, f"v{brr.__version__}")

    assert result.returncode == 0, result.stderr
    assert brr.__version__ in result.stdout


def test_the_success_line_names_only_what_it_compared(capsys):
    assert checker.main(["check", f"v{brr.__version__}"]) == 0
    line = capsys.readouterr().out

    # "agrees everywhere" is what #674 was about: a claim wider than the check.
    assert "everywhere" not in line
    for surface in ("src/brr/__init__.py", "pyproject.toml", "packaging/npm/package.json"):
        assert surface in line
    assert f"tag v{brr.__version__}" in line


def test_the_version_is_read_without_importing_brr(tmp_path):
    # The release workflow runs the guard from a bare checkout, before
    # `pip install`. A source file that cannot be imported must still be read.
    tree = _release_tree(tmp_path / "release")
    source = tree / "src/brr/__init__.py"
    source.write_text(source.read_text() + "\nimport a_module_that_does_not_exist\n")

    result = _run_guard(tree, "v0.1.0")

    assert result.returncode == 0, result.stderr


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
