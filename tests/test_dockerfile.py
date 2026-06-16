from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[1] / "src" / "brr" / "Dockerfile"


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

    Docker tasks often need ``brr review`` or other local tooling while
    dogfooding brr itself. The image installs this checkout from the build
    context — never ``pip install brr`` from PyPI (name taken by an unrelated
    terminal image renderer).
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY pyproject.toml README.md /opt/brr/" in text
    assert "COPY src /opt/brr/src" in text
    assert "pip install --no-cache-dir /opt/brr" in text
    assert "'requests>=2.31,<3'" in text
    assert "python3 -m brr review --help" in text
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
