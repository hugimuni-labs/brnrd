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
import time
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
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                pass
            holder = _read_lock_status(status)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CI gate, as CI defines it.")
    parser.add_argument("--list", action="store_true", help="show the legs and exit")
    parser.add_argument("--job", help="run only this job id")
    args = parser.parse_args(argv)

    if not WORKFLOW.exists():
        sys.exit(f"no workflow at {WORKFLOW}")

    all_legs = legs(_load_workflow(), args.job)
    runnable = [leg for leg in all_legs if leg["kind"] == "run"]
    provided = [leg for leg in all_legs if leg["kind"] == "uses"]

    print(
        f"gate: {WORKFLOW.relative_to(REPO_ROOT)} -> {len(runnable)} run steps, "
        f"{len(provided)} runner-provided steps skipped by design"
    )
    if args.list:
        for leg in runnable:
            mark = "refused" if refusal(leg["command"]) else "run"
            print(f"  [{mark:>7}] {leg['job']}: {leg['name']}  (cwd {leg['cwd']})")
        return 0

    def run_legs() -> list[tuple[str, str, float]]:
        results: list[tuple[str, str, float]] = []
        for leg in runnable:
            label = f"{leg['job']}: {leg['name']}"
            reason = refusal(leg["command"])
            if reason:
                print(f"\n=== SKIP  {label}\n    {reason}")
                results.append((label, "SKIPPED", 0.0))
                continue
            print(f"\n=== RUN   {label}  (cwd {leg['cwd']})", flush=True)
            started = time.monotonic()
            completed = subprocess.run(
                leg["command"], shell=True, cwd=REPO_ROOT / leg["cwd"]
            )
            elapsed = time.monotonic() - started
            ok = completed.returncode == 0
            results.append(
                (label, "PASS" if ok else f"FAIL rc={completed.returncode}", elapsed)
            )
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
