"""Seam bench — spawn a lesser-light runner against scripted events.

The resident's self-experimentation loop. A strong core reads the wake
scroll from inside but routes around rough seams silently; an economy
core (haiku, mini) breaks exactly where the context shape is weak. That
makes the lesser-light the *measuring instrument* for the interaction
seams: voicing, protocol-following, card narration, mid-run fold-ins,
next-move closeouts, temporal/cost awareness.

The bench packages one probe cycle:

1. **Sandbox** — a scratch repo plus a scratch ``BRNRD_HOME`` (own
   dominion, fresh playbook seed) so the probed wake rides the *real*
   orientation stack, fully isolated from the operator's account.
2. **Daemon** — ``python -m brr up`` spawned against the sandbox. The
   dev tree is an editable install, so prompt/code edits under test
   apply to the next bench run without any build step.
3. **Scenario** — a scripted lead event injected through the real inbox
   protocol (``protocol.create_event``), plus optional follow-ups
   injected mid-run (on first signal, or after a delay) to probe the
   fold-in seam.
4. **Harvest** — conversation records, responses, run count, timings,
   and the exact ``prompt.md`` the lesser core saw.
5. **Probes** — deterministic seam checks (card ✓/✗, interim ✓/✗,
   next-move ✓/✗, fold ✓/✗) rendered into ``report.md`` next to a woven
   ``transcript.md`` for the judgment-side read.

Deliberately *not* CI material: a bench run spends real runner quota and
needs runner CLI auth. It is an operator/resident tool — run it, read
the report, adjust the seam, run it again, and diff behaviour across
cores. Design: ``kb/design-bench-loop.md``.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import course, emotes, gitops, hooks, protocol, run_ledger, worktree

# ── Scenarios ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FollowUp:
    """A message injected mid-run to probe the fold-in seam.

    *after* is either ``"first-signal"`` (inject once the run shows its
    first card note / interim reply — the earliest moment a fold-in is
    plausible) or ``"+<seconds>"`` for a fixed delay from lead injection.
    """

    body: str
    after: str = "first-signal"


@dataclass(frozen=True)
class Scenario:
    """One scripted probe of the daemon⇄runner interaction seam."""

    name: str
    description: str
    lead: str
    followups: tuple[FollowUp, ...] = ()
    probes: tuple[str, ...] = ("response", "next_move")
    timeout_seconds: int = 600
    # Extra .brr/config lines for the sandbox repo.
    config: dict[str, Any] = field(default_factory=dict)
    # Extra files written into the sandbox repo before the scaffold commit,
    # as ``relative/path -> content``. The default scaffold is a notes file
    # and an empty kb — enough to probe a *short* seam, and nowhere near
    # enough to probe a long one. A scenario that needs the run to accumulate
    # real context before its obligations come due has to bring its own
    # substrate, because turn count is the independent variable there.
    scaffold: dict[str, str] = field(default_factory=dict)


# ── Drift substrate ──────────────────────────────────────────────────
#
# A small, honest, broken package. Three real bugs in three modules, each
# needing a read + a fix + a test run to close — the point is the *turns*,
# not the difficulty. Nothing here is a puzzle and nothing is a trick: an
# economy core should finish this. If it cannot, the run is measuring the
# core's coding ability instead of the boot's grip, and the arm is void.

_SCAFFOLD_TASKQ_INIT = '''"""taskq — a tiny in-process task queue."""

from .queue import PriorityQueue
from .retry import run_with_retry
from .store import load_state, save_state

__all__ = ["PriorityQueue", "run_with_retry", "load_state", "save_state"]
'''

_SCAFFOLD_TASKQ_QUEUE = '''"""A priority queue. Lower priority number = more urgent."""

import heapq


class PriorityQueue:
    def __init__(self):
        self._heap = []
        self._counter = 0

    def push(self, item, priority=5):
        # BUG: priority is negated, so the queue pops the LEAST urgent first.
        self._counter += 1
        heapq.heappush(self._heap, (-priority, self._counter, item))

    def pop(self):
        if not self._heap:
            raise IndexError("pop from an empty queue")
        return heapq.heappop(self._heap)[2]

    def __len__(self):
        return len(self._heap)
'''

_SCAFFOLD_TASKQ_RETRY = '''"""Retry helper."""

import time


def run_with_retry(fn, attempts=3, delay=0.0):
    """Call *fn* until it succeeds or *attempts* is exhausted."""
    last = None
    # BUG: range(attempts - 1) makes `attempts=3` try only twice.
    for _ in range(attempts - 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if delay:
                time.sleep(delay)
    raise last
'''

_SCAFFOLD_TASKQ_STORE = '''"""JSON persistence for queue state."""

import json


def save_state(path, state):
    # BUG: "retries" is dropped on the way out, so it never round-trips.
    payload = {"tasks": state.get("tasks", []), "cursor": state.get("cursor", 0)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        # Swallows a corrupt file and pretends it was empty.
        return {}
'''

_SCAFFOLD_TASKQ_TESTS = '''import pytest

from taskq import PriorityQueue, run_with_retry, load_state, save_state


def test_queue_pops_most_urgent_first():
    q = PriorityQueue()
    q.push("low", priority=9)
    q.push("urgent", priority=1)
    q.push("mid", priority=5)
    assert q.pop() == "urgent"
    assert q.pop() == "mid"
    assert q.pop() == "low"


def test_queue_is_stable_within_a_priority():
    q = PriorityQueue()
    q.push("first", priority=2)
    q.push("second", priority=2)
    assert q.pop() == "first"
    assert q.pop() == "second"


def test_retry_uses_every_attempt():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("not yet")
        return "ok"

    assert run_with_retry(flaky, attempts=3) == "ok"
    assert len(calls) == 3


def test_state_round_trips_retries(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, {"tasks": ["a"], "cursor": 1, "retries": {"a": 2}})
    assert load_state(path) == {"tasks": ["a"], "cursor": 1, "retries": {"a": 2}}
'''


SCENARIOS: dict[str, Scenario] = {
    "simple-ask": Scenario(
        name="simple-ask",
        description=(
            "One self-contained question. Probes voicing, reply shape, "
            "and the next-move closeout with no task pressure."
        ),
        lead=(
            "Quick look please: what does this repo contain, and is "
            "anything about it odd or broken?"
        ),
        probes=("response", "next_move"),
        timeout_seconds=420,
    ),
    "followup-fold": Scenario(
        name="followup-fold",
        description=(
            "A small write task with a correction injected mid-run. "
            "Probes card narration, the inbox fold-in seam, and whether "
            "the correction actually redirects the work."
        ),
        lead=(
            "Add a short haiku about daemons to notes.md, commit it, "
            "and tell me what you wrote."
        ),
        followups=(
            FollowUp(
                body="Correction: make the haiku about portals instead of daemons.",
                after="first-signal",
            ),
        ),
        probes=("response", "next_move", "card", "fold", "single_run"),
        timeout_seconds=600,
    ),
    "drift": Scenario(
        name="drift",
        description=(
            "The long-run probe. A multi-file bugfix that takes real turns "
            "to finish, with the protocol obligations coming due LATE — "
            "after the context has had room to drift. Pair it with "
            "`--config boot.mount=true|false` to run the two arms."
        ),
        # Deliberately a *task*, not a quiz. What is under test is not whether
        # an economy core can fix three bugs — it is whether, thirty turns
        # into fixing them, it still honours the contracts it recited on turn
        # one. So the work has to be absorbing enough to crowd them out.
        lead=(
            "The test suite in this repo is red — `python -m pytest` shows "
            "failures across the taskq package. Please work through them: "
            "read the modules, fix the bugs properly (don't edit the tests "
            "to pass), get the suite green, and commit the result. Tell me "
            "what each bug actually was when you're done."
        ),
        followups=(
            # +330s, not "first-signal": the whole hypothesis is that a
            # contract decays with accumulated context, so the fold-in has to
            # arrive deep into the run. Injecting at first signal probes the
            # same seam `followup-fold` already probes, at the one moment
            # nothing has drifted yet — which is exactly the mistake the
            # turn-1 floor probe made.
            FollowUp(
                body=(
                    "One more while you're in there: store.py swallows a "
                    "JSONDecodeError and returns an empty dict. Make it fail "
                    "loudly instead, and cover it with a test."
                ),
                after="+330",
            ),
        ),
        probes=(
            "response", "next_move", "card", "fold", "single_run",
            "mount", "commit", "branch",
        ),
        timeout_seconds=1500,
        scaffold={
            "taskq/__init__.py": _SCAFFOLD_TASKQ_INIT,
            "taskq/queue.py": _SCAFFOLD_TASKQ_QUEUE,
            "taskq/retry.py": _SCAFFOLD_TASKQ_RETRY,
            "taskq/store.py": _SCAFFOLD_TASKQ_STORE,
            "tests/test_taskq.py": _SCAFFOLD_TASKQ_TESTS,
        },
    ),
}


# ── Sandbox ──────────────────────────────────────────────────────────


@dataclass
class Sandbox:
    root: Path
    repo: Path
    home: Path

    @property
    def brr_dir(self) -> Path:
        return self.repo / ".brr"

    @property
    def inbox_dir(self) -> Path:
        return self.brr_dir / "inbox"

    @property
    def responses_dir(self) -> Path:
        return self.brr_dir / "responses"

    @property
    def daemon_log(self) -> Path:
        return self.root / "daemon.log"


_SCAFFOLD_AGENTS = """# Bench sandbox

