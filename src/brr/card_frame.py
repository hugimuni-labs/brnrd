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
compared with its ``## Said`` projection removed and the frame's ticks
un-ticked, so neither a said-row nor a tick reads as the weaver catching up.
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


def _strip_said(text: str) -> str:
    start = text.find(_SAID_HEADING)
    if start == -1 or (start > 0 and text[start - 1] != "\n"):
        return text
    nxt = text.find("\n## ", start + len(_SAID_HEADING))
    return text[:start] + (text[nxt + 1:] if nxt != -1 else "")


def intent_hash(card_text: str, frame_ticked: Iterable[str] = ()) -> str:
    """A digest of the weaver's half: no ``## Said``, the frame's ticks undone."""
    ticked = set(frame_ticked)
    lines = []
    for line in _strip_said(card_text or "").split("\n"):
        if _DONE_RE.match(line) and _line_key(_DONE_RE.sub(r"\1[ ]\2", line)) in ticked:
            line = _DONE_RE.sub(r"\1[ ]\2", line)
        lines.append(line.rstrip())
    return hashlib.sha1("\n".join(lines).strip().encode("utf-8")).hexdigest()


# ── the delta ────────────────────────────────────────────────────────────


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def draft_delta(
    *,
    returns: list[str],
    merges: list[int],
    delivered: int,
    refusals: int,
    commits: int = 0,
) -> str | None:
    """*since your last card write: …* — one line, or ``None`` when nothing moved."""
    parts: list[str] = []
    if returns:
        shown = ", ".join(returns[:3]) + (" …" if len(returns) > 3 else "")
        parts.append(f"{_plural(len(returns), 'strand', 'strands')} returned ({shown})")
    if merges:
        parts.append(" ".join(f"#{n}" for n in sorted(merges)[:5]) + " merged")
    if commits:
        parts.append(_plural(commits, "commit landed", "commits landed"))
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
) -> PassResult:
    """One heartbeat of the frame's hand on the card. Never raises.

    *meta* is the run's ``task.meta`` (state persists under ``card_frame``);
    *notice* is ``(kind, text) -> None``. A strand's card is its own and its
    delta has no reader, so a strand gets the ticks and no delta item.
    """
    result = PassResult()
    if outbox_dir is None:
        return result
    try:
        return _frame_pass(
            meta, result, outbox_dir=Path(outbox_dir), run_dir=run_dir,
            repo_root=repo_root, stats=stats, events=list(events or ()),
            notices=notices, now=now, notice=notice, run_id=run_id, is_strand=is_strand,
        )
    except Exception:  # noqa: BLE001 - a courtesy must never sink a heartbeat
        return result


def _frame_pass(meta, result, *, outbox_dir, run_dir, repo_root, stats, events, notices,
                now, notice, run_id, is_strand) -> PassResult:
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
    knots = [r for r in produce_rows if r.get("kind") == "knot"]
    if "intent" not in state:
        # First pass: the card as it stands is the baseline, never "a write".
        state["intent"] = digest
        state["weaver_at"] = card_mtime if card_text.strip() else now
        state["delivered_at_write"] = delivered
        state["refusals_at_write"] = len(counted)
        state["knots_at_write"] = len(knots)
        state["returns_seen"] = list(facts.return_labels)[-_SEEN_IDS_MAX:]
        state["merges_seen"] = sorted(facts.merged_prs)[-_SEEN_IDS_MAX:]
        state["delivered_seen"] = delivered
        state["refusals_seen"] = len(counted)
        meta[META_KEY] = state
        return result
    if digest != state.get("intent"):
        state["intent"] = digest
        state["weaver_at"] = now
        state["delivered_at_write"] = delivered
        state["refusals_at_write"] = len(counted)
        state["knots_at_write"] = len(knots)
        # an edit folds the standing delta in
        if isinstance(state.get("delta"), dict):
            state["delta"] = None
    weaver_at = float(state.get("weaver_at") or 0.0)

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

    if triggers and not is_strand:
        returns = [
            k for k, at in facts.return_labels.items()
            if (at >= weaver_at if at is not None else k in new_returns)
        ]
        merges = [
            n for n, at in facts.merged_prs.items()
            if _merge_is_ours(n, facts, card_text, produce_rows, relics, meta)
            and (at >= weaver_at if at is not None else n in new_merges)
        ]
        text = draft_delta(
            returns=returns,
            merges=merges,
            delivered=max(0, delivered - int(state.get("delivered_at_write") or 0)),
            refusals=max(0, len(counted) - int(state.get("refusals_at_write") or 0)),
            commits=max(0, len(knots) - int(state.get("knots_at_write") or 0)),
        )
        if text:
            current = state.get("delta") if isinstance(state.get("delta"), dict) else None
            if current is None:
                seq = int(state.get("delta_seq") or 0) + 1
                state["delta_seq"] = seq
                suffix = (run_id or "run")[-4:]
                current = {"id": f"{DELTA_ID_PREFIX}{suffix}-{seq}"}
            current = {**current, "text": text, "at": _iso(now), "trigger": triggers[-1]}
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
