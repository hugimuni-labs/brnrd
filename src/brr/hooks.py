"""Runner hooks back channel — ``brr hook <phase>``.

Tier 2 of the runner interface (``kb/design-runner-back-channel.md``).
Every target CLI agent (claude, codex, gemini) ships lifecycle hooks:
runner-native callbacks at tool/turn boundaries whose JSON result is
injected back into the agent's context. brr exposes **one** endpoint,
``brr hook <phase>``, reading a JSON event on stdin and writing a JSON
result on stdout. brr owns the abstract *phases*; each runner profile maps
its native hook names onto them, and brr renders the one neutral result
into that runner's native fields.

Two directions across the single endpoint:

- **Outbound flush** (runner → daemon): ``post-tool`` / ``stop`` drop a
  ``.flush`` signal in the run outbox so the daemon drains the
  outbox / ``.card`` *immediately* instead of waiting for the next
  heartbeat tick. The hook **never drains itself** — ``daemon._drain_outbox``
  is in-process-coupled (worker emit + conversation indexing) and guarded
  by a ``threading.Lock``, so a separate ``brr hook`` process draining in
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
    returns ``None`` when nothing shifted, so the channel injects no noise.
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
    lines.append(
        f"[{header}] {pending} pending event(s), "
        f"{pending_files} undelivered outbox file(s)."
    )
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
    acked = outbound.get("replies_current") or outbound.get("outbound_messages")
    if acked:
        lines.append(
            f"- delivery so far: current={outbound.get('replies_current', 0)} "
            f"other={outbound.get('replies_other', 0)} "
            f"outbound={outbound.get('outbound_messages', 0)}."
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
    # Work-status posture (cost / quota / parallelism). A boundary signal like
    # scm: rendered at seed / stop only, where the affirmative picture is worth
    # a line. Known fields carry their value; not-yet-built ones read
    # "unavailable" so the resident sees the slot honestly rather than a gap.
    if (seed or stop) and resources:
        rendered = _format_resources(resources)
        if rendered:
            lines.append(rendered)
    # Mid-run, a bare header with no pending work and no movement isn't worth
    # a turn. Seed and stop always render: their empty state ("0 pending") is
    # the affirmative signal, not noise.
    if not seed and not stop and pending == 0 and pending_files == 0 and not acked:
        return None
    return "\n".join(lines)


def _format_resources(resources: dict[str, Any]) -> str | None:
    """One compact 'work status' line: quota/cost/coexisting/remote posture."""
    def _facet_text(key: str, label: str) -> str:
        facet = resources.get(key) if isinstance(resources.get(key), dict) else {}
        if facet.get("status") == "known":
            summary = str(facet.get("summary") or "").strip()
            return f"{label}={summary}" if summary else f"{label}=known"
        return f"{label}=unavailable"

    parts = [
        _facet_text("quota", "quota"),
        _facet_text("cost", "cost"),
        _facet_text("coexisting_runs", "coexisting-runs"),
        _facet_text("remote_scm", "remote-scm"),
    ]
    return "- resources: " + "; ".join(parts) + "."


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
        # The closeout boundary renders unconditionally (not token-gated):
        # the affirmative "0 pending" signal and the SCM commit/push
        # reminder must land even when nothing moved since the last tick.
        inject = format_delta(portal, stop=True)
        state["last_token"] = portal.get("change_token")
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
        if pending > 0 and not state.get("stop_blocked"):
            block = True
            block_reason = (
                f"{pending} pending event(s) are still waiting — fold the "
                "foldable ones into this wake (read inbox.json) before "
                "ending, or say why they should wait."
            )
            state["stop_blocked"] = True

    _write_hook_state(ctx, state)
    return {"inject": inject, "block": block, "block_reason": block_reason}


# ── Native rendering (neutral → runner flavour) ──────────────────────────

_CLAUDE_EVENT_NAME = {
    PHASE_POST_TOOL: "PostToolUse",
    PHASE_STOP: "Stop",
    PHASE_SESSION_START: "SessionStart",
}


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

    if flavour == "claude":
        event_name = _CLAUDE_EVENT_NAME.get(phase, "PostToolUse")
        out: dict[str, Any] = {}
        if block:
            # `decision: block` prevents the stop and feeds the reason back.
            out["decision"] = "block"
            if reason:
                out["reason"] = reason
        if inject:
            out["hookSpecificOutput"] = {
                "hookEventName": event_name,
                "additionalContext": inject,
            }
        return out, 0

    if flavour == "codex":
        out = {}
        if inject:
            out["additionalContext"] = inject
        if block:
            out["continue"] = False
            if reason:
                out["stopReason"] = reason
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


# ── Config generation (brr-managed, per-run, worktree-scoped) ────────────
#
# brr generates the runner's *native* hook config into the run's working
# directory each run so the user never hand-writes it, and so it disappears
# with the worktree (nothing touches the user's global config). A runner is
# only treated as hooks-capable after a runtime precheck confirms the
# prerequisites — the profile's ``hooks:`` field is the *intent*, the
# precheck is the *assertion* (kb/design-runner-back-channel.md §Resolutions).

# Flavours brr can currently emit native hook config for. codex / gemini
# declare the capability (their docs confirm bidirectional hooks) but their
# config emitters are a follow-up; until then they degrade to Tier 0/1.
_CONFIG_SUPPORTED = {"claude"}


def hook_config_supported(flavour: str | None) -> bool:
    """True when brr can generate native hook config for *flavour* today."""
    return bool(flavour) and flavour in _CONFIG_SUPPORTED


def hook_command(phase: str, brr_bin: str = "brr") -> str:
    """The shell command a native hook runs for *phase*."""
    return f"{brr_bin} hook {phase}"


def _claude_hook_settings(brr_bin: str) -> dict[str, Any]:
    def _entry(phase: str) -> dict[str, Any]:
        return {"hooks": [{"type": "command", "command": hook_command(phase, brr_bin)}]}

    return {
        "hooks": {
            "PostToolUse": [_entry(PHASE_POST_TOOL)],
            "Stop": [_entry(PHASE_STOP)],
            "SessionStart": [_entry(PHASE_SESSION_START)],
        }
    }


def hook_capability(
    flavour: str | None, cwd: Path | None, *, brr_bin: str = "brr"
) -> bool:
    """Runtime precheck: is this run actually hooks-capable?

    Asserts (not assumes) the per-runner prerequisites: brr can emit config
    for the flavour, the brr endpoint is invocable on PATH, and the run cwd
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
    flavour: str | None, cwd: Path, *, brr_bin: str = "brr"
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
    # User overrides layer on top of brr's defaults; brr owns only the
    # ``hooks`` block, so a merge preserves any other local settings.
    merged = {**existing, **generated}
    if "hooks" in existing:
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
