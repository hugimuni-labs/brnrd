import fnmatch
from pathlib import Path

import pytest

from brr import adopt

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "src" / "brr" / "Dockerfile"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _pyproject() -> dict:
    tomllib = pytest.importorskip(
        "tomllib",  # stdlib on 3.11+; CI runs 3.12, so the check is enforced there
        reason="tomllib needs Python 3.11+",
    )
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def assembled_context(tmp_path_factory) -> Path:
    """The real build context ``brnrd init -i`` hands to ``docker build``.

    Assembled from the *real* Dockerfile against the *real* repo root, once
    per module: every assertion below is about what actually reaches the
    docker daemon, and a fake fixture Dockerfile could not catch a
    ``COPY`` line that over-reaches into this checkout (#680). Assembly is
    the expensive step here — it copytrees the declared sources — so the
    tests share one context rather than paying for it each.
    """
    ctx = tmp_path_factory.mktemp("runner-build-context")
    adopt._assemble_build_context(DOCKERFILE, REPO_ROOT, ctx)
    return ctx


def _apt_install_packages(text: str) -> set[str]:
    packages: set[str] = set()
    in_install = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "apt-get install" in line:
            in_install = True
            continue
        if not in_install:
            continue
        if line.startswith("&&"):
            break
        package = line.rstrip("\\").strip()
        if package and not package.startswith("-"):
            packages.add(package)
    return packages


def test_bundled_runner_image_has_baseline_dev_tools():
    text = DOCKERFILE.read_text(encoding="utf-8")

    required = {
        "bash",
        "build-essential",
        "ca-certificates",
        "curl",
        "file",
        "git",
        "jq",
        "openssh-client",
        "pkg-config",
        "python-is-python3",
        "python3",
        "python3-pip",
        "python3-venv",
        "ripgrep",
        "rsync",
        "unzip",
        "wget",
        "zip",
    }
    assert required <= _apt_install_packages(text)
    assert "ENV PIP_BREAK_SYSTEM_PACKAGES=1" in text
    assert "ln -sf /usr/bin/pip3 /usr/local/bin/pip" in text


def test_bundled_runner_image_installs_brr_cli_and_runtime_deps():
    """The default agent image should carry brr's own CLI surface.

    Docker tasks often need ``brnrd review`` or other local tooling while
    dogfooding brr itself. The image installs this checkout from the build
    context — never ``pip install brr`` from PyPI (name taken by an unrelated
    terminal image renderer).
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    # Derived, not a literal COPY line: the packaging tree must reach the
    # context, but *which* extra files ride along is allowed to grow (#675).
    # The package trees themselves are checked at the context layer by
    # ``test_build_context_has_everything_pip_install_needs`` — asserting a
    # literal ``"src"`` source here would re-pin the wholesale copy #680
    # removed, and would pass just as happily if a COPY line dragged the
    # whole checkout in.
    sources = adopt.dockerfile_context_paths(text)
    assert {"pyproject.toml", "README.md"} <= set(sources)
    assert "pip install --no-cache-dir /opt/brr" in text
    assert "'requests>=2.31,<3'" in text
    assert "brnrd review --help" in text
    assert "'brr>=0.1.0'" not in text


def test_bundled_runner_image_installs_github_cli():
    """``gh`` is part of the runner toolbox so agents can open PRs when
    a task lacks an auto-land target. We pull from GitHub's upstream APT
    repo rather than Debian's to track current ``gh`` features (Debian
    sometimes lags by years). Authentication is wired by the docker env
    via an injected ``GITHUB_TOKEN`` env var (resolved from gate state,
    daemon env, or ``gh auth token`` on the host); ``~/.config/gh`` is
    deliberately not bind-mounted because the gh keyring backend isn't
    reachable from inside the container.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "cli.github.com/packages" in text
    assert "githubcli-archive-keyring.gpg" in text
    assert "apt-get install -y --no-install-recommends gh" in text


def test_bundled_runner_image_supports_arbitrary_uid():
    """The image must run as the host UID without root-only assumptions.

    A writable ``/brr-home`` with mode 1777 means any UID can write
    there; ``ENV HOME=/brr-home`` means the CLIs and git find their
    config at ``$HOME/...`` regardless of whether the runtime UID has
    a ``/etc/passwd`` entry. Together they keep bind-mounted host
    paths owned by the host user.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "mkdir -p /brr-home" in text
    assert "chmod 1777 /brr-home" in text
    assert "ENV HOME=/brr-home" in text


def test_bundled_runner_image_exposes_user_local_bin_on_path():
    """User-mode ``pip install`` scripts must be on ``PATH``.

    The container runs as the host UID, which has no write access to
    the system Python site-packages. Any ``pip install`` an agent runs
    inside the container therefore lands in ``$HOME/.local/bin``. The
    image prepends that directory to ``PATH`` so freshly-installed
    console scripts (``pytest``, the project's own ``brr`` entry point,
    ruff, etc.) are reachable by name, rather than forcing agents into
    ``python -m <module>`` workarounds.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "ENV PATH=/brr-home/.local/bin:$PATH" in text


# ── Build context vs. declared license files (#675) ──────────────────
#
# ``pyproject.toml``'s ``license-files`` and the Dockerfile's ``COPY``
# sources are two enumerated lists, and before #675 nothing checked that
# the second contained the first: the root ``LICENSE`` and
# ``LICENSE-OVERVIEW.md`` were declared but never copied, so the wheel
# installed into the runner image carried two of the four licenses and
# said so only through a ``SetuptoolsDeprecationWarning`` buried in a
# docker build. Both sides below are *read* rather than restated — adding
# a fifth license file turns this red until the context carries it, and
# needs no edit here.