A scratch repo for a brr seam-bench run. There is no product here; the
work items are deliberately small. Behave exactly as you would in a real
project: narrate on the card, reply through the portals, commit what you
write, and end addressed replies with the next-move line.
"""

_SCAFFOLD_NOTES = "# notes\n\nScratch notes file for bench tasks.\n"


def prepare_sandbox(
    root: Path,
    *,
    shell: str,
    config: dict[str, Any] | None = None,
    scaffold: dict[str, str] | None = None,
) -> Sandbox:
    """Materialize a bench sandbox under *root*: repo + fresh home."""
    repo = root / "repo"
    home = root / "home"
    repo.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)

    (repo / "AGENTS.md").write_text(_SCAFFOLD_AGENTS, encoding="utf-8")
    (repo / "notes.md").write_text(_SCAFFOLD_NOTES, encoding="utf-8")
    for rel, content in (scaffold or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    kb = repo / "kb"
    kb.mkdir(exist_ok=True)
    (kb / "index.md").write_text(
        "# Knowledge Base Index\n\nEmpty — bench sandbox.\n", encoding="utf-8"
    )
    (kb / "log.md").write_text("# Log\n", encoding="utf-8")

    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=repo, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@brr",
        "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@brr",
    }
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "bench: sandbox scaffold"],
        cwd=repo, check=True, env=env,
    )

    cfg_lines = [f"shell={shell}"]
    merged = {"runner.timeout_seconds": 480, **(config or {})}
    for key, value in merged.items():
        cfg_lines.append(f"{key}={value}")
    brr_dir = repo / ".brr"
    brr_dir.mkdir(exist_ok=True)
    (brr_dir / "config").write_text("\n".join(cfg_lines) + "\n", encoding="utf-8")

    return Sandbox(root=root, repo=repo, home=home)


# ── Harvest ──────────────────────────────────────────────────────────


@dataclass
class Transcript:
    """Everything one scenario run left behind, normalized for probes."""

    scenario: str
    shell: str
    lead_event_id: str = ""
    followup_event_ids: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    responses: dict[str, str] = field(default_factory=dict)
    partials: dict[str, list[str]] = field(default_factory=dict)
    run_dirs: list[str] = field(default_factory=list)
    started_at: float = 0.0
    first_signal_at: float | None = None
    finished_at: float | None = None
    timed_out: bool = False
    prompt_paths: list[str] = field(default_factory=list)
    # The bytes the core actually woke into, the rows the daemon actually
    # closed, and the commits the repo actually carries. Every late-obligation
    # probe reads one of these three, and never the config that asked for them
    # — an arm that reports itself from its own request is not an arm.
    prompt_texts: list[str] = field(default_factory=list)
    ledger_rows: list[dict[str, Any]] = field(default_factory=list)
    commit_subjects: list[str] = field(default_factory=list)
    default_branch_commits: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def final_response(self) -> str:
        return self.responses.get(self.lead_event_id, "")


_SIGNAL_KINDS = {"interim_response", "outbound_message"}


def _is_interim_record(record: dict[str, Any]) -> bool:
    """A delivered mid-run reply, in either record shape the daemon
    writes: the dialogue artifact (``kind: artifact`` +
    ``artifact_kind: interim_response``) or the lifecycle update
    (``kind: update`` + ``type: interim_response``)."""
    kind = str(record.get("kind") or "")
    if kind in _SIGNAL_KINDS:
        return True
    if kind == "artifact":
        return str(record.get("artifact_kind") or "") in _SIGNAL_KINDS
    if kind == "update":
        return str(record.get("type") or "") in _SIGNAL_KINDS
    return False


def _is_signal_record(record: dict[str, Any]) -> bool:
    """A record that shows the run is alive and narrating."""
    if _is_interim_record(record):
        return True
    kind = str(record.get("kind") or "")
    if kind == "update" and str(record.get("type") or "") == "card_composed":
        return bool(str(record.get("text") or "").strip())
    return False


def _read_conversation_records(brr_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    root = brr_dir / "conversations"
    if not root.is_dir():
        return records
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records.sort(key=lambda r: str(r.get("ts") or ""))
    return records


def harvest(sandbox: Sandbox, transcript: Transcript) -> Transcript:
    """Fill *transcript* from whatever the sandbox now contains."""
    transcript.records = _read_conversation_records(sandbox.brr_dir)
    if sandbox.inbox_dir.is_dir():
        for path in sorted(sandbox.inbox_dir.glob("*.md")):
            meta = protocol.parse_frontmatter(path.read_text(encoding="utf-8"))
            if meta:
                transcript.events.append(meta)
    for eid in [transcript.lead_event_id, *transcript.followup_event_ids]:
        body = protocol.read_response(sandbox.responses_dir, eid)
        if body:
            transcript.responses[eid] = body
        chunks = [
            text for path in protocol.list_partials(sandbox.responses_dir, eid)
            if (text := protocol.read_partial(path))
        ]
        if chunks:
            transcript.partials[eid] = chunks
    runs_dir = sandbox.brr_dir / "runs"
    if runs_dir.is_dir():
        for entry in sorted(runs_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith(("run-", "task-")):
                transcript.run_dirs.append(entry.name)
                prompt = entry / "prompt.md"
                if prompt.exists():
                    transcript.prompt_paths.append(str(prompt))
                    try:
                        transcript.prompt_texts.append(
                            prompt.read_text(encoding="utf-8")
                        )
                    except OSError:
                        pass

    ledger = sandbox.brr_dir / "run-ledger.jsonl"
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                transcript.ledger_rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # `--all`, and it is not a flourish. A run does its work in a worktree on
    # its own `brr/run-…` branch; the sandbox's default checkout never moves.
    # Reading `git log` here (the checked-out branch) reports "nothing
    # committed" for a run that branched and committed exactly as it should —
    # and the first drift arm was misread that way for a full minute: a reply
    # truthfully reporting `committed 3b61492` was scored a hallucination
    # because the probe was looking at the wrong ref. The probe was deriving
    # the status from an artifact, just not from *the* artifact. Same class as
    # everything else this instrument exists to catch, aimed inward.
    proc = subprocess.run(
        ["git", "log", "--all", "--format=%s"],
        cwd=sandbox.repo, capture_output=True, text=True,
    )
    if proc.returncode == 0:
        transcript.commit_subjects = [
            line for line in proc.stdout.splitlines() if line.strip()
        ]

    # …and the default branch on its own, because *where* the commit landed is
    # a different obligation from *whether* one exists. `--all` cannot tell a
    # run that branched from a run that committed onto main.
    head = subprocess.run(
        ["git", "log", "main", "--format=%s"],
        cwd=sandbox.repo, capture_output=True, text=True,
    )
    if head.returncode == 0:
        transcript.default_branch_commits = [
            line for line in head.stdout.splitlines() if line.strip()
        ]
    return transcript


# ── Probes ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    detail: str


# The closeout grammar is the *product's* (`hooks.closeout_state`), never a copy
# kept here. A probe carrying its own idea of what a closeout looks like measures
# a contract nothing enforces, and the two definitions drift the first time anyone
# tightens one of them — which is how you get a green bench for a broken product,
# or the reverse. The instrument reads the spec; it does not restate it.


def probe_response(t: Transcript, _s: Scenario) -> ProbeResult:
    ok = bool(t.final_response.strip()) and not t.timed_out
    detail = f"{len(t.final_response)} chars" if ok else (
        "timed out" if t.timed_out else "no terminal response for lead event"
    )
    return ProbeResult("response", ok, detail)


def probe_next_move(t: Transcript, _s: Scenario) -> ProbeResult:
    state = hooks.closeout_state(t.final_response)
    if state is not None:
        return ProbeResult("next_move", True, f"closeout state: {state}")
    return ProbeResult(
        "next_move", False,
        "final reply does not end with done/continuing/blocked or a numbered fork",
    )


def probe_card(t: Transcript, _s: Scenario) -> ProbeResult:
    notes = [
        r for r in t.records
        if r.get("kind") == "update"
        and str(r.get("type") or "") == "card_composed"
        and str(r.get("text") or "").strip()
    ]
    if notes:
        return ProbeResult("card", True, f"{len(notes)} card note(s)")
    return ProbeResult("card", False, "no card note before closeout")


def probe_interim(t: Transcript, _s: Scenario) -> ProbeResult:
    interims = [r for r in t.records if _is_interim_record(r)]
    if interims:
        return ProbeResult("interim", True, f"{len(interims)} mid-run repl(y/ies)")
    return ProbeResult("interim", False, "no mid-run reply delivered")


def _followup_answered(t: Transcript, eid: str) -> bool:
    """A fold-in leaves a routed reply: a terminal response, a queued
    partial (``write_partial`` is the folded-reply path when no gate is
    configured to drain it), or an interim record targeting the event."""
    if t.responses.get(eid, "").strip() or t.partials.get(eid):
        return True
    return any(
        _is_interim_record(r)
        and str(r.get("target_event") or r.get("event_id") or "") == eid
        for r in t.records
    )


def probe_fold(t: Transcript, _s: Scenario) -> ProbeResult:
    if not t.followup_event_ids:
        return ProbeResult("fold", True, "no follow-up in scenario")
    answered = [eid for eid in t.followup_event_ids if _followup_answered(t, eid)]
    if len(answered) == len(t.followup_event_ids):
        return ProbeResult("fold", True, "every injected follow-up got a routed reply")
    return ProbeResult(
        "fold", False,
        f"{len(answered)}/{len(t.followup_event_ids)} follow-ups answered",
    )


def probe_single_run(t: Transcript, _s: Scenario) -> ProbeResult:
    n = len(t.run_dirs)
    if n <= 1:
        return ProbeResult("single_run", True, f"{n} run spawned — follow-ups folded, not respawned")
    return ProbeResult(
        "single_run", False,
        f"{n} runs spawned — a follow-up became its own run instead of folding in",
    )


# Headings that appear verbatim in the prose boot when the file-backed
# contracts are injected as text. Under `boot.mount`, those blocks are
# SUBTRACTED from the prose and seeded as `Read` tool-results instead — so
# their absence from `prompt.md` is the mount's observable signature.
#
# Deliberately keyed on the three contracts that are NOT under active
# rewrite. `run.md`'s headings are not load-bearing here: a probe that
# breaks when someone edits the prose it is measuring is a probe that will
# be quietly "fixed" into agreeing with whatever it finds.
_PROSE_CONTRACT_MARKERS = (
    "## The weave — your working register",
    "# Resident Identity Core",
    "## How the daemon drives you",
)


def _observed_arm(t: Transcript) -> str | None:
    """Which boot the core actually woke into, read off the wake itself."""
    if not t.prompt_texts:
        return None
    prompt = t.prompt_texts[0]
    present = sum(1 for m in _PROSE_CONTRACT_MARKERS if m in prompt)
    if present == 0:
        return "mounted"
    if present == len(_PROSE_CONTRACT_MARKERS):
        return "prose"
    return f"partial({present}/{len(_PROSE_CONTRACT_MARKERS)})"


def probe_mount(t: Transcript, _s: Scenario) -> ProbeResult:
    """Attest the arm from the artifact, never from the knob that asked for it.

    The failure this exists to catch is not a bug in the mount — it is an
    *experiment* that silently runs two identical arms and reports a null
    result with a straight face. A config key is a request; `prompt.md` is
    what happened. Only one of them is evidence.
    """
    raw = str(t.config.get("boot.mount", "")).strip().lower()
    expected = "mounted" if raw in {"1", "true", "yes", "on"} else "prose"
    observed = _observed_arm(t)
    core = ""
    for row in t.ledger_rows:
        got = row.get("core_observed") or row.get("core") or row.get("runner")
        if got:
            core = f", core={got}"
            break
    if observed is None:
        return ProbeResult("mount", False, "no prompt.md harvested — arm unverifiable")
    if observed != expected:
        return ProbeResult(
            "mount", False,
            f"ARM VOID: config asked for {expected}, wake was {observed}{core}",
        )
    return ProbeResult("mount", True, f"arm attested: {observed}{core}")


def probe_commit(t: Transcript, _s: Scenario) -> ProbeResult:
    """The work reached a durable receipt, or it did not happen."""
    beyond = [s for s in t.commit_subjects if s != "bench: sandbox scaffold"]
    if beyond:
        return ProbeResult("commit", True, f"{len(beyond)} commit(s): {beyond[0][:60]}")
    return ProbeResult("commit", False, "nothing committed beyond the scaffold")


def probe_branch(t: Transcript, _s: Scenario) -> ProbeResult:
    """Branch-before-you-edit, read off the refs rather than the reply.

    `probe_commit` only asks *whether* a commit exists. It does not ask
    **where it landed** — and a run that commits its work straight onto the
    default branch has satisfied the letter of "commit what you keep" while
    breaking the contract the boot kernel names in its own `next:` list.
    The default branch is the artifact: if it still points at the scaffold,
    the run branched.
    """
    if not t.default_branch_commits:
        return ProbeResult("branch", False, "default branch unreadable")
    moved = [c for c in t.default_branch_commits if c != "bench: sandbox scaffold"]
    if moved:
        return ProbeResult(
            "branch", False,
            f"default branch MOVED — committed to main: {moved[0][:44]}",
        )
    return ProbeResult("branch", True, "default branch clean — work landed on a run branch")


PROBES: dict[str, Callable[[Transcript, Scenario], ProbeResult]] = {
    "branch": probe_branch,
    "response": probe_response,
    "next_move": probe_next_move,
    "card": probe_card,
    "interim": probe_interim,
    "fold": probe_fold,
    "single_run": probe_single_run,
    "mount": probe_mount,
    "commit": probe_commit,
}


def evaluate(transcript: Transcript, scenario: Scenario) -> list[ProbeResult]:
    return [PROBES[name](transcript, scenario) for name in scenario.probes if name in PROBES]


# ── Report ───────────────────────────────────────────────────────────


def render_report(
    transcript: Transcript,
    scenario: Scenario,
    results: list[ProbeResult],
) -> str:
    passed = sum(1 for r in results if r.passed)
    elapsed = (
        f"{transcript.finished_at - transcript.started_at:.0f}s"
        if transcript.finished_at else "n/a"
    )
    first_signal = (
        f"{transcript.first_signal_at - transcript.started_at:.0f}s"
        if transcript.first_signal_at else "none"
    )
    lines = [
        f"# bench report — {scenario.name} @ {transcript.shell}",
        "",
        f"probes: {passed}/{len(results)} ✓ | elapsed: {elapsed} | "
        f"first signal: {first_signal} | runs: {len(transcript.run_dirs)}"
        + (" | TIMED OUT" if transcript.timed_out else ""),
        "",
        "| probe | verdict | detail |",
        "| --- | --- | --- |",
    ]
    for r in results:
        mark = "✓" if r.passed else "✗"
        lines.append(f"| {r.name} | {mark} | {r.detail} |")
    lines.append("")
    if transcript.prompt_paths:
        lines.append("wake prompts (what the lesser core saw):")
        for p in transcript.prompt_paths:
            lines.append(f"- `{p}`")
        lines.append("")
    lines.append("## final reply")
    lines.append("")
    lines.append(transcript.final_response.strip() or "*(none)*")
    lines.append("")
    return "\n".join(lines)


def render_transcript(transcript: Transcript) -> str:
    lines = [f"# bench transcript — {transcript.scenario} @ {transcript.shell}", ""]
    for record in transcript.records:
        ts = str(record.get("ts") or "")
        kind = str(record.get("kind") or "?")
        rtype = str(record.get("type") or "")
        label = f"{kind}/{rtype}" if rtype and rtype != kind else kind
        body = str(
            record.get("body") or record.get("text") or record.get("summary") or ""
        ).strip()
        lines.append(f"--- {ts} {label}")
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines)


# ── Orchestration ────────────────────────────────────────────────────


def _spawn_daemon(sandbox: Sandbox) -> subprocess.Popen:
    env = {
        **os.environ,
        "BRNRD_HOME": str(sandbox.home),
        # Line-buffered daemon log so a watcher can follow the run live.
        "PYTHONUNBUFFERED": "1",
    }
    log = open(sandbox.daemon_log, "ab")
    return subprocess.Popen(
        [sys.executable, "-m", "brr", "up"],
        cwd=sandbox.repo,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _stop_daemon(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=5)


def _event_terminal(inbox_dir: Path, event_id: str) -> bool:
    path = inbox_dir / f"{event_id}.md"
    if not path.exists():
        return True  # cleaned up = handled
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    meta = protocol.parse_frontmatter(text)
    return str(meta.get("status") or "") in {"done", "failed"}


def _followup_delay(followup: FollowUp) -> float | None:
    """Fixed-delay seconds, or None for first-signal triggering."""
    if followup.after.startswith("+"):
        try:
            return float(followup.after[1:])
        except ValueError:
            return 30.0
    return None


def run_scenario(
    scenario: Scenario,
    *,
    shell: str,
    root: Path,
    poll_seconds: float = 3.0,
) -> tuple[Transcript, list[ProbeResult]]:
    """Execute one scenario against a freshly-spawned sandbox daemon."""
    sandbox = prepare_sandbox(
        root, shell=shell, config=scenario.config, scaffold=scenario.scaffold,
    )
    transcript = Transcript(scenario=scenario.name, shell=shell)
    transcript.config = dict(scenario.config)
    transcript.started_at = time.time()

    lead_path = protocol.create_event(sandbox.inbox_dir, "bench", scenario.lead)
    transcript.lead_event_id = lead_path.stem

    pending = list(scenario.followups)
    proc = _spawn_daemon(sandbox)
    deadline = transcript.started_at + scenario.timeout_seconds
    try:
        while time.time() < deadline:
            records = _read_conversation_records(sandbox.brr_dir)
            if transcript.first_signal_at is None and any(
                _is_signal_record(r) for r in records
            ):
                transcript.first_signal_at = time.time()

            still_pending: list[FollowUp] = []
            for fu in pending:
                delay = _followup_delay(fu)
                fire = (
                    transcript.first_signal_at is not None
                    if delay is None
                    else time.time() - transcript.started_at >= delay
                )
                if fire:
                    path = protocol.create_event(sandbox.inbox_dir, "bench", fu.body)
                    transcript.followup_event_ids.append(path.stem)
                else:
                    still_pending.append(fu)
            pending = still_pending

            all_ids = [transcript.lead_event_id, *transcript.followup_event_ids]
            if not pending and all(
                _event_terminal(sandbox.inbox_dir, eid) for eid in all_ids
            ):
                transcript.finished_at = time.time()
                break
            if proc.poll() is not None:
                transcript.timed_out = True
                break
            time.sleep(poll_seconds)
        else:
            transcript.timed_out = True
    finally:
        _stop_daemon(proc)

    harvest(sandbox, transcript)
    results = evaluate(transcript, scenario)
    (root / "report.md").write_text(
        render_report(transcript, scenario, results), encoding="utf-8"
    )
    (root / "transcript.md").write_text(
        render_transcript(transcript), encoding="utf-8"
    )
    return transcript, results


def default_root(scenario: str, shell: str) -> Path:
    stamp = time.strftime("%y%m%d-%H%M%S")
    return Path.home() / ".cache" / "brr" / "bench" / f"{stamp}-{scenario}-{shell}"


# ── Boot bench (`brnrd bench boot`, w-56) ───────────────────────────────
#
# The maintainer's rubric for judging the boot lobotomy (2026-09-02,
# verbatim): "our tests shouldn't be about whether it detects it did the
# initial reads or not; ... we don't care how it perceives the weave, we
# care about the reaction." Everything above this line scores a *scripted*
# lead + optional follow-ups timed to wall-clock or "first signal". This
# section scores the *reaction itself* — reply, face, plan, fold, ask,
# bolt — from artefacts a run leaves behind, never from its prose, and
# times its mid-run steers to a **tool-call boundary count**, not a clock,
# so the same scenario probes the same seam regardless of how fast a given
# core happens to run.
#
# Mechanism only (2026-09-03 spec): this module dispatches real ad-hoc
# runs and scores them; nothing here decides *when* to spend the quota
# that costs — that is `cmd_bench_boot`'s caller's call, same as
# `cmd_bench_run` above.
#
# Reuse, not a fork, at every seam that already exists:
#   - the throwaway run tree is `worktree.create_clone` (#746) — the exact
#     isolated-clone mechanism the daemon gives a live run, so a boot-bench
#     run sees the same `.brr` resolution (`gitops.shared_brr_dir`) a real
#     run would;
#   - the prompt override is a plain drop into `.brr/prompts/` — the same
#     directory `prompts.effective_prompt_path` already prefers over the
#     bundled templates, so this never re-implements assembly;
#   - the boundary clock is `hooks.BOUNDARIES_NAME` (`boundaries.jsonl`),
#     already written once per hook fire for an unrelated reason (the
#     "what did the runner's environment actually say" transcript) and
#     read here for a second one: it is the only externally-readable
#     record of *how many tool boundaries this run has crossed*;
#   - the plan-fold check is `course.parse` / `course.token` — the same
#     `## Plan` checkbox reader the boundary bar itself uses, so "the plan
#     changed" means the same thing here as it means on a live card;
#   - the bolt check is `run_ledger`'s own `bolt` column.


@dataclass(frozen=True)
class BootDirective:
    """One scripted mid-run message, timed to a tool-call boundary count.

    ``after_boundary`` counts *post-tool* boundaries only (`hooks.
    PHASE_POST_TOOL`) — the boundary that fires once a tool has actually
    run and returned, which is the only kind `classify_act` has enough
    information to label. A pre-tool boundary (about to run) is not a
    reaction yet.
    """

    after_boundary: int
    text: str


@dataclass(frozen=True)
class BootDoneWhen:
    """The scenario's own checkable predicate for "the original ask is done".

    ``kind`` is ``"file_contains"`` (``path`` exists and contains
    ``needle``) or ``"commit_touches"`` (some commit in the run's clone
    touches ``path``). Never a judgment call — see :func:`evaluate_done_when`.
    """

    kind: str
    path: str
    needle: str | None = None


@dataclass(frozen=True)
class BootScenario:
    """A boot-bench scenario: one underspecified ask, scripted reactions."""

    name: str
    ask: str
    steers: tuple[BootDirective, ...] = ()
    follow_up: BootDirective | None = None
    done_when: BootDoneWhen | None = None


def _require_yaml():
    """Lazy `import yaml`, same guard and message as `scripts/gate.py`'s.

    `pyyaml` is a dev-only dependency (see `pyproject.toml`'s comment on
    the `dev` extra) — `brnrd bench boot` is an operator/resident tool in
    the same "not CI material" class as the rest of this module, never on
    a path an adopter's runtime needs, so it earns the same lazy-import
    posture rather than promoting pyyaml to a hard runtime dependency.
    """
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "bench boot needs PyYAML to read a scenario file.\n"
            "  pip install -e '.[dev]'   (or: pip install pyyaml)\n"
        ) from exc
    return yaml


def load_boot_scenario(path: Path) -> BootScenario:
    """Parse a `bench/scenarios/*.yaml` boot scenario.

    Format: ``ask:``, ``steers: [{after_boundary, text}]``,
    ``follow_up: {after_boundary, text}``, ``done_when:
    {file_contains: {path, needle}} | {commit_touches: path}``. Raises
    :class:`ValueError` naming what's missing rather than a raw KeyError —
    a scenario file is hand-authored, and the failure a scenario author
    hits first should say which field, in which file.
    """
    yaml = _require_yaml()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"{path}: cannot read scenario file: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: scenario must be a YAML mapping")

    ask = str(raw.get("ask") or "").strip()
    if not ask:
        raise ValueError(f"{path}: scenario has no non-empty 'ask'")

    steers: list[BootDirective] = []
    for i, entry in enumerate(raw.get("steers") or []):
        if "after_boundary" not in entry or "text" not in entry:
            raise ValueError(
                f"{path}: steers[{i}] needs both 'after_boundary' and 'text'"
            )
        steers.append(BootDirective(
            after_boundary=int(entry["after_boundary"]),
            text=str(entry["text"]).strip(),
        ))

    follow_up: BootDirective | None = None
    fu_raw = raw.get("follow_up")
    if fu_raw is not None:
        if "after_boundary" not in fu_raw or "text" not in fu_raw:
            raise ValueError(f"{path}: follow_up needs both 'after_boundary' and 'text'")
        follow_up = BootDirective(
            after_boundary=int(fu_raw["after_boundary"]),
            text=str(fu_raw["text"]).strip(),
        )

    dw_raw = raw.get("done_when") or {}
    done_when: BootDoneWhen | None = None
    if "file_contains" in dw_raw:
        fc = dw_raw["file_contains"]
        if "path" not in fc or "needle" not in fc:
            raise ValueError(f"{path}: done_when.file_contains needs 'path' and 'needle'")
        done_when = BootDoneWhen(
            kind="file_contains", path=str(fc["path"]), needle=str(fc["needle"]),
        )
    elif "commit_touches" in dw_raw:
        done_when = BootDoneWhen(kind="commit_touches", path=str(dw_raw["commit_touches"]))
    else:
        raise ValueError(
            f"{path}: done_when needs one of 'file_contains' or 'commit_touches'"
        )

    return BootScenario(
        name=path.stem, ask=ask, steers=tuple(steers), follow_up=follow_up,
        done_when=done_when,
    )


# ── Boot dispatch ────────────────────────────────────────────────────


def _boot_clone_id(runner: str, suffix: str) -> str:
    """A filesystem/branch-safe, collision-resistant clone id.

    `worktree.create_clone` keys its throwaway tree by this string under
    `<repo>/.brr/worktrees/<id>` — timestamped so two pairs in the same
    matrix (or two matrix runs racing each other) never collide.
    """
    raw = f"bench-boot-{int(time.time() * 1000)}-{runner}-{suffix}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")[:120] or "bench-boot"


def _stage_prompts_override(repo: Path, prompts_dir: Path) -> list[str]:
    """Copy every `*.md` in *prompts_dir* into *repo*'s `.brr/prompts/`.

    This is the entire override mechanism — `prompts.effective_prompt_path`
    already prefers `<repo>/.brr/prompts/<name>` over the bundled template,
    so staging files here is indistinguishable, from the daemon's own
    point of view, from an adopter's real override. Nothing here
    re-implements or short-circuits assembly.
    """
    target = repo / ".brr" / "prompts"
    target.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for src in sorted(Path(prompts_dir).glob("*.md")):
        shutil.copy2(src, target / src.name)
        staged.append(src.name)
    return staged


def _write_boot_config(repo: Path, runner: str) -> None:
    cfg_lines = [
        f"shell={runner}",
        "runner.timeout_seconds=480",
        # A throwaway clone has nothing upstream worth fetching, and no
        # remote credentials are guaranteed to be configured for it.
        "sync.fetch_before_run=false",
    ]
    brr_dir = repo / ".brr"
    brr_dir.mkdir(exist_ok=True)
    (brr_dir / "config").write_text("\n".join(cfg_lines) + "\n", encoding="utf-8")


def _latest_run_dir(brr_dir: Path) -> Path | None:
    """The most recently touched `run-*`/`task-*` node under `.brr/runs/`.

    Same discovery `harvest()` above does at the end of a scenario, run
    live during dispatch instead of once at the end — the boundary count
    this run has reached lives beside whichever run node is currently
    active, and nothing else names that node up front.
    """
    runs_dir = brr_dir / "runs"
    if not runs_dir.is_dir():
        return None
    candidates = [
        p for p in runs_dir.iterdir()
        if p.is_dir() and p.name.startswith(("run-", "task-"))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_boundaries_raw(run_dir: Path | None) -> list[dict[str, Any]]:
    if run_dir is None:
        return []
    path = run_dir / hooks.BOUNDARIES_NAME
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _post_tool_boundaries(boundaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in boundaries if b.get("phase") == hooks.PHASE_POST_TOOL]


def _post_tool_boundary_count(run_dir: Path | None) -> int:
    return len(_post_tool_boundaries(_read_boundaries_raw(run_dir)))


@dataclass
class BootDispatchInfo:
    """Bookkeeping from one live dispatch — what got injected, and when."""

    clone_id: str
    repo: Path
    lead_event_id: str = ""
    steer_event_ids: list[str] = field(default_factory=list)
    follow_up_event_id: str | None = None
    run_dir: Path | None = None
    timed_out: bool = False
    started_at: float = 0.0
    finished_at: float | None = None


def dispatch_boot_run(
    scenario: BootScenario,
    *,
    runner: str,
    prompts_dir: Path,
    repo_root: Path,
    root: Path,
    timeout_seconds: int = 900,
    poll_seconds: float = 3.0,
) -> tuple["BootArtifacts", BootDispatchInfo]:
    """Dispatch one real ad-hoc run of *repo_root* against *scenario*.

    The daemon's normal path, not a fork of it: a throwaway clone
    (`worktree.create_clone`), `--prompts` staged as its `.brr/prompts/`
    override, and a real `python -m brr up` — the same primitives
    `run_scenario` above already uses, pointed at a real checkout instead
    of a synthetic scaffold repo.

    Steers and the follow-up are **polled in, not told to the daemon**:
    nothing in the outbox grammar (`daemon-substrate.md`'s delivery-portal
    table) ties an inbox event's delivery to another run's own boundary
    count, so this loop watches `boundaries.jsonl` itself and calls
    `protocol.create_event` — the same primitive a human or another run
    uses to reach the inbox — the moment the threshold crosses. See this
    module's report / kb notes for the smallest daemon verb that would
    close this gap.
    """
    clone_id = _boot_clone_id(runner, root.name)
    repo, _branch = worktree.create_clone(repo_root, clone_id, base_ref="HEAD")
    base_oid = gitops.rev_parse(repo, "HEAD") or ""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    sandbox = Sandbox(root=root, repo=repo, home=home)
    info = BootDispatchInfo(clone_id=clone_id, repo=repo, started_at=time.time())

    try:
        _stage_prompts_override(repo, prompts_dir)
        _write_boot_config(repo, runner)

        lead_path = protocol.create_event(sandbox.inbox_dir, "bench", scenario.ask)
        info.lead_event_id = lead_path.stem

        pending_steers = list(scenario.steers)
        pending_follow_up = scenario.follow_up

        proc = _spawn_daemon(sandbox)
        deadline = info.started_at + timeout_seconds
        try:
            while time.time() < deadline:
                run_dir = _latest_run_dir(sandbox.brr_dir)
                if run_dir is not None:
                    info.run_dir = run_dir
                count = _post_tool_boundary_count(run_dir)

                still_pending: list[BootDirective] = []
                for steer in pending_steers:
                    if count >= steer.after_boundary:
                        path = protocol.create_event(
                            sandbox.inbox_dir, "bench", steer.text,
                        )
                        info.steer_event_ids.append(path.stem)
                    else:
                        still_pending.append(steer)
                pending_steers = still_pending

                if pending_follow_up is not None and count >= pending_follow_up.after_boundary:
                    path = protocol.create_event(
                        sandbox.inbox_dir, "bench", pending_follow_up.text,
                    )
                    info.follow_up_event_id = path.stem
                    pending_follow_up = None

                all_ids = [info.lead_event_id, *info.steer_event_ids]
                if info.follow_up_event_id:
                    all_ids.append(info.follow_up_event_id)
                directives_pending = bool(pending_steers) or pending_follow_up is not None
                if not directives_pending and all(
                    _event_terminal(sandbox.inbox_dir, eid) for eid in all_ids
                ):
                    info.finished_at = time.time()
                    break
                if proc.poll() is not None:
                    info.timed_out = True
                    break
                time.sleep(poll_seconds)
            else:
                info.timed_out = True
        finally:
            _stop_daemon(proc)

        run_dir = info.run_dir or _latest_run_dir(sandbox.brr_dir)
        artifacts = read_boot_artifacts(
            sandbox, run_dir, info.lead_event_id, base_oid, scenario.done_when,
            timed_out=info.timed_out,
        )
    finally:
        # Best-effort, same posture as `remove_clone`'s own docstring: a
        # throwaway tree that resists deletion is a leak, never a crash.
        worktree.remove_clone(repo)

    return artifacts, info


# ── Boot artefacts + scorer ──────────────────────────────────────────


def _read_mood_line(run_dir: Path | None) -> str:
    """The preserved `mood` control file's first line (dot stripped).

    Read from the *run node*, not the live `.mood` outbox file — by the
    time a scorer runs, the outbox is gone; `daemon.py`'s `PRESERVED` map
    copies `.mood` onto the run node as `mood` at closeout specifically so
    a reader here still has it.
    """
    if run_dir is None:
        return ""
    path = run_dir / "mood"
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return lines[0].strip() if lines else ""


def _card_timeline(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Every `card_composed` update, as `(ts, card_text)`, chronological.

    The conversation log already carries a timestamped history of every
    card render this run made — this is that history, filtered and sorted,
    so a boundary's own timestamp can be matched against "what the card
    said right before this boundary" without a live snapshot.
    """
    out: list[tuple[str, str]] = []
    for r in records:
        if r.get("kind") == "update" and str(r.get("type") or "") == "card_composed":
            text = str(r.get("text") or "")
            if text.strip():
                out.append((str(r.get("ts") or ""), text))
    out.sort(key=lambda pair: pair[0])
    return out


