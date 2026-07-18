"""Runner hooks back channel — ``brnrd hook <phase>``.

Tier 2 of the runner interface (``kb/design-runner-back-channel.md``).
Some target CLI agents expose runner-native lifecycle hooks: callbacks at
tool/turn boundaries whose JSON result is injected back into the agent's
context. brr exposes **one** endpoint, ``brnrd hook <phase>``, reading a JSON
event on stdin and writing a JSON result on stdout. brr owns the abstract
*phases*; each hook-backed runner profile maps its native hook names onto
them, and brr renders the one neutral result into that runner's native fields.

Two directions across the single endpoint:

- **Outbound flush** (runner → portal broker): ``post-tool`` / ``stop`` drop a
  token in ``.flush`` and, on daemon-managed Tier-2 runs, wait for the broker's
  matching ``.flush.ack``. The daemon remains the sole process that promotes
  files (worker emit + conversation indexing are in-process-coupled), but the
  *runner boundary* now owns when the promotion must be complete. In
  particular, Stop cannot race a final ``gate: forge`` handoff against runner
  exit. Tier-0/1 runners retain the heartbeat/post-return recovery path.
- **Inbound injection** (daemon → runner): the hook reads the
  daemon-written ``portal-state.json`` and, when its ``change_token`` moved
  since the last injection, returns a compact delta for the runner to weave
  into context. This makes the INBOUND-CHECK portal *automatic* instead of
  "remember to read ``inbox.json``."

The neutral result the phases compute is ``{inject, block, block_reason}``;
:func:`render_native` turns it into each runner flavour's native hook
fields. Keeping that split is what lets one endpoint serve three runners.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import facets

PHASE_POST_TOOL = "post-tool"
PHASE_STOP = "stop"
PHASE_SESSION_START = "session-start"
PHASES = (PHASE_POST_TOOL, PHASE_STOP, PHASE_SESSION_START)

# Control dotfile the post-tool/stop hook touches to ask the daemon to
# drain now. Lives beside the outbox; the daemon's drain skips dotfiles, so
# it is never delivered. Matches the ``.keepalive`` / ``.card`` idiom.
FLUSH_SIGNAL_NAME = ".flush"
FLUSH_ACK_NAME = ".flush.ack"
_FLUSH_ACK_TIMEOUT_SECONDS = 5.0
# Per-run hook memory: the last change_token injected, and whether a
# premature stop was already blocked once (so the nudge fires once, not in
# a loop). Daemon-independent; the hook owns this file.
HOOK_STATE_NAME = ".hook-state.json"

# Closeout artifact obligations the armed guard can escalate from the soft
# `inject` mention (see `format_delta`, which already surfaces a missing
# `.task-classification` / stale card / unpushed SCM as additionalContext)
# to a hard `block`. Each maps to a control file the resident owes by
# closeout. The check reads the *file*, fresh, at Stop — never the
# heartbeat portal snapshot, which can predate a control file written in the
# run's final action. That is the same "assert only from THE artifact"
# doctrine the next-move guard keeps, and why escalation lives here rather
# than promoting the portal-derived `inject` lines in place.
CARD_NAME = ".card"
TASK_CLASSIFICATION_NAME = ".task-classification"
FORGE_HANDOFF_NAME = ".forge-handoff"
_CLOSEOUT_ARTIFACT_ORDER = ("card", "classification")
_CLOSEOUT_ARTIFACTS = {
    "card": (
        CARD_NAME,
        "no `.card` was written — put one line on the progress surface the "
        "user watches between replies",
    ),
    "classification": (
        TASK_CLASSIFICATION_NAME,
        "no `.task-classification` was written — add the one-slug run shape "
        "(its run_ledger row joins the cost rollup on that field and stays "
        "null without it)",
    ),
}


# ── Context resolution ──────────────────────────────────────────────────


class HookContext:
    """Resolved run handles the hook operates on, from the runner env."""

    def __init__(self, env: dict[str, str]) -> None:
        self.run_id = env.get("BRR_RUN_ID") or None
        self.event_id = env.get("BRR_EVENT_ID") or None
        self.flavour = (env.get("BRR_RUNNER") or "").strip().lower() or None
        # The closeout guard, armed by the daemon (`hooks.next_move`). Off unless
        # the daemon says otherwise: the guard is an *unmeasured* intervention, and
        # the flag is what keeps a control arm alive for the bench to measure it
        # against — the same discipline that made `boot.mount` an experiment
        # instead of a hunch that shipped.
        self.next_move_guard = (
            env.get("BRR_NEXT_MOVE_GUARD") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        # The artifact obligations the closeout guard escalates to a block,
        # armed per-run by the daemon (`BRR_CLOSEOUT_OBLIGATIONS=card,...`).
        # Empty unless armed — same control-arm discipline as next_move_guard.
        raw_obligations = (env.get("BRR_CLOSEOUT_OBLIGATIONS") or "").strip()
        self.closeout_obligations = frozenset(
            part.strip().lower()
            for part in raw_obligations.split(",")
            if part.strip()
        )
        # Repo checkout + seed ref, for the `scm` closeout obligation to read
        # git *fresh at Stop* rather than trust the heartbeat snapshot (which
        # can predate a commit made in the run's final action). Armed by the
        # daemon only for the `host` environment — the one that does NOT
        # publish the end branch, so uncommitted / unpushed work is genuinely
        # lost. In a worktree the daemon publishes, and this stays unset.
        repo = env.get("BRR_REPO_DIR")
        self.repo_dir = Path(repo) if repo else None
        self.seed_ref = (env.get("BRR_SEED_REF") or "").strip() or None
        self.flush_sync = (
            env.get("BRR_FLUSH_SYNC") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        portal = env.get("BRR_PORTAL_STATE")
        self.portal_state_path = Path(portal) if portal else None
        outbox = env.get("BRR_OUTBOX_DIR")
        if outbox:
            self.outbox_dir: Path | None = Path(outbox)
        elif self.portal_state_path is not None:
            # Fall back to the portal file's directory — the live state and
            # the outbox share the per-event run directory.
            self.outbox_dir = self.portal_state_path.parent
        else:
            self.outbox_dir = None

    @property
    def flush_path(self) -> Path | None:
        return self.outbox_dir / FLUSH_SIGNAL_NAME if self.outbox_dir else None

    @property
    def state_path(self) -> Path | None:
        return self.outbox_dir / HOOK_STATE_NAME if self.outbox_dir else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_hook_state(ctx: HookContext) -> dict[str, Any]:
    return _read_json(ctx.state_path)


FIRED_KEY = "fired"


def _utc_now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_fired(state: dict[str, Any], phase: str) -> None:
    """Stamp *phase*'s last-fired time into the hook state.

    The BootScore's hook contract reports this back (``brnrd prompts show``),
    so the phase set can be seen *firing* rather than merely declared.  Kept
    per-phase: a post-tool hook firing says nothing about session-start.
    """
    fired = state.get(FIRED_KEY)
    if not isinstance(fired, dict):
        fired = {}
    fired[phase] = _utc_now_iso()
    state[FIRED_KEY] = fired


def _write_hook_state(ctx: HookContext, state: dict[str, Any]) -> None:
    if ctx.state_path is None:
        return
    try:
        ctx.state_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.state_path.write_text(
            json.dumps(state, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        pass


def _touch_flush(ctx: HookContext) -> None:
    """Request a portal flush and, when armed, wait for its acceptance.

    The token/ack handshake makes the lifecycle boundary the authority: a Stop
    hook returns only after every complete outbox message visible at that
    boundary has been promoted. Ad-hoc hooks and older daemons do not set
    ``BRR_FLUSH_SYNC`` and keep the old fire-and-forget behaviour.
    """
    if ctx.flush_path is None:
        return
    token = str(time.time_ns())
    ack_path = ctx.flush_path.parent / FLUSH_ACK_NAME
    try:
        ctx.flush_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.flush_path.write_text(token, encoding="utf-8")
    except OSError:
        return
    if not ctx.flush_sync:
        return
    deadline = time.monotonic() + _FLUSH_ACK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            if ack_path.read_text(encoding="utf-8").strip() == token:
                return
        except OSError:
            pass
        time.sleep(0.01)


# ── Injection rendering (portal-state → compact delta) ───────────────────


def format_delta(
    payload: dict[str, Any], *, seed: bool = False, stop: bool = False
) -> str | None:
    """Render a compact context delta from the live portal-state payload.

    Short on purpose: it is woven into the agent's context every boundary,
    so it carries only what shifts attention — pending events, delivery
    acks, budget pressure — plus the run's compact attested produce briefing.

    Two boundaries render *unconditionally* (``seed`` and ``stop``): the
    seed is the initial capsule, and the stop is the closeout capsule. At
    those moments an explicit "0 pending event(s)" is itself the signal —
    silence is ambiguous, an affirmative "all clear" is not (maintainer's
    point, 2026-06-23). Stop additionally surfaces the local SCM posture
    (unpushed commits / modified files) so a wake about to end sees its
    branch is not yet pushed. Mid-run (``post-tool``) it stays gated and
    returns ``None`` when nothing shifted, so the channel injects no noise —
    except card staleness (2026-07-05), which renders at every boundary: a
    stale-or-blank ``.card`` is itself a mid-run failure, not one that can
    wait for closeout.
    """
    if not payload:
        return None
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    attention = (
        payload.get("attention")
        if isinstance(payload.get("attention"), dict) else {}
    )
    inbound = (
        payload.get("inbound") if isinstance(payload.get("inbound"), dict) else {}
    )
    outbound = (
        payload.get("outbound")
        if isinstance(payload.get("outbound"), dict) else {}
    )
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    scm = payload.get("scm") if isinstance(payload.get("scm"), dict) else {}
    produce = (
        payload.get("produce") if isinstance(payload.get("produce"), dict) else {}
    )
    card = payload.get("card") if isinstance(payload.get("card"), dict) else {}
    resources = (
        payload.get("resources")
        if isinstance(payload.get("resources"), dict) else {}
    )

    pending = int(attention.get("pending_event_count", 0) or 0)
    pending_files = int(attention.get("pending_outbox_file_count", 0) or 0)
    lines: list[str] = []
    if seed:
        header = "brnrd portal seed"
    elif stop:
        header = "brnrd portal closeout"
    else:
        header = "brnrd portal update"
    # Framing, not just data: a bare count reads as ambient telemetry and
    # habituates fast — a maintainer caught this live (2026-07-05) when two
    # follow-ups sat unacknowledged on the outward-facing card for 8 minutes
    # despite the count appearing in every batch. Non-zero pending events get
    # an explicit action verb so the line reads as something to do, not
    # something to note; zero stays the plain affirmative-clear line.
    header_line = (
        f"[{header}] {pending} pending event(s), "
        f"{pending_files} undelivered outbox file(s)."
    )
    if pending:
        header_line += (
            " Address each below — fold in, or say on .card why it stays "
            "queued — before your next plan boundary or closeout."
        )
    lines.append(header_line)
    events = inbound.get("events") if isinstance(inbound.get("events"), list) else []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        summary = str(ev.get("summary") or "").strip()
        lines.append(
            f"- pending {ev.get('id') or '-'} ({ev.get('source') or '-'}): "
            f"{summary[:200]}"
        )
    elapsed = budget.get("elapsed_seconds")
    limit = budget.get("budget_seconds")
    if elapsed is not None and limit is not None:
        lines.append(f"- budget: {elapsed}s of {limit}s used.")
        # "Running so long" is a missing-data signal worth surfacing the
        # moment it is true (evt-go5z): a run past its soft budget is either
        # legitimately deep or quietly stuck, and the resident should see the
        # fact rather than have to compute it from two numbers.
        if budget.get("long_running"):
            lines.append(
                f"- running long: past the {limit}s soft budget — extend via "
                ".keepalive if the work needs it, else wind down."
            )
    replied_current = outbound.get("replies_current")
    any_delivery = (
        replied_current
        or outbound.get("replies_other")
        or outbound.get("outbound_messages")
    )
    if any_delivery:
        lines.append(
            f"- delivery so far: current={outbound.get('replies_current', 0)} "
            f"other={outbound.get('replies_other', 0)} "
            f"outbound={outbound.get('outbound_messages', 0)}."
        )
    # Produce is already attested by relics.py; the briefing only compresses
    # it. It rides hook deltas that are rendering for an existing reason and
    # is intentionally absent from the mid-run gate below, so committing work
    # cannot manufacture an injection by itself.
    produce_counts = (
        produce.get("counts") if isinstance(produce.get("counts"), dict) else {}
    )
    if produce.get("known") and any(
        int(count or 0) > 0 for count in produce_counts.values()
    ):
        parts: list[str] = []
        commit_count = int(produce_counts.get("commit", 0) or 0)
        if commit_count:
            commit_part = f"{commit_count} commit(s)"
            if produce.get("latest_commit"):
                commit_part += f" (latest {produce['latest_commit']})"
            parts.append(commit_part)
        branch_count = int(produce_counts.get("branch", 0) or 0)
        if branch_count:
            parts.append(
                f"branch {produce['branch']}" if produce.get("branch")
                else f"{branch_count} branch(es)"
            )
        pr_count = int(produce_counts.get("pr", 0) or 0)
        if pr_count:
            parts.append(
                f"PR #{produce['pr']}" if produce.get("pr") is not None
                else f"{pr_count} PR(s)"
            )
        for kind, label in (
            ("kb", "kb page"),
            ("issue", "issue"),
            ("comment", "comment"),
            ("message", "message"),
            ("file", "file"),
        ):
            count = int(produce_counts.get(kind, 0) or 0)
            if count:
                suffix = "" if count == 1 else "s"
                parts.append(f"{count} {label}{suffix}")
        if parts:
            lines.append("- produce: " + " · ".join(parts))
    # Affirmative-empty: an *addressed* run reaching closeout with nothing
    # communicated anywhere is suspicious, not silent — surface the absence at
    # the boundary, before the slot is gone. A warn, not a requirement: the
    # daemon dispatches the run's terminal stream to the waking thread on its
    # own (2026-07-16 ceremony cut), so the resident is never asked to
    # re-deliver through the outbox what its final message already carries —
    # that ask is what produced double-posts.
    #
    # Gated on ``inbound.current_event`` because the warning names a fact
    # about the waking thread. A scheduled wake has no current event to reply
    # to, so on those runs the sentence is not merely noisy — it is false. A
    # guard may only assert something the run can be proven wrong about; the
    # moment it nags about a chore that does not apply, it teaches the reader
    # to skip the channel, and it is gone the one night it is right.
    if stop and inbound.get("current_event"):
        if not any_delivery:
            lines.append(
                "- delivery: nothing communicated on any thread yet — the "
                "daemon dispatches your final message to the waking thread "
                "when this run ends, so end on the reply itself (no outbox "
                "re-delivery needed). A run that ends silent everywhere is "
                "surfaced as a failure."
            )
        elif not replied_current:
            lines.append(
                "- delivery: the waking thread itself has no reply yet — "
                "your final message will be dispatched there by the daemon; "
                "end on the reply, not on scratch."
            )
    # SCM posture is a boundary signal (seed / stop only): the commit/push
    # reminder a wake about to end needs. Rendered only when there is
    # something to act on — unpushed commits or modified files — so a clean
    # tree stays quiet. ``known`` is False when no worktree was inspected.
    if (seed or stop) and scm.get("known"):
        unpushed = int(scm.get("unpushed_commits", 0) or 0)
        modified = int(scm.get("modified_files", 0) or 0)
        if unpushed or modified:
            branch = scm.get("branch") or "-"
            lines.append(
                f"- scm: {unpushed} commit(s) not pushed, "
                f"{modified} modified file(s) on {branch} — commit and let "
                "the branch publish before ending."
            )
    # Task-classification presence (stop only): unlike the card, there is
    # nothing wrong with it being unwritten mid-run — the file legitimately
    # gets written anytime before closeout — so this renders only at the
    # boundary where "still missing" actually means something. A
    # card-staleness-style forcing function requested directly
    # (2026-07-07/08) after a run caught itself nearly shipping without it:
    # the miss is silent otherwise — no error, just a `run_ledger` row whose
    # `task_classification` stays null forever, the one field the whole
    # cost-rollup workstream joins on.
    task_cls = (
        payload.get("task_classification")
        if isinstance(payload.get("task_classification"), dict) else {}
    )
    if stop and not task_cls.get("written"):
        lines.append(
            "- .task-classification: not written yet — a short slug (e.g. "
            "`bugfix`, `kb-brainstorm`) before this ends, or this run's "
            "run_ledger row has a null task_classification forever."
        )
    # Card staleness (all phases): the note is the one live surface a
    # watching user sees between replies, so its own silence needs the same
    # "this is attention-worthy" framing pending events got 2026-07-05 — a
    # maintainer-set bar (240s) rather than a bare data point. Renders at
    # every boundary (unlike SCM's seed/stop-only gate) because the failure
    # this guards — a long stretch with no card update — is exactly the
    # mid-run gap that framing fix was built for; catching it only at
    # closeout would be too late to matter.
    card_stale = bool(card.get("stale"))
    if card_stale:
        age = card.get("age_seconds")
        age_txt = f"{age}s" if age is not None else "a while"
        lines.append(
            f"- card: no change in {age_txt} — rewrite .card (even one "
            "line) so the surface the user is watching isn't sitting blank "
            "or stale."
        )
    # Work-status posture (cost / quota / parallelism). Known fields carry
    # their value; not-yet-built ones read as named states with reasons so the
    # resident sees the slot honestly rather than a gap. It renders on seed /
    # stop, and on post-tool updates whenever portal-state changed enough to be
    # injected at all — live quota is a wall, not a footer-only nicety.
    rendered_resources = None
    if resources:
        rendered = _format_resources(resources)
        if rendered:
            rendered_resources = rendered
            lines.append(rendered)
    # Mid-run, a bare header with no pending work and no movement isn't worth
    # a turn. Seed and stop always render: their empty state ("0 pending") is
    # the affirmative signal, not noise.
    if (
        not seed and not stop and pending == 0 and pending_files == 0
        and not any_delivery and not rendered_resources and not card_stale
    ):
        return None
    return "\n".join(lines)


def _format_resources(resources: dict[str, Any]) -> str | None:
    """One 'work status' line: the schema's facets, in order.

    Delegates to :func:`facets.render_line` so the woven line and the JSON
    snapshot project from the same facet schema (``kb/design-resident-boundary``
    §1 — "by schema, not by convention"). Three-state honesty: ``known`` carries
    its value; ``absent`` names what is genuinely empty; ``unimplemented`` names
    a not-yet-built collector, with the reason riding along so the resident sees
    *why* a slot is empty without opening the JSON.
    """
    return facets.render_line(resources)


# ── The closeout grammar (`next_move`) ───────────────────────────────────
#
# The product owns this definition and the bench imports it. That direction is
# not incidental: a probe that carries its *own* idea of what a closeout looks
# like measures something the product does not enforce, and the two drift on the
# first day someone tightens one of them. `bench.probe_next_move` reads
# `closeout_state`; so does the guard. One grammar, one place, or the experiment
# is measuring a different contract than the one that ships.

_NEXT_MOVE_RE = re.compile(
    r"(?:^|\n)\s*(?:\*\*)?(done|continuing|blocked)(?:\*\*)?\s*(?:—|–|-|:)",
    re.IGNORECASE,
)
_OPTIONS_RE = re.compile(r"(?:^|\n)\s*1[.)]\s+\S.*(?:\n\s*2[.)]\s+\S)", re.DOTALL)

CLOSEOUT_TAIL = 800
"""How much of the reply's end counts as "the closeout". The contract says the
reply *ends* with the next move; a `done —` in paragraph two is not a closeout."""


def closeout_state(reply: str) -> str | None:
    """The next-move state a reply ends on — ``done`` / ``continuing`` /
    ``blocked`` / ``fork`` — or ``None`` if it ends on none of them.

    Reads **the reply**, which is the artifact the contract is about. Not the
    outbox, not the card, not a self-report: the bytes the user will actually
    read. (Claude's ``Stop`` payload hands this over as ``last_assistant_message``
    — see :func:`_armed_next_move_block`.)
    """
    tail = reply[-CLOSEOUT_TAIL:]
    match = _NEXT_MOVE_RE.search(tail)
    if match:
        return match.group(1).lower()
    if _OPTIONS_RE.search(tail):
        return "fork"
    return None


def _closeout_artifact_written(ctx: "HookContext", filename: str) -> bool:
    """True if control file *filename* exists with non-whitespace content.

    Read fresh from disk at Stop, not from the heartbeat portal snapshot — a
    file written in the run's final action must count. When the run has no
    outbox dir to check (ad-hoc / editor sessions), the obligation is
    unassertable, so it reads as satisfied: silent without the artifact, never
    a nag on a proxy.
    """
    if ctx.outbox_dir is None:
        return True
    try:
        return (ctx.outbox_dir / filename).read_text(encoding="utf-8").strip() != ""
    except OSError:
        return False


def _git_out(repo: Path, args: list[str], timeout: int = 10) -> str | None:
    """Read-only ``git`` call in *repo*; ``None`` on any failure.

    Best-effort like every other closeout reader: a missing repo, an unknown
    ref, or a timeout degrades to "unassertable", never a crash or a false
    block.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _scm_pr_number(outbox_dir: Path | None) -> str | None:
    """The PR number from the ``.pr`` control file, or ``None`` — same
    tolerant parse as ``relics._read_pr_control`` / ``daemon._read_pr_control``,
    re-implemented locally to keep ``hooks`` import-cycle-free."""
    if outbox_dir is None:
        return None
    try:
        text = (outbox_dir / ".pr").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    match = re.search(r"(\d+)\s*$", text)
    return match.group(1) if match else None


