"""A project repo is the room's clone of the self, with another source.

One bare mirror per directory name lives at ``home/repos/<name>.git``.
A second source for that name gets no checkout. A body start fetches the
mirror, then (once per room) clones ``--shared`` into
``rooms/<strand>/<name>`` on ``loom/<thread>``.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from .config import load_config
from .home import Home
from .selfrepo import SelfError, git, pin_clone

_AUTHOR = re.compile(r"^(.+?) <([^<>]+)>$")
# Not a remote. git push origin echoes it, which is the refusal a reader sees.
_PUSH_REFUSAL = "use brnrd publish"
# The wake's last repo line. Without it a body meets the verb only as a refusal.
PUBLISH_HINT = ("to send a repo's branch out: `python -m brr.loom.runtime publish <name>`; "
                "git push is refused\n")


class AuthorError(ValueError):
    """The home's author cannot start a body. ``enrolled`` picks the notice."""

    def __init__(self, why: str, *, enrolled: bool = False) -> None:
        super().__init__(why)
        self.enrolled = enrolled


def author_identity(root: Path) -> tuple[str, str] | None:
    """Home config ``author``, as ``(name, email)``. Unset is none.

    A value that is not ``Name <email>`` is refused.
    """
    raw = load_config(root).author
    if raw is None:
        return None
    match = _AUTHOR.fullmatch(raw)
    if match is None or not match.group(1).strip() or not match.group(2).strip():
        raise AuthorError(f"author must be 'Name <email>': {raw!r}")
    return match.group(1).strip(), match.group(2).strip()


def _enrolled_on_main(self_dir: Path) -> set[str]:
    """Emails in ``core/loom/people-commit`` at ``main``. The worktree does not count.

    Same line rules as ``20-labels``: blank lines and ``#`` comments are ignored.
    """
    if not (self_dir / ".git").exists():
        return set()
    proc = git(self_dir, "show", "main:core/loom/people-commit", check=False)
    if proc.returncode != 0:
        return set()
    return {
        line.strip()
        for line in proc.stdout.split("\n")
        if line.strip() and not line.strip().startswith("#")
    }


def require_body_author(home: Home) -> None:
    """A malformed author, or one enrolled on main, does not start a body.

    Enrollment is the unlabeled lane to main. The body's environment would
    hand that lane to every repo it touches.
    """
    identity = author_identity(home.root)
    if identity is None:
        return
    email = identity[1]
    if email in _enrolled_on_main(home.root / "self"):
        raise AuthorError(f"author {email} is an enrolled person", enrolled=True)


def _bare(path: Path) -> bool:
    return (path / "HEAD").is_file() and (path / "objects").is_dir()


def _source_key(source: str) -> str:
    """Ignore Git URL suffix spelling when comparing mirror sources."""
    return source.rstrip("/").removesuffix(".git")


def _mirror(home: Home, name: str, source: str) -> tuple[Path | None, str | None]:
    """The bare mirror, fetched. ``(None, "fetch failed")`` when it cannot be created.

    A mirror is one source. ``(None, "holds:<url>")`` when this directory
    already holds a different one: the caller makes no checkout.
    """
    path = home.root / "repos" / f"{name}.git"
    created = False
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            git(path.parent, "clone", "--bare", "--", source, str(path))
        except SelfError:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            return None, "fetch failed"
        created = True
    elif not _bare(path):
        raise SelfError(f"{path} exists and is not a mirror; refusing to overwrite")
    else:
        held = git(path, "remote", "get-url", "origin", check=False)
        other = held.stdout.strip() if held.returncode == 0 else ""
        if _source_key(other) != _source_key(source):
            return None, f"holds:{other or 'an unknown source'}"
    # Auto-gc can delete objects a --shared room still borrows once this
    # mirror's branch no longer reaches them.
    git(path, "config", "gc.auto", "0")
    git(path, "config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*")
    git(path, "config", "credential.helper", "")
    if created:
        return path, None
    try:
        git(path, "fetch", "origin")
    except SelfError:
        return path, "fetch failed"
    return path, None


def _ref(repo: Path, ref: str) -> bool:
    return git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def _cut_from(dest: Path, thread: str, parent: str | None, name: str | None = None) -> str:
    """Where ``loom/<thread>`` starts: published branch, parent room, else default."""
    branch = f"loom/{thread}"
    remote = f"refs/remotes/origin/{branch}"
    if _ref(dest, remote):
        git(dest, "checkout", "-B", branch, f"origin/{branch}")
        return f"origin/{branch}"
    if parent:
        parent_checkout = dest.parent.parent / parent / (name or dest.name)
        if (parent_checkout / ".git").exists():
            proc = git(dest, "fetch", str(parent_checkout), branch, check=False)
            if proc.returncode == 0:
                git(dest, "checkout", "-B", branch, "FETCH_HEAD")
                return f"parent {parent}"
    default = git(dest, "symbolic-ref", "--short", "HEAD").stdout.strip()
    git(dest, "checkout", "-B", branch)
    return f"origin/{default}" if default else "the default branch"


def prepare_repos(home: Home, strand: str, thread: str,
                  declared: list[tuple[str, str]], parent: str | None,
                  ) -> tuple[str, tuple[str, ...]]:
    """Fetch each mirror and open a missing checkout.

    Returns the wake text, one line per repo, and the lines whose fetch
    failed. A checkout that already exists is left as it stands, including
    its commits. A failed fetch still starts the body: on the mirror's last
    state when there is one, and with no checkout when the mirror was never
    created. A mirror that already holds a different source makes no checkout.
    """
    if not declared:
        return "", ()
    lines: list[str] = []
    failures: list[str] = []
    for name, source in declared:
        mirror, failed = _mirror(home, name, source)
        dest = home.room(strand) / name
        if mirror is None and failed and failed.startswith("holds:"):
            line = f"repo {name}: the mirror holds {failed.removeprefix('holds:')}; no checkout"
            failures.append(line)
            lines.append(line + "\n")
            continue
        if (dest / ".git").exists():
            line = f"repo {name}: {dest} on loom/{thread}, left as it stands"
            if failed:
                line += "; fetch failed"
                failures.append(line)
            lines.append(line + "\n")
            continue
        if mirror is None:
            line = f"repo {name}: fetch failed; no checkout"
            failures.append(line)
            lines.append(line + "\n")
            continue
        if dest.exists():
            raise SelfError(f"{dest} exists and is not a clone; refusing to overwrite")
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Cloned beside and renamed in once cut, so a crash leaves no half
        # checkout that the next start would refuse to overwrite.
        tmp = Path(tempfile.mkdtemp(prefix=f".{name}-clone-", dir=dest.parent))
        try:
            git(home.root, "clone", "--shared", str(mirror), str(tmp))
            git(tmp, "remote", "set-url", "origin", "--", source)
            pin_clone(tmp)
            # A local source never asks a helper. This push URL is what `git push
            # origin` hits instead, for every source.
            git(tmp, "remote", "set-url", "--push", "origin", _PUSH_REFUSAL)
            cut = _cut_from(tmp, thread, parent, name)
            os.rename(tmp, dest)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        line = f"repo {name}: {dest} on loom/{thread}, cut from {cut}"
        if failed:
            sha = git(mirror, "rev-parse", "--short", "HEAD").stdout.strip()
            line += f"; fetch failed, on the last fetched state {sha}"
            failures.append(line)
        lines.append(line + "\n")
    return "".join(lines), tuple(failures)
