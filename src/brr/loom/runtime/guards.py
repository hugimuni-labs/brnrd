"""Guards: rules the self keeps as data and the loom runs (core/guards/*.toml).

A guard is read from the self's main, never from a room's branch, so a
strand cannot loosen its own guard by editing it. ``match`` warns at the
tool boundary and never blocks. ``paths`` blocks one stop and refuses the
outward acts (send-self, publish, settle). A broken file is skipped whole
and reported, never applied in part.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

GUARD_DIR = "core/guards"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SCOPE = re.compile(r"^(self|thread:[a-z0-9][a-z0-9-]*|repo:[A-Za-z0-9._-]+)$")
_FILE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}


class GuardError(ValueError):
    """A guard file that does not parse. The text names the field."""


@dataclass(frozen=True)
class Match:
    at: str
    tool: re.Pattern
    input: re.Pattern | None = None
    unless: re.Pattern | None = None
    path: re.Pattern | None = None
    cwd: re.Pattern | None = None
    output: re.Pattern | None = None


@dataclass(frozen=True)
class Paths:
    repo: str
    untouched: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()


@dataclass(frozen=True)
class Guard:
    id: str
    says: str
    set_by: str
    scope: str
    paths: tuple[Paths, ...] = ()
    matches: tuple[Match, ...] = ()

    @property
    def label(self) -> str:
        return f"guard {self.id} ({self.set_by}): {self.says}"


def _regex(ident: str, where: str, value) -> re.Pattern | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise GuardError(f"{ident}: {where} must be a non-empty string")
    try:
        return re.compile(value)
    except re.error as exc:
        raise GuardError(f"{ident}: {where} is not a regex: {exc}") from exc


def _globs(ident: str, where: str, value) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(g, str) and g.strip("/") for g in value):
        raise GuardError(f"{ident}: {where} must be a list of path globs")
    return tuple(value)


def parse(ident: str, text: str) -> Guard:
    if not _ID.fullmatch(ident):
        raise GuardError(f"{ident}: the file name must be [a-z0-9-].toml")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise GuardError(f"{ident}: {exc}") from exc
    unknown = set(data) - {"says", "set-by", "scope", "paths", "match"}
    if unknown:
        raise GuardError(f"{ident}: unknown keys {', '.join(sorted(unknown))}")
    says, set_by = data.get("says"), data.get("set-by")
    if not isinstance(says, str) or not says.strip():
        raise GuardError(f"{ident}: says is required: the rule in its own words")
    if not isinstance(set_by, str) or not set_by.startswith(("letter:", "scar:")) or len(set_by) < 8:
        raise GuardError(f"{ident}: set-by is required: letter:<id> or scar:<file>#<title>")
    scope = data.get("scope", "self")
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        raise GuardError(f"{ident}: scope is self, thread:<id> or repo:<name>")
    default_repo = scope[5:] if scope.startswith("repo:") else "self"
    paths = []
    for n, entry in enumerate(data.get("paths") or ()):
        where = f"paths[{n}]"
        if not isinstance(entry, dict) or set(entry) - {"repo", "untouched", "absent"}:
            raise GuardError(f"{ident}: {where} takes repo, untouched, absent")
        repo = entry.get("repo", default_repo)
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", repo):
            raise GuardError(f"{ident}: {where}.repo must be self or a repo name")
        item = Paths(repo, _globs(ident, f"{where}.untouched", entry.get("untouched")),
                     _globs(ident, f"{where}.absent", entry.get("absent")))
        if not item.untouched and not item.absent:
            raise GuardError(f"{ident}: {where} names no untouched or absent globs")
        paths.append(item)
    matches = []
    for n, entry in enumerate(data.get("match") or ()):
        where = f"match[{n}]"
        keys = {"at", "tool", "input", "unless", "path", "cwd", "output"}
        if not isinstance(entry, dict) or set(entry) - keys:
            raise GuardError(f"{ident}: {where} takes {', '.join(sorted(keys))}")
        at = entry.get("at", "pre")
        if at not in ("pre", "post"):
            raise GuardError(f"{ident}: {where}.at is pre or post")
        item = Match(at, _regex(ident, f"{where}.tool", entry.get("tool", ".*")),
                     *(_regex(ident, f"{where}.{key}", entry.get(key))
                       for key in ("input", "unless", "path", "cwd", "output")))
        if item.input is None and item.path is None and item.output is None:
            raise GuardError(f"{ident}: {where} needs input, path or output")
        if item.output is not None and at != "post":
            raise GuardError(f"{ident}: {where}.output needs at = \"post\"")
        matches.append(item)
    if not paths and not matches:
        raise GuardError(f"{ident}: a guard needs [[paths]] or [[match]]")
    return Guard(ident, says.strip(), set_by, scope, tuple(paths), tuple(matches))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


def _texts(self_repo: Path) -> tuple[str, dict[str, str]]:
    """The guard files on main, by id: one ls-tree and one cat-file per new tip."""
    from .lifecycle import main_tip
    tip = main_tip(self_repo)
    listing = _git(self_repo, "ls-tree", tip, f"{GUARD_DIR}/")
    names = []
    for line in listing.stdout.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and path.endswith(".toml"):
            names.append((Path(path).stem, parts[2]))
    if not names:
        return tip, {}
    batch = _git_batch(self_repo, [blob for _, blob in names])
    return tip, {ident: batch[blob] for ident, blob in names if blob in batch}


def _git_batch(repo: Path, blobs: list[str]) -> dict[str, str]:
    proc = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, input=("\n".join(blobs) + "\n").encode(),
                          capture_output=True, timeout=60)
    out, found, at = proc.stdout, {}, 0
    while at < len(out):
        end = out.index(b"\n", at)
        head = out[at:end].decode().split()
        at = end + 1
        if len(head) != 3:
            continue
        size = int(head[2])
        found[head[0]] = out[at:at + size].decode(errors="replace")
        at += size + 1
    return found


def load(self_repo: Path, cache: Path | None = None) -> tuple[list[Guard], list[str]]:
    """Every guard on main and every file that failed to parse.

    With ``cache``, the files are kept per main tip in that JSON file: the jack
    runs once per tool call, and main rarely moves between calls.
    """
    self_repo = Path(self_repo)
    if not (self_repo / ".git").exists():
        return [], []
    from .lifecycle import main_tip
    texts = None
    if cache is not None and cache.is_file():
        try:
            saved = json.loads(cache.read_text())
            if saved.get("tip") == main_tip(self_repo):
                texts = saved["files"]
        except (OSError, ValueError, KeyError, TypeError):
            texts = None
    if texts is None:
        tip, texts = _texts(self_repo)
        if cache is not None:
            from .home import atomic_write
            atomic_write(cache, json.dumps({"tip": tip, "files": texts}) + "\n")
    guards, errors = [], []
    for ident in sorted(texts):
        try:
            guards.append(parse(ident, texts[ident]))
        except GuardError as exc:
            errors.append(str(exc))
    return guards, errors


def applies(guard: Guard, room: Path, thread: str) -> bool:
    if guard.scope == "self":
        return True
    kind, _, name = guard.scope.partition(":")
    if kind == "thread":
        return name == thread
    return (Path(room) / name / ".git").exists()


def for_room(room: Path, thread: str, cache: bool = False) -> tuple[list[Guard], list[str]]:
    """The guards on the home self's main that cover this room's thread."""
    from .home import home_of_room
    room = Path(room)
    guards, errors = load(home_of_room(room).root / "self",
                          room / "port" / "guards.json" if cache else None)
    return [g for g in guards if applies(g, room, thread)], errors


def glob_regex(glob: str) -> re.Pattern:
    """``*`` stays in a segment, ``**`` crosses them, a trailing ``/`` is the whole directory."""
    if glob.endswith("/"):
        glob += "**"
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif glob[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out.lstrip("/") + r"\Z")


def _hits(globs: tuple[str, ...], names) -> list[str]:
    patterns = [glob_regex(g) for g in globs]
    return sorted({n for n in names if any(p.match(n) for p in patterns)})


def _base(tree: Path, commit: str) -> str | None:
    for upstream in ("origin/main", "origin/HEAD"):
        proc = _git(tree, "merge-base", commit, upstream)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None


def _changed(tree: Path, commit: str | None) -> list[str] | None:
    """Paths changed since the branch left main: the commit's, or the room's whole state."""
    base = _base(tree, commit or "HEAD")
    if base is None:
        return None
    names = set(_git(tree, "diff", "--name-only", "--no-renames", base,
                     *((commit,) if commit else ())).stdout.splitlines())
    if commit is None:
        names |= set(_git(tree, "ls-files", "--others", "--exclude-standard").stdout.splitlines())
    return sorted(names)


def _present(tree: Path, globs: tuple[str, ...], commit: str | None) -> list[str]:
    if commit is not None:
        names = _git(tree, "ls-tree", "-r", "--name-only", commit).stdout.splitlines()
        return _hits(globs, names)
    literal = [g for g in globs if not any(c in g for c in "*?") and not g.endswith("/")]
    found = [g.lstrip("/") for g in literal if (tree / g.lstrip("/")).exists()]
    wild = tuple(g for g in globs if g not in literal)
    if wild:
        names = _git(tree, "ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
        found += _hits(wild, names)
    return sorted(set(found))


def _tree(room: Path, repo: str) -> Path:
    return Path(room) / repo


def violations(guards: list[Guard], room: Path, *, repo: str | None = None,
               commit: str | None = None) -> list[str]:
    """One block of lines per broken path guard. ``commit`` judges that commit
    alone (what is about to leave); without it, the room's whole worktree."""
    lines = []
    for guard in guards:
        found = []
        for item in guard.paths:
            if repo is not None and item.repo != repo:
                continue
            tree = _tree(room, item.repo)
            if not (tree / ".git").exists():
                continue
            if item.untouched:
                changed = _changed(tree, commit)
                if changed is None:
                    # No base to diff against is not a clean diff.
                    found.append(f"{item.repo}: no origin/main to diff against; untouched is unchecked")
                for path in _hits(item.untouched, changed or ()):
                    found.append(f"{item.repo}: {path} changed")
            for path in _present(tree, item.absent, commit) if item.absent else ():
                found.append(f"{item.repo}: {path} exists")
        if found:
            lines.append(guard.label)
            lines.extend(f"  {line}" for line in found)
    return lines


