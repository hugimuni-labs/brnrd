"""The agent's dominion — durable, owned working memory.

The current daemon shape stores resident memory inside the account dominion
repo, normally under ``repos/<repo-label>/dominion/``. That account repo is a
local-first git repo: it can stay purely local, or the user can opt into a
remote for off-machine durability. The older repo-local orphan branch
(``brr-home`` at ``.brr/dominion/``) remains supported as a legacy fallback
while installs migrate.

This module owns legacy bootstrap (:func:`ensure_dominion`), account-resident
seed files, and resolving the self-inject index into a wake-time digest
(:func:`resolve_self_inject`).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
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
SYNC_MARKER_FILE = "dominion.needs-sync"
# Total UTF-8 budget for the wake-time self-inject digest. Sized to fit
# the living playbook seed in full with headroom for the agent's own entries
# (recent pain, current focus); the agent can re-tune what it injects, and a
# repo can override via `dominion.inject_budget_bytes`. Bumped 8192 → 12288 →
# 20480 as the old fused seed grew; the identity-core split made the seed
# smaller again, but the guard test keeps the budget honest.
DEFAULT_INJECT_BUDGET_BYTES = 20480

# The seed playbook copied into a fresh dominion. It's the *starting*
# orientation — the agent owns and evolves its own copy thereafter.
_SEED_PLAYBOOK = Path(__file__).resolve().parent / "prompts" / "dominion-playbook.md"

_HEAD_RE = re.compile(r"^head:(\d+)$")
_TAIL_RE = re.compile(r"^tail:(\d+)$")


@dataclass(frozen=True)
class ResidentDominion:
    """One candidate resident-memory directory for a repo wake."""

    path: Path
    capture_root: Path
    label: str
    legacy: bool = False


def dominion_path(repo_root: Path) -> Path:
    """Return the legacy repo-local dominion worktree path."""
    return gitops.shared_brr_dir(repo_root) / WORKTREE_DIRNAME


def resident_dominion_candidates(
    repo_root: Path,
    cfg: dict | None = None,
    *,
    repo_label: str | None = None,
    include_legacy: bool = True,
) -> list[ResidentDominion]:
    """Return resident-memory locations, newest account path first.

    The account-scoped path is authoritative when present. The repo-local
    orphan-branch path remains a fallback so partially migrated installs can
    still wake with their old memory instead of going blank.
    """

    if cfg is None:
        from . import config as conf

        cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return []

    candidates: list[ResidentDominion] = []
    try:
        from . import account

        ctx = account.resolve_context(repo_root, cfg, create=False)
        if ctx.enabled:
            label = repo_label or account.repo_label(repo_root, cfg)
            candidates.append(
                ResidentDominion(
                    path=account.repo_dominion_path(ctx, label),
                    capture_root=ctx.dominion_repo,
                    label=f"account:{label}",
                )
            )
            candidates.append(
                ResidentDominion(
                    path=ctx.dominion_repo,
                    capture_root=ctx.dominion_repo,
                    label="account-root",
                )
            )
    except Exception:
        pass

    if include_legacy:
        legacy = dominion_path(repo_root)
        candidates.append(
            ResidentDominion(
                path=legacy,
                capture_root=legacy,
                label="legacy-repo-local",
                legacy=True,
            )
        )

    deduped: list[ResidentDominion] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            key = candidate.path.resolve()
        except OSError:
            key = candidate.path
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def seed_account_dominion(path: Path) -> None:
    """Seed missing starter files into an account-scoped resident dominion."""

    _seed(path, readme=_ACCOUNT_README, overwrite=False)


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


def mark_needs_sync(brr_dir: Path, reason: str) -> None:
    """Record that the dominion's remote diverged (a push was rejected).

    A best-effort hint, written to the runtime dir (gitignored), surfaced
    in the next wake prompt so the resident reconciles the dominion remote
    itself — pull / merge / resolve / push is git-layer dissonance resolution,
    the agent's judgement, not the daemon's (``kb/design-self-scheduled-
    thoughts.md`` → sync companion). Cleared by the next successful push.
    """
    try:
        brr_dir.mkdir(parents=True, exist_ok=True)
        (brr_dir / SYNC_MARKER_FILE).write_text(reason.strip() + "\n", encoding="utf-8")
    except OSError:
        pass


def clear_needs_sync(brr_dir: Path) -> None:
    """Clear the dominion sync-needed marker (best-effort)."""
    try:
        (brr_dir / SYNC_MARKER_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def needs_sync(brr_dir: Path) -> str | None:
    """Return the dominion sync-needed reason, or ``None`` when in sync."""
    try:
        text = (brr_dir / SYNC_MARKER_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


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

    **Push is a durability floor, not a merge.** When *push* is on, brr
    best-effort pushes the dominion repo after committing. A *rejected* push
    (the remote diverged — a second machine / failover host wrote it) is
    **not** silently swallowed: it sets a ``needs_sync`` marker so the next
    thought reconciles by hand (fetch / merge / resolve / push is the
    agent's judgement). A successful push clears the marker — including a
    clean-tree no-op push, so a resident that reconciled out-of-band clears
    its own stale marker on the next capture.
    """
    if not dominion_dir.is_dir():
        return False
    brr_dir = dominion_dir.parent
    committed = False
    try:
        with _commit_lock(dominion_dir, lock_timeout) as held:
            if not held:
                return False
            if gitops.worktree_dirty(dominion_dir):
                committed = gitops.commit_all(dominion_dir, message)
        if push and remote:
            target = branch or gitops.current_branch(dominion_dir)
            # Push after a real commit, or to settle a standing divergence
            # (clean tree but a marker is set — the agent may have just
            # reconciled). Otherwise leave the network alone.
            if target and target != "HEAD" and (committed or needs_sync(brr_dir)):
                if gitops.push_branch(dominion_dir, remote, target):
                    clear_needs_sync(brr_dir)
                else:
                    mark_needs_sync(
                        brr_dir,
                        f"push of {target} to {remote} was rejected — the "
                        f"dominion's remote has diverged; reconcile by hand "
                        f"(fetch / merge / push) in {dominion_dir}",
                    )
        return committed
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


