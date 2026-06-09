"""The agent's dominion — durable, owned working memory on a dedicated branch.

The dominion lives on an orphan branch (default ``brr-home``) materialized as
a long-lived ``git worktree`` at ``.brr/dominion/``. The *branch* is the
durable thing — it shares no history with ``main``, never merges into it, and
travels with the repo's remote so ``git fetch`` brings it back on any machine;
the local checkout is a disposable view. See ``kb/design-agent-dominion.md``.

The plain branch name is deliberate: it reads as ordinary infrastructure to
anyone browsing the repo. The *concept* — the agent's dominion — lives in the
playbook and design docs, where the ownership weight belongs.

This module owns bootstrap (:func:`ensure_dominion`) and resolving the
self-inject index into a wake-time digest (:func:`resolve_self_inject`).
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from pathlib import Path

from . import gitops

try:
    import fcntl  # POSIX-only; brr targets Linux/macOS hosts + containers.
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


DEFAULT_BRANCH = "brr-home"
WORKTREE_DIRNAME = "dominion"
SELF_INJECT_FILE = "self-inject"
PLAYBOOK_FILE = "playbook.md"
COMMIT_LOCK_FILE = "dominion.commit.lock"
# Total UTF-8 budget for the wake-time self-inject digest. Sized to fit
# the seed playbook in full with headroom for the agent's own entries
# (recent pain, current focus); the agent can re-tune what it injects.
DEFAULT_INJECT_BUDGET_BYTES = 12288

# The seed playbook copied into a fresh dominion. It's the *starting*
# orientation — the agent owns and evolves its own copy thereafter.
_SEED_PLAYBOOK = Path(__file__).resolve().parent / "prompts" / "dominion-playbook.md"

_HEAD_RE = re.compile(r"^head:(\d+)$")
_TAIL_RE = re.compile(r"^tail:(\d+)$")


def dominion_path(repo_root: Path) -> Path:
    """Return the dominion worktree path (``.brr/dominion/``)."""
    return gitops.shared_brr_dir(repo_root) / WORKTREE_DIRNAME


def ensure_dominion(
    repo_root: Path,
    *,
    branch: str = DEFAULT_BRANCH,
    remote: str | None = None,
    push: bool = True,
) -> Path:
    """Materialize the dominion worktree, creating the branch if needed.

    Idempotent. If *branch* is already checked out in a worktree, return
    that path untouched (a daemon restart re-attaches). Otherwise:

    - local branch exists → add the worktree on it (returning after the
      checkout was removed);
    - the remote has the branch → fetch and add a tracking worktree
      (second machine, reinstall, managed failover);
    - neither → create the orphan branch empty, add the worktree, seed a
      skeleton, and push it (best-effort) when a remote exists.

    Returns the worktree path. Raises ``RuntimeError`` only when the
    worktree genuinely cannot be created; boot-path callers treat that as
    a soft failure rather than crashing.
    """
    path = dominion_path(repo_root)

    existing = gitops.branch_checkout_path(repo_root, branch)
    if existing is not None:
        try:
            if existing.resolve() == path.resolve():
                return path
        except OSError:
            pass
        return existing

    if remote is None:
        remote = gitops.default_remote(repo_root)

    if gitops.branch_exists(repo_root, branch):
        gitops.add_worktree(repo_root, path, branch=branch)
        return path

    if remote and gitops.remote_branch_exists(repo_root, remote, branch):
        gitops.fetch_branch(repo_root, remote, branch)
        gitops.add_worktree(
            repo_root, path,
            branch=branch, create_branch=True,
            start_point=f"{remote}/{branch}", track=True,
        )
        return path

    commit = gitops.create_orphan_branch(
        repo_root, branch, message=f"{branch}: initialize dominion",
    )
    if commit is None:
        raise RuntimeError(
            f"could not create dominion branch {branch!r} "
            "(git plumbing failed — is a committer identity configured?)"
        )
    gitops.add_worktree(repo_root, path, branch=branch)
    _seed(path)
    gitops.commit_all(path, f"{branch}: seed dominion")
    if push and remote:
        gitops.push_branch(repo_root, remote, branch)
    return path


@contextlib.contextmanager
def _commit_lock(dominion_dir: Path, timeout: float):
    """Hold an exclusive cross-process lock for the dominion commit step.

    The lock file lives in the shared ``.brr/`` dir (the worktree's
    parent), not inside the worktree, so it never lands in the dominion's
    own history. ``fcntl.flock`` is advisory and per-open-file-description,
    which is exactly what serializes two *separate processes* (a daemon
    thought and an ad-hoc session) — a ``threading.Lock`` would only cover
    threads of one process. Yields True when held, False when the lock
    couldn't be acquired within *timeout* (caller skips rather than races)
    or locking is unavailable on the platform (degrade to no-op lock).
    """
    if fcntl is None:  # pragma: no cover - non-POSIX
        yield True
        return
    lock_path = dominion_dir.parent / COMMIT_LOCK_FILE
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        yield True  # can't make a lock file — proceed best-effort
        return
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        yield acquired
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def commit(
    dominion_dir: Path,
    message: str,
    *,
    remote: str | None = None,
    branch: str | None = None,
    push: bool = False,
    lock_timeout: float = 30.0,
) -> bool:
    """Capture the dominion's working-tree changes as one commit, serialized.

    The persistence half of "the agent is its memory": whatever the
    resident wrote into ``.brr/dominion/`` during a thought is captured
    here, so it survives to the next wake without the agent having to
    remember a commit dance. Called by the daemon after each thought, on
    success *and* failure (a failed thought may still have recorded the
    pain that caused it).

    The commit step is serialized across processes by a file lock so an
    overlapping daemon thought and an ad-hoc session never race the shared
    worktree's git index — file *edits* stay free; only the index-touching
    commit serializes (the Society-of-Mind model,
    ``kb/design-agent-dominion.md`` §4). Returns True when a commit was
    made. A clean worktree is a no-op (False) — most thoughts don't touch
    the dominion. Every failure is swallowed to False: capturing memory
    must never break the thought that produced it.
    """
    if not dominion_dir.is_dir():
        return False
    try:
        with _commit_lock(dominion_dir, lock_timeout) as held:
            if not held:
                return False
            if not gitops.worktree_dirty(dominion_dir):
                return False
            if not gitops.commit_all(dominion_dir, message):
                return False
        if push and remote:
            target = branch or gitops.current_branch(dominion_dir)
            if target and target != "HEAD":
                gitops.push_branch(dominion_dir, remote, target)
        return True
    except Exception:  # noqa: BLE001 - capture is best-effort, never fatal
        return False


def resolve_self_inject(
    dominion_dir: Path,
    *,
    budget_bytes: int = DEFAULT_INJECT_BUDGET_BYTES,
) -> str:
    """Resolve the self-inject manifest into a wake-time digest.

    Reads the ``self-inject`` manifest (one ``<mode> <path>`` entry per
    line; ``#`` comments and blank lines ignored), renders each entry
    against the dominion's own files, and concatenates the fragments in
    order within *budget_bytes* (UTF-8) — entries past the budget are
    truncated, so order the manifest by importance.

    Modes: ``full`` | ``head:N`` | ``tail:N`` | ``grep:<pattern>``.
    ``exec`` is recognised but **not run** yet — it is the
    integrity-sensitive entry and lands with its guard in a later slice,
    so such entries are skipped. Returns ``""`` when the manifest is
    missing, empty, or resolves to nothing.
    """
    manifest = dominion_dir / SELF_INJECT_FILE
    if not manifest.exists():
        return ""

    fragments: list[str] = []
    used = 0
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        mode, _, rest = line.partition(" ")
        target = rest.strip()
        if not target:
            continue
        rendered = _render_entry(dominion_dir, mode, target)
        if not rendered:
            continue
        fragment = f"<!-- self-inject: {line} -->\n{rendered}".rstrip()
        sep = 2 if fragments else 0  # fragments are joined with "\n\n"
        frag_bytes = len(fragment.encode("utf-8"))
        if used + sep + frag_bytes <= budget_bytes:
            fragments.append(fragment)
            used += sep + frag_bytes
            continue
        remaining = budget_bytes - used - sep
        if remaining > 0:
            clipped = fragment.encode("utf-8")[:remaining].decode("utf-8", "ignore")
            fragments.append(
                f"{clipped}\n…[truncated to fit dominion inject budget]"
            )
        break

    return "\n\n".join(fragments).strip()


def _render_entry(dominion_dir: Path, mode: str, target: str) -> str:
    """Render one self-inject entry to text, or ``""`` to skip it."""
    if mode == "exec":
        return ""  # deferred: persistent-execution surface needs its guard

    candidate = dominion_dir / target
    try:
        resolved = candidate.resolve()
        resolved.relative_to(dominion_dir.resolve())
    except (OSError, ValueError):
        return ""  # keep reads inside the dominion
    if not resolved.is_file():
        return ""
    text = resolved.read_text(encoding="utf-8", errors="replace")

    if mode == "full":
        return text.rstrip("\n")
    head = _HEAD_RE.match(mode)
    if head:
        return "\n".join(text.splitlines()[: int(head.group(1))])
    tail = _TAIL_RE.match(mode)
    if tail:
        n = int(tail.group(1))
        return "\n".join(text.splitlines()[-n:]) if n else ""
    if mode.startswith("grep:"):
        pattern = mode[len("grep:"):]
        if not pattern:
            return ""
        try:
            rx = re.compile(pattern)
            matched = [ln for ln in text.splitlines() if rx.search(ln)]
        except re.error:
            matched = [ln for ln in text.splitlines() if pattern in ln]
        return "\n".join(matched)
    return ""  # unknown mode


def _seed(path: Path) -> None:
    """Write the starter files into a freshly created dominion."""
    (path / "README.md").write_text(_README, encoding="utf-8")
    (path / PLAYBOOK_FILE).write_text(
        _SEED_PLAYBOOK.read_text(encoding="utf-8"), encoding="utf-8",
    )
    (path / SELF_INJECT_FILE).write_text(_SELF_INJECT_SEED, encoding="utf-8")


_README = """\
# brr-home — the resident agent's working memory

This is an **orphan branch**: it shares no history with `main` and never merges
into it, so it won't appear in `main`'s diffs or pull requests. It's named
plainly so it reads as ordinary infrastructure to anyone browsing the repo —
nothing here needs your review.

It is brr's durable, owned working memory: the space the agent governs and
carries across runs (the design calls it the *dominion*; see
`kb/design-agent-dominion.md`). You're welcome to look — it's inspectable on
purpose. You just don't have to.
"""

_SELF_INJECT_SEED = """\
# self-inject — what rides into context on each wake.
#
# One entry per line: <mode> <path>
#   modes: full | head:N | tail:N | grep:<pattern> | exec
# Lines starting with '#' are comments. This file is yours: add, remove, and
# reorder freely. A budget cap bounds the total, and entries past it are
# truncated — so order by importance.
full playbook.md
"""
