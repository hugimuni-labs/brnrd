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

from . import assignments
from . import card as card_rule
from . import course
from . import facets
from . import gate_receipt
from . import portals
from . import promises
from . import protocol
from . import relics
from . import run_ledger

PHASE_POST_TOOL = "post-tool"
PHASE_STOP = "stop"
PHASE_SESSION_START = "session-start"
# #1184: the fourth phase, and a different shape from the other three — those
# compute an *injection* (portal delta, closeout briefing); this one computes
# a *permission decision* on one tool call, before it runs. See
# `_rooted_write_neutral` and `run_hook`'s early branch for it.
PHASE_PRE_TOOL = "pre-tool"
PHASES = (PHASE_POST_TOOL, PHASE_STOP, PHASE_SESSION_START, PHASE_PRE_TOOL)

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

ACT_LABELS = ("orient", "probe", "mutate", "publish", "dispatch", "wait")


def classify_act(tool_name: object, tool_input: object) -> str:
    """Return one coarse effect label without retaining the tool input."""
    name = tool_name.strip().lower() if isinstance(tool_name, str) else ""
    compact_name = name.replace("-", "_").replace(".", "_")
    input_path = ""
    input_body = ""
    if isinstance(tool_input, dict):
        for key in ("file_path", "path"):
            value = tool_input.get(key)
            if isinstance(value, str):
                input_path = value.lower()
                break
        for key in ("content", "text", "patch"):
            value = tool_input.get(key)
            if isinstance(value, str):
                input_body = value.lower()
                break

    if any(part in compact_name for part in ("write", "edit", "patch")):
        if input_path.endswith("/.keepalive"):
            return "wait"
        if "/outbox/" in input_path:
            if re.search(r"(?:spawn|respawn|to|stop):\s*", input_body):
                return "dispatch"
            if not Path(input_path).name.startswith("."):
                return "publish"
        return "mutate"
    if any(part in compact_name for part in ("spawn", "dispatch", "steer", "stop_agent")):
        return "dispatch"
    if any(part in compact_name for part in ("await", "sleep", "keepalive")):
        return "wait"
    if any(part in compact_name for part in ("publish", "push", "send_message", "create_pr")):
        return "publish"
    if any(part in compact_name for part in (
        "delete", "remove", "rename", "move", "imagegen",
    )):
        return "mutate"
    if any(part in compact_name for part in (
        "read", "grep", "glob", "find", "list", "view", "open", "status", "diff", "log",
    )):
        return "orient"

    if compact_name in {"bash", "shell", "exec", "exec_command", "functions_exec"}:
        command = ""
        if isinstance(tool_input, dict):
            value = tool_input.get("command", tool_input.get("cmd", ""))
            if isinstance(value, str):
                command = value
        command = command.strip().lower()
        if re.search(r"(?:^|[;&|]\s*)(?:brnrd\s+)?await(?:\s|$)", command) or re.search(
            r"(?:^|[;&|]\s*)sleep(?:\s|$)", command
        ) or ".keepalive" in command:
            return "wait"
        if re.search(r"(?:spawn|respawn|to|stop):\s*", command):
            return "dispatch"
        if re.search(
            r"\bgit\s+push\b"
            r"|\bgh\s+pr\s+(?:create|edit|merge|comment)\b"
            r"|\bgh\s+issue\s+(?:create|comment|close|edit)\b",
            command,
        ):
            return "publish"
        # brnrd's own speech acts. Without these the single most important
        # question a register bench can ask — *did this wake say anything to
        # anyone* — falls through to the shell default and reads as `probe`,
        # which is the one misclassification that would make the field look
        # like it varies while staying blind to the axis it exists for.
        # `brnrd do` only counts when it carries a delivery flag: `--mood` /
        # `--card` / a bare status read change nothing outside this machine.
        if re.search(
            r"\bbrnrd\s+do\b(?=.*--(?:gate|reply)\b)"
            r"|\bbrnrd\s+cut\b"
            r"|\b(?:x-browser|x-post|x-intent|bsky-post)\.py\s+(?:post|send|reply)\b",
            command,
        ):
            return "publish"
        if re.search(r"\b(?:curl|wget)\b.*(?:--request|-x)\s*(?:post|put|patch|delete)\b", command):
            return "publish"
        if re.search(
            r"\b(?:git\s+(?:add|commit|merge|rebase|switch|checkout|restore|reset|clean)|"
            r"apply_patch|mkdir|touch|rm|mv|cp|install)\b|(?:^|\s)(?:>>?|2>)\s*\S+",
            command,
        ):
            return "mutate"
        if re.search(
            r"\b(?:pytest|npm\s+(?:test|run)|cargo\s+test|go\s+test|scripts/gate\.py)\b",
            command,
        ):
            return "probe"
        if re.search(
            r"\b(?:git\s+(?:status|diff|log|show|branch|rev-parse)|gh\s+(?:issue|pr|run)\s+"
            r"(?:view|list)|rg|grep|find|ls|sed\s+-n|head|tail|cat)\b",
            command,
        ):
            return "orient"
        return "probe"

    if any(part in compact_name for part in ("test", "run", "fetch", "search", "request", "query")):
        return "probe"
    return "probe"


# ── Tool-detail extraction for boundary records ──────────────────────────
#
# Boundaries now carry a ``detail`` field: a short, redacted summary of
# *what* the tool did (command line for Bash, path for file tools, brief
# input summary for anything else).  ``out_bytes`` records the byte count of
# the tool's response without retaining the response text itself.
#
# Redaction is the care point: a command line can carry API keys, auth tokens,
# and env-var assignments.  The patterns below err toward over-masking rather
# than under-masking, per the design doc's boot-receipt rule (safe exact
# facts, never credential values).

# Patterns where group 1 is the key/prefix and the value follows (redact
# value, preserve key for context).
_SECRET_PATTERNS_KEYED: list[re.Pattern[str]] = [
    # Authorization: Bearer TOKEN  /  Cookie: session=VALUE
    # Value runs to the next quote character or end of line (covers multi-word
    # schemes like "Bearer TOKEN" and "Basic BASE64BLOB").
    re.compile(r"(?i)((?:Authorization|Cookie)\s*:\s*)[^'\"\n]+"),
    # token=VALUE  secret=VALUE  password=VALUE  api_key=VALUE  etc.
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
        r"token|secret|password|passwd|credential|auth)\s*[=:]\s*)\S+"
    ),
    # --token VALUE  --key VALUE  --secret VALUE  --password VALUE  etc.
    re.compile(
        r"(?i)(--(?:token|key|secret|password|api-key|apikey|auth-token)\s+)\S+"
    ),
]

# Patterns where the whole match is the credential (no useful prefix to keep).
_SECRET_PATTERNS_WHOLE: list[re.Pattern[str]] = [
    # JWT: three base64url-encoded segments separated by dots.
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    # Known API-key prefixes (Anthropic, GitHub, GitLab, Slack, AWS).
    re.compile(
        r"(?:sk-ant-|sk-[a-zA-Z0-9]{2}-|ghp_|ghs_|gho_|glpat-|"
        r"xoxb-|xapp-|xoxa-|xoxe-|xoxp-|xoxr-|AKIA)[A-Za-z0-9_\-]{8,}"
    ),
]

_REDACT_PLACEHOLDER = "<redacted>"

#: Max bytes retained for the Bash command detail field.
_DETAIL_BASH_MAX = 500
#: Max bytes retained for non-Bash tool detail summaries.
_DETAIL_OTHER_MAX = 200


def redact_detail(text: str) -> str:
    """Mask secret-bearing values in a command-line or summary string.

    Over-masks rather than under-masks — a value that *looks* like a token is
    masked even if it might not be one.  The ``detail`` field is diagnostic,
    not operational: losing a few non-secret values is cheaper than leaking
    one real credential.
    """
    for pattern in _SECRET_PATTERNS_KEYED:
        text = pattern.sub(lambda m: m.group(1) + _REDACT_PLACEHOLDER, text)
    for pattern in _SECRET_PATTERNS_WHOLE:
        text = pattern.sub(_REDACT_PLACEHOLDER, text)
    return text


