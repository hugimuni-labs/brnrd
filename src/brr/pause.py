"""Pause — SIGSTOP a live run's tool-call children, never the run itself.

The daemon reaches a resident only at a tool boundary (see
``prompts/daemon-substrate.md`` §"boundary tempo"). A long call — a test
suite, a ``daemon logs`` tail, a sleep loop — is a stretch no correspondent
message can land in; killing the call to deliver the message wastes the
call entirely (#959, #1187 measured exactly that cost for ``brnrd await``).
This module is the alternative: **pause, don't kill**. When a message
arrives for a live claude-Shell run, the daemon stops that run's live tool
children (never the Shell process itself, never an MCP server, never a
``brnrd`` helper process) so the resident can read the message at the very
next boundary its Shell's own per-call cap yields, and decide — resume or
drop — instead of losing the call's work.

Shared by the daemon (which detects the message and calls
:func:`pause_run_children`, and sweeps stale pauses via
:func:`overdue_records` / :func:`release_all`) and the CLI verbs
``brnrd resume`` / ``brnrd drop`` (:func:`resume_pids` / :func:`drop_pids`),
which act on the same on-disk record directly — no daemon round-trip
needed, because the record already names the real OS pid and any process
on this host with permission can signal it.

**POSIX only.** ``SIGSTOP``/``SIGCONT`` do not exist on Windows; every
entry point here degrades to a silent no-op (``[]`` / ``False``) when
``signal.SIGSTOP`` is unavailable, matching the "unassertable stays
silent" doctrine the rest of the hooks/portal machinery already follows
rather than raising into a caller that can't do anything about it either.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The daemon-owned control file recording which pids this run's own
#: pause put to sleep. Lives beside `.card` / `.mood` / `.room` in the run's
#: outbox dir — same drawer, same "read fresh, never cached" doctrine.
PAUSED_CONTROL_NAME = ".paused.json"

#: A process whose argv contains this token is never a pause candidate: a
#: `brnrd hook …` child serves the very boundary a pause would block, and a
#: `brnrd await` child already resolves on the message with no help from
#: this module — stopping either deadlocks or wastes a pause for nothing.
_NEVER_PAUSE_MARKER = "brnrd"

#: POSIX signals this module needs. Absent on Windows; every public
#: function checks this once and no-ops rather than raising, per the module
#: docstring's degrade rule.
_POSIX_CAPABLE = hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT")

#: How long `drop_pids` waits after SIGTERM before escalating to SIGKILL —
#: matches the spec's stated grace period, not configurable: a drop is
#: already the operator's "give up on this" word, and a longer grace only
#: delays that.
_DROP_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class ProcInfo:
    """One live process, as read from a single `ps` snapshot."""

    pid: int
    ppid: int
    started_at: float | None  # epoch seconds; None when `ps`'s lstart didn't parse
    command: str


def _run_ps(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout


def _parse_lstart(tokens: list[str]) -> float | None:
    """Parse `ps`'s fixed 5-token ctime-style `lstart` (``Wed Sep 9 17:58:38 2026``).

    Both darwin and linux `ps` render the same `ctime(3)`-derived shape for
    `lstart`; day-of-month is space-padded to two characters on darwin,
    which `str.split()` collapses away before this ever sees it (see
    `_parse_ps_snapshot`'s tokenisation) — so one parse covers both.
    """
    if len(tokens) != 5:
        return None
    try:
        return time.mktime(time.strptime(" ".join(tokens), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _parse_ps_snapshot(raw: str) -> list[ProcInfo]:
    """Parse `ps -A -o pid=,ppid=,lstart=,command=` output.

    Column order matters: `lstart` (5 whitespace-separated tokens: weekday,
    month, day, time, year) sits *between* `ppid` and `command`, and
    `command` itself may contain arbitrary internal whitespace (a Bash tool
    call's full argv, quoted paths, …). Fixed-count `split(None, 7)` handles
    both: the first two tokens are pid/ppid, the next five are `lstart`, and
    whatever remains — however much whitespace it holds — is one `command`
    field, never re-split.
    """
    procs: list[ProcInfo] = []
    for line in raw.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        started_at = _parse_lstart(parts[2:7])
        procs.append(ProcInfo(pid=pid, ppid=ppid, started_at=started_at, command=parts[7]))
    return procs


def list_all_processes() -> list[ProcInfo]:
    """One `ps` snapshot of every process on the host, parsed to `ProcInfo`.

    `-A` ("every process") is the portable spelling: darwin's BSD `ps`
    reserves `-e` for "show environment", not "every process" (the opposite
    of Linux's procps, where `-e`/`-A` are aliases) — `-A` is the one flag
    both accept with the same meaning. `-o pid=,ppid=,lstart=,command=`
    drops the header row (each `=` suppresses its own column heading) so
    every line is data.
    """
    return _parse_ps_snapshot(_run_ps(["ps", "-A", "-o", "pid=,ppid=,lstart=,command="]))


def _looks_like_brnrd(command: str) -> bool:
    """Whether *command* is a brnrd helper invocation, not merely a path.

    This repository itself is named ``brnrd``. Substring matching therefore
    classified ordinary tool calls whose shell command mentioned the checkout
    path as daemon helpers and made pause-on-message silently inert. Match an
    executable followed by a subcommand (or ``python -m brr``) instead.
    """
    lowered = command.lower()
    return bool(
        re.search(r"(?:^|\s)(?:\S*/)?brnrd\s+[a-z][\w-]*", lowered)
        or re.search(r"(?:^|\s)(?:\S*/)?python(?:3(?:\.\d+)?)?\s+-m\s+brr(?:\s|$)", lowered)
    )


def descendants(root_pid: int, snapshot: list[ProcInfo] | None = None) -> list[ProcInfo]:
    """Every live descendant of *root_pid* (root excluded), any depth.

    A single `ps` snapshot may be passed in (the daemon's cap sweep walks
    several runs off one snapshot); a fresh one is taken otherwise.
    """
    procs = snapshot if snapshot is not None else list_all_processes()
    by_parent: dict[int, list[ProcInfo]] = {}
    for proc in procs:
        by_parent.setdefault(proc.ppid, []).append(proc)
    found: list[ProcInfo] = []
    frontier = [root_pid]
    seen: set[int] = set()
    while frontier:
        parent = frontier.pop()
        for child in by_parent.get(parent, []):
            if child.pid in seen or child.pid == root_pid:
                continue
            seen.add(child.pid)
            found.append(child)
            frontier.append(child.pid)
    return found


def pausable_children(
    runner_pid: int,
    since_ts: float,
    *,
    snapshot: list[ProcInfo] | None = None,
) -> list[ProcInfo]:
    """Descendants of *runner_pid* eligible for a pause right now.

    Two gates, both from the spec: (a) started strictly after *since_ts*
    (the run's last recorded boundary) — anything older predates the
    boundary and is machinery the boundary already accounted for (an MCP
    server, the Shell's own long-lived helpers), not a call made *since*;
    (b) argv does not name `brnrd` — a `brnrd hook` child serves the
    boundary itself (stopping it deadlocks the very thing being waited on)
    and a `brnrd await` child already resolves on the message with no help
    here. A process whose start time didn't parse is treated as "not
    provably after the boundary" and skipped — the pausable set is a
    positive claim, not a default.
    """
    procs = snapshot if snapshot is not None else list_all_processes()
    roots = [proc for proc in procs if proc.ppid == runner_pid]
    result: list[ProcInfo] = []
    for root in roots:
        subtree = _subtree_deepest_first(root, descendants(root.pid, procs))
        # The root is the unit we can later resume safely. If any ancestor
        # predates the boundary, the subtree may belong to a long-lived MCP
        # server; if any descendant is a brnrd helper, stopping its wrapper
        # still deadlocks the boundary. Both claims therefore gate the whole
        # root instead of filtering individual descendants out of it.
        if all(
            proc.started_at is not None
            and proc.started_at > since_ts
            and not _looks_like_brnrd(proc.command)
            for proc in subtree
        ):
            result.append(root)
    return result


def _subtree_deepest_first(root: ProcInfo, all_descendants: list[ProcInfo]) -> list[ProcInfo]:
    """*root* plus everything under it, ordered children-before-parent.

    SIGSTOP order matters here (spec step 2): a parent stopped while its own
    child is still running can leave that child to fork further or exit
    mid-signal; stopping depth-first means every descendant is already
    frozen before its parent is.
    """
    by_parent: dict[int, list[ProcInfo]] = {}
    for proc in all_descendants:
        by_parent.setdefault(proc.ppid, []).append(proc)
    ordered: list[ProcInfo] = []

    def _visit(node: ProcInfo) -> None:
        for child in by_parent.get(node.pid, []):
            _visit(child)
        ordered.append(node)

    _visit(root)
    return ordered


def pause_run_children(
    *,
    runner_pid: int,
    since_ts: float,
    outbox_dir: Path,
    cap_seconds: float,
    snapshot: list[ProcInfo] | None = None,
) -> list[dict[str, Any]] | None:
    """Stop *runner_pid*'s qualifying children and record the pause.

    Idempotent: a non-empty existing record means a pause is already in
    effect for this run, so this is a no-op (returns ``None``) rather than
    re-stopping an already-stopped tree or overwriting the first pause's
    ``resumes_at``. Returns the list of records written, or ``None`` when
    there was nothing to pause (no qualifying child) or the platform can't
    do this (Windows) or a pause was already active.
    """
    if not _POSIX_CAPABLE:
        return None
    if read_paused_record(outbox_dir):
        return None
    all_procs = snapshot if snapshot is not None else list_all_processes()
    candidates = pausable_children(runner_pid, since_ts, snapshot=all_procs)
    if not candidates:
        return None
    now = time.time()
    records: list[dict[str, Any]] = []
    stopped_pids: set[int] = set()
    for child in candidates:
        if child.pid in stopped_pids:
            continue
        for proc in _subtree_deepest_first(child, all_procs):
            if proc.pid in stopped_pids:
                continue
            try:
                os.kill(proc.pid, signal.SIGSTOP)
            except ProcessLookupError:
                continue
            except PermissionError:
                continue
            stopped_pids.add(proc.pid)
        records.append(
            {
                "pid": child.pid,
                "argv": child.command,
                "started_at": child.started_at,
                "stopped_at": now,
                "resumes_at": now + cap_seconds,
            }
        )
    if not records:
        return None
    write_paused_record(outbox_dir, records)
    return records


def read_paused_record(outbox_dir: Path) -> list[dict[str, Any]]:
    path = outbox_dir / PAUSED_CONTROL_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def write_paused_record(outbox_dir: Path, records: list[dict[str, Any]]) -> None:
    """Atomic write — stage `.tmp`, rename — same idiom as every other
    outbox control file (`daemon-substrate.md`'s outbox rule)."""
    path = outbox_dir / PAUSED_CONTROL_NAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(records), encoding="utf-8")
    tmp.replace(path)


def clear_paused_record(outbox_dir: Path) -> None:
    try:
        (outbox_dir / PAUSED_CONTROL_NAME).unlink()
    except FileNotFoundError:
        pass


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return pid_permission_error(pid)
    return True


def pid_permission_error(pid: int) -> bool:
    """A `PermissionError` from a signal-0 probe means the pid exists but
    isn't ours — still "alive" for our purposes (a later signal will just
    as reliably fail loud, not silently skip a live process)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _record_process(
    record: dict[str, Any], snapshot: list[ProcInfo],
) -> ProcInfo | None:
    """Resolve a pause record to the same process, refusing PID reuse."""
    try:
        pid = int(record.get("pid", -1))
        started_at = float(record["started_at"])
    except (KeyError, TypeError, ValueError):
        return None
    for proc in snapshot:
        if proc.pid != pid or proc.started_at is None:
            continue
        if abs(proc.started_at - started_at) <= 1.0:
            return proc
    return None


def _signal_with_subtree(pid: int, sig: int, snapshot: list[ProcInfo]) -> None:
    """Signal *pid* and every process currently under it.

    `pause_run_children` stops a whole subtree under each recorded pid
    (spec step 2) but records only the top-level pid — walking the subtree
    again here, fresh, is what makes resume/drop symmetric with the stop:
    without it a grandchild stopped alongside its parent would never be
    named anywhere on disk and would sit frozen forever once its parent
    pid is the only one anybody signals.
    """
    targets = [pid] + [proc.pid for proc in descendants(pid, snapshot)]
    for target in targets:
        try:
            os.kill(target, sig)
        except (ProcessLookupError, PermissionError):
            pass


def resume_pids(
    outbox_dir: Path, *, only_pid: int | None = None,
) -> list[dict[str, Any]]:
    """SIGCONT every recorded pid (or just *only_pid*) and its subtree,
    clear what's resumed.

    Returns the records actually resumed. A pid no longer alive is dropped
    from the record without complaint — it exited or was reaped while
    stopped, which is not this call's problem to report. When *only_pid* is
    given and matches nothing recorded, nothing happens and `[]` returns;
    the record is left exactly as it was (no phantom clear).
    """
    if not _POSIX_CAPABLE:
        return []
    records = read_paused_record(outbox_dir)
    if not records:
        return []
    resumed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    snapshot = list_all_processes()
    for record in records:
        pid = int(record.get("pid", -1))
        if only_pid is not None and pid != only_pid:
            remaining.append(record)
            continue
        if _record_process(record, snapshot) is None:
            continue
        _signal_with_subtree(pid, signal.SIGCONT, snapshot)
        resumed.append(record)
    if remaining:
        write_paused_record(outbox_dir, remaining)
    else:
        clear_paused_record(outbox_dir)
    return resumed


def drop_pids(
    outbox_dir: Path, *, only_pid: int | None = None,
    grace_seconds: float = _DROP_GRACE_SECONDS,
) -> list[dict[str, Any]]:
    """SIGCONT, then SIGTERM, then (after *grace_seconds*) SIGKILL any
    survivor — for each recorded pid and its whole subtree. The "give up on
    this call" verb. Same record semantics as `resume_pids`: only the
    matched records are dropped and cleared.
    """
    if not _POSIX_CAPABLE:
        return []
    records = read_paused_record(outbox_dir)
    if not records:
        return []
    dropped: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    targets: set[int] = set()
    snapshot = list_all_processes()
    for record in records:
        pid = int(record.get("pid", -1))
        if only_pid is not None and pid != only_pid:
            remaining.append(record)
            continue
        if _record_process(record, snapshot) is None:
            continue
        dropped.append(record)
        targets.add(pid)
        targets.update(proc.pid for proc in descendants(pid, snapshot))
    for pid in targets:
        for sig in (signal.SIGCONT, signal.SIGTERM):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
    deadline = time.monotonic() + grace_seconds
    survivors = set(targets)
    while survivors and time.monotonic() < deadline:
        survivors = {pid for pid in survivors if _is_alive(pid)}
        if survivors:
            time.sleep(0.1)
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if remaining:
        write_paused_record(outbox_dir, remaining)
    else:
        clear_paused_record(outbox_dir)
    return dropped


def release_all(outbox_dir: Path) -> list[dict[str, Any]]:
    """Unconditional SIGCONT + clear — the run-end / terminal-status path.

    A run ending while a pause record is still on disk is a leak (spec step
    5): the process it stopped has no daemon left to ever resume it. This
    is the one call every terminal-status branch must make, regardless of
    *how* the run ended (done, failed, stopped, crashed, swept as a
    zombie) — same "unconditional cleanup" shape as
    `_run_worker_and_finalize`'s own `finally:` block.
    """
    return resume_pids(outbox_dir)


def overdue_records(
    outbox_dir: Path, *, now: float | None = None,
) -> list[dict[str, Any]]:
    """Records whose `resumes_at` cap has passed — the heartbeat sweep's read.

    A stopped process past its cap is a leak, not a pause (spec step 5):
    the correspondent it was frozen for may have been answered ages ago by
    a resident that never got back to this particular call. Returns the
    overdue records only; does not itself resume or clear anything (the
    caller CONTs them and records the advisory, then calls `resume_pids`).
    """
    now = time.time() if now is None else now
    return [
        record for record in read_paused_record(outbox_dir)
        if float(record.get("resumes_at", 0)) < now
    ]


def describe_paused(records: list[dict[str, Any]]) -> str:
    """The boundary-line fragment's payload: `basename argv[0..1]`, joined.

    `argv[0..1]` per the spec — the command name plus its first argument,
    not the full line (a Bash tool call's command can be arbitrarily long
    and multi-line; the boundary line is one row on a chip bar).
    """
    names: list[str] = []
    for record in records:
        argv = str(record.get("argv", "")).strip()
        tokens = argv.split(None, 2)[:2]
        if not tokens:
            continue
        first = tokens[0].rsplit("/", 1)[-1]
        names.append(" ".join([first, *tokens[1:]]))
    return ", ".join(names)
