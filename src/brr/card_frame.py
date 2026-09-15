"""The frame's hand on the card — ticks and the delta (design-the-loom §19).

Two readers over rows the daemon already keeps, run once per heartbeat by
:func:`frame_pass`. Neither is a new sensor; each removes a chore the body
was failing at.

**The plan's ticks (§19.1).** A ``- [ ]`` line on ``.card`` that carries a
*coordinate* — a PR (``#N``), a branch (``brr/…``) or a strand id
(``run-YYMMDD-HHMM-xxxx`` / ``evt-<ns>-xxxx``) — is ticked by the frame when
that PR merges, that branch's PR merges, or that strand returns. Only such
lines: every other line is the weaver's, and so is un-ticking one. A tick
lands only for a fact that happened *after* the frame first saw the open
line, so a line written about an already-merged PR ("follow up on #1975")
is left alone. Each write records a ``card_ticks:`` advisory notice naming
the lines.

Facts: a merge is a ``land:`` produce row, a ``merge`` relic, or a
``MERGED`` row in the forge PR cache (the daemon's own network-free read,
which is how a merge done by hand "observed on main" arrives); a return is a
``spawn_completed`` / ``spawn_submitted`` event.

**The card's delta as mail (§19.2).** At the moments the frame recognises —
a strand returned, a PR merged, a delivery batch, a refusal — it drafts one
line from the ledger, *since your last card write: …*, and stages it as a
pending item on the portal (``card.delta{id, text, at, trigger}``). The
hooks render it the way they render finished spawns: named, kept, never an
obligation. ``note: <id>`` accepts it; the weaver's next card edit folds it
in (the item leaves on its own). It replaces the ``card behind (N acts)``
counter.

It is **not** an inbox event, on purpose: any pending event resolves a
``brnrd await`` and can dispatch a run, and a delta must never cost a turn
(§19 item 6).

"The weaver's last card write" excludes the frame's own hand: the card is
compared with its two frame blocks (``## Said``, ``## Ledger``) removed and
the frame's ticks un-ticked, so neither a said-row, a ledger line nor a tick
reads as the weaver catching up.

**The card in two halves (§19.3).** ``## Ledger`` is the frame's half:
rebuilt on every pass from the HUD and the run's ledgers — produce, strands,
merges, spend, heddles — and spliced in before ``## Said`` (or at the end).
Every other heading is the weaver's and is never touched beyond the ticks.
With the ledger on the card, the delta keeps only what the ledger cannot
show: replies delivered and refusals. See :func:`build_ledger`.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

META_KEY = "card_frame"
CARD_NAME = ".card"
DELTA_ID_PREFIX = "card-delta-"
_SAID_HEADING = "## Said"
LEDGER_HEADING = "## Ledger"
LEDGER_LEGEND = (
    "⇐ projected by the frame from the HUD and the run's ledgers · rebuilt every heartbeat · "
    "edits here are overwritten"
)
#: The frame's two blocks: never the weaver's, never in the intent hash.
FRAME_HEADINGS = (_SAID_HEADING, LEDGER_HEADING)
#: The whole block, heading and legend included.
LEDGER_MAX_LINES = 24
_LEDGER_REFS = 5
_LEDGER_MERGES = 6
_LEDGER_STRANDS_LIVE = 6
_LEDGER_STRANDS_DONE = 6
_LEDGER_TITLE_CHARS = 60
_OPEN_RE = re.compile(r"^(\s*[-*+]\s+)\[ \](\s+.*)$")
_DONE_RE = re.compile(r"^(\s*[-*+]\s+)\[[xX]\](\s+.*)$")
PR_RE = re.compile(r"(?<![\w&])#(\d{1,6})\b")
BRANCH_RE = re.compile(r"(?<![\w/])(brr/[A-Za-z0-9][A-Za-z0-9._/-]*[A-Za-z0-9])")
STRAND_RE = re.compile(r"\b(run-\d{6}-\d{4}-[a-z0-9]{4}|evt-\d{16,}-[a-z0-9]{4})\b")
_SEEN_LINES_MAX = 200
_SEEN_IDS_MAX = 200
_DELTA_TEXT_MAX = 240
_RETURN_SOURCES = ("spawn_completed", "spawn_submitted")


# ── time ─────────────────────────────────────────────────────────────────


def _epoch(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_epoch(event: Mapping[str, Any]) -> float | None:
    at = _epoch(event.get("created") or event.get("created_at"))
    if at is not None:
        return at
    match = re.match(r"^evt-(\d{16,})-", str(event.get("id") or ""))
    return int(match.group(1)) / 1e9 if match else None


# ── facts ────────────────────────────────────────────────────────────────


@dataclass
class Facts:
    #: PR number → when it merged (``None`` when the source carries no time).
    merged_prs: dict[int, float | None] = field(default_factory=dict)
    #: head branch → when its PR merged.
    merged_branches: dict[str, float | None] = field(default_factory=dict)
    #: strand run id / dispatch event id → when it returned.
    returned: dict[str, float | None] = field(default_factory=dict)
    #: ids of the returns, one entry per strand, for the delta's wording.
    return_labels: dict[str, float | None] = field(default_factory=dict)
    #: PR number → head branch, where the forge cache says.
    pr_branch: dict[int, str] = field(default_factory=dict)


def _keep_earliest(store: dict, key, at: float | None) -> None:
    if key not in store or store[key] is None:
        store[key] = at
    elif at is not None:
        store[key] = min(store[key], at)


def gather_facts(
    *,
    events: Iterable[Mapping[str, Any]] = (),
    produce_rows: Iterable[Mapping[str, Any]] = (),
    relics: Iterable[Mapping[str, Any]] = (),
    forge_prs: Iterable[Mapping[str, Any]] = (),
) -> Facts:
    facts = Facts()
    for event in events or ():
        if not isinstance(event, Mapping):
            continue
        if str(event.get("source") or "") not in _RETURN_SOURCES:
            continue
        at = _event_epoch(event)
        run = str(event.get("spawned_by_run") or "").strip()
        dispatch = str(event.get("spawned_by_event") or "").strip()
        for key in (run, dispatch):
            if key:
                _keep_earliest(facts.returned, key, at)
        label = run or dispatch
        if label:
            _keep_earliest(facts.return_labels, label, at)
        # A returned strand's PR is not a merge — it only names the branch.
    for row in produce_rows or ():
        if not isinstance(row, Mapping) or str(row.get("verb") or "") != "land":
            continue
        try:
            number = int(str(row.get("pr") or "").lstrip("#"))
        except ValueError:
            continue
        _keep_earliest(facts.merged_prs, number, _epoch(row.get("at")))
    for record in relics or ():
        if not isinstance(record, Mapping) or record.get("kind") != "merge":
            continue
        try:
            number = int(str(record.get("pr") or "").lstrip("#"))
        except ValueError:
            continue
        _keep_earliest(facts.merged_prs, number, _epoch(record.get("at")))
    for pr in forge_prs or ():
        if not isinstance(pr, Mapping) or str(pr.get("state") or "").upper() != "MERGED":
            continue
        at = _epoch(pr.get("merged_at"))
        try:
            number = int(pr.get("number"))
        except (TypeError, ValueError):
            number = None
        branch = str(pr.get("branch") or "").strip()
        if number is not None:
            _keep_earliest(facts.merged_prs, number, at)
            if branch:
                facts.pr_branch[number] = branch
        if branch:
            _keep_earliest(facts.merged_branches, branch, at)
    return facts


# ── the ticks ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tick:
    index: int
    line: str
    coordinate: str
    why: str


def coordinates(line: str) -> list[tuple[str, str]]:
    """``(kind, value)`` for every coordinate on *line*: pr · branch · strand."""
    out: list[tuple[str, str]] = []
    out.extend(("pr", m.group(1)) for m in PR_RE.finditer(line))
    out.extend(("branch", m.group(1)) for m in BRANCH_RE.finditer(line))
    out.extend(("strand", m.group(1)) for m in STRAND_RE.finditer(line))
    return out


def _line_key(line: str) -> str:
    return " ".join(line.split())


def propose_ticks(
    card_text: str,
    facts: Facts,
    first_seen: Mapping[str, float],
) -> list[Tick]:
    """The open coordinate lines whose things have *all* happened since the
    line was first seen. A line naming two PRs waits for both: a tick is a
    claim, and half a line is not done."""
    ticks: list[Tick] = []
    for index, line in enumerate((card_text or "").split("\n")):
        if not _OPEN_RE.match(line):
            continue
        seen_at = first_seen.get(_line_key(line))
        coords = coordinates(line)
        if seen_at is None or not coords:
            continue
        reasons: list[str] = []
        for kind, value in coords:
            if kind == "pr":
                at, why = facts.merged_prs.get(int(value), False), f"#{value} merged"
            elif kind == "branch":
                at, why = facts.merged_branches.get(value, False), f"{value}'s PR merged"
            else:
                at, why = facts.returned.get(value, False), f"{value} returned"
            if at is False or (at is not None and at < seen_at):
                reasons = []
                break
            reasons.append(why)
        if reasons:
            ticks.append(Tick(index=index, line=line, coordinate=coords[0][1], why=" · ".join(reasons)))
    return ticks


def apply_ticks(card_text: str, ticks: Iterable[Tick]) -> str:
    lines = (card_text or "").split("\n")
    for tick in ticks:
        if 0 <= tick.index < len(lines) and lines[tick.index] == tick.line:
            lines[tick.index] = _OPEN_RE.sub(r"\1[x]\2", tick.line, count=1)
    return "\n".join(lines)


# ── the weaver's own write ───────────────────────────────────────────────


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """``(start, end)`` of the section under *heading* — the heading's own line
    through the character before the next ``## `` line (or the end)."""
    offset = 0
    start = -1
    for line in text.splitlines(keepends=True):
        if start == -1:
            if line.rstrip() == heading:
                start = offset
        elif line.startswith("## "):
            return start, offset
        offset += len(line)
    return (start, len(text)) if start != -1 else None


