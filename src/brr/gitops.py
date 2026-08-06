"""Git helpers — repo detection, branching, and file tracking."""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import closekeyword

try:  # pragma: no cover - POSIX only, and every supported host is POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


@contextlib.contextmanager
def file_lock(lock_path: Path, timeout: float = 30.0):
    """Hold an exclusive cross-process advisory lock at *lock_path*.

    Serializes the index-touching step of two *separate processes* sharing
    one git worktree — a daemon thought and an ad-hoc session, or two
    concurrent runs capturing the same account-scoped repo. ``fcntl.flock``
    is advisory and per-open-file-description, which is exactly that scope;
    a ``threading.Lock`` would only cover threads of one process.

    Yields True when the lock is held, False when it couldn't be acquired
    within *timeout* (the caller skips rather than races). Degrades to a
    no-op lock (yields True) when locking is unavailable or the lock file
    can't be created — capture is best-effort and must never become the
    thing that fails.

    The lock file must live *outside* the worktree it guards, or it lands
    in that repo's own history.
    """
    if fcntl is None:  # pragma: no cover - non-POSIX
        yield True
        return
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        yield True
        return
    acquired = False
    deadline = time.monotonic() + timeout
    try:
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


# ── Capture sync markers ─────────────────────────────────────────────
#
# One protocol, two memories. A capture net (dominion, knowledge) pushes
# best-effort; a failed push is never mislabeled or swallowed — it writes a
# classified marker to the gitignored runtime dir and the wake prompt surfaces
# it. Only a non-fast-forward rejection asks the resident to reconcile refs by
# hand. A successful push clears the marker.


_SYNC_STATUS_PREFIX = "status: "


def write_sync_marker(
    brr_dir: Path, name: str, reason: str, *, status: str = "",
) -> None:
    """Write a capture-sync marker, optionally carrying its failure class.

    The classification is written as a machine-readable first line
    (``status: <PushStatus value>``) ahead of the human sentence, because
    the wake prompt has to *render* the failure and a renderer that
    re-derives the class by matching the sentence is a second copy of the
    classifier — the thing this whole change exists to remove. Absent
    status ⇒ the marker is a bare reason, which is what every pre-#786
    marker on disk already is.
    """
    try:
        brr_dir.mkdir(parents=True, exist_ok=True)
        body = reason.strip()
        if status:
            body = f"{_SYNC_STATUS_PREFIX}{status}\n{body}"
        (brr_dir / name).write_text(body + "\n", encoding="utf-8")
    except OSError:
        pass


def clear_sync_marker(brr_dir: Path, name: str) -> None:
    try:
        (brr_dir / name).unlink(missing_ok=True)
    except OSError:
        pass


def read_sync_marker(brr_dir: Path, name: str) -> str | None:
    """Return the marker's human sentence, status line stripped."""
    return _read_sync_marker_parts(brr_dir, name)[1]


def read_sync_status(brr_dir: Path, name: str) -> str | None:
    """Return the marker's :class:`PushStatus` value, or ``None``.

    ``None`` covers both "no marker" and "a marker written before markers
    carried a class" — a caller must treat an unknown class as *unknown*,
    never as divergence. That defaulting is the original defect.
    """
    return _read_sync_marker_parts(brr_dir, name)[0]


