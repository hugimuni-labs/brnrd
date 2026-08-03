import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
NPM = ROOT / "packaging" / "npm"
LAUNCHER = NPM / "bin" / "brnrd.js"

# `npx brnrd` and a globally installed `brnrd` both reach `bin/brnrd.js`, and
# only one of them leaves a command on the user's PATH. npm marks the former
# with `npm_command=exec` (probed on npm 11.6.1 / node 22 in both directions).
NPX_ENV = {"npm_command": "exec", "npm_lifecycle_event": "npx"}


def test_uv_release_assets_are_pinned_for_supported_node_hosts():
    release = json.loads((NPM / "uv-assets.json").read_text())

    assert re.fullmatch(r"\d+\.\d+\.\d+", release["version"])
    assert release["python"] == "3.12"
    assert release["checksums"] == (
        f"https://github.com/astral-sh/uv/releases/download/"
        f"{release['version']}/sha256.sum"
    )
    assert set(release["assets"]) == {
        "darwin-arm64",
        "darwin-x64",
        "linux-arm64-gnu",
        "linux-arm64-musl",
        "linux-ia32-gnu",
        "linux-ia32-musl",
        "linux-x64-gnu",
        "linux-x64-musl",
        "win32-arm64",
        "win32-ia32",
        "win32-x64",
    }
    for asset in release["assets"].values():
        assert asset["archive"].startswith("uv-")
        assert asset["archive"].endswith((".tar.gz", ".zip"))
        assert re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
        assert asset["size"] > 10_000_000


def test_uv_release_manifest_is_in_the_npm_tarball():
    package = json.loads((NPM / "package.json").read_text())
    assert "uv-assets.json" in package["files"]


# ── the launcher, driven ────────────────────────────────────────────
#
# These run `bin/brnrd.js` for real. The virtualenv it would build is faked
# in a tmp `BRNRD_HOME`, so nothing touches the network: a `bin/python` that
# exits 0 satisfies the install step, and a `bin/brnrd` that echoes its own
# environment stands in for the payload. What is under test is the launcher's
# own decisions — which environment it hands the payload, and what it tells
# the user — not the bootstrap it wraps.


def _script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _fake_home(tmp_path: Path, *, reported_version: str) -> Path:
    """A BRNRD_HOME whose venv is already there and answers `--version`."""
    home = tmp_path / "brnrd-home"
    bin_dir = home / "venv" / "bin"
    _script(bin_dir / "python", 'exit 0')
    _script(
        bin_dir / "brnrd",
        f'case "$1" in --version) echo "brnrd {reported_version}"; exit 0;; esac\n'
        'echo "LAUNCHER=${BRNRD_LAUNCHER-unset}"',
    )
    return home


def _run_launcher(home: Path, tmp_path: Path, extra_env: dict) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment-dependent
        pytest.skip("node is not installed")
    env = {
        # A curated PATH: `uv` on the developer's own PATH would send the
        # install step down the uv branch and make the test host-dependent.
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "BRNRD_HOME": str(home),
    }
    env.update(extra_env)
    return subprocess.run(
        [node, str(LAUNCHER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _version() -> str:
    return json.loads((NPM / "package.json").read_text())["version"]


def test_payload_is_told_when_it_was_launched_through_npx(tmp_path):
    """`BRNRD_LAUNCHER=npx` is the payload's only way to know.

    brnrd prints its own next steps ("next: `brnrd up`"), and under npx that
    command does not exist. The launcher is the only component that can tell
    the two npm spellings apart, so this variable is a contract.
    """
    home = _fake_home(tmp_path, reported_version=_version())

    result = _run_launcher(home, tmp_path, NPX_ENV)

    assert "LAUNCHER=npx" in result.stdout, result.stderr


def test_a_path_install_leaves_the_launcher_marker_unset(tmp_path):
    """`npm i -g brnrd`, `pip`, `uv`, `pipx` — a real `brnrd` exists.

    The regression this guards is silent: were the marker always set, every
    properly installed user would be told to type `npx brnrd …` forever.
    """
    home = _fake_home(tmp_path, reported_version=_version())

    result = _run_launcher(home, tmp_path, {})

    assert "LAUNCHER=unset" in result.stdout, result.stderr


def test_first_npx_bootstrap_says_where_brnrd_went_and_how_to_get_the_command(
    tmp_path,
):
    """The `command not found` a first-time npx user hits, pre-empted.

    A stale reported version forces the bootstrap branch; the faked `python`
    makes its install step succeed offline.
    """
    home = _fake_home(tmp_path, reported_version="0.0.0-stale")

    result = _run_launcher(home, tmp_path, NPX_ENV)

    assert "npm install -g brnrd" in result.stderr, result.stderr
    assert str(home / "venv" / "bin" / "brnrd") in result.stderr


def test_a_path_install_is_not_lectured_about_its_own_path(tmp_path):
    """Same bootstrap, no npx: the advice would be noise, so it is not given."""
    home = _fake_home(tmp_path, reported_version="0.0.0-stale")

    result = _run_launcher(home, tmp_path, {})

    assert "npm install -g brnrd" not in result.stderr, result.stderr