def _strip_frame_blocks(text: str) -> str:
    """*text* without the frame's blocks (``## Said``, ``## Ledger``)."""
    for heading in FRAME_HEADINGS:
        span = _section_span(text, heading)
        if span is not None:
            text = text[: span[0]] + text[span[1]:]
    return text


#: The name #1978 shipped; it strips both frame blocks now.
_strip_said = _strip_frame_blocks


def intent_hash(card_text: str, frame_ticked: Iterable[str] = ()) -> str:
    """A digest of the weaver's half: no frame blocks, the frame's ticks undone."""
    ticked = set(frame_ticked)
    lines = []
    for line in _strip_frame_blocks(card_text or "").split("\n"):
        if _DONE_RE.match(line) and _line_key(_DONE_RE.sub(r"\1[ ]\2", line)) in ticked:
            line = _DONE_RE.sub(r"\1[ ]\2", line)
        lines.append(line.rstrip())
    return hashlib.sha1("\n".join(lines).strip().encode("utf-8")).hexdigest()


# ── the delta ────────────────────────────────────────────────────────────


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


#: What the delta still says (§19.3): the two things the ``## Ledger`` block
#: cannot show. Strand returns, merges and commits are ledger lines now, and a
#: fact said twice — once on the card, once as mail — is the nag §19 item 6
#: refuses.
DELTA_KEEPS = ("delivered", "refusals")


