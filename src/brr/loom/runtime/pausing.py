"""A letter mid-call pauses the body's tool call, so the body decides now (4538).

A stopped child does not end the call: Claude Code waits on the wrapper
shell it spawned for each Bash call, so a SIGSTOP alone holds the call to
its own timeout (probed on 2.1.303). Killing that wrapper ends the call at
once, as an error, which the PostToolUseFailure hook turns into a boundary.
So the pause is: stop the work, keep its output (the shell unlinks the task
file when the call ends), kill the wrapper. Resume continues the work, now
detached, into the kept file; drop ends it. The exit status is lost.

A body folder opts in with a ``pause`` file: one regex matching the shell's
wrapper among the body's children. Without it nothing here runs.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from brr import pause

# Unanswered, a pause resumes itself: a stopped process is a leak past this.
CAP = 600.0
# A call this young reaches its own boundary soon enough; pausing it costs
# the call's exit status for nothing.
MIN_AGE = 5.0


def wrapper(folder: Path) -> re.Pattern | None:
    path = folder / "pause"
    return re.compile(path.read_text().strip()) if path.is_file() else None


def _stdout(pid: int) -> Path | None:
    if sys.platform.startswith("linux"):
        try:
            return Path(os.readlink(f"/proc/{pid}/fd/1"))
        except OSError:
            return None
    proc = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "1", "-Fn"],
                          capture_output=True, text=True, check=False)
    names = [line[1:] for line in proc.stdout.splitlines() if line.startswith("n")]
    return Path(names[0]) if names else None


def _keep(port: Path, pid: int) -> str:
    """A second name for the work's output file, in the room if the disk allows."""
    out = _stdout(pid)
    if out is None or not out.is_file():
        return ""
    for kept in (port / "paused" / f"{pid}.out", out.with_name(f"{out.name}.paused-{pid}")):
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.unlink(missing_ok=True)
            os.link(out, kept)
            return str(kept)
        except OSError:
            continue
    return ""


def pause_call(port: Path, body_pid: int, pattern: re.Pattern, since: float) -> list[dict]:
    """Pause each foreground call of the body begun after its last boundary.

    A call begun before it (a background task) already returned and is left
    running. Returns the records written; none when a pause is in effect.
    """
    if not hasattr(signal, "SIGSTOP") or pause.read_paused_record(port):
        return []
    procs, now = pause.list_all_processes(), time.time()
    records = []
    for shell in procs:
        if (shell.ppid != body_pid or not pattern.search(shell.command) or shell.started_at is None
                or shell.started_at <= since or now - shell.started_at < MIN_AGE):
            continue
        work = [p for p in procs if p.ppid == shell.pid]
        if not work:
            continue
        for child in work:
            for proc in pause._subtree_deepest_first(child, pause.descendants(child.pid, procs)):
                try:
                    os.kill(proc.pid, signal.SIGSTOP)
                except (ProcessLookupError, PermissionError):
                    pass
        kept = _keep(port, work[0].pid)
        try:
            os.kill(shell.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # The call ended on its own meanwhile: let its work go on.
            for child in work:
                pause._signal_with_subtree(child.pid, signal.SIGCONT, procs)
            continue
        records += [{"pid": child.pid, "argv": child.command, "started_at": child.started_at,
                     "stopped_at": now, "resumes_at": now + CAP, "output": kept} for child in work]
    if records:
        pause.write_paused_record(port, records)
    return records


def describe(records: list[dict]) -> str:
    """The jack's ``paused:`` line, or nothing."""
    if not records:
        return ""
    outputs = sorted({r["output"] for r in records if r.get("output")})
    where = f"; its output goes on into {', '.join(outputs)}" if outputs else ""
    return (f"paused: {pause.describe_paused(records)} · a letter arrived, so your call returned early "
            f"with its work stopped, not lost{where}. `brnrd runtime resume` lets it "
            "finish in the background (its exit status is gone); `brnrd runtime drop` "
            f"ends it. Unanswered, it resumes in {CAP / 60:.0f} min.")
