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


def test_bundled_runner_image_installs_github_cli():
    """``gh`` is part of the runner toolbox so agents can open PRs when
    a task lacks an auto-land target. We pull from GitHub's upstream APT
    repo rather than Debian's to track current ``gh`` features (Debian
    sometimes lags by years). Authentication comes from the host's
    ``~/.config/gh`` mount, mirroring how we ship Codex/Claude tokens.
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
