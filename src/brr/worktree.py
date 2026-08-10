"""Git worktree/clone helpers for run-isolated execution.

Each run gets a fresh tree at ``.brr/worktrees/<run-id>/`` on a dedicated
``brr/<run-id>`` branch sprouted from the resolved seed ref. The agent runs
inside that sandbox and decides how its work should land:

- Leaving commits on ``brr/<run-id>`` follows the daemon's branch
  plan: finalization fast-forwards a resolved auto-land target, or
  preserves the run branch when no safe target exists.
- Switching to a different branch (``git switch -c feat/foo`` or
  ``git switch existing``) records a runtime branch choice, and the
  branch is preserved as-is on cleanup.

**Two shapes share this path template.** :func:`create` gives a run a
*linked* ``git worktree`` — cheap, but its ``.git/config``, ``refs/stash``,
and index namespace are shared with the main checkout by construction
(there is exactly one common git dir). :func:`create_clone` gives a run its
own ``git clone --shared`` — its own ``.git`` directory entirely (own
config, own stash, own HEAD/index), objects still borrowed cheaply from the
source via ``.git/objects/info/alternates`` rather than copied. #746 is the
reason the second shape exists: a strand's own ``git config`` write or
``git stash`` landed in the *host's* shared state, twice, because a linked
worktree never isolated either. Strand/worker checkouts use
:func:`create_clone`; the resident's own ``worktree``-env runs keep
:func:`create` — see ``envs.WorktreeEnv.prepare``, which picks per run
based on ``daemon._is_strand``.

**The `--shared` clone's one open window, and how it's closed.** Objects a
clone references only via its alternates file are the *source* repo's to
keep or prune — if the source's own ``git gc`` ever collected an object the
clone still needs (because nothing in the source points at it any more),
the clone would corrupt. Two guards, not one: :func:`create_clone` sets
``gc.auto=0`` in the clone's own config (belt), and
:func:`land_clone_branch` fetches the clone's new commits into the host
checkout's own refs the moment a run finishes (braces) — once a host-side
ref points at them, the host's *own* gc can never consider them
unreferenced, closing the window from the other end. The window that
remains is only ever "a commit exists solely in a live clone's own object
store, not yet landed" — the ordinary lifetime of any unpushed commit
anywhere, not a new hazard.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import gitops


def _git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in *repo_root*.

    ``env`` drops git's environment-level repository overrides — see
    ``gitops.explicit_repo_env``. Every call here names its worktree, and a
    ``GIT_DIR`` inherited from a pinned strand run (#703) would outrank that.
    """
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=gitops.explicit_repo_env(),
    )


WorktreeKind = Literal["brr", "external"]


@dataclass(frozen=True)
class WorktreeInfo:
    """One worktree of this repo, other than the main checkout.

    ``kind`` names which population the entry belongs to:

    - ``"brr"`` — a run worktree brnrd minted under ``.brr/worktrees/``.
    - ``"external"`` — a worktree of this same repo living anywhere else: a
      resident's hand-made ``/tmp/brr-wt-<slug>`` tree (which the ``host``
      environment's standing invariant *mandates*, precisely so a run stays
      out of the maintainer's checkout), a Shell's own agent-isolation
      directory, a maintainer's scratch checkout.

    ``run_id`` is meaningful only for ``kind == "brr"``, where the layout
    ``.brr/worktrees/<run-id>`` makes the directory name the run id. An
    external worktree's directory name is *not* a run id — ``/tmp/brr-wt-mood``
    would yield ``"brr-wt-mood"`` — and passing that off as one silently
    mis-resolves every consumer that joins it into a path (a missing
    ``.brr/runs/<run-id>/run.md`` reads as "no new commit" rather than as "no
    such run"). So it carries ``None``, and consumers must handle it.
    """

    path: Path
    run_id: str | None
    branch: str
    kind: WorktreeKind


class BranchCheckedOutError(RuntimeError):
    """Raised when a branch is already checked out in another worktree."""

    def __init__(self, branch: str, checkout_path: Path):
        self.branch = branch
        self.checkout_path = checkout_path
        super().__init__(f"{branch} is checked out at {checkout_path}")


def run_branch_name(run_id: str) -> str:
    """Return the standard run branch name brr creates for a worktree."""
    return f"brr/{run_id}"


def _resolved(path: Path) -> Path:
    """Best-effort canonical form, for *comparison* only.

    Both sides of every path test here come from different producers (git's
    porcelain vs a config-derived ``Path``), so a symlinked checkout or a
    ``..`` segment would otherwise make two names for one directory compare
    unequal. The un-resolved path is what gets stored: it is the name git
    itself prints, and the one an operator can paste back.
    """
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - defensive; resolve() is non-strict
        return path