def _read_sync_marker_parts(
    brr_dir: Path, name: str,
) -> tuple[str | None, str | None]:
    try:
        text = (brr_dir / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if not text:
        return None, None
    head, _, rest = text.partition("\n")
    if head.startswith(_SYNC_STATUS_PREFIX):
        status = head[len(_SYNC_STATUS_PREFIX):].strip() or None
        reason = rest.strip() or None
        return status, reason
    return None, text


@dataclass
class BranchUpdateResult:
    """Result of fast-forwarding a local branch to another ref."""

    success: bool
    branch: str
    commit: str = ""
    detail: str = ""


class PushStatus(str, Enum):
    """Outcome class for a best-effort git push."""

    OK = "ok"
    REJECTED_NON_FAST_FORWARD = "rejected_non_fast_forward"
    AUTH_FAILED = "auth_failed"
    UNREACHABLE = "unreachable"
    OTHER = "other"


@dataclass(frozen=True)
class PushResult:
    """Structured push outcome with backward-compatible truthiness."""

    status: PushStatus
    detail: str = ""
    remote_url: str = ""
    transport: str = "unknown"

    def __bool__(self) -> bool:
        return self.status is PushStatus.OK


# Git's two environment-level repository overrides. Both outrank *every*
# cwd-based discovery mechanism — `cwd=`, `-C <path>`, even an absolute
# pathspec — so a process that inherits them cannot address any repository
# but the pinned one.
#
# #703 pins these into a strand run's environment on purpose (see
# `daemon._child_git_pin`), which makes the inheritance a hazard for brnrd's
# own code: every git call in this module names the repository it means, and
# under an inherited pin each one would silently report the pinned worktree
# while naming another path. Driven, git 2.43: `git -C <other-repo> rev-parse
# --show-toplevel` under a pin returns the *pinned* tree, exit 0 — a
# confident wrong answer, which is the "an absent reading renders as fine"
# class this repo keeps paying for.
#
# So the rule is symmetric and belongs next to the wrapper it protects: the
# pin governs a *bare* git run from a drifted shell; code that names its repo
# drops the pin. Nothing is lost — brnrd never runs a bare git.
DISCOVERY_OVERRIDE_VARS = ("GIT_DIR", "GIT_WORK_TREE")


def explicit_repo_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """``base`` (default ``os.environ``) minus git's repository overrides.

    For any git invocation that names its own repository via ``cwd=`` or
    ``-C``. See :data:`DISCOVERY_OVERRIDE_VARS`.
    """
    source = os.environ if base is None else base
    return {k: v for k, v in source.items() if k not in DISCOVERY_OVERRIDE_VARS}


# The identity brnrd stamps on commits **it** authors: the dominion capture
# net, kb pages, a worktree salvage commit, a founding deed. Not the user's
# — #475 split those two, and #746 re-opened the split by a different route:
# identity resolution fell through to the shared checkout's config and a
# daemon-made commit was authored *and* committed as the human maintainer,
# in their own repository, with a message they never wrote. The identity is
# stated once, here, next to the other environment invariant every git call
# in this module already obeys. The numeric-id noreply form cannot be
# reassigned to another GitHub login.
BOT_NAME = "brnrd-bot"
BOT_EMAIL = "289761152+brnrd-bot@users.noreply.github.com"


def bot_identity_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """:func:`explicit_repo_env` plus brnrd's own commit identity, pinned.

    For any git invocation that *creates a commit on brnrd's behalf*.

    **Environment, not ``-c user.name=``.** Git resolves identity from
    ``GIT_AUTHOR_*``/``GIT_COMMITTER_*`` *before* it consults config at any
    level, so ``-c`` is the weaker lever: it beats a contaminated
    ``.git/config`` but loses to an inherited ``GIT_AUTHOR_NAME``. Setting
    the four variables is the only form that does not depend on ambient
    state at all — which is the whole property being bought.

    Deliberately narrower than the whole environment: this pins *who
    committed*, and inherits everything else (``GIT_CONFIG_GLOBAL``, PATH,
    proxy settings) so a caller's hermetic or sandboxed environment still
    reaches git.
    """
    env = explicit_repo_env(base)
    env.update({
        "GIT_AUTHOR_NAME": BOT_NAME,
        "GIT_AUTHOR_EMAIL": BOT_EMAIL,
        "GIT_COMMITTER_NAME": BOT_NAME,
        "GIT_COMMITTER_EMAIL": BOT_EMAIL,
    })
    return env


class RepoTreeUnusable(OSError):
    """The working tree git names for a repository is not there. (#746, #1108)

    **Why it subclasses ``OSError`` and not ``RuntimeError``.** The raw
    failure it replaces *is* an ``OSError`` — ``subprocess`` raises
    ``FileNotFoundError`` when handed a ``cwd=`` that does not exist — so
    every existing ``except OSError`` around a git call keeps exactly the
    behaviour it has today, only better informed. ``RuntimeError`` would be
    actively wrong: the CLI's ``_maybe_*`` helpers swallow that class to
    mean *not a brnrd repository*, and answering "no brnrd here" when the
    truth is "your shared git config points at a tree that was deleted" is
    the silent-narrowing failure this guard exists to end. The message is
    the whole value — it must reach a human, not be caught and dropped.
    """


def diagnose_unusable_tree(named: Path, *, asked_from: Path) -> str:
    """Explain why git named *named* as a working tree that isn't there.

    Classified, never one confident sentence. The repair for a stale
    ``core.worktree`` pin (unset it) fixes nothing when the real answer is
    that the caller is standing in a directory that was deleted, and a
    diagnostic that offers the wrong repair has lied twice — once about the
    cause and once about the fix (#786, #792). Where the evidence does not
    separate the cases, this says so rather than picking the likelier
    branch.
    """
    head = (
        f"git says this repository's working tree is {named}, and that "
        f"directory does not exist."
    )
    pin = _config_value(asked_from, "core.worktree")
    if pin and _same_path(Path(pin), named):
        return (
            f"{head}\n"
            f"  cause: core.worktree in the *shared* git config pins it there. "
            f"A git worktree isolates files, never .git/config — a torn-down "
            f"run's worktree can leave this pin behind, and then every git "
            f"command in this checkout answers about a tree that is gone, "
            f"exit 0 throughout.\n"
            f"  repair: git config --unset core.worktree"
        )
    if pin:
        return (
            f"{head}\n"
            f"  cause: unclear. core.worktree is set in the shared git config, "
            f"but to {pin}, which is not the path git resolved — so the pin is "
            f"a repoint of its own and may not be the whole story.\n"
            f"  repair: inspect `git config --get core.worktree` before "
            f"unsetting it; something writes this config that should not."
        )
    return (
        f"{head}\n"
        f"  cause: unclear. No core.worktree pin explains it, so this is "
        f"likely a checkout (or a cwd, asked from {asked_from}) that was "
        f"deleted underneath the process.\n"
        f"  repair: cd to a checkout that exists and retry; if that is where "
        f"you already are, `git worktree prune` and re-check the config."
    )


def _same_path(left: Path, right: Path) -> bool:
    """Path equality that survives symlinks *and* non-existent paths."""
    if left == right:
        return True
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _config_git(cwd: Path, *args: str) -> subprocess.CompletedProcess | None:
    """Run ``git config`` in *cwd* so that a broken ``core.worktree`` can't block it.

    Deliberately its own ``subprocess.run`` rather than :func:`_git`: this
    runs *from* ``_git``'s failure path, and a guard that re-enters the
    thing it is diagnosing is a guard that recurses.

    **``GIT_WORK_TREE``, on purpose, in the one place it is the instrument.**
    Everywhere else in this module that variable is the hazard
    :data:`DISCOVERY_OVERRIDE_VARS` exists to scrub — it outranks every
    cwd-based discovery mechanism, which is exactly why an inherited one is
    poison. Here that ranking is the point: a ``core.worktree`` whose
    *parent* directory is also missing makes git refuse **every** command in
    the repository, ``git config --get`` included (driven, git 2.43:
    ``fatal: Invalid path``, rc 128 — even with ``-f <the config file>``).
    Pointing ``GIT_WORK_TREE`` at a directory that demonstrably exists — the
    caller's own cwd — stops git validating the pin and lets the value be
    read and unset.

    Found by a test, not by reasoning: the first version of this helper used
    a plain ``git config --get``, returned ``""`` for a deep-broken pin, and
    the classifier above cheerfully reported *no pin explains this* about a
    repository that had one. A search that cannot match is not evidence of
    absence, and a diagnostic built on one lies with a straight face.
    """
    env = explicit_repo_env()
    env["GIT_WORK_TREE"] = str(cwd)
    try:
        return subprocess.run(
            ["git", "config", *args],
            cwd=cwd, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env,
        )
    except OSError:
        return None


def _config_value(cwd: Path, key: str) -> str:
    """Read one git config value as seen from *cwd*, or ``""``."""
    result = _config_git(cwd, "--get", key)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git(
    repo_root: Path, *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a git command in *repo_root*.

    *env* defaults to :func:`explicit_repo_env` — the discovery-override
    scrub every call in this module needs because it names its repository.
    Commit-creating calls pass :func:`bot_identity_env`, which is that same
    scrub plus brnrd's identity; nothing else should override it.

    **The cwd guard.** ``subprocess`` raises a bare ``FileNotFoundError``
    naming the *cwd* when the directory is gone — a message that reads like
    a missing git binary and names a path the caller never typed. That is
    how #1108 reached the operator: 312 identical tracebacks, a daemon in a
    5-second restart loop for 27 minutes, and not one line saying which
    config had repointed the checkout. Same failure, diagnosed.
    """
    if not Path(repo_root).is_dir():
        raise RepoTreeUnusable(
            diagnose_unusable_tree(Path(repo_root), asked_from=Path.cwd())
        )
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=explicit_repo_env() if env is None else env,
    )


def heal_stale_brnrd_worktree_pin(cwd: Path) -> bool:
    """Unset a ``core.worktree`` naming a deleted ``.brr/worktrees/<run>``.

    Returns True when it repaired something. The narrowest possible
    self-repair, and the narrowness is the argument for allowing it at all:
    it fires only for a path that (a) does not exist, and (b) sits directly
    under a ``.brr/worktrees`` directory — the tree brnrd creates and tears
    down itself. Both conditions are structural. Neither requires knowing
    who wrote the pin, which #746 never established and #1108 still hasn't.

    Any *other* repoint — a real directory, or a path outside brnrd's own
    worktree root — is left exactly where it is and reported by
    :func:`diagnose_unusable_tree` instead. Somebody may mean that one; no
    one can mean this one.
    """
    pinned = _config_value(cwd, "core.worktree")
    if not pinned:
        return False
    path = Path(pinned)
    if path.exists():
        return False
    if path.parent.name != "worktrees" or path.parent.parent.name != ".brr":
        return False
    result = _config_git(cwd, "--unset", "core.worktree")
    if result is None or result.returncode != 0:
        return False
    print(
        f"[brnrd] unset a stale core.worktree pin: it named {path}, a brnrd "
        f"run worktree that no longer exists. Left in place it makes every "
        f"git command in this checkout answer about a deleted tree at exit 0 "
        f"(#746/#1108)."
    )
    return True


def ensure_git_repo() -> Path:
    """Return the repository root, or raise.

    Two failures, deliberately different classes. *Not a git repository* is
    a ``RuntimeError``, which callers legitimately treat as "no brnrd here"
    and degrade around. A repository whose working tree **does not exist**
    is :class:`RepoTreeUnusable` — a fault with a named cause and a repair
    step, and one that no caller may quietly turn into a shrug.

    Git will not raise here on its own: ``rev-parse --show-toplevel``
    returns a deleted directory and *exits 0* (driven, git 2.43). The
    existence check is the only thing between that answer and a
    ``FileNotFoundError`` thrown by whichever call uses it as a cwd next.
    """
    cwd = Path.cwd()
    try:
        result = _git(cwd, "rev-parse", "--show-toplevel")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Not a Git repository; run `git init` first.") from exc
    root = Path(result.stdout.strip())
    if not root.is_dir():
        raise RepoTreeUnusable(diagnose_unusable_tree(root, asked_from=cwd))
    return root


def current_branch(repo_root: Path) -> str:
    """Return the current branch name, or ``HEAD`` when detached."""
    result = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    if result.returncode != 0:
        return "HEAD"
    return result.stdout.strip() or "HEAD"


def rev_parse(repo_root: Path, ref: str) -> str | None:
    """Return the commit OID for *ref*, or None when it cannot resolve."""
    result = _git(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def absolute_git_dir(repo_root: Path) -> Path | None:
    """The absolute ``.git`` directory serving *repo_root*, or None.

    For a linked worktree this is the worktree's own administrative dir
    (``<main>/.git/worktrees/<name>``), *not* the shared common dir — which
    is exactly the distinction ``GIT_DIR`` needs: pointing it at the common
    dir would put a strand on the main checkout's HEAD (#703).
    """
    try:
        result = _git(repo_root, "rev-parse", "--absolute-git-dir", check=False)
    except OSError:
        # A run root that does not exist on disk: subprocess raises on `cwd`
        # before git ever runs. The caller pins an environment from this, so
        # None (no pin) is the only safe answer — a half-built pin is worse
        # than none.
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


def shared_brr_dir(repo_root: Path) -> Path:
    """Return the shared ``.brr`` dir for a repo or worktree checkout.

    In a normal checkout this is ``repo_root/.brr``. In a git worktree,
    runtime state lives beside the common git dir in the main checkout.
    """
    local = repo_root / ".brr"
    if local.exists():
        return local

    result = _git(repo_root, "rev-parse", "--git-common-dir", check=False)
    if result.returncode != 0:
        return local

    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (repo_root / common_dir).resolve()
    return common_dir.parent / ".brr"


def is_working_tree(path: Path) -> bool:
    """Whether *path* sits inside a git working tree at all.

    Public wrapper over :func:`_is_working_tree` — `gates.cloud` needs this
    predicate to tell "no remote, but still a real checkout" (worth a
    synthesized local identity) apart from "not a git checkout at all"
    (nothing to synthesize one from — the pre-existing no-capabilities
    fallback is correct there, unchanged).
    """
    return _is_working_tree(path)


def _is_working_tree(path: Path) -> bool:
    """Return True when git considers *path* to sit in a working tree.

    Guards ``_git``'s ``cwd=`` against a path that is not a usable
    directory — ``git worktree list`` happily names a checkout that has
    since been deleted, and ``subprocess`` raises ``OSError`` rather than
    returning a non-zero status for that.
    """
    try:
        result = _git(path, "rev-parse", "--is-inside-work-tree", check=False)
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _is_linked_worktree(repo_root: Path) -> bool:
    """Return True when *repo_root* is a linked worktree, not the main one.

    A linked worktree's git dir is ``<common>/worktrees/<name>``; every
    other checkout's git dir *is* the common dir. Both paths come out of
    one ``rev-parse``, so they share whatever normalization git applied —
    comparing them never has to reconcile a caller-supplied path.
    """
    result = _git(
        repo_root, "rev-parse", "--git-dir", "--git-common-dir", check=False,
    )
    if result.returncode != 0:
        return False
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 2:
        return False
    git_dir, common_dir = ((repo_root / line).resolve() for line in lines)
    return git_dir != common_dir


def main_worktree_root(repo_root: Path) -> Path | None:
    """Return the **main** working tree for *repo_root*, or ``None``.

    Deliberately not :func:`shared_brr_dir`'s ``.parent``. That function
    answers *where does runtime state live* and prefers a local ``.brr``,
    so it reports the worktree itself whenever one exists there — a
    perfectly good answer to its own question and the wrong one to this.
    Two questions sharing one derivation is the substitution bug #654 was
    filed about; asking git directly keeps them apart.

    ``git worktree list`` always prints the main working tree first — but
    it prints it *as git models it*, and that is not always a checkout.
    Under ``--separate-git-dir`` the head entry is the **git dir**; for a
    bare repo it is the bare repo. So the head entry is trusted only when
    git agrees it is a working tree (#663 — the docstring shipped in
    ``d1af2924`` claimed the opposite, and two other sites cited it).

    When it is not, there is nothing further to ask. Under
    ``--separate-git-dir`` git records the main working tree's path
    **nowhere** inside the git dir: ``core.worktree`` is unset, no reverse
    pointer is written, and ``<checkout>/.git`` is a one-way edge *into*
    the git dir (driven against git 2.43). The main checkout is therefore
    recoverable only when *repo_root* is itself that checkout, via
    ``rev-parse --show-toplevel``. From a **linked** worktree of such a
    repo the answer does not exist, and this returns ``None``.

    That ``None`` is a named limitation, not an oversight:
    ``account._connected_account_id`` reads it as "no retry" and degrades
    to a project home — the same outcome the wrong path produced, minus
    the lie to the next caller. Closing it needs a repo key that survives
    the trip from a worktree, which is the registry's design, not this
    function's.

    Returns *repo_root* itself for an ordinary repo with no linked
    worktrees, and ``None`` when this is not a git checkout at all.
    """
    result = _git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return None
    head: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            head = Path(line[len("worktree "):].strip())
            break
    if head is None:
        return None
    if _is_working_tree(head):
        return head
    if _is_linked_worktree(repo_root):
        return None
    return toplevel(repo_root)


def toplevel(repo_root: Path) -> Path | None:
    """The working tree git resolves for *repo_root* — or ``None`` if it won't say.

    A repository's own answer to *which tree am I*, and normally
    *repo_root* itself. Not so when ``core.worktree`` in the **common git
    dir** repoints it: a git worktree isolates files, it does not isolate
    ``.git/config``, which the main checkout and every linked worktree
    share. #746 is that mode — one run wrote ``core.worktree = <its own
    worktree>`` into the shared config and for fifteen minutes every git
    command in the maintainer's checkout operated on another run's tree,
    exit 0 throughout, noticed only when the tree was torn down.

    So this is deliberately *not* ``repo_root``-with-extra-steps: the whole
    value is that the two can disagree, and that a caller about to ship
    something can ask rather than assume. ``None`` means git declined to
    answer — this is not a repository — and for an assertion that is a
    failure too, since "I cannot tell which tree this is" is not a
    confirmation.

    **What ``None`` does *not* cover, corrected #1108.** This docstring used
    to claim ``None`` also meant "a ``core.worktree`` pointing somewhere
    deleted". It does not, and never did: ``rev-parse --show-toplevel``
    returns the deleted path and *exits 0* (driven, git 2.43), so that case
    comes back here as a live-looking ``Path`` to a directory that is gone.
    The reading is still correct for every caller — the path is what git
    believes, and it compares unequal to any real checkout, which is how
    ``_publish_tree_mismatch`` catches it — but a caller that hands the
    result to ``cwd=`` gets :class:`RepoTreeUnusable`. Reporting what git
    says is this function's job; existence is the caller's question, and
    :func:`ensure_git_repo` is where brnrd asks it.
    """
    result = _git(repo_root, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


def is_tracked(path: Path) -> bool:
    """Return True if *path* is tracked by Git."""
    try:
        _git(Path.cwd(), "ls-files", "--error-unmatch", str(path))
        return True
    except subprocess.CalledProcessError:
        return False


def is_tracked_in(repo_root: Path, relpath: str) -> bool:
    """Return True if *relpath* is tracked by Git, resolved against *repo_root*.

    Explicit-repo-root sibling of :func:`is_tracked`, which resolves against
    ``Path.cwd()`` instead. A daemon-side migration (never the operator's
    shell) must not depend on process cwd to answer a question this
    consequential — see ``account._untrack_newly_ignored``.
    """
    result = _git(repo_root, "ls-files", "--error-unmatch", relpath, check=False)
    return result.returncode == 0


def untrack_cached(repo_root: Path, relpath: str) -> bool:
    """``git rm --cached`` *relpath* in *repo_root* — drop it from the index,
    leave the working-tree file alone. Returns whether the removal succeeded.

    A ``.gitignore`` line never untracks a file the index already knows
    about (git says so itself); this is the other half a migration needs to
    actually repair a home created before the rule existed, rather than
    merely keeping it out of *future* commits. Best-effort: a failure here
    must not abort whatever bootstrap step triggered it.
    """
    result = _git(repo_root, "rm", "--cached", "--quiet", relpath, check=False)
    return result.returncode == 0


def branch_exists(repo_root: Path, branch: str) -> bool:
    """Return True if *branch* exists locally."""
    result = _git(repo_root, "show-ref", "--verify", f"refs/heads/{branch}", check=False)
    return result.returncode == 0


def list_local_branches(repo_root: Path) -> list[str]:
    """Return local branch names sorted by ref name.

    Used by the daemon's pre-task sync to enumerate every branch with a
    potential remote counterpart for the best-effort ff sweep. Returns an
    empty list on detached HEAD or when ``git for-each-ref`` fails — the
    sync layer treats missing branches as a no-op.
    """
    result = _git(
        repo_root, "for-each-ref",
        "--format=%(refname:short)", "refs/heads/", check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def branch_head(repo_root: Path, branch: str) -> str | None:
    """Return the OID for local *branch*, or None when it is missing."""
    return rev_parse(repo_root, f"refs/heads/{branch}")


def valid_branch_name(repo_root: Path, branch: str) -> bool:
    """Return True when *branch* is acceptable as a local branch name."""
    if not branch or branch == "HEAD":
        return False
    result = _git(repo_root, "check-ref-format", "--branch", branch, check=False)
    return result.returncode == 0


def default_branch(repo_root: Path) -> str | None:
    """Best-effort local default branch name, falling back to current branch."""
    remote_head = _git(
        repo_root, "symbolic-ref", "--quiet", "--short",
        "refs/remotes/origin/HEAD", check=False,
    )
    if remote_head.returncode == 0:
        ref = remote_head.stdout.strip()
        if "/" in ref:
            candidate = ref.split("/", 1)[1]
            if branch_exists(repo_root, candidate):
                return candidate

    for candidate in ("main", "master"):
        if branch_exists(repo_root, candidate):
            return candidate

    current = current_branch(repo_root)
    if current != "HEAD":
        return current
    return "HEAD" if rev_parse(repo_root, "HEAD") else None


def branch_checkout_path(repo_root: Path, branch: str) -> Path | None:
    """Return the worktree path where *branch* is checked out, if any."""
    result = _git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return None

    current_path: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
        elif line.startswith("branch ") and current_path is not None:
            ref = line.split(" ", 1)[1]
            if ref == f"refs/heads/{branch}":
                return current_path
        elif line == "":
            current_path = None
    return None


def is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    """Return True when *ancestor* is reachable from *descendant*."""
    result = _git(
        repo_root, "merge-base", "--is-ancestor", ancestor, descendant,
        check=False,
    )
    return result.returncode == 0


def fast_forward_branch(
    repo_root: Path,
    branch: str,
    source_ref: str,
    *,
    expected_old_oid: str | None = None,
) -> BranchUpdateResult:
    """Fast-forward local *branch* to *source_ref* without guessing checkout state.

    If *branch* is checked out in the daemon's repo, use ``git merge
    --ff-only`` so the worktree updates. If it is not checked out,
    advance the ref directly with ``git update-ref``. A branch checked
    out in some other worktree is refused because updating it behind
    that worktree's back would leave a confusing checkout.
    """
    if not valid_branch_name(repo_root, branch):
        return BranchUpdateResult(
            success=False,
            branch=branch,
            detail=f"invalid branch name: {branch}",
        )

    source_oid = rev_parse(repo_root, source_ref)
    if source_oid is None:
        return BranchUpdateResult(
            success=False,
            branch=branch,
            detail=f"cannot resolve source ref: {source_ref}",
        )

    old_oid = branch_head(repo_root, branch)
    if expected_old_oid is not None and old_oid != expected_old_oid:
        return BranchUpdateResult(
            success=False,
            branch=branch,
            detail=f"{branch} changed while task was running",
        )
    if old_oid is not None and not is_ancestor(repo_root, old_oid, source_oid):
        return BranchUpdateResult(
            success=False,
            branch=branch,
            detail=f"{source_ref} is not a fast-forward of {branch}",
        )

    if current_branch(repo_root) == branch:
        result = _git(repo_root, "merge", "--ff-only", source_ref, check=False)
        if result.returncode == 0:
            commit = rev_parse(repo_root, "HEAD") or source_oid
            return BranchUpdateResult(success=True, branch=branch, commit=commit)
        return BranchUpdateResult(
            success=False,
            branch=branch,
            detail=result.stderr.strip() or result.stdout.strip(),
        )

    checkout_path = branch_checkout_path(repo_root, branch)
    if checkout_path is not None and checkout_path.resolve() != repo_root.resolve():
        return BranchUpdateResult(
            success=False,
            branch=branch,
            detail=f"{branch} is checked out at {checkout_path}",
        )

    ref = f"refs/heads/{branch}"
    args = ["update-ref", ref, source_oid]
    if old_oid is not None:
        args.append(old_oid)
    result = _git(repo_root, *args, check=False)
    if result.returncode == 0:
        return BranchUpdateResult(success=True, branch=branch, commit=source_oid)
    return BranchUpdateResult(
        success=False,
        branch=branch,
        detail=result.stderr.strip() or result.stdout.strip(),
    )


def branch_upstream(repo_root: Path, branch: str) -> str | None:
    """Return the upstream ref for *branch*, e.g. ``origin/main``."""
    result = _git(
        repo_root, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}",
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def branch_remote(repo_root: Path, branch: str) -> str | None:
    """Return the configured remote for *branch*, if one exists."""
    result = _git(repo_root, "config", f"branch.{branch}.remote", check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def default_remote(repo_root: Path) -> str | None:
    """Return ``origin`` if present, otherwise the first configured remote."""
    result = _git(repo_root, "remote", check=False)
    if result.returncode != 0:
        return None
    remotes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if "origin" in remotes:
        return "origin"
    return remotes[0] if remotes else None


def remote_url(repo_root: Path, remote: str) -> str | None:
    """Return the URL configured for *remote*, or ``None``.

    Wraps ``git remote get-url <remote>``. Returns ``None`` for
    unknown remotes or any git failure so callers can fall through to
    "no link" without raising.
    """
    if not remote:
        return None
    result = _git(repo_root, "remote", "get-url", remote, check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def exclude_from_git(repo_root: Path, pattern: str) -> Path:
    """Add *pattern* to this checkout's ``.git/info/exclude``.

    Linked worktrees may keep the exclude file outside *repo_root*, so ask
    git for its path rather than assuming ``.git`` is a directory.
    """
    result = _git(repo_root, "rev-parse", "--git-path", "info/exclude")
    exclude = Path(result.stdout.strip())
    if not exclude.is_absolute():
        exclude = (repo_root / exclude).resolve()
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if pattern in {line.strip() for line in current.splitlines()}:
        return exclude
    with exclude.open("a", encoding="utf-8") as fh:
        if current and not current.endswith("\n"):
            fh.write("\n")
        fh.write(f"{pattern}\n")
    return exclude


def remote_branch_exists(repo_root: Path, remote: str, branch: str) -> bool:
    """Return True if *branch* exists on *remote* (best-effort, networked).

    Wraps ``git ls-remote --heads``. Any git failure (no network, unknown
    remote) reads as "absent" so callers fall through to local creation.
    """
    if not remote or not branch:
        return False
    result = _git(repo_root, "ls-remote", "--heads", remote, branch, check=False)
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def create_orphan_branch(
    repo_root: Path,
    branch: str,
    *,
    message: str = "initialize",
) -> str | None:
    """Create *branch* as an orphan root commit over the empty tree.

    Uses plumbing (``mktree`` → ``commit-tree`` → ``update-ref``) so it
    works on any git version and never touches the main worktree's index
    or HEAD. Returns the new commit OID, the existing head if *branch*
    already exists, or ``None`` on failure (e.g. no committer identity).
    """
    if branch_exists(repo_root, branch):
        return branch_head(repo_root, branch)

    # These two are the module's only hand-rolled ``subprocess.run`` git
    # calls, and both were missing the environment every ``_git`` call gets:
    # unscrubbed they address an inherited ``GIT_DIR`` pin instead of
    # *repo_root* (#703), and unpinned the root commit is authored by
    # whatever identity config resolution finds (#746).
    env = bot_identity_env()
    tree = subprocess.run(
        ["git", "mktree"],
        cwd=repo_root, input="", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )
    if tree.returncode != 0:
        return None
    tree_oid = tree.stdout.strip()

    commit = subprocess.run(
        ["git", "commit-tree", tree_oid, "-m", message],
        cwd=repo_root, input="", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )
    if commit.returncode != 0:
        return None
    commit_oid = commit.stdout.strip()

    update = _git(
        repo_root, "update-ref", f"refs/heads/{branch}", commit_oid, check=False,
    )
    if update.returncode != 0:
        return None
    return commit_oid


def add_worktree(
    repo_root: Path,
    worktree_path: Path,
    *,
    branch: str,
    create_branch: bool = False,
    start_point: str | None = None,
    track: bool = False,
) -> None:
    """Add a git worktree at *worktree_path* checked out on *branch*.

    With ``create_branch=False`` (default) the local *branch* must already
    exist. With ``create_branch=True`` a new *branch* is sprouted from
    *start_point*; ``track=True`` adds ``--track`` so it follows that
    start point's remote. Raises ``RuntimeError`` with git's message on
    failure.
    """
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["worktree", "add"]
    if track:
        args.append("--track")
    if create_branch:
        args += ["-b", branch]
    args.append(str(worktree_path))
    args.append(start_point or branch if create_branch else branch)
    result = _git(repo_root, *args, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"failed to add worktree {worktree_path}")


def fetch_branch(repo_root: Path, remote: str, branch: str) -> bool:
    """Fetch *branch* from *remote*, updating its remote-tracking ref. Best-effort."""
    if not remote or not branch:
        return False
    result = _git(repo_root, "fetch", remote, branch, check=False)
    return result.returncode == 0


def push_branch(
    repo_root: Path,
    remote: str,
    branch: str,
    *,
    set_upstream: bool = True,
) -> PushResult:
    """Push local *branch* to *remote* and classify any failure.

    ``PushResult`` keeps the old boolean contract: successful results are
    truthy and every failure is falsey.
    """
    configured_url = remote_url(repo_root, remote) or remote
    transport = _remote_transport(configured_url)
    if not remote or not branch:
        return PushResult(
            PushStatus.OTHER,
            detail="remote and branch are required",
            remote_url=configured_url,
            transport=transport,
        )
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args += [remote, branch]
    result = _git(repo_root, *args, check=False)
    if result.returncode == 0:
        return PushResult(
            PushStatus.OK,
            remote_url=configured_url,
            transport=transport,
        )
    detail = result.stderr.strip()
    return PushResult(
        _classify_push_failure(detail),
        detail=detail,
        remote_url=configured_url,
        transport=transport,
    )


def _remote_transport(url: str) -> str:
    """Name the configured transport for a remote URL."""
    value = url.strip()
    lowered = value.lower()
    if lowered.startswith("ssh://") or (
        "://" not in value
        and ":" in value
        and "@" in value.split(":", 1)[0]
    ):
        return "SSH"
    if lowered.startswith("https://"):
        return "HTTPS"
    if lowered.startswith("http://"):
        return "HTTP"
    if lowered.startswith("file://") or value.startswith(("/", "./", "../")):
        return "local"
    if "://" in value:
        return value.split("://", 1)[0].upper()
    return "unknown"


def _classify_push_failure(stderr: str) -> PushStatus:
    """Classify git-push stderr without treating an unknown as divergence."""
    detail = stderr.lower()
    non_fast_forward = (
        "non-fast-forward",
        "(fetch first)",
        "updates were rejected because the remote contains work",
    )
    if any(marker in detail for marker in non_fast_forward):
        return PushStatus.REJECTED_NON_FAST_FORWARD

    auth_failed = (
        "authentication failed",
        "permission denied (publickey)",
        "could not read username",
        "invalid username or password",
        "http basic: access denied",
        "repository not found",
        "remote: not found",
        "requested url returned error: 401",
        "requested url returned error: 403",
        "requested url returned error: 404",
    )
    if any(marker in detail for marker in auth_failed):
        return PushStatus.AUTH_FAILED

    unreachable = (
        "could not resolve host",
        "could not resolve hostname",
        "connection timed out",
        "connection refused",
        "network is unreachable",
        "couldn't connect to server",
        "failed to connect to",
        "no route to host",
        "connection reset by peer",
    )
    if any(marker in detail for marker in unreachable):
        return PushStatus.UNREACHABLE
    return PushStatus.OTHER


def format_push_failure(
    result: PushResult,
    *,
    branch: str,
    remote: str,
    remote_label: str,
    repo_path: Path,
) -> str:
    """Render one truthful capture marker sentence for a failed push."""
    if result.status is PushStatus.REJECTED_NON_FAST_FORWARD:
        return (
            f"push of {branch} to {remote} was rejected — {remote_label} "
            f"has diverged; reconcile by hand (fetch / merge / push) in "
            f"{repo_path}"
        )
    if result.status is PushStatus.AUTH_FAILED:
        return (
            f"push of {branch} to {remote} failed authentication against "
            f"{result.remote_url} over {result.transport} — check remote "
            f"access in {repo_path}"
        )
    if result.status is PushStatus.UNREACHABLE:
        return (
            f"push of {branch} to {remote} could not reach {result.remote_url} "
            f"over {result.transport} — check the network or remote "
            f"availability in {repo_path}"
        )
    return (
        f"push of {branch} to {remote} failed for an unclassified reason "
        f"against {result.remote_url} over {result.transport} — retry from "
        f"{repo_path} to inspect git's error"
    )


# Git trailer stamped on every brr-created commit so brnrd's metadata-only
# conversation graph can re-derive conversation linkage from any branch
# (kb/plan-conversation-id-propagation.md). The value is the existing
# ``conversation_key`` string — no separate id scheme.
CONVERSATION_TRAILER = "Brnrd-Conversation-Id"

# Git trailer stamped on every brr-created commit to the account-knowledge
# repo, identifying the one run that owns it (#565). Produce derives kb
# relics by filtering a shared-checkout commit window against this trailer —
# see ``knowledge.committed_pages_in_window`` — so a stopped run's dashboard
# node never picks up a concurrent sibling's kb pages.
RUN_ID_TRAILER = "Brnrd-Run-Id"


def commit_all(
    worktree_path: Path, message: str, *,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> bool:
    """Stage everything and commit in *worktree_path*. Best-effort; returns success.

    ``conversation_id`` (the task's ``conversation_key``, when known) is
    stamped as a ``Brnrd-Conversation-Id`` git trailer; ``run_id`` (the
    task's own id) as a ``Brnrd-Run-Id`` trailer. Either empty/None means no
    trailer — never stamp an empty value.

    Authored and committed as brnrd (:func:`bot_identity_env`). This is the
    funnel for nine of brnrd's eleven commit sites — the dominion capture
    net, kb capture, worktree salvage, policy and config proposals — and
    every one of them writes into a repository whose git config belongs to
    somebody else. Pinning here rather than at each caller is what stops the
    tenth caller, written later, from inheriting the maintainer's name
    (#746, re-opening #475 by a different route).
    """
    add = _git(worktree_path, "add", "-A", check=False)
    if add.returncode != 0:
        return False
    args = ["commit", "-m", message]
    key = (conversation_id or "").strip()
    if key:
        args += ["--trailer", f"{CONVERSATION_TRAILER}: {key}"]
    run = (run_id or "").strip()
    if run:
        args += ["--trailer", f"{RUN_ID_TRAILER}: {run}"]
    commit = _git(worktree_path, *args, check=False, env=bot_identity_env())
    return commit.returncode == 0


# Marker line inside every hook this function installs, so a later brnrd
# version (or a maintainer) can tell "ours, safe to rewrite" from
# "hand-customized, leave alone" without diffing the whole script. Shared
# across every repo this hook is installed into (account-knowledge, a
# project checkout) — one grammar, one marker. Text preserved verbatim from
# the original account-knowledge-only installer (knowledge.py, #565) so a
# hook already on disk from that version still self-identifies as ours.
_RUN_ID_HOOK_MARKER = "# brnrd: stamp Brnrd-Run-Id trailer (#565) — do not hand-edit"

# The newline guard is load-bearing, not tidiness. ``git commit`` hands the
# hook a message file ending in a newline, so ``interpret-trailers`` opens a
# fresh paragraph and the trailer parses. ``git merge -m`` hands it a
# message with **no trailing newline** — the trailer is then appended to the
# subject's own paragraph, which means `%(trailers:key=…)` reports nothing
# and `%s` renders as ``Merge feat Brnrd-Run-Id: run-…``. Merging a
# reviewed branch is this project's canonical produce event, so without the
# guard every host-run merge would be silently dropped by the identity
# filter it is supposed to satisfy. Measured, not reasoned about.
_RUN_ID_HOOK_SCRIPT = (
    "#!/bin/sh\n"
    f"{_RUN_ID_HOOK_MARKER}\n"
    'if [ -n "$BRR_RUN_ID" ]; then\n'
    # Newline guard — load-bearing, see comment above.
    '  if [ -s "$1" ] && [ -n "$(tail -c 1 "$1")" ]; then printf \'\\n\' >> "$1"; fi\n'
    # Close-keyword predicate (#652 -> #653 -> #657 -> #839): a run may not
    # put a close keyword + #NNN anywhere but the start of a line, and a
    # line-start close may carry nothing after the ref except more refs.
    # Refused when BRR_RUN_ID is set (run commits only); a maintainer typing
    # the same line by hand is not bound here (see issue for the trade-off).
    #
    # The patterns, the three remedy sets and the whole argument for them live
    # in `closekeyword.py` -- one owner, because GitHub closes on a **pull
    # request body** with the same authority and #749 died of a guard that
    # covered this channel only (#839). This is not a second copy of the rule
    # rendered for sh; it is the rule, rendered for sh. `hook_script_body()`
    # is byte-frozen against the literal that shipped with #657, so this
    # interpolation changed no character of the installed hook.
    f"{closekeyword.hook_script_body()}"
    f'  git interpret-trailers --if-exists doNothing '
    f'--trailer "{RUN_ID_TRAILER}=$BRR_RUN_ID" --in-place "$1"\n'
    "fi\n"
)


def ensure_run_id_hook(repo_root: Path) -> None:
    """Install a ``commit-msg`` hook stamping ``$BRR_RUN_ID`` as a trailer.

    A resident commits directly, mid-run, in a shell (``git commit`` typed
    by hand) — not through :func:`commit_all`, so a Python-level ``run_id=``
    parameter never sees that commit. brnrd's own runner process exports
    ``BRR_RUN_ID`` into every run's environment; this hook is the
    code-only interception point that turns it into the same
    ``Brnrd-Run-Id`` trailer :func:`commit_all` stamps for an automated
    commit — no prompt file has to teach a resident to type ``--trailer``
    by hand. Originally installed on the account-knowledge checkout alone
    (#565); a project checkout needs the identical hook so
    ``relics.collection_scope``'s shared-window fallback can filter a host
    run's commits by identity too (#575) — one hook, two checkouts, same
    grammar.

    A hand commit made with no ``BRR_RUN_ID`` in its environment (a
    maintainer, logged in directly) leaves the message untouched —
    credited to no run, never misattributed by a fallback. Idempotent and
    best-effort: only (re)writes the hook when it is absent or still
    carries this function's own marker, so a hook a maintainer customized
    by hand is left alone; any OSError is swallowed, matching every other
    capture-net step.
    """
    hooks_dir = repo_root / ".git" / "hooks"
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "commit-msg"
        if hook_path.exists():
            existing = hook_path.read_text(encoding="utf-8", errors="replace")
            if _RUN_ID_HOOK_MARKER not in existing:
                return
            if existing == _RUN_ID_HOOK_SCRIPT:
                return
        hook_path.write_text(_RUN_ID_HOOK_SCRIPT, encoding="utf-8")
        hook_path.chmod(0o755)
    except OSError:
        pass


def worktree_dirty(worktree_path: Path) -> bool:
    """Return True if *worktree_path* has staged, unstaged, or untracked changes.

    A cheap pre-check so callers can skip a no-op commit (``git commit``
    fails with a non-zero exit when there's nothing to commit, which is
    indistinguishable from a real error). An unreadable / non-repo path
    reports clean rather than raising — callers treat capture as
    best-effort.
    """
    result = _git(worktree_path, "status", "--porcelain", check=False)
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def dirty_paths(worktree_path: Path) -> set[str]:
    """The set of paths ``git status --porcelain`` reports in *worktree_path*.

    The path-set sibling of :func:`worktree_dirty`, for callers that need to
    compare two readings rather than ask a yes/no. Used by #703's stray-write
    check: a strand whose deliverable landed in the *shared* checkout leaves
    new entries here that were not present when the run was dispatched.

    Paths only — the two-character status prefix is dropped, because the
    question is *which files*, not how they differ, and a file's status can
    legitimately change between readings (untracked, then staged). A rename
    (``R  old -> new``) contributes the destination. Unreadable or non-repo
    reports empty, matching :func:`worktree_dirty`'s best-effort posture.
    """
    result = _git(worktree_path, "status", "--porcelain", check=False)
    if result.returncode != 0:
        return set()
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        entry = line[3:].strip() if len(line) > 3 else ""
        if not entry:
            continue
        # `R  old -> new` / `C  old -> new`: the destination is the path that
        # now exists in the tree.
        _, sep, dest = entry.partition(" -> ")
        paths.add((dest if sep else entry).strip('"'))
    return paths


def commits_owned_by_run(
    repo_root: Path, start_oid: str, run_id: str,
) -> list[str]:
    """Commit SHAs in ``start_oid..HEAD`` whose :data:`RUN_ID_TRAILER` is *run_id*.

    Identity, not proximity: a commit with no trailer, or a sibling run's
    trailer, is excluded rather than defaulting into this run's credit (#565).
    The trailer arrives two ways and this reads both — :func:`commit_all`
    stamps it for an automated commit, and :func:`ensure_run_id_hook`'s
    ``commit-msg`` hook stamps a commit a resident types by hand.

    That second path is what makes this an *attribution* primitive rather
    than a time window, and #703 leans on it: the hook lives in the shared
    ``.git/hooks`` (a linked worktree resolves to the same file), so a
    strand whose cwd drifted into the host checkout stamped its stray
    commits there with its own run id. Driven against a real checkout —
    ``test_gitops.py::test_commits_owned_by_run_*``.

    Empty on an unresolvable range, an unreadable repo, or no match.
    """
    result = _git(
        repo_root, "log",
        f"--format=%H%x00%(trailers:key={RUN_ID_TRAILER},valueonly,separator=%x2C)",
        f"{start_oid}..HEAD",
        check=False,
    )
    if result.returncode != 0:
        return []
    owned: list[str] = []
    for line in result.stdout.split("\n"):
        if not line:
            continue
        sha, _, value = line.partition("\0")
        if sha and value.strip() == run_id:
            owned.append(sha)
    return owned
