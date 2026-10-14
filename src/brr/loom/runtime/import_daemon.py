"""Refresh a self from six named daemon files, through its incumbent immune.

The daemon remains live until cutover. This is a snapshot, not a directory
mirror: absent sources refuse the import, and nothing is ever deleted.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

from .repos import author_identity
from .selfrepo import AUTHOR_EMAIL, AUTHOR_NAME, SelfError, _env_without_pin, _segment, git


def _plain_path(root: Path, relative: str) -> Path:
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise SelfError(f"import-daemon: symlink refused: {path}")
    return path


def import_daemon(home: Path, account: Path, person: str) -> str:
    """Commit changed allowlisted files only; a refusal leaves the self untouched."""
    _segment(person, "person")
    home, account = Path(home).resolve(), Path(account).resolve()
    self_dir = _plain_path(home, "self")
    if not (self_dir / ".git").exists():
        raise SelfError(f"no self at {self_dir}; init first")
    if git(self_dir, "rev-parse", "--show-toplevel").stdout.strip() != str(self_dir):
        raise SelfError(f"not a self repository: {self_dir}")
    if git(self_dir, "branch", "--show-current").stdout.strip() != "main":
        raise SelfError("import-daemon: self must have main checked out")
    if git(self_dir, "status", "--porcelain").stdout:
        raise SelfError("import-daemon: self is dirty; commit its edits first")
    sources = [(Path(__file__).resolve().parents[2], "prompts/identity-core.md", "core/identity.md")]
    sources.extend((account, f"home/dominion/{name}.md", f"memory/{name}.md")
                   for name in ("notebook", "position", "playbook", "pitfalls"))
    sources.append((account, "home/surface/workflow.md", f"people/{person}/agreement.md"))
    snapshot = {}
    for root, source, target in sources:
        snapshot[target] = _plain_path(root, source).read_bytes()
        _plain_path(self_dir, target)
    changed = sorted(name for name, data in snapshot.items()
                     if not (self_dir / name).is_file() or (self_dir / name).read_bytes() != data)
    if not changed:
        return f"import-daemon: unchanged {self_dir}"
    name, email = author_identity(home) or (AUTHOR_NAME, AUTHOR_EMAIL)
    env = _env_without_pin()
    env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
               GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    old = git(self_dir, "rev-parse", "HEAD").stdout.strip()
    with tempfile.TemporaryDirectory(prefix="loom-import-daemon-") as temp:
        clone = Path(temp) / "self"
        git(home, "clone", "--shared", "--branch", "main", str(self_dir), str(clone))
        if git(clone, "rev-parse", "HEAD").stdout.strip() != old:
            raise SelfError("import-daemon: self moved during snapshot; rerun")
        for target in changed:
            path = _plain_path(clone, target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(snapshot[target])
        git(clone, "add", "--", *changed)
        git(clone, "commit", "-m", "Import daemon resident files.\n\n"
            "Refresh the named sources while the daemon remains live.\n\n"
            + "\n".join(changed) + "\n\n"
            "Loom-Strand: import-daemon\nLoom-Label: taint=0; audience=self\n", env=env)
        new = git(clone, "rev-parse", "HEAD").stdout.strip()
        # A local commit does not run pre-receive. Judge it explicitly with
        # the incumbent's committed script before touching the home's ref.
        mode = git(clone, "ls-tree", old, "--", "core/immune").stdout.split()
        if not mode or mode[0] != "100755":
            raise SelfError("import-daemon: incumbent core/immune missing or not executable")
        immune = Path(temp) / "immune"
        immune.write_text(git(clone, "show", f"{old}:core/immune").stdout, encoding="utf-8")
        immune.chmod(0o700)
        checked = subprocess.run([str(immune), old, new, "refs/heads/main"],
                                 cwd=clone, env=env, capture_output=True, text=True, timeout=60)
        if checked.returncode:
            raise SelfError("import-daemon: immune refused\n" + checked.stdout + checked.stderr)
        if checked.stdout:
            print(checked.stdout, end="")
        # Local fast-forward only. Existing receive hooks also run; neither
        # this clone nor the home's configured remote is ever pushed to a forge.
        git(clone, "push", "origin", "HEAD:refs/heads/main")
    return f"import-daemon: {new[:12]} {self_dir}\n" + "\n".join(changed)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime import-daemon")
    parser.add_argument("--home", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--person", required=True)
    args = parser.parse_args(argv)
    try:
        print(import_daemon(Path(args.home), Path(args.account), args.person))
    except (SelfError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
