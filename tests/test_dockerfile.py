from pathlib import Path

import pytest

from brr import adopt

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "src" / "brr" / "Dockerfile"
PYPROJECT = REPO_ROOT / "pyproject.toml"


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
    sources = adopt.dockerfile_context_paths(text)
    assert {"pyproject.toml", "README.md", "src"} <= set(sources)
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
    tomllib = pytest.importorskip(
        "tomllib",  # stdlib on 3.11+; CI runs 3.12, so the check is enforced there
        reason="tomllib needs Python 3.11+",
    )
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return declared["project"]["license-files"]


def test_declared_license_files_reach_the_runner_build_context(tmp_path):
    """Every ``license-files`` entry must survive context assembly.

    Assembles the real build context ``brnrd init -i`` would hand to
    ``docker build`` and looks for each declared license inside it. A
    declared-but-uncopied license makes the wheel built in the image drop
    that license file.
    """
    adopt._assemble_build_context(DOCKERFILE, REPO_ROOT, tmp_path)

    missing = [
        rel for rel in _declared_license_files()
        if not (tmp_path / rel).is_file()
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
