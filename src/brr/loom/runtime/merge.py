"""Rebase onto the self and push to ``main``, or come back with a named failure.

``send_to_self`` is the body of step 1's later ``send --to self``. It never
force-pushes. A lost race (``main`` moved) costs another fetch. A check that
refuses comes back as the remote's own stderr — paraphrasing that is how a
real refusal turns into a polite zero.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .selfrepo import SelfError as SendError, git as _git, refresh_index

_RACE_LIMIT = 10
_STOP_LIMIT = 3
_STOPS = "loom-send-stops"

# Two lines was the shape asked for. A pipeline whose ``git show`` fails
# feeds an interpreter an empty script, which can exit 0 — a deletion, or
# a commit that dropped ``core/immune``, would be accepted. The shim is as
# long as it needs to be to make that a refusal. A deletion has no new tree,
# so the script that judges it is the one on the tip being deleted.
_HOOK = """\
#!/bin/sh
# The incumbent judges the change: core/immune comes from the tip being
# replaced, never from the incoming commit (a push that rewrites immune to
# "exit 0" would otherwise judge itself), and never from a copy beside this
# hook. Only the first push (old all zeros) is judged by its own script.
# A missing, empty or non-executable script refuses. Run its own shebang.
status=0
while read -r old new ref; do
    [ -n "$old" ] || continue
    tip=$old
    case "$old" in
        ""|*[!0]*) ;;
        *) tip=$new ;;
    esac
    script=$(mktemp "${TMPDIR:-/tmp}/immune-hook.XXXXXX") || exit 1
    if ! git show "$tip:core/immune" >"$script" 2>/dev/null || [ ! -s "$script" ]; then
        printf 'immune: core/immune missing at %s\\n' "$tip" >&2
        rm -f "$script"
        status=1
        continue
    fi
    mode=$(git ls-tree "$tip" -- core/immune | cut -d ' ' -f 1)
    if [ "$mode" != "100755" ]; then
        printf 'immune: core/immune not executable at %s\\n' "$tip" >&2
        rm -f "$script"
        status=1
        continue
    fi
    chmod +x "$script" || { rm -f "$script"; exit 1; }
    "$script" "$old" "$new" "$ref" || status=1
    rm -f "$script"
done
exit "$status"
"""


@dataclass(frozen=True)
class Outcome:
    """How one ``send_to_self`` ended.

    ``merged`` carries ``sha``. ``stopped`` and ``failed`` carry ``files``
    (``failed`` also ``tries``). ``refused`` carries ``stderr`` unchanged.
    """

    status: str
    sha: str | None = None
    files: tuple[str, ...] = ()
    tries: int | None = None
    stderr: str = ""


def install_pre_receive(repo: str | Path) -> Path:
    """Write the pre-receive shim, so the incumbent ``main``'s immune judges each push.

    A bare repo keeps the hook in ``hooks/``. A working repo (step 2's
    ``home/self``, ``main`` checked out) keeps it in ``.git/hooks`` and gets
    ``receive.denyCurrentBranch=updateInstead``, so an accepted push updates
    that worktree instead of dying on the checked-out branch. A dirty
    worktree still refuses the push — git will not overwrite it, and this
    function does not override that.

    Returns the hook path. A second call replaces the hook and, on a working
    repo, sets the config again. A symlink at the hook path is refused
    rather than followed.
    """

    root = Path(repo)
    hooks = _hooks_dir(root)
    hooks.mkdir(parents=True, exist_ok=True)
    path = hooks / "pre-receive"
    if path.is_symlink():
        raise SendError(f"refusing to follow symlink: {path}")
    path.write_text(_HOOK, encoding="utf-8")
    path.chmod(0o755)
    if not _is_bare(root):
        _git(root, "config", "receive.denyCurrentBranch", "updateInstead")
    return path


def send_to_self(room: str | Path, widening: str | None = None) -> Outcome:
    """Land the room's ``HEAD`` on ``origin``'s ``main``.

    The room is a clone on its own branch. A conflict stops the rebase and
    leaves it in progress; the caller resolves in the room and calls again.
    The third stop of one send aborts the rebase and returns ``failed``.
    """

    room_path = Path(room).resolve()
    if not room_path.is_dir():
        raise SendError(f"no such room: {room_path}")
    probe = _git(room_path, "rev-parse", "--is-inside-work-tree", check=False)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise SendError(f"not a git work tree: {room_path}")

    last = ""
    for _attempt in range(_RACE_LIMIT):
        stopped = _continue(room_path) if _rebasing(room_path) else _rebase(room_path)
        if stopped is not None:
            return stopped
        # After the fetch and rebase: origin/main is fresh, so the diff is ours alone.
        _guarded(room_path)
        _fold_index(room_path)
        _stamp_range(room_path, widening)
        sha = _git(room_path, "rev-parse", "HEAD").stdout.strip()
        with _push_lock(room_path):
            push = _git(room_path, "push", "origin", "HEAD:main", check=False)
        if push.returncode == 0:
            _write_stops(room_path, 0)
            return Outcome("merged", sha=sha)
        last = _text(push)
        # Immune refuses a non-fast-forward in the same words receive-pack
        # uses, and two pushes at once can lose the ref lock. Both are "main
        # moved or was busy": fetch again. A check's refusal says
        # "pre-receive hook declined" and does not say these.
        if _busy(last):
            continue
        if _declined(last):
            return Outcome("refused", stderr=last)
        raise SendError(last or "push failed")
    raise SendError(
        f"main moved {_RACE_LIMIT} times; push still rejected\n{last}"
    )


def _guarded(clone: Path) -> None:
    """A self clone in a strand's room lands nothing a path guard forbids."""
    room = clone.parent
    if room.parent.name != "rooms" or clone.name != "self":
        return
    from .guards import born_broken, for_room, violations
    from .lifecycle import own_thread
    born = born_broken(clone, room)
    if born:
        raise SendError("send-self: a guard in this landing could never pass:\n" + "\n".join(born))
    try:
        thread = own_thread(room)
    except Exception:
        thread = ""
    guards, _ = for_room(room, thread)
    lines = violations(guards, room, repo="self", commit="HEAD")
    if lines:
        raise SendError("send-self: a path guard refuses this landing:\n" + "\n".join(lines))


