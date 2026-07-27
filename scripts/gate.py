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
(`BRR_OUTBOX_DIR` set) this writes `.gate-receipt.json` beside the run's other
control files, naming the tree it gated. That turns "did the gate run" from a
thing a resident has to remember into a fact on disk — which is what
`brr.hooks._gate_closeout_clause` reads before letting a run end. A script
nobody is reminded to call gets called when forgetting it is *checkable*.

Usage:  python scripts/gate.py [--list] [--job backend]
Exit:   0 iff every executed leg exited 0.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
# Written beside the run's other control dotfiles; the daemon's outbox drain
# skips dotfiles, so it is never delivered to chat. Same idiom as `.card`.
RECEIPT_NAME = ".gate-receipt.json"

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


def _git(*args: str) -> str | None:
    """Raw stdout of a read-only git command in this checkout, or None."""
    try:
        done = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def tree_referents() -> dict[str, str] | None:
    """The three raw git outputs that identify *which tree* was gated.

    Deliberately not a fingerprint *algorithm*. Two implementations of one
    hash — this writer and the hook that reads it — is the shape where copies
    agree with each other and are wrong together (#722). So the receipt stores
    git's own output verbatim and the reader compares git against git; the
    only computation is a sha256 over a diff, which is arithmetic, not a
    judgement.

    Untracked files need the fourth referent, and finding that out cost a
    failing test rather than a thought: `status --porcelain` *names* them and
    `diff HEAD` does not *read* them, so a receipt taken before editing a
    brand-new file would still validate after — which is the most common shape
    of a work-in-progress tree there is. `hash-object` closes it.
    """
    head = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    diff = _git("diff", "HEAD")
    if head is None or status is None or diff is None:
        return None
    return {
        "head": head.strip(),
        "status": status,
        "diff_digest": hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest(),
        "untracked_digest": untracked_digest(_git),
    }


def untracked_digest(git) -> str:
    """A digest over the *content* of every untracked, non-ignored file.

    Takes the git callable so `brr.hooks` can compute the identical value from
    its own checkout without importing this script — the same output of the
    same two git commands on both sides, which is the point of the whole
    referent design.
    """
    listing = git("ls-files", "--others", "--exclude-standard")
    if not listing:
        return ""
    paths = [line for line in listing.splitlines() if line]
    if not paths:
        return ""
    hashes = git("hash-object", "--", *paths) or ""
    return hashlib.sha256(
        "\n".join(
            f"{path}\t{blob}"
            for path, blob in zip(paths, hashes.splitlines())
        ).encode("utf-8", "replace")
    ).hexdigest()


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


def write_receipt(verdict: str, results: list[tuple[str, str, float]]) -> Path | None:
    """Record the verdict and the tree it was reached on. Best-effort.

    Written for RED as well as GREEN: the obligation a reader checks is *the
    gate ran on this tree*, never *the gate was green*. A run may legitimately
    end red and report it; a run that never looked is the failure.
    """
    path = receipt_path()
    if path is None:
        return None
    referents = tree_referents()
    if referents is None:
        return None
    payload = {
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
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return None
    return path


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

    results: list[tuple[str, str, float]] = []
    failed = False
    for leg in runnable:
        label = f"{leg['job']}: {leg['name']}"
        reason = refusal(leg["command"])
        if reason:
            print(f"\n=== SKIP  {label}\n    {reason}")
            results.append((label, "SKIPPED", 0.0))
            continue
        print(f"\n=== RUN   {label}  (cwd {leg['cwd']})", flush=True)
        started = time.monotonic()
        completed = subprocess.run(leg["command"], shell=True, cwd=REPO_ROOT / leg["cwd"])
        elapsed = time.monotonic() - started
        ok = completed.returncode == 0
        failed = failed or not ok
        results.append((label, "PASS" if ok else f"FAIL rc={completed.returncode}", elapsed))

    print("\n" + "=" * 66)
    for label, verdict, elapsed in results:
        print(f"{verdict:>12}  {elapsed:6.1f}s  {label}")
    print("=" * 66)
    verdict = "RED" if failed else "GREEN"
    print(f"gate: {verdict}")
    receipt = write_receipt(verdict, results)
    if receipt is not None:
        print(f"gate: receipt {receipt}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
