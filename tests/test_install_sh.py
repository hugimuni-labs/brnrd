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
SYSTEM = ("sh", "grep", "sed", "wc", "tr", "mktemp", "rm", "id")
SHELLS = [path for path in ("/bin/sh", "/bin/dash") if os.path.exists(path)]
CURL = "curl -LsSf https://hugimuni-labs.github.io/brnrd/install.sh | sh"
UV_BOOTSTRAP = "curl -LsSf https://astral.sh/uv/install.sh | sh"

UV_ABSENT = """
case "$*" in
  "tool dir --bin") printf '%s\\n' "$HOME/.local/bin"; exit 0 ;;
  "tool dir") printf '%s\\n' "$HOME/.local/share/uv/tools"; exit 0 ;;
esac
if [ "$1" = "tool" ] && [ "$2" = "list" ]; then
  exit 0
fi
printf '%s\\n' "unexpected uv $*" >> "$BRNRD_TRACE"
exit 1
"""

UV_PRESENT = """
case "$*" in
  "tool dir --bin") printf '%s\\n' "$HOME/.local/bin"; exit 0 ;;
  "tool dir") printf '%s\\n' "$HOME/.local/share/uv/tools"; exit 0 ;;
esac
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

# ps -U <uid> -o pid=,args=: this user's processes, from FAKE_PS.
PS = """
printf '%s' "${FAKE_PS:-}"
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


def _system(tmp_path: Path) -> Path:
    # Only the utilities the script uses: a runner's /usr/bin holds gh (and
    # may hold uv or pipx), which a test needs absent.
    sysbin = tmp_path / "system"
    sysbin.mkdir()
    for name in SYSTEM:
        found = shutil.which(name, path="/usr/bin:/bin")
        assert found, name
        (sysbin / name).symlink_to(found)
    return sysbin