def born_broken(clone: Path, room: Path) -> list[str]:
    """Guards this landing adds or changes that could never pass.

    An ``absent`` judged at a commit reads the whole tree, so a guard naming a
    file already on its repo's main would refuse every landing until someone
    lifts it. The self is judged at the landing's tip, another repo at the
    room clone's ``origin/main``; a repo not in the room is not judged here.
    """
    base = _base(clone, "HEAD")
    if base is None:
        return []
    changed = _git(clone, "diff", "--name-only", "--no-renames", "--diff-filter=AM", base, "HEAD",
                   "--", f"{GUARD_DIR}/").stdout.splitlines()
    lines = []
    for path in changed:
        if not path.endswith(".toml"):
            continue
        try:
            guard = parse(Path(path).stem, _git(clone, "show", f"HEAD:{path}").stdout)
        except GuardError as exc:
            lines.append(f"{path}: {exc}")
            continue
        for item in guard.paths:
            if not item.absent:
                continue
            tree, rev = (clone, "HEAD") if item.repo == "self" else (_tree(room, item.repo), "origin/main")
            if not (tree / ".git").exists():
                continue
            for hit in _present(tree, item.absent, rev):
                lines.append(f"{path}: absent names {item.repo}: {hit}, which is already on main")
    return lines


def _call_text(tool: str, tool_input) -> str:
    if not isinstance(tool_input, dict):
        return json.dumps(tool_input)
    if tool == "Bash":
        return str(tool_input.get("command") or "")
    if tool == "Edit":
        return str(tool_input.get("new_string") or "")
    if tool == "MultiEdit":
        return "\n".join(str(e.get("new_string") or "") for e in tool_input.get("edits") or ()
                         if isinstance(e, dict))
    if tool == "Write":
        return str(tool_input.get("content") or "")
    return json.dumps(tool_input, separators=(",", ":"))