def _fold_index(room: Path) -> None:
    """Regenerate INDEX.md and CLAUDE.md from the rebased tip and fold them into it.

    The self's ``core/loom/index`` derives both from the committed tree, so
    they are made here, after the rebase, and amended into the room's last
    commit: one landing, one index, and 30-index at the self finds them
    equal to its own regeneration. A room with nothing of its own to land
    but a stale index gets one commit for it.
    """
    changed = refresh_index(room)
    if not changed:
        return
    _git(room, "add", "--", *changed)
    head = _git(room, "rev-parse", "HEAD").stdout.strip()
    base = _git(room, "rev-parse", "origin/main").stdout.strip()
    if head == base:
        _git(room, "commit", "-m", "index", "--only", "--", *changed)
    else:
        # A commit that only deleted the index is empty once it is back: drop it,
        # the stamp's cherry-pick refuses an empty commit.
        _git(room, "commit", "--amend", "--no-edit", "--allow-empty", "--only", "--", *changed)
        if _git(room, "diff", "--quiet", "HEAD~1", "HEAD", check=False).returncode == 0:
            _git(room, "reset", "--soft", "HEAD~1")


@contextmanager
def _push_lock(room: Path):
    """One push into a local self at a time.

    A self with a work tree takes pushes with ``updateInstead``: receive-pack
    checks the new tree out *before* it moves the ref. Two pushes at once:
    the loser's tree is already in the index when its ref update fails, so
    every later push is refused with "Working directory has staged changes"
    (#2370). The lock lives in the self's git dir, so every room that pushes
    there shares it. A remote that is not a local path is left to its server.
    """
    url = _git(room, "remote", "get-url", "origin", check=False).stdout.strip()
    target = room / (url[7:] if url.startswith("file://") else url)
    if not url or not target.is_dir():
        yield
        return
    git_dir = target / ".git" if (target / ".git").is_dir() else target
    with open(git_dir / "loom-push.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _rebase(room: Path) -> Outcome | None:
    _git(room, "fetch", "origin", "main")
    proc = _git(room, "rebase", "origin/main", check=False)
    if proc.returncode == 0:
        return None
    if _rebasing(room):
        return _stop(room)
    raise SendError(_text(proc) or "rebase failed")


def _continue(room: Path) -> Outcome | None:
    if _unmerged(room):
        return _stop(room)
    proc = _git(room, "rebase", "--continue", check=False)
    if proc.returncode == 0:
        return None
    if _rebasing(room):
        return _stop(room)
    raise SendError(_text(proc) or "rebase --continue failed")


def _stop(room: Path) -> Outcome:
    files = _unmerged(room)
    n = _read_stops(room) + 1
    if n >= _STOP_LIMIT:
        _git(room, "rebase", "--abort")
        _write_stops(room, 0)
        return Outcome("failed", files=files, tries=n)
    _write_stops(room, n)
    return Outcome("stopped", files=files)


def _busy(text: str) -> bool:
    low = text.lower()
    return (
        "non-fast-forward" in low
        or "non fast-forward" in low
        or "fetch first" in low
        or "cannot lock ref" in low
        or "failed to update ref" in low
    )


def _declined(text: str) -> bool:
    # pre-receive only. A local pre-push hook saying "hook declined" is not
    # the self refusing the commit, and must not be returned as one.
    return "pre-receive hook declined" in text.lower()


def _rebasing(room: Path) -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        if _git_path(room, name).exists():
            return True
    return False


def _unmerged(room: Path) -> tuple[str, ...]:
    names = [line for line in _git(room, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if line]
    return tuple(sorted(set(names)))


def _read_stops(room: Path) -> int:
    path = _git_path(room, _STOPS)
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return 0
    if not text.isdigit():
        raise SendError(f"{path} is not a stop count: {text!r}")
    return int(text)


def _write_stops(room: Path, n: int) -> None:
    path = _git_path(room, _STOPS)
    if n <= 0:
        path.unlink(missing_ok=True)
        return
    path.write_text(f"{n}\n", encoding="utf-8")


def _git_path(room: Path, name: str) -> Path:
    out = _git(room, "rev-parse", "--git-path", name).stdout.strip()
    path = Path(out)
    return path if path.is_absolute() else room / path


def _is_bare(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-bare-repository").stdout.strip() == "true"


def _hooks_dir(repo: Path) -> Path:
    # A working tree can contain a directory named hooks/. That is not
    # git's hook dir. Bare repos are the ones whose hooks live at the top.
    if _is_bare(repo):
        return repo / "hooks"
    raw = _git(repo, "rev-parse", "--git-path", "hooks").stdout.strip()
    path = Path(raw)
    return path if path.is_absolute() else repo / path


def _stamp_range(room: Path, widening: str | None) -> None:
    """Rewrite ``origin/main..HEAD`` so the body cannot keep its own trailers.

    The label is the strand's fold alone, never a body-written trailer or
    the tip it rebased onto. Main's commits already passed immune; joining
    them would make admitted taint sticky for every later strand.
    ``Widening`` is written only when this call was given one; a body-written
    citation is dropped.
    """
    from .labels import label_inputs, strand_label

    branch = _git(room, "symbolic-ref", "--short", "HEAD", check=False)
    if branch.returncode != 0:
        raise SendError("room is not on a branch; refusing to stamp")
    name = branch.stdout.strip()
    listed = [
        line for line in _git(room, "rev-list", "--reverse", "origin/main..HEAD").stdout.split()
        if line
    ]
    if not listed:
        return
    _strand, facts, self_root, log = label_inputs(room)
    strand = _strand
    label = strand_label(facts, strand, self_root=self_root, jack_log=log)
    original = _git(room, "rev-parse", "HEAD").stdout.strip()
    _git(room, "checkout", "--detach", "origin/main")
    try:
        for sha in listed:
            picked = _git(room, "cherry-pick", sha, check=False)
            if picked.returncode != 0:
                _git(room, "cherry-pick", "--abort", check=False)
                raise SendError(_text(picked) or f"could not replay {sha} to stamp it")
            message = _git(room, "log", "-1", "--format=%B").stdout
            stamped = _stamp_message(message, strand=strand, label=label, widening=widening)
            # A work tree's .git may be a file (a linked clone). Ask git.
            git_dir = Path(_git(room, "rev-parse", "--absolute-git-dir").stdout.strip())
            path = git_dir / "loom-stamp-msg"
            path.write_text(stamped, encoding="utf-8")
            amended = _git(
                room, "-c", "commit.gpgsign=false", "commit", "--amend", "-F", os.fspath(path),
                check=False,
            )
            if amended.returncode != 0:
                raise SendError(_text(amended) or "could not stamp the commit message")
    except Exception:
        _git(room, "checkout", "-B", name, original, check=False)
        raise
    _git(room, "checkout", "-B", name, "HEAD")


def _split_message(message: str) -> tuple[str, list[str]]:
    lines = message.splitlines()
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    start = end
    while start > 0 and _looks_like_trailer(lines[start - 1]):
        start -= 1
    trailers = lines[start:end]
    blank_before = start > 0 and not lines[start - 1].strip()
    known = any(
        line.split(":", 1)[0].strip() in {"Loom-Strand", "Loom-Label", "Widening"}
        for line in trailers
    )
    if trailers and (blank_before or known):
        body = lines[:start]
        while body and not body[-1].strip():
            body.pop()
        return "\n".join(body), trailers
    return "\n".join(lines[:end]), []


def _looks_like_trailer(line: str) -> bool:
    if line[:1] in {" ", "\t"}:
        return True
    key, sep, _value = line.partition(":")
    return bool(sep) and bool(key.strip()) and " " not in key.strip()


def _stamp_message(message: str, *, strand: str, label, widening: str | None) -> str:
    """Drop body-written loom trailers, then let ``git interpret-trailers`` add ours."""
    body, trailers = _split_message(message)
    kept = [
        line for line in trailers
        if line.split(":", 1)[0].strip() not in {"Loom-Strand", "Loom-Label", "Widening"}
    ]
    text = body
    if kept:
        text = (text + "\n\n" + "\n".join(kept)) if text else "\n".join(kept)
    if not text.endswith("\n"):
        text += "\n"
    cmd = [
        "interpret-trailers", "--if-exists", "replace",
        "--trailer", f"Loom-Strand: {strand}",
        "--trailer", f"Loom-Label: {label.trailer()}",
    ]
    if widening:
        cmd += ["--trailer", f"Widening: {widening}"]
    proc = _git(Path.cwd(), *cmd, input=text, check=False)
    if proc.returncode != 0:
        raise SendError(proc.stderr.strip() or "git interpret-trailers failed")
    return proc.stdout if proc.stdout.endswith("\n") else proc.stdout + "\n"


def _text(proc: subprocess.CompletedProcess[str]) -> str:
    err = proc.stderr or ""
    out = proc.stdout or ""
    if out and out not in err:
        if err and not err.endswith("\n"):
            err += "\n"
        return err + out
    return err or out
