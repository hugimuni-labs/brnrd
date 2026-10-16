"""The public installer, run through /bin/sh the way a user pipes it.

BRNRD_INSTALL_DRY_RUN=1 is the whole effect under test. The fakes sit at the
front of PATH, and HOME is a tmp dir, so a probe cannot reach a real uv.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "docs" / "public" / "install.sh"
SHELLS = [path for path in ("/bin/sh", "/bin/dash") if os.path.exists(path)]
CURL = "curl -LsSf https://hugimuni-labs.github.io/brnrd/install.sh | sh"
UV_BOOTSTRAP = "curl -LsSf https://astral.sh/uv/install.sh | sh"

UV_ABSENT = """
if [ "$1" = "tool" ] && [ "$2" = "list" ]; then
  exit 0
fi
printf '%s\\n' "unexpected uv $*" >> "$BRNRD_TRACE"
exit 1
"""

UV_PRESENT = """
if [ "$1" = "tool" ] && [ "$2" = "list" ]; then
  printf '%s\\n' "brnrd v1.2.3"
  printf '%s\\n' "- brnrd"
  exit 0
fi
printf '%s\\n' "unexpected uv $*" >> "$BRNRD_TRACE"
exit 1
"""

PIPX_ABSENT = """
if [ "$1" = "list" ] && [ "$2" = "--short" ]; then
  exit 0
fi
printf '%s\\n' "unexpected pipx $*" >> "$BRNRD_TRACE"
exit 1
"""

PIPX_PRESENT = """
if [ "$1" = "list" ] && [ "$2" = "--short" ]; then
  printf '%s\\n' "brnrd 1.2.3"
  exit 0
fi
printf '%s\\n' "unexpected pipx $*" >> "$BRNRD_TRACE"
exit 1
"""

REFUSES = """
printf '%s\\n' "unexpected $0 $*" >> "$BRNRD_TRACE"
exit 1
"""


def _tool(bindir: Path, name: str, body: str) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body.lstrip("\n"), encoding="utf-8")
    path.chmod(0o755)


def _run(tmp_path: Path, tools: dict[str, str], shell: str = "/bin/sh") -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "bin"
    for name, body in tools.items():
        _tool(bindir, name, body)
    trace = tmp_path / "trace"
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(bindir), "/usr/bin", "/bin"))
    env["HOME"] = str(tmp_path / "home")
    env["BRNRD_INSTALL_DRY_RUN"] = "1"
    env["BRNRD_TRACE"] = str(trace)
    result = subprocess.run(
        [shell, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    result.trace = trace.read_text(encoding="utf-8") if trace.exists() else ""
    return result


def _commands(result: subprocess.CompletedProcess[str]) -> list[str]:
    assert result.returncode == 0, result.stderr
    assert result.trace == ""
    lines = result.stdout.splitlines()
    assert lines[-1] == "Next: brnrd setup"
    return lines[:-1]


@pytest.mark.parametrize("shell", SHELLS)
def test_dry_run_installs_with_uv_when_uv_is_present(tmp_path, shell):
    commands = _commands(_run(tmp_path, {"uv": UV_ABSENT, "pipx": REFUSES, "curl": REFUSES}, shell))
    assert commands == [
        "uv tool install brnrd",
        "uv tool update-shell",
        "brnrd --version",
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_dry_run_upgrades_when_uv_already_has_brnrd(tmp_path, shell):
    commands = _commands(_run(tmp_path, {"uv": UV_PRESENT, "pipx": REFUSES}, shell))
    assert commands == [
        "uv tool upgrade brnrd",
        "uv tool update-shell",
        "brnrd --version",
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_dry_run_installs_with_pipx_when_only_pipx_is_present(tmp_path, shell):
    commands = _commands(_run(tmp_path, {"pipx": PIPX_ABSENT, "curl": REFUSES}, shell))
    assert commands == [
        "pipx install brnrd",
        "pipx ensurepath",
        "brnrd --version",
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_dry_run_upgrades_when_pipx_already_has_brnrd(tmp_path, shell):
    commands = _commands(_run(tmp_path, {"pipx": PIPX_PRESENT, "curl": REFUSES}, shell))
    assert commands == [
        "pipx upgrade brnrd",
        "pipx ensurepath",
        "brnrd --version",
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_dry_run_bootstraps_uv_when_neither_is_present(tmp_path, shell):
    # No uv and no pipx on PATH. curl is present and refuses, so a dry run
    # that actually downloads fails the trace instead of touching the network.
    commands = _commands(_run(tmp_path, {"curl": REFUSES}, shell))
    assert commands == [
        UV_BOOTSTRAP,
        "uv tool install brnrd",
        "uv tool update-shell",
        "brnrd --version",
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_uv_wins_when_both_uv_and_pipx_are_present(tmp_path, shell):
    commands = _commands(_run(tmp_path, {"uv": UV_ABSENT, "pipx": REFUSES}, shell))
    assert commands[0] == "uv tool install brnrd"
    assert all(not line.startswith("pipx") for line in commands)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_failed_probe_exits_nonzero_with_one_sentence(tmp_path, shell):
    result = _run(tmp_path, {"uv": REFUSES}, shell)
    assert result.returncode != 0
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert lines == [
        "uv tool list failed; run uv tool install brnrd by hand, or uv tool upgrade brnrd if it is already installed."
    ]


def test_shellcheck_sh_when_installed():
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(
        [shellcheck, "-s", "sh", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_quickstart_and_install_page_lead_with_the_curl_line():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = (ROOT / "docs" / "src" / "content" / "docs" / "getting-started" / "install.md").read_text(
        encoding="utf-8"
    )
    assert CURL in install
    start = readme.index("## ✦ Quickstart")
    window = readme[start:readme.index("## ✦ What arrives", start)]
    assert window.index(CURL) < window.index("npm install -g brnrd")
    assert window.index(CURL) < window.index("uv tool install brnrd")
    assert window.index(CURL) < window.index("pipx install brnrd")