def _classify_worktree(path: Path, branch: str, worktrees_dir: Path) -> WorktreeInfo:
    """Tag one parsed worktree ``brr`` or ``external``. Never drops it."""
    try:
        _resolved(path).relative_to(_resolved(worktrees_dir))
    except ValueError:
        return WorktreeInfo(path=path, run_id=None, branch=branch, kind="external")
    return WorktreeInfo(
        path=path, run_id=path.name, branch=branch, kind="brr",
    )


def list_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    """Every worktree of this repo except the main checkout, classified.

    Parses ``git worktree list --porcelain`` and tags each entry ``brr``
    (under ``.brr/worktrees/``) or ``external`` (anywhere else) — it does not
    *drop* the latter. It used to: a filter by path prefix, silent, no count.
    That made a whole population invisible to every consumer of this list,
    and the invisible population was not incidental — the ``host``
    environment's standing invariant is *pin* ``git worktree add
    /tmp/brr-wt-<slug>``, so the rule that keeps a run out of the
    maintainer's tree is the same rule that put its work where nothing could
    see it, in a directory that does not survive a reboot (#721).

    The **main checkout is excluded**, deliberately and not as a filter's
    leftover: it is the repository rather than a worktree of it, it is the
    one tree its owner is already looking at, and counting its working-tree
    dirt would light the wake facet on every wake in which someone was
    simply editing.

    Callers whose subject is brnrd's own housekeeping want
    :func:`list_brr_worktrees` — ask for the narrow set by name rather than
    re-filtering this one, so which population a caller means stays legible
    at the call site.
    """
    from . import gitops

    brr_dir = gitops.shared_brr_dir(repo_root)
    worktrees_dir = brr_dir / "worktrees"
    main_checkout = _resolved(brr_dir.parent)

    result = _git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return []

    entries: list[WorktreeInfo] = []
    current_path: Path | None = None
    current_branch: str = ""

    def flush() -> None:
        nonlocal current_path, current_branch
        if current_path is not None and _resolved(current_path) != main_checkout:
            entries.append(
                _classify_worktree(current_path, current_branch, worktrees_dir)
            )
        current_path = None
        current_branch = ""

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
            current_branch = ""
        elif line.startswith("branch "):
            ref = line.split(" ", 1)[1]
            current_branch = ref.removeprefix("refs/heads/")
        elif line == "":
            # Records are blank-line separated; a detached worktree simply
            # has no ``branch`` line, so its branch stays "".
            flush()

    flush()
    return entries


def list_brr_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    """Only the run worktrees brnrd itself minted under ``.brr/worktrees/``.

    The narrow half of :func:`list_worktrees`, for callers whose subject is
    brnrd's own housekeeping rather than the repo's whole worktree
    population. The distinction load-bears wherever a count drives a remedy:
    "these accumulated, prune them" is sound advice about trees brnrd created
    and abandoned, and wrong — destructive, even — about a Shell's live agent
    isolation directory or a resident's ``/tmp`` tree holding unpushed work.
    """
    return [wt for wt in list_worktrees(repo_root) if wt.kind == "brr"]


def path_for(repo_root: Path, run_id: str) -> Path:
    """Return the worktree path for *run_id*."""
    from . import gitops

    return gitops.shared_brr_dir(repo_root) / "worktrees" / run_id


def create(repo_root: Path, run_id: str, *, base_ref: str = "HEAD") -> tuple[Path, str]:
    """Create a fresh run worktree on a new ``brr/<run_id>`` branch.

    Always sprouts a new branch from *base_ref* so worktree creation
    never collides with a branch that's checked out elsewhere. Returns
    ``(worktree_path, branch_name)``.
    """
    worktree_path = path_for(repo_root, run_id)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        raise RuntimeError(f"worktree already exists: {worktree_path}")

    branch = run_branch_name(run_id)
    args = ["worktree", "add", "-b", branch, str(worktree_path), base_ref]
    result = _git(repo_root, *args, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"failed to create worktree {worktree_path}")
    return worktree_path, branch


