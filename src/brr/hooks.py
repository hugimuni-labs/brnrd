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
import hashlib
import json
import os
import re
import shutil
import subprocess
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import card as card_rule
from . import facets
from . import gate_receipt
from . import portals
from . import promises
from . import relics

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
# The last context block the runner proved it received. The change token is a
# broad portal-snapshot key; heartbeat-only fields can move it while the
# rendered context stays byte-identical. Keep a content-derived fingerprint
# *and* the exact text: the digest makes the key explicit, while the text
# comparison keeps the suppression pessimistic even under a collision. No
# block-name allowlist exists here, so a future renderer gets the same rule.
LAST_INJECT_KEY = "last_inject"
# A render is not yet proof the runner saw it: a crash can land between the
# state write and stdout. The next lifecycle hook is the acknowledgement.
PENDING_INJECT_KEY = "pending_inject"
# The boundary transcript: one JSON line per hook fire, appended beside the
# wake's own `prompt.md` in the run directory. The wake is captured and the
# boundaries were not — but they are the *same channel*: a run's context is
# the boot prompt plus every injection the hooks made after it, and reading
# only the first half gives a false picture of what the runner actually saw.
# Written from the run directory the daemon already names (`BRR_BOOT_SCORE`'s
# parent), so no new env var and no daemon restart are needed to arm it.
BOUNDARIES_NAME = "boundaries.jsonl"
# A bound, not a budget: the transcript is diagnostic, and a runaway run must
# not be able to fill a disk with its own boundaries. Past the cap the file
# stops growing and says so on its last line, because a transcript that
# silently stops is indistinguishable from a run that went quiet.
_BOUNDARIES_MAX_BYTES = 4_000_000
# The gate-less routing fact (#728) is true for the whole life of a gate-less
# run and can never be cleared, so it is said once and then remembered here —
# the one line in the closeout briefing that is a constant rather than an
# obligation. Everything else there (scm, card staleness, pending events) goes
# quiet when the resident acts, and so re-renders freely.
GATELESS_ROUTING_KEY = "gateless_routing_noted"

# Closeout artifact obligations the armed guard can escalate from the soft
# `inject` mention (see `format_delta`, which already surfaces a stale card
# / unpushed SCM as additionalContext) to a hard `block`. Each maps to a
# control file the resident owes by closeout. The check reads the *file*, fresh, at Stop — never the
# heartbeat portal snapshot, which can predate a control file written in the
# run's final action. That is the same "assert only from THE artifact"
# doctrine the next-move guard keeps, and why escalation lives here rather
# than promoting the portal-derived `inject` lines in place.
CARD_NAME = ".card"
FORGE_HANDOFF_NAME = ".forge-handoff"
# The local CI-gate receipt: written by whatever command the repo declares as
# its gate (`hooks.gate_command`), read by `_gate_closeout_clause`. Either the
# repo writes its own (this repo's `scripts/gate.py`) or `brnrd gate-run`
# (`brr.gate_receipt`) wraps `hooks.gate_command` and writes it generically —
# brr still owns no opinion about what the command *is*, only that a run of
# it leaves this file. Single source of truth: `brr.gate_receipt.RECEIPT_NAME`.
GATE_RECEIPT_NAME = gate_receipt.RECEIPT_NAME
# Resident-authored mood glyph/name (#566 layer 2 — the daemon-derived mood
# is computed elsewhere; this is the resident's own meta-channel). A control
# dotfile beside `.card`, same idiom as `.keepalive`/`.pr`: never delivered,
# read fresh at every boundary. First line only — see `_read_mood`.
MOOD_NAME = ".mood"
# The run body rides the closeout delta whole. Capped only against a
# pathological card: this is the resident's own prose, and truncating it is a
# worse failure than the tokens it costs at a once-per-run boundary.
_STOP_BODY_MAX_CHARS = 6000
# The closeout produce manifest. Generous — a run that made 40 things
# should see them — but bounded, because a runaway `.relics.jsonl` must
# not be able to flood the one boundary the resident reads most carefully.
_STOP_MANIFEST_MAX_RECORDS = 40

#: How many notice texts the seed/stop briefing spells out, newest last, and
#: how much of each. Bounded because a notices list is unbounded and a
#: boundary line the reader scrolls past is a line the reader stops reading;
#: the overflow is counted, so the cap can never read as "that was all of
#: them". The daemon already truncates the stored list, so this is a second
#: bound on a bounded thing rather than the only one.
_NOTICE_LINES = 4
_NOTICE_TEXT_CAP = 220