def draft_delta(*, delivered: int, refusals: int) -> str | None:
    """*since your last card write: …* — one line, or ``None`` when nothing moved.

    Only :data:`DELTA_KEEPS`: replies delivered (the ``## Said`` rows are the
    lead lines, not the count, and they roll off at six) and refusals (a
    notice, never a ledger row).
    """
    parts: list[str] = []
    if delivered:
        parts.append(_plural(delivered, "reply delivered", "replies delivered"))
    if refusals:
        parts.append(_plural(refusals, "refusal", "refusals"))
    if not parts:
        return None
    text = "since your last card write: " + " · ".join(parts)
    if len(text) > _DELTA_TEXT_MAX:
        text = text[: _DELTA_TEXT_MAX - 1].rstrip() + "…"
    return text


def delivered_total(stats: Mapping[str, Any] | None) -> int:
    stats = stats or {}
    total = 0
    for key in ("current", "other", "outbound"):
        try:
            total += int(stats.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _counted_notices(notices: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        n for n in notices or ()
        if isinstance(n, Mapping)
        and n.get("kind") in ("refused", "dropped")
        and n.get("lifetime") != "standing"
    ]


# ── the ledger half (§19.3) ──────────────────────────────────────────────

_LOOM_KINDS = ("knot", "heddle", "card", "page")


def _coarse_tokens(value: object) -> str:
    """A token count at two significant figures, rounded down: ``38k`` · ``380k``
    · ``1.1m`` · ``15m``. The ledger is a card write, and a card write is a
    mirror write; the chip's one-decimal ``38.4k`` moves every boundary."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    sign, n = ("-", -n) if n < 0 else ("", n)
    if n >= 100:
        step = 10 ** (len(str(n)) - 2)
        n = (n // step) * step
    for bound, suffix in ((1_000_000, "m"), (1_000, "k")):
        if n >= bound:
            return sign + f"{n / bound:.1f}".rstrip("0").rstrip(".") + suffix
    return sign + str(n)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _short_ref(kind: str, ref: str) -> str:
    if kind == "knot" and re.fullmatch(r"[0-9a-f]{8,40}", ref):
        return ref[:7]
    return ref


def _produce_lines(hud: Mapping[str, Any]) -> list[str]:
    ledger = _mapping(_mapping(hud.get("produce")).get("ledger"))
    counts = _mapping(ledger.get("counts"))
    last = [e for e in ledger.get("last") or () if isinstance(e, Mapping) and e.get("ref")]
    total = 0
    for kind in _LOOM_KINDS:
        try:
            total += int(counts.get(kind) or 0)
        except (TypeError, ValueError):
            continue
    if not total and not last:
        return []
    lines = ["- produce: " + " · ".join(f"{kind} {int(counts.get(kind) or 0)}" for kind in _LOOM_KINDS)]
    if last:
        # ``ledger.last`` is newest first; the block reads oldest → newest.
        refs = [f"{e.get('kind')} {_short_ref(str(e.get('kind')), str(e.get('ref')))}"
                for e in reversed(last[:_LEDGER_REFS])]
        lines.append("- last: " + " · ".join(refs))
    return lines


def _strand_lines(
    hud: Mapping[str, Any], events: Iterable[Mapping[str, Any]], since: float | None,
) -> list[str]:
    finished: list[tuple[float, str, str]] = []
    finished_ids: set[str] = set()
    for event in events or ():
        if not isinstance(event, Mapping) or event.get("source") != "spawn_completed":
            continue
        at = _event_epoch(event)
        if since is not None and at is not None and at < since:
            continue
        ident = str(event.get("spawned_by_run") or event.get("spawned_by_event") or "").strip()
        if not ident:
            continue
        outcome = str(event.get("spawn_status") or "").strip() or "status unknown"
        pr = str(event.get("spawn_pr_number") or "").strip().lstrip("#")
        if pr:
            outcome += f" · #{pr}"
        finished.append((at or 0.0, ident, outcome))
        finished_ids.update(
            str(event.get(k) or "").strip() for k in ("spawned_by_run", "spawned_by_event")
        )
    finished_ids.discard("")
    finished.sort(key=lambda row: (row[0], row[1]))

    resources = _mapping(hud.get("resources"))
    children = _mapping(resources.get("coexisting_runs")).get("owned_children")
    if not isinstance(children, list):
        children = _mapping(_mapping(resources.get("quota")).get("draws")).get("strands")
    live: list[str] = []
    for child in children if isinstance(children, list) else ():
        if not isinstance(child, Mapping):
            continue
        ident = str(child.get("run_id") or child.get("event_id") or "").strip()
        if not ident or ident in finished_ids or str(child.get("event_id") or "") in finished_ids:
            continue
        line = f"- strand live: {ident}"
        title = " ".join(str(child.get("title") or "").split())
        if len(title) > _LEDGER_TITLE_CHARS:
            title = title[:_LEDGER_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" —-·:,") + "…"
        if title:
            line += f" — {title}"
        if child.get("status") == "submitted":
            line += " (submitted)"
        live.append(line)

    lines = live[:_LEDGER_STRANDS_LIVE]
    if len(live) > _LEDGER_STRANDS_LIVE:
        lines.append(f"- strand live: +{len(live) - _LEDGER_STRANDS_LIVE} more")
    shown = finished[-_LEDGER_STRANDS_DONE:]
    if len(finished) > len(shown):
        lines.append(f"- strand done: +{len(finished) - len(shown)} earlier")
    lines.extend(f"- strand done: {ident} — {outcome}" for _, ident, outcome in shown)
    return lines


def _merge_line(
    produce_rows: Iterable[Mapping[str, Any]],
    relics: Iterable[Mapping[str, Any]],
    merged: Mapping[int, float | None] | None = None,
) -> list[str]:
    """``- merges: #N → sha`` — ``land:`` rows and ``merge`` relics carry the
    sha; *merged* (the forge cache's merges this card is about, see
    :func:`ledger_merges`) adds the ones merged by hand, as bare ``#N``."""
    def number(value: object) -> int | None:
        try:
            return int(str(value or "").strip().lstrip("#"))
        except ValueError:
            return None

    rows: dict[int, tuple[float, str]] = {}  # number → (when, sha)

    def admit(n: int, at: float | None, sha: str) -> None:
        when = at if at is not None else float("inf")
        seen = rows.get(n)
        if seen is None:
            rows[n] = (when, sha)
        else:
            rows[n] = (min(seen[0], when), seen[1] or sha)

    for row in produce_rows or ():
        if isinstance(row, Mapping) and row.get("verb") == "land":
            n = number(row.get("pr"))
            if n is not None:
                admit(n, _epoch(row.get("at")), str(row.get("ref") or row.get("sha") or ""))
    for record in relics or ():
        if isinstance(record, Mapping) and record.get("kind") == "merge":
            n = number(record.get("pr") or record.get("number"))
            if n is not None:
                admit(n, _epoch(record.get("at")), str(record.get("sha") or ""))
    for n, at in (merged or {}).items():
        admit(int(n), at, "")
    if not rows:
        return []
    ordered = sorted(rows.items(), key=lambda kv: (kv[1][0], kv[0]))[-_LEDGER_MERGES:]
    return ["- merges: " + " · ".join(
        f"#{n} → {sha[:7]}" if sha else f"#{n}" for n, (_, sha) in ordered
    )]


def run_started(run_id: str) -> float | None:
    """When a ``run-YYMMDD-HHMM-xxxx`` began (UTC), from its id."""
    match = re.match(r"^run-(\d{6})-(\d{4})-", str(run_id or ""))
    if not match:
        return None
    try:
        stamp = _dt.datetime.strptime(match.group(1) + match.group(2), "%y%m%d%H%M")
    except ValueError:
        return None
    return stamp.replace(tzinfo=_dt.timezone.utc).timestamp()


def ledger_merges(
    facts: Facts, *, card_text: str, produce_rows, relics, meta: Mapping[str, Any], run_id: str,
) -> dict[int, float | None]:
    """The merges the ledger names beyond its own ``land:`` rows: the ones the
    delta used to name (:func:`_merge_is_ours` — the card, the run's relics or
    its own PR/branch), merged since the run began. A card line about a PR
    merged last week is not this run's merge."""
    began = run_started(run_id)
    if began is None:
        return {}
    return {
        n: at for n, at in facts.merged_prs.items()
        if at is not None and at >= began
        and _merge_is_ours(n, facts, card_text, produce_rows, relics, meta)
    }


def _spend_line(hud: Mapping[str, Any]) -> list[str]:
    facet = _mapping(_mapping(hud.get("resources")).get("allowance"))
    if facet.get("status") != "known" or facet.get("spent") is None:
        return []
    spent, tokens = facet.get("spent"), facet.get("tokens")
    parts: list[str] = []
    if str(facet.get("scope") or "strand") == "resident" and not facet.get("explicit"):
        parts.append(f"spend {_coarse_tokens(spent)}")
    elif tokens:
        text = f"spend {_coarse_tokens(spent)}/{_coarse_tokens(tokens)}"
        try:
            text += f" · {int(100 * float(spent) / float(tokens))}%"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        parts.append(text)
    stake = _mapping(facet.get("stake"))
    if stake.get("state") in ("armed", "cut"):
        if stake.get("unit") == "share":
            denominator = f"{float(stake.get('share_pct') or 0):g}%"
        else:
            denominator = _coarse_tokens(stake.get("tokens"))
        text = f"stake {_coarse_tokens(stake.get('spent') or 0)}/{denominator}"
        try:
            text += f" · {int(100 * float(stake.get('spent') or 0) / float(stake.get('tokens')))}%"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        if stake.get("state") == "cut":
            text += " · at cut-at"
        parts.append(text)
    return ["- spend: " + " · ".join(parts)] if parts else []


def _heddle_line(heddles: Iterable[Mapping[str, Any]] | None) -> list[str]:
    from . import heddles as heddles_mod

    segment = heddles_mod.chip_segment(heddles or ())
    return [f"- heddles: {segment}"] if segment else []


def build_ledger(
    hud: Mapping[str, Any] | None,
    *,
    produce_rows: Iterable[Mapping[str, Any]] = (),
    relics: Iterable[Mapping[str, Any]] = (),
    events: Iterable[Mapping[str, Any]] = (),
    heddles: Iterable[Mapping[str, Any]] | None = None,
    since: float | None = None,
    merged: Mapping[int, float | None] | None = None,
) -> list[str]:
    """The ``## Ledger`` block's lines, heading and legend excluded — each part
    only when it has something to say, oldest → newest within a part.

    ========  ==========================================================
    produce   ``hud.produce.ledger`` — the four kinds' counts, the last
              five refs (``knot a06f566 · heddle #1975 · page x.md``)
    strands   live: ``hud.resources.coexisting_runs.owned_children``
              (else ``quota.draws.strands``), id — title (submitted);
              done: pending ``spawn_completed`` events since *since*
              (the weaver's last card write), id — ``spawn_status``
    merges    ``land:`` rows (``produce.jsonl``) and ``merge`` relics
              (``.relics.jsonl``), ``#N → sha``; plus *merged* — the
              forge cache's merges of this card's PRs since the run
              began (:func:`ledger_merges`), ``#N`` (the cache has no sha)
    spend     ``hud.resources.allowance``: the seat's or strand's reading,
              and the stake when one is on (``stake 1.1m/5% · 22%``)
    heddles   *heddles* (else ``hud.heddles``) as the chip prints them
    ========  ==========================================================
    """
    hud = _mapping(hud)
    if heddles is None:
        heddles = [h for h in hud.get("heddles") or () if isinstance(h, Mapping)]
    lines = (
        _produce_lines(hud)
        + _strand_lines(hud, events, since)
        + _merge_line(produce_rows, relics, merged)
        + _spend_line(hud)
        + _heddle_line(heddles)
    )
    return lines[: LEDGER_MAX_LINES - 2]


def render_ledger(lines: Iterable[str]) -> str:
    """The block as it stands on the card: heading, legend, lines; no trailing newline."""
    return "\n".join([LEDGER_HEADING, LEDGER_LEGEND, *lines])


def splice_ledger(card_text: str, section: str) -> str:
    """*card_text* with *section* as its ``## Ledger`` block.

    Replaces the block in place when the heading stands; otherwise inserts it
    just before ``## Said``, or appends it. Never rewrites a byte outside the
    block except the blank line that separates it — a heartbeat that renders
    the same block returns the same text.
    """
    text = card_text or ""
    span = _section_span(text, LEDGER_HEADING)
    if span is not None:
        after = text[span[1]:]
        return text[: span[0]] + section + ("\n\n" if after else "\n") + after
    said = _section_span(text, _SAID_HEADING)
    if said is not None:
        before = text[: said[0]]
        if before and not before.endswith("\n\n"):
            before += "\n" if before.endswith("\n") else "\n\n"
        return before + section + "\n\n" + text[said[0]:]
    if not text:
        return section + "\n"
    sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return text + sep + section + "\n"


def card_halves(card_text: str) -> tuple[str, str]:
    """``(weaver's half, frame's half)`` of a card as it stands: the frame's
    half is its two blocks, in card order; the weaver's is everything else."""
    text = card_text or ""
    spans = sorted(
        span for span in (_section_span(text, h) for h in FRAME_HEADINGS) if span is not None
    )
    frame = "\n\n".join(text[a:b].strip("\n") for a, b in spans)
    return _strip_frame_blocks(text).strip("\n"), frame


def read_portal(outbox_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads((Path(outbox_dir) / "portal-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_card_if_unchanged(path: Path, base: str, text: str) -> bool:
    """Write *text* only if the card still reads *base* — a weaver write that
    lands between the frame's read and its write wins, and the frame retries
    next heartbeat."""
    try:
        if path.read_text(encoding="utf-8", errors="replace") != base:
            return False
    except OSError:
        return False
    return _write_card(path, text)


def _project_ledger(state, result, *, card_path, card_text, outbox_dir, hud, heddles,
                    produce_rows, relics, events, since, merged, notice, notices_mod) -> str:
    """Splice the rebuilt ledger into the card; returns the card text now on disk."""
    if not card_text.strip():
        return card_text  # no card yet: the weaver writes it first
    if hud is None:
        hud = read_portal(outbox_dir)
    lines = build_ledger(
        hud, produce_rows=produce_rows, relics=relics, events=events,
        heddles=heddles, since=since or None, merged=merged,
    )
    present = _section_span(card_text, LEDGER_HEADING) is not None
    readding = False
    if not present:
        if state.get("ledger_written"):
            if state.get("ledger_readded"):
                return card_text  # deleted twice: the weaver's call stands for this run
            readding = True
        elif not lines:
            return card_text  # nothing to project: no empty heading on a fresh card
    updated = splice_ledger(card_text, render_ledger(lines))
    if updated == card_text:
        state["ledger_written"] = True
        return card_text
    if not _write_card_if_unchanged(card_path, card_text, updated):
        return card_text
    state["ledger_written"] = True
    result.ledger_written = True
    if readding:
        state["ledger_readded"] = True
        text = (
            "card_ledger: `## Ledger` was deleted from the card — re-added once; "
            "it is the frame's half, rebuilt every heartbeat. Delete it again and "
            "it stays off for this run."
        )
        if notice is not None:
            notice("advisory", text)
        else:
            notices_mod.write("advisory", text, outbox_dir=outbox_dir, lifetime="run")
    return updated


# ── the pass ─────────────────────────────────────────────────────────────


def _read_jsonl(path: Path | None, cap: int = 2 * 1024 * 1024) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        with path.open("rb") as handle:
            raw = handle.read(cap)
    except OSError:
        return []
    rows = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


_FORGE_CACHE: dict[str, tuple[int, list[dict[str, Any]]]] = {}


def _forge_prs(repo_root: Path | None) -> list[dict[str, Any]]:
    """The forge PR cache's rows, re-read only when its mtime moves."""
    if repo_root is None:
        return []
    from . import forge_pr_cache

    path = forge_pr_cache.cache_path(Path(repo_root))
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return []
    cached = _FORGE_CACHE.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = forge_pr_cache.load(Path(repo_root)) or {}
    rows = [r for r in (data.get("prs") or []) if isinstance(r, dict)]
    _FORGE_CACHE[str(path)] = (mtime, rows)
    return rows


def _write_card(path: Path, text: str) -> bool:
    tmp = path.with_name(".card.frame.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def _cap_dict(store: dict, limit: int) -> dict:
    if len(store) <= limit:
        return store
    return dict(sorted(store.items(), key=lambda kv: kv[1] or 0)[-limit:])


@dataclass
class PassResult:
    ticks: list[Tick] = field(default_factory=list)
    delta: dict[str, Any] | None = None
    triggers: list[str] = field(default_factory=list)
    #: ``True`` when this pass wrote the ``## Ledger`` block to the card.
    ledger_written: bool = False


def frame_pass(
    meta: dict[str, Any],
    *,
    outbox_dir: Path | None,
    run_dir: Path | None = None,
    repo_root: Path | None = None,
    stats: Mapping[str, Any] | None = None,
    events: Iterable[Mapping[str, Any]] = (),
    notices: Iterable[Mapping[str, Any]] | None = None,
    now: float | None = None,
    notice: Callable[[str, str], None] | None = None,
    run_id: str = "",
    is_strand: bool = False,
    hud: Mapping[str, Any] | None = None,
    heddles: Iterable[Mapping[str, Any]] | None = None,
) -> PassResult:
    """One heartbeat of the frame's hand on the card. Never raises.

    *meta* is the run's ``task.meta`` (state persists under ``card_frame``);
    *notice* is ``(kind, text) -> None``. A strand's card is its own and its
    delta has no reader, so a strand gets the ticks and the ledger, and no
    delta item.

    *hud* is the portal payload the ledger reads (produce, spend, live
    strands); ``None`` reads ``portal-state.json`` as last written — on the
    heartbeat that is one pass old, because the portal is written right after
    this pass. *heddles* is the lit list from the same heartbeat; ``None``
    falls back to the HUD's.
    """
    result = PassResult()
    if outbox_dir is None:
        return result
    try:
        return _frame_pass(
            meta, result, outbox_dir=Path(outbox_dir), run_dir=run_dir,
            repo_root=repo_root, stats=stats, events=list(events or ()),
            notices=notices, now=now, notice=notice, run_id=run_id, is_strand=is_strand,
            hud=hud, heddles=None if heddles is None else list(heddles),
        )
    except Exception:  # noqa: BLE001 - a courtesy must never sink a heartbeat
        return result


def _frame_pass(meta, result, *, outbox_dir, run_dir, repo_root, stats, events, notices,
                now, notice, run_id, is_strand, hud, heddles) -> PassResult:
    from .outbox import notices as notices_mod

    now = float(now) if now is not None else _dt.datetime.now(tz=_dt.timezone.utc).timestamp()
    state = meta.get(META_KEY) if isinstance(meta.get(META_KEY), dict) else {}
    state = dict(state)
    card_path = outbox_dir / CARD_NAME
    try:
        card_text = card_path.read_text(encoding="utf-8", errors="replace")
        card_mtime = card_path.stat().st_mtime
    except OSError:
        card_text, card_mtime = "", None

    produce_rows = _read_jsonl(Path(run_dir) / "produce.jsonl") if run_dir else []
    relics = _read_jsonl(outbox_dir / ".relics.jsonl")
    forge_rows = _forge_prs(repo_root)
    facts = gather_facts(events=events, produce_rows=produce_rows, relics=relics, forge_prs=forge_rows)

    # ── first sight of each open line ──
    seen_lines: dict[str, float] = dict(state.get("seen_lines") or {})
    for line in card_text.split("\n"):
        if _OPEN_RE.match(line):
            seen_lines.setdefault(_line_key(line), now)
    frame_ticked: list[str] = list(state.get("ticked") or [])

    # ── the ticks ──
    ticks = propose_ticks(card_text, facts, seen_lines)
    if ticks:
        updated = apply_ticks(card_text, ticks)
        if updated != card_text and _write_card(card_path, updated):
            card_text = updated
            result.ticks = ticks
            for tick in ticks:
                frame_ticked.append(_line_key(tick.line))
            text = "card_ticks: " + " · ".join(
                f"ticked “{_line_key(t.line)[:80]}” — {t.why}" for t in ticks
            )
            if notice is not None:
                notice("advisory", text)
            else:
                notices_mod.write("advisory", text, outbox_dir=outbox_dir, lifetime="run")
    state["ticked"] = frame_ticked[-_SEEN_LINES_MAX:]
    state["seen_lines"] = _cap_dict(seen_lines, _SEEN_LINES_MAX)

    # ── the weaver's last write ──
    digest = intent_hash(card_text, frame_ticked)
    delivered = delivered_total(stats)
    if notices is None:
        notices = _read_jsonl(outbox_dir / notices_mod.NOTICES_FILE)
    counted = _counted_notices(notices)
    baseline = "intent" not in state
    if baseline:
        # First pass: the card as it stands is the baseline, never "a write".
        state["intent"] = digest
        state["weaver_at"] = card_mtime if card_text.strip() else now
        state["delivered_at_write"] = delivered
        state["refusals_at_write"] = len(counted)
        state["returns_seen"] = list(facts.return_labels)[-_SEEN_IDS_MAX:]
        state["merges_seen"] = sorted(facts.merged_prs)[-_SEEN_IDS_MAX:]
        state["delivered_seen"] = delivered
        state["refusals_seen"] = len(counted)
    elif digest != state.get("intent"):
        state["intent"] = digest
        state["weaver_at"] = now
        state["delivered_at_write"] = delivered
        state["refusals_at_write"] = len(counted)
        # an edit folds the standing delta in
        if isinstance(state.get("delta"), dict):
            state["delta"] = None
    weaver_at = float(state.get("weaver_at") or 0.0)

    # ── the ledger half (§19.3) ──
    card_text = _project_ledger(
        state, result, card_path=card_path, card_text=card_text, outbox_dir=outbox_dir,
        hud=hud, heddles=heddles, produce_rows=produce_rows, relics=relics,
        events=events, since=weaver_at, notice=notice, notices_mod=notices_mod,
        merged=ledger_merges(facts, card_text=card_text, produce_rows=produce_rows,
                             relics=relics, meta=meta, run_id=run_id),
    )
    if baseline:
        meta[META_KEY] = state
        return result

    # ── the moments ──
    returns_seen = set(state.get("returns_seen") or [])
    merges_seen = set(state.get("merges_seen") or [])
    new_returns = [k for k in facts.return_labels if k not in returns_seen]
    new_merges = [
        n for n in facts.merged_prs
        if n not in merges_seen and _merge_is_ours(n, facts, card_text, produce_rows, relics, meta)
    ]
    triggers: list[str] = []
    if new_returns:
        triggers.append("strand_returned")
    if new_merges:
        triggers.append("pr_merged")
    if delivered > int(state.get("delivered_seen") or 0):
        triggers.append("delivery")
    if len(counted) > int(state.get("refusals_seen") or 0):
        triggers.append("refusal")
    state["returns_seen"] = sorted(returns_seen | set(facts.return_labels))[-_SEEN_IDS_MAX:]
    state["merges_seen"] = sorted(merges_seen | set(facts.merged_prs))[-_SEEN_IDS_MAX:]
    state["delivered_seen"] = delivered
    state["refusals_seen"] = len(counted)
    result.triggers = triggers

    # A strand returning or a PR merging is still a moment (the ticks and the
    # ledger act on it), but it is no longer the delta's to say (§19.3).
    delta_moments = [t for t in triggers if t in ("delivery", "refusal")]
    if delta_moments and not is_strand:
        text = draft_delta(
            delivered=max(0, delivered - int(state.get("delivered_at_write") or 0)),
            refusals=max(0, len(counted) - int(state.get("refusals_at_write") or 0)),
        )
        if text:
            current = state.get("delta") if isinstance(state.get("delta"), dict) else None
            if current is None:
                seq = int(state.get("delta_seq") or 0) + 1
                state["delta_seq"] = seq
                suffix = (run_id or "run")[-4:]
                current = {"id": f"{DELTA_ID_PREFIX}{suffix}-{seq}"}
            current = {**current, "text": text, "at": _iso(now), "trigger": delta_moments[-1]}
            state["delta"] = current
    result.delta = state.get("delta") if isinstance(state.get("delta"), dict) else None
    meta[META_KEY] = state
    return result


def _merge_is_ours(number, facts, card_text, produce_rows, relics, meta) -> bool:
    """A merge the card is about: this run landed it, its relics name it, the
    card names it, or it is the run's own PR/branch. The forge cache carries
    every PR in the repo; a stranger's merge is not this card's delta."""
    for row in produce_rows:
        if str(row.get("pr") or "").lstrip("#") == str(number):
            return True
    for record in relics:
        if str(record.get("pr") or record.get("number") or "").lstrip("#") == str(number):
            return True
    if re.search(rf"(?<![\w&])#{number}\b", card_text or ""):
        return True
    if str(meta.get("github_pr_number") or "") == str(number):
        return True
    branch = str(meta.get("branch_name") or "")
    head = facts.pr_branch.get(number, "")
    if head and (head == branch or re.search(
        rf"(?<![\w/]){re.escape(head)}(?![\w/.-])", card_text or "",
    )):
        return True
    return False


def clear_delta(meta: dict[str, Any], delta_id: str) -> bool:
    """``note: <id>`` on the standing delta item: accept it. ``True`` if cleared."""
    state = meta.get(META_KEY)
    if not isinstance(state, dict):
        return False
    current = state.get("delta")
    if not isinstance(current, dict) or str(current.get("id") or "") != str(delta_id or "").strip():
        return False
    state = dict(state)
    state["delta"] = None
    meta[META_KEY] = state
    return True


def portal_delta(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    state = meta.get(META_KEY) if isinstance(meta, Mapping) else None
    if not isinstance(state, dict):
        return None
    current = state.get("delta")
    return dict(current) if isinstance(current, dict) else None