def create_clone(repo_root: Path, run_id: str, *, base_ref: str = "HEAD") -> tuple[Path, str]:
    """Create a fresh run *clone* on a new ``brr/<run_id>`` branch (#746).

    Same contract and same path template as :func:`create` — same
    ``(worktree_path, branch_name)`` return, same ``.brr/worktrees/<run_id>``
    location (so every existing consumer that mounts/bind-mounts that path,
    e.g. Docker's ``-v repo_root:repo_root``, needs no change) — but the
    child gets its own ``.git`` directory via ``git clone --shared`` instead
    of a linked ``git worktree add``. See the module docstring for why.

    *base_ref* is resolved to a commit OID in *repo_root* **before**
    cloning, and the clone is created ``--no-checkout`` then checked out at
    that OID directly, rather than passed as a branch name to ``git
    clone``/``git checkout -b``. A clone (unlike a linked worktree) does not
    share *repo_root*'s local branch namespace — only the branch checked
    out in *repo_root* at clone time becomes a local branch there, and every
    other branch is only a `refs/remotes/origin/*` name — so an unresolved
    *base_ref* naming some other local branch would silently pick up the
    wrong start point via git's remote-branch DWIM instead of failing
    loudly. A bare OID sidesteps the ambiguity entirely.
    """
    worktree_path = path_for(repo_root, run_id)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        raise RuntimeError(f"clone already exists: {worktree_path}")

    base_oid = gitops.rev_parse(repo_root, base_ref)
    if base_oid is None:
        raise RuntimeError(f"cannot resolve base_ref {base_ref!r} in {repo_root}")

    result = _git(
        repo_root, "clone", "--shared", "--no-checkout", "--origin", "origin",
        str(repo_root), str(worktree_path), check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"failed to clone {worktree_path}")

    # Belt: see the module docstring's alternates/gc paragraph. Best-effort —
    # a clone that can't take this config is still a clone; the fetch-at-land
    # brace is what actually has to hold.
    _git(worktree_path, "config", "gc.auto", "0", check=False)

    # Record which checkout this clone belongs to, for
    # `gitops.shared_brr_dir` — a clone's own `--git-common-dir` names
    # itself, not the host, so nothing else can answer "where is the shared
    # `.brr`" for a tree resolved to this clone's path. Inside `.git/`, never
    # the checked-out working tree: it must never appear in `git status`,
    # never be committable, and never collide with anything the repo itself
    # tracks. Best-effort — an unwritable `.git` still yields a working
    # clone, just one `shared_brr_dir` has to find the slower way.
    try:
        (worktree_path / ".git" / gitops._CLONE_HOST_ROOT_MARKER).write_text(
            f"{repo_root}\n", encoding="utf-8",
        )
    except OSError:
        pass

    # `git clone <local-path> <dest>` points `origin` at the *local path* it
    # cloned from, not the real remote — fine for objects (that's what
    # --shared borrows), wrong for anything that resolves the repository
    # from the remote URL: `gh pr view`, `gh issue view`, a credential
    # helper matching on host. Repoint it at repo_root's own origin so a
    # strand's `gh` calls resolve the real repository, not a filesystem
    # path only this host can read.
    real_origin = gitops.remote_url(repo_root, "origin")
    if real_origin:
        _git(worktree_path, "remote", "set-url", "origin", real_origin, check=False)

    branch = run_branch_name(run_id)
    result = _git(worktree_path, "checkout", "-b", branch, base_oid, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            detail or f"failed to create branch {branch} in {worktree_path}"
        )
    return worktree_path, branch


def remove_clone(worktree_path: Path) -> None:
    """Delete a run clone's directory (#746).

    A clone has no linked-worktree registration to remove from the host's
    ``.git/worktrees/`` bookkeeping (:func:`remove`'s ``git worktree
    remove``) and no shared ``core.worktree`` pin to clear
    (:func:`clear_stale_worktree_pin`) — it is just a directory. Callers
    must land whatever branch matters (:func:`land_clone_branch`) *before*
    calling this: the clone's own object store is the only copy of any
    commit that has not yet been landed or pushed, and this deletes it
    unconditionally.

    Best-effort: a directory that resists deletion is reported by neither
    raising nor pretending — the same "leak rather than lose work" posture
    :func:`has_uncommitted_changes`'s caller already applies before ever
    reaching here.
    """
    shutil.rmtree(worktree_path, ignore_errors=True)


def land_clone_branch(repo_root: Path, clone_path: Path, branch: str) -> gitops.BranchUpdateResult:
    """Fetch *branch* from a run clone into *repo_root* as a local ref (#746).

    ``daemon.publish`` reads and pushes branches as plain local refs in
    *repo_root* — true for free with a linked worktree (one shared refs
    db), not true for a clone (its own, separate ref namespace holds any
    commit the run made). This makes it true again the same way
    ``--shared`` already made the *objects* cheap to reach: a ``git fetch
    <clone_path> <branch>`` costs nothing extra to transfer (the objects
    are already visible via ``.git/objects/info/alternates``) and lands the
    ref exactly where every existing publish-lane read already expects it.

    Delegates the actual ref update to :func:`fast_forward_branch` (via
    ``FETCH_HEAD``) rather than a bare ``update-ref``, so the same
    checked-out-elsewhere refusal an ordinary branch update gets applies
    here too — a collision ``git worktree list``-based detection cannot see
    for a clone (a clone is not a linked worktree; nothing registers it),
    but git's own working-tree state in *repo_root* is still a fact this
    function can and does check before writing over it.

    Also closes the module docstring's alternates/gc window: the instant
    this ref exists in *repo_root*, the commits it points at are reachable
    from the *host's own* refs, so the host's own gc can no longer consider
    them unreferenced — independent of whether the clone's own ``gc.auto=0``
    survived.
    """
    fetch = _git(repo_root, "fetch", "--no-tags", str(clone_path), branch, check=False)
    if fetch.returncode != 0:
        detail = fetch.stderr.strip() or fetch.stdout.strip()
        return gitops.BranchUpdateResult(success=False, branch=branch, detail=detail)
    return gitops.fast_forward_branch(repo_root, branch, "FETCH_HEAD")