def _run(
    tmp_path: Path,
    tools: dict[str, str],
    shell: str = "/bin/sh",
    args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "bin"
    for name, body in tools.items():
        _tool(bindir, name, body)
    trace = tmp_path / "trace"
    base = os.environ.copy()
    base.pop("PYTHONPATH", None)
    base.update(env or {})
    env = base
    env["PATH"] = os.pathsep.join((str(bindir), str(_system(tmp_path))))
    env["HOME"] = str(tmp_path / "home")
    env["BRNRD_INSTALL_DRY_RUN"] = "1"
    env["BRNRD_TRACE"] = str(trace)
    result = subprocess.run(
        [shell, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    result.trace = trace.read_text(encoding="utf-8") if trace.exists() else ""
    return result


GIT_CLEAN = """
case "$*" in
  --version) exit 0 ;;
  *"symbolic-ref -q --short HEAD") printf '%s\\n' "${FAKE_BRANCH:-main}"; exit 0 ;;
  *"status --porcelain --untracked-files=no") printf '%s' "${FAKE_DIRTY:-}"; exit 0 ;;
esac
printf '%s\\n' "unexpected git $*" >> "$BRNRD_TRACE"
exit 1
"""

GH_SIGNED_IN = """
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  exit 0
fi
printf '%s\\n' "unexpected gh $*" >> "$BRNRD_TRACE"
exit 1
"""

GH_SIGNED_OUT = """
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  exit 1
fi
printf '%s\\n' "unexpected gh $*" >> "$BRNRD_TRACE"
exit 1
"""

DEV_TOOLS = {
    "git": GIT_CLEAN,
    "uv": UV_ABSENT,
    "gh": GH_SIGNED_IN,
    "claude": REFUSES,
    "pipx": PIPX_ABSENT,
    "ps": PS,
    "curl": REFUSES,
}


def _dev_tail(clone: Path) -> list[str]:
    return [
        f"uv tool install --editable {clone}",
        "uv tool update-shell",
        "brnrd loom install --dev --dry-run",
        "brnrd loom install --dev",
        "brnrd loom status",
    ]


def _clone(tmp_path: Path) -> Path:
    clone = tmp_path / "home" / ".brnrd" / "src"
    (clone / ".git").mkdir(parents=True)
    return clone


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


@pytest.mark.parametrize("shell", SHELLS)
def test_dev_clones_main_then_installs_it_editable_and_starts_the_loom(tmp_path, shell):
    result = _run(tmp_path, DEV_TOOLS, shell, args=("--dev",))
    clone = tmp_path / "home" / ".brnrd" / "src"
    assert result.stderr == ""
    assert _commands(result) == [
        f"git clone https://github.com/hugimuni-labs/brnrd {clone}",
        *_dev_tail(clone),
    ]


def test_dev_env_var_means_the_flag_and_dev_dir_moves_the_clone(tmp_path):
    clone = tmp_path / "elsewhere"
    result = _run(tmp_path, DEV_TOOLS, env={"BRNRD_INSTALL_DEV": "1", "BRNRD_DEV_DIR": str(clone)})
    assert _commands(result) == [
        f"git clone https://github.com/hugimuni-labs/brnrd {clone}",
        *_dev_tail(clone),
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_dev_rerun_fast_forwards_a_clean_clone_on_main(tmp_path, shell):
    clone = _clone(tmp_path)
    # An untracked file does not count, as in the loom's own rule.
    result = _run(tmp_path, DEV_TOOLS, shell, args=("--dev",), env={"FAKE_DIRTY": ""})
    assert _commands(result) == [
        f"git -C {clone} pull --ff-only origin main",
        *_dev_tail(clone),
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_dev_rerun_refuses_a_dirty_clone_with_one_sentence(tmp_path, shell):
    clone = _clone(tmp_path)
    result = _run(tmp_path, DEV_TOOLS, shell, args=("--dev",),
                  env={"FAKE_DIRTY": " M src/brr/cli.py\nM  README.md\n"})
    assert result.returncode != 0
    assert result.trace == ""
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"checkout {clone} has 2 uncommitted change(s) (M src/brr/cli.py): not touched; "
        "commit or stash them and run this again."
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_dev_rerun_refuses_a_clone_on_another_branch(tmp_path, shell):
    clone = _clone(tmp_path)
    result = _run(tmp_path, DEV_TOOLS, shell, args=("--dev",), env={"FAKE_BRANCH": "wip"})
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"checkout {clone} is on branch wip, not main: not touched; "
        f"switch it back with git -C {clone} switch main and run this again."
    ]


def test_dev_refuses_a_directory_that_is_not_a_checkout(tmp_path):
    clone = tmp_path / "home" / ".brnrd" / "src"
    clone.mkdir(parents=True)
    result = _run(tmp_path, DEV_TOOLS, args=("--dev",))
    assert result.returncode != 0
    assert result.stderr.splitlines() == [
        f"{clone} exists and is not a git checkout: not touched; "
        "move it away or set BRNRD_DEV_DIR to another directory."
    ]


def test_dev_bootstraps_uv_when_it_is_missing(tmp_path):
    tools = {name: body for name, body in DEV_TOOLS.items() if name != "uv"}
    commands = _commands(_run(tmp_path, tools, args=("--dev",)))
    assert commands[:2] == [UV_BOOTSTRAP, f"git clone https://github.com/hugimuni-labs/brnrd {tmp_path / 'home' / '.brnrd' / 'src'}"]


def test_an_unknown_option_is_refused(tmp_path):
    result = _run(tmp_path, DEV_TOOLS, args=("--stable",))
    assert result.returncode != 0
    assert result.stderr.splitlines() == ["Unknown option --stable; the only option is --dev."]


@pytest.mark.parametrize("args", [(), ("--dev",)])
def test_pythonpath_warns_on_both_paths_and_does_not_stop_the_install(tmp_path, args):
    result = _run(tmp_path, {**DEV_TOOLS, "uv": UV_ABSENT}, args=args, env={"PYTHONPATH": "/x/brnrd/src"})
    _commands(result)
    assert result.stderr.splitlines() == [
        "warning: PYTHONPATH is set (/x/brnrd/src), so brnrd may run another checkout's code "
        "instead of this install; unset it here and in your shell profile."
    ]


def test_dev_warns_when_gh_is_missing(tmp_path):
    tools = {name: body for name, body in DEV_TOOLS.items() if name != "gh"}
    result = _run(tmp_path, tools, args=("--dev",))
    _commands(result)
    assert result.stderr.splitlines() == [
        "warning: gh is not installed, and following main reads CI through it; "
        "install the GitHub CLI and run gh auth login."
    ]


def test_dev_warns_when_gh_is_signed_out(tmp_path):
    result = _run(tmp_path, {**DEV_TOOLS, "gh": GH_SIGNED_OUT}, args=("--dev",))
    _commands(result)
    assert result.stderr.splitlines() == [
        "warning: gh auth status fails, and following main reads CI through it; run gh auth login."
    ]


def test_dev_warns_when_no_body_cli_is_on_path(tmp_path):
    tools = {name: body for name, body in DEV_TOOLS.items() if name != "claude"}
    result = _run(tmp_path, tools, args=("--dev",))
    _commands(result)
    assert result.stderr.splitlines() == [
        "warning: neither claude nor codex is on PATH, and loom install picks the loom's body from them; "
        "install and sign in to one first."
    ]


@pytest.mark.parametrize("args", [(), ("--dev",)])
@pytest.mark.parametrize("unit", ["Library/LaunchAgents/dev.brnrd.brr.plist", ".config/systemd/user/brr.service"])
def test_an_old_daemon_service_warns_on_both_paths(tmp_path, args, unit):
    path = tmp_path / "home" / unit
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    result = _run(tmp_path, DEV_TOOLS, args=args)
    _commands(result)
    assert result.stderr.splitlines() == [
        f"warning: an old brnrd daemon is installed ({path}) and runs beside the loom; "
        "remove it with brnrd daemon uninstall."
    ]


def test_dev_warns_when_pipx_holds_a_brnrd_release(tmp_path):
    result = _run(tmp_path, {**DEV_TOOLS, "pipx": PIPX_PRESENT}, args=("--dev",))
    _commands(result)
    assert result.stderr.splitlines() == [
        "warning: pipx has a brnrd release installed, and its brnrd command collides with this install; "
        "run pipx uninstall brnrd."
    ]


def test_dev_warns_when_another_brnrd_shadows_the_uv_tool_bin(tmp_path):
    result = _run(tmp_path, {**DEV_TOOLS, "brnrd": REFUSES}, args=("--dev",))
    _commands(result)
    ubin = tmp_path / "home" / ".local" / "bin"
    assert result.stderr.splitlines() == [
        f"warning: brnrd on PATH is {tmp_path / 'bin' / 'brnrd'}, not {ubin}/brnrd, so it shadows this install; "
        f"remove it or put {ubin} first on PATH."
    ]


def test_dev_warns_about_a_loom_running_from_another_install_only(tmp_path):
    ours = tmp_path / "home" / ".local" / "share" / "uv" / "tools" / "brnrd" / "bin" / "python"
    ps = (f"  101 /opt/py/bin/python -m brr.loom.runtime loom --home /h\n"
          f"  102 {ours} -m brr.loom.runtime loom --home /h\n"
          f"  103 /bin/zsh\n")
    result = _run(tmp_path, DEV_TOOLS, args=("--dev",), env={"FAKE_PS": ps})
    _commands(result)
    assert result.stderr.splitlines() == [
        "warning: a loom from another install is running (pid 101: /opt/py/bin/python); loom install --dev "
        "replaces the loom service, but a loom started by hand keeps running beside it: stop it with that "
        "install's brnrd loom stop, or kill 101."
    ]


def test_the_release_path_does_not_ask_about_gh(tmp_path):
    result = _run(tmp_path, {"uv": UV_ABSENT, "gh": REFUSES})
    _commands(result)
    assert result.stderr == ""


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
