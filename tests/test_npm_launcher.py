"""Drive the npm launcher (packaging/npm/bin/brnrd.js) with node against a fake installer and a fake shim.

The launcher has one job: run brnrd's one installer once, pinned to the package's release tag, then hand every
call to the shim it made. What is under test is that decision and what it tells the person, never the install.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NPM = ROOT / "packaging" / "npm"
VERSION = json.loads((NPM / "package.json").read_text())["version"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _package(tmp_path: Path, where: str = "global/lib/node_modules/brnrd") -> Path:
    """The package as npm lays it out: brnrd.js and package.json, plus a fake install.sh that records its env
    and makes the shim (or fails, when FAIL_INSTALL is set)."""
    package = tmp_path / where
    (package / "bin").mkdir(parents=True)
    shutil.copy(NPM / "bin" / "brnrd.js", package / "bin" / "brnrd.js")
    shutil.copy(NPM / "package.json", package / "package.json")
    _script(package / "install.sh", r'''printf 'installer BRNRD_REF=%s\n' "$BRNRD_REF" >> "$TRACE"
[ -n "${FAIL_INSTALL:-}" ] && exit 7
[ -n "${NO_SHIM:-}" ] && exit 0
mkdir -p "$BRNRD_ROOT/bin"
printf '#!/bin/sh\nprintf "shim %%s LAUNCHER=%%s\\n" "$*" "${BRNRD_LAUNCHER-unset}" >> "$TRACE"\nexit 3\n' > "$BRNRD_ROOT/bin/brnrd"
chmod +x "$BRNRD_ROOT/bin/brnrd"
''')
    return package / "bin" / "brnrd.js"


def _run(tmp_path, launcher, *args, **env):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("BRNRD_", "npm_", "XDG_"))}
    full = {**base, "HOME": str(home), "BRNRD_ROOT": str(home / ".brnrd"), "TRACE": str(tmp_path / "trace"), **env}
    result = subprocess.run(["node", str(launcher), *args], env=full, text=True, capture_output=True, timeout=30)
    trace = (tmp_path / "trace").read_text().splitlines() if (tmp_path / "trace").exists() else []
    return result, trace


def test_first_run_installs_this_release_then_every_run_is_the_shim(tmp_path):
    launcher = _package(tmp_path)
    result, trace = _run(tmp_path, launcher, "setup", "--yes")
    assert trace == [f"installer BRNRD_REF=v{VERSION}", "shim setup --yes LAUNCHER=unset"], result.stderr
    assert result.returncode == 3  # the shim's status is the launcher's
    assert f"installing v{VERSION}" in result.stderr
    (tmp_path / "trace").unlink()
    result, trace = _run(tmp_path, launcher, "--version")
    assert trace == ["shim --version LAUNCHER=unset"] and "installing" not in result.stderr


def test_an_existing_shim_is_used_and_the_installer_never_runs(tmp_path):
    launcher = _package(tmp_path)
    _script(tmp_path / "home/.brnrd/bin/brnrd", 'printf "own shim %s\\n" "$*" >> "$TRACE"\n')
    result, trace = _run(tmp_path, launcher, "loom", "status")
    assert result.returncode == 0 and trace == ["own shim loom status"]


def test_a_failed_install_stops_with_its_status_and_runs_nothing(tmp_path):
    result, trace = _run(tmp_path, _package(tmp_path), "setup", FAIL_INSTALL="1")
    assert result.returncode == 7 and trace == [f"installer BRNRD_REF=v{VERSION}"]


def test_an_install_that_leaves_no_shim_says_so(tmp_path):
    result, _ = _run(tmp_path, _package(tmp_path), NO_SHIM="1")
    assert result.returncode == 1 and "is missing" in result.stderr


def test_npx_is_told_to_the_payload_by_where_the_launcher_lives_not_by_env(tmp_path):
    npx = _package(tmp_path, "npm-cache/_npx/deadbeef/node_modules/brnrd")
    _, trace = _run(tmp_path, npx, "up")
    assert trace[-1] == "shim up LAUNCHER=npx"
    # #1084: a leaked npm_command=exec from a parent shell never fakes npx for a global install.
    (tmp_path / "trace").unlink()
    _, trace = _run(tmp_path, _package(tmp_path / "g"), "up", npm_command="exec", npm_lifecycle_event="npx")
    assert trace[-1] == "shim up LAUNCHER=unset"


def test_an_old_launcher_venv_is_named_once_and_left_alone(tmp_path):
    launcher = _package(tmp_path)
    old = tmp_path / "home/.local/share/brnrd/venv"
    old.mkdir(parents=True)
    first, _ = _run(tmp_path, launcher, "--version")
    again, _ = _run(tmp_path, launcher, "--version")
    assert str(old) in first.stderr and "no longer used" in first.stderr
    assert "no longer used" not in again.stderr
    assert old.is_dir()


def test_windows_is_told_to_use_wsl_and_nothing_runs(tmp_path):
    # npm must let the package onto Windows (no "os" field: it would refuse with EBADPLATFORM),
    # or this message is never read.
    assert "os" not in json.loads((NPM / "package.json").read_text())
    fake = tmp_path / "win32.js"
    fake.write_text('require("node:os").platform = () => "win32";\n')
    result, trace = _run(tmp_path, _package(tmp_path), "setup", NODE_OPTIONS=f"--require {fake}")
    assert result.returncode == 1 and "inside WSL" in result.stderr
    assert trace == []  # neither the installer nor a shim ran


def test_the_tarball_ships_the_public_installer_copied_at_pack_time():
    package = json.loads((NPM / "package.json").read_text())
    assert "install.sh" in package["files"] and "uv-assets.json" not in package["files"]
    assert package["scripts"]["prepack"] == "cp ../../docs/public/install.sh install.sh"
    assert (NPM / ".gitignore").read_text().split() == ["install.sh"]  # never a second, drifting copy in git
    assert not (NPM / "install.sh").exists() or (NPM / "install.sh").read_bytes() == (
        ROOT / "docs/public/install.sh").read_bytes()