def switch_to(worktree_path: Path, branch: str) -> None:
    """Switch a worktree's HEAD to *branch*, creating it if it doesn't exist.

    Uses ``git switch <branch>`` when the branch already exists locally,
    otherwise ``git switch -c <branch>`` to create it at the current HEAD.
    Called by ``WorktreeEnv.prepare`` to move the agent's starting point
    from the throwaway ``brr/<run-id>`` placeholder to the event's named
    target branch before the agent runs.

    Raises ``BranchCheckedOutError`` before invoking git when the branch is
    already checked out in another worktree. Git refuses that checkout anyway;
    the typed error lets callers keep the unique run branch instead.
    """
    from . import gitops

    checkout_path = gitops.branch_checkout_path(worktree_path, branch)
    if (
        checkout_path is not None
        and checkout_path.resolve() != worktree_path.resolve()
    ):
        raise BranchCheckedOutError(branch, checkout_path)

    result = subprocess.run(
        ["git", "switch", branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    result = subprocess.run(
        ["git", "switch", "-c", branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        checkout_path = gitops.branch_checkout_path(worktree_path, branch)
        if (
            checkout_path is not None
            and checkout_path.resolve() != worktree_path.resolve()
        ):
            raise BranchCheckedOutError(branch, checkout_path)
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            detail or f"failed to switch worktree to branch {branch!r}"
        )


def current_branch(worktree_path: Path) -> str | None:
    """Return the branch HEAD points at inside *worktree_path*, or None.

    Returns ``None`` for a detached HEAD (rare — only happens if the
    agent explicitly detaches inside the worktree).
    """
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


class BaseUnresolvable(RuntimeError):
    """No candidate base resolved inside the checkout being probed (#1298).

    Raised rather than answered, because the only two answers
    :func:`has_commits_beyond` can give both mean something it does not know.
    ``False`` in particular is the sentence "this run committed nothing", and
    three separate call sites act on it by discarding the run's checkout.
    """


def has_commits_beyond(
    worktree_path: Path,
    base_ref: str,
    *,
    base_oid: str | None = None,
) -> bool:
    """Return True if the worktree HEAD has commits not reachable from the base.

    *base_oid* is preferred over *base_ref* and exists because a ref **name**
    is not portable into the checkout this probe runs in. A strand's checkout
    is a ``git clone --shared`` (#746), whose local heads are only the branch
    ``repo_root`` had checked out at clone time — every other branch is a
    ``refs/remotes/origin/*`` name, and git's rev-parse fallback tries
    ``refs/remotes/<name>``, never ``refs/remotes/origin/<name>``. So the
    ordinary seed name ``main`` resolves to *nothing* in a strand clone taken
    from a host checkout standing on any other branch, and
    ``git rev-list --count main..HEAD`` exits 128.

    An oid has neither problem: the clone shares the object store, so it
    always resolves, and it cannot drift under a running child the way a
    branch name can.

    **Raises :class:`BaseUnresolvable` when no candidate resolves**, instead of
    returning ``False``. #1298 is what the old ``return False`` cost: git
    could not answer, the caller heard "the child committed nothing", the
    publish was skipped and the clone — the only copy of the commits — was
    deleted. A probe may report what it measured; it may not report a failure
    to measure as a measurement.
    """
    candidates = [c for c in (base_oid, base_ref) if c]
    last_detail = ""
    for candidate in candidates:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{candidate}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            last_detail = (result.stderr or result.stdout or "").strip()
            continue
        try:
            return int(result.stdout.strip() or "0") > 0
        except ValueError:
            # git exited 0 with something that is not a count. Unreadable is
            # not zero, so this is the same refusal as an unresolvable base.
            last_detail = f"unreadable count {result.stdout.strip()!r}"
    raise BaseUnresolvable(
        f"no base resolved in {worktree_path} from "
        f"{candidates or ['(nothing given)']}: {last_detail or 'no detail'}"
    )


def unpushed_commit_count(worktree_path: Path) -> int:
    """Return the number of HEAD commits not present on any remote-tracking ref.

    ``git rev-list --count HEAD --not --remotes`` counts commits reachable
    from HEAD but from no ``refs/remotes/*`` ref — i.e. local work not yet
    pushed anywhere. It needs no configured upstream, so a fresh run
    branch (which has none) still reports honestly. Any git failure
    (detached/empty repo, command error) yields ``0`` rather than raising:
    the forge facet this feeds is observational and must never fail a run.
    """
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or "0")
    except ValueError:
        return 0


def has_uncommitted_changes(worktree_path: Path) -> bool:
    """Return True when the worktree has untracked, unstaged, or staged changes.

    Used by finalization to decide whether the worktree directory can be
    discarded safely. If the agent created files but didn't commit them,
    those files are only present here — tearing the worktree down would
    silently drop them, so we keep it for forensic inspection instead.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Conservatively assume there is something worth keeping when we
        # can't read the status — better to leak a worktree than to drop
        # uncommitted work.
        return True
    return bool(result.stdout.strip())


def uncommitted_file_count(worktree_path: Path) -> int:
    """Return the number of changed paths (untracked + unstaged + staged).

    A line of ``git status --porcelain`` per affected path. Feeds the
    portal-state ``scm`` facet so the back channel can report "you have N
    modified file(s)" at closeout — the cheap, observational counterpart to
    :func:`has_uncommitted_changes`. Any git failure yields ``0`` rather than
    raising: like :func:`unpushed_commit_count`, this is observational and
    must never fail a run.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def clear_stale_worktree_pin(repo_root: Path, removed: Path) -> bool:
    """Unset ``core.worktree`` when it names *removed*. Returns True if it did.

    **Structural, not a guess about who wrote it.** #746 never established
    the writer — a test fixture, a drifted `git config`, something in a
    child's own suite — and a repair that waits for that answer is a repair
    that never ships. It does not need the answer: whoever set the pin, a
    value naming a directory *this function has just deleted* is garbage
    from the moment the removal completes. Nothing can want it.

    Left in place it is not inert. ``rev-parse --show-toplevel`` keeps
    answering with the deleted path at exit 0, so every git command in the
    shared checkout — the operator's own — addresses a tree that is gone,
    and the first one to use that answer as a ``cwd`` dies. #1108 is that
    bill: the daemon crash-looped on its own first line 312 times across 27
    minutes, restarting every 5 seconds, while `brnrd daemon status` — the
    one command an operator types to ask what is wrong — died in the same
    three frames.

    Best-effort by construction: a config that cannot be read or written is
    not a reason to fail a teardown that has already succeeded.
    """
    pinned = gitops._config_value(repo_root, "core.worktree")
    if not pinned or not gitops._same_path(Path(pinned), removed):
        return False
    unset = gitops._config_git(repo_root, "--unset", "core.worktree")
    if unset is None or unset.returncode != 0:
        return False
    print(
        f"[brnrd] unset core.worktree — it pinned the shared checkout to "
        f"{removed}, the worktree just removed (#746/#1108)"
    )
    return True


def remove(
    repo_root: Path,
    run_id: str,
    *,
    branch: str | None = None,
    delete_branch: bool = False,
    force: bool = False,
) -> None:
    """Remove a run worktree and optionally delete its branch."""
    worktree_path = path_for(repo_root, run_id)
    if worktree_path.exists():
        args = ["worktree", "remove", str(worktree_path)]
        if force:
            args.insert(2, "--force")
        result = _git(repo_root, *args, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or f"failed to remove worktree {worktree_path}")

    clear_stale_worktree_pin(repo_root, worktree_path)

    if delete_branch and branch:
        result = _git(repo_root, "branch", "-D", branch, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or f"failed to delete branch {branch}")


WorktreeHygieneClassification = Literal["reap-safe", "preserve", "unknown"]


@dataclass(frozen=True)
class WorktreeHygieneEntry:
    """A row from ``git worktree list --porcelain``."""

    path: Path
    branch: str | None


@dataclass(frozen=True)
class WorktreeHygieneSnapshot:
    """Inspection results for one worktree before classification.

    ``pr_lookup_error`` and ``pr_lookup_unsupported`` are two different
    facts and never both set (#1064): *we asked ``gh`` and it failed*
    implies a retry might help, and *this forge is not one ``gh`` answers
    for* does not. The second is resolved once per report, before any
    subprocess runs, and ``forge_kind`` carries the name it was resolved
    to (``None`` when brr can't name the forge at all).
    """

    path: Path
    branch: str | None
    dirty: bool
    inspection_error: str | None = None
    upstream_ref: str | None = None
    commits_ahead: int | None = None
    origin_main_is_ancestor: bool | None = None
    pr_states: tuple[str, ...] = ()
    pr_lookup_error: str | None = None
    pr_lookup_unsupported: bool = False
    forge_kind: str | None = None
    commit_lookup_error: str | None = None


@dataclass(frozen=True)
class WorktreeHygieneReport:
    """Final report row for one worktree."""

    path: Path
    branch: str | None
    classification: WorktreeHygieneClassification
    reason: str


def parse_worktree_hygiene_list(output: str) -> list[WorktreeHygieneEntry]:
    """Parse ``git worktree list --porcelain`` output."""
    entries: list[WorktreeHygieneEntry] = []
    current_path: Path | None = None
    current_branch: str | None = None

    def flush() -> None:
        nonlocal current_path, current_branch
        if current_path is not None:
            entries.append(
                WorktreeHygieneEntry(path=current_path, branch=current_branch)
            )
        current_path = None
        current_branch = None

    for line in output.splitlines():
        if not line:
            flush()
            continue
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
            current_branch = None
            continue
        if line.startswith("branch "):
            ref = line.split(" ", 1)[1].strip()
            current_branch = ref.removeprefix("refs/heads/") or None
            continue
        if line.startswith("detached"):
            current_branch = None

    flush()
    return entries


def classify_worktree_hygiene(
    snapshot: WorktreeHygieneSnapshot,
) -> WorktreeHygieneReport:
    """Classify one inspected worktree for the report."""
    branch = (snapshot.branch or "").strip() or None
    path = snapshot.path

    if snapshot.dirty:
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="preserve",
            reason=_worktree_hygiene_dirty_reason(branch),
        )

    if snapshot.inspection_error:
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="unknown",
            reason=f"inspection failed: {snapshot.inspection_error}",
        )

    if branch is None:
        return WorktreeHygieneReport(
            path=path,
            branch=None,
            classification="unknown",
            reason="detached HEAD",
        )

    if snapshot.pr_lookup_unsupported:
        # Not "PR lookup failed" — nothing was asked, and nothing will be
        # while this holds, so the error phrasing would imply a retry that
        # is not coming.  Still ``unknown`` rather than falling through to
        # the commit checks: a GitLab remote can have an open MR we cannot
        # see, and "reap-safe; no open PR" would assert a check we now know
        # was never made.  Deliberately not "permanent" — the kind is
        # re-derived on every report, so a ``git remote set-url`` or a
        # ``.brr/config`` ``forge.kind`` fix is picked up on the next run.
        forge = f" ({snapshot.forge_kind})" if snapshot.forge_kind else ""
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="unknown",
            reason=(
                f"PR state unsupported{forge} — this remote isn't GitHub, "
                "and gh is never queried for it"
            ),
        )

    if snapshot.pr_lookup_error:
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="unknown",
            reason=f"PR lookup failed: {snapshot.pr_lookup_error}",
        )

    if _worktree_hygiene_has_open_pr(snapshot.pr_states):
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="preserve",
            reason="open PR",
        )

    if snapshot.commit_lookup_error:
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="unknown",
            reason=f"commit lookup failed: {snapshot.commit_lookup_error}",
        )

    if snapshot.upstream_ref:
        if snapshot.commits_ahead is None:
            return WorktreeHygieneReport(
                path=path,
                branch=branch,
                classification="unknown",
                reason=f"cannot count commits ahead of {snapshot.upstream_ref}",
            )
        if snapshot.commits_ahead > 0:
            return WorktreeHygieneReport(
                path=path,
                branch=branch,
                classification="preserve",
                reason=(
                    f"{snapshot.commits_ahead} unpushed commit(s) "
                    f"ahead of {snapshot.upstream_ref}"
                ),
            )
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="reap-safe",
            reason=(
                f"clean; no commits ahead of {snapshot.upstream_ref}; "
                "no open PR"
            ),
        )

    if snapshot.origin_main_is_ancestor is None:
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="unknown",
            reason="cannot compare against origin/main",
        )

    if snapshot.origin_main_is_ancestor:
        return WorktreeHygieneReport(
            path=path,
            branch=branch,
            classification="reap-safe",
            reason="clean; HEAD is an ancestor of origin/main; no open PR",
        )

    return WorktreeHygieneReport(
        path=path,
        branch=branch,
        classification="preserve",
        reason="HEAD is not an ancestor of origin/main",
    )