def _scm_forge_handoff_written(outbox_dir: Path | None) -> bool:
    """True when the portal broker durably accepted ``gate: forge``.

    The marker is written synchronously during the Stop flush handshake. It
    proves intent was handed to the gate abstraction without pretending the
    asynchronous forge call has already created a PR.
    """
    if outbox_dir is None:
        return False
    try:
        return (
            outbox_dir / FORGE_HANDOFF_NAME
        ).read_text(encoding="utf-8").strip() != ""
    except OSError:
        return False


def _scm_closeout_clause(ctx: "HookContext") -> str | None:
    """The `scm` closeout obligation: a work-loss block, read fresh at Stop.

    Armed only on the ``host`` environment (the daemon sets ``BRR_REPO_DIR``),
    where nothing publishes the end branch for the resident. Blocks on three
    states that are artifact-provable at Stop:

    - **uncommitted** modified files — a host checkout publishes nothing you
      don't commit; and
    - **unpushed** commits — committed but stranded on the machine, since host
      finalization is a no-op; and
    - **missing forge handoff** for commits beyond the seed — neither a real
      ``.pr`` nor the broker's durable ``.forge-handoff`` acceptance receipt.

    The clause carries the full receipt the maintainer asked for — ``N
    commit(s) +x/−y on <branch>``, plus ``PR #n`` when a ``.pr`` handle exists
    — so the block hands back produce, not a scold.

    The third condition became sound when the runner boundary started waiting
    for portal acceptance: a final ``gate: forge`` file is promoted before the
    Stop hook continues, and promotion writes ``.forge-handoff``. Absence now
    means absence of both actual PR and accepted intent, not merely "the daemon
    has not reached its post-return drain yet."
    """
    repo = ctx.repo_dir
    if repo is None or not repo.exists():
        return None
    status = _git_out(repo, ["status", "--porcelain"])
    if status is None:
        # Not a git repo / unreadable → the obligation is unassertable. Silent,
        # never a nag on a proxy — the guard doctrine this whole module keeps.
        return None
    modified = sum(1 for line in status.splitlines() if line.strip())

    branch = (_git_out(repo, ["rev-parse", "--abbrev-ref", "HEAD"]) or "").strip() or "-"

    # Commits on this branch beyond the seed ref, and their diffstat — the
    # receipt body. merge-base handles a seed that has since moved on.
    seed = ctx.seed_ref or "HEAD"
    base = seed
    merge_base = _git_out(repo, ["merge-base", seed, "HEAD"])
    if merge_base and merge_base.strip():
        base = merge_base.strip()
    commits = 0
    count = _git_out(repo, ["rev-list", "--count", f"{base}..HEAD"])
    if count and count.strip().isdigit():
        commits = int(count.strip())
    insertions = deletions = 0
    if commits:
        numstat = _git_out(repo, ["diff", "--numstat", f"{base}..HEAD"])
        if numstat:
            for row in numstat.splitlines():
                parts = row.split("\t")
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    insertions += int(parts[0])
                    deletions += int(parts[1])

    # Unpushed: commits ahead of the branch's upstream. No upstream on a
    # freshly-branched host run ⇒ every commit-beyond-seed is unpushed.
    unpushed = commits
    ahead = _git_out(repo, ["rev-list", "--count", "@{upstream}..HEAD"])
    if ahead is not None and ahead.strip().isdigit():
        unpushed = int(ahead.strip())

    pr = _scm_pr_number(ctx.outbox_dir)
    forge_handoff = _scm_forge_handoff_written(ctx.outbox_dir)
    missing_handoff = commits > 0 and not pr and not forge_handoff
    if modified == 0 and unpushed == 0 and not missing_handoff:
        return None

    receipt = ""
    if commits:
        receipt = f"{commits} commit(s) +{insertions}/−{deletions} on {branch}"
        if pr:
            receipt += f", PR #{pr}"
    gaps: list[str] = []
    if unpushed:
        gaps.append(f"{unpushed} not pushed")
    if modified:
        gaps.append(f"{modified} file(s) uncommitted")
    if missing_handoff:
        gaps.append("no PR or accepted `gate: forge` handoff")
    detail = "; ".join(p for p in (receipt, ", ".join(gaps)) if p)
    return (
        f"the work isn't landed — {detail}. A host checkout publishes nothing "
        "on its own; commit, push, and hand off the branch (`gate: forge`) "
        "before ending"
    )