def _write_seed_file(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.write_text(text, encoding="utf-8")


def _seed(path: Path, *, readme: str = "", overwrite: bool = True) -> None:
    """Write starter files into a freshly created or newly scoped dominion."""
    path.mkdir(parents=True, exist_ok=True)
    _write_seed_file(path / "README.md", readme or _README, overwrite=overwrite)
    _write_seed_file(
        path / PLAYBOOK_FILE,
        _SEED_PLAYBOOK.read_text(encoding="utf-8"),
        overwrite=overwrite,
    )
    _write_seed_file(path / SELF_INJECT_FILE, _SELF_INJECT_SEED, overwrite=overwrite)
    _write_seed_file(path / "pitfalls.md", _PITFALLS_SEED, overwrite=overwrite)
    _write_seed_file(path / "schedule.md", _SCHEDULE_SEED, overwrite=overwrite)


_README = """\
# brr-home — the resident agent's working memory

This is an **orphan branch**: it shares no history with `main` and never merges
into it, so it won't appear in `main`'s diffs or pull requests. It's named
plainly so it reads as ordinary infrastructure to anyone browsing the repo —
nothing here needs your review.

It is brr's durable, owned working memory: the space the agent governs and
carries across runs — its notes, learned pitfalls, schedule, and playbook (the
design calls it the *dominion*; see `kb/design-agent-dominion.md` on `main`).
You're welcome to look — it's inspectable on purpose. You just don't have to.

## Please don't delete this branch

brr pushes `brr-home` as the **off-machine backup** of that memory. Deleting the
remote branch removes the only copy that outlives the machine running the agent,
so it can lose the context it has built up. (If the daemon is still running it
recreates the branch on its next push — but anything that lived only on the
remote is gone.) Leaving it alone costs you nothing: it never touches `main`.

Per-task branches named `brr/<run-id>` are a different thing — ordinary feature
branches that open PRs, safe to handle like any other.
"""

_ACCOUNT_README = """\
# Dominion — resident agent working memory

This directory is the resident-owned working memory for one registered repo
inside the account dominion repo. It is intentionally separate from the
project's source checkout and from the shared `kb/`: notes here are the
resident's workshop until a durable, user-shared fact is promoted outward.

The surrounding account home is local-first. It can remain only on this
machine, or you can opt into durability by pointing the account git repo at a
remote and letting brr push it. Default startup does not create a GitHub repo
or any other forge object on your behalf.
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

_PITFALLS_SEED = """\
# Pitfalls — trigger-indexed failure memory
#
# The *remember* step of the environment-shaping loop. When you hit
# friction worth recording but not yet worth a forcing function, write it
# here: brr surfaces a pitfall in your wake prompt when one of its
# triggers appears in the task at hand — the lesson placed in your path,
# not prose you must remember to re-read.
#
# Format: a `## ` heading (the lesson's name), a `trigger:` line
# (comma-separated keywords or loci that tend to appear when the failure
# is about to recur), then the lesson. Slash a pitfall once a lint, test,
# or baked tool guards the failure — the forcing function is the better
# memory, and a stale pitfall is just orientation tax.
#
# Example (delete once you have real ones):
#
# ## Blind 5xx retry masks caller bugs
# trigger: retry, 5xx, http client
# The HTTP client surfaces 5xx to the caller without retrying. If you add
# a retry, gate it behind idempotency — a blind retry hid a caller bug
# here before.
"""

_SCHEDULE_SEED = """\
# Schedule — thoughts you wake yourself for
#
# This is how you stop being purely reactive. Each entry here becomes an
# event in your inbox when it comes due — a fresh thought, woken on your
# own clock rather than by a user. Use it to defer work, run periodic
# upkeep, or chain a train of thought across wakes.
#
# Format: a `## ` heading (the entry's id), one trigger line, then the
# body — the prompt for the woken thought.
#   trigger:  at: <ISO-8601>   one-shot   e.g. at: 2026-06-10T09:00:00Z
#             every: <dur>     recurring  e.g. every: 24h  (s/m/h/d, summable: 1h30m)
# `every` is anchored when first seen (adding it doesn't fire instantly),
# then fires each interval. A self-scheduled thought's effect is the work
# it does (an edit, a commit, a reconcile) — it has no chat to reply to.
# This file is yours: add, edit, and remove entries freely.
#
# Example (delete once you have real ones):
#
# ## reconcile my dominion
# every: 24h
# Fetch and reconcile the account dominion remote if one is configured:
# pull, resolve any conflicts with the remote, and push. Then skim
# pitfalls.md and self-inject for anything stale.
"""