def format_worktree_hygiene_line(report: WorktreeHygieneReport) -> str:
    """Render one report row."""
    branch = report.branch or "<detached>"
    return f"{report.path} | {branch} | {report.classification} | {report.reason}"


@dataclass(frozen=True)
class _GhForgeVerdict:
    """Whether ``gh`` is worth asking about this repo's remote at all.

    A property of the *repo*, so it is resolved once per report and threaded
    down — not rediscovered per branch.
    """

    queryable: bool
    kind: str | None


def _gh_forge_verdict(repo_root: Path) -> _GhForgeVerdict:
    """Resolve whether ``gh pr list`` can answer for *repo_root*'s remote (#1064).

    Reuses :func:`forge_pr_cache._forge_kind_and_label` as a *function* — the
    one place that owns "read the remote, honour the ``[forge]`` overrides,
    name the kind" — while deliberately not borrowing its cache shape (a JSON
    file on disk); this path keeps its own per-report dict. Imported lazily so
    this low-level module keeps its import-time dependency on ``gitops`` alone.

    **Only a forge brr can name is gated** — ``kind is not None and kind !=
    "github"``. Everything else is queried, which is a deliberate narrowing of
    :func:`forge_pr_cache.refresh`'s condition rather than an oversight, and
    the reason is in that function's own docstring:

        both are a labeled remote ``gh --repo OWNER/REPO`` would silently
        resolve against github.com instead, which is the hazard #852 gates on

    ``refresh`` must conflate the two ``kind is None`` shapes because it passes
    ``--repo OWNER/REPO``. **This call site passes no ``--repo``** — its
    ``gh pr list --head`` is cwd-resolved, so gh's own host detection is the
    mechanism, and the hazard the conflation exists for cannot occur. Take
    away ``--repo`` and the reason to conflate goes with it.

    What the narrower gate buys, and it is not primarily a saved subprocess:
    the ``unsupported`` state is only ever *claimed* about a forge brr named.
    A GitHub Enterprise remote resolves to ``kind = None`` with a valid label
    (``_HOST_PATTERNS`` has ``^github[.]com$`` and no fuzzy pattern), and
    ``PR state unsupported — this remote isn't GitHub`` would be a **false**
    sentence rendered with no hedge, since the ``(kind)`` parenthetical drops
    out exactly when brr could not name the forge. A diagnostic that cannot
    tell two cases apart must name the ambiguity, not pick the confident
    branch.

    So three shapes stay queryable:

    - ``label is None`` — no remote, or one that doesn't parse. Nothing to
      base a verdict on; gh's own detection is the honest fallback, the same
      one :func:`forge_pr_cache.refresh` keeps for this case.
    - ``kind is None`` with a label — a host no pattern matches (GHE, an
      unrecognised self-host). Costs **one** subprocess for the whole report,
      globally short-circuited by ``__gh_global_error__`` if it fails.
    - ``kind == "github"``.

    ``gitlab`` / ``bitbucket`` / ``gitea``, including the ``gitlab.<corp>``
    self-hosted patterns ``forges.py`` matches, are named and therefore gated
    — which is the saving this whole function exists for. ``.brr/config``
    ``forge.kind`` moves a host into or out of the gated set either way.
    """
    from . import forge_pr_cache

    try:
        kind, label = forge_pr_cache._forge_kind_and_label(repo_root)
    except Exception:  # noqa: BLE001 - a report must not die on a remote read
        return _GhForgeVerdict(queryable=True, kind=None)
    if kind is not None and kind != "github":
        return _GhForgeVerdict(queryable=False, kind=kind)
    return _GhForgeVerdict(queryable=True, kind=kind)


