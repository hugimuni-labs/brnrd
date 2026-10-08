"""A self is a git repo. ``init`` seeds it once; a room is a clone, not a worktree.

The wake recipe lives in the self after the copy (``seed/core/loom/wake``).
Nothing here decides what a later wake contains.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Pinned parent git must not retarget a self. 10-readme does the opposite
# on purpose: a bare-repo hook finds the repo through GIT_DIR.
_PINNED_GIT = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

STATUSES = ("open", "live", "dormant", "settled", "retired")
TENSES = ("plan", "reference")
STATUS_WHY = "open|live|dormant|settled|retired"
TENSE_WHY = "plan|reference"
_EXECUTABLES = ("core/loom/wake", "core/immune.d/10-readme")

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


def _env_without_pin() -> dict[str, str]:
    env = os.environ.copy()
    for key in _PINNED_GIT:
        env.pop(key, None)
    return env


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        env=_env_without_pin(),
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise SelfError(f"git {' '.join(args)} ({cwd}) -> {proc.returncode}: {detail}")
    return proc


def _segment(value: str, what: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise SelfError(f"{what} is not a single path segment: {value!r}")
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


def _commit(repo: Path, message: str) -> None:
    env = _env_without_pin()
    env["GIT_AUTHOR_NAME"] = AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = AUTHOR_EMAIL
    env["GIT_COMMITTER_NAME"] = AUTHOR_NAME
    env["GIT_COMMITTER_EMAIL"] = AUTHOR_EMAIL
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", message],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise SelfError(f"git commit ({repo}) -> {proc.returncode}: {detail}")


def init_self(home: Path, person: str | None = None, remote: str | None = None) -> str:
    """Create ``home/self`` and return the one-line result.

    A finished self is a no-op. A failed attempt removes the directory it
    created, so the no-op cannot hide a push that never happened.
    """
    home = Path(home).resolve()
    self_dir = home / "self"
    if (self_dir / ".git").exists():
        return f"self already exists: {self_dir}"
    if self_dir.exists():
        raise SelfError(f"{self_dir} exists and is not a git self; refusing to overwrite")
    if person is not None:
        _segment(person, "person")
    self_dir.mkdir(parents=True)
    try:
        _copy_seed(self_dir)
        if person:
            agreement = self_dir / "people" / person / "agreement.md"
            agreement.parent.mkdir(parents=True, exist_ok=True)
            agreement.write_text(_agreement(person), encoding="utf-8")
        git(self_dir, "init", "-b", "main")
        git(self_dir, "add", "-A")
        _commit(
            self_dir,
            "Seed the self.\n\n"
            "The loom copies these files once; the self owns them after this commit.\n",
        )
        if remote:
            git(self_dir, "remote", "add", "origin", remote)
            git(self_dir, "push", "-u", "origin", "main")
    except Exception:
        shutil.rmtree(self_dir, ignore_errors=True)
        raise
    sha = git(self_dir, "rev-parse", "--short", "HEAD").stdout.strip()
    return f"initialized {self_dir} {sha}"


def room(home: Path, strand: str) -> Path:
    """``home/rooms/<strand>/self``, a ``--shared`` clone on ``strand/<strand>``.

    A room that already exists is returned as it stands. A molt keeps its commits.
    """
    home = Path(home).resolve()
    strand = _segment(strand, "strand")
    source = home / "self"
    if not (source / ".git").exists():
        raise SelfError(f"no self at {source}; init first")
    dest = home / "rooms" / strand / "self"
    if (dest / ".git").exists():
        return dest
    if dest.exists():
        raise SelfError(f"{dest} exists and is not a clone; refusing to overwrite")
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(home, "clone", "--shared", str(source), str(dest))
    git(dest, "checkout", "-b", f"strand/{strand}")
    return dest


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
        "answer with `python -m brr.loom.runtime send`. "
        "when you are done, stop; the jack holds you while letters may come."
    )
    return "\n".join(lines) + "\n"


def run_wake(room_dir: Path, thread: str, owed: Path | None = None) -> int:
    """Exec the clone's ``core/loom/wake``. The prompt is its stdout; the code is its code."""
    _segment(thread, "thread")
    clone = resolve_clone(room_dir)
    recipe = clone / "core" / "loom" / "wake"
    if not os.access(recipe, os.X_OK):
        raise SelfError(f"{recipe} is not executable")
    env = os.environ.copy()
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
        proc = subprocess.run([str(recipe), thread, owed_arg], cwd=clone, env=env)
        return proc.returncode
    finally:
        if owed_file:
            os.unlink(owed_file)
        if part_file:
            os.unlink(part_file)
