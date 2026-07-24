"""Guard the version: one source, one mirror, one tag.

brnrd ships from a single tag to two registries — PyPI and npm. The number
they publish is written down in exactly two places:

* ``src/brr/__init__.py`` → ``__version__`` — the **source**. ``pyproject.toml``
  declares ``dynamic = ["version"]`` and points setuptools at this attribute,
  so it is also the version PyPI receives; nothing restates it.
* ``packaging/npm/package.json`` → ``version`` — the **mirror**. npm's manifest
  cannot be derived from Python, so it is a literal, and this module is what
  keeps it honest.

That split is deliberate: this guard used to compare ``pyproject.toml`` against
``package.json`` while ``__version__`` was a third, unwatched literal — so a
release could pass the check and still ship a ``brnrd --version`` and a
self-update banner reporting the *previous* version (#674). A class defined by
listing its members will meet the member nobody listed, so the fix was to
delete a member rather than widen the list.

Three drifts are therefore possible, and all three are checked:

* the mirror falling behind the source (``manifest_mismatch``),
* the release tag naming a version the tree does not declare (``tag_mismatch``),
* ``pyproject.toml`` re-growing its own literal and silently taking the source's
  job back (``derivation_mismatch``).

Nothing here imports ``brr``: the release workflow runs this from a bare
checkout, before anything is installed. The version is read out of the source
file statically, the same way setuptools reads it at build time.

Importable for the test suite, runnable from the release workflow.
"""

from __future__ import annotations

import ast
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
NPM_PACKAGE = ROOT / "packaging" / "npm" / "package.json"
VERSION_SOURCE = ROOT / "src" / "brr" / "__init__.py"

#: What ``pyproject.toml`` must delegate its version to for the above to hold.
VERSION_ATTR = "brr.__version__"


def python_version() -> str:
    """The version PyPI will publish.

    Read statically out of ``src/brr/__init__.py`` — no import, because the
    release workflow calls this before the package is installed. setuptools
    resolves ``dynamic`` versions from the same file the same way, so this is
    the number that lands in the built distribution's metadata.
    """
    module = ast.parse(VERSION_SOURCE.read_text(encoding="utf-8"))
    for node in module.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in targets
        ):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        raise ValueError(
            f"{VERSION_SOURCE} assigns __version__ from something other than a "
            "string literal; setuptools cannot read it statically either"
        )
    raise ValueError(f"{VERSION_SOURCE} does not assign __version__")


def npm_version() -> str:
    """The version npm will publish."""
    return json.loads(NPM_PACKAGE.read_text())["version"]


def manifest_mismatch() -> str | None:
    """Describe the drift between source and npm mirror, or ``None`` when they agree."""
    declared, launcher = python_version(), npm_version()
    if declared == launcher:
        return None
    return (
        f"src/brr/__init__.py declares {declared} but "
        f"packaging/npm/package.json declares {launcher}"
    )


def derivation_mismatch() -> str | None:
    """Describe a ``pyproject.toml`` that stopped deriving its version, or ``None``.

    Everything else here rests on PyPI's version coming from ``__version__``.
    Restoring a literal ``[project] version`` would quietly take that back and
    make this guard certify a number it no longer checks — the exact shape of
    #674. So the delegation is itself a checked fact.
    """
    config = tomllib.loads(PYPROJECT.read_text())
    project = config.get("project", {})
    if "version" in project:
        return (
            f"pyproject.toml declares its own version {project['version']!r}; "
            f"it must stay dynamic so {VERSION_ATTR} is the only source"
        )
    if "version" not in project.get("dynamic", []):
        return 'pyproject.toml does not list "version" in project.dynamic'
    declared_attr = (
        config.get("tool", {})
        .get("setuptools", {})
        .get("dynamic", {})
        .get("version", {})
        .get("attr")
    )
    if declared_attr != VERSION_ATTR:
        return (
            f"pyproject.toml derives its version from {declared_attr!r}, "
            f"not {VERSION_ATTR!r}"
        )
    return None


def tag_mismatch(tag: str) -> str | None:
    """Describe the drift between a release tag and the declared version, or ``None``."""
    wanted = tag[1:] if tag.startswith("v") else tag
    declared = python_version()
    if wanted == declared:
        return None
    return f"release tag {tag} does not name the declared version {declared}"


def _surfaces(tag: str | None) -> str:
    """Name what was actually compared — the success line may not claim more."""
    checked = ["src/brr/__init__.py", "pyproject.toml", "packaging/npm/package.json"]
    if tag:
        checked.append(f"tag {tag}")
    return ", ".join(checked)


def main(argv: list[str]) -> int:
    tag = argv[1] if len(argv) > 1 and argv[1] else None
    problems = [derivation_mismatch(), manifest_mismatch()]
    if tag:
        problems.append(tag_mismatch(tag))
    failures = [problem for problem in problems if problem]
    for failure in failures:
        print(f"release version check: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"release version check: {python_version()} agrees across {_surfaces(tag)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