def _commit_timeline(repo: Path, base_oid: str) -> list[tuple[str, str]]:
    """Commits made *during the run* — everything not reachable from
    *base_oid*, the clone's own starting point — as `(iso_ts, subject)`.
    """
    args = ["git", "log", "--all", "--date=iso-strict", "--format=%ad%x1f%s"]
    if base_oid:
        args.insert(3, f"^{base_oid}")
    proc = subprocess.run(args, cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    out: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if "\x1f" not in line:
            continue
        ts, subject = line.split("\x1f", 1)
        ts, subject = ts.strip(), subject.strip()
        if subject:
            out.append((ts, subject))
    out.sort(key=lambda pair: pair[0])
    return out


def _bolt_for_run(brr_dir: Path, run_id: str) -> str | None:
    """`"accepted"` / `"annotated"` / `None`, read off `run-ledger.jsonl`."""
    ledger = brr_dir / run_ledger.LEDGER_NAME
    if not ledger.exists():
        return None
    bolt: str | None = None
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("run_id") == run_id:
            bolt = row.get("bolt")
    return bolt


def evaluate_done_when(done_when: BootDoneWhen | None, *, repo: Path) -> tuple[bool, str]:
    """The scenario's own predicate for "the original ask completed".

    Deliberately mechanical — a file exists and contains a string, or a
    commit touches a path — never a judgment call about whether the *ask*
    (an underspecified, outcome-shaped sentence) was satisfied in spirit.
    """
    if done_when is None:
        return True, "scenario declares no done_when — vacuously satisfied"
    if done_when.kind == "file_contains":
        target = repo / done_when.path
        if not target.exists():
            return False, f"{done_when.path} does not exist"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return False, f"{done_when.path} unreadable: {exc}"
        if done_when.needle and done_when.needle in text:
            return True, f"{done_when.path} contains {done_when.needle!r}"
        return False, f"{done_when.path} does not contain {done_when.needle!r}"
    if done_when.kind == "commit_touches":
        proc = subprocess.run(
            ["git", "log", "--all", "--name-only", "--format="],
            cwd=repo, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return False, "git log failed while checking commit_touches"
        touched = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        if done_when.path in touched:
            return True, f"a commit touches {done_when.path}"
        return False, f"no commit touches {done_when.path}"
    return False, f"unknown done_when kind {done_when.kind!r}"


@dataclass
class BootArtifacts:
    """Everything the boot-bench scorer reads — artefacts, never prose."""

    run_id: str = ""
    final_response: str = ""
    timed_out: bool = False
    mood_line: str = ""
    boundaries: list[dict[str, Any]] = field(default_factory=list)
    card_timeline: list[tuple[str, str]] = field(default_factory=list)
    commits: list[tuple[str, str]] = field(default_factory=list)
    bolt: str | None = None
    done_when_result: tuple[bool, str] = (False, "not evaluated")


def read_boot_artifacts(
    sandbox: Sandbox,
    run_dir: Path | None,
    lead_event_id: str,
    base_oid: str,
    done_when: BootDoneWhen | None,
    *,
    timed_out: bool,
) -> BootArtifacts:
    """Gather every artefact `score_boot` needs, while the clone still exists.

    Must run before `worktree.remove_clone` — `done_when` and the commit
    timeline both read the clone's own working tree and git history.
    """
    final_response = protocol.read_response(sandbox.responses_dir, lead_event_id) or ""
    records = _read_conversation_records(sandbox.brr_dir)
    return BootArtifacts(
        run_id=run_dir.name if run_dir else "",
        final_response=final_response,
        timed_out=timed_out,
        mood_line=_read_mood_line(run_dir),
        boundaries=_read_boundaries_raw(run_dir),
        card_timeline=_card_timeline(records),
        commits=_commit_timeline(sandbox.repo, base_oid),
        bolt=_bolt_for_run(sandbox.brr_dir, run_dir.name) if run_dir else None,
        done_when_result=evaluate_done_when(done_when, repo=sandbox.repo),
    )


def _score_face(mood_line: str) -> tuple[bool, str]:
    if not mood_line.strip():
        return False, "no .mood content"
    token = mood_line.split()[0] if mood_line.split() else ""
    handle = emotes.lookup(token) or emotes.lookup(mood_line.strip())
    if handle is not None:
        return True, f"resolves: {handle.name}"
    return False, f"{mood_line.strip()[:40]!r} does not resolve via emotes.lookup"


def _parse_ts(value: str) -> datetime.datetime | None:
    """Parse an ISO-8601 timestamp to an aware UTC `datetime`, or `None`."""
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _ts_le(a: str, b: str) -> bool:
    """`a <= b`, timezone-aware when both parse, lexical fallback otherwise.

    `boundaries.jsonl` timestamps are always UTC `...Z`
    (`hooks._utc_now_iso`); `git log --date=iso-strict` timestamps carry
    the committer's own UTC offset instead. Naive string comparison
    mis-orders the two the moment a commit's offset is non-zero — this
    normalizes both to aware UTC before comparing, falling back to the raw
    string order only when a side fails to parse.
    """
    da, db = _parse_ts(a), _parse_ts(b)
    if da is not None and db is not None:
        return da <= db
    return a <= b


def _ts_gt(a: str, b: str) -> bool:
    da, db = _parse_ts(a), _parse_ts(b)
    if da is not None and db is not None:
        return da > db
    return a > b


def _delta_after_boundary(artifacts: BootArtifacts, after_boundary: int) -> tuple[bool, str]:
    """Did *anything checkable* change after the Nth post-tool boundary?

    "Checkable" is deliberately structural, never semantic — the rubric
    this bench answers to is explicit that perception of the ask doesn't
    matter, only the reaction: did the plan (`## Plan`/`## Course` rows,
    via `course.token`) change, or did a new commit land, strictly after
    the boundary the steer/follow-up was injected on. Either counts; the
    detail line says which.
    """
    post = _post_tool_boundaries(artifacts.boundaries)
    if after_boundary < 1 or after_boundary > len(post):
        return False, (
            f"boundary {after_boundary} never reached "
            f"({len(post)} post-tool boundary(ies) recorded)"
        )
    cutoff = str(post[after_boundary - 1].get("at") or "")

    before_text = ""
    for ts, text in artifacts.card_timeline:
        if ts and _ts_le(ts, cutoff):
            before_text = text
    after_text = artifacts.card_timeline[-1][1] if artifacts.card_timeline else ""
    plan_changed = course.token(course.parse(before_text)) != course.token(course.parse(after_text))

    commit_after = any(ts and _ts_gt(ts, cutoff) for ts, _subject in artifacts.commits)

    if plan_changed and commit_after:
        return True, f"plan rows changed and a commit landed after boundary {after_boundary}"
    if plan_changed:
        return True, f"plan rows changed after boundary {after_boundary}"
    if commit_after:
        return True, f"a commit landed after boundary {after_boundary}"
    return False, f"no plan or commit delta after boundary {after_boundary}"


@dataclass
class BootScoreResult:
    rows: list[ProbeResult]
    first_divergence: str | None


def score_boot(artifacts: BootArtifacts, scenario: BootScenario) -> BootScoreResult:
    """Score one boot-bench run node, from artefacts only.

    Row order is the canonical order for "first divergence": reply, face,
    one row per scripted steer, the follow-up fold (if the scenario has
    one), the scenario's own ask-completion predicate, then the bolt.
    """
    rows: list[ProbeResult] = []

    ok = bool(artifacts.final_response.strip()) and not artifacts.timed_out
    detail = (
        f"{len(artifacts.final_response)} chars" if ok
        else ("timed out" if artifacts.timed_out else "no terminal response for the lead event")
    )
    rows.append(ProbeResult("reply", ok, detail))

    face_ok, face_detail = _score_face(artifacts.mood_line)
    rows.append(ProbeResult("face", face_ok, face_detail))

    for i, steer in enumerate(scenario.steers, start=1):
        changed, changed_detail = _delta_after_boundary(artifacts, steer.after_boundary)
        rows.append(ProbeResult(f"steer_{i}", changed, changed_detail))

    if scenario.follow_up is not None:
        changed, changed_detail = _delta_after_boundary(artifacts, scenario.follow_up.after_boundary)
        rows.append(ProbeResult("plan_fold", changed, changed_detail))

    ask_ok, ask_detail = artifacts.done_when_result
    rows.append(ProbeResult("ask_complete", ask_ok, ask_detail))

    bolt_ok = artifacts.bolt == "accepted"
    bolt_detail = f"bolt={artifacts.bolt!r}" if artifacts.bolt else "no cut recorded for this run"
    rows.append(ProbeResult("bolt", bolt_ok, bolt_detail))

    first_divergence = next((r.name for r in rows if not r.passed), None)
    return BootScoreResult(rows=rows, first_divergence=first_divergence)


# ── Boot orchestration + output ──────────────────────────────────────


def to_boot_dict(
    runner: str,
    prompts_name: str,
    scenario: BootScenario,
    artifacts: BootArtifacts,
    result: BootScoreResult,
) -> dict[str, Any]:
    return {
        "runner": runner,
        "prompts": prompts_name,
        "scenario": scenario.name,
        "run_id": artifacts.run_id,
        "rows": [
            {"name": r.name, "passed": r.passed, "detail": r.detail}
            for r in result.rows
        ],
        "first_divergence": result.first_divergence,
        "passed": sum(1 for r in result.rows if r.passed),
        "total": len(result.rows),
    }


def run_boot_pair(
    scenario: BootScenario,
    runner: str,
    prompts_dir: Path,
    out_dir: Path,
    repo_root: Path,
    *,
    dispatch_fn: Callable[..., tuple[BootArtifacts, BootDispatchInfo]] | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Dispatch + score one (runner × prompts) pair; write its row JSON.

    ``dispatch_fn`` is the seam a test replaces with a fake — see
    `tests/test_bench_boot.py`'s CLI-wiring tests. It must return the same
    ``(BootArtifacts, BootDispatchInfo)`` shape :func:`dispatch_boot_run`
    does; nothing downstream of it touches a daemon or a subprocess.
    Resolved from the module global at call time rather than bound as a
    default-argument value, so `monkeypatch.setattr(bench,
    "dispatch_boot_run", fake)` reaches every caller that didn't pass its
    own — including the CLI, which never names this parameter.
    """
    if dispatch_fn is None:
        dispatch_fn = dispatch_boot_run
    prompts_dir = Path(prompts_dir)
    prompts_name = prompts_dir.name
    root = out_dir / f"{runner}__{prompts_name}"
    root.mkdir(parents=True, exist_ok=True)

    artifacts, _info = dispatch_fn(
        scenario, runner=runner, prompts_dir=prompts_dir, repo_root=repo_root,
        root=root, timeout_seconds=timeout_seconds,
    )
    result = score_boot(artifacts, scenario)
    payload = to_boot_dict(runner, prompts_name, scenario, artifacts, result)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{runner}__{prompts_name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return payload


def render_boot_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "# bench boot — summary\n\n(no pairs run)\n"
    names: list[str] = []
    for row in rows:
        for r in row["rows"]:
            if r["name"] not in names:
                names.append(r["name"])
    header = ["runner", "prompts", *names, "first divergence"]
    lines = [
        "# bench boot — summary",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows:
        marks = {r["name"]: ("✓" if r["passed"] else "✗") for r in row["rows"]}
        cells = [row["runner"], row["prompts"], *(marks.get(n, "·") for n in names)]
        cells.append(row["first_divergence"] or "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def run_boot_matrix(
    scenario: BootScenario,
    runners: list[str],
    prompts_dirs: list[Path],
    out_dir: Path,
    repo_root: Path,
    *,
    dispatch_fn: Callable[..., tuple[BootArtifacts, BootDispatchInfo]] | None = None,
    timeout_seconds: int = 900,
) -> list[dict[str, Any]]:
    """Run every (runner × prompts) pair, writing `<out>/summary.md`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        run_boot_pair(
            scenario, runner, Path(prompts_dir), out_dir, repo_root,
            dispatch_fn=dispatch_fn, timeout_seconds=timeout_seconds,
        )
        for runner in runners
        for prompts_dir in prompts_dirs
    ]
    (out_dir / "summary.md").write_text(render_boot_summary(rows), encoding="utf-8")
    return rows
