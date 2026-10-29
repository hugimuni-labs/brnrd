"""A self is a git repo. ``init`` seeds it once; a room is a clone, not a worktree.

The wake recipe lives in the self after the copy (``seed/core/loom/wake``).
The runtime adds the current thread's wish projection to the recipe's output.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import seed_config

# Pinned parent git must not retarget a self. 10-readme does the opposite
# on purpose: a bare-repo hook finds the repo through GIT_DIR.
_PINNED_GIT = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

STATUSES = ("open", "live", "dormant", "settled", "retired")
TENSES = ("plan", "reference")
STATUS_WHY = "open|live|dormant|settled|retired"
TENSE_WHY = "plan|reference"
_EXECUTABLES = (
    "core/loom/wake",
    "core/loom/index",
    "core/immune.d/10-readme",
    "core/immune.d/20-labels",
    "core/immune.d/30-index",
    "core/immune.d/40-guards",
    "core/immune.d/40-links",
    "core/immune.d/45-scars",
)

AUTHOR_NAME = "brnrd-loom"
AUTHOR_EMAIL = "loom@localhost"


class ReadmeError(ValueError):
    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


class SelfError(RuntimeError):
    """A self operation refused. The message is the reason, already specific."""


def seed_root() -> Path:
    return Path(__file__).resolve().parent / "seed"


def identity_source() -> Path:
    """The product's character, seeded once as ``core/identity.md``.

    The daemon's inject stack reads the same file, so there is one copy.
    """
    return Path(__file__).resolve().parents[2] / "prompts" / "identity-core.md"


IDENTITY_NOTE = ("core/identity.md is who brnrd is: the product's character, now this self's. "
                 "Edit it or replace it; the next wake says the new thing.")


def _env_without_pin() -> dict[str, str]:
    env = os.environ.copy()
    for key in _PINNED_GIT:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Resolved rebases keep their message without waiting on an editor.
    env["GIT_EDITOR"] = "true"
    env["GIT_SEQUENCE_EDITOR"] = "true"
    return env


def git(cwd: Path, *args: str, check: bool = True, env: dict[str, str] | None = None,
        input: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args], cwd=cwd,
            env=_env_without_pin() if env is None else env,
            input=input, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise SelfError(f"git {' '.join(args)} timed out") from exc
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise SelfError(f"git {' '.join(args)} ({cwd}) -> {proc.returncode}: {detail}")
    return proc


# A forge's browse path for a folder on main; an origin elsewhere has no link.
_FORGES = {"github.com": "tree/main", "gitlab.com": "-/tree/main", "codeberg.org": "src/branch/main"}
_ORIGIN = re.compile(r"(?:(?:https?|ssh)://)?(?:[^@/]+@)?([^/:@]+)(?::\d+)?[:/]([^/]+)/([^/]+?)(?:\.git)?/?")


def forge_threads(self_dir: Path) -> str | None:
    """The self's threads folder on the forge its origin names, credentials dropped."""
    if not (self_dir / ".git").exists():
        return None
    url = git(self_dir, "remote", "get-url", "origin", check=False).stdout.strip()
    m = _ORIGIN.fullmatch(url)
    if not m or m[1].lower() not in _FORGES:
        return None
    return f"https://{m[1].lower()}/{m[2]}/{m[3]}/{_FORGES[m[1].lower()]}/threads"

def _segment(value: str, what: str | None = None) -> str | None:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        if what is not None:
            raise SelfError(f"{what} is not a single path segment: {value!r}")
        return None
    return value


def _key_ok(key: str) -> bool:
    if not key or not key[0].isalpha():
        return False
    return all(ch.isalnum() or ch in "-_" for ch in key)


def _unquote(value: str) -> str:
    if value.startswith('"') or value.endswith('"'):
        if len(value) < 2 or value[0] != '"' or value[-1] != '"' or '"' in value[1:-1]:
            raise ReadmeError(f"unclosed quote: {value}")
        return value[1:-1]
    return value


