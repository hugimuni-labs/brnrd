"""Guard the one fact two package manifests both claim: the version.

brnrd ships from a single tag to two registries — PyPI from ``pyproject.toml``
and npm from ``packaging/npm/package.json``. Nothing in either publisher checks
the other, and neither checks the tag: a release cut as ``v0.2.0`` against a
tree still declaring ``0.1.0`` republishes the old version to PyPI and fails
npm with a stale-version error halfway through the run. This module is the
check that makes that mismatch loud before anything is uploaded.

Importable for the test suite, runnable from the release workflow.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
NPM_PACKAGE = ROOT / "packaging" / "npm" / "package.json"


def python_version() -> str:
    """The version PyPI will publish."""
    return tomllib.loads(PYPROJECT.read_text())["project"]["version"]


def npm_version() -> str:
    """The version npm will publish."""
    return json.loads(NPM_PACKAGE.read_text())["version"]


def manifest_mismatch() -> str | None:
    """Describe the drift between the two manifests, or ``None`` when they agree."""
    declared, launcher = python_version(), npm_version()
    if declared == launcher:
        return None
    return (
        f"pyproject.toml declares {declared} but "
        f"packaging/npm/package.json declares {launcher}"
    )


def tag_mismatch(tag: str) -> str | None:
    """Describe the drift between a release tag and the manifests, or ``None``."""
    wanted = tag[1:] if tag.startswith("v") else tag
    declared = python_version()
    if wanted == declared:
        return None
    return f"release tag {tag} does not name the declared version {declared}"


def main(argv: list[str]) -> int:
    problems = [manifest_mismatch()]
    if len(argv) > 1 and argv[1]:
        problems.append(tag_mismatch(argv[1]))
    failures = [problem for problem in problems if problem]
    for failure in failures:
        print(f"release version check: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"release version check: {python_version()} agrees everywhere")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
