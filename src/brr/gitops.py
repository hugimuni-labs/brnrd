"""Git helpers — repo detection, branching, and file tracking."""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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


# ── Divergence markers ───────────────────────────────────────────────
#
# One protocol, two memories. A capture net (dominion, knowledge) pushes
# best-effort; a *rejected* push is never swallowed — it writes a marker to
# the gitignored runtime dir, the wake prompt surfaces it, and the resident
# reconciles by hand (fetch / merge / resolve / push is judgement, not a
# reflex the daemon should fake). A successful push clears it.


def write_sync_marker(brr_dir: Path, name: str, reason: str) -> None:
    try:
        brr_dir.mkdir(parents=True, exist_ok=True)
        (brr_dir / name).write_text(reason.strip() + "\n", encoding="utf-8")
    except OSError:
        pass


def clear_sync_marker(brr_dir: Path, name: str) -> None:
    try:
        (brr_dir / name).unlink(missing_ok=True)
    except OSError:
        pass


def read_sync_marker(brr_dir: Path, name: str) -> str | None:
    try:
        text = (brr_dir / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


@dataclass
class BranchUpdateResult:
    """Result of fast-forwarding a local branch to another ref."""

    success: bool
    branch: str
    commit: str = ""
    detail: str = ""


def _git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in *repo_root*."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def ensure_git_repo() -> Path:
    """Return the repository root, or raise RuntimeError."""
    try:
        result = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Not a Git repository; run `git init` first.") from exc
    return Path(result.stdout.strip())


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


def main_worktree_root(repo_root: Path) -> Path | None:
    """Return the **main** working tree for *repo_root*, or ``None``.

    Deliberately not :func:`shared_brr_dir`'s ``.parent``. That function
    answers *where does runtime state live* and prefers a local ``.brr``,
    so it reports the worktree itself whenever one exists there — a
    perfectly good answer to its own question and the wrong one to this.
    Two questions sharing one derivation is the substitution bug #654 was
    filed about; asking git directly keeps them apart.

    ``git worktree list`` always prints the main working tree first, and
    says so in porcelain form. It is also correct under
    ``--separate-git-dir``, where the common git dir's parent is not a
    checkout at all. Returns *repo_root* itself for a repo with no linked
    worktrees, and ``None`` when this is not a git checkout.
    """
    result = _git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):].strip())
    return None


