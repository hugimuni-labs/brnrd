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
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import facets
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
    """Fold this batch's `Read` calls into the ledger; return the read count.

    Matches on the resolved absolute path of each `Read`'s ``file_path`` —
    claude's ``PostToolBatch`` hands calls over as ``{tool_name, tool_input,
    ...}``. A runner whose payload carries no ``tool_calls`` (codex's
    ``PostToolUse`` shape differs) simply observes nothing: the ledger under-
    counts there rather than inferring, and the segment quietly never
    completes — which the skip declaration resolves, same as prior knowledge.
    Persisted in `.hook-state.json` under :data:`ORIENTATION_READ_KEY`, and
    pruned to the current set so a stale path can never inflate the count.
    """
    read = {
        p for p in (state.get(ORIENTATION_READ_KEY) or [])
        if isinstance(p, str) and p in set_paths
    }
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
            if resolved in set_paths:
                read.add(resolved)
    state[ORIENTATION_READ_KEY] = sorted(read)
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
        "the live `.card` surface's own health: `ok` / `stale` / `blank`. "
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
    if card_stale:
        return "card stale"
    return "card ok" if card.get("active") else "card blank"


def _notices_chip(notices: list) -> str | None:
    """``!N`` when *notices* is non-empty; absent at zero.

    A refused outbox file is deleted exactly like an accepted one — the only
    thing between a dropped reply and silence is a resident habitually opening
    ``portal-state.json``.  ``!N`` on the bar makes a non-zero refusal count
    visible without demanding that read.  Absent at zero so it earns its ink
    the same way every other differential segment does.
    """
    n = len(notices) if isinstance(notices, list) else 0
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
    notices: list[Any] | None = None,
    finished_spawn_count: int = 0,
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
    *finished_spawn_count* is the number of ``spawn_completed`` events the
    parent already observed; they are facts, not obligations, and are
    reported separately rather than counted against *pending*.
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
    notices_chip = _notices_chip(notices or [])
    if notices_chip:
        segments.append(notices_chip)
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
            "file(s). Address each below — fold in, or say on .card why it "
            "stays queued — before your next plan boundary or closeout."
        )
        for ev in events:
            if not isinstance(ev, dict):
                continue
            summary = str(ev.get("summary") or "").strip()
            details.append(
                f"- pending {ev.get('id') or '-'} ({ev.get('source') or '-'}): "
                f"{summary[:200]}"
            )
    if finished_spawn_count:
        # Finished spawns are facts, not obligations — the parent already
        # observed them; they will self-retire at run end. Reported as a
        # distinct line so the run body can name them without any "address each"
        # pressure.
        details.append(
            f"- ▷ {finished_spawn_count} finished spawn(s) observed — "
            "no address needed; will retire at run end."
        )
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
    # A mood edge is laden by definition: something the resident did just came
    # back wrong. Without this clause the caller's gate opens and this one
    # closes again — the ask would still be silent on exactly the boundary it
    # exists for, one layer past where the fix was aimed.
    if (
        pending == 0 and pending_files == 0 and not any_delivery
        and not resources_laden and not card_stale and not surprise
        and not notices_chip and not finished_spawn_count
    ):
        return None
    bar = " │ ".join(segments)
    return bar + ("\n" + "\n".join(details) if details else "")


def format_delta(
    payload: dict[str, Any],
    *,
    seed: bool = False,
    stop: bool = False,
    run_body: str | None = None,
    mood: str | None = None,
    surprise: str | None = None,
    orient: tuple[int, int] | None = None,
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
    events = inbound.get("events") if isinstance(inbound.get("events"), list) else []
    notices = payload.get("notices") if isinstance(payload.get("notices"), list) else []

    # Partition pending events into obligations vs finished-spawn facts.
    # spawn_completed events whose spawn_parent_run_id matches this run are
    # facts — the parent observed them; they will self-retire at run end.
    # They must not count toward the obligation total or demand an "address".
    run_id = str(run.get("id") or "")
    finished_spawns = [
        e for e in events
        if isinstance(e, dict)
        and e.get("source") == "spawn_completed"
        and e.get("spawn_parent_run_id") == run_id
    ] if run_id else []
    action_events = [e for e in events if e not in finished_spawns]
    action_pending = max(0, pending - len(finished_spawns))

    if not seed and not stop:
        card_stale = bool(card.get("stale"))
        run_name = payload.get("name") if isinstance(payload.get("name"), dict) else {}
        return _render_bar(
            run=run, pending=action_pending, pending_files=pending_files,
            events=action_events,
            budget=budget, outbound=outbound, produce=produce, card=card,
            card_stale=card_stale, resources=resources, run_name=run_name,
            mood=mood, surprise=surprise, orient=orient,
            notices=notices, finished_spawn_count=len(finished_spawns),
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
            " Address each below — fold in, or say on .card why it stays "
            "queued — before your next plan boundary or closeout."
        )
    lines.append(header_line)
    for ev in action_events:
        if not isinstance(ev, dict):
            continue
        summary = str(ev.get("summary") or "").strip()
        lines.append(
            f"- pending {ev.get('id') or '-'} ({ev.get('source') or '-'}): "
            f"{summary[:200]}"
        )
    if finished_spawns:
        # Distinct fact line: not obligations, but visible to the parent.
        lines.append(
            f"- ▷ {len(finished_spawns)} finished spawn(s) observed — "
            "no address needed; will retire at run end."
        )
        for ev in finished_spawns:
            summary = str(ev.get("summary") or "").strip()
            lines.append(
                f"  · {ev.get('id') or '-'}: {summary[:200]}"
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
                "- delivery: nothing communicated on any thread yet — the "
                "daemon dispatches your final message to the waking thread "
                "when this run ends, so end on the reply itself (no outbox "
                "re-delivery needed). A run that ends silent everywhere is "
                "surfaced as a failure."
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
    # Read fresh at every boundary (#566 layer 2), same "artifact, not a
    # cached copy" doctrine as `.card` — the resident may rewrite `.mood`
    # between hook fires, and the whole point is that the face rendered here
    # is the face the resident actually just set.
    mood = _read_mood(ctx)

    if phase == PHASE_SESSION_START:
        inject = format_delta(portal, seed=True, mood=mood)
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
            inject = format_delta(
                portal, stop=True, run_body=_read_card_body(ctx), mood=mood
            )
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
        token = portal.get("change_token")
        # An edge opens the gate on its own. Gating it on the portal token
        # would be a contract the signal can't keep: a failing tool call
        # changes nothing the daemon writes into portal-state, so the one
        # boundary the ask exists for is exactly the one that would render
        # nothing.
        if token is not None and (token != state.get("last_token") or edge):
            inject = format_delta(portal, mood=mood, surprise=edge, orient=orient)
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