def _parse_list(value: str, key: str) -> list[str]:
    if not value.endswith("]"):
        raise ReadmeError(f"list unclosed: {key}")
    inner = value[1:-1].strip()
    if inner == "":
        return []
    items: list[str] = []
    for part in inner.split(","):
        piece = part.strip()
        if piece == "":
            raise ReadmeError(f"list malformed: {key}")
        items.append(_unquote(piece))
    return items


def _require(data: dict) -> None:
    ident = data.get("id")
    if not isinstance(ident, str) or ident == "":
        raise ReadmeError("id missing")
    status = data.get("status")
    if not isinstance(status, str) or status == "":
        raise ReadmeError("status missing")
    if status not in STATUSES:
        raise ReadmeError(f"status {status} is not one of {STATUS_WHY}")
    tense = data.get("tense")
    if not isinstance(tense, str) or tense == "":
        raise ReadmeError("tense missing")
    if tense not in TENSES:
        raise ReadmeError(f"tense {tense} is not one of {TENSE_WHY}")
    if tense == "reference" and status != "settled":
        raise ReadmeError("tense reference requires status settled")


def _headline(lines: list[str]) -> str:
    index = 0
    while index < len(lines) and lines[index].strip() == "":
        index += 1
    if index >= len(lines):
        raise ReadmeError("heading missing")
    line = lines[index]
    rest = line[2:] if line.startswith("# ") else ""
    if not line.startswith("# ") or rest[:1] == "#" or rest.strip() == "":
        raise ReadmeError("heading must be a single '# ' line")
    return rest.strip()


def parse_readme(text: str) -> dict:
    """Frontmatter plus ``headline``. Raises :class:`ReadmeError` with the why.

    ``id`` against the folder is not knowable from text. :func:`check_readme_file`
    adds it, and so does ``seed/core/immune.d/10-readme``.
    """
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ReadmeError("frontmatter missing")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ReadmeError("frontmatter unclosed") from None
    data: dict = {}
    for line in lines[1:end]:
        if line.strip() == "":
            continue
        if line[:1] in " \t" or ":" not in line:
            raise ReadmeError(f"frontmatter line not key: value: {line}")
        key, _, raw = line.partition(":")
        key = key.strip()
        if not _key_ok(key):
            raise ReadmeError(f"frontmatter line not key: value: {line}")
        if key in data:
            raise ReadmeError(f"duplicate key: {key}")
        value = raw.strip()
        # `for` names one brnrd, or is empty. A list is a different fact.
        if key == "for" and value.startswith("["):
            raise ReadmeError("for must be a string")
        data[key] = _parse_list(value, key) if value.startswith("[") else _unquote(value)
    _require(data)
    data["headline"] = _headline(lines[end + 1 :])
    return data


