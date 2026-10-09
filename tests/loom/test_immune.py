"""core/immune, judged through a real bare repo and its pre-receive hook."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from brr.loom.runtime.merge import install_pre_receive

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "src" / "brr" / "loom" / "runtime" / "seed"
_DISCOVERY = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_NAMESPACE",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}
_ALWAYS_EXEC = {
    "core/immune",
    "core/immune.d/50-core-notice",
}


def _shells() -> list[str]:
    found = ["sh"]
    if shutil.which("dash"):
        found.append("dash")
    return found


@pytest.fixture(params=_shells())
def immune_sh(request, monkeypatch):
    """Run the immune file once under sh and, when present, again under dash."""

    monkeypatch.setenv("LOOM_IMMUNE_SH", request.param)
    return request.param


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _DISCOVERY}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def git(repo: Path | None, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git"]
    if repo is not None:
        cmd += ["-C", os.fspath(repo)]
    cmd += list(args)
    proc = subprocess.run(cmd, env=_env(), capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise AssertionError(f"{cmd}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def write(repo: Path, rel: str, text: str, *, exe: bool = False) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if exe:
        path.chmod(0o755)


def copy_seed(repo: Path, *, notice: bool = True) -> None:
    for src in SEED.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(SEED).as_posix()
        if not notice and rel == "core/immune.d/50-core-notice":
            continue
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if rel in _ALWAYS_EXEC or src.stat().st_mode & 0o111:
            dest.chmod(0o755)

    # 20-labels fails closed: only an enrolled address may push unlabeled.
    # These fixtures push as the test's git identity, so enroll it.
    email = git(None, "config", "--global", "user.email").stdout.strip()
    listing = repo / "core" / "loom" / "people-commit"
    listing.write_text(listing.read_text(encoding="utf-8") + email + "\n", encoding="utf-8")


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def published(tmp: Path, *, notice: bool = True) -> tuple[Path, Path]:
    bare = tmp / "self.git"
    work = tmp / "work"
    git(None, "init", "--bare", "-b", "main", os.fspath(bare))
    git(None, "init", "-b", "main", os.fspath(work))
    copy_seed(work, notice=notice)
    write(work, "memory/scars/x.md", "base\n")
    write(work, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nalpha line\n")
    commit(work, "seed")
    git(work, "remote", "add", "origin", os.fspath(bare))
    install_pre_receive(bare)
    first = git(work, "push", "origin", "HEAD:main", check=False)
    assert first.returncode == 0, first.stderr + first.stdout
    return bare, work


def _blob(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stderr or "") + (proc.stdout or "")


def test_sh_n_on_immune_and_the_shim(tmp_path: Path, immune_sh: str) -> None:
    bare = tmp_path / "self.git"
    git(None, "init", "--bare", "-b", "main", os.fspath(bare))
    hook = install_pre_receive(bare)
    for path in (SEED / "core" / "immune", SEED / "core" / "immune.d" / "50-core-notice", hook):
        proc = subprocess.run([immune_sh, "-n", os.fspath(path)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


def test_empty_immune_d_fast_forward(tmp_path: Path, immune_sh: str) -> None:
    _bare, work = published(tmp_path, notice=False)
    write(work, "memory/scars/x.md", "base\nmore\n")
    commit(work, "a scar")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode == 0, _blob(proc)


def test_force_push_to_main_is_refused(tmp_path: Path, immune_sh: str) -> None:
    bare, work = published(tmp_path)
    base = git(work, "rev-parse", "HEAD").stdout.strip()
    write(work, "memory/scars/x.md", "base\nsecond\n")
    commit(work, "second")
    tip = git(work, "rev-parse", "HEAD").stdout.strip()
    assert git(work, "push", "origin", "HEAD:main", check=False).returncode == 0
    git(work, "checkout", "-b", "diverge", base)
    write(work, "memory/scars/x.md", "base\ndiverged\n")
    commit(work, "diverge")
    forced = git(work, "push", "--force", "origin", "HEAD:main", check=False)
    assert forced.returncode != 0
    assert "non-fast-forward" in _blob(forced).lower()
    assert git(bare, "rev-parse", "main").stdout.strip() == tip


def test_deleting_main_is_refused(tmp_path: Path, immune_sh: str) -> None:
    bare, work = published(tmp_path)
    tip = git(bare, "rev-parse", "main").stdout.strip()
    proc = git(work, "push", "origin", ":main", check=False)
    assert proc.returncode != 0
    assert "refusing deletion of main" in _blob(proc)
    assert git(bare, "rev-parse", "main").stdout.strip() == tip


def test_strand_branch_passes_untouched(tmp_path: Path, immune_sh: str) -> None:
    bare, work = published(tmp_path)
    tip = git(bare, "rev-parse", "main").stdout.strip()
    write(work, "core/immune.d/40-fail", "#!/bin/sh\nprintf 'nope\\n' >&2\nexit 1\n", exe=True)
    commit(work, "a check that would refuse main")
    proc = git(work, "push", "origin", "HEAD:refs/heads/strand/z", check=False)
    assert proc.returncode == 0, _blob(proc)
    assert git(bare, "rev-parse", "main").stdout.strip() == tip


def test_failing_check_is_named_in_stderr(tmp_path: Path, immune_sh: str) -> None:
    _bare, work = published(tmp_path)
    write(work, "core/immune.d/40-fail", "#!/bin/sh\nprintf 'nope\\n' >&2\nexit 1\n", exe=True)
    commit(work, "failing check")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode != 0
    assert "40-fail: nope" in _blob(proc)


def test_two_failing_checks_one_refusal(tmp_path: Path, immune_sh: str) -> None:
    _bare, work = published(tmp_path)
    write(work, "core/immune.d/40-a", "#!/bin/sh\nprintf 'reason-a\\n' >&2\nexit 1\n", exe=True)
    write(work, "core/immune.d/41-b", "#!/bin/sh\nprintf 'reason-b\\n' >&2\nexit 1\n", exe=True)
    commit(work, "two failing checks")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    text = _blob(proc)
    assert proc.returncode != 0
    assert "40-a: reason-a" in text
    assert "41-b: reason-b" in text
    assert text.lower().count("hook declined") == 1


def test_blank_failure_is_still_named(tmp_path: Path, immune_sh: str) -> None:
    _bare, work = published(tmp_path)
    write(work, "core/immune.d/40-quiet", "#!/bin/sh\nexit 1\n", exe=True)
    commit(work, "quiet check")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode != 0
    assert "40-quiet: failed" in _blob(proc)


def test_hook_runs_the_pushed_immune_not_the_one_on_disk(tmp_path: Path, immune_sh: str) -> None:
    bare, work = published(tmp_path)
    tip = git(bare, "rev-parse", "main").stdout.strip()
    decoy = bare / "core" / "immune"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    decoy.chmod(0o755)
    write(work, "core/immune.d/40-fail", "#!/bin/sh\nprintf 'from-the-commit\\n' >&2\nexit 1\n", exe=True)
    commit(work, "stricter immune")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode != 0
    assert "40-fail: from-the-commit" in _blob(proc)
    assert git(bare, "rev-parse", "main").stdout.strip() == tip


def test_first_push_with_zero_old(tmp_path: Path, immune_sh: str) -> None:
    bare = tmp_path / "self.git"
    work = tmp_path / "work"
    git(None, "init", "--bare", "-b", "main", os.fspath(bare))
    git(None, "init", "-b", "main", os.fspath(work))
    copy_seed(work)
    write(work, "core/identity.md", "who\n")
    commit(work, "seed")
    git(work, "remote", "add", "origin", os.fspath(bare))
    install_pre_receive(bare)
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    text = _blob(proc)
    assert proc.returncode == 0, text
    assert "core/identity.md" in text
    assert "core/immune" in text


def test_core_notice_prints_changed_core_paths(tmp_path: Path, immune_sh: str) -> None:
    _bare, work = published(tmp_path)
    old = git(work, "rev-parse", "HEAD").stdout.strip()
    write(work, "core/identity.md", "who\n")
    write(work, "memory/stance.md", "stance\n")
    commit(work, "touch core and memory")
    new = git(work, "rev-parse", "HEAD").stdout.strip()
    notice = work / "core" / "immune.d" / "50-core-notice"
    direct = subprocess.run(
        [immune_sh, os.fspath(notice), old, new],
        cwd=work,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.returncode == 0, direct.stderr
    assert direct.stdout.strip() == "core changed: core/identity.md"
    gated = subprocess.run(
        [immune_sh, os.fspath(work / "core" / "immune"), old, new],
        cwd=work,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert gated.returncode == 0, gated.stderr
    assert "core changed: core/identity.md" in gated.stdout


def test_core_notice_is_silent_when_core_is_untouched(tmp_path: Path, immune_sh: str) -> None:
    _bare, work = published(tmp_path)
    old = git(work, "rev-parse", "HEAD").stdout.strip()
    write(work, "memory/stance.md", "stance\n")
    commit(work, "memory only")
    new = git(work, "rev-parse", "HEAD").stdout.strip()
    direct = subprocess.run(
        [immune_sh, os.fspath(work / "core" / "immune.d" / "50-core-notice"), old, new],
        cwd=work,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.returncode == 0
    assert direct.stdout == ""


def test_a_push_that_deletes_a_check_is_judged_by_it(tmp_path: Path, immune_sh: str) -> None:
    _bare, work = published(tmp_path)
    guard = "#!/bin/sh\nif git cat-file -e \"$2:forbidden\" 2>/dev/null; then printf 'forbidden is here\\n' >&2; exit 1; fi\n"
    write(work, "core/immune.d/40-guard", guard, exe=True)
    commit(work, "add the guard")
    assert git(work, "push", "origin", "HEAD:main", check=False).returncode == 0
    (work / "core" / "immune.d" / "40-guard").unlink()
    write(work, "forbidden", "x\n")
    commit(work, "drop the guard and do the thing it guards")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode != 0
    assert "old/40-guard: forbidden is here" in _blob(proc)


def test_a_push_that_rewrites_immune_to_exit_0_is_judged_by_the_old_one(tmp_path: Path, immune_sh: str) -> None:
    _bare, work = published(tmp_path)
    guard = "#!/bin/sh\nif git cat-file -e \"$2:forbidden\" 2>/dev/null; then printf 'forbidden is here\\n' >&2; exit 1; fi\n"
    write(work, "core/immune.d/40-guard", guard, exe=True)
    commit(work, "add the guard")
    assert git(work, "push", "origin", "HEAD:main", check=False).returncode == 0
    write(work, "core/immune", "#!/bin/sh\nexit 0\n", exe=True)
    write(work, "forbidden", "x\n")
    commit(work, "neuter immune")
    proc = git(work, "push", "origin", "HEAD:main", check=False)
    assert proc.returncode != 0
    assert "forbidden is here" in _blob(proc)


@pytest.mark.parametrize("first, incumbent", [(False, None), (False, ""), (False, "exit 1\n"), (True, None), (True, "")])
def test_ci_uses_the_incumbent_and_refuses_missing_or_empty_scripts(tmp_path: Path, first: bool, incumbent: str | None) -> None:
    work = tmp_path / "work"
    git(None, "init", "-b", "main", os.fspath(work))
    write(work, "marker", "base\n")
    if incumbent is not None:
        write(work, "core/immune", incumbent)
    commit(work, "incumbent")
    old = git(work, "rev-parse", "HEAD").stdout.strip()
    if first:
        old = "0" * 40
    else:
        # A new permissive script cannot judge a missing/empty/refusing old one.
        write(work, "core/immune", "exit 0\n")
        commit(work, "incoming")
    new = git(work, "rev-parse", "HEAD").stdout.strip()
    workflow = (SEED / "ci/immune.yml").read_text(encoding="utf-8")
    script = textwrap.dedent(workflow.split("        run: |\n", 1)[1])
    for expression, value in {"github.event_name": "push", "github.event.before": old,
                              "github.sha": new, "github.event.pull_request.base.sha": old,
                              "github.event.pull_request.head.sha": new}.items():
        script = script.replace("${{ " + expression + " }}", value)
    env = _env()
    env["RUNNER_TEMP"] = str(tmp_path)
    result = subprocess.run(["sh", "-c", script], cwd=work, env=env, capture_output=True, text=True)
    assert result.returncode != 0, result.stdout
    if incumbent is None or incumbent == "":
        assert "immune: core/immune missing at" in result.stderr