_CLOSEOUT_ARTIFACT_ORDER = ("card",)
_CLOSEOUT_ARTIFACTS = {
    "card": (
        CARD_NAME,
        "no `.card` was written — put one line on the progress surface the "
        "user watches between replies",
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
        # Whether `gate: forge` is deliverable on this account, armed by the
        # daemon from the same `_gate_can_deliver` probe the router itself
        # uses (`daemon._runner_runtime`). This hook has no way to probe gate
        # config on its own — only the runner env — so the `scm` closeout
        # clause may name the `gate: forge` escape route only when this is
        # set; absent (an older daemon, an ad-hoc hook run) reads as off,
        # never as "assume it's there."
        self.forge_gate = (
            env.get("BRR_FORGE_GATE") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        # The command this repo calls its CI gate, verbatim from `.brr/config`
        # (`hooks.gate_command`) via the daemon. brr owns no opinion about what
        # a project's gate is — it only knows the name to say back when the
        # receipt is missing. Unset ⇒ the `gate` obligation is unassertable and
        # stays silent, the same doctrine as `BRR_REPO_DIR`.
        self.gate_command = (env.get("BRR_GATE_COMMAND") or "").strip() or None
        self.flush_sync = (
            env.get("BRR_FLUSH_SYNC") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        portal = env.get("BRR_PORTAL_STATE")
        self.portal_state_path = Path(portal) if portal else None
        # The wake's persisted BootScore (`boot-score.json`), armed by the
        # daemon — the orientation ledger (#513 Slice 9) reads its
        # ``orientation_set`` from here. Absent (an older daemon, an ad-hoc
        # hook run) ⇒ the ledger is unassertable and stays silent — the same
        # "never a nag on a proxy" doctrine as `BRR_REPO_DIR`.
        boot_score = env.get("BRR_BOOT_SCORE")
        self.boot_score_path = Path(boot_score) if boot_score else None
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

    @property
    def run_dir(self) -> Path | None:
        """The daemon's per-run directory — where `prompt.md` already lives.

        Derived from ``BRR_BOOT_SCORE`` rather than taking an env var of its
        own: the daemon writes ``<brr>/runs/<run-id>/boot-score.json`` and
        arms that path already, so the wake capture and the boundary
        transcript land in one directory without a daemon change. Absent
        (an older daemon, an ad-hoc hook run) ⇒ nothing is recorded, the same
        "unassertable stays silent" doctrine the other optional handles keep.
        """
        if self.boot_score_path is None:
            return None
        return self.boot_score_path.parent


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_card_body(ctx: HookContext) -> str | None:
    """Read the resident's `.card` fresh, for the closeout delta.

    ``None`` when there is no outbox to read from, so the caller can fall back
    to the portal snapshot rather than assert an empty body.
    """
    if ctx.outbox_dir is None:
        return None
    try:
        return (ctx.outbox_dir / CARD_NAME).read_text(encoding="utf-8")
    except OSError:
        return None


# Defensive read cap on `.mood` I/O: free-authored text with no size
# contract, so a runaway echo or accidental paste must never be able to
# bloat a hook boundary or crash rendering. Far larger than the rendered
# chip's own truncation (`_MOOD_DISPLAY_MAX_CHARS`) — this one only bounds
# the read itself.
_MOOD_READ_CAP_CHARS = 500


def _read_mood(ctx: HookContext) -> str | None:
    """Read the resident's `.mood` control file fresh, first line only.

    Same "read the artifact, not a cached copy" doctrine :func:`_read_card_body`
    keeps: the resident may rewrite it between boundaries, and the point of
    the channel (#566) is that the face the user sees and the face the
    resident knows it is wearing are the same object. Defensive by
    construction — a missing file, an unreadable one, or a blank first line
    all fall through to ``None`` (no mood segment renders) rather than
    raising or reading the whole file into memory.
    """
    if ctx.outbox_dir is None:
        return None
    path = ctx.outbox_dir / MOOD_NAME
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline(_MOOD_READ_CAP_CHARS)
    except OSError:
        return None
    text = first_line.strip()
    return text or None


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


def _stop_is_gate_less(portal: dict[str, Any]) -> bool:
    """True when the closeout delivery block will speak as a gate-less run.

    The exact predicate ``format_delta``'s delivery block uses, kept here so
    the once-per-run latch (#728) and the line it gates cannot drift apart:
    a waking event must exist for the block to render at all, and
    ``current_event_replyable`` must be explicitly ``False`` — an *absent*
    key is an older or partial portal state, which falls back to the
    addressed-run shape rather than inventing a gate-less run.
    """
    inbound = portal.get("inbound") if isinstance(portal.get("inbound"), dict) else {}
    return bool(inbound.get("current_event")) and (
        inbound.get("current_event_replyable") is False
    )


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


def _ack_previous_inject(state: dict[str, Any], phase: str) -> None:
    """Promote the prior render only when this boundary proves delivery."""
    pending = state.pop(PENDING_INJECT_KEY, None)
    if phase == PHASE_SESSION_START:
        # A fresh runner did not necessarily see the prior process's stdout.
        # Reseed and relearn instead of suppressing across that uncertainty.
        state.pop(LAST_INJECT_KEY, None)
        return
    if (
        isinstance(pending, dict)
        and isinstance(pending.get("sha256"), str)
        and isinstance(pending.get("text"), str)
    ):
        state[LAST_INJECT_KEY] = pending


def _suppress_unchanged_inject(
    state: dict[str, Any], inject: str | None,
) -> str | None:
    """Return only context whose exact rendered content moved.

    Absence, malformed prior state, or any byte of change sends the complete
    block. A new render stays pending until the next hook proves the runner
    continued after receiving it. State-write failure therefore fails open:
    the next subprocess sees no receipt and resends. Dropping useful context
    is worse than paying for one uncertain repeat.
    """
    if inject is None:
        return None
    digest = hashlib.sha256(inject.encode("utf-8")).hexdigest()
    previous = state.get(LAST_INJECT_KEY)
    unchanged = (
        isinstance(previous, dict)
        and previous.get("sha256") == digest
        and previous.get("text") == inject
    )
    if unchanged:
        return None
    state[PENDING_INJECT_KEY] = {"sha256": digest, "text": inject}
    return inject


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


# ── The orientation ledger (#513 Slice 9) ────────────────────────────────
#
# The BootScore names a deterministic `orientation_set` — files this wake
# ought to have read (see `bootscore.OrientationFile`; NOT `orientation`,
# the kernel's next-actions list). The hook observes `Read` calls in the
# post-tool batch against that set and renders `orient x/y` as a bar segment
# until the walk completes or the resident declares the skip on `.card`.
# Observation always runs (it is Slice 4's instrument: orientation
# completeness vs obligation recall, per core class); only the *segment*
# is silenced by completion or skip.

ORIENTATION_READ_KEY = "orientation_read"
ORIENTATION_READ_RANGES_KEY = "orientation_read_ranges"

# Claude's Read tool returns at most 2,000 lines when ``limit`` is omitted.
# Treating an omitted limit as "the whole file" made a large orientation page
# complete on the first page, which is the same touch-vs-coverage bug as an
# explicit 90-line read. The hook records requested line ranges and only
# completes a file when their union covers it.
_ORIENTATION_READ_DEFAULT_LIMIT = 2000

# What counts as a declared skip. Two forms, both *declarations*: a terse
# `orient: skip` heading the line (list/quote/heading markers tolerated), or
# the canonical sentence. A first-class outcome, not a failure state.
#
# The first shape was `^(?=.*orient)(?=.*skip).*$` — line-scoped, which the
# comment claimed and the regex delivered. But **line-scoped is not
# declaration-scoped**, and the resident holds the pen on `.card`. Driven
# against realistic card prose, that form fired on:
#
#     "Reviewed Slice 9: skip is a first-class outcome for the orientation walk"
#     "orient 3/5 rendered; nothing skipped"        ← a NEGATION, silencing the meter
#
# A guard that a description of itself disables is not a guard, and the second
# line is the ledger *reporting its own value* and thereby turning itself off.
# So: narrow, and lean false-negative on purpose. A meter that renders when it
# should have hidden costs one segment; a meter that hides when the walk never
# happened costs the whole feature.
_ORIENT_SKIP_RE = re.compile(
    # `orient: skip` / `orientation = skip`, at the head of a line
    r"^[\s>*\-#]*orient(?:ation)?\s*[:=-]\s*skip\b"
    # …or the canonical sentence, specific enough to be intentional anywhere
    r"|assuming prior knowledge[,\s]+skipping orientation\b",
    re.IGNORECASE | re.MULTILINE,
)


def _orientation_set_paths(ctx: HookContext) -> list[str]:
    """The orientation set's resolved absolute paths, from `boot-score.json`.

    Read fresh from the artifact the daemon persisted (same doctrine as every
    other closeout reader); empty on any absence — no score armed, unreadable
    file, or a score without the field (an older daemon) — so every consumer
    degrades to "no ledger" rather than a guess.
    """
    if ctx.boot_score_path is None:
        return []
    score = _read_json(ctx.boot_score_path)
    raw = score.get("orientation_set")
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    for entry in raw:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            path = entry["path"].strip()
            if path:
                paths.append(os.path.realpath(path))
    return paths


def _observe_orientation_reads(
    payload: dict[str, Any], set_paths: list[str], state: dict[str, Any]
) -> int:
    """Fold this batch's `Read` coverage into the ledger; return completions.

    Claude's ``PostToolBatch`` hands calls over as ``{tool_name, tool_input,
    ...}``. A file is complete only when the union of its requested line
    ranges covers the file as it exists at this boundary. A partial first page,
    an explicit ``offset``/``limit`` slice, and a failed-to-page large file
    therefore stay open; adjacent paged reads can complete it.

    Completed paths remain in :data:`ORIENTATION_READ_KEY` for compatibility
    with the score's original per-run state. Incomplete ranges live under
    :data:`ORIENTATION_READ_RANGES_KEY`. Both are pruned to the current set so
    stale paths can never inflate the count. A runner whose payload carries no
    ``tool_calls`` still under-counts rather than guessing.
    """
    read = {
        p for p in (state.get(ORIENTATION_READ_KEY) or [])
        if isinstance(p, str) and p in set_paths
    }
    raw_ranges = state.get(ORIENTATION_READ_RANGES_KEY)
    ranges_by_path: dict[str, list[list[int]]] = {}
    if isinstance(raw_ranges, dict):
        for path, ranges in raw_ranges.items():
            if path not in set_paths or not isinstance(ranges, list):
                continue
            valid = [
                [item[0], item[1]]
                for item in ranges
                if (
                    isinstance(item, list)
                    and len(item) == 2
                    and all(isinstance(value, int) for value in item)
                    and 0 <= item[0] < item[1]
                )
            ]
            if valid:
                ranges_by_path[path] = valid

    def total_lines(path: str) -> int | None:
        try:
            body = Path(path).read_bytes()
        except OSError:
            return None
        if not body:
            return 0
        return body.count(b"\n") + (0 if body.endswith(b"\n") else 1)

    def add_range(path: str, start: int, end: int) -> None:
        merged: list[list[int]] = []
        for left, right in sorted([*ranges_by_path.get(path, []), [start, end]]):
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        ranges_by_path[path] = merged

    calls = payload.get("tool_calls")
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict) or call.get("tool_name") != "Read":
                continue
            tool_input = call.get("tool_input")
            if not isinstance(tool_input, dict):
                continue
            file_path = tool_input.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                continue
            resolved = os.path.realpath(file_path.strip())
            if resolved not in set_paths or resolved in read:
                continue
            line_count = total_lines(resolved)
            if line_count is None:
                continue
            offset = tool_input.get("offset", 0)
            limit = tool_input.get("limit", _ORIENTATION_READ_DEFAULT_LIMIT)
            if (
                not isinstance(offset, int)
                or isinstance(offset, bool)
                or offset < 0
                or not isinstance(limit, int)
                or isinstance(limit, bool)
                or limit <= 0
            ):
                continue
            end = min(line_count, offset + limit)
            if offset < end:
                add_range(resolved, offset, end)
            covered = ranges_by_path.get(resolved, [])
            if line_count == 0 or (
                covered and covered[0][0] == 0 and covered[0][1] >= line_count
            ):
                read.add(resolved)
    state[ORIENTATION_READ_KEY] = sorted(read)
    state[ORIENTATION_READ_RANGES_KEY] = {
        path: ranges
        for path, ranges in sorted(ranges_by_path.items())
        if path not in read
    }
    return len(read)


def _orientation_progress(
    ctx: HookContext, payload: dict[str, Any], state: dict[str, Any]
) -> tuple[int, int] | None:
    """The `orient x/y` segment's value, or ``None`` when it must not render.

    ``None`` on: no set (unassertable), walk complete (the meter *leaves* —
    a meter that never leaves trains skimming), or a skip declared on `.card`
    (first-class outcome). Observation into hook state happens regardless, so
    completeness stays measurable even for a wake that skipped.
    """
    set_paths = _orientation_set_paths(ctx)
    if not set_paths:
        return None
    count = _observe_orientation_reads(payload, set_paths, state)
    if count >= len(set_paths):
        return None
    card = _read_card_body(ctx)
    if card and _ORIENT_SKIP_RE.search(card):
        return None
    return count, len(set_paths)


# ── The wake census (#739, the reading half of #683) ─────────────────────
#
# `boot-score.json` has carried an owner-attributed per-block census since
# #683, and the one surface that never saw it was the wake it describes.
# Three fields, straight off the artifact the daemon already wrote before
# the runner started — projection, not measurement, so it cannot disagree
# with the score an operator reads later.
#
# Why these three (the maintainer's ask, 2026-07-25): a block that doubles
# shows up in `top` the boot it lands, and a block still carrying material
# from days ago shows up in `oldest` — which is exactly how #736's 12 KB
# dead turn would have announced itself four days before anyone asked.


def _fmt_kb(size: int) -> str:
    """Bytes as `24.7 KB`. One decimal: enough to see a block move, short
    enough to ride a bar segment."""
    return f"{size / 1024:.1f} KB"


def _wake_census(ctx: HookContext) -> str | None:
    """`wake 115.7 KB · top work-surface 24.7 KB · oldest 2026-07-25`.

    ``None`` on any absence — no score armed, unreadable file, a score with
    no `contracts` (an older daemon), or nothing measured. Same three-state
    discipline the score itself keeps: a missing measurement renders nothing
    rather than a zero, because "0 B" is a claim and absence is not.

    Each field degrades on its own. A score whose `prompt_bytes` is missing
    still renders `top` and `oldest`; a wake where no block carries an
    `oldest_item` renders the first two. The alternative — one absent field
    silencing the line — would hide the census exactly when the score is
    partial, which is when it is most worth reading.
    """
    score = _read_json(ctx.boot_score_path)
    contracts = score.get("contracts")
    if not isinstance(contracts, list):
        return None
    measured = [
        entry for entry in contracts
        if isinstance(entry, dict)
        and entry.get("present")
        and isinstance(entry.get("bytes"), int)
        and entry["bytes"] > 0
    ]
    if not measured:
        return None

    parts: list[str] = []
    total = score.get("prompt_bytes")
    if isinstance(total, int) and total > 0:
        # `prompt_bytes` is the whole wake (prose + mounted blocks) since
        # #741; before that it was the prose subtotal wearing the name, and
        # this line would have under-reported by ~24%.
        parts.append(f"wake {_fmt_kb(total)}")

    top = max(measured, key=lambda entry: entry["bytes"])
    label = str(top.get("block_key") or top.get("label") or "?").strip() or "?"
    parts.append(f"top {label} {_fmt_kb(top['bytes'])}")

    # ISO-shaped strings, so lexical order is chronological order.
    oldest = min(
        (
            str(entry["oldest_item"]).strip()
            for entry in measured
            if isinstance(entry.get("oldest_item"), str)
            and str(entry["oldest_item"]).strip()
        ),
        default="",
    )
    if oldest:
        parts.append(f"oldest {oldest[:10]}")
    return " · ".join(parts)


# ── Injection rendering (portal-state → compact delta) ───────────────────
#
# Slice 8 (#513): the mid-run (``post-tool``) boundary renders as ONE compact
# "agnoster"-style status bar instead of the multi-line prose block seed/stop
# keep. ``seed`` and ``stop`` stay affirmative, clear prose on purpose (the
# maintainer's 2026-06-23 rule, kept below) — only ``post-tool`` gets the bar,
# because it is the boundary that fires often enough for verbosity to become
# habituation. See :func:`_render_bar` for the assembler and
# :data:`BAR_SEGMENTS` for the vocabulary.


@dataclass(frozen=True)
class _BarSegment:
    """One documented entry in the bar's fixed segment vocabulary."""

    key: str
    glyph: str
    meaning: str


#: The bar's segment vocabulary, in render order. Fixed and documented (#513
#: — "a fixed documented segment vocabulary") rather than ad-hoc per-render
#: choices, so a resident (or a human skimming several bars) learns it once.
#: Every segment renders only when laden/changed; a quiet boundary emits a
#: short bar with just a handful of these. Deliberately *not* here: pending
#: events, a stale/blank ``.card``'s reason, an unwritten ``.name``, and a
#: "running long" warning — those are obligations, and burying an obligation
#: in a glyph is exactly the failure this vocabulary exists to avoid, so they
#: stay full detail lines below the bar (see :func:`_render_bar`) instead of
#: a segment here.
BAR_SEGMENTS: tuple[_BarSegment, ...] = (
    _BarSegment(
        "run", "⌁",
        "run identity — the run id's 4-char random disambiguator "
        "(`run-YYMMDD-HHMM-<rand>` → `<rand>`). Always first when the bar "
        "renders at all.",
    ),
    _BarSegment(
        "budget", "⏱",
        "wall-clock posture — elapsed/soft-limit minutes (`16/120m`). "
        "Renders whenever both numbers are known.",
    ),
    _BarSegment(
        "quota", "q",
        "every subscription quota bucket the `quota` facet knows about, "
        "abbreviated to one letter + remaining percent, joined by `·` "
        "(`S57·W50·F27` = session 57%, week 50%, a named per-model bucket "
        "27%). Renders only when quota is `known`.",
    ),
    _BarSegment(
        "orient", "orient",
        "the orientation ledger (#513 Slice 9): files read from the wake's "
        "deterministic orientation set (`orient 3/5`). Renders only while "
        "the walk is open — set non-empty, not every file read, no skip "
        "**declared** on `.card`. A declaration is `orient: skip` heading a "
        "line, or the sentence \"assuming prior knowledge, skipping "
        "orientation\"; merely *mentioning* both words is prose, not a "
        "declaration, and does not silence the meter. Disappears at "
        "completion or skip, and never opens the "
        "bar on its own: a meter is not an obligation, and a meter that "
        "never leaves trains skimming.",
    ),
    _BarSegment(
        "census", "wake",
        "the wake census (#739, #683): total wake bytes, the biggest single "
        "block, and the oldest item any block still carries "
        "(`wake 115.7 KB · top work-surface 24.7 KB · oldest 2026-07-25`). "
        "Projection off the `boot-score.json` the daemon wrote before this "
        "runner started, so it never re-measures and never disagrees with "
        "the score. Static for a run by construction — it is the shape of "
        "the boot, not a meter that moves — and each field degrades "
        "independently when the score is partial.",
    ),
    _BarSegment(
        "siblings", "▷",
        "coexisting sibling runs in this dominion (`▷1`). Renders only "
        "when the count is > 0 — an idle dominion says nothing here.",
    ),
    _BarSegment(
        "keepalive", "rb",
        "keepalive extension remaining, the slot held past budget (`rb3h`). "
        "Renders only while `.keepalive` is active.",
    ),
    _BarSegment(
        "delivery", "⇡",
        "delivery this run — current-thread replies + everything else "
        "(other threads, outbound messages) (`⇡2+3`). Renders only once "
        "something has been sent.",
    ),
    _BarSegment(
        "produce", "⚒",
        "total attested produce items this run (commits, branches, PRs, kb "
        "pages, issues, comments, messages, files) (`⚒4`). Renders only "
        "when nonzero.",
    ),
    _BarSegment(
        "gate", "gate",
        "this run's gate receipt: verdict and the head it was earned on "
        "(`gate GREEN@e59f527`), plus `(moved)` when the tree moved under "
        "the gate while it ran (#917). Renders only once a receipt exists — "
        "a run that has not gated is not a run that failed. The sha is the "
        "honest half: the tree a run gates is often not the tree it is "
        "checked out in, so a receipt describing another tree is legible as "
        "such rather than mistaken for this one. Not a staleness verdict — "
        "that needs the tree the receipt names, and the closeout clause "
        "makes that comparison where it has the standing to (#1048).",
    ),
    _BarSegment(
        "owed", "owed",
        "outstanding promises — rows in `.promises.jsonl` this run has not "
        "yet matched with produce (`owed 2`). Renders only when the count is "
        "> 0, the same differential discipline as `!N`: a blueprint with "
        "nothing outstanding says nothing. Deliberately not a ratio against "
        "`⚒` — that count includes things nobody promised, so `2/5` would be "
        "an aggregate over a population with no shared denominator, and a "
        "chip shaped like a progress bar gets read as one. The chip is the "
        "ambient half; *which* things are owed rides a detail line, latched "
        "on the blueprint's own delta (#1008).",
    ),
    _BarSegment(
        "mood", "mood",
        "the resident's own `.mood` control file (#566 layer 2), truncated "
        "to 16 chars, with the emote's base-frame glyph prefixed when "
        "`brr.emotes` resolves the name. Renders every boundary it is "
        "present; on a boundary that *surprised* the run it also carries "
        "`← <what happened>`, which is the ask — the mood channel questions "
        "itself on an edge, not on every tick (#604). The older "
        "unconditional `·keep?` suffix this entry used to document was "
        "removed with that change.",
    ),
    _BarSegment(
        "notices", "!",
        "refusal count — directives brr dropped this run (`!1`). Renders only "
        "when the count is > 0: a refused outbox file is deleted exactly like "
        "an accepted one, so the only thing between a dropped reply and silence "
        "is the resident opening `portal-state.json → notices`. This segment "
        "surfaces a non-zero count without demanding a read. Absent at zero so "
        "it earns its ink the same way every other differential segment does.",
    ),
    _BarSegment(
        "card", "card",
        "the live `.card` surface's own health: `ok` / `stale` / `blank` / "
        "`cut N>4096`. The last measures the *projection* the transport "
        "publishes, not the file — a long card with a well-formed `Now` is "
        "fine; `cut` means the live surface is losing the tail. "
        "Always the last segment when the bar renders at all — the cheap, "
        "always-current anchor. A `stale` value also gets its own detail "
        "line naming why (see above) — the chip alone is never the whole "
        "obligation.",
    ),
)


def _run_id_chip(run: dict[str, Any]) -> str | None:
    run_id = str(run.get("id") or "").strip()
    if not run_id:
        return None
    tail = run_id.rsplit("-", 1)[-1].strip()
    return f"⌁ {tail}" if tail else None


def _budget_chip(budget: dict[str, Any]) -> str | None:
    elapsed = budget.get("elapsed_seconds")
    limit = budget.get("budget_seconds")
    if elapsed is None or limit is None:
        return None
    try:
        return f"⏱ {int(elapsed) // 60}/{int(limit) // 60}m"
    except (TypeError, ValueError):
        return None


# One quota-bucket phrase within the facet's rendered summary string, e.g.
# "session 57% left (resets ...)" or "Fable week 27% left" (claude_usage.py)
# or "5h 79% left" / "weekly 41% left" (codex_status.py) — parsed back apart
# because the bar needs the label+percent, not the prose. A leading digit in
# a duration-style label ("5h") is not part of the captured label — harmless,
# since `_quota_bucket_letter` derives the chip from the label's first
# *alphabetic* character, not strictly its first character.
_QUOTA_BUCKET_RE = re.compile(
    r"(?P<label>[A-Za-z][\w]*(?:\s+[A-Za-z][\w]*)*?)\s+"
    r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*left",
)
_QUOTA_MODEL_WEEK_RE = re.compile(r"^(?P<model>.+?)\s+week$")


def _quota_bucket_letter(label: str, taken: set[str]) -> str:
    """Abbreviate one quota bucket's label to a single letter.

    ``session`` → S, ``week`` → W (the maintainer's own shorthand, #513); a
    per-model week bucket (Claude's ``"Fable week"``) abbreviates to the
    *model's* first letter, not W again — two buckets both reading "week"
    would be indistinguishable. Anything else (Codex's ``5h`` / ``weekly``)
    falls back to its label's first *alphabetic* character (a duration label
    can lead with a digit the regex above doesn't capture, but defends here
    too rather than assuming). A repeat letter (two per-model buckets
    sharing an initial) widens to two characters rather than one chip
    silently swallowing another.
    """
    key = label.strip().lower()
    if key == "session":
        letter = "S"
    elif key == "week":
        letter = "W"
    else:
        model_week = _QUOTA_MODEL_WEEK_RE.match(key)
        if model_week:
            model = model_week.group("model").strip()
            letter = (model[:1] or "w").upper()
        else:
            alpha = next((ch for ch in key if ch.isalpha()), None)
            letter = alpha.upper() if alpha else (key[:1].upper() or "?")
    if letter in taken:
        letter = label.strip()[:2] or letter
    taken.add(letter)
    return letter


def _quota_chip(resources: dict[str, Any]) -> str | None:
    facet = resources.get("quota") if isinstance(resources, dict) else None
    facet = facet if isinstance(facet, dict) else {}
    if facet.get("status") != "known":
        return None
    summary = str(facet.get("summary") or "").strip()
    if not summary:
        return None
    taken: set[str] = set()
    chips: list[str] = []
    for part in summary.split(";"):
        match = _QUOTA_BUCKET_RE.search(part)
        if not match:
            continue
        pct = match.group("pct").split(".")[0]
        chips.append(f"{_quota_bucket_letter(match.group('label'), taken)}{pct}")
    return "q " + "·".join(chips) if chips else None


def _siblings_chip(resources: dict[str, Any]) -> str | None:
    facet = resources.get("coexisting_runs") if isinstance(resources, dict) else None
    facet = facet if isinstance(facet, dict) else {}
    if facet.get("status") != "known":
        return None
    siblings = facet.get("siblings")
    n = len(siblings) if isinstance(siblings, list) else 0
    return f"▷{n}" if n else None


def _keepalive_remaining_seconds(until_iso: Any) -> float | None:
    if not isinstance(until_iso, str) or not until_iso.strip():
        return None
    try:
        dt = datetime.datetime.strptime(until_iso.strip(), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    dt = dt.replace(tzinfo=datetime.timezone.utc)
    remaining = (dt - datetime.datetime.now(tz=datetime.timezone.utc)).total_seconds()
    return remaining if remaining > 0 else None


def _fmt_short_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds >= 3600:
        return f"{max(1, round(seconds / 3600))}h"
    return f"{max(1, round(seconds / 60))}m"


def _keepalive_chip(budget: dict[str, Any]) -> str | None:
    keepalive = budget.get("keepalive") if isinstance(budget.get("keepalive"), dict) else {}
    if keepalive.get("status") != "active":
        return None
    remaining = _keepalive_remaining_seconds(keepalive.get("until"))
    if remaining is None:
        return None
    return f"rb{_fmt_short_duration(remaining)}"


def _delivery_chip(outbound: dict[str, Any]) -> str | None:
    current = int(outbound.get("replies_current", 0) or 0)
    other = int(outbound.get("replies_other", 0) or 0) + int(
        outbound.get("outbound_messages", 0) or 0
    )
    if not current and not other:
        return None
    return f"⇡{current}+{other}"


def _produce_total(produce: dict[str, Any]) -> int:
    if not produce.get("known"):
        return 0
    counts = produce.get("counts") if isinstance(produce.get("counts"), dict) else {}
    return sum(int(v or 0) for v in counts.values() if isinstance(v, (int, float)))


def _card_chip(card: dict[str, Any], card_stale: bool) -> str:
    """The live `.card` surface's health — measured, not assumed.

    This chip said ``card ok`` throughout #685: it checked that the file was
    non-empty, which is not the question the transport asks. What publishes is
    the *projection*, against a 4096-char field, so a 30 KB card with a
    well-formed ``Now`` is safe and a 4.1 KB one is not — and the resident
    cannot tell those apart from any other surface it can see.

    So the meter runs the function the transport runs. Since #722 the daemon
    bounds the projection rather than overflowing it, which means this no
    longer warns of an imminent 422 — it warns that the live card is being
    *truncated*, which is the same fact arriving early enough to act on.
    """
    if card_stale:
        return "card stale"
    if not card.get("active"):
        return "card blank"
    text = card.get("text")
    if isinstance(text, str):
        size = len(card_rule.now_projection(text))
        if size > card_rule.CARD_TEXT_MAX_CHARS:
            return f"card cut {size}>{card_rule.CARD_TEXT_MAX_CHARS}"
    # No body in the capsule to measure — an older capsule shape, not a
    # verdict. Report what the chip has always reported rather than inventing
    # a failure out of a missing field.
    return "card ok"


# #1002: a notice carries a ``kind`` since daemon.py:5765 — ``refused`` |
# ``dropped`` | ``advisory``. Only the first two are a claim that something
# the resident asked for did not happen; an ``advisory`` is FYI on a
# directive that *was* accepted and acted on (the ``note:`` body-ignored
# case that opened this ticket). Both surfaces below (the bar chip, the
# seed/stop briefing count) must agree on which kinds count, so the
# filter lives once, here.
#
# #716: ``kind`` alone under-distinguishes. A record also carries a
# ``lifetime`` — ``"run"`` (this run's own directive; a fresh refusal is a
# real transition worth an alarm) or ``"standing"`` (an environmental fact
# — an ignored `.brr/config` security key, an unreachable `runners.md` —
# that re-fires identically on every wake and nothing this run does can
# clear). Counting a standing notice pins ``!N`` at a nonzero baseline from
# t=0, so a genuine new refusal reads as a delta against noise (``!1`` ->
# ``!2``) instead of the zero-to-one transition the chip exists to show.
# Excluded for the same *reason* as ``advisory`` — FYI, not a failure to
# act on right now — via a second, independent field rather than folding
# it into ``kind`` (which would make the record lie about what happened).
def _counted_notices(notices: list) -> list:
    """The subset of *notices* that represent a refusal or a drop.

    A record with no ``kind`` key, no ``lifetime`` key, or neither, is a
    legacy entry — written by a daemon generation before #1002 or #716,
    possibly still sitting in a live ``portal-state.json`` across a daemon
    restart. It counts as refusing: the pessimistic direction, chosen
    deliberately, because a real refusal hidden by an under-count costs far
    more than one stale advisory or standing notice over-counted. Only an
    entry explicitly marked ``kind == "advisory"`` or
    ``lifetime == "standing"`` is excluded.
    """
    if not isinstance(notices, list):
        return []
    return [
        n for n in notices
        if not (
            isinstance(n, dict)
            and (n.get("kind") == "advisory" or n.get("lifetime") == "standing")
        )
    ]


def _gate_chip(receipt: dict[str, Any] | None) -> str | None:
    """``gate GREEN@e59f527`` — the verdict, and the tree it was earned on.

    `.gate-receipt.json` decides whether a run may merge — ``workflow.md``
    self-merge condition 1 is, operationally, a question about this file —
    and until now it was the only control file in the outbox with no chip
    (#1048). Reaching the verdict meant grepping a log, which is exactly the
    habit *the gate's verdict is the receipt line, never the exit code a
    pipe hands you* exists to force. A chip removes the need to remember it.

    **The sha is not decoration; it is the honesty.** The tree a run gates
    is frequently not the tree it is checked out in — a gate run inside
    ``/tmp/brr-wt-<slug>`` writes its receipt into the run's outbox while
    the checkout sits on ``main``. Carrying the head the verdict was earned
    on lets a reader see that a receipt describes *another* tree instead of
    silently mistaking it for this one.

    Deliberately **not** a staleness verdict. "Passed on a tree that has
    since moved" needs the tree the receipt names, not the run's checkout,
    and comparing against the checkout would read STALE on a receipt that is
    perfectly current — a guard firing constantly for a non-reason. The
    closeout's own :func:`_gate_closeout_clause` does that comparison where
    it has the repo dir and the standing to make it; this chip states the
    fact and lets the reader compare.

    ``tree_moved_during_gate`` is the one qualifier that *is* exact (#917 —
    the tree moved under the gate while it ran), so it rides along in words
    rather than as a mark nobody can resolve.

    Absent when there is no receipt: the same differential discipline as
    ``!N`` and ``owed N``. **A run that has not gated is not a run that
    failed**, and a chip claiming otherwise would be the pessimistic lie
    where the honest answer is silence.
    """
    if not isinstance(receipt, dict) or not receipt:
        return None
    verdict = str(receipt.get("verdict") or "").strip()
    if not verdict:
        return None
    head = str(receipt.get("head") or "").strip()
    chip = f"gate {verdict}" + (f"@{head[:7]}" if head else "")
    if receipt.get("tree_moved_during_gate"):
        chip += " (moved)"
    return chip


def _notices_chip(notices: list) -> str | None:
    """``!N`` when *notices* has a refused/dropped entry; absent at zero.

    A refused outbox file is deleted exactly like an accepted one — the only
    thing between a dropped reply and silence is a resident habitually opening
    ``portal-state.json``.  ``!N`` on the bar makes a non-zero refusal count
    visible without demanding that read.  Absent at zero so it earns its ink
    the same way every other differential segment does. An ``advisory``
    notice never drives this count (#1002), and neither does a ``standing``
    one (#716 — an environmental fact this run cannot clear, not a fresh
    refusal) — both stay readable in ``portal-state.json`` and in the
    seed/stop briefing below, just not here.
    """
    n = len(_counted_notices(notices))
    return f"!{n}" if n else None


# Rendered chip length for a `.mood` name — short enough that a verbose mood
# can't dominate a boundary, long enough that a real emote name reads whole
# (e.g. "quietly_stuck").
_MOOD_DISPLAY_MAX_CHARS = 16


def _emote_glyph(name: str) -> str | None:
    """Best-effort base-frame glyph for *name*, from `brr.emotes`.

    `brr.emotes` may not be importable in a stripped install, and a name the
    resident invented has no entry — both degrade to no glyph (the raw name
    still renders) rather than raising. The import lives inside the ``try``,
    not at module scope, so a sibling module can never break every hook
    boundary in this one.

    **The broad ``except`` is a tolerance, not a contract.** It was written
    while `brr.emotes` was still in flight (#566 / #601) against an assumed
    ``glyph(name)``, with a note to reconcile if the shipped surface
    differed. It differed — the library shipped ``lookup`` / ``for_telemetry``
    and no ``glyph`` — so every boundary since raised ``AttributeError`` here
    and swallowed it, and the mood chip has rendered a bare name with no face
    for its whole life. Nobody could see it, because a guard that catches the
    signal it was meant to survive fails *quietly* by construction. The seam
    is now a named function in `brr.emotes` and pinned by a test that renders
    a real chip end to end; this guard covers only the cases named above.
    """
    try:
        from . import emotes  # type: ignore
    except ImportError:
        return None
    try:
        glyph = emotes.glyph(name)
    except Exception:
        return None
    glyph = str(glyph or "").strip()
    return glyph or None


def _emote_near_misses(name: str) -> list[str]:
    """Handles a failed ``.mood`` write was probably reaching for.

    Same tolerance shape as ``_emote_glyph``: a hook boundary never dies for
    a face. Empty list ⇒ say nothing.
    """
    try:
        from . import emotes  # type: ignore
    except ImportError:
        return []
    try:
        return [e.name for e in emotes.near_misses(name, limit=3)]
    except Exception:
        return []


def _mood_chip(raw: str) -> str:
    """The resident's `.mood` first line, rendered as a short chip.

    **An unresolved handle says so.** Until 2026-07-25 a `.mood` line that
    matched no emote rendered as the bare word here and as four ``null``s on
    the wire, and the dashboard printed the raw string — so a run that wrote
    ``focused`` (the handle is ``fo.cus``) believed it was wearing a face,
    published an id, and had no way to find out. The whole first week of the
    mood channel went out that way; the human looking at brnrd.dev was the
    only reader who could catch it, and did.

    ``lookup`` is tolerant now, so most of that class resolves. What is left
    is the honest miss — a family word (``satisfied`` is four faces; picking
    one would be the guess the honesty bar forbids) or an invented handle —
    and the fix for a miss is never to guess, it is to **not be silent**.
    The chip names the nearest faces, at the boundary, while the run can
    still rewrite the file.
    """
    name = raw.strip()
    if len(name) > _MOOD_DISPLAY_MAX_CHARS:
        name = name[:_MOOD_DISPLAY_MAX_CHARS].rstrip() + "…"
    glyph = _emote_glyph(name)
    if glyph:
        return f"{glyph} {name}"
    near = _emote_near_misses(name)
    if near:
        return f"✗ {name} → " + " · ".join(near)
    return f"✗ {name}"


# Substrings that mark a tool result as a failure. Deliberately a heuristic:
# claude's ``PostToolBatch`` payload hands each call over as
# ``{tool_name, tool_input, tool_use_id, tool_response}`` with **no structured
# error flag** (verified against a live payload 2026-07-23) — a non-zero Bash
# exit arrives as the plain string ``"Exit code 1"``. A fuzzy signal is fine
# here precisely because of what it is allowed to do: this one only ever
# *annotates* a mood chip. It never blocks, never destroys, and a false
# positive costs one deictic mark on one boundary.
# Kept deliberately short and high-precision. A wider net ("error:",
# "permission denied", "no such file") fires on tool output that merely
# *contains* those words — a grep over source, a log tail — and every one of
# those cases already arrives with a non-zero exit anyway, so the wider net
# buys no recall and costs precision.
_TOOL_FAILURE_MARKERS = (
    "exit code ",
    "<error>",
    "traceback (most recent call last)",
    "command not found",
)


def _tool_surprise(payload: dict[str, Any]) -> str | None:
    """Name the tool whose result just came back wrong, or ``None``.

    The mood channel's whole problem (#566 layer 2, maintainer 2026-07-23) is
    that an invitation rendered at *every* boundary is an invitation nobody
    reads — the same habituation this module already names one segment over,
    where a bare pending count had to grow an action verb because "a dense bar
    habituates faster than prose". A mood is a *derivative*, not a level: it
    moves when something unexpected happens, and most boundaries are not that.

    So the ask fires on the edge, and this is the edge detector. Returns a
    short deictic tag (``"Bash ✗"``) for the first failed call in the batch —
    enough to point at what happened without re-describing it.
    """
    calls = payload.get("tool_calls")
    if not isinstance(calls, list):
        return None
    for call in calls:
        if not isinstance(call, dict):
            continue
        response = call.get("tool_response")
        if not isinstance(response, str):
            # A structured response is the success shape for most tools; a
            # dict carrying its own error flag is the one exception worth
            # honouring if a runner ever grows one.
            if isinstance(response, dict) and response.get("is_error"):
                name = str(call.get("tool_name") or "tool").strip()
                return f"{name} ✗"
            continue
        head = response[:200].lower()
        if any(marker in head for marker in _TOOL_FAILURE_MARKERS):
            name = str(call.get("tool_name") or "tool").strip()
            return f"{name} ✗"
    return None


# ── Pending-event letter chrome ──────────────────────────────────────────
#
# The boundary-injected pending list used to be a flat, lossy block — three
# measured defects, observed live (run-260731-1802-j6ke):
#
# 1. Truncation without accounting: a 600-byte maintainer message rendered
#    as a ~200-char preview cut mid-sentence — no sender, no age, no size,
#    no pointer to the rest.
# 2. Verbatim refeed of huge bodies: a schedule tick whose body is a ~10 KB
#    entry spec was re-injected whole at every boundary — ten-plus copies a
#    run, drowning the real messages.
# 3. A dishonest label: the Stop fold-in called a schedule firing "from the
#    user", which it is not.
#
# So each pending event now gets letter chrome — one compact header row
# (glyph · id · source · correspondent · age · size) — a body policy that
# never cuts a short message and never refeeds a huge one, and per-run
# seen-suppression so an unchanged body renders once in full and one honest
# line thereafter. Never lossy about what *exists*: every event always gets
# at least one line, and every elision states its size and where the full
# body lives.

# Inline-in-full ceiling, in bytes of body. Below it a message renders whole
# (never cut a short message mid-sentence); above it the first line renders
# plus an explicit accounting line (total size + path to the full body).
_EVENT_INLINE_BODY_MAX = 700

# Cap on the *first line* excerpt of an over-ceiling body — a 10 KB body can
# legally be one single line, and the excerpt must not become the refeed it
# exists to prevent.
_EVENT_FIRST_LINE_MAX = 160

# Hook-state key: per-event ``{"digest": sha256[:16], "shown": N}``, pruned
# to events still pending. Lives in `.hook-state.json` beside the other
# run-scoped hook memory (tokens, latches) — hooks are fresh subprocesses,
# so "already shown" must be persisted, not remembered.
EVENTS_SEEN_KEY = "events_seen"

# Source → glyph, first substring match wins. `schedule` before the default
# so a compound source stays honest; anything unknown reads as a letter.
_EVENT_GLYPHS = (
    ("schedule", "⏰"),
    ("github", "⎇"),
    ("forge", "⎇"),
    ("spawn", "⚙"),
    ("worker", "⚙"),
)
_EVENT_GLYPH_DEFAULT = "✉"


def _event_glyph(source: str) -> str:
    src = source.lower()
    for key, glyph in _EVENT_GLYPHS:
        if key in src:
            return glyph
    return _EVENT_GLYPH_DEFAULT


def _short_event_id(eid: object) -> str:
    """`evt-1785520992817014311-8jwi` → `evt-…8jwi`; short ids stay whole.

    Chrome-stub form only — mirror-card stubs and the daemon's ambiguous-id
    notices. The surfaces that *announce* a pending event (boundary rows,
    Stop fold-in) print the full id via :func:`_full_event_id` instead: an
    `event:` reply needs the id verbatim, and #934 showed a reconstructed
    prefix guessed wrong drops the reply. #906's short-id resolution stays
    as the safety net, not the interface.
    """
    raw = str(eid or "").strip()
    if not raw:
        return "-"
    match = re.fullmatch(r"(evt-)\d{6,}-(\w+)", raw)
    if match:
        return f"{match.group(1)}…{match.group(2)}"
    return raw


def _full_event_id(eid: object) -> str:
    """The id verbatim — the announcing surface must be copy-able (#934).

    `evt-…i10p` is a description of an id, not an id: an `event:`-addressed
    reply requires the full form, so the one line that announces an event
    carries it whole (~30 chars; the chrome row has no length budget — the
    excerpt caps bound only the body block).
    """
    raw = str(eid or "").strip()
    return raw or "-"


def _event_body(ev: dict[str, Any]) -> str:
    """The event's body, falling back to its summary when no body rode along."""
    body = str(ev.get("body") or "").strip()
    if body:
        return body
    return str(ev.get("summary") or "").strip()


def _fmt_body_size(size: int) -> str:
    """`612 B` under a KB, else `_fmt_kb`'s `9.8 KB`."""
    if size < 1024:
        return f"{size} B"
    return _fmt_kb(size)


def _event_age_seconds(created: object) -> float | None:
    text = str(created or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return max(0.0, (now - stamp).total_seconds())


def _fmt_age(seconds: float | None) -> str | None:
    """Humanized age: `42s` / `3m` / `2h` / `5d`. ``None`` stays silent."""
    if seconds is None:
        return None
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _event_correspondent(ev: dict[str, Any]) -> str | None:
    """Who (or what) is speaking, from the event's own frontmatter meta.

    Telegram carries the sender's display name and handle, GitHub its login,
    the cloud relay its own user fields, and a schedule firing names the
    entry that fired — the honest analogue of a sender for a timer.
    """
    name = str(ev.get("telegram_user") or "").strip()
    handle = str(ev.get("telegram_username") or "").strip()
    if not name and not handle:
        name = str(ev.get("github_author") or ev.get("cloud_user") or "").strip()
        handle = str(ev.get("cloud_username") or "").strip()
    if handle and not handle.startswith("@"):
        handle = f"@{handle}"
    if name and handle:
        return f"{name} ({handle})"
    if name or handle:
        return name or handle
    schedule_id = str(ev.get("schedule_id") or "").strip()
    if schedule_id:
        return schedule_id
    return None


def _event_header(
    ev: dict[str, Any], *, size: int, changed: bool = False
) -> str:
    """The one letter-chrome row: glyph · id · source · who · age · size."""
    source = str(ev.get("source") or "-").strip() or "-"
    parts = [f"{_event_glyph(source)} {_full_event_id(ev.get('id'))}", source]
    correspondent = _event_correspondent(ev)
    if correspondent and correspondent != source:
        parts.append(correspondent)
    age = _fmt_age(_event_age_seconds(ev.get("created")))
    if age:
        parts.append(age)
    parts.append(_fmt_body_size(size))
    if changed:
        parts.append("Δ changed")
    return " · ".join(parts)


def _event_body_block(
    body: str, size: int, inbox_pointer: str | None, *, indent: str = "  "
) -> list[str]:
    """Render *body* per the letter policy — whole, or excerpt + accounting.

    ≤ :data:`_EVENT_INLINE_BODY_MAX` bytes renders verbatim (a short message
    is never cut); above it, the first line plus an explicit `… N KB total ·
    full body: <path>` line, so nothing is elided without saying how much
    and where it lives.
    """
    if not body:
        return [f"{indent}(no body)"]
    if size <= _EVENT_INLINE_BODY_MAX:
        return [indent + line for line in body.splitlines()]
    first = body.splitlines()[0].strip()
    if len(first) > _EVENT_FIRST_LINE_MAX:
        first = first[: _EVENT_FIRST_LINE_MAX - 1].rstrip() + "…"
    pointer = inbox_pointer or "inbox.json (this run's outbox)"
    return [
        indent + first,
        f"{indent}… {_fmt_body_size(size)} total · full body: {pointer}",
    ]


def _event_seen_line(ev: dict[str, Any], shown: int) -> str:
    """`⏰ evt-1785520992817014311-8jwi · schedule · seen ×3 · unchanged`.

    One honest line — full id, because even the collapsed form is the
    surface a reply gets addressed from (#934).
    """
    source = str(ev.get("source") or "-").strip() or "-"
    return (
        f"{_event_glyph(source)} {_full_event_id(ev.get('id'))} · {source} "
        f"· seen ×{shown} · unchanged"
    )


def _render_event_rows(
    events: list[Any],
    event_seen: dict[str, dict[str, Any]] | None,
    inbox_pointer: str | None,
) -> list[str]:
    """One letter per pending event: chrome row + body per policy.

    *event_seen* is the boundary's per-event decision map from
    :func:`_event_seen_decisions` (``None`` ⇒ everything renders as new —
    the shape ad-hoc callers and replay get). An unchanged already-shown
    body collapses to the one-line seen form; a changed one re-renders in
    full under a ``Δ changed`` mark.
    """
    rows: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        decision = (event_seen or {}).get(str(ev.get("id") or ""))
        status = (decision or {}).get("status") or "new"
        shown = int((decision or {}).get("shown") or 0)
        if status == "seen":
            rows.append(f"- {_event_seen_line(ev, shown)}")
            continue
        body = _event_body(ev)
        size = len(body.encode("utf-8", "replace"))
        rows.append(f"- {_event_header(ev, size=size, changed=status == 'changed')}")
        rows.extend(_event_body_block(body, size, inbox_pointer))
    return rows


def _event_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:16]


def _event_seen_decisions(
    state: dict[str, Any], events: list[Any]
) -> dict[str, dict[str, Any]]:
    """Decide, once per boundary, how each pending event renders.

    Pure read of the persisted ledger — committing (increment + prune) is
    :func:`_commit_event_seen`'s, and only for events a render actually
    carried: a boundary that injected nothing must not age the ledger, or
    the first *rendered* appearance would already claim "seen".
    """
    seen = state.get(EVENTS_SEEN_KEY)
    if not isinstance(seen, dict):
        seen = {}
    decisions: dict[str, dict[str, Any]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("id") or "")
        if not eid:
            continue
        digest = _event_digest(_event_body(ev))
        entry = seen.get(eid)
        if isinstance(entry, dict) and entry.get("digest") == digest:
            status = "seen"
        elif isinstance(entry, dict):
            status = "changed"
        else:
            status = "new"
        decisions[eid] = {
            "status": status,
            "shown": int(entry.get("shown") or 0) if isinstance(entry, dict) else 0,
            "digest": digest,
        }
    return decisions


def _commit_event_seen(
    state: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    shown_ids: set[str],
) -> None:
    """Persist the boundary's showings; prune events no longer pending.

    Only *shown_ids* — events an actual injection or fold-in carried — get
    their digest stamped and count bumped; an event the gates kept silent
    keeps its old entry so its next real render is still honest about what
    was last shown.
    """
    seen = state.get(EVENTS_SEEN_KEY)
    if not isinstance(seen, dict):
        seen = {}
    out: dict[str, Any] = {}
    for eid, decision in decisions.items():
        prev = seen.get(eid) if isinstance(seen.get(eid), dict) else None
        if eid in shown_ids:
            out[eid] = {
                "digest": decision["digest"],
                "shown": (int(prev.get("shown") or 0) if prev else 0) + 1,
            }
        elif prev is not None:
            out[eid] = prev
    state[EVENTS_SEEN_KEY] = out


def _render_armed_rows(armed: list[Any] | None) -> list[str]:
    """The armed dated-letters block (#904): one line per pending ``at:`` entry.

    An ``every:`` entry self-heals — it re-derives its own condition fresh
    at every firing. A one-shot ``at:`` cannot: it was armed against a
    premise that can stop holding at any point before its clock goes off,
    and nothing else re-checks it in between. So every boundary that shows
    pending events also shows the still-armed set, verbatim off the
    daemon's own schedule projection (``schedule.armed_letters``) — a run
    sweeps its own changes against them by *reading*, not remembering.

    Facts, not obligations (same status as *finished_spawns*): always
    rendered when non-empty, never gated behind "address this", and never
    seen-suppressed — the whole point is that it re-shows every boundary.
    """
    if not armed:
        return []
    count = len(armed)
    rows = [
        f"⏲ {count} armed dated letter(s) — pending `at:` schedule "
        "entries, swept fresh each boundary:"
    ]
    for entry in armed:
        if not isinstance(entry, dict):
            continue
        when = str(entry.get("when") or entry.get("at") or "-").strip() or "-"
        heading = str(entry.get("heading") or entry.get("id") or "-").strip() or "-"
        premise = str(entry.get("premise") or "").strip()
        line = f"- ⏲ {when} · {heading}"
        if premise:
            line += f" · premise: {premise}"
        rows.append(line)
    return rows


def _partition_pending_events(
    payload: dict[str, Any],
) -> tuple[list[Any], list[dict[str, Any]], int]:
    """Split actionable events from this run's self-retiring completions.

    A ``spawn_completed`` event dispatched by the current run is an observed
    fact: the daemon retires it when the parent ends.  Both the boundary
    renderer and the Stop blocker consume this partition so they cannot call
    the same event self-retiring and still-pending in one payload (#990).
    """
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    inbound = (
        payload.get("inbound")
        if isinstance(payload.get("inbound"), dict) else {}
    )
    attention = (
        payload.get("attention")
        if isinstance(payload.get("attention"), dict) else {}
    )
    events = inbound.get("events") if isinstance(inbound.get("events"), list) else []
    pending = int(attention.get("pending_event_count", 0) or 0)
    run_id = str(run.get("id") or "")

    def is_finished_spawn(event: Any) -> bool:
        return (
            bool(run_id)
            and isinstance(event, dict)
            and event.get("source") == "spawn_completed"
            and event.get("spawn_parent_run_id") == run_id
        )

    finished_spawns = [event for event in events if is_finished_spawn(event)]
    action_events = [event for event in events if not is_finished_spawn(event)]
    action_pending = max(0, pending - len(finished_spawns))
    return action_events, finished_spawns, action_pending


def _render_bar(
    *,
    run: dict[str, Any],
    pending: int,
    pending_files: int,
    events: list[Any],
    budget: dict[str, Any],
    outbound: dict[str, Any],
    produce: dict[str, Any],
    card: dict[str, Any],
    card_stale: bool,
    resources: dict[str, Any],
    run_name: dict[str, Any],
    mood: str | None,
    surprise: str | None = None,
    orient: tuple[int, int] | None = None,
    census: str | None = None,
    notices: list[Any] | None = None,
    finished_spawns: list[dict[str, Any]] | None = None,
    event_seen: dict[str, dict[str, Any]] | None = None,
    inbox_pointer: str | None = None,
    armed: list[Any] | None = None,
    gate_receipt_data: dict[str, Any] | None = None,
    plan: "promises.Blueprint | None" = None,
    plan_edge: bool = False,
) -> str | None:
    """The mid-run (``post-tool``) status bar: one line + obligation details.

    Builds the fixed :data:`BAR_SEGMENTS` chips left to right, then appends
    detail lines *only* for new obligations — non-zero pending events, a
    stale/blank card's reason, an unwritten ``.name``, running long — the
    same guardrail this whole redesign exists to keep (#513: "never bury an
    obligation in a glyph"). Returns ``None`` when nothing here is worth a
    turn, mirroring the mid-run gate the old prose form kept: mere resource
    or produce chatter must not manufacture an injection by itself.

    *notices* drives the ``!N`` segment: non-zero refusal count only.
    *finished_spawns* are the ``spawn_completed`` events the parent already
    observed; they are facts, not obligations, and are reported separately
    rather than counted against *pending*. *armed* is the #904 armed
    dated-letters projection — see :func:`_render_armed_rows`.
    """
    segments: list[str] = []
    id_chip = _run_id_chip(run)
    if id_chip:
        segments.append(id_chip)
    budget_chip = _budget_chip(budget)
    if budget_chip:
        segments.append(budget_chip)
    quota_chip = _quota_chip(resources)
    if quota_chip:
        segments.append(quota_chip)
    if orient is not None:
        # The orientation ledger, open. Deliberately absent from the gate
        # below: the meter rides boundaries the bar renders anyway and never
        # manufactures one — an unwalked set is not an obligation (skip is a
        # first-class outcome), and a segment that could keep the bar alive
        # at every boundary would train the exact skimming it measures.
        segments.append(f"orient {orient[0]}/{orient[1]}")
    if census:
        # Sits beside `orient` because both describe the *wake*, not the run:
        # what the boot cost, and how much of it has been walked. Never in the
        # gate below, for `orient`'s reason and one of its own — a value that
        # is constant for a whole run must never be what keeps the bar alive.
        segments.append(census)
    siblings_chip = _siblings_chip(resources)
    if siblings_chip:
        segments.append(siblings_chip)
    keepalive_chip = _keepalive_chip(budget)
    if keepalive_chip:
        segments.append(keepalive_chip)
    delivery_chip = _delivery_chip(outbound)
    if delivery_chip:
        segments.append(delivery_chip)
    produce_total = _produce_total(produce)
    if produce_total:
        segments.append(f"⚒{produce_total}")
    # Beside the produce count, because they are the same fact in two tenses
    # (#1008). Gateless like `⚒`: an outstanding promise is an obligation,
    # but the *chip* is its ambient half and must not manufacture a boundary
    # by itself — the `owed` detail line below does that, once per change.
    owed_chip = promises.chip(plan) if plan is not None else None
    if owed_chip:
        segments.append(owed_chip)
    notices_chip = _notices_chip(notices or [])
    if notices_chip:
        segments.append(notices_chip)
    # Ambient, like the produce count: it never opens the gate by itself.
    # A run that just gated already has a boundary; what this buys is that
    # the verdict is on screen at *every* later boundary without a grep.
    gate_chip = _gate_chip(gate_receipt_data)
    if gate_chip:
        segments.append(gate_chip)
    if mood:
        # Display every boundary (it is the user's window onto the resident's
        # own face); *ask* only on an edge. The old unconditional "·keep?"
        # asked about the artifact — "is this label still the one you'd
        # write?" — which is answered yes for free and induces nothing. The
        # edge form asks nothing in words: it sets the mood the resident
        # claimed beside the thing that just went wrong, and lets the
        # mismatch do the work. Deictic, per the weave's own measure of a
        # mark — it points at what both parties just looked at.
        if surprise:
            segments.append(f"mood {_mood_chip(mood)} ← {surprise}")
        else:
            segments.append(f"mood {_mood_chip(mood)}")
    segments.append(_card_chip(card, card_stale))

    details: list[str] = []
    if pending:
        # Same framing fix as the prose form (2026-07-05): a bare count reads
        # as ambient telemetry, so non-zero pending gets an explicit verb —
        # applies *more* here, since a dense bar habituates faster than prose.
        details.append(
            f"{pending} pending event(s), {pending_files} undelivered outbox "
            "file(s). Address each below with an `event:` reply, or retire it "
            "deliberately with `note:`, before your next plan boundary or "
            "closeout."
        )
        details.extend(_render_event_rows(events, event_seen, inbox_pointer))
    if finished_spawns:
        # Finished spawns are facts, not obligations — the parent already
        # observed them; they will self-retire at run end. Reported as a
        # distinct line so the run body can name them without any "address each"
        # pressure.
        details.append(_finished_spawns_line(finished_spawns))
    details.extend(_render_armed_rows(armed))
    # The blueprint's obligation half. Latched on its own delta by the caller
    # (`plan_edge`), never rendered per boundary: an owed line that repeats
    # for as long as it stands is the *fires constantly for a non-reason*
    # death, and the chip above already carries the standing fact. It speaks
    # when a promise is made, when one is met or released, and at the
    # closeout — the three moments the number actually means something new.
    if plan is not None and plan_edge:
        owed = promises.owed_line(plan)
        if owed:
            details.append(owed)
    if budget.get("long_running"):
        limit = budget.get("budget_seconds")
        details.append(
            f"- running long: past the {limit}s soft budget — extend via "
            ".keepalive if the work needs it, else wind down."
        )
    elapsed = budget.get("elapsed_seconds")
    if not run_name.get("written") and isinstance(elapsed, (int, float)) and elapsed >= 240:
        details.append(
            "- .name: still unwritten — add a short resident-authored run name "
            "so the live dashboard can identify this work beyond its waking-message excerpt."
        )
    if card_stale:
        age = card.get("age_seconds")
        age_txt = f"{age}s" if age is not None else "a while"
        moved = card.get("state_moved_seconds")
        if card.get("active") and moved is not None:
            details.append(
                f"- card: the run moved {moved}s ago (produce, branch, "
                "delivery, or pending events) and .card hasn't been rewritten "
                f"since — it's {age_txt} old and now describes a different run."
            )
        else:
            details.append(
                f"- card: no change in {age_txt} — rewrite .card (even one "
                "line) so the surface the user is watching isn't sitting blank "
                "or stale."
            )

    resources_laden = bool(quota_chip or siblings_chip or keepalive_chip)
    any_delivery = bool(delivery_chip)
    # A blueprint edge opens the gate on its own, for `surprise`'s reason:
    # writing a promise changes nothing the daemon puts in portal-state, so
    # gating this on the portal token would leave the one boundary the
    # signal exists for rendering nothing.
    plan_laden = bool(plan is not None and plan_edge and plan.owed)
    # A mood edge is laden by definition: something the resident did just came
    # back wrong. Without this clause the caller's gate opens and this one
    # closes again — the ask would still be silent on exactly the boundary it
    # exists for, one layer past where the fix was aimed.
    if (
        pending == 0 and pending_files == 0 and not any_delivery
        and not resources_laden and not card_stale and not surprise
        and not notices_chip and not finished_spawns and not armed
        and not plan_laden
    ):
        return None
    bar = " │ ".join(segments)
    return bar + ("\n" + "\n".join(details) if details else "")


def _finished_spawns_line(finished_spawns: list[dict[str, Any]]) -> str:
    """Render the shared finished-spawn fact without inferring absent status."""
    count = len(finished_spawns)
    statuses = [
        str(event["spawn_status"]).strip()
        for event in finished_spawns
        if str(event.get("spawn_status") or "").strip()
    ]
    error_count = sum(status != "done" for status in statuses)
    ok_count = statuses.count("done")
    # An event that carries no status is not a healthy one — it is one whose
    # outcome was never determined, and that is the case this whole line
    # exists for. Folding it into the all-clear branch would restate #730's
    # defect in the one place nobody drives: "no address needed" is itself a
    # claim about outcome. It is reachable, too — every completion event
    # minted before this shipped has no ``spawn_status``, and daemon.py is
    # the restart-only liveness class, so that window is real.
    unknown_count = count - len(statuses)
    missing_reports = [
        event for event in finished_spawns
        if event.get("spawn_report_found") is False
    ]

    if not error_count and not missing_reports and not unknown_count:
        return (
            f"- ▷ {count} finished spawn(s) observed — "
            "no address needed; will retire at run end."
        )

    # The parts sum to the whole, always. A ledger whose columns do not add
    # up is how a reader learns to stop trusting the ledger (#683, one file
    # over), so every spawn is accounted for under exactly one term.
    counts: list[str] = []
    if ok_count:
        counts.append(f"{ok_count} ok")
    if error_count:
        counts.append(f"{error_count} error")
    if unknown_count:
        counts.append(f"{unknown_count} status unknown")

    noteworthy = [
        event for event in finished_spawns
        if not str(event.get("spawn_status") or "").strip()
        or event.get("spawn_status") != "done"
        or event.get("spawn_report_found") is False
    ]
    notes: list[str] = []
    for event in noteworthy:
        child_id = str(event.get("spawned_by_run") or event.get("id") or "-")
        note = child_id
        if event.get("spawn_report_found") is False:
            note += "; no report written"
        notes.append(note)

    summary = ", ".join(counts) if counts else "report missing"
    detail = f" ({'; '.join(notes)})" if notes else ""
    return f"- ▷ {count} finished spawn(s) — {summary}{detail}."


def format_delta(
    payload: dict[str, Any],
    *,
    seed: bool = False,
    stop: bool = False,
    run_body: str | None = None,
    mood: str | None = None,
    surprise: str | None = None,
    orient: tuple[int, int] | None = None,
    census: str | None = None,
    note_routing: bool = False,
    event_seen: dict[str, dict[str, Any]] | None = None,
    inbox_pointer: str | None = None,
    gate_receipt_data: dict[str, Any] | None = None,
    plan: "promises.Blueprint | None" = None,
    plan_edge: bool = False,
) -> str | None:
    """Render a compact context delta from the live portal-state payload.

    Short on purpose: it is woven into the agent's context every boundary,
    so it carries only what shifts attention — pending events, delivery
    acks, budget pressure — plus the run's compact attested produce briefing.

    Two boundaries render *unconditionally* (``seed`` and ``stop``) as
    affirmative, clear prose — never compressed into the bar (#513): the
    seed is the initial capsule, and the stop is the closeout capsule. At
    those moments an explicit "0 pending event(s)" is itself the signal —
    silence is ambiguous, an affirmative "all clear" is not (maintainer's
    point, 2026-06-23). Stop additionally surfaces the local SCM posture
    (unpushed commits / modified files) so a wake about to end sees its
    branch is not yet pushed.

    Mid-run (``post-tool``) renders as the single compact status bar
    :func:`_render_bar` builds — one line per boundary, working-register
    style, from the fixed :data:`BAR_SEGMENTS` vocabulary — with detail lines
    below it only for new obligations. It stays gated and returns ``None``
    when nothing shifted, so the channel injects no noise — except card
    staleness (2026-07-05) and non-zero pending events, which always earn a
    detail line: a stale-or-blank ``.card`` or an unaddressed follow-up is a
    mid-run failure, not one that can wait for closeout or be buried in a
    glyph.

    ``mood`` is the resident's own `.mood` control file (#566 layer 2), read
    fresh by the caller (:func:`_read_mood`) at every boundary — rendered as
    a bar segment mid-run, or its own prose line at seed/stop.

    ``orient`` is the orientation ledger's open value (#513 Slice 9),
    computed by the caller (:func:`_orientation_progress`) — a mid-run bar
    segment only, never seed/stop prose: the kernel already names the walk
    at seed, and by stop the walk is either done, skipped, or moot.

    ``census`` is the wake census (#739), computed by the caller
    (:func:`_wake_census`) off `boot-score.json` — a mid-run bar segment
    only, for `orient`'s reason: seed already names the boot's shape, and by
    stop the number is a week-old fact about a prompt nobody will re-read.

    ``note_routing`` is the gate-less routing fact's once-per-run latch
    (#728), owned by the caller for the same reason ``orient`` and
    ``surprise`` are: this renderer is a pure function of the portal
    snapshot, and "has this already been said" is run state, not snapshot
    state. See the delivery block below for why that fact is latched rather
    than gated.

    ``event_seen`` / ``inbox_pointer`` belong to the pending-event letter
    chrome (see that section above): the caller-owned per-boundary decision
    map (again run state, not snapshot state) and the path a resident's
    ``Read`` can open for a body too large to refeed inline. ``None`` for
    both keeps this a pure function of the snapshot — everything renders as
    a first appearance, and elided bodies point at ``inbox.json`` by name.

    ``gate_receipt_data`` is this run's ``.gate-receipt.json``, read by the
    caller for ``mood``'s reason — it is an outbox artifact, not part of the
    portal snapshot, and the resident rewrites it mid-run by gating again
    (#1048). Mid-run bar segment only: at seed there is never a receipt, and
    at stop :func:`_gate_closeout_clause` already speaks with the repo dir in
    hand and far more standing than a chip has.
    ``plan`` is the run's blueprint joined against its produce
    (:mod:`brr.promises`, #1008) — computed by the caller for ``orient``'s
    reason: it is read off ``.promises.jsonl``, which is not in the portal
    snapshot. ``plan_edge`` is its once-per-change latch, owned by the caller
    like ``note_routing``: mid-run the *chip* carries the standing fact every
    boundary and the *owed line* speaks only when the blueprint moves. At
    seed and stop the latch does not apply — the closeout is the moment the
    whole feature exists for.

    The armed dated-letters block (#904, :func:`_render_armed_rows`) reads
    straight off ``payload["schedule"]["armed"]`` — the daemon's own
    projection of still-pending ``at:`` entries — with no caller-owned
    state at all: unlike the letter chrome it is never seen-suppressed, so
    it re-shows at every boundary that shows anything else, which is the
    entire point (sweep by reading, not remembering).
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

    pending_files = int(attention.get("pending_outbox_file_count", 0) or 0)
    notices = payload.get("notices") if isinstance(payload.get("notices"), list) else []
    schedule_facet = (
        payload.get("schedule") if isinstance(payload.get("schedule"), dict) else {}
    )
    armed = (
        schedule_facet.get("armed")
        if isinstance(schedule_facet.get("armed"), list) else []
    )

    action_events, finished_spawns, action_pending = _partition_pending_events(
        payload
    )

    if not seed and not stop:
        card_stale = bool(card.get("stale"))
        run_name = payload.get("name") if isinstance(payload.get("name"), dict) else {}
        return _render_bar(
            run=run, pending=action_pending, pending_files=pending_files,
            events=action_events,
            budget=budget, outbound=outbound, produce=produce, card=card,
            card_stale=card_stale, resources=resources, run_name=run_name,
            mood=mood, surprise=surprise, orient=orient, census=census,
            notices=notices, finished_spawns=finished_spawns,
            event_seen=event_seen, inbox_pointer=inbox_pointer,
            armed=armed, gate_receipt_data=gate_receipt_data,
            plan=plan, plan_edge=plan_edge,
        )

    lines: list[str] = []
    # Only seed/stop reach this point — post-tool returned via `_render_bar`
    # above — so this is always one of the two verbose-prose headers.
    header = "brnrd portal seed" if seed else "brnrd portal closeout"
    # Framing, not just data: a bare count reads as ambient telemetry and
    # habituates fast — a maintainer caught this live (2026-07-05) when two
    # follow-ups sat unacknowledged on the outward-facing card for 8 minutes
    # despite the count appearing in every batch. Non-zero pending events get
    # an explicit action verb so the line reads as something to do, not
    # something to note; zero stays the plain affirmative-clear line.
    # Finished spawns are excluded from the obligation count — they are facts,
    # not messages with correspondents.
    header_line = (
        f"[{header}] {action_pending} pending event(s), "
        f"{pending_files} undelivered outbox file(s)."
    )
    if action_pending:
        header_line += (
            " Address each below with an `event:` reply, or retire it "
            "deliberately with `note:`, before your next plan boundary or "
            "closeout."
        )
    lines.append(header_line)
    lines.extend(_render_event_rows(action_events, event_seen, inbox_pointer))
    if finished_spawns:
        # Distinct fact line: not obligations, but visible to the parent.
        lines.append(_finished_spawns_line(finished_spawns))
        for ev in finished_spawns:
            summary = str(ev.get("summary") or "").strip()
            lines.append(
                f"  · {ev.get('id') or '-'}: {summary[:200]}"
            )
    lines.extend(_render_armed_rows(armed))
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
    # The blueprint, against the produce below it (#1008). At seed and stop
    # this is never latched: the closeout is the moment the whole feature
    # exists for, and a promise that went unmet has to be said *there* even
    # if it was already said mid-run — that is the difference between an
    # obligation and an ambient line, and it is why this sits outside the
    # content-dedupe that guards the ambient phases.
    #
    # Both directions render here, and only here. Mid-run the kept case is
    # silence, because "all kept" every boundary is noise; at the closeout it
    # is the receipt half of the same fact, and the resident is writing a
    # reply from this block.
    if plan is not None and plan.any_promises:
        owed = promises.owed_line(plan)
        if owed:
            lines.append(owed)
        elif stop:
            lines.append(
                "- blueprint: every promise this run made is in its manifest."
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
        # At the closeout boundary the compression is the wrong shape. The
        # resident is writing a receipt *from* this list — naming the commits,
        # linking the PR, saying what the run made — and a count line makes it
        # reconstruct from memory what the daemon already knows exactly
        # (maintainer, 2026-07-19: "make the live accrued relics useful for
        # you too... inspected as you go to maintain the focus"). This is the
        # resident's rendering of the node's own `## Produce` section: same
        # records, both faces of one run.
        records = produce.get("records")
        if stop and isinstance(records, list) and records:
            manifest = [
                f"  {relics.icon(str(r.get('kind') or ''))} {relics.label(r)}"
                + (f" — {r['url']}" if r.get("url") else "")
                for r in records[:_STOP_MANIFEST_MAX_RECORDS]
                if isinstance(r, dict) and relics.label(r).strip()
            ]
            if manifest:
                overflow = len(records) - len(manifest)
                lines.append(
                    "- your produce this run (the manifest this node carries, "
                    "and what a receipt should name):\n" + "\n".join(manifest)
                    + (f"\n  … and {overflow} more" if overflow > 0 else "")
                )
    # Affirmative-empty: an *addressed* run reaching closeout with nothing
    # communicated anywhere is suspicious, not silent — surface the absence at
    # the boundary, before the slot is gone. A warn, not a requirement: the
    # daemon dispatches the run's terminal stream to the waking thread on its
    # own (2026-07-16 ceremony cut), so the resident is never asked to
    # re-deliver through the outbox what its final message already carries —
    # that ask is what produced double-posts.
    #
    # Two gates, and the second one is the whole point (#562).
    #
    # ``inbound.current_event`` — the warning names a fact about the waking
    # thread, so a run with no waking event has nothing here to be wrong
    # about.
    #
    # ``inbound.current_event_replyable`` — the daemon's mechanical answer to
    # "can a reply addressed to this event actually be delivered?", computed
    # from the same ownership predicate the router uses. A schedule wake DOES
    # carry a current event (the schedule evt id), so the first gate passes;
    # but no gate owns ``schedule`` events, the router refuses ``event:``
    # replies to them, and ``replied_current`` therefore stays 0 for the life
    # of the run. Gating only on the first check made the reply nag
    # un-clearable — it re-fired at every boundary, hardest at the runs that
    # had already delivered on telegram. A guard may only assert something
    # the run can be proven wrong about; the moment it nags about a chore
    # that cannot be done, it teaches the reader to skip the channel, and it
    # is gone the one night it is right.
    if stop and inbound.get("current_event"):
        replyable = inbound.get("current_event_replyable")
        # Absent key ⇒ an older/partial portal state: keep the historical
        # addressed-run behavior rather than inventing a gate-less run.
        gate_less = replyable is False
        if not any_delivery:
            lines.append(
                "- delivery: nothing communicated on any thread yet — no "
                "gate owns this waking event, so nothing dispatches your "
                "final message: it is captured to the response path as this "
                "run's body/message store only. Report on a configured user "
                "gate (`gate: telegram`) if this run has something to say. A "
                "run that ends silent everywhere is surfaced as a failure."
                if gate_less else
                # #743. This used to end "end on the reply itself (no outbox
                # re-delivery needed)" — orientation that actively taught
                # reliance on the static dispatch, which is the thing being
                # measured. The fallback still works and the line still says
                # so; what it no longer does is recommend it.
                "- delivery: nothing communicated on any thread yet — the "
                "daemon's fallback net dispatches your final message to the "
                "waking thread when this run ends, and records the run as "
                "`terminal_route: gate-sole` for having been carried by it. "
                "That still reaches the reader; a run that ends silent "
                "everywhere does not, and is surfaced as a failure."
            )
        elif not replied_current and not gate_less:
            # Gate-less runs can never clear this: the router refuses
            # ``event:`` replies to an unowned source, so silence is the
            # success state once anything was delivered anywhere.
            lines.append(
                "- delivery: the waking thread itself has no reply yet — "
                "your final message will be dispatched there by the daemon; "
                "end on the reply, not on scratch."
            )
        elif gate_less and note_routing:
            # The fourth cell (#728). ``gate_less`` was computed above and
            # then consumed only inside the silence arm, so the one fact that
            # actually loses content went unsaid in the one case that loses
            # it: a gate-less run that pinged a gate mid-run and then wrote
            # its real closeout to stdout, where it stages
            # ``status: undeliverable`` and nobody reads it.
            #
            # Two things had to be got right, and the first is a correction
            # to the obvious fix.
            #
            # *Not* "warn unless a gate delivery exists." ``outbound_messages``
            # already IS the gate-delivery count — daemon.py's ``stats``
            # writes ``outbound`` in exactly one place (the ``gate:`` branch)
            # and only when ``_deliver_out_of_bound`` returns True, so an
            # unconfigured gate does not count. Discriminating on it would
            # therefore go quiet on precisely the runs this exists for: the
            # mid-run ping is what sets it, and the closeout is lost anyway.
            # The loss does not depend on prior delivery, so neither may the
            # statement.
            #
            # Which makes it unconditional under ``gate_less`` — and an
            # unconditional line at a recurring boundary is the #562 trap
            # wearing new clothes. The escape is not a cleverer clearing
            # condition; there isn't one, because this is the run's topology
            # and not a chore. It is that a constant carries its whole
            # payload the first time it is read: latched to once per run by
            # the caller, and phrased to say outright that it is a fact and
            # not something owed. Note the token gate above would NOT have
            # been enough — ``outbound`` is part of ``change_token``, so every
            # gate delivery re-renders the briefing, and an unlatched line
            # would nag hardest at the runs delivering most. That is #562's
            # exact signature.
            lines.append(
                "- delivery: routing fact, stated once — no gate owns this "
                "waking event, so nothing dispatches your final message "
                "however much has already gone out: stdout is captured to "
                "the response path as this run's body/message store only. "
                "Content the reader must see rides a `gate: telegram` "
                "delivery, and the closeout is not exempt. Not a chore — "
                "this is the run's topology and cannot be cleared."
            )
    # Notices are the directives brnrd *refused or dropped*. Until now their
    # only surface was the post-tool bar's ``!N`` count — and that path
    # returns at the ``not seed and not stop`` branch above, so on the two
    # verbose boundaries ``notices`` was read into a local and then never
    # rendered at all. Including Stop: the last boundary at which a dropped
    # reply can still be re-routed said nothing about the drop.
    #
    # A count is the wrong shape even where it does render. A refused outbox
    # file is deleted exactly like an accepted one, so the count is the only
    # trace that survives, and turning it into a fact costs a
    # ``portal-state.json`` open the resident has to remember to make — a
    # polling tax on the one class of event that cannot be recovered
    # afterwards. Measured on this account 2026-07-25: 178 undeliverable
    # outbound messages / 287,638 B, 115 of them mid-run interims, each in a
    # run that closed reporting zero undelivered outbox files. The largest
    # single loss was a 10,229 B analysis, staged and gone two minutes before
    # its run's closeout claimed nothing was outstanding.
    #
    # Text, not classification — that *was* true here, and it was the split
    # #1002 closed: sorting loss-notices from config-warnings by matching
    # their prose was the renderer guessing at something only the writer
    # knew. A ``kind:`` field now rides the record itself (daemon.py:5765),
    # written at the source that actually knows whether a directive was
    # refused, dropped, or merely advisory — so the header count below
    # counts only the first two, exactly like the bar's ``!N`` chip
    # (``_notices_chip``, same filter, ``_counted_notices``). Rendering the
    # full text stays: it is still the half that is live in the very next
    # hook subprocess, and it is what makes a *standing* notice legible even
    # once it stops driving the count.
    #
    # #1002's ``kind`` field alone was not enough. This account carries a
    # standing notice permanently (a repo-side ``runners.md`` that is
    # ignored by design) — but it is genuinely ``kind="refused"``/
    # ``"dropped"`` (brnrd really did not honour the input); retagging it
    # ``advisory`` to keep it out of the count would make the chip honest by
    # making the record lie about what happened. #716's fix is a second,
    # orthogonal field: ``lifetime``. A ``"standing"`` record is excluded
    # from the count below for the same *reason* as an advisory (FYI, not a
    # fresh failure to act on) without pretending it is one — so the header
    # now names the two exclusions separately, and a reader sees "standing"
    # on the environmental notice rather than the wrong-severity "advisory".
    # Four words of the text still tell a reader "that one again" from
    # "that one is new"; the count alone cannot, which is why it habituated
    # before either field existed.
    if (seed or stop) and notices:
        shown = notices[-_NOTICE_LINES:]
        counted = len(_counted_notices(notices))
        advisory_extra = sum(
            1
            for n in notices
            if isinstance(n, dict)
            and n.get("lifetime") != "standing"
            and n.get("kind") == "advisory"
        )
        standing_extra = sum(
            1
            for n in notices
            if isinstance(n, dict) and n.get("lifetime") == "standing"
        )
        head = f"- notices: {counted} directive(s) brnrd refused or dropped"
        extras = []
        if advisory_extra:
            extras.append(f"+{advisory_extra} advisory")
        if standing_extra:
            extras.append(f"+{standing_extra} standing")
        if extras:
            head += f" ({', '.join(extras)})"
        if stop:
            head += (
                " — a refused outbox file is deleted exactly like an accepted"
                " one, so this is the last boundary that can re-route one"
            )
        lines.append(head + ":")
        for record in shown:
            text = " ".join(str(record.get("text") or "").split())
            if len(text) > _NOTICE_TEXT_CAP:
                text = text[: _NOTICE_TEXT_CAP - 1].rstrip() + "…"
            if text:
                kind = record.get("kind") if isinstance(record, dict) else None
                lifetime = (
                    record.get("lifetime") if isinstance(record, dict) else None
                )
                # Only "standing" earns a place in the label — it's the
                # surprising case (an environmental fact, not this run's own
                # directive) and the one #716 needs legible; "run" is the
                # default for nearly every record and would just be noise.
                label_bits = [b for b in (kind,) if b]
                if lifetime == "standing":
                    label_bits.append("standing")
                label = f"[{' · '.join(label_bits)}] " if label_bits else ""
                lines.append(f"  · {label}{text}")
        if len(notices) > len(shown):
            lines.append(
                f"  · (+{len(notices) - len(shown)} older — "
                "`portal-state.json` → `notices` for the full list)"
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
    # A name is useful while the run is still visible, not as a closeout
    # chore. Give the resident a few minutes to orient, then gently surface
    # the omission at ordinary hook boundaries; Stop is deliberately quiet.
    run_name = payload.get("name") if isinstance(payload.get("name"), dict) else {}
    elapsed = budget.get("elapsed_seconds")
    if not stop and not run_name.get("written") and isinstance(elapsed, (int, float)) and elapsed >= 240:
        lines.append(
            "- .name: still unwritten — add a short resident-authored run name "
            "so the live dashboard can identify this work beyond its waking-message excerpt."
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
        moved = card.get("state_moved_seconds")
        if card.get("active") and moved is not None:
            # Name the movement, not the clock. The nudge now fires only when
            # a fact the card would report has changed since the card was
            # written, so it can say what the card is behind *on* — which is
            # also the difference between a forcing function and a nag you
            # learn to silence with a cosmetic edit.
            lines.append(
                f"- card: the run moved {moved}s ago (produce, branch, "
                "delivery, or pending events) and .card hasn't been rewritten "
                f"since — it's {age_txt} old and now describes a different run."
            )
        else:
            lines.append(
                f"- card: no change in {age_txt} — rewrite .card (even one "
                "line) so the surface the user is watching isn't sitting blank "
                "or stale."
            )
    # The run's own body, at closeout only (maintainer, 2026-07-19: "run's own
    # body on stop - right, that's what I actually meant").
    #
    # Stop is the moment `.card` is captured as the node's permanent `body.md`
    # — the run writing its own record. It is also the moment the resident is
    # least able to write it: on a long run the *earliest* card text is the
    # first thing to fall out of context, so the record gets finalised against
    # a memory of itself rather than the thing itself. The body is already in
    # this payload for the live card; handing it back here costs one render at
    # the one boundary where it is the working material.
    #
    # Deliberately not the prior run's body (the first shape proposed): that
    # one is one `Read` away and the wake's heading list says whether it is
    # worth opening. This one has no fallback — gone from context is just gone.
    if stop:
        # Read from the artifact, never the heartbeat snapshot — the same
        # doctrine the closeout obligations keep. A card rewritten in the run's
        # final action predates no portal write, and handing back a stale body
        # at the exact moment it becomes permanent is the failure this whole
        # block exists to prevent.
        body = (run_body if run_body is not None else str(card.get("text") or "")).strip()
        if body:
            if len(body) > _STOP_BODY_MAX_CHARS:
                # Keep the tail: the run's latest thinking is the part the
                # closeout is being written from, and the head is the part
                # most likely still in context.
                body = "…\n" + body[-_STOP_BODY_MAX_CHARS:]
            lines.append(
                "- your run body (`.card`, captured as this node's body.md at "
                "closeout — the whole arc, not the live projection):\n"
                + textwrap.indent(body, "  ")
            )
    # Work-status posture (cost / quota / parallelism). Known fields carry
    # their value; not-yet-built ones read as named states with reasons so the
    # resident sees the slot honestly rather than a gap.
    if resources:
        rendered = _format_resources(resources)
        if rendered:
            lines.append(rendered)
    # The resident's own `.mood` (#566 layer 2): unconditional prose here,
    # same as the rest of this seed/stop block — the bar-style "·keep?"
    # segment is the mid-run shape (`_render_bar`); this is its plain-prose
    # twin so the invitation to reconsider still lands at the two boundaries
    # that stay verbose on purpose.
    if mood:
        lines.append(
            f"- mood: {_mood_chip(mood)} — the face you're wearing on the "
            "run node right now. Read it against the last few turns, not "
            "against the file: a mood worth showing is one the work moved."
        )
    # Seed and stop always render — their empty state ("0 pending") is the
    # affirmative signal, not noise (this function only reaches here for
    # those two boundaries; post-tool returns earlier via `_render_bar`).
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


# ── The vigil claim (#947): "Holding…" from a run that then exits ────────
#
# run-260801-1247-mivp ended on `Holding for the integration gate.` and then
# finished cleanly; run-260801-1430-1sur ended on `continuing — … vigil armed
# (keepalive live…)` with no `.keepalive` on disk at all. Both times the
# maintainer read a live vigil and waited on a run that was already `done`. The
# prose contract exists (`weave`: the last line is a bare state that must be
# true) and a runner reliably drops it, so it goes down the escalation ladder to
# the Stop boundary like every other obligation here.
#
# The matcher is prose over a resident-authored surface, so it obeys the
# playbook rule a matcher on this kind of surface has to: **blank the code
# first**. A run that *documents* this guard writes ``continuing — …`` inside a
# fence, and a matcher blind to code spans reads the example as the thing —
# the #562 false-positive shape, one module over (`_orientation_set_paths`).
_CODE_FENCE_RE = re.compile(r"(?:```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# The bare-state line, anchored at the *start* of the final line. `run.md` /
# `weave.md` define that line as one of done / continuing / blocked, so
# `continuing` there is the strong signal — and `done` / `blocked` there are the
# honest closes this guard must never touch.
_VIGIL_STATE_RE = re.compile(
    r"^(?:[-*>]\s*|#{1,6}\s*)*(?:\*\*|__)?(done|continuing|blocked)\b",
    re.IGNORECASE,
)
# The prose claims, matched only on a final line that ends on *no* recognised
# state — where the phrase is the only evidence there is. These are claim
# phrases, not topic words: bare ``holding`` and ``vigil`` describe ordinary
# work too ("holding the value constant", "the vigil design"). Each accepted
# phrase promises a *later* message from this run.
_VIGIL_PHRASE_RE = re.compile(
    r"\b(holding for|vigil (?:armed|live)|waiting on|will report back|"
    r"standing by(?: for)?)\b",
    re.IGNORECASE,
)


def _blank_code(reply: str) -> str:
    """*reply* with fenced blocks dropped and inline code spans blanked.

    Same fence-toggle idiom as :mod:`brr.card`'s section reader. Blanking rather
    than deleting the span keeps the surrounding words apart, so `` `continuing`
    `` in prose cannot fuse two words into a phrase that was never written.
    """
    kept: list[str] = []
    fenced = False
    for line in reply.replace("\r\n", "\n").split("\n"):
        if _CODE_FENCE_RE.match(line.strip()):
            fenced = not fenced
            continue
        if fenced:
            continue
        kept.append(_INLINE_CODE_RE.sub(" ", line))
    return "\n".join(kept)


def vigil_claim(reply: str) -> str | None:
    """The continuation *reply* claims — ``continuing`` or the phrase used — or
    ``None`` when it claims none.

    Conservative by construction, in three steps:

    1. **Code is blanked** (see above), so an example is never the thing.
    2. **The final line decides.** Its bare state wins outright. An earlier
       state in the closeout tail cannot classify a different final line —
       inheriting it is how ``continuing — that was the plan`` above an honest
       conclusion became a false hard block.
    3. **`done` / `blocked` / a fork are never a claim**, whatever else the line
       says. Those are the complementary honest closes the ticket names as
       always legal, and a guard that second-guesses them would be arguing with
       the contract rather than enforcing it.
    """
    text = _blank_code(reply)
    line = ""
    for candidate in reversed(text.split("\n")):
        if candidate.strip():
            line = candidate.strip()
            break
    if not line:
        return None
    match = _VIGIL_STATE_RE.match(line)
    state = match.group(1).lower() if match else None
    if state == "continuing":
        return "continuing"
    if state is not None:
        return None  # done / blocked / fork — an honest close
    phrase = _VIGIL_PHRASE_RE.search(line)
    return phrase.group(1).lower() if phrase else None


def _keepalive_armed(ctx: "HookContext") -> bool:
    """True when a **live** ``.keepalive`` sits in this run's outbox.

    Read fresh from disk at Stop, not from the heartbeat portal snapshot: the
    keepalive that arms a vigil is written in the run's final actions, which is
    exactly the window the snapshot cannot see (the same reason
    :func:`_closeout_artifact_written` reads files rather than the portal).

    Present is not enough — a keepalive whose deadline has passed is a lapsed
    vigil, and the daemon already stops honouring it (``_keepalive_state``
    calls that ``expired``). Same parse as the budget extension, from the one
    shared reader, so the guard can never accept a file the daemon would not.
    """
    if ctx.outbox_dir is None:
        return False
    until = portals.keepalive_until(ctx.outbox_dir / portals.KEEPALIVE_NAME)
    return until is not None and until > time.time()


def _spawn_child_armed(portal: dict[str, Any], run_id: str | None) -> bool | None:
    """Whether a ``spawn:`` child owned by *run_id* is still running.

    ``True`` / ``False`` / ``None`` — and the third state is load-bearing. The
    fact comes from the presence registry, which the daemon projects into
    portal-state as the ``coexisting_runs`` facet: every live participant, minus
    this run, each carrying the ``parent_run_id`` it was registered with
    (``daemon`` → ``presence.register``). A concurrent spawn child is precisely a
    live entry whose parent is this run.

    The facet's own three-state honesty is why ``None`` exists. ``known`` and
    ``absent`` are both *reads* of the registry, so their silence is evidence;
    ``unimplemented`` means no presence collector was wired at that call site,
    and absence of evidence there is not evidence of absence. Nor is it, when
    the hook was handed no run id to match on. In both cases the caller must
    stay silent rather than block a run whose child it simply could not see.
    """
    resources = portal.get("resources") if isinstance(portal, dict) else None
    facet = (resources or {}).get("coexisting_runs")
    facet = facet if isinstance(facet, dict) else None
    if facet is None or facet.get("status") not in ("known", "absent"):
        return None
    if not run_id:
        return None
    siblings = facet.get("siblings")
    for entry in siblings if isinstance(siblings, list) else []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("parent_run_id") or "").strip() == run_id:
            return True
    return False


def _vigil_closeout_clause(
    ctx: "HookContext", payload: dict[str, Any], portal: dict[str, Any]
) -> str | None:
    """The `vigil` closeout obligation: a continuation claim with nothing armed.

    Assertable only from artifacts, and silent whenever one of them cannot be
    read — the doctrine every clause in this module keeps:

    - **no reply handed over** (codex today) ⇒ silent. The claim is *in the
      reply*; there is nothing else to read it from.
    - **no continuation claim** ⇒ silent. `done — receipt` and `blocked — whose
      move` are always legal and are the whole complementary half of this rule.
    - **either arming present** ⇒ silent. One live continuation is all the
      claim promised.
    - **spawn ownership unreadable** ⇒ silent (see :func:`_spawn_child_armed`).

    What is left is the defect: the reply promises a later message, and no
    mechanism exists that could send one. The block names both armings it
    looked for, because when it fires both were missing and "which one" is the
    first thing the resident needs in order to fix it.
    """
    reply = payload.get("last_assistant_message")
    if not isinstance(reply, str) or not reply.strip():
        return None
    claim = vigil_claim(reply)
    if claim is None:
        return None
    if _keepalive_armed(ctx):
        return None
    spawned = _spawn_child_armed(portal, ctx.run_id)
    if spawned is None or spawned:
        return None
    where = (
        f"`{ctx.outbox_dir / portals.KEEPALIVE_NAME}`" if ctx.outbox_dir
        else f"`{portals.KEEPALIVE_NAME}`"
    )
    return (
        f"your last line claims an ongoing state ({claim!r}) and nothing is "
        f"armed to keep it — no live {where} (absent, or its deadline already "
        f"passed) and no running `spawn:` child of this run. A background shell "
        f"command is not a continuation: its completion cannot re-enter a run "
        f"that has already emitted its terminal stream. So either arm one now "
        f"(write the keepalive — one line, `+30m` or an ISO deadline — and "
        f"spend the wait in-thought) or end on a state that is true as you "
        f"exit: `done — <receipt>` or `blocked — <whose move + what's needed>`"
    )


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
    # Name the `gate: forge` route only when the daemon told us it can
    # actually deliver on this account (`ctx.forge_gate`) — a guard may only
    # point at a door it has been told opens. Off (or unset) ⇒ the generic
    # instruction, no route that may not exist.
    handoff = (
        "hand off the branch (`gate: forge`)" if ctx.forge_gate
        else "open the PR yourself"
    )
    return (
        f"the work isn't landed — {detail}. A host checkout publishes nothing "
        f"on its own; commit, push, and {handoff} before ending"
    )


def _untracked_digest(repo: Path) -> str:
    """Content digest of every untracked, non-ignored file in `repo`.

    Delegates to :func:`brr.gate_receipt.untracked_digest` — the writer's own
    implementation — rather than keeping a second copy here. Two
    implementations of one rule is the shape where copies agree and are wrong
    together (#722); this guard and the receipt writer now share the one
    computation, pinned by a test that drives both sides against the same
    repository.
    """
    return gate_receipt.untracked_digest(repo)


def _gate_closeout_clause(ctx: "HookContext") -> str | None:
    """The `gate` closeout obligation: a run changed the tree and never ran the
    project's own CI gate on *this* tree.

    The failure this exists for is not laziness, it is arithmetic. A resident
    runs the test command it remembers — `pytest` — and reports green, while CI
    defines four legs in two working directories. The rule *"run what CI runs"*
    was written into this account's standing memory and broken twice within an
    hour by its author; a project-level instruction (`AGENTS.md`) sits in a
    38 KB file a wake is explicitly allowed to skip. Prose in a bigger, more
    skippable place is not the escalation rung, it is the same rung repainted.
    The rung is: **make forgetting checkable**, then check it at the one moment
    the claim is about to ship.

    Guard doctrine, clause by clause — this may only assert what an artifact
    proves, and it is silent everywhere else:

    - **no declared gate** (`hooks.gate_command` unset) ⇒ silent. brr does not
      know what a stranger's gate is and never guesses one.
    - **git unreadable** ⇒ silent, like `_scm_closeout_clause`.
    - **nothing changed** ⇒ silent. The referent for *is a gate owed* is CI's
      own trigger: `ci.yml` filters no paths, so anything this run changed is
      something CI will run on. A repo whose CI is path-filtered wants that
      filter read, not a list maintained here — noted as the next honest
      narrowing, not faked now.
    - **receipt present and matching** ⇒ silent, *including when it is RED*.
      The obligation is that the gate ran on this tree, never that it passed:
      a run may end red and report it. A run that never looked is the defect.
    - **receipt matching but `tree_moved_during_gate`** ⇒ block, with its own
      sentence. Three states, not two: *never ran* · *ran, then you edited* ·
      *ran, and you edited while it ran*. The third used to read as the
      honest path, because both writers sampled the tree only after the last
      leg and the comparison here recomputed that same end state (#917). A
      remedy aimed at the wrong cause is worse than none, so each state says
      what actually happened.
    - **receipt without the field** ⇒ unassertable on stillness, silent.
      Written by a writer too old to sample a "before"; absence is not
      evidence of a moved tree.
    """
    if not ctx.gate_command:
        return None
    repo = ctx.repo_dir
    if repo is None or not repo.exists():
        return None
    head = _git_out(repo, ["rev-parse", "HEAD"])
    status = _git_out(repo, ["status", "--porcelain"])
    diff = _git_out(repo, ["diff", "HEAD"])
    if head is None or status is None or diff is None:
        return None

    # Did this run change anything at all? Commits beyond the seed, a dirty
    # tree, or commits the remote has not seen. The third is not redundant: a
    # host run that commits straight onto `main` moves the seed with it, so
    # `merge-base(main, HEAD) == HEAD` and the first count reads zero on a run
    # that changed plenty.
    seed = ctx.seed_ref or "HEAD"
    base = seed
    merge_base = _git_out(repo, ["merge-base", seed, "HEAD"])
    if merge_base and merge_base.strip():
        base = merge_base.strip()
    commits = 0
    count = _git_out(repo, ["rev-list", "--count", f"{base}..HEAD"])
    if count and count.strip().isdigit():
        commits = int(count.strip())
    unpushed = 0
    ahead = _git_out(repo, ["rev-list", "--count", "@{upstream}..HEAD"])
    if ahead is not None and ahead.strip().isdigit():
        unpushed = int(ahead.strip())
    if commits == 0 and unpushed == 0 and not status.strip():
        return None
    changed = max(commits, unpushed)

    # `_read_json` collapses absent, unreadable and malformed into `{}`. That
    # collapse is deliberate here rather than sloppy: all three mean *no
    # trustworthy record that the gate ran*, and the direction this claim is
    # allowed to be wrong in is the pessimistic one.
    receipt = _read_json(
        ctx.outbox_dir / GATE_RECEIPT_NAME if ctx.outbox_dir else None
    )
    if not receipt:
        return (
            f"the gate never ran — {changed} commit(s) and a changed tree, no "
            f"`{ctx.gate_command}` receipt. The test command you remember is "
            f"one leg of what CI runs; run `brnrd gate-run` (it runs "
            f"`{ctx.gate_command}` and writes this receipt) before claiming green"
        )
    stale = (
        receipt.get("head") != head.strip()
        or receipt.get("status") != status
        or receipt.get("diff_digest")
        != hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()
        or receipt.get("untracked_digest", "") != _untracked_digest(repo)
    )
    if stale:
        return (
            f"the gate ran on a different tree than the one you are ending on "
            f"(receipt: {str(receipt.get('verdict') or '?')} at "
            f"{str(receipt.get('head') or '?')[:8]}). Re-run `brnrd gate-run` "
            f"(runs `{ctx.gate_command}`) — a green verdict for a tree you "
            f"have since edited is a claim about code nobody ran"
        )
    # Third state, and the reason it is checked *after* `stale`: the tree the
    # receipt describes is the tree you are ending on, so everything above is
    # satisfied — and the receipt still does not certify it, because it moved
    # under the gate while the gate was running (#917). This case used to be
    # silent. Absent field ⇒ still silent: a receipt from a writer that never
    # sampled a "before" is unassertable, not guilty, and every receipt
    # written before that field existed looks exactly like one.
    if receipt.get("tree_moved_during_gate"):
        return (
            f"the gate ran, and the tree moved under it while it ran — "
            f"{gate_receipt.moved_sentence(receipt, ctx.gate_command)}. The "
            f"receipt's {str(receipt.get('verdict') or '?')} is about the tree "
            f"as it was before that change. Re-run `brnrd gate-run` now that "
            f"the tree is still"
        )
    return None


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


#: How many closeout blocks one run may spend, whatever it says between them
#: (#981). Three is a bound, not a budget: a resident that has been handed the
#: unmet capsule twice and still ends on a bad closeout is not going to be
#: argued into a good one on the fourth pass, and #282 is the scar that says
#: an unbounded guard costs more than the miss it is chasing.
_CLOSEOUT_BLOCK_CAP = 3


def _closeout_reply_key(payload: dict[str, Any]) -> str:
    """A stable key for the reply this Stop is closing on (#981).

    Whitespace-normalised and hashed rather than stored whole — the hook state
    file is re-read at every boundary and a full reply would bloat it for no
    added discrimination. A Shell that hands over no reply (codex today) keys
    every Stop identically, so such a run keeps exactly the old one-block
    behaviour: with nothing to compare, "the claim changed" is unassertable,
    and the guard doctrine here is silence over a guess.
    """
    reply = payload.get("last_assistant_message")
    text = " ".join(reply.split()) if isinstance(reply, str) else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _armed_closeout_block(
    ctx: "HookContext",
    payload: dict[str, Any],
    state: dict[str, Any],
    portal: dict[str, Any] | None = None,
) -> str | None:
    """The closeout guard: block when Stop is reached with a named obligation
    still unmet, listing every unmet one in a single capsule.

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
    proves.** next-move reads the reply (``last_assistant_message``); vigil
    reads the same reply against the run's own ``.keepalive`` and the presence
    projection; the file obligations read their control file fresh. An
    obligation whose artifact cannot be read is silent — never a nag on a
    proxy, the bug class this repo spent the week killing.

    *portal* is the snapshot ``compute_neutral`` already read this boundary,
    passed in rather than re-read: the Stop flush handshake refreshes it just
    before, so it is this boundary's view of who is alive, not a stale one.

    **The latch is on the claim, not on the run (#981).** It used to be one
    bit — blocked once, silent forever after — and `run-260802-0001-9qgz`
    is what that costs. The vigil clause caught a false ``continuing`` at
    00:21:45Z (its first live fire, and it was right); the run then worked
    thirty more minutes, crossed fourteen more Stop boundaries, delivered
    three messages, and ended at 00:51:37Z on *another* false ``continuing``
    — unblocked, because the one bit was spent. The work it promised to
    report sat unpushed on an unnamed branch until the next tick found it.

    The bit was answering two questions with one value: *have I already
    nagged **this** miss* (the #282 loop, worth breaking) and *have I ever
    nagged **anything*** (what it stored). And the exposure ran backwards —
    the runs that spend the latch earliest are the long ones, which are
    exactly the runs still holding something unfinished at the end.

    So: keyed on the reply that was blocked. The same reply, presented again,
    is the #282 loop and stays silent; a reply the resident rewrote and
    re-submitted is a new claim and re-evaluates. ``_CLOSEOUT_BLOCK_CAP``
    bounds the exchange anyway, because "the resident keeps producing bad
    closeouts" must terminate on a number rather than on good intentions —
    #282 is the scar that says so. ``payload["stop_hook_active"]`` remains the
    within-turn breaker, independent of both.
    """
    blocked_for = state.get("closeout_blocked_for")
    reply_key = _closeout_reply_key(payload)
    if blocked_for is not None and blocked_for == reply_key:
        return None
    if int(state.get("closeout_blocks") or 0) >= _CLOSEOUT_BLOCK_CAP:
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

    # Second, and beside it deliberately: next-move asks whether the reply ends
    # on a state at all, this asks whether the state it ends on is *true*. Both
    # read the same artifact, so they belong in the same breath.
    if "vigil" in ctx.closeout_obligations:
        vigil_clause = _vigil_closeout_clause(ctx, payload, portal or {})
        if vigil_clause:
            unmet.append(vigil_clause)

    for name in _CLOSEOUT_ARTIFACT_ORDER:
        if name in ctx.closeout_obligations:
            filename, clause = _CLOSEOUT_ARTIFACTS[name]
            if not _closeout_artifact_written(ctx, filename):
                unmet.append(clause)

    # SCM is not a file-existence check but a fresh-git computation, so it
    # lives outside the artifact loop. Last in the capsule: the reply-shape and
    # the control files come first, the land-the-work imperative closes it.
    # Same shape as `scm` — a fresh-git computation, not a file-existence
    # check. Ordered *before* it: "you never ran the gate" is a claim about the
    # work being right, and it should be read before "now land it".
    if "gate" in ctx.closeout_obligations:
        gate_clause = _gate_closeout_clause(ctx)
        if gate_clause:
            unmet.append(gate_clause)

    if "scm" in ctx.closeout_obligations:
        scm_clause = _scm_closeout_clause(ctx)
        if scm_clause:
            unmet.append(scm_clause)

    if not unmet:
        return None

    state["closeout_blocked_for"] = reply_key
    state["closeout_blocks"] = int(state.get("closeout_blocks") or 0) + 1
    return _render_closeout_capsule(unmet)


# ── Stop fold-in (verbatim, framed as the user's words) ──────────────────


def _first_pending_event(events: list[Any]) -> dict[str, Any] | None:
    """The first foldable pending event (one carrying a body), or None."""
    for ev in events:
        if isinstance(ev, dict) and str(ev.get("body") or "").strip():
            return ev
    return None


def _fold_in_message(
    event: dict[str, Any],
    decision: dict[str, Any] | None = None,
    inbox_pointer: str | None = None,
) -> str:
    """Frame a newly-arrived event under an honest relay label.

    The 2026-06-26 spike found framing is load-bearing: a coercive daemon
    interrupt is perceived but *refused* (correct injection defense), while
    the same content relayed as the user's genuine words is acted on. So the
    Stop block carries the event body under a neutral, non-imperative relay
    header — not an operational summary. Honest per source, though: a
    schedule firing is the entry's own spec, not the user speaking, and
    labelling it "from the user" was defect 3 of the letter-chrome rework
    (see that section above) — the user-follow-up framing stays only for
    sources where a human actually wrote the words.

    Same letter policy and seen-suppression as the pending list: a short
    body still lands verbatim, a huge one lands as first line + accounting
    (defect 2 was this very path refeeding a ~10 KB spec every Stop), and an
    already-shown unchanged body collapses to the one-line seen form.
    """
    source = str(event.get("source") or "user").strip() or "user"
    status = (decision or {}).get("status") or "new"
    shown = int((decision or {}).get("shown") or 0)
    if status == "seen":
        return (
            f"{_event_seen_line(event, shown)} — still pending: reply with "
            f"`event: {event.get('id') or '<id>'}`, or retire it deliberately "
            f"with `note: {event.get('id') or '<id>'}`."
        )
    if source == "schedule":
        label = "(schedule firing folded in — the entry's spec, not a user message:)"
    else:
        label = f"(folded-in follow-up from the user via {source}:)"
    body = str(event.get("body") or "").strip()
    size = len(body.encode("utf-8", "replace"))
    header = _event_header(event, size=size, changed=status == "changed")
    body_block = "\n".join(
        _event_body_block(body, size, inbox_pointer, indent="")
    )
    return f"{label}\n\n{header}\n\n{body_block}"


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
    _ack_previous_inject(state, phase)
    _record_fired(state, phase)
    inject: str | None = None
    block = False
    block_reason: str | None = None
    # The pending-event seen ledger (letter chrome, see that section): decide
    # once per boundary how each event renders, off the persisted per-run
    # state — hooks are fresh subprocesses, so this is the only memory the
    # suppression has. Decisions here, commit at the bottom, and only for
    # events a render actually carried.
    portal_inbound = (
        portal.get("inbound") if isinstance(portal.get("inbound"), dict) else {}
    )
    portal_events = (
        portal_inbound.get("events")
        if isinstance(portal_inbound.get("events"), list) else []
    )
    event_decisions = _event_seen_decisions(state, portal_events)
    folded_event_id: str | None = None
    # Where a resident's Read can open a body too large to refeed inline:
    # the daemon-maintained live inbox view beside the outbox — always
    # mounted into the run environment, unlike the inbox event file itself.
    inbox_pointer = (
        str(ctx.outbox_dir / portals.LIVE_INBOX_NAME)
        if ctx.outbox_dir is not None else None
    )
    # Read fresh at every boundary (#566 layer 2), same "artifact, not a
    # cached copy" doctrine as `.card` — the resident may rewrite `.mood`
    # between hook fires, and the whole point is that the face rendered here
    # is the face the resident actually just set.
    mood = _read_mood(ctx)
    # Re-read every boundary, like `.mood` and `.card`: a resident gates
    # more than once in a long run, and a cached verdict is exactly the
    # stale claim this chip exists to make visible. `_read_json` collapses
    # absent/unreadable/malformed to `{}`, and the chip renders nothing for
    # all three — a run that has not gated is not a run that failed.
    gate_receipt_data = _read_json(
        ctx.outbox_dir / GATE_RECEIPT_NAME if ctx.outbox_dir else None
    )
    # The blueprint (#1008), read fresh for `.mood`'s reason: the resident
    # writes `.promises.jsonl` between hook fires and the point is that the
    # line rendered here reflects what it just claimed. Joined against the
    # produce counts the daemon already computed, so nothing re-derives them.
    #
    # `plan_edge` is the once-per-change latch. It is *not* a content check:
    # an owed line and an ambient bar are byte-identical when nothing moved,
    # and content dedupe eats an unmet obligation exactly as fast as noise
    # (#818/#963). Keying on the blueprint's own token makes the line speak
    # when a promise is made, met, or released — and stay quiet in between,
    # where the `owed N` chip carries the standing fact for seven characters.
    portal_produce = (
        portal.get("produce") if isinstance(portal.get("produce"), dict) else {}
    )
    produce_counts = (
        portal_produce.get("counts")
        if isinstance(portal_produce.get("counts"), dict) else {}
    )
    plan = promises.blueprint(promises.read(ctx.outbox_dir), produce_counts)
    plan_token = promises.token(plan)
    plan_edge = plan.any_promises and plan_token != state.get("plan_token")
    state["plan_token"] = plan_token

    if phase == PHASE_SESSION_START:
        inject = format_delta(
            portal, seed=True, mood=mood,
            event_seen=event_decisions, inbox_pointer=inbox_pointer,
            plan=plan,
        )
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
        # #728: latch, don't gate. ``format_delta`` is a pure function of the
        # snapshot, so "already said once" has to be decided here — same
        # division of labour as ``orient`` and ``surprise``.
        gate_less_run = _stop_is_gate_less(portal)
        note_routing = gate_less_run and not state.get(GATELESS_ROUTING_KEY)
        if stop_token != state.get("stop_last_token"):
            inject = format_delta(
                portal, stop=True, run_body=_read_card_body(ctx), mood=mood,
                note_routing=note_routing,
                event_seen=event_decisions, inbox_pointer=inbox_pointer,
                plan=plan,
            )
            # Latch on the render, not on the decision: a Stop whose token
            # did not move injects nothing, and burning the one statement on
            # a boundary the resident never saw is how a once-per-run signal
            # becomes a never-per-run one. Both gate-less arms of the
            # delivery block state the routing fact — the silence arm has
            # always carried it — so a rendered gate-less closeout means it
            # was said, whichever arm said it.
            if gate_less_run:
                state[GATELESS_ROUTING_KEY] = True
        state["stop_last_token"] = stop_token
        state["last_token"] = stop_token
    else:
        # A mood *edge*: something in the batch that just ran came back
        # wrong. Transition-stamped, not per-pass — the same discipline a
        # commit inside a retry loop needs. A run debugging a red test would
        # otherwise be told "something broke" at every boundary of the
        # debugging, which is the habituation this whole change exists to
        # avoid; the interesting moment is clean → broken, once.
        surprise = _tool_surprise(payload) if mood else None
        was_surprised = bool(state.get("mood_surprised"))
        edge = surprise if (surprise and not was_surprised) else None
        state["mood_surprised"] = bool(surprise)
        # The orientation ledger (#513 Slice 9): observe this batch's Reads
        # against the score's orientation set — unconditionally, because the
        # observation is Slice 4's instrument and must not depend on whether
        # a bar happens to render this boundary. The returned value is the
        # segment's, and only when the walk is still open.
        orient = _orientation_progress(ctx, payload, state)
        # The wake census (#739): a pure read of the score the daemon wrote
        # before this runner started. Computed here rather than inside the
        # renderer for the same reason `orient` is — `format_delta` stays a
        # function of the portal snapshot, and the score is not in it.
        census = _wake_census(ctx)
        token = portal.get("change_token")
        # An edge opens the gate on its own. Gating it on the portal token
        # would be a contract the signal can't keep: a failing tool call
        # changes nothing the daemon writes into portal-state, so the one
        # boundary the ask exists for is exactly the one that would render
        # nothing.
        # A blueprint edge opens the gate on its own, for the mood edge's
        # reason: writing `.promises.jsonl` changes nothing the daemon puts
        # into portal-state, so gating on the portal token alone would leave
        # the one boundary this signal exists for rendering nothing.
        if token is not None and (
            token != state.get("last_token") or edge or plan_edge
        ):
            inject = format_delta(
                portal, mood=mood, surprise=edge, orient=orient, census=census,
                event_seen=event_decisions, inbox_pointer=inbox_pointer,
                gate_receipt_data=gate_receipt_data,
                plan=plan, plan_edge=plan_edge,
            )
            state["last_token"] = token

    # The portal token decides whether rendering is worth attempting; exact
    # rendered content decides whether the runner has anything new to see.
    # Apply this after every phase has built its complete block, and before
    # the seen ledger records what was actually delivered.
    #
    # **Ambient phases only, and that boundary is the whole correctness
    # argument.** #818 measured the defect on a *worker's post-tool* channel
    # — 26 byte-identical status bars in eighteen minutes — and named the
    # real answer as splitting the channel, because "the statusline and the
    # actual obligations share one channel". Content dedupe cannot tell the
    # two apart: an ambient bar repeats because nothing *happened*, while an
    # obligation repeats because nothing was *done* about it, and
    # byte-identical is the signature of the second as much as the first.
    # The closeout render is where the obligations live (the gate-less
    # delivery warning clears itself the moment the run delivers — see
    # ``format_delta``'s ``any_delivery`` arm), so it keeps its own
    # ``stop_last_token`` gate and is never content-suppressed. This is the
    # cheapest seam the split already has; the finer per-block split stays
    # #818's open residue.
    if phase != PHASE_STOP:
        inject = _suppress_unchanged_inject(state, inject)

    if phase == PHASE_STOP:
        action_events, _finished_spawns, action_pending = (
            _partition_pending_events(portal)
        )
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
        if action_pending > 0 and state.get("stop_blocked_token") != stop_token:
            block = True
            event = _first_pending_event(action_events)
            if event is not None:
                # Fold the waiting follow-up in — the resident addresses it
                # in this same thought. Honest label and letter policy per
                # `_fold_in_message`; the seen ledger applies here too, so an
                # already-shown unchanged body costs one line, not a refeed.
                folded_event_id = str(event.get("id") or "") or None
                block_reason = _fold_in_message(
                    event,
                    event_decisions.get(folded_event_id or ""),
                    inbox_pointer,
                )
            else:
                block_reason = (
                    f"{action_pending} pending event(s) are still waiting — "
                    "reply with `event: <id>`, or retire deliberately with "
                    "`note: <id>`, before ending. Read inbox.json for the "
                    "complete event bodies."
                )
            state["stop_blocked_token"] = stop_token

        # The closeout guard, second in line and deliberately so: a user's waiting
        # message outranks the shape of a reply that is about to be rewritten
        # anyway. Only when nothing is pending does "how does this reply end"
        # become the last question of the run.
        if not block:
            reason = _armed_closeout_block(ctx, payload, state, portal)
            if reason is not None:
                block = True
                block_reason = reason

    # Commit the seen ledger for exactly what this boundary rendered: every
    # rendered delta (bar, seed, closeout) carries the whole pending list, so
    # a non-None inject shows all of them; a fold-in shows its one event even
    # on a boundary whose delta gate stayed shut. An unreadable/absent portal
    # commits nothing — wiping the ledger on a bad read would re-arm a full
    # refeed of every body the resident has already seen.
    if portal:
        shown_ids: set[str] = set(event_decisions) if inject is not None else set()
        if folded_event_id:
            shown_ids.add(folded_event_id)
        _commit_event_seen(state, event_decisions, shown_ids)

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
# argv (:func:`codex_hook_args`).
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
    # Record the *neutral* result, not the rendered native JSON: the neutral
    # shape is the one thing every Shell flavour shares, so a transcript
    # written from here reads the same whether the run was claude or codex.
    record_boundary(ctx, phase, neutral)
    return render_native(ctx.flavour, phase, neutral)


def record_boundary(
    ctx: HookContext, phase: str, neutral: dict[str, Any]
) -> Path | None:
    """Append this boundary to the run's transcript. Best-effort, never raises.

    What a run *is*, as context, is the wake plus every hook injection after
    it. The daemon has always captured the first half (`prompt.md`) and never
    the second, so the only readable record of a run's environment stopped at
    t=0 — and the boundaries are where the environment actually talks: the
    portal delta, the pending-event nag, a Stop block and its reason. Anyone
    reasoning about what a runner saw needed both halves and could only get
    one.

    Deliberately unconditional rather than dev-flagged. The wake capture is
    unconditional; making its other half opt-in would mean the runs worth
    inspecting are exactly the ones nobody armed it for. It is bounded
    instead (:data:`_BOUNDARIES_MAX_BYTES`) — and the bound announces itself,
    because a transcript that just stops reads as a run that went quiet.
    """
    directory = ctx.run_dir
    if directory is None or not directory.is_dir():
        return None
    path = directory / BOUNDARIES_NAME
    try:
        if path.exists() and path.stat().st_size >= _BOUNDARIES_MAX_BYTES:
            return None
    except OSError:
        return None
    record = {
        "at": _utc_now_iso(),
        "phase": phase,
        "inject": neutral.get("inject"),
        "block": bool(neutral.get("block")),
        "block_reason": neutral.get("block_reason"),
    }
    try:
        line = json.dumps(record, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            if handle.tell() >= _BOUNDARIES_MAX_BYTES:
                handle.write(
                    json.dumps(
                        {
                            "at": _utc_now_iso(),
                            "phase": phase,
                            "truncated": True,
                            "inject": (
                                f"boundary transcript capped at "
                                f"{_BOUNDARIES_MAX_BYTES} bytes — later "
                                f"boundaries in this run were not recorded"
                            ),
                            "block": False,
                            "block_reason": None,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
    except OSError:
        return None
    return path


# ── Boundary summary (the run node's `boundaries.json`) ──────────────────
#
# `boundaries.jsonl` is complete but linear: reading it for one fact (did the
# closeout guard ever fire? did the run end over a live block?) means
# scrolling a whole run's transcript. The wake's "## Your last run" block
# (`prompts._build_prior_run_block`) projects the previous run's node — its
# frame, its `## Now`, its section shape — and until now that projection
# dropped the one field that says whether the closeout guard AGREED with the
# reply it is showing: a run that ended on a false "continuing" claim (the
# guard fired, and the run ended anyway) reads identical to a clean close.
# This is the derivation half — pure and read-only over the transcript
# `record_boundary` already wrote, no schema change to it. The write half
# (deciding *where* the summary lands) is `daemon._persist_boundaries_summary`;
# the render half (turning it into the wake's one `guard:` line) is
# `prompts._guard_line`.

#: Sibling of `BOUNDARIES_NAME`, same run-node directory.
BOUNDARIES_SUMMARY_NAME = "boundaries.json"


def derive_boundaries_summary(path: Path) -> dict[str, Any] | None:
    """Summarise *path* (a run's `boundaries.jsonl`) into a small flat dict.

    ``None`` — never a zero-valued summary — whenever the transcript cannot
    be trusted: absent, unreadable, or every line in it failed to parse.
    That is deliberate and pessimistic: a missing `boundaries.json` must stay
    distinguishable from one honestly reporting "nothing happened", and the
    only way to keep that true is to never emit the latter from data this
    thin. In practice a real transcript always carries at least the
    unconditional `session-start` record, so an all-malformed file is
    corruption, not an ordinary empty run.

    Malformed individual lines — a line torn by a crash mid-write, a
    dict missing the `phase` field — are skipped and counted, not fatal:
    `record_boundary`'s own append is best-effort, so its reader tolerates
    exactly the damage the writer already tolerates in itself.

    The "guard fire" this counts is the only signal the transcript can prove:
    a `phase: stop` record with `block: true`. `compute_neutral` has two
    sources for that bit at Stop — the pending-event fold-in and
    `_armed_closeout_block` — and `record_boundary` does not carry which one
    fired, only the outcome. Rather than guess from `block_reason`'s prose
    (an assertion-on-a-proxy this codebase specifically avoids elsewhere),
    this counts every Stop block as a guard verdict: the defect this feature
    answers is "did the run end over a live objection", not "which clause
    raised it", and that question the bare `block` bit already answers
    honestly. The final stop's own verdict — the fact the accepted defect is
    about — is the last `phase: stop` record in file order, blocked or not.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    total = 0
    skipped = 0
    stops = 0
    guard_fires: list[dict[str, Any]] = []
    final_stop: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(record, dict) or not isinstance(record.get("phase"), str):
            skipped += 1
            continue
        total += 1
        at = record.get("at")
        at = at if isinstance(at, str) else None
        block = bool(record.get("block"))
        if record["phase"] != PHASE_STOP:
            continue
        stops += 1
        block_reason = record.get("block_reason")
        final_stop = {
            "at": at,
            "block": block,
            "block_reason": block_reason if isinstance(block_reason, str) else None,
        }
        if block:
            guard_fires.append({"at": at, "blocked": True})

    if total == 0:
        return None

    return {
        "total": total,
        "skipped": skipped,
        "stops": stops,
        "guard_fire_count": len(guard_fires),
        "guard_fires": guard_fires,
        "final_stop_at": final_stop["at"] if final_stop else None,
        "final_stop_block": final_stop["block"] if final_stop else False,
        "final_stop_block_reason": (
            final_stop["block_reason"] if final_stop else None
        ),
    }


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