def check_readme_file(path: Path) -> dict:
    """``parse_readme`` plus ``id`` equals the folder when the file is ``README.md``."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ReadmeError(f"not utf-8: {exc}") from exc
    data = parse_readme(text)
    if Path(path).name == "README.md" and data["id"] != Path(path).parent.name:
        raise ReadmeError(f"id {data['id']} is not folder {Path(path).parent.name}")
    return data


def _copy_seed(dest: Path) -> None:
    root = seed_root()
    if not root.is_dir():
        raise SelfError(f"seed missing at {root}")
    for src in sorted(root.rglob("*")):
        rel = src.relative_to(root)
        target = dest / rel
        if src.is_symlink():
            raise SelfError(f"seed symlink refused: {rel.as_posix()}")
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    identity = identity_source()
    if not identity.is_file():
        raise SelfError(f"identity missing at {identity}")
    (dest / "core").mkdir(parents=True, exist_ok=True)
    shutil.copy2(identity, dest / "core" / "identity.md")
    for rel in _EXECUTABLES:
        path = dest / rel
        if not path.is_file():
            raise SelfError(f"seed is missing {rel}")
        path.chmod(path.stat().st_mode | 0o111)


def _agreement(person: str) -> str:
    return (
        f"# Agreement with {person}\n\n"
        f"This page is the agreement between {person} and the self.\n"
        "The person writes the clauses. The loom does not invent them.\n"
    )


def _commit(repo: Path, message: str, amend: bool = False) -> None:
    env = _env_without_pin()
    env["GIT_AUTHOR_NAME"] = AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = AUTHOR_EMAIL
    env["GIT_COMMITTER_NAME"] = AUTHOR_NAME
    env["GIT_COMMITTER_EMAIL"] = AUTHOR_EMAIL
    args = ("commit", "--amend", "--no-edit") if amend else ("commit", "-m", message)
    git(repo, *args, env=env)


def refresh_index(clone: Path) -> list[str]:
    """Run ``core/loom/index --write`` at ``HEAD`` and name what changed on disk.

    INDEX.md, CLAUDE.md and each ``memory/scars/<domain>/index.md`` are
    derived from the committed tree by the self's own recipe. A self without
    the recipe is left as it is. The caller commits the named paths; nothing
    else in the worktree is touched.
    """
    recipe = clone / "core" / "loom" / "index"
    if not recipe.is_file() or not os.access(recipe, os.X_OK):
        return []
    try:
        proc = subprocess.run([str(recipe), "--write"], cwd=clone, env=_env_without_pin(),
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SelfError(f"core/loom/index: {exc}") from exc
    if proc.returncode != 0:
        raise SelfError(f"core/loom/index --write -> {proc.returncode}: {proc.stderr.strip()}")
    status = git(clone, "status", "--porcelain", "-z", "--untracked-files=all", "--",
                 "INDEX.md", "CLAUDE.md", "memory/scars/*/index.md").stdout
    return [entry[3:] for entry in status.split("\0") if entry[3:]]


def _enroll_person_email(self_dir: Path) -> None:
    """List the person's git email in ``core/loom/people-commit``.

    Enrolled addresses may push without ``Loom-Label`` for hand commits.
    Body processes receive one identity from ``loom._env``: the home's
    ``author`` when set, otherwise the loom's. A home whose author email
    is enrolled does not start a body, so an unlabeled raw body push is
    refused by 20-labels. This is not a sandbox against a body deliberately
    overriding its identity or forging a label.
    """
    proc = git(self_dir, "config", "--global", "user.email", check=False)
    email = proc.stdout.strip()
    if proc.returncode != 0 or not email or email == AUTHOR_EMAIL:
        return
    path = self_dir / "core" / "loom" / "people-commit"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if email not in text.splitlines():
        path.write_text(text + email + "\n", encoding="utf-8")


def init_self(home: Path, person: str | None = None, remote: str | None = None) -> str:
    """Create ``home/self`` and return the one-line result.

    A finished self is a no-op. A failed attempt removes the directory it
    created, so the no-op cannot hide a push that never happened.
    """
    home = Path(home).resolve()
    self_dir = home / "self"
    if (self_dir / ".git").exists():
        seed_config(home)
        return f"self already exists: {self_dir}"
    if person is not None:
        _segment(person, "person")
    # A pairing that ran before any self left its files here: set them aside,
    # seed, and put back what the seed does not hold, uncommitted.
    aside = None
    if self_dir.exists():
        aside = Path(tempfile.mkdtemp(prefix="self-", dir=home))
        self_dir.rename(aside / "self")
    self_dir.mkdir(parents=True)
    try:
        _copy_seed(self_dir)
        if person:
            agreement = self_dir / "people" / person / "agreement.md"
            agreement.parent.mkdir(parents=True, exist_ok=True)
            agreement.write_text(_agreement(person), encoding="utf-8")
        _enroll_person_email(self_dir)
        git(self_dir, "init", "-b", "main")
        from .merge import install_pre_receive
        install_pre_receive(self_dir)
        git(self_dir, "add", "-A")
        _commit(
            self_dir,
            "Seed the self.\n\n"
            "The loom copies these files once; the self owns them after this commit.\n\n"
            # The seed is the self's first push; 20-labels fails closed on an
            # unlabeled commit, so the seed carries its own clean label.
            "Loom-Strand: seed\n"
            "Loom-Label: taint=0; audience=self\n",
        )
        # The index derives from the committed tree, so it is made after the
        # seed commit and folded into it: one commit, with its label.
        if refresh_index(self_dir):
            git(self_dir, "add", "-A")
            _commit(self_dir, "", amend=True)
        if remote:
            git(self_dir, "remote", "add", "origin", remote)
            git(self_dir, "push", "-u", "origin", "main")
    except Exception:
        shutil.rmtree(self_dir, ignore_errors=True)
        if aside is not None:
            (aside / "self").rename(self_dir)
            shutil.rmtree(aside, ignore_errors=True)
        raise
    if aside is not None:
        for src in sorted((aside / "self").rglob("*")):
            target = self_dir / src.relative_to(aside / "self")
            if src.is_file() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
        shutil.rmtree(aside, ignore_errors=True)
    seed_config(home)
    sha = git(self_dir, "rev-parse", "--short", "HEAD").stdout.strip()
    return f"initialized {self_dir} {sha}\n{IDENTITY_NOTE}"


def heal_home_thread(home: Path) -> str:
    """Put the seed's home thread README back on the self's ``main``; return the sha.

    The loom's own repair for a self whose home thread was deleted or never
    landed: letters with no other thread have nowhere to go. It lands through
    the self's pre-receive like any push, from a scratch clone, so a dirty
    ``home/self`` worktree is never touched. A home thread already on main is
    a no-op. Raises ``SelfError`` when there is no self or the push is refused.
    """
    from .home import HOME_THREAD
    from .merge import _push_lock

    self_dir = Path(home).resolve() / "self"
    if not (self_dir / ".git").exists():
        raise SelfError(f"no self at {self_dir}")
    path = f"threads/{HOME_THREAD}/README.md"
    if git(self_dir, "cat-file", "-e", f"main:{path}", check=False).returncode == 0:
        return git(self_dir, "rev-parse", "--short", "main").stdout.strip()
    seed = seed_root() / path
    scratch = Path(tempfile.mkdtemp(prefix="heal-", dir=self_dir.parent))
    try:
        clone = scratch / "self"
        git(scratch, "clone", "-q", "--branch", "main", str(self_dir), str(clone))
        target = clone / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed, target)
        git(clone, "add", "--", path)
        _commit(clone, f"Put {HOME_THREAD} back on main.\n\n"
                "Letters with no thread had nowhere to land; the loom restores the seed's.\n\n"
                "Loom-Strand: loom\n"
                "Loom-Label: taint=0; audience=self\n")
        changed = refresh_index(clone)
        if changed:
            git(clone, "add", "--", *changed)
            _commit(clone, "", amend=True)
        with _push_lock(clone):
            push = git(clone, "push", "-q", "origin", "HEAD:main", check=False)
        if push.returncode != 0:
            raise SelfError(f"push refused: {(push.stderr or push.stdout).strip()}")
        return git(clone, "rev-parse", "--short", "HEAD").stdout.strip()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def pin_clone(repo: Path) -> None:
    """Give the clone no credential helper.

    An empty ``credential.helper`` clears helpers inherited from the machine,
    including one scoped to a URL. Commit identity is the body's environment,
    which covers every clone it touches. A project checkout also refuses
    ``git push origin``; a local source never asks a helper, so that refusal
    is the checkout's push URL.
    """
    git(repo, "config", "credential.helper", "")


def room(home: Path, strand: str, gen: int | None = None) -> Path:
    """``home/rooms/<strand>/self``, a ``--shared`` clone on ``strand/<strand>``.

    A room that already exists keeps its commits and edits (a molt keeps its
    commits), once ``heal_room`` has repaired what a crash left (see there).
    The clone has an empty credential helper. Commit identity is the body's
    environment.
    """
    return ensure_room(home, strand, gen)[0]


def ensure_room(home: Path, strand: str, gen: int | None = None):
    """``room`` and the ``room.repaired`` fact it wrote, or None for a sound room."""
    home = Path(home).resolve()
    strand = _segment(strand, "strand")
    source = home / "self"
    if not (source / ".git").exists():
        raise SelfError(f"no self at {source}; init first")
    dest = home / "rooms" / strand / "self"
    report = None
    if (dest / ".git").exists() or dest.exists():
        report = heal_room(home, strand)
    if not (dest / ".git").exists():
        _create_room(source, dest, strand)
    if report is not None and report.get("aside"):
        _recover_branch(Path(report["aside"]), dest, strand, report)
    pin_clone(dest)
    fact = _record_repair(home, strand, gen, report) if report else None
    return dest, fact


def _create_room(source: Path, dest: Path, strand: str) -> None:
    """Clone beside ``dest`` and rename it in once it is whole.

    A crash before the rename leaves only a ``.self-clone-<pid>-*`` sibling,
    which the next creation removes once its pid is dead: ``dest`` is either
    absent or a finished clone on its strand branch.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in dest.parent.glob(".self-clone-*"):
        pid = stale.name.split("-")[2]
        if not (pid.isdigit() and _pid_alive(int(pid))):
            shutil.rmtree(stale, ignore_errors=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".self-clone-{os.getpid()}-", dir=dest.parent))
    try:
        git(dest.parent, "clone", "--shared", str(source), str(tmp))
        git(tmp, "checkout", "-b", f"strand/{strand}")
        # The README driver lives on the room that rebases, not on the bare self.
        from brr.loom.runtime.merge_driver import install_merge_driver
        install_merge_driver(tmp)
        pin_clone(tmp)
        os.rename(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# A loom git call times out at 60 s; a lock older than that with no body in the
# room has no holder left.
STALE_LOCK_SECONDS = 60


def heal_room(home: Path, strand: str) -> dict | None:
    """Repair what a crash left in an existing room, or move the room aside.

    Repairs are lossless by construction: a dead ``index.lock`` goes, an
    unreadable or empty index (while HEAD has a tree) is rebuilt from HEAD
    with the work tree untouched, and a tracked file that is missing, or empty
    where HEAD has content, is written back from HEAD. A file with content is
    never touched, nor is any ref. A room whose HEAD or tree cannot be read,
    or a ``dest`` with no ``.git``, is renamed to ``self.broken-<stamp>`` (kept,
    never deleted) for ``ensure_room`` to reclone. A live lock (a body in the
    room, or younger than any loom git call) leaves everything as it stands.
    Before the first change it reads the crash site (``_crash_site``): the
    strand branch's sha and what in the work tree differs from it.
    Returns None when nothing was repaired, else ``{"found": [...], "done": [...],
    "aside": path | None, "head": sha | None, "dirty": [...] | None, "dirty_count": n}``.
    """
    room_dir = Path(home) / "rooms" / strand
    dest = room_dir / "self"
    # A broken .git must fail, not send discovery up to some enclosing repo.
    env = dict(_env_without_pin(), GIT_DIR=str(dest / ".git"), GIT_WORK_TREE=str(dest))

    def hgit(*args, **kw):
        return git(dest, *args, check=False, env=env, **kw)

    found: list[str] = []
    done: list[str] = []
    crash: dict = {}

    def site() -> None:
        if not crash:
            crash.update(_crash_site(dest, strand))

    def report(**kw) -> dict:
        site()
        return {"found": found, "done": done, "aside": None, **crash, **kw}

    def aside(why: str) -> dict:
        site()
        found.append(why)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        target = room_dir / f"self.broken-{stamp}"
        n = 1
        while target.exists():
            n += 1
            target = room_dir / f"self.broken-{stamp}-{n}"
        os.rename(dest, target)
        done.append(f"moved the room aside to {target.name} and recloned from self")
        return report(aside=str(target))

    if not (dest / ".git").exists():
        return aside("the room has no .git (a clone that never finished)")
    lock = dest / ".git" / "index.lock"
    if lock.exists():
        from .loom import body_alive
        age = time.time() - lock.stat().st_mtime
        if body_alive(room_dir) or age < STALE_LOCK_SECONDS:
            return None  # Someone may still be writing: touch nothing.
        site()
        lock.unlink()
        found.append(f"a stale index.lock ({age:.0f}s old, no body in the room)")
        done.append("removed index.lock")
    head = hgit("rev-parse", "--verify", "-q", "HEAD^{tree}")
    if head.returncode != 0:
        return aside("HEAD has no readable commit")
    tree = hgit("ls-tree", "-r", "-z", "--long", "HEAD")
    if tree.returncode != 0:
        return aside(f"HEAD's tree is unreadable: {tree.stderr.strip()}")
    entries = []
    for row in tree.stdout.split("\0"):
        if not row:
            continue
        meta, path = row.split("\t", 1)
        mode, kind, _sha, size = meta.split()
        if kind == "blob":
            entries.append((path, mode, int(size) if size.isdigit() else 0))
    index = hgit("ls-files", "-z")
    if index.returncode != 0 or (entries and not index.stdout):
        found.append("an unreadable index" if index.returncode != 0
                     else f"an empty index while HEAD has {len(entries)} files")
        site()
        broken = dest / ".git" / "index"
        if broken.exists():
            os.replace(broken, dest / ".git" / "index.broken")
        if hgit("reset", "-q").returncode != 0:
            return aside("the index could not be rebuilt from HEAD")
        done.append("rebuilt the index from HEAD (work tree untouched)")
    missing, emptied = [], []
    for path, mode, size in entries:
        target = dest / path
        if not os.path.lexists(target):
            missing.append(path)
        elif mode != "120000" and size > 0 and target.is_file() and target.stat().st_size == 0:
            emptied.append(path)
    if missing or emptied:
        if missing:
            found.append(f"{len(missing)} tracked files missing ({_some(missing)})")
        if emptied:
            found.append(f"{len(emptied)} tracked files empty where HEAD has content ({_some(emptied)})")
        paths = missing + emptied
        site()
        restore = hgit("restore", "--source=HEAD", "--worktree",
                       "--pathspec-from-file=-", "--pathspec-file-nul",
                       input="\0".join(paths) + "\0")
        if restore.returncode != 0:
            return aside(f"tracked files could not be restored: {restore.stderr.strip()}")
        done.append(f"restored {len(paths)} files from HEAD")
    branch = f"strand/{strand}"
    if hgit("rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode:
        found.append(f"no {branch} branch (a clone that stopped before it)")
        site()
        if hgit("checkout", "-q", "-b", branch).returncode:
            return aside(f"{branch} could not be created")
        from brr.loom.runtime.merge_driver import install_merge_driver
        install_merge_driver(dest)
        done.append(f"created {branch} at HEAD")
    if not found:
        return None
    return report()


# The fact keeps this many dirty paths; ``dirty_count`` counts them all.
DIRTY_KEPT = 200


def _crash_site(dest: Path, strand: str) -> dict:
    """The strand branch's sha and the work tree's differences from it, read only.

    Refs are read as files, so a broken ``HEAD`` doesn't hide them. ``dirty``
    is ``git status --porcelain`` of the work tree against that commit (or
    HEAD's, before the branch existed), taken through a stand-in git dir
    with its own index: the room's index may be what broke, and nothing in
    the room is written. None when there is no commit to compare with.
    """
    gitdir = dest / ".git"
    head = _read_ref(gitdir, f"refs/heads/strand/{strand}")
    base = head or _read_ref(gitdir, "HEAD")
    site = {"head": head, "dirty": None, "dirty_count": 0}
    if not base or not (gitdir / "objects").is_dir():
        return site
    with tempfile.TemporaryDirectory(prefix="loom-site-") as view:
        view = Path(view)
        for name in ("objects", "refs", "packed-refs", "info"):
            if (gitdir / name).exists():
                (view / name).symlink_to(gitdir / name)
        (view / "HEAD").write_text(base + "\n")
        (view / "config").write_text("[core]\n\tbare = false\n")
        env = dict(_env_without_pin(), GIT_DIR=str(view), GIT_WORK_TREE=str(dest),
                   GIT_INDEX_FILE=str(view / "index"))
        if git(dest, "read-tree", base, check=False, env=env).returncode:
            return site
        status = git(dest, "status", "--porcelain", "-z", "--no-renames",
                     "--untracked-files=all", check=False, env=env)
    if status.returncode:
        return site
    dirty = [row for row in status.stdout.split("\0") if row]
    site.update(dirty=dirty[:DIRTY_KEPT], dirty_count=len(dirty))
    return site


def _read_ref(gitdir: Path, name: str, depth: int = 3) -> str | None:
    """The sha ``name`` points to, read from loose and packed refs without git."""
    try:
        text = (gitdir / name).read_text().strip()
    except (OSError, UnicodeDecodeError):
        text = ""
    if text.startswith("ref: ") and depth:
        return _read_ref(gitdir, text[5:].strip(), depth - 1)
    if re.fullmatch(r"[0-9a-f]{40,64}", text):
        return text
    try:
        packed = (gitdir / "packed-refs").read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in packed:
        sha, _, ref = line.partition(" ")
        if ref == name and re.fullmatch(r"[0-9a-f]{40,64}", sha):
            return sha
    return None


def _some(paths: list[str], n: int = 3) -> str:
    return ", ".join(paths[:n]) + (f", +{len(paths) - n} more" if len(paths) > n else "")


def _recover_branch(old: Path, dest: Path, strand: str, report: dict) -> None:
    """Carry the strand branch's commits from a room moved aside, if readable.

    The old room is read, never written. When its ``HEAD`` is what broke, git
    refuses it as a repository, so the fetch reads its refs and objects
    through a stand-in directory with a sound ``HEAD``.
    """
    branch = f"strand/{strand}"
    gitdir = old / ".git"
    with tempfile.TemporaryDirectory(prefix="loom-aside-") as view:
        source = gitdir
        if (gitdir / "objects").is_dir():
            source = Path(view)
            for name in ("objects", "refs", "packed-refs"):
                if (gitdir / name).exists():
                    (source / name).symlink_to(gitdir / name)
            (source / "HEAD").write_text("ref: refs/heads/main\n")
            (source / "config").write_text("[core]\n\tbare = true\n")
        fetched = git(dest, "fetch", "-q", "--no-tags", str(source), f"refs/heads/{branch}",
                      check=False)
    if fetched.returncode != 0:
        report["done"].append(f"found no readable {branch} in the old room; it starts at main")
        return
    git(dest, "reset", "-q", "--hard", "FETCH_HEAD")
    sha = git(dest, "rev-parse", "--short", "HEAD").stdout.strip()
    report["done"].append(f"kept {branch}'s commits ({sha})")


def _record_repair(home: Path, strand: str, gen: int | None, report: dict):
    from brr.daemon2.facts import Fact
    from .home import Home
    from .ledger import append
    loom = Home(home)
    return append(loom, Fact(kind="room.repaired", by=f"loom:{loom.install_id()}", data={
        "strand": strand, "gen": gen, "found": report["found"],
        "done": report["done"], "aside": report.get("aside"),
        "head": report.get("head"), "dirty": report.get("dirty"),
        "dirty_count": report.get("dirty_count", 0),
    }))


def repaired_line(fact) -> str:
    """The boundary's line for a ``room.repaired`` fact."""
    data = fact.data
    line = "repaired: room " + "; ".join(data["found"]) + " -> " + "; ".join(data["done"])
    site = []
    if data.get("aside"):
        site.append(f"the old room is at {data['aside']}")
    if data.get("head"):
        site.append(f"strand/{data['strand']} was at {data['head'][:12]}")
    if data.get("dirty") is not None:
        n = data.get("dirty_count", len(data["dirty"]))
        paths = [row[3:] for row in data["dirty"]]
        site.append(f"{n} dirty paths ({_some(paths)})" if n else "no dirty paths")
    if site:
        line += " · crash site: " + "; ".join(site)
    return line.replace("\n", "\\n")


def resolve_clone(room_dir: Path) -> Path:
    """The self clone at ``room_dir`` or at ``room_dir/self``."""
    room_dir = Path(room_dir).resolve()
    if (room_dir / "core" / "loom" / "wake").is_file():
        return room_dir
    nested = room_dir / "self"
    if (nested / "core" / "loom" / "wake").is_file():
        return nested
    raise SelfError(
        f"no self clone at {room_dir}: neither core/loom/wake nor self/core/loom/wake"
    )


def default_loom_part(clone: Path, thread: str) -> str:
    """Used only when the caller did not pass ``LOOM_PART``. Step 1 owns the real text."""
    lines: list[str] = []
    if clone.name == "self" and clone.parent.parent.name == "rooms":
        lines.append(f"strand: {clone.parent.name}")
    lines.append(f"thread: {thread}")
    lines.append(
        "letters arrive at your tool boundaries. "
        "answer with `brnrd runtime send`. "
        "when you are done, stop; the jack holds you while letters may come."
    )
    return "\n".join(lines) + "\n"


def insert_briefing(prompt: str, briefing: str) -> str:
    """After the core: before the thread README, else first."""
    start = prompt.find("## threads/")
    if start < 0:
        return briefing + prompt
    return prompt[:start] + briefing + prompt[start:]


def run_wake(room_dir: Path, thread: str, owed: Path | None = None, *,
             loom_part: Path | None = None, repos: Path | None = None,
             core: Path | None = None, stdout=None, stderr=None,
             recipe: Path | None = None) -> int:
    """Run the self recipe and add the loom briefing and thread wishes within the wake budget.

    With ``core``, a recipe that knows ``LOOM_CORE`` writes the core there and
    leaves it out of stdout; one that does not leaves the file unwritten.
    ``recipe`` runs another recipe (the packaged one) against the same clone.
    """
    _segment(thread, "thread")
    clone = resolve_clone(room_dir)
    if recipe is not None:
        argv = [sys.executable, str(recipe)]
    else:
        recipe = clone / "core" / "loom" / "wake"
        if not os.access(recipe, os.X_OK):
            raise SelfError(f"{recipe} is not executable")
        argv = [str(recipe)]
    env = _env_without_pin()
    if loom_part is not None:
        env["LOOM_PART"] = str(loom_part)
    if repos is not None:
        env["LOOM_REPOS"] = str(repos)
    if core is not None:
        env["LOOM_CORE"] = str(core)
    from .home import home_of_room
    from .ledger import read_facts
    from .wishes import insert_wishes, wake_wishes
    section = ""
    try:
        home = home_of_room(clone.parent)
    except ValueError:
        home = None  # A standalone self clone has no loom ledger.
    if home is not None:
        from .lifecycle import main_snapshot
        rows = main_snapshot(home).get(thread, {}).get("plan_rows") or ()
        facts = read_facts(home)
        section = wake_wishes(facts, thread, rows)
        if thread == home.main_thread():
            # The center wakes with both queues and Next build, derived, never a prose line.
            from .queues import queues, render
            from .wishes import told, waits_on
            found = told(facts)
            section += render(queues(found, waits_on(found, main_snapshot(home))))
    from .briefing import section as briefing_section
    briefing = briefing_section()
    # Reserve what the loom adds before the recipe fits its tree/README to the budget.
    limit = int(env.get("LOOM_BUDGET_BYTES") or 24000)
    env["LOOM_BUDGET_BYTES"] = str(max(0, limit - len((section + briefing).encode("utf-8"))))
    if core is not None:
        # Absent after the run means the recipe kept the core in stdout.
        Path(core).unlink(missing_ok=True)
    owed_file = None
    part_file = None
    try:
        if owed is None:
            handle = tempfile.NamedTemporaryFile("w", prefix="loom-owed-", delete=False)
            handle.write("[]")
            handle.close()
            owed_file = handle.name
            owed_arg = handle.name
        else:
            owed_arg = str(owed)
        if not env.get("LOOM_PART"):
            handle = tempfile.NamedTemporaryFile("w", prefix="loom-part-", delete=False)
            handle.write(default_loom_part(clone, thread))
            handle.close()
            part_file = handle.name
            env["LOOM_PART"] = handle.name
        proc = subprocess.run([*argv, thread, owed_arg], cwd=clone, env=env,
                              stdout=subprocess.PIPE, stderr=stderr, timeout=60)
        output = proc.stdout.decode("utf-8")
        if proc.returncode == 0:
            if core is not None and Path(core).is_file():
                with open(core, "a", encoding="utf-8") as handle:
                    handle.write(briefing)
            else:
                output = insert_briefing(output, briefing)
        if proc.returncode == 0 and section:
            output = insert_wishes(output, section)
        (stdout or sys.stdout).write(output)
        return proc.returncode
    finally:
        if owed_file:
            os.unlink(owed_file)
        if part_file:
            os.unlink(part_file)