def _render_closeout_capsule(unmet: list[str]) -> str:
    """One differential capsule naming every unmet obligation — the closeout
    twin of the SessionStart capsule, listing what is still open rather than
    restating what is always true."""
    if len(unmet) == 1:
        return f"Before this run ends: {unmet[0]}. Then stop — don't restate the reply."
    body = "\n".join(f"- {u}" for u in unmet)
    return (
        "Before this run ends, the closeout is unfinished:\n"
        f"{body}\n"
        "Address each, then stop — don't restate the reply."
    )


def _armed_closeout_block(
    ctx: "HookContext", payload: dict[str, Any], state: dict[str, Any]
) -> str | None:
    """The closeout guard: block once when Stop is reached with a named
    obligation still unmet, listing every unmet one in a single capsule.

    **Why this is a hook and not a sentence in the prompt.** The closeout
    contract is stated plainly in ``daemon-substrate.md`` and a weak core
    ignored it in *every arm of every round* of the drift bench — mounted and
    prose alike, 0/6. Position could not fix it, because position was never the
    problem: the contract is read at wake and spent 60 turns later, at the one
    moment the model is busy ending. This is the playbook's escalation rung — a
    contract prose cannot keep goes to *code that cannot fail silently* — and it
    is the point-of-use answer to "make the final obligation dead simple": the
    weak core no longer carries N obligations across 60 turns, it answers one
    imperative delivered the instant a miss is checkable.

    **Every obligation obeys the guard doctrine: assert only what an artifact
    proves.** next-move reads the reply (``last_assistant_message``); the file
    obligations read their control file fresh. An obligation whose artifact
    cannot be read is silent — never a nag on a proxy, the bug class this repo
    spent the week killing.

    Fires at most once per run (``closeout_blocked``): a second block on a run
    already asked to fix its closeout is the #282 loop. So the unmet set is
    gathered into one message, not chained across Stop fires.
    """
    if state.get("closeout_blocked"):
        return None
    # The Shell's own loop-breaker. If a stop hook already blocked this turn,
    # never stack another.
    if payload.get("stop_hook_active"):
        return None

    unmet: list[str] = []

    if ctx.next_move_guard:
        reply = payload.get("last_assistant_message")
        # Assertable only when the Shell handed the reply over (codex: none).
        if isinstance(reply, str) and reply.strip() and closeout_state(reply) is None:
            unmet.append(
                "your reply ends on nothing — close with where the loop stands "
                "(`done — <receipt>`, `continuing — <next>`, `blocked — "
                "<needed>`, or a 2-4 option fork + your recommendation, last)"
            )

    for name in _CLOSEOUT_ARTIFACT_ORDER:
        if name in ctx.closeout_obligations:
            filename, clause = _CLOSEOUT_ARTIFACTS[name]
            if not _closeout_artifact_written(ctx, filename):
                unmet.append(clause)

    # SCM is not a file-existence check but a fresh-git computation, so it
    # lives outside the artifact loop. Last in the capsule: the reply-shape and
    # the control files come first, the land-the-work imperative closes it.
    if "scm" in ctx.closeout_obligations:
        scm_clause = _scm_closeout_clause(ctx)
        if scm_clause:
            unmet.append(scm_clause)

    if not unmet:
        return None

    state["closeout_blocked"] = True
    return _render_closeout_capsule(unmet)