def build_worktree_hygiene_report(repo_root: Path) -> list[WorktreeHygieneReport]:
    """Inspect all worktrees in *repo_root* and classify them.

    The forge kind is resolved **once**, before the first ``gh`` call, and
    threaded into every inspection (#1064). It is a fact about the repo, not
    about a branch, and asking it per branch is how a permanent cause gets
    rediscovered N times.
    """
    result = _git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or "failed to list worktrees")

    entries = parse_worktree_hygiene_list(result.stdout)
    forge = _gh_forge_verdict(repo_root)
    pr_cache: dict[str, tuple[tuple[str, ...], str | None]] = {}
    reports: list[WorktreeHygieneReport] = []
    for entry in entries:
        try:
            snapshot = inspect_worktree_hygiene(
                repo_root, entry, pr_cache=pr_cache, forge=forge,
            )
        except Exception as exc:  # pragma: no cover - defensive, report-only tool
            snapshot = WorktreeHygieneSnapshot(
                path=entry.path,
                branch=entry.branch,
                dirty=False,
                inspection_error=str(exc),
            )
        reports.append(classify_worktree_hygiene(snapshot))
    return reports


def inspect_worktree_hygiene(
    repo_root: Path,
    entry: WorktreeHygieneEntry,
    *,
    pr_cache: dict[str, tuple[tuple[str, ...], str | None]],
    forge: _GhForgeVerdict,
) -> WorktreeHygieneSnapshot:
    """Collect the git/gh facts needed to classify one worktree.

    *forge* is required rather than defaulted so no caller can reach the ``gh``
    call without having decided whether ``gh`` can answer at all.
    """
    try:
        dirty = has_uncommitted_changes(entry.path)
    except Exception as exc:
        return WorktreeHygieneSnapshot(
            path=entry.path,
            branch=entry.branch,
            dirty=False,
            inspection_error=str(exc),
        )

    branch = entry.branch
    if branch is None:
        return WorktreeHygieneSnapshot(path=entry.path, branch=None, dirty=dirty)

    pr_states: tuple[str, ...] = ()
    pr_error: str | None = None
    if forge.queryable:
        pr_states, pr_error = _lookup_pr_states(repo_root, branch, pr_cache=pr_cache)

    upstream_ref: str | None = None
    commits_ahead: int | None = None
    commit_lookup_error: str | None = None
    origin_main_is_ancestor: bool | None = None

    try:
        upstream_ref = gitops.branch_upstream(repo_root, branch)
    except Exception as exc:
        commit_lookup_error = str(exc)

    if commit_lookup_error is None and upstream_ref:
        commits_ahead, commit_lookup_error = _count_commits_ahead(
            entry.path, upstream_ref,
        )
    elif commit_lookup_error is None:
        origin_main_oid = gitops.rev_parse(repo_root, "origin/main")
        if origin_main_oid is None:
            commit_lookup_error = "cannot resolve origin/main"
        elif gitops.rev_parse(repo_root, branch) is None:
            commit_lookup_error = f"cannot resolve {branch}"
        else:
            origin_main_is_ancestor = _is_ancestor(repo_root, branch, "origin/main")

    return WorktreeHygieneSnapshot(
        path=entry.path,
        branch=branch,
        dirty=dirty,
        upstream_ref=upstream_ref,
        commits_ahead=commits_ahead,
        origin_main_is_ancestor=origin_main_is_ancestor,
        pr_states=pr_states,
        pr_lookup_error=pr_error,
        pr_lookup_unsupported=not forge.queryable,
        forge_kind=forge.kind,
        commit_lookup_error=commit_lookup_error,
    )


