"""Self-scheduled thoughts — the resident wakes itself on its own clock.

The resident keeps a declarative **schedule** in its dominion
(``schedule.md`` in the resident dominion); the daemon's reflex loop reads it each
tick and fires due entries as ordinary inbox events, which flow through
the normal single-flight pipeline. A self-scheduled wake *is just an
event* whose source happens to be the resident itself — consistent with
the agent-as-memory thesis. See ``kb/design-self-scheduled-thoughts.md``.

Two trigger forms cover the ground without cron's 5-field grammar:

- ``at: <ISO-8601>`` — one-shot, absolute (deferral, reminders); the
  absolute time travels with the dominion, so it fires correctly on a
  second machine / after reinstall.
- ``every: <duration>`` — recurring at a fixed interval (``30m``, ``1h``,
  ``24h``, ``1h30m``); anchored on first sight (adding it does not fire
  instantly), then fired each interval. Optionally paired with
  ``reset_on: spawn`` so a concurrent ``spawn:`` dispatch elsewhere in the
  daemon pushes this entry's cooldown out from the dispatch time, instead
  of firing redundantly moments after related work already happened.

Split of concerns mirrors the memory layers: the **specs** are owned and
durable (dominion, committed); the **firing-state** (last-fired
timestamps) is operational — daemon-owned, gitignored, machine-persistent
(survives daemon restarts; lost only on machine-loss). The daemon never
writes the agent's ``schedule.md``, so firing never races the dominion
commit lock.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import trust

SCHEDULE_FILE = "schedule.md"  # in the dominion
STATE_DIRNAME = "schedule"  # under the .brr runtime dir
STATE_FILE = "state.json"
SIGNAL_FILE = "signals.json"  # also under STATE_DIRNAME
DEFAULT_STALE_GRACE_S = 7 * 24 * 3600  # an `at:` older than this won't surprise-fire

_FIELD_RE = re.compile(
    r"^\s*(at|every|conversation_key|reset_on)\s*:\s*(.+?)\s*$", re.IGNORECASE
)
_DURATION_TOKEN_RE = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class ScheduleEntry:
    """One schedule spec: a thought to emit when its trigger is due."""

    id: str
    kind: str  # "at" | "every"
    body: str
    at: float | None = None  # epoch seconds, for kind == "at"
    interval: float | None = None  # seconds, for kind == "every"
    raw_when: str = ""  # original trigger string, for messages
    # Optional conversation this entry's firings thread into. Defaults
    # (at fire time) to ``schedule:<id>`` so a recurring entry's wakes
    # share a readable history; set explicitly to thread into an existing
    # gate conversation (e.g. ``telegram:12345:``).
    conversation_key: str | None = None
    # Optional named signal (e.g. ``spawn``) that resets this entry's
    # cooldown as if it had just fired, without actually firing it. Only
    # meaningful for ``every`` entries — see ``apply_reset_signals``.
    reset_on: str | None = None


# ── Parsing ──────────────────────────────────────────────────────────


def parse_duration(text: str) -> float | None:
    """Parse ``1h30m`` / ``45s`` / ``2d`` into seconds, or ``None``.

    Tokens are ``<int><unit>`` with unit s/m/h/d, summed. The whole string
    must be tokens (and whitespace) — a stray word makes it invalid.
    """
    if not text:
        return None
    if _DURATION_TOKEN_RE.sub("", text).strip():
        return None  # leftover non-token characters
    total = 0
    matched = False
    for amount, unit in _DURATION_TOKEN_RE.findall(text):
        total += int(amount) * _UNIT_SECONDS[unit.lower()]
        matched = True
    return float(total) if matched else None


def parse_iso(text: str) -> float | None:
    """Parse an ISO-8601 timestamp into epoch seconds (UTC), or ``None``.

    Accepts a trailing ``Z`` and naive timestamps (assumed UTC).
    """
    if not text:
        return None
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _slug(title: str) -> str:
    return _SLUG_RE.sub("-", title.strip().lower()).strip("-")


def _build_entry(title: str, fields: dict[str, str], body_lines: list[str]) -> ScheduleEntry | None:
    eid = _slug(title)
    if not eid:
        return None
    body = "\n".join(body_lines).strip()
    conv = (fields.get("conversation_key") or "").strip() or None
    reset_on = (fields.get("reset_on") or "").strip() or None
    # `every` wins if both are present (one trigger per entry is the convention).
    if "every" in fields:
        interval = parse_duration(fields["every"])
        if not interval or interval <= 0:
            return None
        return ScheduleEntry(
            eid, "every", body, interval=interval,
            raw_when=fields["every"], conversation_key=conv, reset_on=reset_on,
        )
    if "at" in fields:
        at = parse_iso(fields["at"])
        if at is None:
            return None
        return ScheduleEntry(
            eid, "at", body, at=at, raw_when=fields["at"], conversation_key=conv,
        )
    return None  # no trigger → inert, skipped


def parse_schedule(dominion_dir: Path) -> list[ScheduleEntry]:
    """Parse the dominion's ``schedule.md`` into :class:`ScheduleEntry` records.

    Format: a ``## `` heading per entry (its id is the slugified heading),
    an ``at:`` or ``every:`` line, an optional ``conversation_key:`` line
    (threads the firings; defaults to ``schedule:<id>`` at fire time), an
    optional ``reset_on:`` line for ``every`` entries (a named signal —
    currently only ``spawn`` — that pushes this entry's cooldown out as if
    it had just fired, so it doesn't redundantly fire right after other,
    more specific work already covered similar ground; see
    ``apply_reset_signals``), then optional body prose (the thought to
    run). Text before the first heading is a comment/header and ignored.
    An entry with no/invalid trigger is dropped.
    """
    path = dominion_dir / SCHEDULE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_schedule_text(text)


def parse_schedule_text(text: str) -> list[ScheduleEntry]:
    """Parse ``schedule.md`` *content* into entries — the file-free half of
    :func:`parse_schedule`.

    Split out because trust-tier attribution (#413 §7 S8) parses the
    schedule as it stood at a *git blob*, not as it sits on disk: the
    daemon reads both sides of its own dominion commit through
    ``git show`` and never materialises either one as a file.
    """
    entries: list[ScheduleEntry] = []
    title: str | None = None
    fields: dict[str, str] = {}
    body_lines: list[str] = []

    def _flush() -> None:
        if title is None:
            return
        entry = _build_entry(title, fields, body_lines)
        if entry:
            entries.append(entry)

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            title = line[3:].strip()
            fields = {}
            body_lines = []
            continue
        if title is None:
            continue
        m = _FIELD_RE.match(line)
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()
            continue
        body_lines.append(line)
    _flush()
    return entries


# ── Firing-state (runtime, daemon-owned) ─────────────────────────────


def _state_path(brr_dir: Path) -> Path:
    return brr_dir / STATE_DIRNAME / STATE_FILE


def load_state(brr_dir: Path) -> dict:
    """Load the firing-state map (entry id → record). ``{}`` on absence/parse error."""
    try:
        return json.loads(_state_path(brr_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(brr_dir: Path, state: dict) -> None:
    """Persist the firing-state map atomically (temp + rename)."""
    path = _state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, json.dumps(state, indent=2).encode("utf-8"))
        os.close(fd)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _signal_path(brr_dir: Path) -> Path:
    return brr_dir / STATE_DIRNAME / SIGNAL_FILE


def load_signals(brr_dir: Path) -> dict[str, float]:
    """Load the named-signal timestamp map (signal name → epoch seconds).

    ``{}`` on absence/parse error — a missing/corrupt signal file just
    means no reset applies this tick, never a crash.
    """
    try:
        raw = json.loads(_signal_path(brr_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def record_signal(brr_dir: Path, name: str, now: float | None = None) -> None:
    """Best-effort: record that named signal *name* just happened at *now*.

    Called from wherever the daemon dispatches the thing a schedule entry
    might want to react to (currently: a concurrent ``spawn:`` dispatch —
    see ``daemon._queue_spawn_request``). Read back by ``apply_reset_signals``
    on the next scheduling tick. Swallows I/O errors: a missed signal just
    means one entry doesn't get its cooldown reset this time, never a
    daemon-loop failure.
    """
    ts = now if now is not None else time.time()
    try:
        signals = load_signals(brr_dir)
        signals[name] = ts
        path = _signal_path(brr_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            os.write(fd, json.dumps(signals, indent=2).encode("utf-8"))
            os.close(fd)
            os.rename(tmp, path)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


def apply_reset_signals(
    entries: list[ScheduleEntry],
    state: dict,
    signals: dict[str, float],
    now: float,
) -> dict:
    """Push a matching ``reset_on`` entry's cooldown out to a recent signal.

    Pure (no I/O): for each ``every`` entry naming a ``reset_on`` signal
    that fired more recently than the entry's own ``last_fired``, record
    the signal's timestamp as the new ``last_fired`` — exactly as if the
    entry had itself fired then, without actually emitting an event for
    it. Never moves ``last_fired`` backwards, and never touches an entry
    with no ``reset_on`` or a signal that hasn't happened. Feed the result
    into ``due_entries`` so the interval math sees the reset.
    """
    if not signals:
        return state
    new_state = dict(state)
    for e in entries:
        if e.kind != "every" or not e.reset_on:
            continue
        signal_ts = signals.get(e.reset_on)
        if signal_ts is None or signal_ts > now:
            continue
        rec = new_state.get(e.id)
        last = rec.get("last_fired") if isinstance(rec, dict) else None
        if last is not None and last >= signal_ts:
            continue
        new_state[e.id] = {"kind": "every", "last_fired": signal_ts}
    return new_state


# ── Due computation (pure) ───────────────────────────────────────────


def due_entries(
    entries: list[ScheduleEntry],
    state: dict,
    now: float,
    *,
    stale_grace: float = DEFAULT_STALE_GRACE_S,
) -> tuple[list[ScheduleEntry], dict]:
    """Decide which entries are due, returning ``(due, new_state)``.

    Pure: no clock, no I/O — ``now`` and ``state`` are inputs. ``new_state``
    reflects the firings/anchorings this call implies and is pruned to the
    ids still present in *entries* (so removing an entry forgets its state).

    - ``every`` — anchored (recorded, not fired) on first sight; fired when
      ``now - last_fired >= interval``.
    - ``at`` — fired once when ``now >= at``; an ``at`` more than
      *stale_grace* in the past is anchored-as-fired without firing (so a
      stale one-shot can't surprise-fire after a machine-loss state wipe).
    """
    new_state = dict(state)
    due: list[ScheduleEntry] = []

    for e in entries:
        rec = new_state.get(e.id)
        seen = rec is not None
        if e.kind == "every":
            if not seen:
                new_state[e.id] = {"kind": "every", "last_fired": now}
                continue
            last = rec.get("last_fired")
            if last is None or (now - last) >= (e.interval or 0):
                due.append(e)
                new_state[e.id] = {"kind": "every", "last_fired": now}
        elif e.kind == "at":
            if seen and rec.get("fired"):
                continue
            if now >= (e.at or 0):
                fired_record = {"kind": "at", "last_fired": now, "fired": True}
                if (now - (e.at or 0)) > stale_grace:
                    new_state[e.id] = fired_record  # too late — anchor, don't fire
                else:
                    due.append(e)
                    new_state[e.id] = fired_record

    present = {e.id for e in entries}
    new_state = {k: v for k, v in new_state.items() if k in present}
    return due, new_state


# ── Mechanical lint (pure, no I/O, no model) ─────────────────────────
#
# Issue #579: schedule.md entries are written by runs and never read back
# by anything except the firing itself, so an entry can go stale, duplicate
# another entry's remit, or describe a world that no longer exists,
# indefinitely — the only detector today is a resident noticing mid-wake.
# This is the "mechanical first, judgement second" half of the issue's
# proposed rail: detection with no model in the loop. *Resolution*
# (cancel/postpone/rephrase/merge an entry) stays a resident's judgement
# call — this module only ever reads ``entries``/``state``/``forge``; it
# never writes ``schedule.md``.

# Tuned against this account's real ``schedule.md`` (`director tick` /
# `release-push dispatch tick`, both `every:` entries the maintainer has
# repeatedly named as overlapping "dispatch grant" authority): their full
# bodies score a ``SequenceMatcher.ratio()`` of ~0.006 — long, independently
# narrated histories sharing almost no literal prose, even though their
# *remit* plainly overlaps. So this threshold is tuned against synthetic
# near-duplicate text instead (see ``tests/test_schedule.py``): a paraphrased
# rewrite of the same entry scores ~0.70, a copy with one clause appended
# scores ~0.86, two genuinely unrelated short entries score ~0.29. 0.6
# separates paraphrase-or-closer from unrelated with real headroom either
# side. It will **not** catch this account's own standing example — see the
# worker report for #579, this is a known, load-bearing limitation, not an
# oversight: a threshold low enough to catch it would flag most entry pairs
# and make the linter cry wolf on everything, which the issue itself calls
# worse than not existing.
OVERLAP_RATIO_THRESHOLD = 0.6

_ISSUE_PR_REF_RE = re.compile(r"#(\d+)\b")

# A schedule entry's body cites forge numbers in two quite different voices:
# as *remit* ("dispatch #580") and as *provenance* ("#527 for #519's cheap
# half — the merged PR did part of the work"). Only the first goes stale when
# the number closes; the second cites it precisely *because* it closed.
# Nothing mechanical can read intent, but the sentence usually says so out
# loud, so a reference sharing its sentence with one of these words is left
# alone. Calibrated against this account's real schedule.md, where the rule's
# first live run produced exactly one finding and it was a false positive of
# this shape. Under-reporting is the correct direction here: the issue's own
# bar is that a linter which cries wolf is worse than one that does not exist.
# Deliberately narrow. The first draft also carried `was|were|already|fixed`,
# which suppresses ordinary remit prose ("check whether #580 was addressed") —
# a suppressor wide enough to swallow the rule is not a calibration, it is a
# deletion with extra steps.
_SETTLED_CONTEXT_RE = re.compile(
    r"\b(merged|closed|resolved|shipped|landed|superseded|withdrawn|"
    r"stale-open|history|historical)\b",
    re.IGNORECASE,
)


def _sentence_around(text: str, index: int) -> str:
    """The sentence-ish span of *text* containing *index* — split on `.`/newline."""
    start = max(text.rfind("\n", 0, index), text.rfind(". ", 0, index)) + 1
    ends = [e for e in (text.find("\n", index), text.find(". ", index)) if e != -1]
    end = min(ends) if ends else len(text)
    return text[start:end]


@dataclass(frozen=True)
class ScheduleFinding:
    """One mechanical observation about a schedule entry (or a pair).

    Detection only — no verdict on what to do about it. ``entry_ids`` is
    one id for ``stale-at``/``stale-reference``, two (in file order) for
    ``overlap``.
    """

    rule: str  # "stale-at" | "overlap" | "stale-reference"
    entry_ids: tuple[str, ...]
    message: str


def _format_age(seconds: float) -> str:
    """``14m`` / ``3h`` / ``2d`` — coarse, enough to judge staleness by."""
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _lint_stale_at(entries: list[ScheduleEntry], state: dict, now: float) -> list[ScheduleFinding]:
    findings: list[ScheduleFinding] = []
    for e in entries:
        if e.kind != "at" or e.at is None or now < e.at:
            continue
        age = _format_age(now - e.at)
        rec = state.get(e.id) if isinstance(state, dict) else None
        fired = bool(isinstance(rec, dict) and rec.get("fired"))
        if fired:
            message = (
                f"`at: {e.raw_when}` fired {age} ago and is still listed — "
                "nobody removed it after it ran."
            )
        else:
            message = f"`at: {e.raw_when}` passed {age} ago."
        findings.append(ScheduleFinding("stale-at", (e.id,), message))
    return findings


def _lint_overlap(entries: list[ScheduleEntry]) -> list[ScheduleFinding]:
    findings: list[ScheduleFinding] = []
    every_entries = [e for e in entries if e.kind == "every" and e.body]
    for i, a in enumerate(every_entries):
        for b in every_entries[i + 1 :]:
            ratio = difflib.SequenceMatcher(None, a.body, b.body).ratio()
            if ratio >= OVERLAP_RATIO_THRESHOLD:
                findings.append(
                    ScheduleFinding(
                        "overlap",
                        (a.id, b.id),
                        f"`{a.id}` and `{b.id}` are {ratio:.0%} similar text — "
                        "possible duplicate remit.",
                    )
                )
    return findings


def _forge_pr_lookup(forge: Any) -> dict[int, str]:
    """``{pr number: state}`` from a network-free forge PR list, or ``{}``.

    Accepts the shape :func:`brr.forge_pr_cache.read_state` already returns
    (a dict with a ``"prs"`` list) or a bare list of the same PR dicts —
    either way, purely a local read on the caller's side; this function
    itself performs none.
    """
    if isinstance(forge, dict):
        rows = forge.get("prs")
    else:
        rows = forge
    if not isinstance(rows, list):
        return {}
    out: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("number"))
        except (TypeError, ValueError):
            continue
        state = str(row.get("state") or "").strip().upper()
        if state:
            out[number] = state
    return out


def _lint_stale_reference(entries: list[ScheduleEntry], forge: Any) -> list[ScheduleFinding]:
    lookup = _forge_pr_lookup(forge)
    if not lookup:
        return []
    findings: list[ScheduleFinding] = []
    for e in entries:
        seen: set[int] = set()
        for match in _ISSUE_PR_REF_RE.finditer(e.body):
            number = int(match.group(1))
            if number in seen:
                continue
            seen.add(number)
            pr_state = lookup.get(number)
            if pr_state not in ("MERGED", "CLOSED"):
                continue
            if _SETTLED_CONTEXT_RE.search(_sentence_around(e.body, match.start())):
                continue  # cited as provenance, not as pending work
            findings.append(
                ScheduleFinding(
                    "stale-reference",
                    (e.id,),
                    f"names #{number}, which is {pr_state} — worth checking "
                    "it is cited as history and not as pending work.",
                )
            )
    return findings


def lint_schedule(
    entries: list[ScheduleEntry],
    *,
    now: float,
    state: dict | None = None,
    forge: Any | None = None,
) -> list[ScheduleFinding]:
    """Mechanical, deterministic findings about ``entries`` — no I/O, no model.

    Three rules, each independent (an entry can trip more than one):

    - ``stale-at`` — an ``at:`` entry whose instant has passed. When *state*
      (the daemon's firing-state map, :func:`load_state`) shows it already
      fired, that is named as a *stronger* finding (it fired and nobody
      removed it), not a weaker one.
    - ``overlap`` — two ``every:`` entries whose bodies are near-duplicate
      text (:data:`OVERLAP_RATIO_THRESHOLD`). ``at:`` entries are excluded:
      a one-shot's overlap with another one-shot is moot once either fires.
    - ``stale-reference`` — an entry whose body names a ``#<number>`` that
      *forge* (a network-free PR list/state — see
      :func:`brr.forge_pr_cache.read_state`) reports ``MERGED`` or
      ``CLOSED``, *and* whose surrounding sentence does not already speak of
      it in the settled past (see :data:`_SETTLED_CONTEXT_RE`) — an entry
      citing a merged PR as provenance is not stale, it is well-sourced.
      Skipped entirely when *forge* is ``None`` or carries no usable rows — this rule only ever reads a local cache, never a forge
      API, so an absent cache just means this rule finds nothing, not an
      error.

    Runs on any Core, including an economy one: every comparison here is
    string/arithmetic, nothing calls out and nothing judges — that
    determinism is the entire point of this half of issue #579.
    """
    findings: list[ScheduleFinding] = []
    findings.extend(_lint_stale_at(entries, state or {}, now))
    findings.extend(_lint_overlap(entries))
    findings.extend(_lint_stale_reference(entries, forge))
    return findings


# ── Schedule-entry trust-tier attribution (#413 §7 S8) ───────────────
#
# The daemon attributes authorship of a schedule entry to the run that
# wrote it, at that run's resolved trust tier.  The record lives in the
# daemon-owned state file (alongside the existing firing-state) so it
# survives daemon restarts and is not written by the run's own agent —
# the daemon derives it from a git diff rather than accepting a claim.
# It is *not* beyond the reach of a collaborator run: see the forgeability
# limit named at the attribution seam in ``daemon.py`` (S7's slice, not
# S8's) — a `worktree`-env run can reach this state file directly.
#
# State keys (prefixed `_` to distinguish from entry-id records):
#   _tier_by_entry:      {entry_id: {"tier": ..., "fp": ...}}
#   _noticed_untiered:   [entry_id, ...]  — one-time notice already sent
#   _tier_grandfathered: true  — the one-time grandfather pass has run
#
# **The record has a lifecycle**, which is the whole of the #644 rework:
#
#   created / re-recorded — when a run's *own dominion commit* changes
#       that entry's fingerprint.  Not "when the id is new": rewriting an
#       entry's body leaves the id untouched, and that edit is the exact
#       escalation the id-set predicate could not see.
#   grandfathered — once, at the first tick after upgrade, for every entry
#       present in schedule.md at that moment: stamped `owner`, explicitly.
#       Those entries predate S8 and there is no evidence to attribute them
#       with; recording the value *at the moment of consent* is what lets
#       absence mean something afterwards.
#   read — only when the recorded fingerprint still matches the entry on
#       disk.  A drifted fingerprint means the entry changed with no
#       attributing commit, so the record no longer speaks for this text.
#   pruned — at the fire site, for ids no longer in the schedule at all,
#       so a deleted entry's tier cannot be inherited by a later entry
#       that reuses its id, and neither map grows without bound.
#
# **Absence fails closed.**  After the grandfather pass, an entry with no
# usable record fires at ``UNRECORDED_TIER_FLOOR`` (collaborator), with a
# one-time notice naming it — never at owner.  The old behaviour (fall
# through to the ``schedule`` entry in trust._OWNER_SOURCES) made *not
# being attributed* the most authoritative state an entry could be in, so
# every path that dodged attribution — a run committing its own dominion
# so the capture net finds a clean tree, a crash before the seam, a future
# seam nobody thought of — escalated instead of demoting.  The floor is a
# structural property, not an enumeration of those paths: declining to be
# attributed can only ever *lower* what an entry may do.
#
# The stamp S8 writes into create_event beats the source-based default
# because trust.resolve_tier prefers the explicit stamp; with the floor,
# the fire site now always stamps, so _OWNER_SOURCES["schedule"] is no
# longer reachable for an entry that fires through this path.

_TIER_BY_ENTRY_KEY = "_tier_by_entry"
_NOTICED_UNTIERED_KEY = "_noticed_untiered"
_TIER_GRANDFATHERED_KEY = "_tier_grandfathered"

# The tier an entry fires at when nothing is known about who authored it.
# Named here rather than inlined at the fire site: it is the security
# property this slice exists for, and F4 on #644 was two comments
# disagreeing about one.
UNRECORDED_TIER_FLOOR = trust.COLLABORATOR


def entry_fingerprint(entry: ScheduleEntry) -> str:
    """Return a stable content fingerprint for *entry*.

    Covers everything that decides *what gets said and when* — kind,
    trigger, ``conversation_key``, ``reset_on``, body — so that editing an
    entry in place is a visible event to attribution.  The id is
    deliberately excluded: the fingerprint is the record's *value*, keyed
    by id, and folding the key into the value buys nothing.

    The trigger is taken from ``raw_when`` (the authored string) rather
    than the parsed ``at``/``interval``.  That is not cosmetic: quota
    pacing rebuilds entries with ``replace(e, interval=...)`` before
    firing, so a fingerprint over the parsed interval would change under
    the daemon's own hand every time quota dipped, and every ``every:``
    entry would drift out of its record on a paced tick.
    """
    payload = json.dumps(
        [
            entry.kind,
            entry.raw_when,
            entry.conversation_key or "",
            entry.reset_on or "",
            entry.body,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fingerprints_from_text(text: str) -> dict[str, str]:
    """Return ``{entry_id: fingerprint}`` for a ``schedule.md`` *content* string."""
    return {e.id: entry_fingerprint(e) for e in parse_schedule_text(text)}


def _read_record(state: dict, entry_id: str) -> tuple[str, str] | None:
    """Return ``(tier, fp)`` for *entry_id*, or ``None`` when unrecorded.

    Only the ``{"tier": ..., "fp": ...}`` shape is a record.  Anything else
    — a bare string, a truncated dict, hand-written junk — is not evidence
    about who wrote which body, so it reads as unrecorded and the entry
    takes the floor.  (There is no compatibility shim for the pre-rework
    bare-string shape: S8 has never been on ``main``, so no such record
    exists in the world.)
    """
    tier_map = state.get(_TIER_BY_ENTRY_KEY)
    if not isinstance(tier_map, dict):
        return None
    rec = tier_map.get(entry_id)
    if isinstance(rec, dict):
        tier = str(rec.get("tier") or "")
        return (tier, str(rec.get("fp") or "")) if tier else None
    return None


def tier_for_entry(state: dict, entry_id: str, fingerprint: str) -> str | None:
    """Return the attributed tier for *entry_id*, or ``None`` if unusable.

    ``None`` means "no run is known to have authored *this* text": the
    entry appeared with no attributing commit, or it has been edited since
    it was attributed with no commit to attribute the edit to.  Either way
    the fire site fires it at ``UNRECORDED_TIER_FLOOR`` with a one-time
    notice — never at owner.

    *fingerprint* is required, not optional.  An optional one would let a
    caller silently skip the check that makes the record mean anything,
    and the fire site is the only caller that matters.
    """
    rec = _read_record(state, entry_id)
    if rec is None:
        return None
    tier, fp = rec
    return tier if fp and fp == fingerprint else None


def record_entry_tiers(brr_dir: Path, touched: dict[str, str], tier: str) -> None:
    """Persist trust-tier attribution for *touched* (``{id: fingerprint}``).

    Called at the capture-net seam with the entries **this run's own
    dominion commit changed** — added, or edited in place — at the run's
    resolved trust tier (``task.meta["trust_tier"]``).

    **Last write wins, deliberately**, reversing the first-wins rule the
    id-set predicate needed.  Under that predicate re-recording was
    unsound: a global before/after snapshot cannot say *who* changed an
    entry, so a concurrent owner run finalizing after a collaborator's
    edit would re-attribute that edit to owner.  Sourcing *touched* from
    the run's own commit removes the ambiguity — this run changed these
    entries, at this tier — so overwriting is now the correct rule and the
    only one that can follow an entry through an edit.

    Best-effort: I/O errors are swallowed so capture never fails because
    of attribution.
    """
    if not touched or not tier:
        return
    try:
        state = load_state(brr_dir)
        tier_map: dict = dict(state.get(_TIER_BY_ENTRY_KEY) or {})
        for eid in sorted(touched):  # sorted for deterministic output
            tier_map[eid] = {"tier": tier, "fp": touched[eid]}
        state[_TIER_BY_ENTRY_KEY] = tier_map
        save_state(brr_dir, state)
    except OSError:
        pass


def grandfather_entry_tiers(
    state: dict, fingerprints: dict[str, str]
) -> list[str] | None:
    """Stamp every currently-present entry as ``owner``, once.

    Returns the ids stamped (possibly empty — an upgrade onto an empty
    schedule still closes the window), or ``None`` when the pass has
    already run and *state* was not touched.

    The migration that makes the floor safe to introduce.  Entries written
    before S8 have no attributing commit and never will; without this pass
    the floor would silently demote the operator's whole existing schedule
    on upgrade, and *with* the old fall-through-to-owner rule absence stays
    an escalation forever.  So the value is written at the one moment the
    operator's consent to it is real — the schedule as it stands at
    upgrade — and recorded explicitly, like every other record.

    Runs on the **first tick after upgrade and no later**, which is why the
    marker is set even when there is nothing to stamp.  A pass that waited
    for the first tick that happened to see a schedule would grandfather
    whatever was written in the meantime — including by a run that declined
    attribution, which is the exact hole the floor closes.

    Entries that already carry a record are left alone: a run may have
    finalized between daemon start and this tick, and its attribution is
    evidence where the grandfather is only a default.
    """
    if state.get(_TIER_GRANDFATHERED_KEY):
        return None
    tier_map: dict = dict(state.get(_TIER_BY_ENTRY_KEY) or {})
    stamped: list[str] = []
    for eid in sorted(fingerprints):  # sorted for deterministic output
        if eid not in tier_map:
            tier_map[eid] = {"tier": trust.OWNER, "fp": fingerprints[eid]}
            stamped.append(eid)
    if tier_map:
        state[_TIER_BY_ENTRY_KEY] = tier_map
    state[_TIER_GRANDFATHERED_KEY] = True
    return stamped


def prune_entry_records(state: dict, present_ids: "set[str] | frozenset[str]") -> bool:
    """Drop tier records and notice marks for ids no longer in the schedule.

    Mutates *state* in place; returns whether anything changed.

    **Call this against the full parsed entry list, never against the
    pacing-filtered one.**  A quota-critical tick drops every ``every:``
    entry from the list handed to :func:`due_entries`, and pruning against
    *that* would delete the tier record of every recurring entry the
    moment quota dipped — after which each one would fire as ``owner``.
    It is the same hazard ``dropped_ids`` already exists to prevent for
    firing state, in the one direction where the cost is an escalation
    rather than a missed beat.
    """
    changed = False
    tier_map = state.get(_TIER_BY_ENTRY_KEY)
    if isinstance(tier_map, dict):
        kept = {k: v for k, v in tier_map.items() if k in present_ids}
        if kept != tier_map:
            state[_TIER_BY_ENTRY_KEY] = kept
            changed = True
    noticed = state.get(_NOTICED_UNTIERED_KEY)
    if isinstance(noticed, list):
        kept_notices = [k for k in noticed if k in present_ids]
        if kept_notices != noticed:
            state[_NOTICED_UNTIERED_KEY] = kept_notices
            changed = True
    return changed


def render_lint_block(findings: list[ScheduleFinding]) -> str:
    """Render *findings* as a short flagged block, or ``""`` when there are none.

    Zero findings must render **nothing** — not even a "no findings" line.
    A clean schedule is not news, and a line printed every wake for the
    common case is a tax paid forever for a rare event.
    """
    if not findings:
        return ""
    lines = [
        "**Schedule lint** (mechanical, no judgement applied — yours to "
        "resolve: keep / cancel / postpone / rephrase / merge):",
    ]
    for f in findings:
        ids = ", ".join(f"`{i}`" for i in f.entry_ids)
        lines.append(f"- {f.rule} — {ids}: {f.message}")
    return "\n".join(lines)