# ── Stop fold-in (verbatim, framed as the user's words) ──────────────────


def _first_pending_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The first foldable pending event (one carrying a body), or None."""
    inbound = payload.get("inbound") if isinstance(payload.get("inbound"), dict) else {}
    events = inbound.get("events") if isinstance(inbound.get("events"), list) else []
    for ev in events:
        if isinstance(ev, dict) and str(ev.get("body") or "").strip():
            return ev
    return None


def _fold_in_message(event: dict[str, Any]) -> str:
    """Frame a newly-arrived event as the user's own relayed words.

    The 2026-06-26 spike found framing is load-bearing: a coercive daemon
    interrupt is perceived but *refused* (correct injection defense), while the
    same content relayed as the user's genuine words is acted on. So the Stop
    block carries the event **body verbatim** under a neutral, non-imperative
    relay header — not an operational summary.
    """
    source = str(event.get("source") or "user").strip() or "user"
    body = str(event.get("body") or "").strip()
    return f"(folded-in follow-up from the user via {source}:)\n\n{body}"


# ── Phase logic (neutral result) ─────────────────────────────────────────


def compute_neutral(
    phase: str, ctx: HookContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Run one phase: side effects (flush signal, state) + neutral result.

    Returns ``{"inject": str|None, "block": bool, "block_reason": str|None}``.
    The phases:

    - ``post-tool`` — flush signal + change-token-gated injection.
    - ``stop`` — flush signal + injection + premature-stop control (block
      once when foldable input is still pending).
    - ``session-start`` — seed the run with the full portal capsule.
    """
    # Flush before reading the portal. On a managed Tier-2 run this is a
    # handshake, so the snapshot below includes the delivery acceptance and a
    # Stop decision observes durable forge intent written at the same boundary.
    if phase in (PHASE_POST_TOOL, PHASE_STOP):
        _touch_flush(ctx)
    portal = _read_json(ctx.portal_state_path)
    state = _read_hook_state(ctx)
    _record_fired(state, phase)
    inject: str | None = None
    block = False
    block_reason: str | None = None

    if phase == PHASE_SESSION_START:
        inject = format_delta(portal, seed=True)
        state["last_token"] = portal.get("change_token")
    elif phase == PHASE_STOP:
        # The closeout boundary renders unconditionally *once per distinct
        # portal snapshot* (gated on ``stop_last_token``, a Stop-scoped twin
        # of post-tool's ``last_token`` so the two gates never fight): the
        # affirmative "0 pending" signal and the SCM commit/push reminder
        # must land at least once even when nothing moved since the last
        # post-tool tick, satisfying the original "explicit all-clear, not
        # silence" intent. What it must not do is re-render the identical
        # text on every subsequent Stop fire once the runner has already
        # seen it — #282: a stuck-clean run (0 pending, token unchanged)
        # kept getting non-empty ``additionalContext`` on every Stop fire,
        # which reads to the CLI as "there's still something to weave in"
        # and drove 10-15+ pointless re-fires burning budget on a run that
        # had nothing left to do. An unchanged token means the runner
        # already has this exact text in-context from the prior Stop; a
        # bare ``{}`` result is the actual "nothing to add, stop cleanly"
        # signal.
        stop_token = portal.get("change_token")
        if stop_token != state.get("stop_last_token"):
            inject = format_delta(portal, stop=True)
        state["stop_last_token"] = stop_token
        state["last_token"] = stop_token
    else:
        token = portal.get("change_token")
        if token is not None and token != state.get("last_token"):
            inject = format_delta(portal)
            state["last_token"] = token

    if phase == PHASE_STOP:
        attention = (
            portal.get("attention")
            if isinstance(portal.get("attention"), dict) else {}
        )
        pending = int(attention.get("pending_event_count", 0) or 0)
        # Token-scoped, not a one-shot boolean: a plain "blocked once ever"
        # latch (the pre-fix shape) never let a *later*, genuinely new
        # follow-up re-block once the run had folded in any earlier one —
        # every pending event after the first silently rode along as inert
        # context instead of forcing the resident to address it before
        # exiting, which is exactly the "quick follow-up before the run
        # closes" contract this hook exists to keep. Re-arming on a token
        # change (a new/changed pending event) while still suppressing a
        # repeat block against the *same* unresolved snapshot preserves the
        # existing "second stop must not block forever" guarantee for the
        # unchanged case.
        if pending > 0 and state.get("stop_blocked_token") != stop_token:
            block = True
            event = _first_pending_event(portal)
            if event is not None:
                # Fold the waiting follow-up in verbatim, as the user's words —
                # the resident addresses it in this same thought.
                block_reason = _fold_in_message(event)
            else:
                block_reason = (
                    f"{pending} pending event(s) are still waiting — fold the "
                    "foldable ones into this wake (read inbox.json) before "
                    "ending, or say why they should wait."
                )
            state["stop_blocked_token"] = stop_token

        # The closeout guard, second in line and deliberately so: a user's waiting
        # message outranks the shape of a reply that is about to be rewritten
        # anyway. Only when nothing is pending does "how does this reply end"
        # become the last question of the run.
        if not block:
            reason = _armed_closeout_block(ctx, payload, state)
            if reason is not None:
                block = True
                block_reason = reason

    _write_hook_state(ctx, state)
    return {"inject": inject, "block": block, "block_reason": block_reason}