def main_worktree_hygiene(argv: list[str] | None = None) -> int:
    """CLI entry point for the dry-run report."""
    del argv
    repo_root = gitops.ensure_git_repo()
    for report in build_worktree_hygiene_report(repo_root):
        print(format_worktree_hygiene_line(report))
    return 0


def _lookup_pr_states(
    repo_root: Path,
    branch: str,
    *,
    pr_cache: dict[str, tuple[tuple[str, ...], str | None]],
) -> tuple[tuple[str, ...], str | None]:
    """``(states, error)`` for one branch, asking ``gh`` at most once per report.

    Reached only when :func:`_gh_forge_verdict` said ``gh`` can answer for this
    remote, so ``__gh_global_error__`` keeps the job it has always had and only
    that one: a ``gh`` that is *installed and broken* (unauthenticated, timing
    out, talking nonsense). That is a failure a retry might fix, which is
    exactly why it stays a separate state from the forge-kind verdict above.
    """
    global_error = pr_cache.get("__gh_global_error__")
    if global_error is not None:
        return global_error

    cached = pr_cache.get(branch)
    if cached is not None:
        return cached

    cached: tuple[tuple[str, ...], str | None]
    global_cached: tuple[tuple[str, ...], str | None] | None = None
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "state",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        cached = ((), "gh pr list timed out after 5s")
        global_cached = cached
    except OSError as exc:
        cached = ((), str(exc))
        global_cached = cached
    else:
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            cached = ((), detail or "gh pr list failed")
            global_cached = cached
        else:
            try:
                payload = json.loads(result.stdout or "[]")
            except ValueError as exc:
                cached = ((), f"invalid gh pr list output: {exc}")
                global_cached = cached
            else:
                if not isinstance(payload, list):
                    cached = ((), "invalid gh pr list payload")
                    global_cached = cached
                else:
                    states: list[str] = []
                    for item in payload:
                        if isinstance(item, dict):
                            state = str(item.get("state") or "").strip()
                            if state:
                                states.append(state)
                    cached = (tuple(states), None)
    pr_cache[branch] = cached
    if global_cached is not None and global_cached[1] is not None:
        pr_cache["__gh_global_error__"] = global_cached
    return cached


def _count_commits_ahead(worktree_path: Path, upstream_ref: str) -> tuple[int | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{upstream_ref}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, str(exc)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        return None, detail or f"failed to count commits ahead of {upstream_ref}"
    try:
        return int(result.stdout.strip() or "0"), None
    except ValueError:
        return None, f"invalid rev-list count: {result.stdout.strip()!r}"


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _worktree_hygiene_has_open_pr(pr_states: tuple[str, ...]) -> bool:
    return any(state.strip().casefold() == "open" for state in pr_states)


def _worktree_hygiene_dirty_reason(branch: str | None) -> str:
    if branch:
        return "dirty working tree"
    return "detached HEAD with dirty working tree"