def is_tracked(path: Path) -> bool:
    """Return True if *path* is tracked by Git."""
    try:
        _git(Path.cwd(), "ls-files", "--error-unmatch", str(path))
        return True
    except subprocess.CalledProcessError:
        return False


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

    tree = subprocess.run(
        ["git", "mktree"],
        cwd=repo_root, input="", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if tree.returncode != 0:
        return None
    tree_oid = tree.stdout.strip()

    commit = subprocess.run(
        ["git", "commit-tree", tree_oid, "-m", message],
        cwd=repo_root, input="", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
) -> bool:
    """Push local *branch* to *remote*. Best-effort; returns success."""
    if not remote or not branch:
        return False
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args += [remote, branch]
    result = _git(repo_root, *args, check=False)
    return result.returncode == 0


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
    commit = _git(worktree_path, *args, check=False)
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
    # Close-keyword predicate (#652 → #653 → #657): a run may not put a
    # close keyword + #NNN anywhere but the start of a line, and a line-start
    # close may carry nothing after the ref except more refs.  GitHub's own
    # closing-keyword scanner does not care about narrative framing or
    # position within the message — it matches "keyword #NNN" wherever it
    # occurs — so both shapes below are real, independently confirmed
    # closes, not just readability nits.  Refused when BRR_RUN_ID is set (run
    # commits only); a maintainer typing the same line by hand is not bound
    # here (see issue for the trade-off).  POSIX sh / POSIX ERE — grep -E, no \b, explicit character
    # classes.
    #
    # #653 first tried a closed word list of leading qualifiers (partially,
    # mostly, ...).  Dropped: a real commit surfaced mid-run — `85ed4735`,
    # "This does not close #477." — that GitHub's timeline confirms closed
    # #477 two seconds after push, and no finite word list anticipates
    # "does not" the way a position rule does for free.  The second real
    # closure is sharper still: GitHub's timeline credits `aef7fa11` — the
    # commit that *shipped* #652 — for re-closing #413, because its own body
    # quotes `Closes #413 §7 S13.` as a documentation example of what the
    # guard refuses.  The same push also carried `c91d3866`, whose subject
    # narrates "...that actually closed #413..."; that one changed nothing
    # only because the issue was already closed by then.  Neither is a
    # leading qualifier.  Position, not vocabulary, is the discriminator.
    #
    # _BRR_ANY: keyword + #NNN anywhere in the line (word-bounded so it
    # doesn't fire inside a longer word like "disclosed").  A line that
    # doesn't match this at all needs no further check.
    #
    # _BRR_LINESTART: the same pair, anchored to the start of the line
    # (leading whitespace allowed) — the one position GitHub-closing intent
    # can be stated unambiguously.
    #
    # _BRR_CLEAN: what a line-start close is *allowed* to look like, whole —
    # a positive rule (#657), replacing #652's negative _BRR_TRAILING.  The
    # negative shape asked "is the character after the ref disqualifying?"
    # and exempted "," to let a genuine multi-close through.  That carve-out
    # exempted the entire rest of the line with it: `Closes #413, not really`
    # and `Closes #413, #414, and #415` both passed while GitHub closed #413
    # anyway.  A "." bought the same exemption a different way — the negative
    # rule wanted whitespace right after the digits, so `Closes #413. Also
    # this fixes #414` passed on both counts, the second close shielded by
    # the first one being well-formed.  Fourth face of the same class, and —
    # like the third — the hole was in the carve-out, not in the rule.
    #
    # So: once a line has cleared the position gate, the only legal
    # continuation after the ref is a repeatable `, #NNN` list, then either
    # end of line (optional "." and trailing space) or ":" — the subject
    # separator.  Anything else is prose, and prose after a close keyword is
    # what this guard exists to catch.
    #
    # The ":" allowance is load-bearing and it is *not* a return to
    # vocabulary.  Measured over this repo's last 300 commit messages, driving
    # the installed hook:
    #
    #     shipped (negative _BRR_TRAILING)   10 refusal lines / 5 commits
    #     strict  (no ":" allowed)           18 refusal lines / 13 commits
    #     this rule (":" allowed)            10 refusal lines / 5 commits
    #
    # The 8 extra strict refusals are all `Fix #NNN: <subject>` — this repo's
    # standard fix-commit subject, where the close is intended, the effect
    # matches the intent, and nothing on the line narrows it.  Refusing those
    # would make the guard fire on nearly every fix commit, and a guard that
    # cries wolf stops being read (#623).  Identical refusal set to what
    # shipped, plus the holes above closed.
    #
    # Residual, knowingly accepted: `Closes #413: partially` passes.  It is
    # the price of the ":" allowance — the guard cannot tell a subject from a
    # qualifier once a colon is legal.  Not covered.  Do not read the rule as
    # covering it.
    '  _BRR_ANY=\'(^|[^[:alnum:]_])(close[sd]?|fix(es|ed)?|resolve[sd]?)[[:space:]]+#[[:digit:]]+\'\n'
    '  _BRR_LINESTART=\'^[[:space:]]*(close[sd]?|fix(es|ed)?|resolve[sd]?)[[:space:]]+#[[:digit:]]+\'\n'
    # "[.]?" rather than "\.?": same POSIX ERE meaning, no backslash to
    # survive Python source escaping *and* sh single-quoting on the way to
    # grep.
    '  _BRR_CLEAN=\'^[[:space:]]*(close[sd]?|fix(es|ed)?|resolve[sd]?)[[:space:]]+'
    '#[[:digit:]]+(,[[:space:]]*#[[:digit:]]+)*([.]?[[:space:]]*$|:)\'\n'
    '  while IFS= read -r _brr_ln; do\n'
    '    if echo "$_brr_ln" | grep -qiE "${_BRR_ANY}"; then\n'
    '      if ! echo "$_brr_ln" | grep -qiE "${_BRR_LINESTART}"; then\n'
    '        printf \'commit-msg: close keyword not at the start of a line (GitHub still closes on it there).\\n\' >&2\n'
    '        printf \'  Offending line: %s\\n\' "$_brr_ln" >&2\n'
    '        printf \'  A close keyword only closes at the start of a line.\\n\' >&2\n'
    '        printf \'  Use instead:\\n\' >&2\n'
    # Both remedies, deliberately.  This message is reached by two authors
    # with opposite intents — "Partially closes #NNN" wants a reference,
    # "This closes #NNN" wants the close — and offering only the scoped form
    # steers the second one into silently *not* closing the issue they meant
    # to close.  A guard that is satisfiable only by abandoning the intent is
    # not satisfiable.
    '        printf \'    Closes #NNN.       (at the start of a line — closes it)\\n\' >&2\n'
    '        printf \'    Part of #NNN ...   (scoped reference — does not close)\\n\' >&2\n'
    '        printf \'  Bypass: git commit --no-verify\\n\' >&2\n'
    '        exit 1\n'
    '      elif ! echo "$_brr_ln" | grep -qiE "${_BRR_CLEAN}"; then\n'
    '        printf \'commit-msg: close keyword with a tail (GitHub ignores the tail and closes the issue).\\n\' >&2\n'
    '        printf \'  Offending line: %s\\n\' "$_brr_ln" >&2\n'
    '        printf \'  After the ref, only more refs may follow: ", #MMM", then end of line or ": subject".\\n\' >&2\n'
    # Every remedy, deliberately.  This branch is now reached by three authors
    # with different intents — a qualifier ("Fixes #NNN (partially)") wants a
    # scoped reference, a list ("Closes #NNN and #MMM") wants both closes, a
    # subject ("Fix #NNN - subject") wants the close plus a title — and an
    # author who is shown only the scoped form is steered into silently *not*
    # closing what they meant to close.  A guard satisfiable only by
    # abandoning the intent is not satisfiable; that is the whole argument
    # this predicate rests on.
    '        printf \'  Use instead:\\n\' >&2\n'
    '        printf \'    Closes #NNN.            (bare close — no tail)\\n\' >&2\n'
    '        printf \'    Closes #NNN, #MMM       (real multi-close — commas, never "and")\\n\' >&2\n'
    '        printf \'    Fix #NNN: subject       (close plus a subject, after the colon)\\n\' >&2\n'
    '        printf \'    Part of #NNN ...        (scoped reference — does not close)\\n\' >&2\n'
    '        printf \'  Bypass: git commit --no-verify\\n\' >&2\n'
    '        exit 1\n'
    '      fi\n'
    '    fi\n'
    '  done < "$1"\n'
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