# ── Native rendering (neutral → runner flavour) ──────────────────────────

# Post-tool boundary event name per flavour. Claude's ``PostToolBatch`` fires
# once after a batch of (possibly parallel) tool calls completes — the right
# seam (it sees every tool result before the next model call) and cheaper than
# per-tool ``PostToolUse``. Codex exposes ``PostToolUse`` only (no
# ``PostToolBatch`` in codex-cli 0.141.0). Both inject via
# ``hookSpecificOutput.additionalContext`` — fire-verified 2026-06-27 on Claude
# Code 2.1.191 (haiku) and codex-cli 0.141.0 (gpt-5.4-mini).
_POST_TOOL_EVENT = {"claude": "PostToolBatch", "codex": "PostToolUse"}


def native_event_name(flavour: str | None, phase: str) -> str:
    """The runner-native hook event name for *phase* under *flavour*."""
    if phase == PHASE_POST_TOOL:
        return _POST_TOOL_EVENT.get(flavour or "", "PostToolUse")
    if phase == PHASE_STOP:
        return "Stop"
    return "SessionStart"


def render_native(
    flavour: str | None, phase: str, neutral: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Render the neutral result into a runner flavour's native hook output.

    Returns ``(json_payload, exit_code)``. Mappings follow each runner's
    current hooks docs (see ``kb/design-runner-back-channel.md`` §Verification).
    An unknown flavour gets the neutral shape verbatim (exit 0) so a custom
    runner can adopt the protocol directly.
    """
    inject = neutral.get("inject")
    block = bool(neutral.get("block"))
    reason = neutral.get("block_reason")

    if flavour in ("claude", "codex"):
        # Both Claude and Codex accept the same ``hookSpecificOutput``
        # injection envelope (fire-verified). They diverge only on stop-control:
        # Claude blocks a premature stop with ``decision: block`` (continues the
        # turn, verified); Codex uses the documented ``continue: false`` /
        # ``stopReason`` shape.
        event_name = native_event_name(flavour, phase)
        out: dict[str, Any] = {}
        if block:
            if flavour == "claude":
                out["decision"] = "block"
                if reason:
                    out["reason"] = reason
            else:  # codex
                out["continue"] = False
                if reason:
                    out["stopReason"] = reason
        if inject:
            out["hookSpecificOutput"] = {
                "hookEventName": event_name,
                "additionalContext": inject,
            }
        return out, 0

    if flavour == "gemini":
        out = {}
        if inject:
            # Injection field for gemini's AfterTool/SessionStart — the exact
            # schema lives in gemini's hooks *reference* page; `additionalContext`
            # is the working name pending that pin (see design §Resolutions).
            out["additionalContext"] = inject
        if block:
            # gemini blocks with `decision: "deny"` + exit 2.
            out["decision"] = "deny"
            if reason:
                out["reason"] = reason
            return out, 2
        return out, 0

    # Unknown / custom runner: hand back the neutral envelope unchanged.
    return {
        "inject": inject,
        "block": block,
        "block_reason": reason,
    }, 0


# ── Config generation (brr-managed, per-run) ─────────────────────────────
#
# brr generates the runner's *native* hook config each run so the user never
# hand-writes it. Two install mechanisms, by flavour:
#   - **claude** — a settings file written into the run's working directory
#     (``.claude/settings.local.json``), so it disappears with the worktree and
#     never touches the user's global config. Gated by :func:`hook_capability`.
#   - **codex** — config-override argv (``-c hooks.<Event>=[…]``) injected into
#     the runner command, because the project-level ``.codex/config.toml``
#     install hung under codex's repo-trust gate (2026-06-27). Paired with the
#     ``--dangerously-bypass-hook-trust`` flag carried by the profile cmd.
# A runner is only treated as hooks-capable after a runtime precheck confirms
# the prerequisites — the profile's ``hooks:`` field is the *intent*, the
# precheck is the *assertion* (kb/design-runner-back-channel.md §Resolutions).

# Flavours brr writes a native hook *settings file* for. Codex installs via
# argv (:func:`codex_hook_args`); gemini's emitter is a follow-up, so it
# degrades to Tier 0/1 until that exists.
_FILE_CONFIG_FLAVOURS = {"claude"}


def hook_config_supported(flavour: str | None) -> bool:
    """True when brr writes a native hook *settings file* for *flavour*.

    Codex is hooks-capable but installs via argv, not a file — see
    :func:`codex_hook_args` — so it is deliberately excluded here.
    """
    return bool(flavour) and flavour in _FILE_CONFIG_FLAVOURS


def hook_command(phase: str, brr_bin: str = "brnrd") -> str:
    """The shell command a native hook runs for *phase*."""
    return f"{brr_bin} hook {phase}"


def _claude_hook_settings(brr_bin: str) -> dict[str, Any]:
    def _entry(phase: str) -> dict[str, Any]:
        return {"hooks": [{"type": "command", "command": hook_command(phase, brr_bin)}]}

    # PostToolBatch (not PostToolUse): one injection per tool batch, after every
    # result lands — see ``_POST_TOOL_EVENT``. Claude ``statusLine`` is a TUI
    # footer and does not fire under the daemon's ``claude --print`` mode, so
    # brr does not register it here; terminal spend/context accounting comes
    # from the result JSON instead.
    return {
        "hooks": {
            native_event_name("claude", PHASE_POST_TOOL): [_entry(PHASE_POST_TOOL)],
            "Stop": [_entry(PHASE_STOP)],
            "SessionStart": [_entry(PHASE_SESSION_START)],
        },
    }


def codex_hook_capability(*, brr_bin: str = "brnrd") -> bool:
    """Runtime precheck for codex's argv-injected hooks: brnrd on PATH.

    Codex needs no writable config file (the config rides on the runner argv),
    so the only prerequisite is that the ``brnrd hook`` endpoint each hook
    command invokes is resolvable.
    """
    return shutil.which(brr_bin) is not None


def codex_hook_args(brr_bin: str = "brnrd") -> list[str]:
    """Argv tokens that install codex's native hook config inline.

    Returns ``-c hooks.<Event>=[…]`` overrides for each phase, to append to a
    ``codex exec`` command (the profile cmd carries
    ``--dangerously-bypass-hook-trust``). Each override is one argv token, so
    the embedded command string's spaces survive without shell quoting. The
    matcher field is deliberately omitted: current Codex docs define omitted
    matcher as "match every occurrence" for supported events. Codex exposes
    ``PostToolUse`` / ``Stop`` / ``SessionStart``; fire-verified ``PostToolUse``
    + ``additionalContext`` injection on codex-cli 0.141.0.
    """
    def _override(event: str, phase: str) -> str:
        cmd = hook_command(phase, brr_bin)
        return f'hooks.{event}=[{{hooks=[{{type="command",command="{cmd}"}}]}}]'

    args: list[str] = []
    for event, phase in (
        ("PostToolUse", PHASE_POST_TOOL),
        ("Stop", PHASE_STOP),
        ("SessionStart", PHASE_SESSION_START),
    ):
        args.extend(["-c", _override(event, phase)])
    return args


def hook_capability(
    flavour: str | None, cwd: Path | None, *, brr_bin: str = "brnrd"
) -> bool:
    """Runtime precheck: is this run actually hooks-capable?

    Asserts (not assumes) the per-runner prerequisites: brr can emit config
    for the flavour, the brnrd endpoint is invocable on PATH, and the run cwd
    is a writable place to drop the native config. Returns False — degrade
    cleanly to the heartbeat-polled model — when any prerequisite is missing.
    """
    if not hook_config_supported(flavour):
        return False
    if cwd is None or not Path(cwd).is_dir():
        return False
    if shutil.which(brr_bin) is None:
        return False
    return os.access(cwd, os.W_OK)


def install_hook_config(
    flavour: str | None, cwd: Path, *, brr_bin: str = "brnrd"
) -> Path | None:
    """Write *flavour*'s native per-run hook config into *cwd*.

    For claude this is ``<cwd>/.claude/settings.local.json`` — the local
    project overlay that layers on top of any committed ``settings.json``
    and is conventionally gitignored, so brr's generated hooks coexist with
    user settings rather than clobbering them. Merges into an existing local
    overlay (user keys win except for the ``hooks`` block brr owns). Returns
    the written path, or None when the flavour is unsupported.
    """
    if flavour != "claude":
        return None
    settings_dir = cwd / ".claude"
    settings_path = settings_dir / "settings.local.json"
    existing: dict[str, Any] = _read_json(settings_path)
    generated = _claude_hook_settings(brr_bin)
    # brr's generated keys are *defaults*; user keys in the local overlay layer
    # on top and win — except the ``hooks`` block, which brr owns and force-
    # merges. So a user's own footer or local settings are preserved while
    # brr's lifecycle hooks always install.
    merged = {**generated, **existing}
    merged["hooks"] = {**existing.get("hooks", {}), **generated["hooks"]}
    try:
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        return None
    return settings_path


# ── Entry point ──────────────────────────────────────────────────────────


def run_hook(
    phase: str, stdin_text: str, env: dict[str, str]
) -> tuple[dict[str, Any], int]:
    """Execute one hook phase end to end.

    *stdin_text* is the runner's native hook payload. Run *context* still comes
    from the env handles — but the payload is no longer inert: claude's ``Stop``
    event carries ``last_assistant_message``, the reply itself, which is the one
    artifact the closeout guard is allowed to judge (:func:`_armed_next_move_block`).
    It went unread until 2026-07-14; the guard that needed it was being written
    while the Shell was already handing it over.
    Returns ``(native_json, exit_code)``. Unknown phases are a no-op success
    so a runner mapping an extra native hook onto brr never hard-fails.
    """
    if phase not in PHASES:
        return {}, 0
    ctx = HookContext(env)
    neutral = compute_neutral(phase, ctx, _safe_json(stdin_text))
    return render_native(ctx.flavour, phase, neutral)


def _safe_json(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main(phase: str) -> int:
    """CLI shim: read stdin, run the phase, print native JSON, exit code."""
    import sys

    stdin_text = ""
    try:
        stdin_text = sys.stdin.read()
    except (OSError, ValueError):
        stdin_text = ""
    payload, code = run_hook(phase, stdin_text, dict(os.environ))
    sys.stdout.write(json.dumps(payload))
    return code
