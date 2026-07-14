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

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import hooks, protocol

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
            "`--config boot.transcript=true|false` to run the two arms."
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
            "mount", "classification", "commit", "branch",
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
# contracts are injected as text. Under `boot.transcript`, those blocks are
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
    raw = str(t.config.get("boot.transcript", "")).strip().lower()
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


def probe_classification(t: Transcript, _s: Scenario) -> ProbeResult:
    """`.task-classification` — the obligation with no natural deadline but
    the closeout, which is exactly why it is a drift probe."""
    if not t.ledger_rows:
        return ProbeResult("classification", False, "no closed-run ledger row")
    written = [
        str(r.get("task_classification"))
        for r in t.ledger_rows
        if r.get("task_classification")
    ]
    if written:
        return ProbeResult("classification", True, f"slug(s): {', '.join(written)}")
    return ProbeResult(
        "classification", False,
        f"{len(t.ledger_rows)} closed run(s), every task_classification null",
    )


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
    "classification": probe_classification,
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
