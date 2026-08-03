"""``brnrd do`` — resident-facing porcelain over the outbox verb grammar.

The daemon's accept/refuse verdict for an outbox directive has always lived
one hop away from the act that produced it: stage a file, and the daemon's
own drain consumes it silently on its next heartbeat — accepted or refused,
the file is gone either way (`docs/portals.md` §notices). A refusal only
ever surfaced through `portal-state.json` → `notices`, which a resident has
to remember to re-read. This module closes that loop in one call: it stages
the same files a resident would stage by hand, then polls the same
`portal-state.json` the daemon already maintains until the file is consumed,
diffing `notices` from just before the stage to read the verdict back in the
same boundary the CLI call itself is.

**This is a wrapper, not a new channel.** Every write here goes through the
outbox directory and the canonical fenced frontmatter
(`protocol.parse_outbox_message` handles both that and the lenient form;
this module only ever emits the canonical one, since a reply/note/gate body
of arbitrary resident text could otherwise collide with the lenient parser's
blank-line/`---`-sniffing heuristics — the canonical fence's own regex is
non-greedy, so it always closes at *this* module's own fence, never at
anything the body happens to contain). No daemon-side code changes; nothing
here is a second implementation of the drain's routing decisions.

**The verdict-observation contract.** A directive's outbox file carries no
identity the daemon reflects back — a notice names the *target* (an event
id, a gate name) and the *verb* ("note", "reply", "gate message"), never the
staged filename. So correlating "this notice is about the file I just
staged" is a text-substring heuristic (`_notice_matches`), not an exact
join. It works because every refusal/drop/redirect notice in `daemon.py`
names both its verb and its target in the message text (grepped across
every `_record_outbox_notice` call site for `note:`/`reply:`/`gate:`) — but
it is still a heuristic, and a concurrent notice from unrelated activity
that happens to share both substrings would misattribute. The precise fix
is a daemon-side one: give `_record_outbox_notice` (`daemon.py` around
L6135-6188) an optional `source_file:` field threaded from each call site's
`fpath.name`, so a reader here could match on identity instead of text. Not
done in this change — see the constraint against daemon-side edits — and
named here so the gap has one place to live instead of being rediscovered
each time this heuristic misses.

**What "consumed" means.** `_drain_outbox` always ends a branch with
`_retire_outbox_staging(fpath)`, whether the directive was accepted or
refused — the file moves to `.processed/` either way. So "the exact path
this module wrote no longer exists at that path" is the ground-truth
consumption signal, independent of accept/refuse (`daemon.py` L6191-6202
`_retire_outbox_staging`; every branch of `_drain_outbox` calls it before
`continue`).

**What this module cannot see.** `.mood` / `.card` are control dotfiles —
`_drain_outbox` explicitly skips anything `CONTROL_NAMES`/dotfile-shaped
(`daemon.py` L6877-6882) — so they are never staged-then-drained; a write
either lands on disk or it doesn't, and the verdict is the write's own
success. And once a `reply`/`gate` directive is accepted by the drain, the
resulting message still has its *own* delivery lifecycle in the message
store (queued → delivered by a gate thread) that `portal-state.json` does
not expose per-message — this module's verdict stops at "the daemon
accepted the directive", not "the gate has sent it yet".
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import protocol

#: Suggested/default wait for a staged directive to be drained, per the
#: task's own guidance — long enough to clear the daemon's heartbeat tick
#: (`daemon._HEARTBEAT_INTERVAL`, ~10s) at least once with margin, short
#: enough that a caller isn't blocked indefinitely on a genuinely wedged
#: daemon. Flag-tunable at the CLI layer.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: How often to re-check the staged file / re-read portal-state.json while
#: waiting. Cheap: both are local filesystem reads.
POLL_INTERVAL_SECONDS = 0.5

#: Outcomes a per-verb wait can resolve to.
OK = "ok"
FAILED = "failed"
QUEUED = "queued"

PORTAL_STATE_NAME = "portal-state.json"


# ── Outbox message staging ──────────────────────────────────────────


def stage_filename(verb: str, index: int) -> str:
    """A staging-safe filename for one directive in a `do` batch.

    Never `.tmp`-suffixed, never dotfile-shaped, never a control name — the
    drain's own staging/control filters (`portals.is_staging_name`,
    `portals.CONTROL_NAMES`) would otherwise treat it as invisible rather
    than a deliverable directive. Timestamped so a batch of same-verb calls
    never collides.
    """
    return f"do-{time.time_ns()}-{verb}-{index}.md"


def stage_message(
    outbox_dir: Path, filename: str, *, meta: dict[str, str], body: str = "",
) -> Path:
    """Write one canonical-fenced outbox message, atomically.

    Canonical form only (`---`-fenced, closed by the module's own fence) —
    see the module docstring for why the lenient no-fence shortcut is never
    used here. `meta` values are plain identifiers/ids; nothing here accepts
    attacker-shaped frontmatter values, so no newline-injection guard is
    needed the way `protocol._validate_meta` needs one for event creation.
    """
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    text = "\n".join(lines) + "\n" + body
    if not text.endswith("\n"):
        text += "\n"
    path = outbox_dir / filename
    protocol._atomic_write(path, text)
    return path


def stage_note(outbox_dir: Path, event_id: str, *, index: int = 0) -> Path:
    """Stage a `note: <event-id>` directive — retire a pending event, silently."""
    return stage_message(
        outbox_dir, stage_filename("note", index), meta={"note": event_id},
        body="noted via `brnrd do`\n",
    )


def stage_reply(
    outbox_dir: Path, event_id: str, body: str, *, index: int = 0,
) -> Path:
    """Stage an `event: <event-id>` reply directive."""
    return stage_message(
        outbox_dir, stage_filename("reply", index), meta={"event": event_id},
        body=body,
    )


def stage_gate(
    outbox_dir: Path, gate_name: str, body: str, *, index: int = 0,
) -> Path:
    """Stage a `gate: <name>` out-of-band send directive."""
    return stage_message(
        outbox_dir, stage_filename("gate", index), meta={"gate": gate_name},
        body=body,
    )


def write_mood(
    outbox_dir: Path, emote_name: str, note: str | None = None,
) -> Path:
    """Overwrite `.mood` — first line the resolved handle, rest the narration."""
    text = emote_name.strip() + "\n"
    if note and note.strip():
        text += note.strip() + "\n"
    path = outbox_dir / ".mood"
    protocol._atomic_write(path, text)
    return path


def write_card(outbox_dir: Path, text: str) -> Path:
    """Overwrite `.card` verbatim with *text* (already read from the caller's file)."""
    body = text if text.endswith("\n") else text + "\n"
    path = outbox_dir / ".card"
    protocol._atomic_write(path, body)
    return path


# ── portal-state.json reading + the notices diff ────────────────────


def read_portal_state(outbox_dir: Path) -> dict[str, Any]:
    """Best-effort read of this run's live `portal-state.json`. `{}` on any failure.

    Read fresh on every call — the daemon rewrites the file on its own
    heartbeat, and the whole verdict mechanism depends on seeing that
    rewrite rather than a cached copy.
    """
    try:
        raw = (outbox_dir / PORTAL_STATE_NAME).read_text(encoding="utf-8")
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def notices_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    notices = payload.get("notices")
    return [n for n in notices if isinstance(n, dict)] if isinstance(notices, list) else []


def _notice_key(notice: dict[str, Any]) -> tuple:
    return (notice.get("at"), notice.get("kind"), notice.get("text"))


def new_notices(
    before: list[dict[str, Any]], after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Notices present in *after* but not *before* — the only ones that count.

    `portal-state.json` → `notices` is a rolling tail (`daemon._MAX_NOTICES`)
    of `.notices.jsonl`, not a diff itself — a notice from *before* this
    directive was even staged is not evidence about it, however similar the
    text.
    """
    seen = {_notice_key(n) for n in before}
    return [n for n in after if _notice_key(n) not in seen]


def _notice_matches(notice: dict[str, Any], needles: tuple[str, ...]) -> bool:
    text = str(notice.get("text") or "").lower()
    return all(needle.lower() in text for needle in needles if needle)


def find_matching_notice(
    notices: list[dict[str, Any]], needles: tuple[str, ...],
) -> dict[str, Any] | None:
    """The first fresh notice that plausibly names this directive, or ``None``."""
    for notice in notices:
        if _notice_matches(notice, needles):
            return notice
    return None


# ── The wait ─────────────────────────────────────────────────────────


def await_verdict(
    outbox_dir: Path,
    staged_path: Path,
    before_notices: list[dict[str, Any]],
    needles: tuple[str, ...],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = POLL_INTERVAL_SECONDS,
    sleep=None,
    clock=None,
) -> tuple[str, str]:
    """Poll until *staged_path* is consumed, or *timeout_seconds* elapses.

    Returns ``(status, detail)`` — ``status`` is one of :data:`OK` (consumed,
    no matching fresh notice), :data:`FAILED` (consumed, and a fresh notice
    names this directive — *detail* is that notice's `kind: text`), or
    :data:`QUEUED` (still sitting in the outbox when the timeout hit — never
    hangs forever, per the task's own "30s, flag-tunable, never hang" rule).

    *sleep*/*clock* default to ``None`` rather than binding ``time.sleep`` /
    ``time.monotonic`` as early-evaluated default values — a default
    argument is captured once at *def* time, which would make
    ``monkeypatch.setattr(brr.do.time, "sleep", fake)`` (the pattern
    ``cli.cmd_portal_await`` already uses) silently not apply. Resolving
    them from the live ``time`` module inside the call, only when the
    caller didn't inject its own, keeps both paths working: a direct-unit
    test can pass a fake explicitly, and a CLI-level test can monkeypatch
    ``time.sleep`` the same way the rest of this codebase already does.
    """
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + max(0.0, timeout_seconds)
    while True:
        if not staged_path.exists():
            payload = read_portal_state(outbox_dir)
            fresh = new_notices(before_notices, notices_of(payload))
            hit = find_matching_notice(fresh, needles)
            if hit is not None:
                kind = hit.get("kind") or "refused"
                return FAILED, f"{kind}: {hit.get('text')}"
            return OK, ""
        if clock() >= deadline:
            return QUEUED, "still queued"
        sleep(min(poll_seconds, max(0.0, deadline - clock())))


# ── The bare snapshot ────────────────────────────────────────────────


def format_snapshot(payload: dict[str, Any]) -> str:
    """The one-screen portal read for a bare `brnrd do` — no verbs given.

    Deliberately narrower than `cli._format_portal_state` (the full `brnrd
    portal state` dump): the task asks for exactly five things — pending
    events with ids, outbound counts, notices, the quota line, and spawn
    pool headroom — as "the canonical read replacing ad-hoc JSON parsing",
    not a restatement of the fuller view a different command already owns.
    """
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    attention = (
        payload.get("attention") if isinstance(payload.get("attention"), dict) else {}
    )
    inbound = payload.get("inbound") if isinstance(payload.get("inbound"), dict) else {}
    outbound = (
        payload.get("outbound") if isinstance(payload.get("outbound"), dict) else {}
    )
    resources = (
        payload.get("resources") if isinstance(payload.get("resources"), dict) else {}
    )
    notices = notices_of(payload)

    lines = [
        "[brnrd do] "
        f"run={run.get('id') or '-'} event={run.get('event_id') or '-'} "
        f"phase={run.get('phase') or '-'} token={payload.get('change_token') or '-'}",
    ]

    events = inbound.get("events") if isinstance(inbound.get("events"), list) else []
    count = attention.get("pending_event_count", len(events))
    if events:
        rows = "; ".join(
            f"{ev.get('id') or '-'} {ev.get('source') or '-'}: "
            f"{str(ev.get('summary') or '').strip()[:80]}"
            for ev in events if isinstance(ev, dict)
        )
        lines.append(f"pending events ({count}): {rows}")
    else:
        lines.append(f"pending events: {count}")

    lines.append(
        "outbound: "
        f"current={outbound.get('replies_current', 0)} "
        f"other={outbound.get('replies_other', 0)} "
        f"outbound={outbound.get('outbound_messages', 0)}"
    )

    if notices:
        rendered = " | ".join(
            f"{n.get('kind') or '?'}: {str(n.get('text') or '').strip()[:120]}"
            for n in notices
        )
        lines.append(f"notices ({len(notices)}): {rendered}")
    else:
        lines.append("notices: none")

    quota = resources.get("quota") if isinstance(resources.get("quota"), dict) else None
    if quota is not None:
        from . import facets

        lines.append(f"quota: {facets.facet_value(quota)}")

    coexisting = (
        resources.get("coexisting_runs")
        if isinstance(resources.get("coexisting_runs"), dict) else None
    )
    pool = coexisting.get("spawn_pool") if isinstance(coexisting, dict) else None
    if isinstance(pool, dict):
        lines.append(
            "spawn pool: "
            f"{pool.get('active')}/{pool.get('max_concurrent')} used, "
            f"{pool.get('available')} available"
        )

    return "\n".join(lines)