def _tool_detail(tool_name: object, tool_input: object) -> str | None:
    """Return a short redacted detail string for one tool call, or ``None``.

    Never retains raw ``tool_input`` — only a derived, human-readable summary
    that has been passed through :func:`redact_detail`.
    """
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None
    name_lower = tool_name.strip().lower().replace("-", "_").replace(".", "_")
    if not isinstance(tool_input, dict):
        return None

    # Bash and shell-equivalent tools: show the command line.
    if name_lower in ("bash", "shell", "exec", "exec_command", "functions_exec"):
        cmd = tool_input.get("command") or tool_input.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            return None
        cmd = re.sub(r"\s+", " ", cmd.strip())
        raw = cmd.encode("utf-8")
        if len(raw) > _DETAIL_BASH_MAX:
            cmd = raw[:_DETAIL_BASH_MAX].decode("utf-8", errors="replace") + "…"
        return redact_detail(cmd)

    # File-oriented tools: show the path/pattern.
    for key in ("file_path", "path", "pattern", "glob"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # Agent/dispatch tools: show a short task summary.
    for key in ("task", "prompt", "description", "query"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            summary = re.sub(r"\s+", " ", val.strip())
            if len(summary) > _DETAIL_OTHER_MAX:
                summary = summary[:_DETAIL_OTHER_MAX] + "…"
            return redact_detail(summary)

    # Generic fallback: first non-empty string value.
    for val in tool_input.values():
        if isinstance(val, str) and val.strip():
            summary = re.sub(r"\s+", " ", val.strip())
            if len(summary) > _DETAIL_OTHER_MAX:
                summary = summary[:_DETAIL_OTHER_MAX] + "…"
            return redact_detail(summary)

    return None


def _response_bytes(response: object) -> int:
    """Return the byte count of a tool response without retaining its content."""
    if response is None:
        return 0
    if isinstance(response, (bytes, bytearray)):
        return len(response)
    if isinstance(response, str):
        return len(response.encode("utf-8"))
    try:
        return len(json.dumps(response).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


# The gate-less routing fact (#728) is true for the whole life of a gate-less
# run and can never be cleared, so it is said once and then remembered here —
# the one line in the closeout briefing that is a constant rather than an
# obligation. Everything else there (scm, card staleness, pending events) goes
# quiet when the resident acts, and so re-renders freely.
GATELESS_ROUTING_KEY = "gateless_routing_noted"
# The Stop "no reply yet" delivery line's consecutive-firing streak (#1142):
# same idiom as REPEAT_COUNTS_KEY below, but scoped to a single Stop-only
# line rather than the post-tool bar's four compressible details, because
# its due-ness and its reset condition are both Stop-specific (see
# `_stop_no_reply_due`). Incremented every Stop firing the line is due,
# reset to 0 the moment it isn't (``replied_current`` turns true, the run
# goes gate-less, or nothing is pending) — persisted because hooks are
# fresh subprocesses each boundary.
STOP_NO_REPLY_STREAK_KEY = "stop_no_reply_streak"
# The waking event id the streak above was last counted against — a streak
# must restart at a new waking thread (a fresh follow-up event replacing
# the one that never got a reply is not the same unanswered wait
# continuing) rather than keep climbing across unrelated events.
STOP_NO_REPLY_EVENT_KEY = "stop_no_reply_event"
# #1296 belt-and-suspenders: once the escalated (post-threshold, verdict-
# form) "no reply yet" line has rendered once for the waking event above, it
# has said everything it has to say — repeating the same verdict on every
# later Stop firing while the streak keeps climbing is exactly the
# unbounded renag #1296 measured live (7 firings, one waking event, a
# schedule-thread run whose `spawn_parent_run_id` outlived its own
# dispatching parent). This latch is deliberately independent of
# `_stop_is_gate_less`/`current_event_replyable`'s own correctness — the
# root cause is fixed at the source in daemon.py
# (`_spawn_parent_still_collecting`), but a streak that has already reached
# the escalation threshold once is, by construction, never going to un-ring
# that bell, so capping it here costs nothing even when the upstream read is
# right, and is the net under the floor for when some future bug makes it
# wrong again. Resets alongside the streak at a new waking event.
STOP_NO_REPLY_ESCALATED_KEY = "stop_no_reply_escalated"
# (The `mood?`/`topic?`/`.name?` nudge family and its latch/counter keys
# retired 2026-08-20 — w-69, `design-the-ignition-assignments.md`: the
# nudges became the claims assignment, whose ledger lives in
# `brr.assignments` and whose state sits under `assignments.STATE_KEY` in
# this same hook state, escalation-laddered instead of latched.)
#
# Whether `.mood` has ever read non-empty this run. `_read_mood` answers
# "does it have content *right now*", and a resident could in principle
# clear or delete the file after writing it — the claims assignment's
# discharge is "a write happened", not "the file happens to be non-empty
# this boundary", so the fact has to survive a mood that later reads blank.
MOOD_EVER_WRITTEN_KEY = "mood_ever_written"
# Three-class boundary split (#1116): ambient vitals render on the first
# post-tool boundary and then only when a threshold crosses, never every tick.
# The keys below are the per-run ambient-state ledger entries.
#
# AMBIENT_FIRST_BAR_KEY — True after the first post-tool bar has been emitted.
# AMBIENT_BUDGET_PCT_KEY — budget % used at the last ambient emission (int).
# AMBIENT_QUOTA_KEY — {bucket_label: pct_left} at the last ambient emission.
AMBIENT_FIRST_BAR_KEY = "ambient_first_bar"
AMBIENT_BUDGET_PCT_KEY = "ambient_budget_pct"
AMBIENT_QUOTA_KEY = "ambient_quota"
# BAR_LAST_CHIPS_KEY — {segment_key: rendered_text} at the last boundary whose
# bar actually rendered (w-54 change-gating): a chip whose text is unchanged
# since then is not news and does not render again. Committed only on render,
# so a suppressed boundary cannot mark a change as seen.
BAR_LAST_CHIPS_KEY = "bar_last_chips"
# COURSE_DRIFT_COUNT_KEY / WORK_TOKEN_KEY — the course drift trigger (w-54):
# count work-deltas (produce / delivery / gate movement) landing while the
# route stands still; at _COURSE_DRIFT_THRESHOLD the course line re-surfaces
# once ("the run moved, the route didn't") and the counter re-arms. Endogenous
# — keyed to evidence of divergence, not to a clock or a message.
COURSE_DRIFT_COUNT_KEY = "course_drift_count"
WORK_TOKEN_KEY = "work_token"
_COURSE_DRIFT_THRESHOLD = 3
# COURSE_STALL_COUNT_KEY — boundaries elapsed since the course last moved
# (route_edge=True resets; a new pending event does not). At
# _COURSE_STALL_THRESHOLD a "stalled ×N" detail line fires once and the
# counter re-arms. Distinct from drift: drift counts *work* moves with no
# route edit; stall counts *boundaries* with no route edit regardless of
# whether work moved.
COURSE_STALL_COUNT_KEY = "course_stall_count"
_COURSE_STALL_THRESHOLD = 10
# The mood's own drift (his ask, evt-…-mhrx: "we should do the mood
# staleness detection and ask to touch the face file at least"): a face
# left standing across _MOOD_DRIFT_THRESHOLD work-deltas gets one
# `still <face>?` ask, then the counter re-arms. Higher threshold than the
# course — a mood legitimately outlives more work than a route row does.
MOOD_DRIFT_COUNT_KEY = "mood_drift_count"
MOOD_LAST_TEXT_KEY = "mood_last_text"
_MOOD_DRIFT_THRESHOLD = 5

# Budget thresholds (% used) at which the ambient bar re-emits; crossed once
# each in the ascending direction only.
_AMBIENT_BUDGET_THRESHOLDS = (25, 50, 75, 90)

# Quota thresholds (% *remaining*) at which the ambient bar re-emits; crossed
# once each in the descending direction only.
_AMBIENT_QUOTA_THRESHOLDS = (30.0, 20.0, 10.0, 5.0)

# An armed ``at:`` letter only opens the obligation boundary once its fire
# time is within this horizon (or already past it) — see
# ``_armed_entry_is_due`` (#1209). The always-shown render of the full armed
# set (``_render_armed_rows``) is unaffected; this constant scopes the
# *obligation classification* only.
_ARMED_IMMINENT_HORIZON_SECONDS = 15 * 60

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
# The resident's own topic claim (the-run-that-claims-its-thread). Same
# idiom as `.mood` above — read fresh at every boundary rather than through
# `run_ledger.read_run_topics_control` (which the daemon side uses for the
# closeout/live-read paths): the hook path stays import-light on purpose
# (see `_read_mood`'s docstring for the same call), so this is a second,
# deliberately small copy of the same lenient parse — the two must never
# disagree, same risk `keepalive_until` names for its own two readers.
TOPICS_NAME = ".topics"
_TOPICS_READ_CAP_CHARS = 2000
_TOPIC_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
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
        # #1184: the rooted-write guard's two facts, both `BRR_`-namespaced
        # copies the daemon arms alongside the git pin (`daemon._runner_runtime`)
        # — deliberately not read off `GIT_DIR`/`GIT_WORK_TREE` directly, even
        # though the pin already exports those. Every `brnrd hook <phase>`
        # invocation runs through `cli.main()`, whose first act
        # (`_drop_inherited_git_pin`) pops `GIT_DIR`/`GIT_WORK_TREE` from
        # `os.environ` before anything else runs — correct and load-bearing for
        # brnrd's own git calls, but it means this hook subprocess can never see
        # the raw variables. `BRR_HOST_ROOT` is the host checkout root a
        # subprocess cannot otherwise derive (cwd and `-C` are exactly what the
        # pin outranks); `BRR_WORK_TREE` is `GIT_WORK_TREE`'s value under a name
        # the scrub does not touch. Both unset ⇒ the guard is unarmed (a
        # resident, a host run, or a strand whose pin didn't apply) and
        # `_rooted_write_neutral` never blocks — the same fact-based degrade
        # `_child_git_pin` uses.
        host_root = env.get("BRR_HOST_ROOT")
        self.host_root = Path(host_root) if host_root else None
        work_tree = env.get("BRR_WORK_TREE")
        self.git_work_tree = Path(work_tree) if work_tree else None
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


def _read_topics(ctx: HookContext) -> list[str] | None:
    """Read the resident's `.topics` claim fresh, lenient, slug-filtered.

    Same "read the artifact" doctrine as :func:`_read_mood`: the resident
    may write this at any boundary, and the discoverability chip needs to
    see it disappear the moment it does. Accepts a bare slug row or a
    `topics:`-prefixed one; junk tokens are dropped rather than raising.
    Returns ``None`` for absent, unreadable, or filtered-to-nothing — same
    class as "no claim" for the caller's eligibility check.
    """
    if ctx.outbox_dir is None:
        return None
    path = ctx.outbox_dir / TOPICS_NAME
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline(_TOPICS_READ_CAP_CHARS)
    except OSError:
        return None
    text = first_line.strip()
    if text.lower().startswith("topics:"):
        text = text[len("topics:"):].strip()
    tokens = [t for t in re.split(r"[\s·]+", text) if t]
    slugs = [t for t in tokens if _TOPIC_SLUG_RE.match(t)]
    return slugs or None


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


def _stop_gate_name(portal: dict[str, Any]) -> str:
    """The gate name the closeout capsule's delivery block may cite (#1481).

    ``portal["delivery"]["gate"]`` is ``daemon._live_delivery_projection``'s
    own resolved answer to "which gate would actually carry a mid-run
    ``gate:`` directive on this run's config, right now" — the same
    question the two hardcoded-``telegram`` sentences below used to answer
    with a literal instead of a fact. Reusing that field (rather than
    re-deriving from ``.brr/gates/`` on disk) keeps this in step with the
    same runtime resolution the routing sentences around it describe,
    including cases where a gate is configured but currently can't
    deliver.

    Empty string when nothing resolves — the caller drops the example
    rather than inventing one: a named instruction that is wrong is worse
    than an unnamed one the reader looks up.
    """
    delivery = (
        portal.get("delivery") if isinstance(portal.get("delivery"), dict) else {}
    )
    name = delivery.get("gate")
    return name.strip() if isinstance(name, str) and name.strip() else ""


def _stop_no_reply_due(portal: dict[str, Any]) -> bool:
    """True when the closeout will render the "no reply yet" line (#1142).

    The exact predicate ``format_delta``'s delivery block uses for its
    ``elif not replied_current and not gate_less`` arm, kept here — same
    discipline as :func:`_stop_is_gate_less` just above — so the firing
    streak this gates and the sentence it counts cannot drift apart. An
    addressed run with something delivered *somewhere* (``any_delivery``)
    but nothing on its own waking thread yet, and not gate-less (that case
    can never clear, so it is not a repeat-firing loop, just the permanent
    routing fact).
    """
    inbound = portal.get("inbound") if isinstance(portal.get("inbound"), dict) else {}
    if not inbound.get("current_event"):
        return False
    if _stop_is_gate_less(portal):
        return False
    outbound = portal.get("outbound") if isinstance(portal.get("outbound"), dict) else {}
    replied_current = outbound.get("replies_current")
    any_delivery = bool(
        replied_current
        or outbound.get("replies_other")
        or outbound.get("outbound_messages")
    )
    return bool(any_delivery and not replied_current)


def _bump_stop_no_reply_streak(state: dict[str, Any], portal: dict[str, Any]) -> int:
    """Advance :data:`STOP_NO_REPLY_STREAK_KEY` for this Stop firing.

    Same increment/reset discipline as :func:`_bump_repeat_streaks`
    (persisted hook state, since hooks are fresh subprocesses): due this
    firing ⇒ +1, not due ⇒ reset to 0. Also resets on a waking-event change
    (:data:`STOP_NO_REPLY_EVENT_KEY`) — a fresh follow-up replacing the
    unanswered event is a new wait, not a continuation of the old one.
    """
    inbound = portal.get("inbound") if isinstance(portal.get("inbound"), dict) else {}
    current_event_id = str(inbound.get("current_event") or "")
    if state.get(STOP_NO_REPLY_EVENT_KEY) != current_event_id:
        state[STOP_NO_REPLY_STREAK_KEY] = 0
        state[STOP_NO_REPLY_EVENT_KEY] = current_event_id
        # A fresh waking event gets its own first escalated rendering too —
        # see :data:`STOP_NO_REPLY_ESCALATED_KEY`.
        state[STOP_NO_REPLY_ESCALATED_KEY] = False
    streak = (
        int(state.get(STOP_NO_REPLY_STREAK_KEY) or 0) + 1
        if _stop_no_reply_due(portal) else 0
    )
    state[STOP_NO_REPLY_STREAK_KEY] = streak
    return streak


def _stop_no_reply_escalation_capped(state: dict[str, Any], no_reply_streak: int) -> bool:
    """True when the escalated "no reply yet" verdict has already rendered once.

    #1296 belt-and-suspenders — read :data:`STOP_NO_REPLY_ESCALATED_KEY`'s
    module comment for the why. Pure read: the caller latches the state
    write itself, on the render, the same division of labour
    ``GATELESS_ROUTING_KEY``/``note_routing`` already use just above this
    one — ``format_delta`` stays a pure function of its arguments.
    """
    if no_reply_streak < _STOP_NO_REPLY_ESCALATE_THRESHOLD:
        return False
    return bool(state.get(STOP_NO_REPLY_ESCALATED_KEY))


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


# ── Three-class ambient split (#1116) ────────────────────────────────────────


def _armed_entry_is_due(entry: dict[str, Any]) -> bool:
    """True when an armed ``at:`` letter is overdue or imminent (#1209).

    Mirrors ``_keepalive_remaining_seconds``'s shape (parse, compare to now,
    fail toward the safe side) but the payload here is already epoch seconds
    (``ScheduleEntry.at`` / the ``"at": e.at`` row in ``armed_letters()``),
    not an ISO string. A missing or unparseable ``at`` fails toward "due" —
    an unavailable portal already counts as an obligation elsewhere in this
    module's own docstring, and a malformed entry must not silently mute the
    escalation the same way.
    """
    at = entry.get("at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return True
    remaining = at - time.time()
    return remaining <= _ARMED_IMMINENT_HORIZON_SECONDS


def _has_post_tool_obligations(
    portal: dict[str, Any],
    plan: "promises.Blueprint | None",
    surprise: str | None,
    plan_edge: bool,
    portal_unavailable: bool,
    route: "course.Course | None" = None,
    route_edge: bool = False,
    bolt_edge: bool = False,
    pending_new_or_changed: bool = True,
) -> bool:
    """True when this boundary carries at least one obligation.

    "Obligation" here means *this boundary must not be silently dropped*,
    which is a coarser axis than the bar segments' own OBLIGATION/DELTA/
    VITAL/AMBIENT class (see `SEGMENT_CLASS`) — most of the list below do
    have a discharge condition the resident can act on (pending events,
    stale card, overdue armed letters, unmet blueprint promise edge, running
    long); refused directives are the counted exception (#1266 audit: `!N`
    is DELTA-classed precisely because reading `notices` cannot clear it,
    yet a fresh refusal still must not go silent, so it stays listed here).
    Pure ambient content (quota %, elapsed time, orientation progress) opens
    nothing on its own.

    An unavailable portal counts as an obligation: the unknown count cannot
    be reported as zero, so the resident must not mistake silence for all-clear.

    ``surprise`` (a mood edge — something just failed) and ``plan_edge`` (a
    blueprint change) are deltas that travel outside the portal token, and
    therefore cannot ride the ambient gate.
    """
    if portal_unavailable:
        return True
    if surprise or plan_edge:
        return True
    attention = (
        portal.get("attention") if isinstance(portal.get("attention"), dict) else {}
    )
    pending_raw = attention.get("pending_event_count")
    pending_known = pending_raw is not None
    try:
        pending = int(pending_raw or 0)
    except (TypeError, ValueError):
        pending_known = False
        pending = 0
    # Seen-only pending events are no longer obligations at the mid-run bar
    # (w-54 follow-on, 2026-08-23): a pending event the resident has already
    # seen collapses to a count chip; only *new* or *changed* bodies — or the
    # first render of any pending set — keep the bar alive by themselves.
    # `pending_unknown` (the ✉? chip) still always forces (unknown ≠ zero).
    # `pending_new_or_changed` defaults to True so callers that don't compute
    # event decisions (tests, direct calls) get the conservative old behaviour:
    # any non-zero pending count opens the boundary.
    if not pending_known or (pending > 0 and pending_new_or_changed):
        return True
    try:
        pending_files = int(attention.get("pending_outbox_file_count", 0) or 0)
    except (TypeError, ValueError):
        pending_files = 0
    if pending_files > 0:
        return True
    card = portal.get("card") if isinstance(portal.get("card"), dict) else {}
    if card.get("stale"):
        return True
    notices = (
        portal.get("notices") if isinstance(portal.get("notices"), list) else []
    )
    if _counted_notices(notices):
        return True
    schedule_facet = (
        portal.get("schedule") if isinstance(portal.get("schedule"), dict) else {}
    )
    armed = (
        schedule_facet.get("armed")
        if isinstance(schedule_facet.get("armed"), list) else []
    )
    if any(_armed_entry_is_due(e) for e in armed if isinstance(e, dict)):
        return True
    budget = portal.get("budget") if isinstance(portal.get("budget"), dict) else {}
    if budget.get("long_running"):
        return True
    # (The unwritten-.name clause retired 2026-08-20 — w-69: the claims
    # assignment escalates it through the ledger's own edges instead of
    # holding the bar open every boundary.)
    if plan is not None and plan_edge and plan.owed:
        return True
    # The course's edge mirrors the blueprint's: the card is a control file
    # the daemon's portal token never sees, so an edited route can only
    # reach the boundary through its own latch (#1008's gate-opener rule).
    if route is not None and route_edge and route.open_rows:
        return True
    # The bolt gauge's edge, same shape once more: an outstanding promise is
    # already an obligation (the ``plan_edge`` clause above), and a bolt-token
    # move while one still stands is worth re-opening the boundary for, even
    # when the thing that moved the token was only the produce count.
    if bolt_edge and plan is not None and plan.owed:
        return True
    return False


def _ambient_should_emit(
    state: dict[str, Any],
    budget: dict[str, Any],
    resources: dict[str, Any],
) -> bool:
    """True when ambient vitals should emit on this boundary.

    Fires on the first post-tool boundary ever, then only when a meaningful
    threshold crosses:

    - budget % used crosses any of ``_AMBIENT_BUDGET_THRESHOLDS``
    - any quota bucket's % remaining crosses any of ``_AMBIENT_QUOTA_THRESHOLDS``

    Between threshold crossings the ambient bar is silent; obligation and delta
    content still surfaces through a separate path.
    """
    if not state.get(AMBIENT_FIRST_BAR_KEY):
        return True  # very first post-tool boundary

    elapsed = budget.get("elapsed_seconds")
    limit = budget.get("budget_seconds")
    if elapsed is not None and limit is not None:
        try:
            pct_used = int(int(elapsed) * 100 / int(limit))
            last_pct = state.get(AMBIENT_BUDGET_PCT_KEY, -1)
            for threshold in _AMBIENT_BUDGET_THRESHOLDS:
                if last_pct < threshold <= pct_used:
                    return True
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    quota = resources.get("quota") if isinstance(resources, dict) else {}
    if isinstance(quota, dict) and quota.get("status") == "known":
        summary = str(quota.get("summary") or "")
        last_quota: dict[str, float] = state.get(AMBIENT_QUOTA_KEY) or {}
        for match in _QUOTA_BUCKET_RE.finditer(summary):
            label = match.group("label")
            try:
                pct_left = float(match.group("pct"))
            except (TypeError, ValueError):
                continue
            last_left = last_quota.get(label, 100.0)
            for threshold in _AMBIENT_QUOTA_THRESHOLDS:
                if last_left > threshold >= pct_left:
                    return True

    return False


def _update_ambient_state(
    state: dict[str, Any],
    budget: dict[str, Any],
    resources: dict[str, Any],
) -> None:
    """Persist current ambient vital values after an ambient emission."""
    state[AMBIENT_FIRST_BAR_KEY] = True

    elapsed = budget.get("elapsed_seconds")
    limit = budget.get("budget_seconds")
    if elapsed is not None and limit is not None:
        try:
            state[AMBIENT_BUDGET_PCT_KEY] = int(int(elapsed) * 100 / int(limit))
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    quota = resources.get("quota") if isinstance(resources, dict) else {}
    if isinstance(quota, dict) and quota.get("status") == "known":
        summary = str(quota.get("summary") or "")
        snapshot: dict[str, float] = {}
        for match in _QUOTA_BUCKET_RE.finditer(summary):
            label = match.group("label")
            try:
                snapshot[label] = float(match.group("pct"))
            except (TypeError, ValueError):
                pass
        if snapshot:
            state[AMBIENT_QUOTA_KEY] = snapshot


# ─────────────────────────────────────────────────────────────────────────────


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
# `orient: skip` heading the line (list/quote/heading markers tolerated,
# arbitrary content before the word `skip`/`skipped`/`skipping`), or the
# canonical sentence. A first-class outcome, not a failure state.
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
#
# #1266: narrowed too far. The next real declaration this shipped against —
#
#     "orientation: AGENTS.md skim skipped — assuming prior knowledge
#      (contract carried in playbook + continuity; surface pages injected
#      this wake)"
#
# — is exactly the field-prefixed idiom the first shape was meant to catch
# (weave.md's own `key: value` convention), but natural phrasing put the
# subject clause *between* the separator and the word "skipped", and used
# the past-tense form. A 145-minute run rendered `orient 0/3` at every
# boundary despite a legal declaration on a legal path. So: keep the same
# structural anchor (a line that *leads* with `orient(ation)` + a separator —
# still a deliberate field, not incidental prose) but allow arbitrary text
# between the separator and the word, and accept skip/skipped/skipping. The
# declaration-scoping this whole guard exists for still holds: a bare mention
# of "skip" deep in an unrelated sentence never starts the line with
# `orient(ation):`, so the two false positives above stay excluded (driven in
# the tests). One narrow safety net kept from the same false-negative-leaning
# doctrine: a negated value on the same declared line ("orientation: not
# skipped, read AGENTS.md") must not discharge the meter — the exact
# reporting-its-own-value trap this guard was first narrowed against, one
# clause shape over.
_ORIENT_SKIP_RE = re.compile(
    # `orient: skip` / `orientation: <whatever> skipped …`, at the head of a
    # line, arbitrary content between the separator and the word, unless that
    # content negates it first.
    r"^[\s>*\-#]*orient(?:ation)?\s*[:=-]\s*"
    r"(?!.*(?:\bnot\b|n't).*\bskip)"
    r".*\bskip(?:s|ped|ping)?\b"
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


#: The three classes of the boundary channel (#1116). A line's class is
#: decided by one question — **is there an act that turns it off?**
#:
#: - ``OBLIGATION`` — yes. It repeats until discharged, and it *should*:
#:   byte-identical repetition is the signature of "nothing was done", not
#:   of "nothing happened", which is why #963's content dedupe could never
#:   reclaim this class and correctly refused to try.
#: - ``DELTA`` — it records something that changed. It has no discharge, but
#:   it earns its bytes by being new.
#: - ``AMBIENT`` — a meter. No discharge, no news; a fresh reading of a
#:   number that was already on screen. This is the class that made 80.2% of
#:   a day's injections share a shape with another.
#:
#: This is **not** the same axis as "may this chip open the bar on its own"
#: — that gate lives in ``_render_bar``'s laden check and several ambient
#: chips are already excluded from it. This axis decides what survives once
#: the bar is open but ambient vitals are quiet.
OBLIGATION = "obligation"
DELTA = "delta"
#: The maintainer's exemption, and it is a fourth fact rather than a
#: loophole: a line you cannot discharge and cannot afford to lose. You
#: cannot *act* on your own quota, and you must still be able to see it.
#: Kept legible by the threshold rule rather than by repetition — a number
#: that never stops moving stops being read.
VITAL = "vital"
AMBIENT = "ambient"
_SEGMENT_CLASSES = (OBLIGATION, DELTA, VITAL, AMBIENT)


@dataclass(frozen=True)
class _BarSegment:
    """One documented entry in the bar's fixed segment vocabulary."""

    key: str
    glyph: str
    meaning: str
    #: No default, deliberately (#1116, and #1118's lesson about lists that
    #: are right on the day they are written): a segment added later must
    #: answer "what turns this off?" rather than inherit an answer. With a
    #: default here, the cheap one would be ``AMBIENT`` and a new obligation
    #: would go silent on exactly the quiet boundary it exists for.
    klass: str


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
    # The `run` id chip retired 2026-08-19 (w-54): the id is ambient context
    # the reader already has, and the bar's anchor role moved to the static
    # `⌁[<mood>]:` preamble (`_bar_preamble`) — the bolt glyph stays, the
    # noise goes.
    _BarSegment(
        "budget", "⏱",
        "the wall-clock ticker — elapsed minutes (`⏱ 41m`), with the "
        "soft limit only when the user configured one (`16/120m`). "
        "Changes nearly every boundary; renders only when its text moved, "
        "like every chip since w-54 — but a minute is a minute, so in "
        "practice it rides most open bars. It never opens one.",
        # a meter.
        klass=VITAL,
    ),
    _BarSegment(
        "quota", "q",
        "every subscription quota bucket the `quota` facet knows about, "
        "abbreviated to one letter + remaining percent, joined by `·` "
        "(`S57·W50·F27` = session 57%, week 50%, a named per-model bucket "
        "27%). Renders only when quota is `known`.",
        # a meter.
        klass=VITAL,
    ),
    _BarSegment(
        "assign", "assign",
        "the ignition assignments' ledger (w-69): boot obligations retired "
        "of boot obligations total (`assign 3/6`). Every row is in one of "
        "three states — *current* (quiet: the chip alone carries it), "
        "*moved* (a discharge renders once), or *overdue* (past its priced "
        "window it grows a detail line per few boundaries, cap 3, then "
        "holds) — the same vocabulary the drift asks use. Discharge, or "
        "the card handoff (a `## Plan` write adopts or defers every open "
        "row), retires a row; nothing is retired by time. The chip leaves "
        "the boundary the last row retires and never returns. Orientation "
        "walk progress rides the orient row's own detail, not a chip of "
        "its own (the old `orient x/y` meter, retired 2026-08-20).",
        # Obligation by the maintainer's test — actionable and
        # turn-off-able: the discharge acts (or the card handoff)
        # retire every row. Never in the laden gate: escalation
        # edges open the bar; a standing row does not.
        klass=OBLIGATION,
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
        # constant for a whole run.
        klass=AMBIENT,
    ),
    _BarSegment(
        "siblings", "▷",
        "coexisting sibling runs in this dominion (`▷1`). Renders only "
        "when the count is > 0 — an idle dominion says nothing here.",
        # a count of other runs; this run cannot act on it.
        klass=VITAL,
    ),
    _BarSegment(
        "keepalive", "rb",
        "keepalive extension remaining, the slot held past budget (`rb3h`). "
        "Renders only while `.keepalive` is active.",
        # a state the resident already set.
        klass=AMBIENT,
    ),
    _BarSegment(
        "delivery", "⇡",
        "delivery this run — current-thread replies + everything else "
        "(other threads, outbound messages) (`⇡2+3`). Renders only once "
        "something has been sent.",
        # it records what was sent.
        klass=DELTA,
    ),
    _BarSegment(
        "produce", "⚒",
        "total attested produce items this run (commits, branches, PRs, kb "
        "pages, issues, comments, messages, files) (`⚒4`). Renders only "
        "when nonzero.",
        # it records what was made.
        klass=DELTA,
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
        # a verdict, and verdicts change.
        klass=DELTA,
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
        # Obligation: an outstanding promise is actionable and
        # turn-off-able — keep it. The neighbouring comment calling
        # the chip "the ambient half" is about the *gate* (it must
        # not manufacture a boundary), which is unchanged; the
        # module's own test pins the chip as the standing fact that
        # rides every boundary while the line speaks on delta.
        klass=OBLIGATION,
    ),
    _BarSegment(
        "course", "course",
        "the run's own route — the `## Plan` / `## Course` checkbox section "
        "of `.card`, read fresh each boundary (`course 2/5` = rows checked "
        "of rows total; :mod:`brr.course`). Renders only while rows stand "
        "open; checking a row on the card is the discharge that moves it. "
        "This one *is* a ratio, unlike `owed`, because both numbers come "
        "from the same authored list — one population, one denominator. "
        "The current row rides a detail line on the course's own delta and "
        "on the boundary a fresh event lands (the derailment moment, where "
        "the route must be in the loud zone to be decidable).",
        # Obligation: an open route row is actionable (do it / check it /
        # rewrite the plan) and turn-off-able by exactly that act. The chip
        # never opens the bar by itself — the detail line, latched on the
        # course's own token, is what earns a boundary.
        klass=OBLIGATION,
    ),
    # The `bolt` chip retired 2026-08-19 (evt-…-mhrx) — it counted events
    # while saying "asks" and restated owed/produce/pending. The bolt the
    # design doc names lives on as the cut-validator, daemon-side.
    _BarSegment(
        "mood", "mood",
        "the mood channel's *edge* forms only (w-54): the steady face moved "
        "into the `⌁[<face>]:` preamble, so this segment speaks just three "
        "ways — `mood <face> ← <what happened>` on a boundary that "
        "surprised the run (the ask: the mood channel questions itself on "
        "an edge, not on every tick, #604); `mood ✗ <name> → <near misses>` "
        "while the written handle resolves to no emote (actionable — "
        "rewrite the file); and `mood <face> — still?` when the face stood "
        "still through five work-moves. (The `mood?`/`topic?` blank-claim "
        "nudges retired 2026-08-20 into the claims assignment — w-69.)",
        # the surprise/unresolved/still? forms each name an act.
        klass=OBLIGATION,
    ),
    _BarSegment(
        "notices", "!",
        "refusal count — directives brr dropped this run (`!1`). Renders only "
        "when the count is > 0: a refused outbox file is deleted exactly like "
        "an accepted one, so the only thing between a dropped reply and silence "
        "is the resident opening `portal-state.json → notices`. This segment "
        "surfaces a non-zero count without demanding a read. Absent at zero so "
        "it earns its ink the same way every other differential segment does.",
        # #1266 audit: this was OBLIGATION ("an act turns it off"), but no
        # such act exists. `.notices.jsonl` (daemon.py) is append-only — no
        # prune, no ack, no read-tracking anywhere in the codebase — so `!N`
        # can never reach zero within a run once tripped, however many times
        # the resident opens `portal-state.json` and reads every entry. That
        # is DELTA's own definition ("records something that changed... has
        # no discharge, but earns its bytes by being new"), not OBLIGATION's.
        # Relabeling only — rendering was unchanged by it; `brnrd legend`
        # (cli.py's cmd_legend) does print this field verbatim, so its `!`
        # row now correctly says `delta`, which is the fix, not a side
        # effect. The change that would make this a genuine OBLIGATION (a
        # mark-as-seen verb) is bigger than a class label and is proposed,
        # not built, here.
        klass=DELTA,
    ),
    _BarSegment(
        "card", "card",
        "the live `.card` surface's health, spoken only when it needs a "
        "hand: `stale` / `blank` / `cut N>4096`. The last measures the "
        "*projection* the transport publishes, not the file — a long card "
        "with a well-formed `Now` is fine; `cut` means the live surface is "
        "losing the tail. A healthy card renders nothing (w-54): `card ok` "
        "was a fact with no act attached, repeated forever. Last segment "
        "when present. A `stale` value also gets its own detail line "
        "naming why — the chip alone is never the whole obligation.",
        # a stale card is discharged by writing one.
        klass=OBLIGATION,
    ),
)


#: Class per rendered chip key, derived from :data:`BAR_SEGMENTS` so the
#: vocabulary stays the single owner. ``pending_unknown`` (the ``✉?`` chip)
#: has no vocabulary entry — it is rendered inline when the pending count is
#: unreadable — and is declared here beside the derivation rather than left
#: to the ``.get`` default, because an unknown obligation count is the one
#: thing that must never be filed as a meter.
SEGMENT_CLASS: dict[str, str] = {
    **{segment.key: segment.klass for segment in BAR_SEGMENTS},
    "pending_unknown": OBLIGATION,
}

# _KEPT_WHEN_QUIET and the `run` id chip retired with w-54 (2026-08-19):
# per-chip change-gating in `_render_bar` subsumes the class-wide
# quiet-boundary filter, and the bar's anchor is the `⌁[<mood>]:` preamble.


def _budget_chip(budget: dict[str, Any]) -> str | None:
    """The wall-clock ticker: elapsed minutes, with the limit only when real.

    The w-54 redesign (2026-08-19): there is no default time limit, so the
    ``x/ym`` form renders only when the user configured one
    (``runner.timeout_seconds``) and the daemon therefore put a
    ``budget_seconds`` in the capsule. Unlimited runs get the bare ticker —
    ``⏱ 41m`` — which changes roughly every boundary, but that's a ticker.
    """
    elapsed = budget.get("elapsed_seconds")
    limit = budget.get("budget_seconds")
    if elapsed is None:
        return None
    try:
        if limit is not None:
            return f"⏱ {int(elapsed) // 60}/{int(limit) // 60}m"
        return f"⏱ {int(elapsed) // 60}m"
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


def _card_chip(card: dict[str, Any], card_stale: bool) -> str | None:
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

    A healthy card renders **nothing** (w-54, 2026-08-19): ``card ok`` was
    the textbook always-on chip — a fact with no act attached, repeated at
    every boundary. The chip now speaks only when the surface needs a hand
    (stale / blank / cut); silence is the ok.
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
    # `card behind` — the run moved after the last card write, said the
    # moment it becomes true (his ask, evt-…-mhrx: the 240s clock made the
    # transition *look wrong* by hiding it for four minutes). The 240s
    # grace now gates only the stale nag above; this is the bare fact,
    # change-gated like every chip, so it costs one render per episode
    # and vanishes the moment the card catches up.
    moved = card.get("state_moved_seconds")
    age = card.get("age_seconds")
    if (
        isinstance(moved, (int, float)) and isinstance(age, (int, float))
        and not isinstance(moved, bool) and not isinstance(age, bool)
        and moved < age
    ):
        return "card behind"
    # Healthy (or an older capsule shape with no body to measure — absence
    # of evidence of trouble is the quiet state, not a verdict to invent).
    return None


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

    *receipt* is this run's own tree's entry from ``.gate-receipts.json``
    (``gate_receipt.read_receipt``, keyed by ``ctx.repo_dir`` — #820), never
    the raw file: a receipt for a tree this run is not asking about must not
    be able to satisfy or trip this chip. ``.gate-receipts.json`` decides
    whether a run may merge — ``workflow.md``
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


def _bar_preamble(mood: str | None) -> str:
    """``⌁[<face>]:`` — the bar's one static element (w-54, his sketch).

    The bolt is the mark of the resident-owned status line; the face inside
    the brackets is the only thing that renders there, and it is the
    resident's own — the ornament curated by the run, connecting it to the
    reader. ``·`` when no mood is written (the ``mood?`` nudge still fires
    once, elsewhere); ``✗`` when the written mood resolves to no emote — the
    unresolved-handle honesty `_mood_chip` keeps, compressed to one mark
    (the near-misses ride the mood chip, which still renders for a miss).
    """
    if not mood:
        return "⌁[·]:"
    name = mood.strip()
    glyph = _emote_glyph(name)
    return f"⌁[{glyph}]:" if glyph else "⌁[✗]:"


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

# Hook-state key: the bolt gauge's ``T`` (design-the-bolt.md §Accretion) — a
# sorted list of every distinct action-event id this run has ever carried as
# pending. Deliberately **not** the same storage as EVENTS_SEEN_KEY above:
# that ledger is pruned to events still pending (it exists to dedupe letter
# chrome), so its size *is* the current pending count, not a cumulative
# total. This key only ever grows — an id, once added, is never removed, so
# ``len()`` of it is "distinct asks this run has seen", the numerator half
# of `bolt A/T asks` survives an event's own disposition.
EVENTS_SEEN_ALL_KEY = "events_seen_all_ids"

# Hook-state key: the bar-path pending sermon's own "did the SET change"
# ledger — ``{"ids": [sorted action-event ids], "count": N}`` as of the
# *previous* laden post-tool boundary. Deliberately its own key rather than
# reusing EVENTS_SEEN_KEY: that ledger tracks *body* content per id (for the
# letter-chrome collapse), while this one only asks whether the pending
# *set* moved, so the full instruction sentence
# ("Address each below with an `event:` reply...") can compress to a
# one-liner on a boundary where nothing about the obligation set changed.
# Pruned to the current set on every laden boundary (full or compact) by
# :func:`_pending_set_changed` — never grows unbounded, same discipline as
# EVENTS_SEEN_KEY.
PENDING_SENTENCE_SET_KEY = "pending_sentence_set"

# Hook-state key: per-detail-line consecutive-render streak, for the
# compression-on-repeat rule (#1116 residue, design-the-live-loop.md §1). One
# entry per compressible OBLIGATION detail line (``notices`` / ``running_long``
# / ``name_nudge`` / ``card_stale``): incremented every laden boundary the
# line is due, reset to 0 the moment it is not — so "3rd+ consecutive laden
# boundary" is a plain counter comparison, not a content diff.
REPEAT_COUNTS_KEY = "detail_repeat_counts"

#: Below this consecutive-streak count a compressible detail line renders in
#: full; at and above it, compact. N≥3 per the design ("Nth consecutive laden
#: boundary (N≥3)").
_REPEAT_COMPRESS_THRESHOLD = 3

#: Below this consecutive-streak count the Stop "no reply yet" delivery line
#: (#1142) renders as state description; at and above it, as a verdict.
#: Deliberately N=2, not :data:`_REPEAT_COMPRESS_THRESHOLD` (N=3): that
#: threshold buys three renders of *display* breathing room for a
#: repeat-render-is-noise concern (four post-tool bar lines whose harm is
#: habituation). Here the sentence's own claim — "your final message will be
#: dispatched" — is a promise about a *specific future event*, and that
#: event demonstrably did not happen between firing #1 and firing #2 (the
#: same waking thread, still replied_current=0). The promise is already
#: false one firing sooner than a mere-repetition threshold would wait for,
#: so the wording has to turn a firing earlier too.
_STOP_NO_REPLY_ESCALATE_THRESHOLD = 2

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
    if _reaches_nobody(source):
        parts.append("no correspondent — `note:` clears it")
    return " · ".join(parts)


def _reaches_nobody(source: str) -> bool:
    """True when an ``event:`` reply to this source is delivered nowhere.

    brnrd mints these itself (``protocol.INTERNAL_SOURCES``) — a schedule
    firing, a child's completion note, a steer to a child — so there is no
    gate behind them and no correspondent waiting. A reply is accepted,
    clears the event, and reaches no one; the run has to *learn* that from
    a "NOT delivered" notice after the fact, and this row is where the
    decision was made.

    Derived rather than listed, and the derivation is checked: a source
    brnrd mints is exactly one no gate owns, which
    ``tests/test_hooks.py::test_the_gateless_set_agrees_with_the_daemons``
    asserts against ``daemon._gate_owns_source`` so the two cannot drift.
    """
    return source.strip().casefold() in protocol.INTERNAL_SOURCES


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


#: 1,203 pending events once rendered 234 KB into a SessionStart seed — the
#: same shape of bug as :data:`prompts._PENDING_EVENTS_RENDER_MAX`. Caps the
#: rendered window here too; the true omitted count always appears on the
#: elision line rather than being silently dropped.
_PENDING_EVENT_ROWS_MAX = 40


def _render_event_rows(
    events: list[Any],
    event_seen: dict[str, dict[str, Any]] | None,
    inbox_pointer: str | None,
    *,
    skip_seen: bool = False,
) -> list[str]:
    """One letter per pending event: chrome row + body per policy.

    *event_seen* is the boundary's per-event decision map from
    :func:`_event_seen_decisions` (``None`` ⇒ everything renders as new —
    the shape ad-hoc callers and replay get). An unchanged already-shown
    body collapses to the one-line seen form; a changed one re-renders in
    full under a ``Δ changed`` mark.

    *skip_seen* (``True`` for the mid-run bar path): silently omit events
    whose bodies are unchanged since last render — they are already accounted
    for by the ``pending N`` chip in the bar line.  The full-replay callers
    (Stop, direct ``format_delta`` calls) leave this ``False`` to preserve
    the existing seen-row behaviour.

    Capped at :data:`_PENDING_EVENT_ROWS_MAX` — existing order is preserved
    (never re-sorted), just truncated, and a fitting list gets no elision
    line at all.
    """
    total = len(events)
    rendered_events = events[:_PENDING_EVENT_ROWS_MAX]
    # Counted from what the loop *kept* — non-dict entries are skipped below,
    # and an omitted count taken from the slice would under-report. A
    # collapsed `seen ×N · unchanged` row is still a rendered row (the event
    # is accounted for on screen, just compactly) — it must bump this
    # counter exactly like a full row, or the elision line below double-
    # counts it: emitted *and* claimed omitted (#1116 residue's own bug).
    # When skip_seen=True, seen rows are NOT rendered and NOT counted here —
    # they are accounted for by the chip, not these detail rows.
    rendered = 0
    seen_skipped = 0  # seen events suppressed via skip_seen — accounted for by chip
    rows: list[str] = []
    for ev in rendered_events:
        if not isinstance(ev, dict):
            continue
        decision = (event_seen or {}).get(str(ev.get("id") or ""))
        status = (decision or {}).get("status") or "new"
        shown = int((decision or {}).get("shown") or 0)
        if status == "seen":
            if skip_seen:
                seen_skipped += 1
                continue
            rendered += 1
            rows.append(f"- {_event_seen_line(ev, shown)}")
            continue
        body = _event_body(ev)
        size = len(body.encode("utf-8", "replace"))
        rendered += 1
        rows.append(f"- {_event_header(ev, size=size, changed=status == 'changed')}")
        rows.extend(_event_body_block(body, size, inbox_pointer))
    omitted = total - rendered - seen_skipped
    if omitted > 0:
        rows.append(
            f"- … +{omitted:,} more pending events not rendered here — read "
            "the live portal-state.json / inbox.json for the full list"
        )
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


def _commit_events_seen_all(state: dict[str, Any], ids: Any) -> int:
    """Union *ids* into the bolt gauge's cumulative distinct-asks ledger.

    Unlike :func:`_commit_event_seen` (pruned to currently-pending events,
    for letter-chrome dedup), this key never prunes: it is ``T`` in
    ``bolt A/T asks`` (design-the-bolt.md §Accretion), a plain count of every
    distinct action-event id this run has ever carried, whether or not it is
    still pending. Called every phase (seed/post-tool/stop) since an ask can
    arrive on any of them. Returns the total count after the union.
    """
    existing = state.get(EVENTS_SEEN_ALL_KEY)
    seen_ids: set[str] = set(existing) if isinstance(existing, list) else set()
    for eid in ids:
        if eid:
            seen_ids.add(eid)
    state[EVENTS_SEEN_ALL_KEY] = sorted(seen_ids)
    return len(seen_ids)


def _pending_set_changed(
    state: dict[str, Any], action_events: list[Any], action_pending: int
) -> bool:
    """Bar-path only: has the pending obligation *set* moved since the last
    laden boundary — not each event's body (that's :data:`EVENTS_SEEN_KEY`),
    just membership?

    True on the first laden boundary this run (no stored snapshot yet), any
    id present now that wasn't in the previous boundary's snapshot, or a
    plain count increase (the fallback for a pending event that carries no
    ``id`` at all, so it never shows up in the id-set comparison but still
    moves the count). A pure count *decrease* — an event got answered,
    nothing new arrived — is deliberately **not** a change: the compact
    line's own numbers already carry that fact.

    Always commits the current snapshot (pruning to the current set, same
    discipline as :data:`EVENTS_SEEN_KEY`), whether this boundary rendered
    full or compact — "since the last render" means the immediately prior
    boundary, not the last time the full sentence happened to fire.
    """
    current_ids = sorted(
        {
            str(ev.get("id"))
            for ev in action_events
            if isinstance(ev, dict) and ev.get("id")
        }
    )
    stored = state.get(PENDING_SENTENCE_SET_KEY)
    if isinstance(stored, dict):
        stored_ids = stored.get("ids") if isinstance(stored.get("ids"), list) else []
        stored_count = int(stored.get("count") or 0)
        changed = (
            bool(set(current_ids) - set(stored_ids))
            or action_pending > stored_count
        )
    else:
        changed = True
    state[PENDING_SENTENCE_SET_KEY] = {
        "ids": current_ids,
        "count": int(action_pending),
    }
    return changed


def _bump_repeat_streaks(
    state: dict[str, Any], dues: dict[str, bool]
) -> dict[str, int]:
    """Advance the compression-on-repeat counters (#1116 residue).

    *dues* maps each compressible detail-line key to whether it is due to
    render on **this** boundary. A key that is due gets its streak
    incremented; a key that is not due resets to 0 — so a line that clears
    and later reappears starts over at "new", full form. Persisted in hook
    state (fresh subprocess per hook fire), same discipline as
    :data:`EVENTS_SEEN_KEY`.
    """
    store = state.get(REPEAT_COUNTS_KEY)
    if not isinstance(store, dict):
        store = {}
    updated: dict[str, int] = {}
    for key, due in dues.items():
        updated[key] = (int(store.get(key) or 0) + 1) if due else 0
    state[REPEAT_COUNTS_KEY] = updated
    return updated


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
    pending_known: bool,
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
    census: str | None = None,
    notices: list[Any] | None = None,
    finished_spawns: list[dict[str, Any]] | None = None,
    event_seen: dict[str, dict[str, Any]] | None = None,
    inbox_pointer: str | None = None,
    armed: list[Any] | None = None,
    gate_receipt_data: dict[str, Any] | None = None,
    plan: "promises.Blueprint | None" = None,
    plan_edge: bool = False,
    ambient_emit: bool = True,
    route: "course.Course | None" = None,
    route_edge: bool = False,
    route_prompt: bool = False,
    bolt_asks_total: int | None = None,
    bolt_edge: bool = False,
    repeat_streaks: dict[str, int] | None = None,
    pending_set_changed: bool = True,
    last_chips: dict[str, str] | None = None,
    rendered_chips: dict[str, str] | None = None,
    route_drift: bool = False,
    route_stall: bool = False,
    mood_drift: bool = False,
    assign_view: "assignments.LedgerView | None" = None,
    assign_facts: dict[str, Any] | None = None,
    assign_edge: bool = False,
) -> str | None:
    """The mid-run (``post-tool``) status bar: preamble + changed chips + details.

    w-54 (2026-08-19): the bar opens with the one static element —
    ``⌁[<mood>]:`` (:func:`_bar_preamble`) — and every chip after it is
    **change-gated**: it renders only when its text differs from what the
    last *rendered* bar carried (*last_chips*), or when its own edge fires
    (course on ``route_edge``/``route_prompt``/``route_drift``, bolt on
    ``bolt_edge``/``route_prompt``, owed on ``plan_edge``) or it is an
    obligation standing unmet (a not-ok card, an unknown pending count).
    A boundary with no due chip and no detail lines injects **nothing**.

    *rendered_chips*, when given, is filled with every laden chip's current
    text so the caller can commit it as the next boundary's *last_chips* —
    only after this boundary actually rendered (commit-on-render, #728's
    discipline). *last_chips* ``None`` means everything is due — the
    conservative default for direct calls and the run's first bar.

    ``ambient_emit`` is accepted for caller compatibility but no longer
    filters chips: per-chip change-gating subsumes the class-wide
    quiet-boundary filter it used to drive.

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

    *pending_set_changed* (:func:`_pending_set_changed`, caller-owned per the
    same "run state, not snapshot state" division of labour as *plan_edge* /
    *route_edge*) gates the pending-events header between its two forms: the
    full instruction sentence on the pending SET's own edge (first laden
    boundary, a new event id, or a count increase), a one-line compact count
    on a boundary where the set stands unchanged from the one before it. The
    per-event rows below the header (:func:`_render_event_rows`) and their
    own seen/changed collapse are untouched by this — this only controls
    which header sentence precedes them. Defaults ``True`` so a caller that
    never computed the edge (a direct :func:`format_delta` call, most
    existing tests) gets the conservative always-full behaviour.

    *assign_view* / *assign_facts* / *assign_edge* are the ignition ledger's
    boundary read (w-69), owned by the caller (:func:`compute_neutral`) for
    ``plan_edge``'s reason: retirement and overdue state are run state, not
    snapshot state. The chip is ambient like ``course`` — the *edge* is what
    opens a boundary, at the caller's gate, and the detail lines render only
    on it.
    """
    segments: list[tuple[str, str]] = []
    budget_chip = _budget_chip(budget)
    if budget_chip:
        segments.append(("budget", budget_chip))
    quota_chip = _quota_chip(resources)
    if quota_chip:
        segments.append(("quota", quota_chip))
    assign_chip = assignments.chip(assign_view)
    if assign_chip:
        # The ignition assignments' standing fact (w-69). Deliberately absent
        # from the gate below, like the `orient x/y` meter it absorbed: the
        # chip rides boundaries the bar renders anyway; only an escalation
        # *edge* (overdue transition, level bump, discharge) opens one, via
        # `assign_edge` at the caller's render gate.
        segments.append(("assign", assign_chip))
    if census:
        # Sits beside `orient` because both describe the *wake*, not the run:
        # what the boot cost, and how much of it has been walked. Never in the
        # gate below, for `orient`'s reason and one of its own — a value that
        # is constant for a whole run must never be what keeps the bar alive.
        segments.append(("census", census))
    siblings_chip = _siblings_chip(resources)
    if siblings_chip:
        segments.append(("siblings", siblings_chip))
    keepalive_chip = _keepalive_chip(budget)
    if keepalive_chip:
        segments.append(("keepalive", keepalive_chip))
    delivery_chip = _delivery_chip(outbound)
    if delivery_chip:
        segments.append(("delivery", delivery_chip))
    if not pending_known:
        segments.append(("pending_unknown", "✉?"))
    elif pending > 0:
        # The count chip: seen-only pending events collapse here instead of
        # repeating as detail rows. Change-gated (no edge_due entry) — the
        # chip rides boundaries that render for other reasons; it does not
        # keep the bar alive by itself when events are all unchanged.
        segments.append(("pending", f"pending {pending}"))
    produce_total = _produce_total(produce)
    if produce_total:
        segments.append(("produce", f"⚒{produce_total}"))
    # Beside the produce count, because they are the same fact in two tenses
    # (#1008). Gateless like `⚒`: an outstanding promise is an obligation,
    # but the *chip* is its ambient half and must not manufacture a boundary
    # by itself — the `owed` detail line below does that, once per change.
    owed_chip = promises.chip(plan) if plan is not None else None
    if owed_chip:
        segments.append(("owed", owed_chip))
    # The route's standing fact, beside the blueprint's: what the run told
    # itself, next to what it told the world (#1008's two tenses, third
    # person). Gateless like `owed` — the detail line below earns the
    # boundary, the chip only rides one.
    course_chip = course.chip(route)
    if course_chip:
        segments.append(("course", course_chip))
    # The bolt CHIP retired 2026-08-19 (his call, evt-…-mhrx: "I honestly
    # don't see the value… the bolt should hold the asks, not events"). It
    # counted pending *events* while wearing the word "asks", and restated
    # what `owed`/`⚒`/the pending rows already carry. The half that earns
    # its life stays untouched and daemon-side: the cut-validator — the
    # declaration diffed against attested state, bounce-cap-3 — which
    # bounced this very run's premature closeout the evening this chip was
    # removed. ``bolt_asks_total``/``bolt_edge`` stay accepted so callers
    # and the seen-ledger keep working; they no longer render here.
    notices_chip = _notices_chip(notices or [])
    if notices_chip:
        segments.append(("notices", notices_chip))
    # Ambient, like the produce count: it never opens the gate by itself.
    # A run that just gated already has a boundary; what this buys is that
    # the verdict is on screen at *every* later boundary without a grep.
    gate_chip = _gate_chip(gate_receipt_data)
    if gate_chip:
        segments.append(("gate", gate_chip))
    if mood:
        # The steady face lives in the preamble now (w-54); this segment
        # keeps only the forms with an act attached. The surprise form sets
        # the mood the resident claimed beside the thing that just went
        # wrong and lets the mismatch do the work (#604) — deictic, per the
        # weave's own measure of a mark. The unresolved-handle form keeps
        # `_mood_chip`'s honesty: a face that resolves to no emote is
        # actionable (rewrite the file), so it renders — change-gated, once
        # per text — until the handle resolves.
        mood_text = _mood_chip(mood)
        if surprise:
            segments.append(("mood", f"mood {mood_text} ← {surprise}"))
        elif mood_text.startswith("✗"):
            segments.append(("mood", f"mood {mood_text}"))
        elif mood_drift:
            # The face stood still through _MOOD_DRIFT_THRESHOLD work-moves
            # (his ask, evt-…-mhrx): one `still?` beside the claimed face —
            # the ask is the mismatch check, the touch is the discharge.
            segments.append(("mood", f"mood {mood_text} — still?"))
    # (The `mood?`/`topic?` blank-claim nudges retired 2026-08-20 — w-69:
    # the claims assignment carries both, with an escalation ladder instead
    # of a latch/counter pair.)
    card_chip = _card_chip(card, card_stale)
    if card_chip:
        segments.append(("card", card_chip))

    details: list[str] = []
    repeat_streaks_in = repeat_streaks or {}
    if notices_chip:
        # #1116 residue: every other OBLIGATION-class chip gets a detail
        # line naming the act that clears it — `!N` didn't. Reuse the count
        # already computed for the chip (`!N`) rather than recomputing it.
        notices_count = int(notices_chip[1:])
        notices_streak = repeat_streaks_in.get("notices", 0)
        if notices_streak >= _REPEAT_COMPRESS_THRESHOLD:
            # Compression-on-repeat (#1116 residue, design-the-live-loop.md
            # §1): the boilerplate sentence is what repeats byte-for-byte
            # every boundary the count stands unaddressed — the compact form
            # keeps the live count and the discharge surface, drops the
            # sentence.
            details.append(
                f"- !{notices_count} · seen ×{notices_streak} — "
                "portal-state.json → notices"
            )
        else:
            details.append(
                f"!{notices_count} — {notices_count} directive"
                + ("s" if notices_count != 1 else "")
                + " refused/dropped this run. Read `portal-state.json` → "
                "`notices` for the text; a refused outbox file is deleted "
                "exactly like an accepted one, so this is the only way to see "
                "what was lost."
            )
    if assign_edge and assign_view is not None:
        # The ignition's loud half (w-69): overdue rows, grown to their
        # unlocked level, rendered only on the ledger's own edge — a level
        # bump changes the text, which renders once; between bumps the
        # `assign k/n` chip carries the standing fact for nine characters.
        details.extend(
            assignments.detail_lines(assign_view, assign_facts or {})
        )
    if pending:
        # Same framing fix as the prose form (2026-07-05): a bare count reads
        # as ambient telemetry, so non-zero pending gets an explicit verb —
        # applies *more* here, since a dense bar habituates faster than prose.
        # The full sentence is the pending SET's own edge (`pending_set_changed`,
        # caller-owned — first laden boundary / new id / count increase); an
        # unchanged set with no new/changed event rows needs no sentence at all —
        # the `pending N` chip on the bar line carries the standing fact.
        # skip_seen=True: seen-only events are accounted for by the
        # `pending N` chip in the bar line; detail rows only for new/changed.
        event_rows = _render_event_rows(
            events, event_seen, inbox_pointer, skip_seen=True
        )
        if event_rows or pending_set_changed:
            # Something new to say: either a new/changed event row or the
            # first-render instruction sentence. Show the sentence only on the
            # pending set's own edge; let the event rows carry changed bodies.
            if pending_set_changed:
                details.append(
                    f"{pending} pending event(s), {pending_files} undelivered outbox "
                    "file(s). Address each below with an `event:` reply, or retire it "
                    "deliberately with `note:`, before your next plan boundary or "
                    "closeout."
                )
            details.extend(event_rows)
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
    # The route reminder: on the course's own delta (the resident edited its
    # plan — confirm the chip moved), on the boundary a fresh event landed
    # (``route_prompt`` — the derailment moment; a steer needs the current
    # row in the loud zone before "continue or turn" is decidable), and on
    # drift (``route_drift``, w-54): the run's work-facts moved
    # _COURSE_DRIFT_THRESHOLD times while the route stood still — the one
    # moment "update the tracker" is evidence rather than a clock. Never
    # per-boundary while merely standing: that is the *fires constantly for
    # a non-reason* death, and the chip already carries the standing fact.
    if route_edge or route_prompt or route_drift:
        route_line = course.current_line(route)
        if route_line:
            if route_drift and not (route_edge or route_prompt):
                route_line += (
                    " — the run has moved "
                    f"{_COURSE_DRIFT_THRESHOLD}× since the route did: check "
                    "a row, or redraw the plan"
                )
            details.append(route_line)
    # The course stall (2026-08-23): _COURSE_STALL_THRESHOLD boundaries have
    # passed with open rows and no route edit — distinct from drift (which
    # counts *work* moves; stall counts *any* boundary). Fires once per
    # threshold crossing. Vetoed by a concurrent route_edge/route_prompt/
    # route_drift — those already re-surface the plan row with a richer
    # message.
    if route_stall and not (route_edge or route_prompt or route_drift):
        stall_chip = course.chip(route)
        if stall_chip:
            details.append(
                f"- {stall_chip} · stalled ×{_COURSE_STALL_THRESHOLD} boundaries"
                " — open rows exist but the plan hasn't moved:"
                " check a row or redraw"
            )
    streaks = repeat_streaks_in
    if budget.get("long_running"):
        limit = budget.get("budget_seconds")
        if streaks.get("running_long", 0) >= _REPEAT_COMPRESS_THRESHOLD:
            details.append(
                f"- running long · seen ×{streaks['running_long']} — .keepalive"
            )
        else:
            details.append(
                f"- running long: past the {limit}s soft budget — extend via "
                ".keepalive if the work needs it, else wind down."
            )
    # (The standalone `.name?` nudge line retired 2026-08-20 — w-69: the
    # claims assignment carries the name beside mood and topics.)
    if card_stale:
        age = card.get("age_seconds")
        age_txt = f"{age}s" if age is not None else "a while"
        moved = card.get("state_moved_seconds")
        if streaks.get("card_stale", 0) >= _REPEAT_COMPRESS_THRESHOLD:
            details.append(
                f"- card stale ({age_txt}) · seen ×{streaks['card_stale']} "
                "— rewrite .card"
            )
        elif card.get("active") and moved is not None:
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

    # ── The due-filter (w-54): change-gating replaces the laden gate. ──
    #
    # Every laden chip's current text is recorded (for the caller to commit
    # as the next boundary's *last_chips* — commit-on-render, its job); a
    # chip *renders* only when it is news: its text moved since the last
    # rendered bar, its own edge fired, or it is an obligation standing
    # unmet whose repetition is the point (a not-ok card, an unknown
    # pending count, the caller-latched nudges). The old class-wide
    # `resources_laden` opener is gone — it held the bar open at every
    # boundary because quota is always known, which is exactly the
    # habituation w-54 ends. Obligations keep their nets elsewhere: the
    # detail lines above, the fresh-event re-render (`route_prompt`), the
    # Stop readback, and the bolt's own validation at the cut.
    chips_now = dict(segments)
    if rendered_chips is not None:
        rendered_chips.clear()
        rendered_chips.update(chips_now)
    edge_due = {
        # An unknown obligation count must never go quiet.
        "pending_unknown": True,
        # `stale` repeats until discharged (rewriting the card is the act) —
        # it rides beside its own detail line, streak-compressed above.
        # `blank`/`cut` announce their transitions via change-gating like
        # everything else; the caller-latched nudges (`mood?`, `topic?`)
        # render once by the same rule — commit-on-render means a rendered
        # chip was seen, so the old miss-a-quiet-boundary caps are moot.
        "card": card_stale,
        # A surprise is fresh news even when its text repeats — the caller
        # already latches it to the clean→broken transition; a drift ask is
        # its own edge the same way.
        "mood": bool(surprise) or mood_drift,
        # Position trackers re-surface on their own edges even when the
        # numbers happen not to have moved.
        "course": route_edge or route_prompt or route_drift or route_stall,
        "owed": plan_edge,
        # The ignition ledger rides its own transitions (w-69): an overdue
        # edge or level bump changes the details, not necessarily the chip
        # text, so the chip is forced due to stand beside them.
        "assign": assign_edge,
    }

    def _due(key: str, text: str) -> bool:
        if edge_due.get(key):
            return True
        if last_chips is None:
            return True
        return last_chips.get(key) != text

    kept = [(key, text) for key, text in segments if _due(key, text)]
    if not kept and not details:
        return None
    bar = " │ ".join(text for _, text in kept)
    line = _bar_preamble(mood) + ((" " + bar) if bar else "")
    return line + ("\n" + "\n".join(details) if details else "")


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
    census: str | None = None,
    note_routing: bool = False,
    event_seen: dict[str, dict[str, Any]] | None = None,
    inbox_pointer: str | None = None,
    gate_receipt_data: dict[str, Any] | None = None,
    plan: "promises.Blueprint | None" = None,
    plan_edge: bool = False,
    ambient_emit: bool = True,
    route: "course.Course | None" = None,
    route_edge: bool = False,
    route_prompt: bool = False,
    bolt_asks_total: int | None = None,
    bolt_edge: bool = False,
    repeat_streaks: dict[str, int] | None = None,
    pending_set_changed: bool = True,
    no_reply_streak: int = 0,
    no_reply_capped: bool = False,
    last_chips: dict[str, str] | None = None,
    rendered_chips: dict[str, str] | None = None,
    route_drift: bool = False,
    route_stall: bool = False,
    mood_drift: bool = False,
    assign_view: "assignments.LedgerView | None" = None,
    assign_facts: dict[str, Any] | None = None,
    assign_edge: bool = False,
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
    point, 2026-06-23). When the capsule is unavailable, the same line says
    the count is unknown instead of laundering absence into that all-clear.
    Stop additionally surfaces the local SCM posture (unpushed commits /
    modified files) so a wake about to end sees its branch is not yet pushed.

    Mid-run (``post-tool``) renders as the single compact status bar
    :func:`_render_bar` builds — one line per boundary, working-register
    style, from the fixed :data:`BAR_SEGMENTS` vocabulary — with detail lines
    below it only for new obligations. It stays gated and returns ``None``
    when nothing shifted, so the channel injects no noise — except card
    staleness (2026-07-05) and non-zero pending events, which always earn a
    detail line: a stale-or-blank ``.card`` or an unaddressed follow-up is a
    mid-run failure, not one that can wait for closeout or be buried in a
    glyph. An unavailable count is the compact ``✉?`` chip — neither a false
    zero nor silence.

    ``mood`` is the resident's own `.mood` control file (#566 layer 2), read
    fresh by the caller (:func:`_read_mood`) at every boundary — rendered as
    a bar segment mid-run, or its own prose line at seed/stop.

    ``assign_view`` / ``assign_facts`` / ``assign_edge`` are the ignition
    ledger's boundary read (w-69), computed by the caller off
    ``boot-score.json`` + this boundary's observable acts
    (:func:`brr.assignments.advance`) — the seed header names the ledger,
    the mid-run bar carries its chip and, on an edge, its overdue rows, and
    the closeout reads back what was never discharged or deferred.

    ``census`` is the wake census (#739), computed by the caller
    (:func:`_wake_census`) off `boot-score.json` — a mid-run bar segment
    only: seed already names the boot's shape, and by
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

    ``gate_receipt_data`` is this run's own tree's entry from
    ``.gate-receipts.json`` (``gate_receipt.read_receipt``, keyed by
    ``ctx.repo_dir`` — #820), read by the caller for ``mood``'s reason — it is
    an outbox artifact, not part of the portal snapshot, and the resident
    rewrites it mid-run by gating again (#1048). Mid-run bar segment only: at
    seed there is never a receipt, and at stop :func:`_gate_closeout_clause`
    already speaks with the repo dir in hand and far more standing than a
    chip has.
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

    pending_raw = attention.get("pending_event_count")
    pending_known = pending_raw is not None
    try:
        int(pending_raw or 0)
    except (TypeError, ValueError):
        pending_known = False
    try:
        pending_files = int(attention.get("pending_outbox_file_count", 0) or 0)
    except (TypeError, ValueError):
        pending_files = 0
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
            run=run, pending=action_pending, pending_known=pending_known,
            pending_files=pending_files,
            events=action_events,
            budget=budget, outbound=outbound, produce=produce, card=card,
            card_stale=card_stale, resources=resources, run_name=run_name,
            mood=mood, surprise=surprise,
            census=census,
            notices=notices, finished_spawns=finished_spawns,
            event_seen=event_seen, inbox_pointer=inbox_pointer,
            armed=armed, gate_receipt_data=gate_receipt_data,
            plan=plan, plan_edge=plan_edge,
            ambient_emit=ambient_emit,
            route=route, route_edge=route_edge, route_prompt=route_prompt,
            bolt_asks_total=bolt_asks_total, bolt_edge=bolt_edge,
            repeat_streaks=repeat_streaks,
            pending_set_changed=pending_set_changed,
            last_chips=last_chips, rendered_chips=rendered_chips,
            route_drift=route_drift, route_stall=route_stall, mood_drift=mood_drift,
            assign_view=assign_view, assign_facts=assign_facts,
            assign_edge=assign_edge,
        )

    lines: list[str] = []
    # Only seed/stop reach this point — post-tool returned via `_render_bar`
    # above — so this is always one of the two verbose-prose headers.
    #
    # The seed's header is the ignition frame (w-69, fork 4 signed: the
    # fold-in replaces the portal seed's prose outright) whenever this wake
    # carries an assignment list: the obligations live as typed rows in the
    # boot kernel, so the seed stops re-instructing and instead names the
    # ledger and its discharge grammar once. The event rows below stay — they
    # are the pending rows' own discharge surface, and they carry the bodies.
    # A wake with no assignment list (no boot score: ad-hoc, older daemon)
    # keeps the pre-w-69 header verbatim.
    if seed and assign_view is not None and assign_view.total:
        header_line = (
            f"[brnrd ignition] {assign_view.total} assignment(s) — the boot "
            "kernel carries the rows; discharge each, adopt them as your "
            ".card ## Plan, or defer with a named reason on the card."
        )
        if pending_known:
            header_line += (
                f" {action_pending} pending event(s), {pending_files} "
                "undelivered outbox file(s)."
            )
        else:
            header_line += (
                " Could not count pending event(s) — portal state did not "
                "provide a count."
            )
        lines.append(header_line)
    else:
        header = "brnrd portal seed" if seed else "brnrd portal closeout"
        # Framing, not just data: a bare count reads as ambient telemetry and
        # habituates fast — a maintainer caught this live (2026-07-05) when two
        # follow-ups sat unacknowledged on the outward-facing card for 8 minutes
        # despite the count appearing in every batch. Non-zero pending events get
        # an explicit action verb so the line reads as something to do, not
        # something to note; zero stays the plain affirmative-clear line.
        # Finished spawns are excluded from the obligation count — they are facts,
        # not messages with correspondents.
        if pending_known:
            header_line = (
                f"[{header}] {action_pending} pending event(s), "
                f"{pending_files} undelivered outbox file(s)."
            )
        else:
            header_line = (
                f"[{header}] could not count pending event(s) — "
                "portal state did not provide a count."
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
    live_children = _live_child_handover_line(
        {"run": run, "resources": resources}
    )
    if live_children:
        lines.append(live_children)
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
    # The course's closeout read-back, beside the blueprint's (#1008's two
    # tenses): every open route row, named, unlatched at stop for the same
    # reason — the closeout is the moment the surface exists for. Inject-only,
    # never a block: the course is a self-report, so the honest ask is
    # disposition, not a wall. Silent at seed (a fresh run has no card yet)
    # and silent when the route is finished or absent.
    if stop:
        lines.extend(course.stop_lines(route))
        # The ignition's own readback beside the course's (w-69): rows never
        # discharged or deferred, named once at the closeout. The waking-event
        # and pending kinds are excluded inside `stop_lines` — the delivery
        # clause and the pending-event block own those seams already.
        lines.extend(assignments.stop_lines(assign_view))
        # The bolt (design-the-bolt.md): the closing act, named — or, once
        # cut, affirmed. An accepted bolt collapses the ask (the polarity
        # flip: the declaration was validated at `cut:` time); the annotated
        # count rides the line so a forced accept is never mistaken for a
        # clean one.
        stop_bolt = payload.get("bolt") if isinstance(payload.get("bolt"), dict) else {}
        if stop_bolt.get("accepted"):
            annotated_n = int(stop_bolt.get("annotated") or 0)
            lines.append(
                "- bolt: accepted — the cut stands."
                if not annotated_n else
                f"- bolt: accepted, annotated — {annotated_n} check(s) "
                "unresolved rode the delivered body."
            )
        else:
            lines.append(
                "- bolt: declare this run's completion with `brnrd cut` before "
                "closing — asks dispositioned, produce attested (legal-minimal: "
                "attesting none is fine, declared as such), no promise left "
                "unaccounted for."
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
            # #1481: the example directive names whichever gate
            # ``delivery.gate`` actually resolves to on this run, not a
            # literal — an unresolvable gate drops the parenthetical
            # rather than naming one that would only be refused.
            gate_name = _stop_gate_name(payload)
            gate_example = f" (`gate: {gate_name}`)" if gate_name else ""
            lines.append(
                "- delivery: nothing communicated on any thread yet — no "
                "gate owns this waking event, so nothing dispatches your "
                "final message: it is captured to the response path as this "
                "run's body/message store only. Report on a configured user "
                f"gate{gate_example} if this run has something to say. A "
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
            #
            # #1142: this arm re-fires unbounded on a run whose waking
            # thread never accrues a reply (e.g. a gate delivers the
            # substance instead) — a Stop hook is a fresh subprocess, so
            # without a persisted counter each firing reads exactly like the
            # first: "will be dispatched" is correct and reassuring on
            # firing #1, actively misleading by firing #2 (the same promise,
            # still unresolved). ``no_reply_streak`` is that counter — the
            # caller's own per-firing tally (:func:`_bump_stop_no_reply_streak`),
            # threaded in like ``note_routing``/``repeat_streaks`` because
            # this renderer is a pure function of the snapshot and "how many
            # times has this exact thing already been said" is run state,
            # not snapshot state.
            prior = max(0, no_reply_streak - 1)
            if no_reply_streak >= _STOP_NO_REPLY_ESCALATE_THRESHOLD:
                # #1296 belt-and-suspenders: the verdict below is a one-time
                # statement — "this is a routing problem, use gate:" — not a
                # progress ticker. Once said, saying it again on every later
                # Stop while the streak climbs (7 times, live, on a run whose
                # `current_event_replyable` was wrong for reasons upstream of
                # this file — see `daemon._spawn_parent_still_collecting`) is
                # pure noise repeating a verdict already delivered. Capped
                # independently of whatever `gate_less` computed, so a future
                # bug in that upstream read still cannot renag forever.
                if not no_reply_capped:
                    # Past the threshold the state-description sentence
                    # itself is now a false promise (it already failed to
                    # resolve ``prior`` times), so this reads as a verdict
                    # instead — pointing at the one delivery path confirmed
                    # to work from inside this same loop: an outbox
                    # ``gate:`` file (#1142's own "workaround that worked"
                    # section).
                    lines.append(
                        f"- delivery: stop boundary #{no_reply_streak} · {prior} "
                        f"prior terminal message{'s' if prior != 1 else ''} "
                        "consumed, current=0 — recurring, not pre-dispatch: this "
                        "promise has now failed to resolve "
                        f"{prior} time{'s' if prior != 1 else ''} in a row. "
                        "Probable delivery-path problem — report through "
                        "`gate: <name>` instead; an outbox gate delivers mid-run "
                        "and does not depend on this line ever clearing."
                    )
            else:
                lines.append(
                    f"- delivery: stop boundary #{no_reply_streak} · {prior} "
                    f"prior terminal message{'s' if prior != 1 else ''} "
                    "consumed, current=0 — the waking thread itself has no "
                    "reply yet; your final message will be dispatched there "
                    "by the daemon; end on the reply, not on scratch."
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
            # #1481: same live-resolved gate name as the silence arm above —
            # "configured gate" with no example when nothing resolves.
            gate_name = _stop_gate_name(payload)
            gate_clause = f"`gate: {gate_name}`" if gate_name else "configured gate"
            lines.append(
                "- delivery: routing fact, stated once — no gate owns this "
                "waking event, so nothing dispatches your final message "
                "however much has already gone out: stdout is captured to "
                "the response path as this run's body/message store only. "
                f"Content the reader must see rides a {gate_clause} "
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


def _linger_opted_out(ctx: "HookContext") -> bool:
    """Whether this run consciously declined the live-chat linger."""
    if ctx.outbox_dir is None:
        return False
    try:
        text = (ctx.outbox_dir / portals.LINGER_OPT_OUT_NAME).read_text(
            encoding="utf-8"
        )
    except OSError:
        return False
    return any(line.strip() for line in text.splitlines())


def _linger_closeout_clause(ctx: "HookContext") -> str | None:
    """Require a completed linger, or an explicit reason for skipping it."""
    if ctx.outbox_dir is None or _linger_opted_out(ctx):
        return None
    until = portals.keepalive_until(ctx.outbox_dir / portals.KEEPALIVE_NAME)
    if until is not None and until <= time.time():
        return None
    state = (
        "no parseable `.keepalive` was armed"
        if until is None
        else "the `.keepalive` horizon is still live — exiting now would not linger"
    )
    return (
        f"{state}. A cloud conversation lingers by default: deliver first, "
        "run `brnrd await --timeout 30m`, and close only after that horizon "
        "resolves quiet. If stopping now is deliberate, write the reason as "
        f"the first line of `{portals.LINGER_OPT_OUT_NAME}`"
    )


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
    if not run_id:
        return None
    if facet is None:
        return None
    owned = facet.get("owned_children")
    if isinstance(owned, list):
        for entry in owned:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("parent_run_id") or "").strip() == run_id:
                return True
        return False
    if facet.get("status") not in ("known", "absent"):
        return None
    siblings = facet.get("siblings")
    for entry in siblings if isinstance(siblings, list) else []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("parent_run_id") or "").strip() == run_id:
            return True
    return False


def _live_child_handover_line(payload: dict[str, Any]) -> str | None:
    """Render the advisory closeout handover when child edges remain live."""
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    run_id = str(run.get("id") or "").strip()
    if not run_id:
        return None
    resources = (
        payload.get("resources")
        if isinstance(payload.get("resources"), dict) else {}
    )
    facet = (
        resources.get("coexisting_runs")
        if isinstance(resources.get("coexisting_runs"), dict) else {}
    )
    rows: list[str] = []
    owned = facet.get("owned_children")
    if isinstance(owned, list):
        for entry in owned:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("parent_run_id") or "").strip() != run_id:
                continue
            child_id = str(entry.get("run_id") or entry.get("event_id") or "").strip()
            if child_id:
                rows.append(child_id)
    elif facet.get("status") == "known":
        siblings = facet.get("siblings")
        for entry in siblings if isinstance(siblings, list) else []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("parent_run_id") or "").strip() != run_id:
                continue
            child_id = str(entry.get("run_id") or entry.get("id") or "").strip()
            if child_id:
                rows.append(child_id)
    if not rows:
        return None
    rows = sorted(dict.fromkeys(rows))
    return (
        f"- ▷ {len(rows)} child run(s) still live — {', '.join(rows)} — "
        "steering them passes to the next run on this thread. Cutting "
        "here needs a `strands:` row per child (handoff / converged / "
        "stopped / abandoned), or the bolt bounces on it."
    )


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
        text = (outbox_dir / relics.PR_CONTROL_NAME).read_text(encoding="utf-8").strip()
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

    # `gate_receipt.read_receipt` collapses absent outbox, absent repo root,
    # absent file, unreadable file, malformed file, *and an entry for a
    # different tree* into `None`. All of that means *no trustworthy record
    # that this tree's gate ran*, and the direction this claim is allowed to
    # be wrong in is the pessimistic one (#820: a receipt for a tree this
    # guard is not asking about must not be able to satisfy it).
    receipt = gate_receipt.read_receipt(ctx.outbox_dir, repo)
    if not receipt:
        return (
            f"the gate never ran — {changed} commit(s) and a changed tree, no "
            f"`{ctx.gate_command}` receipt for {repo}. The test command you "
            f"remember is one leg of what CI runs; run `brnrd gate-run` (it "
            f"runs `{ctx.gate_command}` and writes this receipt) before "
            f"claiming green"
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
            f"the gate ran on a different tree than the one you are ending "
            f"on ({repo}) (receipt: {str(receipt.get('verdict') or '?')} at "
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
            f"({repo}) as it was before that change. Re-run `brnrd gate-run` "
            f"now that the tree is still"
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

    if "linger" in ctx.closeout_obligations:
        linger_clause = _linger_closeout_clause(ctx)
        if linger_clause:
            unmet.append(linger_clause)

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
    portal_unavailable = not portal
    if portal_unavailable:
        # A missing, unreadable, malformed, or non-object capsule is not an
        # all-clear. Carry an explicit unknown into the normal renderer so
        # seed/closeout can state the measurement failure without inventing
        # either an event count or a second error-only rendering path.
        portal = {
            "attention": {
                "pending_event_count": None,
                "pending_outbox_file_count": None,
            },
        }
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
    if mood is not None:
        state[MOOD_EVER_WRITTEN_KEY] = True
    # Same fresh-read discipline, for the topic-discoverability chip's own
    # reason: the resident may write `.topics` between hook fires, and the
    # chip's whole job is to disappear the moment a claim lands.
    topics = _read_topics(ctx)
    # Re-read every boundary, like `.mood` and `.card`: a resident gates
    # more than once in a long run, and a cached verdict is exactly the
    # stale claim this chip exists to make visible. `gate_receipt.read_receipt`
    # collapses absent/unreadable/malformed/wrong-tree to `None` (#820), and
    # the chip renders nothing for all four — a run that has not gated *this*
    # tree is not a run that failed.
    gate_receipt_data = gate_receipt.read_receipt(ctx.outbox_dir, ctx.repo_dir)
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
    # The course (`.card` §Plan/§Course), read fresh for the same reason as
    # the blueprint above — a control file the portal token never sees, so
    # its only path to the boundary is its own latch. Parsed off the card
    # body; a card with no checkbox section costs one regex scan and
    # renders nothing.
    route = course.parse(_read_card_body(ctx))
    route_token = course.token(route)
    route_edge = (
        route is not None and route_token != state.get("route_token")
    )
    state["route_token"] = route_token

    # The course drift trigger (w-54): the course is a promise about work,
    # and the work is already visible — produce, delivery, the gate verdict.
    # So the reminder fires exactly when the run moves and the route doesn't:
    # _COURSE_DRIFT_THRESHOLD work-deltas with no route edit re-surface the
    # current row once, then the counter re-arms. Endogenous — evidence of
    # divergence, not a clock, and the act that discharges it (editing the
    # card) is the act it asks for.
    portal_outbound = (
        portal.get("outbound") if isinstance(portal.get("outbound"), dict) else {}
    )
    work_token = "|".join(str(part) for part in (
        _produce_total(portal_produce),
        portal_outbound.get("replies_current") or 0,
        portal_outbound.get("replies_other") or 0,
        portal_outbound.get("outbound_messages") or 0,
        (gate_receipt_data or {}).get("head") or "",
        (gate_receipt_data or {}).get("verdict") or "",
    ))
    route_drift = False
    prev_work_token = state.get(WORK_TOKEN_KEY)
    work_moved = prev_work_token is not None and work_token != prev_work_token
    if route is not None and route.open_rows:
        if route_edge:
            state[COURSE_DRIFT_COUNT_KEY] = 0
        elif work_moved:
            drift_count = int(state.get(COURSE_DRIFT_COUNT_KEY) or 0) + 1
            if drift_count >= _COURSE_DRIFT_THRESHOLD:
                route_drift = True
                drift_count = 0
            state[COURSE_DRIFT_COUNT_KEY] = drift_count
    else:
        state[COURSE_DRIFT_COUNT_KEY] = 0
    # Course stall (2026-08-23): count *any* boundary where open rows exist
    # and the route didn't change (regardless of whether work moved — that
    # distinction belongs to drift). At _COURSE_STALL_THRESHOLD the stall
    # detail fires once and the counter re-arms. route_edge resets to 0.
    route_stall = False
    if route is not None and route.open_rows:
        if route_edge:
            state[COURSE_STALL_COUNT_KEY] = 0
        else:
            stall_count = int(state.get(COURSE_STALL_COUNT_KEY) or 0) + 1
            if stall_count >= _COURSE_STALL_THRESHOLD:
                route_stall = True
                stall_count = 0
            state[COURSE_STALL_COUNT_KEY] = stall_count
    else:
        state[COURSE_STALL_COUNT_KEY] = 0
    # The mood's drift, same engine (evt-…-mhrx): the face stood still while
    # the run visibly moved. Touching `.mood` — any text change — resets;
    # the ask re-arms after another full threshold of work-moves, so a
    # genuinely steady mood costs one `still?` per five shipped things.
    mood_drift = False
    if mood:
        if mood != state.get(MOOD_LAST_TEXT_KEY):
            state[MOOD_DRIFT_COUNT_KEY] = 0
        elif work_moved:
            mood_count = int(state.get(MOOD_DRIFT_COUNT_KEY) or 0) + 1
            if mood_count >= _MOOD_DRIFT_THRESHOLD:
                mood_drift = True
                mood_count = 0
            state[MOOD_DRIFT_COUNT_KEY] = mood_count
    else:
        state[MOOD_DRIFT_COUNT_KEY] = 0
    state[MOOD_LAST_TEXT_KEY] = mood
    state[WORK_TOKEN_KEY] = work_token

    # The bolt gauge (design-the-bolt.md §Accretion): T/A over the run's
    # whole lifetime, so computed once here — before the phase branch, like
    # `plan`/`route` above — rather than per-phase. The partition is shared
    # with the Stop branch below rather than re-run there.
    action_events, _finished_spawn_events, action_pending = (
        _partition_pending_events(portal)
    )
    bolt_asks_total = _commit_events_seen_all(
        state,
        (
            str(ev.get("id") or "")
            for ev in action_events if isinstance(ev, dict)
        ),
    )
    bolt_owed_total = sum(plan.owed.values()) if plan is not None else 0
    bolt_produce_total = _produce_total(portal_produce)
    bolt_asks_answered = max(0, bolt_asks_total - action_pending)
    bolt_token = json.dumps(
        [bolt_asks_total, bolt_asks_answered, bolt_owed_total, bolt_produce_total],
        separators=(",", ":"),
    )
    bolt_edge = bolt_token != state.get("bolt_token")
    state["bolt_token"] = bolt_token

    # The ignition assignments (w-69): rows off the persisted boot score
    # (same artifact-read doctrine as the census and the orientation set —
    # empty on any absence, so every consumer degrades to "no ledger"), and
    # the observable-act facts their discharge tests read. Built before the
    # phase dispatch because all three phases consume them: the seed names
    # the ledger, the boundary advances it, the closeout reads it back.
    assign_rows = (
        assignments.rows_from_score(_read_json(ctx.boot_score_path))
        if ctx.boot_score_path is not None else []
    )
    assign_view: assignments.LedgerView | None = None
    assign_facts: dict[str, Any] = {}
    # The orientation observation (Slice 4's instrument) runs whether or not
    # this wake carries assignments: completeness stays measurable even for
    # a wake whose score predates the ledger.
    orient_set_paths = _orientation_set_paths(ctx)
    orient_progress = _orientation_progress(ctx, payload, state)
    if assign_rows:
        portal_card = (
            portal.get("card") if isinstance(portal.get("card"), dict) else {}
        )
        portal_name = (
            portal.get("name") if isinstance(portal.get("name"), dict) else {}
        )
        assign_facts = {
            "replies_current": portal_outbound.get("replies_current"),
            "action_pending": action_pending,
            "card_active": bool(portal_card.get("active")),
            "course_rows": (
                [row.text for row in route.rows] if route is not None else None
            ),
            "name_written": bool(portal_name.get("written")),
            "mood_ever": bool(state.get(MOOD_EVER_WRITTEN_KEY)),
            "topics_claimed": bool(topics) or bool(produce_counts.get("item")),
            # Done = the walk closed for a real reason: progress reads None
            # while a non-empty set exists only at completion or a declared
            # skip (`_orientation_progress`'s three-way None).
            "orient_done": bool(orient_set_paths) and orient_progress is None,
            "orient_progress": orient_progress,
        }

    if phase == PHASE_SESSION_START:
        if assign_rows:
            # No ledger tick at the seed — the run has acted zero times.
            # The view exists so the header can name the total.
            assign_view = assignments.LedgerView(rows=assign_rows)
        inject = format_delta(
            portal, seed=True, mood=mood,
            event_seen=event_decisions, inbox_pointer=inbox_pointer,
            plan=plan, assign_view=assign_view,
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
        stop_token = (
            "__portal_state_unavailable__"
            if portal_unavailable else portal.get("change_token")
        )
        # #728: latch, don't gate. ``format_delta`` is a pure function of the
        # snapshot, so "already said once" has to be decided here — same
        # division of labour as ``orient`` and ``surprise``.
        gate_less_run = _stop_is_gate_less(portal)
        note_routing = gate_less_run and not state.get(GATELESS_ROUTING_KEY)
        # #1142: bumped on every Stop firing this is due, independent of the
        # ``stop_token`` render gate just below — a hook fires as a fresh
        # subprocess whether or not its content happened to dedupe against
        # the prior one, and the counter's job is to say how many times the
        # *firing* has happened, not how many times it rendered.
        no_reply_streak = _bump_stop_no_reply_streak(state, portal)
        # #1296: same "decide here, latch on the render" division of labour
        # as ``note_routing``/``GATELESS_ROUTING_KEY`` just above — computed
        # ahead of the call so ``format_delta`` stays a pure read of it.
        no_reply_capped = _stop_no_reply_escalation_capped(state, no_reply_streak)
        # The ignition ledger's closeout read (w-69): observe the final
        # batch's acts without spending a boundary — Stop can fire more than
        # once, and a re-fire is not a boundary the run lived.
        if assign_rows:
            assign_view = assignments.advance(
                assign_rows, state, facts=assign_facts, tick=False
            )
        if stop_token != state.get("stop_last_token"):
            inject = format_delta(
                portal, stop=True, run_body=_read_card_body(ctx), mood=mood,
                note_routing=note_routing,
                event_seen=event_decisions, inbox_pointer=inbox_pointer,
                plan=plan, route=route,
                no_reply_streak=no_reply_streak,
                no_reply_capped=no_reply_capped,
                assign_view=assign_view,
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
            # Same discipline for the escalated verdict (#1296): a render at
            # or past threshold has now said it once, whether or not this
            # particular firing was already capped — the write is idempotent
            # and only meaningful the first time it flips false → true.
            if no_reply_streak >= _STOP_NO_REPLY_ESCALATE_THRESHOLD:
                state[STOP_NO_REPLY_ESCALATED_KEY] = True
        state["stop_last_token"] = stop_token
        state["last_token"] = stop_token
    else:
        # A mood *edge*: something in the batch that just ran came back
        # wrong. Transition-stamped, not per-pass — the same discipline a
        # commit inside a retry loop needs. A run debugging a red test would
        # otherwise be told "something broke" at every boundary of the
        # debugging, which is the habituation this whole change exists to
        # avoid; the interesting moment is clean → broken, once.
        # Count subagent dispatches as they happen, so the Stop boundary can
        # tell whether the discriminator this run depends on actually fired
        # (see `_discriminator_drift_line`). Cheap, and the only place the
        # parent ever sees the dispatch.
        dispatched = _count_subagent_dispatches(payload)
        if dispatched:
            state[SUBAGENT_DISPATCH_KEY] = (
                int(state.get(SUBAGENT_DISPATCH_KEY) or 0) + dispatched
            )
        surprise = _tool_surprise(payload) if mood else None
        was_surprised = bool(state.get("mood_surprised"))
        edge = surprise if (surprise and not was_surprised) else None
        state["mood_surprised"] = bool(surprise)
        # The wake census (#739): a pure read of the score the daemon wrote
        # before this runner started. Computed here rather than inside the
        # renderer because `format_delta` stays a function of the portal
        # snapshot, and the score is not in it. (The orientation observation
        # — Slice 4's instrument — already ran above, in the assignment-facts
        # block, unconditionally: it must not depend on whether a bar renders
        # this boundary. The `orient x/y` segment it fed retired 2026-08-20
        # into the orient assignment row — w-69.)
        census = _wake_census(ctx)
        # (The blank-mood nudge retired 2026-08-20 — w-69: the claims
        # assignment carries the ask, with the same MOOD_EVER_WRITTEN fact
        # as its discharge input.)
        token = portal.get("change_token")

        # The ignition ledger's boundary tick (w-69): advance retirements
        # and the overdue clock against this boundary's observable acts.
        # Its edge — a discharge, an overdue transition, a level bump — is
        # a gate-opener below, exactly like `route_drift`: computed off run
        # state, so no portal token ever moves for it.
        assign_edge = False
        if assign_rows:
            assign_view = assignments.advance(
                assign_rows, state, facts=assign_facts
            )
            assign_edge = assign_view.edge

        # Three-class split (#1116): obligations, deltas, ambient vitals.
        #
        # ``has_obligations`` — content the resident must act to clear (pending
        # events, stale card, refused notices, armed letters, long-running).
        # Surfaces regardless of portal token and bypasses content dedup below —
        # an unmet obligation is *supposed* to repeat until discharged; byte-
        # identical is the signature of "nothing was done", not "nothing new
        # happened", and suppressing it recreates the #963 failure on a class of
        # content that was explicitly excluded from #963's scope.
        #
        # ``ambient_emit`` — ambient vitals (quota %, elapsed, orientation bar,
        # sibling count) have no discharge condition, so they must not repeat
        # every tick. First boundary always renders; thereafter only at
        # ``_AMBIENT_BUDGET_THRESHOLDS`` / ``_AMBIENT_QUOTA_THRESHOLDS`` crossings.
        #
        # Deltas and edges (delivery, finished spawns, mood edge, plan edge) ride
        # the token gate or their own edge-detection latches — unchanged from
        # before.
        pt_budget = (
            portal.get("budget") if isinstance(portal.get("budget"), dict) else {}
        )
        pt_resources = (
            portal.get("resources")
            if isinstance(portal.get("resources"), dict) else {}
        )
        # Whether any pending event is new or changed at this boundary — used
        # to decide if pending events count as an obligation (forcing the bar
        # to render) or merely as a count chip riding the bar if it renders
        # for other reasons. Computed before `has_obligations` so it can gate
        # the pending-event clause there.
        has_new_or_changed_events = any(
            d.get("status") in ("new", "changed")
            for d in (event_decisions or {}).values()
        )

        # The pending sermon's own set-changed edge (bar path only — seed and
        # stop keep the full sentence unconditionally): full instruction
        # sentence on the pending SET's own edge, compact one-liner on a
        # boundary where it stands unchanged from the one before it. Skipped
        # (and left at its unused default) when nothing is pending — the
        # detail line this gates does not render at all in that case, so
        # there is nothing to compress and nothing worth pruning the ledger
        # over. Computed *before* `has_obligations` — its side-effect (ledger
        # snapshot write) must run on every laden boundary, and `has_obligations`
        # uses `pending_new_or_changed` which depends on this result.
        pending_set_changed = (
            _pending_set_changed(state, action_events, action_pending)
            if action_pending
            else True
        )

        # An event obligation exists only when there is something new to see:
        # a fresh id, a changed body, or the first render of any pending set.
        # Seen-only events collapse to the `pending N` chip and never keep the
        # bar alive by themselves — that is the "annoying reminder" this
        # change ends (maintainer, 2026-08-23).
        pending_new_or_changed = has_new_or_changed_events or pending_set_changed

        has_obligations = _has_post_tool_obligations(
            portal, plan, surprise=edge, plan_edge=plan_edge,
            portal_unavailable=portal_unavailable,
            route=route, route_edge=route_edge, bolt_edge=bolt_edge,
            pending_new_or_changed=pending_new_or_changed,
        )
        ambient_emit = _ambient_should_emit(state, pt_budget, pt_resources)

        # The derailment prompt: a *fresh* event letter (new, or a changed
        # body — not a `seen ×N` reminder) is the moment "continue or turn"
        # has to be decided, so the current route row rides that boundary.
        # Keyed on the render decisions the event ledger already made; a
        # standing pending event does not re-prompt.
        route_prompt = route is not None and any(
            decision.get("status") in ("new", "changed")
            for decision in (event_decisions or {}).values()
        )

        # Compression-on-repeat (#1116 residue, design-the-live-loop.md §1):
        # advance each compressible detail line's consecutive-laden-boundary
        # streak, off the exact same "due" reads `_has_post_tool_obligations`
        # and `_render_bar` use for these lines — so the streak can
        # never fall out of step with what actually renders.
        pt_notices = (
            portal.get("notices") if isinstance(portal.get("notices"), list) else []
        )
        pt_card = portal.get("card") if isinstance(portal.get("card"), dict) else {}
        repeat_streaks = _bump_repeat_streaks(state, {
            "notices": bool(_counted_notices(pt_notices)),
            "running_long": bool(pt_budget.get("long_running")),
            "card_stale": bool(pt_card.get("stale")),
        })

        # Gate: open when there is something to say.  Obligations bypass the
        # token check; ambient and deltas use it as before. ``route_drift``
        # opens it for the blueprint edge's reason: the divergence it names
        # is computed off run state, so no portal token ever moves for it.
        token_moved = token is not None and token != state.get("last_token")
        last_chips = (
            state.get(BAR_LAST_CHIPS_KEY)
            if isinstance(state.get(BAR_LAST_CHIPS_KEY), dict) else None
        )
        rendered_chips: dict[str, str] = {}
        if (
            has_obligations or ambient_emit or edge or plan_edge
            or route_edge or bolt_edge or route_drift or route_stall
            or mood_drift or assign_edge or token_moved
        ):
            inject = format_delta(
                portal, mood=mood, surprise=edge,
                census=census,
                event_seen=event_decisions, inbox_pointer=inbox_pointer,
                gate_receipt_data=gate_receipt_data,
                plan=plan, plan_edge=plan_edge,
                ambient_emit=ambient_emit,
                route=route, route_edge=route_edge, route_prompt=route_prompt,
                bolt_asks_total=bolt_asks_total, bolt_edge=bolt_edge,
                repeat_streaks=repeat_streaks,
                pending_set_changed=pending_set_changed,
                last_chips=last_chips, rendered_chips=rendered_chips,
                route_drift=route_drift, route_stall=route_stall,
                mood_drift=mood_drift,
                assign_view=assign_view, assign_facts=assign_facts,
                assign_edge=assign_edge,
            )
            state["last_token"] = token
            if ambient_emit and inject is not None:
                _update_ambient_state(state, pt_budget, pt_resources)

        # Content dedup: ambient-only injections are hash-checked so a content-
        # stable bar does not re-inject on every token tick.  Obligation-carrying
        # injections bypass dedup — see the ``has_obligations`` note above.
        if not has_obligations:
            inject = _suppress_unchanged_inject(state, inject)

        # Commit-on-render (w-54): the chip ledger advances only when this
        # boundary actually injected — a suppressed bar must not mark its
        # changes as seen, or they would never render at all.
        if inject is not None and rendered_chips:
            state[BAR_LAST_CHIPS_KEY] = rendered_chips

    if phase == PHASE_STOP:
        # `action_events` / `action_pending` were already partitioned above
        # (shared with the bolt gauge's T/A, unconditionally computed before
        # the phase branch) — no need to re-run the same pure split here.
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
        #
        # The polarity flip (design-the-bolt.md §The polarity flip): an
        # accepted bolt IS the completion artifact — the daemon already
        # validated the declaration at `cut:` time, so the negative capsule
        # ("did you forget anything?") stands down. Pending events above are
        # deliberately NOT collapsed: a message that lands after the cut is
        # real attention, bolt or no bolt.
        bolt_facet = portal.get("bolt") if isinstance(portal.get("bolt"), dict) else {}
        if not block and not bolt_facet.get("accepted"):
            reason = _armed_closeout_block(ctx, payload, state, portal)
            if reason is not None:
                block = True
                block_reason = reason

        # Last: did the seam this run's isolation rests on actually fire?
        drift = _discriminator_drift_line(ctx, state)
        if drift is not None:
            inject = f"{inject}\n{drift}" if inject else drift

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
    if phase == PHASE_PRE_TOOL:
        return "PreToolUse"
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
        event_name = native_event_name(flavour, phase)
        out: dict[str, Any] = {}
        if phase == PHASE_PRE_TOOL:
            # A tool-call-level refusal, a different control surface from
            # Stop's turn-level block below. Claude's schema is
            # ``hookSpecificOutput.permissionDecision`` (fire-verified against
            # code.claude.com/docs/en/hooks, 2026-08-09) — not the top-level
            # ``decision``/``continue`` fields, which only Stop reads. Codex
            # does not install this phase at all today (`codex_hook_args`
            # covers only PostToolUse/Stop/SessionStart — its own hooks docs
            # verify no more than that), so this is claude-only in practice;
            # an unarmed codex ``pre-tool`` fire (should one ever happen)
            # falls through to the unblocked ``{}`` a no-op ``block=False``
            # already produces.
            if flavour == "claude" and block:
                out["hookSpecificOutput"] = {
                    "hookEventName": event_name,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason or "refused (#1184)",
                }
            return out, 0
        # Both Claude and Codex accept the same ``hookSpecificOutput``
        # injection envelope (fire-verified). They diverge only on stop-control:
        # Claude blocks a premature stop with ``decision: block`` (continues the
        # turn, verified); Codex uses the documented ``continue: false`` /
        # ``stopReason`` shape.
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

    def _matched_entry(phase: str, matcher: str) -> dict[str, Any]:
        return {
            "matcher": matcher,
            "hooks": [{"type": "command", "command": hook_command(phase, brr_bin)}],
        }

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
            # #1184: the rooted-write guard, matcher-scoped unlike the three
            # above (which fire on every tool call / turn boundary). It has
            # an opinion only about the two tools that take a raw file path,
            # so every other tool call never reaches ``brnrd hook pre-tool``
            # at all rather than reaching it and being told "not my concern"
            # on every keystroke. See ``install_hook_config`` for why this
            # one key merges *additively* with a repo's own ``PreToolUse``
            # rather than replacing it the way the other three do.
            "PreToolUse": [_matched_entry(PHASE_PRE_TOOL, "Edit|Write")],
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
    merged_hooks = {**existing.get("hooks", {}), **generated["hooks"]}
    # ``PreToolUse`` (#1184) is the one event key brr does not fully own the
    # way it owns the other three — a repo may already wire its own
    # predicate there (permission prompts, a linter gate, whatever), and the
    # rooted-write guard must not silently replace it; both need to keep
    # firing. So this one key is additive: the user's own matcher entries
    # survive, and brr's own is appended after them, rather than the
    # whole-key overwrite every other lifecycle event gets above.
    existing_pre_tool = existing.get("hooks", {}).get("PreToolUse")
    if isinstance(existing_pre_tool, list) and existing_pre_tool:
        merged_hooks["PreToolUse"] = [
            *existing_pre_tool, *generated["hooks"]["PreToolUse"],
        ]
    merged["hooks"] = merged_hooks
    try:
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        return None
    return settings_path


# ── The in-process subagent boundary (#1095) ─────────────────────────────
#
# A Shell's own subagent (claude's `Agent` tool) runs *inside* the runner
# process. It inherits the runner's environment and `.claude/settings.local.json`
# wholesale, so every hook subprocess it spawns resolves the parent's
# `BRR_*` handles — the parent's portal state, the parent's outbox, the
# parent's hook state. Until this seam existed, a subagent's tool boundaries
# were therefore decorated with the *parent's* correspondence: pending
# events, the correspondent's name and handle, message bodies, and the
# instruction to answer them. Found live on run-260804-1017-mlcm, where a
# research worker read the maintainer's chat messages, correctly classified
# them as prompt injection, and said so in its report. From inside a child,
# brnrd's control channel is indistinguishable from an attack.
#
# The isolation this restores is about *addressing*, not secrecy: one message
# to one run must not make every limb of that run act on it. brnrd's own
# `spawn:` children — separate daemon runs — have answered this since
# 2026-07-18 (`daemon._pending_events_for_agent(strand=True)`: a strand sees
# only its own dispatch-edge traffic). This is the same rule, finally applied
# to the in-process lane.
#
# The discriminator is measured, not assumed. Captured from live claude
# `PostToolBatch` payloads on 2026-08-05:
#
#   parent:   cwd effort hook_event_name permission_mode prompt_id
#             session_id tool_calls transcript_path
#   subagent: cwd agent_id agent_type hook_event_name permission_mode
#             prompt_id session_id tool_calls transcript_path
#
# `session_id`, `transcript_path` and every `BRR_*` variable are *identical*
# across the two — the child shares the parent's session. `agent_id` /
# `agent_type` are the only fields that appear on one and not the other, so
# they are what this keys on. A payload that carries neither is the resident's
# own boundary and takes the ordinary path.

#: Per-run directory of first-boundary latches, one file per `agent_id`.
SUBAGENT_LATCH_DIR_NAME = "subagents"

#: Hook-state key: how many subagent dispatches this run has made.
SUBAGENT_DISPATCH_KEY = "subagent_dispatches"

#: Hook-state key: the drift annotation below has been said once.
SUBAGENT_DRIFT_KEY = "subagent_drift_said"

#: Tool names that dispatch an in-process subagent. This *is* a list of
#: members, with the failure mode that implies — a Shell that renames its
#: subagent tool goes unnoticed here. It is kept anyway, and kept honest by
#: its remedy tier: this set only ever feeds an *annotation*, never a block
#: and never a suppression decision. The suppression itself keys on
#: `subagent_identity`, which reads the child's own payload and needs no
#: list at all.
SUBAGENT_DISPATCH_TOOLS = frozenset({"Agent", "Task"})


def _count_subagent_dispatches(payload: dict[str, Any]) -> int:
    """How many subagent dispatches are in this tool batch."""
    calls = payload.get("tool_calls")
    if not isinstance(calls, list):
        return 0
    count = 0
    for call in calls:
        if not isinstance(call, dict):
            continue
        if str(call.get("tool_name") or "") in SUBAGENT_DISPATCH_TOOLS:
            count += 1
    return count


def _discriminator_drift_line(ctx: HookContext, state: dict) -> str | None:
    """Say so when this run dispatched subagents and none was ever recognised.

    The isolation in :func:`subagent_identity` keys on `agent_id` /
    `agent_type` — *the Shell's* fields, not brnrd's. brnrd deliberately fails
    **open**: a payload carrying neither is treated as the resident, because
    reading the resident as a child would silently starve the run of its own
    correspondence, and a starved run renders identically to a quiet one.

    The cost of that choice is that a Shell which renames those fields
    restores the leak in silence — the "a surface that narrows renders as if
    it hadn't" class, turned on the fix for it. This is the second source that
    should agree: the parent counts its own dispatches, the children leave
    latch files, and the two disagreeing is evidence the seam stopped working.

    Annotation only, and once. The signal is real but not exact — a subagent
    that makes no tool call at all produces no boundary and therefore no latch
    — so the remedy matches the confidence: it names what it saw and what that
    would mean, and decides nothing.
    """
    if state.get(SUBAGENT_DRIFT_KEY):
        return None
    dispatched = int(state.get(SUBAGENT_DISPATCH_KEY) or 0)
    if dispatched <= 0:
        return None
    directory = ctx.run_dir
    if directory is None:
        return None
    latch_dir = directory / SUBAGENT_LATCH_DIR_NAME
    try:
        recognised = sum(1 for _ in latch_dir.glob("*.claimed"))
    except OSError:  # pragma: no cover - defensive
        return None
    if recognised:
        return None
    state[SUBAGENT_DRIFT_KEY] = True
    return (
        f"⚠ subagent isolation unverified: this run dispatched {dispatched} "
        "subagent(s) and brnrd recognised none of their boundaries. Either "
        "they made no tool calls, or the payload fields the isolation keys on "
        "(`agent_id` / `agent_type`) have changed — in which case their "
        "boundaries carried your correspondence. Worth one check before "
        "dispatching more (#1095)."
    )


def subagent_identity(payload: dict[str, Any]) -> dict[str, str] | None:
    """The `agent_id` / `agent_type` of an in-process subagent, or ``None``.

    ``None`` means "this is the resident's own boundary" — the ordinary path.
    Absence is the common case and must stay cheap and total: any payload
    shape that is not a dict carrying at least one of the two fields resolves
    to the resident, because the failure this guards against is *starving the
    resident*, which is far more expensive than one un-suppressed child.
    """
    if not isinstance(payload, dict):
        return None
    agent_id = str(payload.get("agent_id") or "").strip()
    agent_type = str(payload.get("agent_type") or "").strip()
    if not agent_id and not agent_type:
        return None
    return {
        "agent_id": agent_id or "unknown",
        "agent_type": agent_type or "subagent",
    }


def _subagent_latch(ctx: HookContext, agent_id: str) -> bool:
    """Claim the first boundary for *agent_id*. ``True`` exactly once.

    The latch lives in its own per-agent file under the run directory rather
    than in `.hook-state.json`: the whole point of this seam is that a child
    neither reads nor writes the parent's state, and subagents run
    concurrently, so a shared JSON document would be both a violation and a
    race. ``O_CREAT | O_EXCL`` makes the claim atomic between siblings.

    No run directory (an older daemon, an ad-hoc hook run) ⇒ ``False``: the
    child stays silent. Silence is the honest default here — brnrd knows
    nothing about a child it did not dispatch, and a line that cannot be
    latched is a line that repeats at every boundary forever.
    """
    directory = ctx.run_dir
    if directory is None or not directory.is_dir():
        return False
    safe = "".join(c for c in agent_id if c.isalnum() or c in "-_")[:64]
    if not safe:
        return False
    latch_dir = directory / SUBAGENT_LATCH_DIR_NAME
    try:
        latch_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            str(latch_dir / f"{safe}.claimed"),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.write(fd, _utc_now_iso().encode("utf-8") + b"\n")
    except OSError:  # pragma: no cover - defensive
        pass
    finally:
        os.close(fd)
    return True


def subagent_neutral(
    phase: str, ctx: HookContext, identity: dict[str, str]
) -> dict[str, Any]:
    """The neutral result for an in-process subagent's boundary.

    Carries the child's own state and nothing of the parent's. Deliberately
    *not* a filtered :func:`compute_neutral`: the portal is never read, the
    hook state is never read or written, no latch of the parent's is spent, no
    seen-ledger advances, and no Stop can ever be blocked — a child owes the
    parent's correspondents nothing, and cannot be held at a closeout it does
    not own.

    What is left is what brnrd actually knows about an in-process child: who
    it is, whose limb it is, and the one fact that costs work when it is
    unknown — that it dies with the parent's stream (#996). Said once, on the
    child's first boundary, then silence.
    """
    result: dict[str, Any] = {
        "inject": None,
        "block": False,
        "block_reason": None,
        "subagent": dict(identity),
    }
    if phase != PHASE_POST_TOOL:
        # Stop / session-start for a child: nothing to say, and nothing of the
        # parent's to say it with.
        return result
    if not _subagent_latch(ctx, identity["agent_id"]):
        return result
    run_label = f" of {ctx.run_id}" if ctx.run_id else ""
    short = identity["agent_id"][:8]
    result["inject"] = (
        f"[brnrd] subagent · {identity['agent_type']} · {short} — "
        f"a limb{run_label}.\n"
        "This boundary carries your own state. The run's correspondence and "
        "closeout obligations belong to the parent and are not shown here; "
        "you have no outbox of your own, and your final message is the return "
        "value the parent collects.\n"
        "You die when the parent's stream ends — land or report your work "
        "rather than holding it."
    )
    return result


# ── The rooted-write guard (#1184) ────────────────────────────────────────
#
# `daemon._child_git_pin` (#703) pins a strand's *git* to its own worktree,
# but `Edit`/`Write` never go through git — they take a raw absolute path
# and write it directly. The strand worktree is a child directory of the
# shared host checkout (`<host>/.brr/worktrees/<run-id>/…`), so the host path
# is a strict *prefix* of the strand's own, and it is the path shape every kb
# page, issue, and prior commit message in a project uses — exactly what a
# model completes when it reaches for "the absolute path to X", drifted cwd
# or not. Telling the strand to always use absolute paths (`prompts/strand.md`)
# was tried and demonstrably failed live: a strand read the warning,
# understood it, and still wrote to the host checkout three minutes later.
# rootedness, not absoluteness, was always the discriminator — so this
# predicate checks that instead of hoping the model self-polices it.
#
# Tool names `Edit`/`Write` take: Claude Code's own two file-mutating tools.
# `MultiEdit` is deliberately not listed — current Claude Code has folded it
# into `Edit`; a Shell that still emits it independently would simply pass
# this predicate unblocked, same as any tool this list doesn't name.
_ROOTED_WRITE_TOOLS = frozenset({"Edit", "Write"})

# Control files a strand is legitimately entitled to write to in the shared
# outbox directory ($BRR_OUTBOX_DIR). The bundle names these explicitly as
# where the run's control data lives — see daemon-substrate.md →
# control files table.
#
# #1318: this used to be four hand-listed literal strings and missed `.pr`
# and `.topics` — both real, documented control files
# (`relics.PR_CONTROL_NAME`, `TOPICS_NAME` above) whose own home *is* the
# outbox dir. A strand that wrote either one hit this guard, got refused,
# and was pointed at `$GIT_WORK_TREE` — the wrong location for a file
# `relics._read_pr_control` / `run_ledger.read_run_topics_control` only
# ever read from the outbox dir, so the write that should have populated
# `.pr` (and with it a bolt's `produce: attested`) landed nowhere.
#
# Rather than hand-list the fix too, this derives the set the same way
# `daemon._discover_control_file_names` does for closeout preservation:
# scan a fixed tuple of modules for public (no leading underscore)
# module-level string constants named `*_NAME`, so a module that grows a
# new outbox control-file constant joins here with no edit — see that
# function's docstring for the identical mechanism and the bug shape it
# was built to catch ("a class defined by listing its members meets the
# member nobody listed"). Not reused directly: `daemon.py` imports this
# module as `hooks_mod`, so importing back would cycle. This is the local,
# smaller mirror the daemon-side docstring anticipates for exactly that
# case, scoped to the modules this file can already import.
#
# Not every discovered `*_NAME` belongs, though — three of them are
# daemon-owned state a strand should never Edit/Write over regardless of
# location (`portals.LIVE_INBOX_NAME` / `LIVE_PORTAL_STATE_NAME`:
# "daemon-owned, heartbeat-refreshed; inspect, don't edit", per
# daemon-substrate.md; `portals.LIVE_MENU_NAME`: written only through the
# live-menu contract, one atomic generation at a time, never a raw file
# edit), and one names a file that is not outbox-scoped at all
# (`run_ledger.LEDGER_NAME`, the repo-shared ledger). Excluded explicitly
# rather than swept in by the same scan that finds the legitimate ones.
_CONTROL_FILE_MODULES = (gate_receipt, portals, promises, relics, run_ledger)
_CONTROL_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*NAME$")
_NOT_OUTBOX_WRITABLE = frozenset({
    portals.LIVE_INBOX_NAME,
    portals.LIVE_PORTAL_STATE_NAME,
    portals.LIVE_MENU_NAME,
    run_ledger.LEDGER_NAME,
})


def _discover_control_file_names(modules: tuple[Any, ...]) -> frozenset[str]:
    """Local mirror of ``daemon._discover_control_file_names`` (#1318).

    Same contract — every public module-level string constant matching
    ``*_NAME`` on the given modules, keyed by nothing but its own value —
    scoped to modules this file can import without cycling back into
    ``daemon`` (which imports this module as ``hooks_mod``).
    """
    found: set[str] = set()
    for module in modules:
        for attr, value in vars(module).items():
            if not _CONTROL_NAME_RE.match(attr):
                continue
            if isinstance(value, str) and value:
                found.add(value)
    return frozenset(found)


_CONTROL_FILES = (
    frozenset({CARD_NAME})  # this module's own; no other module names it
    | _discover_control_file_names(_CONTROL_FILE_MODULES)
) - _NOT_OUTBOX_WRITABLE


def _rooted_write_neutral(
    ctx: "HookContext", payload: dict[str, Any]
) -> dict[str, Any]:
    """The neutral result for a ``pre-tool`` boundary (#1184).

    Refuses an ``Edit``/``Write`` whose absolute ``file_path`` is rooted in
    the host checkout (``ctx.host_root``) but *not* under this strand's own
    worktree (``ctx.git_work_tree``) — the write `_child_git_pin` cannot see
    coming, because it pins git, not the editor.

    Unarmed (``host_root`` or ``git_work_tree`` absent — not a strand run, or
    the git pin itself found no readable git dir) ⇒ always the pass-through
    ``{"inject": None, "block": False, "block_reason": None}``, the same
    fact-based degrade the pin uses: nothing to compare a write path against
    is not evidence of anything, so it is never treated as one.

    Deliberately shallow otherwise: no portal read, no hook-state read or
    write, no latch — the one thing worth spending a subprocess's-worth of
    work on before every file write is "is this path about to land in the
    wrong tree", not this run's correspondence.
    """
    result: dict[str, Any] = {"inject": None, "block": False, "block_reason": None}
    if ctx.host_root is None or ctx.git_work_tree is None:
        return result
    tool_name = payload.get("tool_name")
    if tool_name not in _ROOTED_WRITE_TOOLS:
        return result
    tool_input = payload.get("tool_input")
    raw_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        return result
    path = Path(raw_path)
    if not path.is_absolute():
        # Rootedness is the discriminator, but only an absolute path can be
        # rooted anywhere in particular — a relative path resolves against
        # the runner's own cwd, which the git pin already keeps inside the
        # worktree (or leaves unpinned, in which case there is nothing this
        # predicate can add). Nothing to refuse here.
        return result
    try:
        resolved = path.resolve()
        host_root = ctx.host_root.resolve()
        work_tree = ctx.git_work_tree.resolve()
    except OSError:
        # An unresolvable path (a broken symlink component, a vanished
        # mount) is a question for the tool call itself, not this predicate —
        # refusing on a resolution failure would block writes this guard was
        # never meant to have an opinion on.
        return result
    if resolved == work_tree or work_tree in resolved.parents:
        return result
    if resolved != host_root and host_root not in resolved.parents:
        # Outside the host checkout entirely — a strand's documented escape
        # hatch (scratch trees elsewhere via `env -u GIT_DIR -u
        # GIT_WORK_TREE`) is not this predicate's concern.
        return result
    # Carve out writes to the strand's own control files (_CONTROL_FILES,
    # above) in the shared outbox directory. These are legitimately written
    # to the outbox dir by design — the bundle names them explicitly as
    # where the run's control data lives (see daemon-substrate.md → control
    # files table). A path "rooted outside the worktree" is not evidence of
    # a mistaken write when the path is a delegated control file the run
    # was told to write to.
    if ctx.outbox_dir is not None:
        try:
            outbox_dir = ctx.outbox_dir.resolve()
            if (
                resolved.parent == outbox_dir
                and resolved.name in _CONTROL_FILES
            ):
                return result
        except OSError:
            # If outbox_dir cannot resolve, fall through to the block below —
            # this is a question for the tool call itself, not this predicate.
            pass
    # #1410: Carve out writes to the shared `.brr/reports/` directory. The
    # daemon declares the report path in the `report:` contract when
    # dispatching a strand, and the strand should be allowed to write there.
    # This is the daemon's own shared runtime location for collect reports.
    if host_root is not None:
        try:
            reports_dir = (host_root / ".brr" / "reports").resolve()
            if reports_dir in resolved.parents or resolved == reports_dir:
                return result
        except OSError:
            # If reports_dir cannot resolve, fall through to the block below —
            # this is a question for the tool call itself, not this predicate.
            pass
    result["block"] = True
    if resolved.name in _CONTROL_FILES:
        # #1318: a recognised control-file name refused only because it
        # landed somewhere other than the outbox dir (a bare host-root
        # write, or `ctx.outbox_dir` failed to resolve above) — the
        # generic "$GIT_WORK_TREE" remedy is actively wrong for these:
        # every reader of `.pr` / `.topics` / `.mood` / `.name` / `.card`
        # (`relics._read_pr_control`, `run_ledger.read_run_topics_control`,
        # ...) reads it from the outbox dir, never the worktree, so
        # rewriting there would just move the file somewhere nothing reads
        # it. #792's rule: a wrong remedy is not a smaller bug than a wrong
        # diagnosis, so the remedy names the actual destination instead.
        outbox_hint = (
            str(ctx.outbox_dir) if ctx.outbox_dir is not None
            else "this run's $BRR_OUTBOX_DIR"
        )
        result["block_reason"] = (
            f"refused (#1184): {raw_path} is rooted in the host checkout "
            f"({host_root}) but outside this strand's own worktree "
            f"({work_tree}). {resolved.name} is a control file — its "
            f"documented home is the outbox dir ({outbox_hint}), not "
            f"$GIT_WORK_TREE; rewrite the path there instead."
        )
    else:
        result["block_reason"] = (
            f"refused (#1184): {raw_path} is rooted in the host checkout "
            f"({host_root}) but outside this strand's own worktree "
            f"({work_tree}). Rewrite the path relative to $GIT_WORK_TREE — "
            f"that is where this write belongs."
        )
    return result


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
    payload = _safe_json(stdin_text)
    if phase == PHASE_PRE_TOOL:
        # #1184: a filesystem-safety predicate, not a correspondence one —
        # unlike every other phase it never touches the portal or hook state,
        # so it takes neither the subagent branch nor `compute_neutral`
        # below. It applies identically whether the write comes from the
        # resident or an in-process subagent (#1095's isolation is about
        # *correspondence*: a child owes no reply to the parent's
        # correspondents. A stray write into the shared host checkout is a
        # hazard either limb can cause, so neither is exempted here).
        neutral = _rooted_write_neutral(ctx, payload)
        record_boundary(ctx, phase, neutral, payload)
        return render_native(ctx.flavour, phase, neutral)
    # An in-process subagent shares every env handle with the resident, so the
    # payload is the only place the two differ (#1095). Branch before anything
    # reads the portal or the hook state — the isolation is the point.
    identity = subagent_identity(payload)
    if identity is not None:
        neutral = subagent_neutral(phase, ctx, identity)
    else:
        neutral = compute_neutral(phase, ctx, payload)
    # Record the *neutral* result, not the rendered native JSON: the neutral
    # shape is the one thing every Shell flavour shares, so a transcript
    # written from here reads the same whether the run was claude or codex.
    record_boundary(ctx, phase, neutral, payload)
    return render_native(ctx.flavour, phase, neutral)


def record_boundary(
    ctx: HookContext,
    phase: str,
    neutral: dict[str, Any],
    payload: dict[str, Any] | None = None,
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
    # Record only the bounded identity of each act. Claude supplies an ordered
    # batch while Codex supplies one top-level tool name; raw inputs, responses,
    # and tool-use ids are deliberately excluded from the transcript.
    # ``detail`` is a derived, redacted summary of the primary tool's input;
    # ``out_bytes`` is the total byte count of responses (never the content).
    tool_names: list[str] = []
    first_act: str | None = None
    first_detail: str | None = None
    total_out_bytes: int = 0
    has_out_bytes = False
    if phase == PHASE_POST_TOOL and isinstance(payload, dict):
        calls = payload.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("tool_name")
                if isinstance(name, str) and name.strip():
                    tool_names.append(name.strip())
                    if first_act is None:
                        first_act = classify_act(name, call.get("tool_input"))
                        first_detail = _tool_detail(name, call.get("tool_input"))
                response = call.get("tool_response")
                if response is not None:
                    total_out_bytes += _response_bytes(response)
                    has_out_bytes = True
        else:
            name = payload.get("tool_name")
            if isinstance(name, str) and name.strip():
                tool_names.append(name.strip())
                first_act = classify_act(name, payload.get("tool_input"))
                first_detail = _tool_detail(name, payload.get("tool_input"))
            response = payload.get("tool_response")
            if response is not None:
                total_out_bytes = _response_bytes(response)
                has_out_bytes = True
    if tool_names:
        record["tools"] = tool_names
        record["act"] = first_act
    if first_detail is not None:
        record["detail"] = first_detail
    if has_out_bytes:
        record["out_bytes"] = total_out_bytes
    # An in-process subagent's boundary is recorded (it happened, and a reader
    # asking "what did this run's environment say" wants it) but tagged, so
    # `derive_boundaries_summary` can keep the run's own verdict — which is
    # about the *resident's* closeout — free of a child's fires (#1095).
    subagent = neutral.get("subagent")
    if isinstance(subagent, dict) and subagent:
        record["subagent"] = subagent
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
        if record.get("subagent"):
            # A limb's boundary, not the run's (#1095). Skipped rather than
            # counted: this summary answers "did *this run* end over a live
            # objection", and a child — which can never be blocked, and whose
            # Stop is not the resident's — would only dilute the count and, at
            # Stop, overwrite the resident's own final verdict.
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
