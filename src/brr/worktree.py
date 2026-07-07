"""Git worktree helpers for run-isolated execution.

Each run gets a fresh worktree at ``.brr/worktrees/<run-id>/`` on a
dedicated ``brr/<run-id>`` branch sprouted from the resolved seed ref. The
agent runs inside that sandbox and decides how its work should land:

- Leaving commits on ``brr/<run-id>`` follows the daemon's branch
  plan: finalization fast-forwards a resolved auto-land target, or
  preserves the run branch when no safe target exists.
- Switching to a different branch (``git switch -c feat/foo`` or
  ``git switch existing``) records a runtime branch choice, and the
  branch is preserved as-is on cleanup.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import gitops


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


@dataclass(frozen=True)
class WorktreeInfo:
    """A brr-managed worktree entry."""

    path: Path
    run_id: str
    branch: str


class BranchCheckedOutError(RuntimeError):
    """Raised when a branch is already checked out in another worktree."""

    def __init__(self, branch: str, checkout_path: Path):
        self.branch = branch
        self.checkout_path = checkout_path
        super().__init__(f"{branch} is checked out at {checkout_path}")


def run_branch_name(run_id: str) -> str:
    """Return the standard run branch name brr creates for a worktree."""
    return f"brr/{run_id}"


def list_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    """List brr-managed worktrees under ``.brr/worktrees/``.

    Parses ``git worktree list --porcelain`` and filters to worktrees
    whose path starts with the brr worktrees directory.
    """
    from . import gitops

    worktrees_dir = gitops.shared_brr_dir(repo_root) / "worktrees"
    result = _git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return []

    entries: list[WorktreeInfo] = []
    current_path: Path | None = None
    current_branch: str = ""

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
            current_branch = ""
        elif line.startswith("branch "):
            ref = line.split(" ", 1)[1]
            current_branch = ref.removeprefix("refs/heads/")
        elif line == "" and current_path is not None:
            try:
                current_path.relative_to(worktrees_dir)
            except ValueError:
                pass
            else:
                run_id = current_path.name
                entries.append(WorktreeInfo(
                    path=current_path,
                    run_id=run_id,
                    branch=current_branch,
                ))
            current_path = None
            current_branch = ""

    if current_path is not None:
        try:
            current_path.relative_to(worktrees_dir)
        except ValueError:
            pass
        else:
            entries.append(WorktreeInfo(
                path=current_path,
                run_id=current_path.name,
                branch=current_branch,
            ))

    return entries


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


def has_commits_beyond(worktree_path: Path, base_ref: str) -> bool:
    """Return True if the worktree HEAD has commits not reachable from *base_ref*."""
    result = subprocess.run(
        ["git", "rev-list", "--count", f"{base_ref}..HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        return int(result.stdout.strip() or "0") > 0
    except ValueError:
        return False


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
    """Inspection results for one worktree before classification."""

    path: Path
    branch: str | None
    dirty: bool
    inspection_error: str | None = None
    upstream_ref: str | None = None
    commits_ahead: int | None = None
    origin_main_is_ancestor: bool | None = None
    pr_states: tuple[str, ...] = ()
    pr_lookup_error: str | None = None
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


def build_worktree_hygiene_report(repo_root: Path) -> list[WorktreeHygieneReport]:
    """Inspect all worktrees in *repo_root* and classify them."""
    result = _git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or "failed to list worktrees")

    entries = parse_worktree_hygiene_list(result.stdout)
    pr_cache: dict[str, tuple[tuple[str, ...], str | None]] = {}
    reports: list[WorktreeHygieneReport] = []
    for entry in entries:
        try:
            snapshot = inspect_worktree_hygiene(repo_root, entry, pr_cache=pr_cache)
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
) -> WorktreeHygieneSnapshot:
    """Collect the git/gh facts needed to classify one worktree."""
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