def _declared_license_files() -> list[str]:
    return _pyproject()["project"]["license-files"]


def test_declared_license_files_reach_the_runner_build_context(assembled_context):
    """Every ``license-files`` entry must survive context assembly.

    Reads the real build context ``brnrd init -i`` would hand to
    ``docker build`` and looks for each declared license inside it. A
    declared-but-uncopied license makes the wheel built in the image drop
    that license file. The containment is checked per declared *path*, so
    it keeps proving the same thing however coarse or narrow the ``COPY``
    sources are — narrowing ``src`` to the two package dirs (#680) does not
    weaken it, because ``src/brr/LICENSE`` is still looked up in full.
    """
    missing = [
        rel for rel in _declared_license_files()
        if not (assembled_context / rel).is_file()
    ]
    assert not missing, (
        f"pyproject declares {missing} in license-files, but the runner image's "
        f"build context never receives them — add them to a COPY source in "
        f"{DOCKERFILE.name}"
    )


def test_declared_license_files_exist_in_the_checkout():
    """A declared license that isn't in the tree is the same bug, one step earlier."""
    missing = [rel for rel in _declared_license_files() if not (REPO_ROOT / rel).is_file()]
    assert not missing, f"license-files names paths that do not exist: {missing}"


# ── Build context vs. what the image is actually for (#680) ──────────
#
# ``COPY src /opt/brr/src`` shipped every top-level directory under
# ``src/`` into the runner image — including ``src/frontend``, whose
# ``node_modules`` is ~188 MB on any checkout that has built the
# dashboard, plus ``*.egg-info`` build residue. ``MANIFEST.in`` already
# carried ``prune src/frontend``, so the sdist surface knew and the docker
# surface did not: the #675 shape again, one exclusion declared on one
# packaging surface with nothing checking the other.
#
# The tests below are the connection. Like #675's they read *both* sides
# rather than restating either: the legitimate set comes from
# ``pyproject.toml``'s ``packages.find.include``, which is what actually
# gets installed, and the actual set comes from assembling the real
# context. Naming ``frontend`` here would only pin today's offender; a
# future ``src/whatever-huge`` has to be caught by the same assertion.


def _declared_package_patterns() -> tuple[list[str], list[str]]:
    """``where`` roots and ``include`` globs from setuptools' package discovery."""
    find = _pyproject()["tool"]["setuptools"]["packages"]["find"]
    return find["where"], find["include"]


def test_build_context_carries_only_declared_python_packages(assembled_context):
    """Nothing reaches the image under ``src/`` that isn't a shipped package.

    ``pip install /opt/brr`` installs exactly the packages setuptools
    discovers under ``where``/``include``; anything else in the context is
    paid for twice — once copytreeing it into the temp dir, once baking it
    into an image layer — and delivers nothing. Deriving the allowed set
    from ``pyproject.toml`` rather than listing it here means a genuinely
    new package needs no edit in this file, while a new *non*-package
    directory fails until someone decides, at the ``COPY`` line, that the
    image should carry it.
    """
    where, include = _declared_package_patterns()

    for root in where:
        root_path = assembled_context / root
        if not root_path.is_dir():
            continue
        strays = sorted(
            entry.name
            for entry in root_path.iterdir()
            if not any(fnmatch.fnmatch(entry.name, pat) for pat in include)
        )
        assert not strays, (
            f"the runner image's build context carries {strays} under "
            f"{root!r}, but pyproject only installs {include} from there — "
            f"name the package directories explicitly in {DOCKERFILE.name}'s "
            f"COPY lines instead of copying {root!r} wholesale"
        )


def test_build_context_carries_no_vendored_dependency_trees(assembled_context):
    """No ``node_modules`` at any depth, ever.

    The concrete symptom of the above, asserted directly because it is the
    expensive one: a single ``node_modules`` is thousands of files and
    hundreds of megabytes, and it reaches the docker daemon as build
    context whether or not any ``COPY`` line in the image ever consumes it.
    """
    vendored = sorted(
        str(path.relative_to(assembled_context))
        for path in assembled_context.rglob("node_modules")
        if path.is_dir()
    )
    assert not vendored, (
        f"build context contains vendored dependency trees {vendored}; the "
        f"runner image installs Python packages, not JS ones"
    )


def test_build_context_has_everything_pip_install_needs(assembled_context):
    """``pip install /opt/brr`` must still be satisfiable from the context.

    The narrowing above is only safe if it removes nothing the build step
    reads. setuptools needs the project metadata, the readme named by
    ``readme =``, every declared license file, and each package tree it
    will discover — asserted here as the complete precondition so a future
    narrowing that cuts too deep fails at context-assembly speed rather
    than minutes into a ``docker build``.
    """
    project = _pyproject()["project"]
    where, _include = _declared_package_patterns()

    required = ["pyproject.toml", project["readme"], *project["license-files"]]
    missing = [rel for rel in required if not (assembled_context / rel).is_file()]
    assert not missing, f"context is missing build inputs: {missing}"

    for root in where:
        found = sorted(
            entry.name
            for entry in (assembled_context / root).iterdir()
            if entry.is_dir() and (entry / "__init__.py").is_file()
        )
        assert found, f"context carries no importable package under {root!r}"