def _response_text(response) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict) and ("stdout" in response or "stderr" in response):
        return "\n".join(str(response.get(k) or "") for k in ("stdout", "stderr"))
    return json.dumps(response, separators=(",", ":"))


def warnings(guards: list[Guard], room: Path, at: str, payload: dict) -> list[str]:
    """The guards this one call trips, in the guard's own words."""
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    text = _call_text(tool, tool_input)
    file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "") \
        if isinstance(tool_input, dict) else ""
    cwd = str(payload.get("cwd") or "")
    response = _response_text(payload.get("tool_response")) if at == "post" else ""
    out = []
    for guard in guards:
        hit = None
        for m in guard.matches:
            if m.at != at or not m.tool.fullmatch(tool):
                continue
            if m.input is not None and not m.input.search(text):
                continue
            if m.path is not None and not m.path.search(file_path):
                continue
            if m.cwd is not None and not m.cwd.search(cwd):
                continue
            if m.output is not None and not m.output.search(response):
                continue
            if m.unless is not None and m.unless.search(text):
                continue
            hit = guard.label
            break
        if hit is None and at == "pre" and tool in _FILE_TOOLS and file_path:
            hit = _file_hit(guard, Path(room), Path(file_path))
        if hit:
            out.append(hit)
    return out


def _file_hit(guard: Guard, room: Path, target: Path) -> str | None:
    for item in guard.paths:
        tree = _tree(room, item.repo).resolve()
        try:
            rel = target.resolve().relative_to(tree).as_posix()
        except (ValueError, OSError):
            continue
        if _hits(item.untouched + item.absent, [rel]):
            return f"{guard.label} ({item.repo}: {rel} is guarded)"
    return None
