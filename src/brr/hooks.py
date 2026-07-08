"""Runner hooks back channel — ``brnrd hook <phase>``.

Tier 2 of the runner interface (``kb/design-runner-back-channel.md``).
Some target CLI agents expose runner-native lifecycle hooks: callbacks at
tool/turn boundaries whose JSON result is injected back into the agent's
context. brr exposes **one** endpoint, ``brnrd hook <phase>``, reading a JSON
event on stdin and writing a JSON result on stdout. brr owns the abstract
*phases*; each hook-backed runner profile maps its native hook names onto
them, and brr renders the one neutral result into that runner's native fields.

Two directions across the single endpoint:

- **Outbound flush** (runner → daemon): ``post-tool`` / ``stop`` drop a
  ``.flush`` signal in the run outbox so the daemon drains the
  outbox / ``.card`` *immediately* instead of waiting for the next
  heartbeat tick. The hook **never drains itself** — ``daemon._drain_outbox``
  is in-process-coupled (worker emit + conversation indexing) and guarded
  by a ``threading.Lock``, so a separate ``brnrd hook`` process draining in
  parallel would double-deliver. The hook only signals; the daemon stays
  the sole drainer.
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

import json
import os
import shutil
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
# Per-run hook memory: the last change_token injected, and whether a
# premature stop was already blocked once (so the nudge fires once, not in
# a loop). Daemon-independent; the hook owns this file.
HOOK_STATE_NAME = ".hook-state.json"


# ── Context resolution ──────────────────────────────────────────────────


class HookContext:
    """Resolved run handles the hook operates on, from the runner env."""

    def __init__(self, env: dict[str, str]) -> None:
        self.run_id = env.get("BRR_RUN_ID") or None
        self.event_id = env.get("BRR_EVENT_ID") or None
        self.flavour = (env.get("BRR_RUNNER") or "").strip().lower() or None
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
    """Signal the daemon to drain the outbox now (best-effort)."""
    if ctx.flush_path is None:
        return
    try:
        ctx.flush_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.flush_path.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


# ── Injection rendering (portal-state → compact delta) ───────────────────


def format_delta(
    payload: dict[str, Any], *, seed: bool = False, stop: bool = False
) -> str | None:
    """Render a compact context delta from the live portal-state payload.

    Short on purpose: it is woven into the agent's context every boundary,
    so it carries only what shifts attention — pending events, delivery
    acks, budget pressure.

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
    card = payload.get("card") if isinstance(payload.get("card"), dict) else {}
    resources = (
        payload.get("resources")
        if isinstance(payload.get("resources"), dict) else {}
    )

    pending = int(attention.get("pending_event_count", 0) or 0)
    pending_files = int(attention.get("pending_outbox_file_count", 0) or 0)
    lines: list[str] = []
    if seed:
        header = "brr portal seed"
    elif stop:
        header = "brr portal closeout"
    else:
        header = "brr portal update"
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
    acked = outbound.get("replies_current") or outbound.get("outbound_messages")
    if acked:
        lines.append(
            f"- delivery so far: current={outbound.get('replies_current', 0)} "
            f"other={outbound.get('replies_other', 0)} "
            f"outbound={outbound.get('outbound_messages', 0)}."
        )
    elif stop:
        # Affirmative-empty: an addressed run that reaches closeout having
        # sent nothing is suspicious, not silent. Surface the absence at the
        # boundary so a forgotten reply is caught before the slot is gone.
        lines.append(
            "- delivery: no outbound messages sent yet — confirm this run "
            "left the signal it owed before ending."
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
        and not acked and not rendered_resources and not card_stale
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
    portal = _read_json(ctx.portal_state_path)
    state = _read_hook_state(ctx)
    inject: str | None = None
    block = False
    block_reason: str | None = None

    if phase in (PHASE_POST_TOOL, PHASE_STOP):
        _touch_flush(ctx)

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

    *stdin_text* is the runner's native hook payload (passed through but not
    required — brr reads run context from the env handles, not the payload).
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
