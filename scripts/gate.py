#!/usr/bin/env python3
"""Run the CI gate locally, reading the leg list *from CI itself*.

The rule this replaces was an instruction to remember something: "green means
the gate's commands, not the ones you remember — open
`.github/workflows/ci.yml` and run what it runs." That rule was written down,
and then broken twice within an hour by the person who wrote it. Awareness is
not a mechanism.

So this script carries **no copy of the leg list**. It parses the workflow and
executes the `run:` steps it finds, in the `working-directory` the workflow
gives them. A leg added to CI is a leg this runs on its next invocation with
no edit here — the failure where someone adds a job and the local command does
not know about it cannot happen, rather than being unlikely.

Two edges, both printed rather than hidden, because a runner that silently
drops work reads exactly like one that ran it:

- `uses:` steps (checkout, setup-python, setup-node) are the CI runner's job.
  They are counted and reported, never silently dropped.
- One command is refused on purpose: `pip install -e`. AGENTS.md forbids it
  outside the operator's own checkout, because an editable install from a
  worktree writes a `.pth` into the shared venv pointing at a path that will
  not exist in ten minutes, and `site.addpackage` then skips the dead path
  without a word (#762). Reported as SKIPPED with that reason attached.

`npm ci` is **not** refused, and that is deliberate. Running `npm test` in a
tree without `node_modules` reported *228 pass / 2 fail* against a suite that
is 238/238 green — two failures that do not exist, on a tree with nothing
wrong with it. An install step you skip does not produce an error; it produces
a plausible wrong answer.

**It leaves a receipt.** Running is only half of it: the other half is that
somebody later can tell whether it ran, and *on which tree*. Under a brnrd run
(`BRR_OUTBOX_DIR` set) this writes this tree's entry into `.gate-receipts.json`
beside the run's other control files — a map keyed per tree (#820), not one
receipt, because a single run can gate more than one tree (this repo's own
`host` pattern: a scratch worktree, then the checkout) under the same outbox.
That turns "did the gate run, on this tree" from a thing a resident has to
remember into a fact on disk — which is what `brr.hooks._gate_closeout_clause`
reads before letting a run end. A script nobody is reminded to call gets
called when forgetting it is *checkable*.

The receipt's shape is **not defined here**. `brr.gate_receipt` owns the
referents, the pre/post capture around the leg loop, the tree-keyed map, and
the comparison; this file calls `gated_run` and hands the result to its own
`write_receipt`, which merges its entry in via `gate_receipt.merge_entry`. It
used to carry its own copy of `tree_referents`/`untracked_digest`, and that
copy is the reason #917 had to be fixed in two places: two implementations of
one fingerprint agree with each other and are wrong together (#722).

Usage:  python scripts/gate.py [--list] [--job backend]
Exit:   0 iff every executed leg exited 0.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:  # pragma: no cover - POSIX only, and every supported host is POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The gate must read *this* tree's receipt module, not whichever copy of `brr`
# happens to be installed in the shared venv. A bare `import brr` from a
# worktree resolves to the host checkout (AGENTS.md -> Build and run), which
# would have this script gating one tree with another tree's rules.
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
from brr import gate_receipt, gitops  # noqa: E402
from brr.parked_branches import TERMINAL_RUN_STATUSES  # noqa: E402

# Written beside the run's other control dotfiles; the daemon's outbox drain
# skips dotfiles, so it is never delivered to chat. Same idiom as `.card`.
# One name, defined by the module the hook also reads.
RECEIPT_NAME = gate_receipt.RECEIPT_NAME
untracked_digest = gate_receipt.untracked_digest
tree_referents = gate_receipt.tree_referents

# Commands this runner deliberately does not execute, each with the reason a
# reader needs in order to judge whether it is still the right call.
REFUSED = {
    "pip install -e": (
        "AGENTS.md -> Build and run: never from a worktree or a task; it writes "
        "a .pth into the shared venv (#762). Assumed already done in this checkout."
    ),
}


def _load_workflow() -> dict:
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover - environment guard
        sys.exit(
            "gate.py needs PyYAML to read the workflow it refuses to duplicate.\n"
            "  pip install -e '.[dev]'   (or: pip install pyyaml)\n"
            "Hardcoding the leg list here instead is the bug this file exists to prevent."
        )
    return yaml.safe_load(WORKFLOW.read_text())


def legs(workflow: dict, only_job: str | None = None) -> list[dict]:
    """Every step in the workflow, classified. Pure — the tests drive this."""
    found: list[dict] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        if only_job and job_id != only_job:
            continue
        job_wd = ((job.get("defaults") or {}).get("run") or {}).get("working-directory", ".")
        for step in job.get("steps") or []:
            command = step.get("run")
            if not command:
                found.append({"job": job_id, "kind": "uses", "name": step.get("uses", "?")})
                continue
            found.append(
                {
                    "job": job_id,
                    "kind": "run",
                    "name": step.get("name") or command.strip().splitlines()[0],
                    "command": command,
                    "cwd": step.get("working-directory", job_wd),
                }
            )
    return found


def refusal(command: str) -> str | None:
    """The reason this command is not executed locally, or None to run it."""
    for needle, reason in REFUSED.items():
        if needle in command:
            return reason
    return None


def receipt_path() -> Path | None:
    """Where this invocation's receipt goes, or None when nothing reads one.

    Only under a brnrd run. A hand invocation in the operator's own shell has
    no guard watching it and writes nothing.
    """
    outbox = (os.environ.get("BRR_OUTBOX_DIR") or "").strip()
    if not outbox:
        return None
    directory = Path(outbox)
    return directory / RECEIPT_NAME if directory.is_dir() else None


def write_receipt(
    verdict: str,
    results: list[tuple[str, str, float]],
    tree: dict | None = None,
) -> Path | None:
    """Record the verdict and the tree it was reached on. Best-effort.

    Written for RED as well as GREEN: the obligation a reader checks is *the
    gate ran on this tree*, never *the gate was green*. A run may legitimately
    end red and report it; a run that never looked is the failure.

    *tree* is `gate_receipt.gated_run`'s second return value: the end-state
    referents plus the record of whether the tree held still across the legs.
    Omitted (a caller that never captured a "before"), this samples the end
    state alone and the receipt stays silent about stillness rather than
    claiming it.

    Writes `REPO_ROOT`'s own slot of the outbox's receipts map
    (`gate_receipt.merge_entry`) rather than the whole file — one run that
    gates two trees under one `BRR_OUTBOX_DIR` (this repo's own `host`
    pattern: a scratch worktree, then the checkout) must not have its first
    receipt destroyed by its second (#820).
    """
    path = receipt_path()
    if path is None:
        return None
    referents = tree if tree is not None else tree_referents(REPO_ROOT)
    if referents is None:
        return None
    entry = {
        **referents,
        "verdict": verdict,
        "workflow": str(WORKFLOW.relative_to(REPO_ROOT)),
        "run_id": os.environ.get("BRR_RUN_ID", ""),
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "legs": [
            {"label": label, "verdict": leg_verdict, "seconds": round(elapsed, 1)}
            for label, leg_verdict, elapsed in results
        ],
    }
    return gate_receipt.merge_entry(path.parent, REPO_ROOT, entry)


# ── #1195 rec 1 + rec 4: one gate at a time per machine, and say where you
# stand while queued ────────────────────────────────────────────────────
#
# The contention #1195 measured was never memory — it was CPU/IO: five
# strands, five full pytest suites plus five `npm ci`/`svelte-check`/`eslint`
# runs, concurrently, on one developer machine. `spawn.max_concurrent` admits
# N *runs*; nothing admits N *gates*, and a run is cheap while its gate is
# not. The fix accepted here (rec 1) is the cheap one: serialize the gate
# itself, machine-wide, and make a queued run say so instead of silently
# burning its window (rec 4). Rec 2 (skip unaffected legs) and rec 3
# (reprice the spawn pool by gate cost) are explicitly out of scope — see
# the issue's own ranking.

#: The lock file's name, in the *shared* `.brr` dir — see `gate_lock_path`.
GATE_LOCK_NAME = "gate.lock"
#: How long between non-blocking flock attempts while queued.
_LOCK_POLL_SECONDS = 2.0
#: How often (in poll ticks) a queued run repeats its status, so a long wait
#: does not spam stderr once per poll but still narrates within a minute.
_LOCK_REPORT_EVERY = 15.0
#: A terminal status is stamped before environment finalization.  The longest
#: bounded finalization operation is a 300-second docker action, so allow twice
#: that tail before treating a still-live child as orphaned.
#:
#: What the grace is measured *against* is the manifest's mtime, because the
#: manifest records `started_at` and no terminal timestamp — there is nothing
#: else on disk that says when the run went terminal.  So any later write to
#: the manifest restarts the clock and the lock keeps queueing.  That is the
#: safe direction (it never breaks a lock early) but it is not what "has been
#: terminal for N seconds" would mean, and the log line below says the one it
#: actually measures.
_TERMINAL_RUN_GRACE_SECONDS = 600.0


def gate_lock_path() -> Path:
    """The machine-wide gate lock's path, in the *shared* `.brr` dir.

    Not `REPO_ROOT / ".gate.lock"` — that is exactly the bug #1195 reports.
    Every worktree gate.py runs from (`.brr/worktrees/run-*`, a scratch
    `/tmp/brr-wt-*` checkout) has its *own* `REPO_ROOT`, so a lock file
    anchored there would give every worktree its own lock and never contend,
    the same way `.gate-receipts.json` would if it were not already keyed
    off the outbox instead.

    `gitops.shared_brr_dir` is the resolver `_child_git_pin` (`daemon.py`)
    already relies on to find the one location every dispatch mode treats as
    shared: a plain checkout's own `.brr`, or — from a linked worktree —
    `git rev-parse --git-common-dir`'s parent, which is the *host*
    checkout's `.brr` regardless of which worktree asks. That covers the
    host checkout, `.brr/worktrees/run-*`, and a scratch
    `/tmp/brr-wt-*` worktree alike, because all three are `git worktree`
    checkouts of the same repository and resolve the same common git dir.
    A containerized strand reaches the identical path for a different
    reason: the docker backend bind-mounts `repo_root` (the host checkout)
    at the same absolute path inside the container
    (`src/brr/envs/__init__.py`, `_docker_git_config_env_args`'s docstring:
    "the repo is bind-mounted at the same absolute path it has on the
    host"), so `.brr` — and this lock file under it — rides the mount
    unchanged. One physical file, four dispatch shapes, no configuration.
    """
    return gitops.shared_brr_dir(REPO_ROOT) / GATE_LOCK_NAME


def _lock_status_path(lock: Path) -> Path:
    """The small sidecar naming who holds *lock* and since when."""
    return lock.with_name(lock.name + ".status")


def _write_lock_status(path: Path, **fields: object) -> None:
    """Best-effort, atomic write (`.tmp` + `replace`, `merge_entry`'s own
    idiom in `gate_receipt.py`): a reader of this file is never itself
    holding the gate lock — it got here *because* the flock failed — so
    nothing serializes a concurrent write against a concurrent read, and a
    non-atomic write could hand a truncated read. A status file that fails
    to write at all is silence, not a crash — the flock is still the real
    mechanism; this is narration on top of it."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(fields), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _read_lock_status(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pid_is_alive(pid: object) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        os.kill(value, 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def _terminal_owner_reason(lock: Path, holder: dict) -> str | None:
    """Return why a current-format holder is stale, or ``None``.

    Missing ``run_id`` deliberately fails closed: old status files carry no
    ownership evidence, so they continue to queue exactly as before.
    """
    run_id = str(holder.get("run_id") or "").strip()
    if not run_id:
        return None
    pid = holder.get("pid")
    if not _pid_is_alive(pid):
        return "holder pid is no longer alive"
    manifest = lock.parent / "runs" / run_id / "run.md"
    try:
        text = manifest.read_text(encoding="utf-8")
        age = time.time() - manifest.stat().st_mtime
    except OSError:
        return None
    status = ""
    for line in text.splitlines():
        if line.startswith("status:"):
            status = line.partition(":")[2].strip()
            break
    if status in TERMINAL_RUN_STATUSES and age > _TERMINAL_RUN_GRACE_SECONDS:
        return (
            f"owner run is terminal ({status}) and its manifest has not been "
            f"written for {age:.0f}s"
        )
    return None


def _fd_names_current_lock(fd: int, lock: Path) -> bool:
    try:
        opened = os.fstat(fd)
        current = lock.stat()
    except OSError:
        return False
    return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


def _break_stale_lock(fd: int, lock: Path, status: Path) -> bool:
    """Replace the stale inode once, serializing competing queued reapers."""
    reap_path = lock.with_name(lock.name + ".reap")
    reap_fd = os.open(str(reap_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(reap_fd, fcntl.LOCK_EX)
        holder = _read_lock_status(status)
        reason = _terminal_owner_reason(lock, holder)
        if reason is None or not _fd_names_current_lock(fd, lock):
            return False
        held_pid = holder.get("pid", "unknown")
        held_run = holder.get("run_id", "unknown")
        print(
            f"gate: broke stale gate lock — pid {held_pid}, run {held_run}: "
            f"{reason}; lock was broken",
            file=sys.stderr,
            flush=True,
        )
        with contextlib.suppress(OSError):
            lock.unlink()
        with contextlib.suppress(OSError):
            status.unlink()
        return True
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(reap_fd, fcntl.LOCK_UN)
        os.close(reap_fd)


def queue_status_path() -> Path | None:
    """Where *this* invocation's own "I am queued" status goes, or None.

    Mirrors `receipt_path()`'s own `BRR_OUTBOX_DIR` detection on purpose —
    same guard, same idiom, so a caller who already knows how the receipt
    surfaces knows how this does too. Written into the run's own outbox
    (never the shared lock dir) so a strand's own status narration, or
    a dispatcher reading its child's outbox, can see "waiting on the gate"
    without reaching into another tree's `.brr`.
    """
    outbox = (os.environ.get("BRR_OUTBOX_DIR") or "").strip()
    if not outbox:
        return None
    directory = Path(outbox)
    return directory / ".gate-wait.json" if directory.is_dir() else None


@contextlib.contextmanager
def held_gate_lock():
    """Hold the machine-wide gate lock for the duration of the block.

    Blocks — via a non-blocking `flock` retried on a short poll, not a
    single blocking call — because the poll is what lets a queued run
    *say* it is queued (rec 4) instead of vanishing into an opaque wait.
    Every few polls it prints who holds the lock and for how long, and
    (under a brnrd run) mirrors that into the run's own outbox as
    `.gate-wait.json` so a caller does not have to scrape stderr.

    No timeout: unlike `gitops.file_lock` (a short, skip-on-timeout lock
    guarding a git-index commit), giving up here would mean the run
    silently never gates, which is worse than any wait. A run that needs a
    ceiling on the wait owns that decision itself (kill it, or hand the
    gate back to its dispatcher per the issue's rec 4 framing) rather than
    having this function decide it invisibly.

    Degrades to a no-op (still yields, still runs the block) when `fcntl`
    is unavailable or the lock file cannot be opened — this project targets
    Linux, but a gate that cannot lock should still gate, same posture as
    `gitops.file_lock`.
    """
    if fcntl is None:  # pragma: no cover - non-POSIX
        yield
        return
    lock = gate_lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        yield
        return
    status = _lock_status_path(lock)
    queue_path = queue_status_path()
    waited = 0.0
    ticks = 0
    try:
        while True:
            if not _fd_names_current_lock(fd, lock):
                os.close(fd)
                fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if _fd_names_current_lock(fd, lock):
                    break
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            holder = _read_lock_status(status)
            stale_reason = _terminal_owner_reason(lock, holder)
            if stale_reason and _fd_names_current_lock(fd, lock):
                if _break_stale_lock(fd, lock, status):
                    continue
            if ticks % max(1, int(_LOCK_REPORT_EVERY / _LOCK_POLL_SECONDS)) == 0:
                held_since = holder.get("since", "unknown")
                held_pid = holder.get("pid", "unknown")
                print(
                    f"gate: queued — pid {held_pid} has held the lock since "
                    f"{held_since}; waited {waited:.0f}s so far",
                    file=sys.stderr, flush=True,
                )
                if queue_path is not None:
                    _write_lock_status(
                        queue_path,
                        waiting_pid=os.getpid(),
                        waited_seconds=round(waited, 1),
                        holder_pid=held_pid,
                        holder_since=held_since,
                    )
            time.sleep(_LOCK_POLL_SECONDS)
            waited += _LOCK_POLL_SECONDS
            ticks += 1
        _write_lock_status(
            status,
            pid=os.getpid(),
            run_id=os.environ.get("BRR_RUN_ID", ""),
            since=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        if queue_path is not None:
            with contextlib.suppress(OSError):
                queue_path.unlink()
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ── #1195 rec 2: --changed-only skips legs the diff provably cannot touch ─
#
# Rec 1 (above) serializes the gate; this is the other half the issue named
# and explicitly deferred out of PR #1215 — real throughput, at the cost of
# a coverage claim that has to be provably right, not merely usually right.
#
# The rule looked simpler at first: skip a job when every changed path falls
# under some *other* job's own `working-directory`. That direction is safe
# one way — nothing under `src/frontend/`'s own toolchain (`npm test`,
# `eslint`, `svelte-check`) ever reads a Python file — but *not* the other:
# `backend`'s own pytest suite reads real content out of other jobs' trees
# by design, not by accident, e.g. `tests/test_spa_serving.py` /
# `tests/test_brnrd_legal_pinning.py` / `tests/test_privacy_notice.py` parse
# real `.svelte` files under `src/frontend/src/routes/` and
# `src/frontend/src/lib/`, and `tests/test_npm_launcher.py` executes the real
# `packaging/npm/bin/brnrd.js` and reads `packaging/npm/uv-assets.json`. A
# diff confined to `src/frontend/` or `packaging/npm/` can still break
# `backend`, and there is no bounded way to enumerate every such coupling
# from here — the pattern ("read the real file, don't duplicate it") is this
# repo's own stated preference (AGENTS.md's "two implementations... wrong
# together"), so it will keep recurring, not shrink to a fixed list.
#
# So: only a job that *has* its own `defaults.run.working-directory` — CI's
# own claim that this job's inputs are scoped to a directory — is eligible
# to be skipped by inference, and only when nothing in the diff falls under
# that directory. A job with no such claim (`backend`: no
# `working-directory`, i.e. "wherever CI didn't scope it") is never skipped
# by this function, full stop — proven necessary above, not merely assumed.
# Per the task's own instruction to leave a direction undispatched rather
# than trust an unbounded guess, that half of the issue's own phrasing
# ("skip the frontend leg... and vice versa") is deliberately not
# implemented; see the report this shipped with. Opt-in (`--changed-only`);
# CI itself never passes the flag, so the leg list a merge must pass is
# unchanged.

#: Basenames whose presence in the diff forces every job — inputs the
#: whole gate depends on, not one job's tree (`pyproject.toml`'s deps and
#: testpaths, any `package.json`'s scripts/deps, this script's own logic).
_FORCE_ALL_BASENAMES = {"package.json", "pyproject.toml"}
#: Exact repo-relative paths with the same effect, named rather than
#: pattern-matched because there is exactly one of each and a prefix match
#: would be broader than intended.
_FORCE_ALL_EXACT_PATHS = {"scripts/gate.py"}


def _forces_every_job(path: str) -> bool:
    """A changed *path* whose effect can't be scoped to one job's tree."""
    if path in _FORCE_ALL_EXACT_PATHS:
        return True
    if path.startswith(".github/workflows/"):
        return True
    return path.rsplit("/", 1)[-1] in _FORCE_ALL_BASENAMES


def _job_working_dirs(workflow: dict) -> dict[str, str]:
    """job_id -> its own ``defaults.run.working-directory``, ``"."`` when unset.

    The same field `legs()` already reads into `job_wd` for a different
    purpose (where a step's command runs) — reused here for what it also
    happens to mean: a job with a `working-directory` is one CI itself
    already scoped to a directory; one without it has never made that claim
    about its own inputs.
    """
    return {
        job_id: ((job.get("defaults") or {}).get("run") or {}).get("working-directory", ".")
        for job_id, job in (workflow.get("jobs") or {}).items()
    }


def jobs_to_run(workflow: dict, changed: list[str]) -> set[str] | None:
    """Job ids `--changed-only` would run, given *changed* repo-relative paths.

    ``None`` means "run every job" — the safe answer for every case this
    can't reason about: an empty/unreadable diff, or any path
    `_forces_every_job` flags. Never the reverse: an empty *result set*
    would mean "skip everything," which this function must not produce from
    ambiguity, only from a changed list that is provably confined to jobs
    that then get returned.

    Only a job with its own `working-directory` (`_job_working_dirs`) is
    ever left out of the result — and only when no changed path falls under
    it. Every job *without* one (the "." catch-all — `backend` in this
    repo's own `ci.yml`) is unconditionally in the result: see the block
    above for the concrete, repeated evidence that such a job reads real
    content out of other jobs' trees, which makes "not under my own
    directory" too weak a proof of "cannot affect me."
    """
    if not changed:
        return None
    if any(_forces_every_job(path) for path in changed):
        return None
    job_dirs = _job_working_dirs(workflow)
    exclusive = {jid: wd for jid, wd in job_dirs.items() if wd not in (".", "")}
    catch_all = {jid for jid, wd in job_dirs.items() if wd in (".", "")}
    run: set[str] = set(catch_all)
    for path in changed:
        run.update(
            jid for jid, wd in exclusive.items()
            if path == wd or path.startswith(wd.rstrip("/") + "/")
        )
    return run


#: Default-branch names tried, in order, when looking for a merge-base.
#: `origin/*` first — the remote-tracking ref reflects the branch's actual
#: state even when the local `main` is stale or absent (a fresh worktree
#: checkout, per AGENTS.md, may never have checked `main` out at all).
_BASE_BRANCH_CANDIDATES = ("origin/main", "main", "origin/master", "master")


def diff_base(repo_root: Path) -> str | None:
    """The ref `--changed-only` diffs against.

    The merge-base with the repo's own default branch when one is
    reachable, else `HEAD~1` — a detached checkout, or one whose default
    branch isn't named `main`/`master` locally or on `origin`. ``None``
    when neither resolves (a one-commit repo with nothing before it, or no
    `HEAD` at all); callers must treat that exactly like an unreadable
    diff — run everything, never guess a base to compare against.
    """
    if gate_receipt.git_out(repo_root, ["rev-parse", "--verify", "-q", "HEAD"]) is None:
        return None
    for candidate in _BASE_BRANCH_CANDIDATES:
        if gate_receipt.git_out(repo_root, ["rev-parse", "--verify", "-q", candidate]) is None:
            continue
        base = gate_receipt.git_out(repo_root, ["merge-base", "HEAD", candidate])
        if base:
            return base.strip()
    fallback = gate_receipt.git_out(repo_root, ["rev-parse", "--verify", "-q", "HEAD~1"])
    return fallback.strip() if fallback else None


def changed_paths(repo_root: Path, base: str) -> list[str] | None:
    """Repo-relative paths touched since *base*: committed diff plus untracked.

    A single ref to `git diff` compares it against the working tree — index
    and worktree both — so a strand's own uncommitted edits are covered
    without a second ref to name. Untracked files never appear in `git
    diff` regardless, so they are unioned in separately: dropping them would
    treat a brand-new file as "no change," the wrong direction to be wrong
    in for a check whose job is to avoid false negatives.
    """
    tracked = gate_receipt.git_out(repo_root, ["diff", "--name-only", base])
    if tracked is None:
        return None
    files = {line.strip() for line in tracked.splitlines() if line.strip()}
    untracked = gate_receipt.git_out(repo_root, ["ls-files", "--others", "--exclude-standard"])
    if untracked:
        files.update(line.strip() for line in untracked.splitlines() if line.strip())
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CI gate, as CI defines it.")
    parser.add_argument("--list", action="store_true", help="show the legs and exit")
    parser.add_argument("--job", help="run only this job id")
    parser.add_argument(
        "--changed-only", action="store_true",
        help=(
            "skip legs whose job cannot be affected by the diff since the "
            "merge-base with main (or HEAD~1) — opt-in; CI still runs everything"
        ),
    )
    args = parser.parse_args(argv)

    if not WORKFLOW.exists():
        sys.exit(f"no workflow at {WORKFLOW}")

    workflow = _load_workflow()
    all_legs = legs(workflow, args.job)

    skip_jobs: set[str] = set()
    base = None
    if args.changed_only:
        base = diff_base(REPO_ROOT)
        changed = changed_paths(REPO_ROOT, base) if base else None
        if changed is None:
            print(
                "gate: --changed-only could not read a diff (no merge-base, no "
                "HEAD~1, or git failed) — running every leg, unaffected either way"
            )
        else:
            run_jobs = jobs_to_run(workflow, changed)
            if run_jobs is None:
                print(
                    f"gate: --changed-only vs {base}: shared config touched, or "
                    "nothing changed — running every leg"
                )
            else:
                all_job_ids = {leg["job"] for leg in all_legs}
                skip_jobs = all_job_ids - run_jobs
                shown = ", ".join(changed[:8]) + (" …" if len(changed) > 8 else "")
                if skip_jobs:
                    print(
                        f"gate: --changed-only vs {base}: diff touches [{shown}] — "
                        f"skipping {', '.join(sorted(skip_jobs))} (provably unaffected)"
                    )
                else:
                    print(
                        f"gate: --changed-only vs {base}: diff touches [{shown}] — "
                        "no job provably unaffected, running everything"
                    )

    runnable = [leg for leg in all_legs if leg["kind"] == "run"]
    provided = [leg for leg in all_legs if leg["kind"] == "uses"]

    print(
        f"gate: {WORKFLOW.relative_to(REPO_ROOT)} -> {len(runnable)} run steps, "
        f"{len(provided)} runner-provided steps skipped by design"
    )
    if args.list:
        for leg in runnable:
            if leg["job"] in skip_jobs:
                mark = "skip Δ"
            elif refusal(leg["command"]):
                mark = "refused"
            else:
                mark = "run"
            print(f"  [{mark:>7}] {leg['job']}: {leg['name']}  (cwd {leg['cwd']})")
        return 0

    # #1603: CI's own jobs (`backend` / `frontend` / `launcher`) carry no
    # `needs:` between them — genuinely independent — but the serial loop
    # this replaced ran every leg of every job back to back under one
    # exclusive lock, so a ~9-10m critical path became a ~40m local wait.
    # The fix stays inside that same lock (#1195 rec 1's scope is untouched):
    # one thread per *job*, legs **within** a job still run in step order —
    # a job can have a setup step before its test step, and reordering those
    # would be a second, worse bug. `print_lock` only serializes the
    # bookkeeping prints and each already-complete output line; it is not a
    # second job-level lock.
    def run_legs() -> list[tuple[str, str, float]]:
        print_lock = threading.Lock()

        def run_one(leg: dict) -> tuple[str, str, float]:
            label = f"{leg['job']}: {leg['name']}"
            prefix = f"[{leg['job']}] "
            if leg["job"] in skip_jobs:
                with print_lock:
                    print(
                        f"\n=== SKIP  {label}\n    --changed-only vs {base}: "
                        f"{leg['job']} not touched by the diff"
                    )
                return (label, "SKIPPED", 0.0)
            reason = refusal(leg["command"])
            if reason:
                with print_lock:
                    print(f"\n=== SKIP  {label}\n    {reason}")
                return (label, "SKIPPED", 0.0)
            with print_lock:
                print(f"\n=== RUN   {label}  (cwd {leg['cwd']})", flush=True)
            started = time.monotonic()
            # Piped (not inherited) so concurrent jobs' output doesn't tear
            # mid-line on the shared terminal fd; merged stderr->stdout so
            # there is exactly one pipe to drain per leg, never two (no
            # separate-pipe deadlock to guard against). Each complete line is
            # prefixed with its job id before it prints, under the same lock
            # that guards the `=== RUN`/`=== SKIP` bookkeeping lines, so a
            # reader can always tell which job printed which line.
            proc = subprocess.Popen(
                leg["command"],
                shell=True,
                cwd=REPO_ROOT / leg["cwd"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                with print_lock:
                    sys.stdout.write(f"{prefix}{line}")
                    sys.stdout.flush()
            proc.wait()
            elapsed = time.monotonic() - started
            ok = proc.returncode == 0
            return (label, "PASS" if ok else f"FAIL rc={proc.returncode}", elapsed)

        def run_job(job_legs: list[dict]) -> list[tuple[str, str, float]]:
            # Sequential on purpose: step order inside one job is a real
            # dependency (a setup step before the step that needs it), not
            # an artifact of the old top-level loop.
            return [run_one(leg) for leg in job_legs]

        # Grouped by first appearance, which is already job order followed
        # by leg order within that job — `legs()` walks jobs, then a job's
        # own steps, in that order — so flattening the per-job results back
        # in this same `jobs_order` reproduces the exact sequence the old
        # single loop produced. `.gate-receipts.json`'s shape (one entry per
        # leg, in leg order) does not change; only how the entries are
        # produced does.
        jobs_order: list[str] = []
        grouped: dict[str, list[dict]] = {}
        for leg in runnable:
            grouped.setdefault(leg["job"], []).append(leg)
            if leg["job"] not in jobs_order:
                jobs_order.append(leg["job"])

        if not jobs_order:
            return []

        results: list[tuple[str, str, float]] = []
        with ThreadPoolExecutor(max_workers=len(jobs_order)) as pool:
            futures = {job_id: pool.submit(run_job, grouped[job_id]) for job_id in jobs_order}
            for job_id in jobs_order:
                results.extend(futures[job_id].result())
        return results

    # The captures bracket *the legs*, not the process: `--list` returned
    # above, so it stays the no-op that writes nothing and samples nothing.
    # Sampling only after the last leg is what let a file written *during* the
    # gate be certified as gated (#917) — the first leg's tree is the one the
    # receipt has to be about.
    #
    # The gate lock (#1195 rec 1) wraps this same span and nothing wider: it
    # is the leg loop that contends for CPU/IO, not argument parsing or
    # `--list`, so only this block queues behind a sibling's gate.
    with held_gate_lock():
        results, tree = gate_receipt.gated_run(REPO_ROOT, run_legs)
    failed = any(verdict.startswith("FAIL") for _, verdict, _ in results)

    print("\n" + "=" * 66)
    for label, verdict, elapsed in results:
        print(f"{verdict:>12}  {elapsed:6.1f}s  {label}")
    print("=" * 66)
    verdict = "RED" if failed else "GREEN"
    print(f"gate: {verdict}")
    if (tree or {}).get("tree_moved_during_gate"):
        # No command string to quote — this runner *is* the gate, and it runs a
        # leg list rather than one command. `moved_sentence` says "while the
        # gate was running" when it is given nothing to name.
        print(
            f"gate: the tree moved while the gate ran — "
            f"{gate_receipt.moved_sentence(tree)}. {verdict} does not cover "
            f"it; re-run on a still tree."
        )
    receipt = write_receipt(verdict, results, tree)
    if receipt is not None:
        print(f"gate: receipt {receipt}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
