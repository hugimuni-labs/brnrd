#!/usr/bin/env python3
"""The cost of a week: did the price of brnrd's work change?

A read-only analysis of files already on the maintainer's machine. It writes
nothing except its own output (markdown on stdout, optional PNG charts), and
it changes nothing the daemon owns. Written for
``docs/analysis/the-cost-of-a-week.md``; that page explains the method and
where the data is thin.

## Sources (the ground truth, in the order they are trusted)

- **Claude transcripts**: ``~/.claude/projects/**/*.jsonl``. Every assistant
  row carries ``message.usage`` and a timestamp. One API message is written as
  several rows, so rows are deduplicated on ``(message.id, requestId)``, keeping
  the largest value of each usage field. With that dedup the per-run totals
  match ``run-ledger.jsonl`` to the token.
- **Codex rollouts**: ``~/.codex/sessions/**/rollout-*.jsonl``. ``token_count``
  events carry a cumulative ``total_token_usage`` per session, plus
  ``rate_limits`` (the Codex 5h and weekly gauges). This is the control
  series: a limit change on one vendor should not move the other.
- **Claude gauge history**: ``claude-usage-levels.json`` in every run dir, plus
  the Claude rows of ``.brr/usage-samples.jsonl``, which only keeps 10 hours.
  Integer ``week_used_percentage`` / ``session_used_percentage`` /
  ``week_models.Fable.used_percentage``, each with its ``*_resets_at``.
- **Run metadata**: the ``state.md`` front matter in every run dir. Gives
  ``source``, ``started_at`` and ``ended_at``. A ``source: spawn`` run is a
  strand; anything else is resident.
- **Merges**: ``git log origin/main --first-parent``. A PR counts once, when a
  first-parent commit's subject is ``Merge pull request #N`` or ends in
  ``(#N)`` (a squash merge is single-parent).

## Run it

    python3 scripts/cost_of_a_week.py --as-of 2026-09-14T10:30:00Z
    python3 scripts/cost_of_a_week.py --as-of 2026-09-14T10:30:00Z --charts media/cost

Anything timestamped after ``--as-of`` is ignored, so a re-run with the same
``--as-of`` prints the same tables, even while the runs keep writing. Charts
need matplotlib (``python3 -m venv /tmp/v && /tmp/v/bin/pip install
matplotlib``). The tables do not need it.
"""

from __future__ import annotations

import argparse
import bisect
import glob
import json
import math
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
ACCOUNT = HOME / ".local/state/brnrd/accounts/acc_bdda426da378d4f0c3cad2eb/home"
RUN_ROOTS = [
    ACCOUNT / "runs/hugimuni-labs__brnrd",
    Path("/Users/gurio/Source/Projects/brnrd/.brr/runs"),
]
USAGE_SAMPLES = Path("/Users/gurio/Source/Projects/brnrd/.brr/usage-samples.jsonl")
CLAUDE_PROJECTS = HOME / ".claude/projects"
CODEX_SESSIONS = HOME / ".codex/sessions"
BRNRD_CHECKOUT = "/Users/gurio/Source/Projects/brnrd"
THIS_REPO = str(Path(__file__).resolve().parents[1])

RUN_ID_RE = re.compile(r"run-\d{6}-\d{4}-[a-z0-9]{4}")
PR_RE = re.compile(r"Merge pull request #(\d+)|\(#(\d+)\)\s*$")
USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
# The weekly window is observed at 1 % resolution; a "reading" whose resets_at
# moves by less than this is the same window, jittered by the PTY scrape.
SAME_WINDOW_SLACK_S = 2 * 3600


def parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso_week(t: datetime) -> str:
    y, w, _ = t.isocalendar()
    return f"{y}-W{w:02d}"


def week_start(t: datetime) -> datetime:
    d = t.date() - timedelta(days=t.isocalendar()[2] - 1)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def model_family(model: str | None) -> str | None:
    m = (model or "").lower()
    for fam in ("fable", "opus", "sonnet", "haiku"):
        if fam in m:
            return fam
    return None


# --------------------------------------------------------------------------
# run metadata


@dataclass
class RunMeta:
    run_id: str
    source: str
    started: datetime | None
    ended: datetime | None
    status: str
    runner: str

    @property
    def strand(self) -> bool:
        return self.source == "spawn"


def load_runs() -> dict[str, RunMeta]:
    runs: dict[str, RunMeta] = {}
    for root in RUN_ROOTS:
        for state in sorted(root.glob("run-*/state.md")):
            fm: dict[str, str] = {}
            with state.open(errors="replace") as fh:
                if fh.readline().strip() != "---":
                    continue
                for line in fh:
                    if line.strip() == "---":
                        break
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()
            rid = fm.get("run_id") or state.parent.name
            if rid in runs:  # the account copy wins over the legacy one
                continue
            runs[rid] = RunMeta(
                run_id=rid,
                source=fm.get("source", ""),
                started=parse_ts(fm.get("started_at")),
                ended=parse_ts(fm.get("ended_at")),
                status=fm.get("status", ""),
                runner=fm.get("runner_name", ""),
            )
    return runs


def seat_intervals(runs: dict[str, RunMeta], as_of: datetime):
    """Resident runs as (start, end) intervals, merged where they overlap."""
    raw, open_live, dropped = [], [], []
    for r in runs.values():
        if r.strand or r.started is None or r.started > as_of:
            continue
        end = r.ended
        if end is None:
            if r.status in ("running", "held", "pending"):
                end = as_of
                open_live.append(r.run_id)
            else:
                dropped.append(r.run_id)
                continue
        raw.append((r.started, min(end, as_of)))
    raw.sort()
    merged: list[list[datetime]] = []
    for s, e in raw:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged, open_live, dropped


def hours_in(intervals, lo: datetime, hi: datetime) -> float:
    total = 0.0
    for s, e in intervals:
        a, b = max(s, lo), min(e, hi)
        if b > a:
            total += (b - a).total_seconds()
    return total / 3600


# --------------------------------------------------------------------------
# tokens


@dataclass
class TokenEvent:
    t: datetime
    shell: str  # claude | codex
    family: str | None  # fable/opus/sonnet/haiku for claude; None for codex
    who: str  # resident | strand | interactive | other-repo
    fresh: int  # input + cache creation + output (claude); uncached input + output (codex)
    cache_read: int

    @property
    def total(self) -> int:
        return self.fresh + self.cache_read


def classify(path_or_cwd: str, runs: dict[str, RunMeta], entrypoint: str | None) -> str:
    m = RUN_ID_RE.search(path_or_cwd)
    if m and m.group(0) in runs:
        return "strand" if runs[m.group(0)].strand else "resident"
    brnrd = "Projects-brnrd" in path_or_cwd or BRNRD_CHECKOUT in path_or_cwd
    brnrd = brnrd or "acc-bdda426da378d4f0c3cad2eb" in path_or_cwd or str(ACCOUNT) in path_or_cwd
    if not brnrd:
        return "other-repo"
    if m:  # a brnrd worktree whose run dir is gone: attribute by daemon entrypoint
        return "strand-or-resident?"
    return "resident" if entrypoint == "sdk-cli" else "interactive"


def load_claude_tokens(runs, as_of: datetime) -> list[TokenEvent]:
    best: dict[tuple, dict] = {}
    for f in glob.glob(str(CLAUDE_PROJECTS / "**" / "*.jsonl"), recursive=True):
        rel = os.path.relpath(f, CLAUDE_PROJECTS)
        with open(f, errors="replace") as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                msg = row.get("message")
                if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                    continue
                fam = model_family(msg.get("model"))
                t = parse_ts(row.get("timestamp"))
                if t is None or t > as_of:
                    continue
                key = (msg.get("id"), row.get("requestId"))
                u = msg["usage"]
                cur = best.get(key)
                if cur is None:
                    cur = best[key] = {
                        "t": t,
                        "fam": fam,
                        "who": classify(rel.split(os.sep)[0], runs, row.get("entrypoint")),
                        **{k: 0 for k in USAGE_KEYS},
                    }
                cur["fam"] = cur["fam"] or fam
                cur["t"] = min(cur["t"], t)
                for k in USAGE_KEYS:
                    v = u.get(k)
                    if isinstance(v, int) and v > cur[k]:
                        cur[k] = v
    out = []
    for c in best.values():
        fresh = c["input_tokens"] + c["cache_creation_input_tokens"] + c["output_tokens"]
        if c["fam"] is None or fresh + c["cache_read_input_tokens"] == 0:
            continue  # '<synthetic>' rows and empty stubs
        out.append(TokenEvent(c["t"], "claude", c["fam"], c["who"], fresh, c["cache_read_input_tokens"]))
    out.sort(key=lambda e: e.t)
    return out


@dataclass
class GaugeReading:
    t: datetime
    used: float
    resets_at: float | None


def load_codex(runs, as_of: datetime):
    """Codex token events plus its weekly gauge readings, from rollouts."""
    events: list[TokenEvent] = []
    weekly: list[GaugeReading] = []
    for f in sorted(glob.glob(str(CODEX_SESSIONS / "**" / "rollout-*.jsonl"), recursive=True)):
        who, prev_in, prev_cached, prev_out = "other-repo", 0, 0, 0
        with open(f, errors="replace") as fh:
            for line in fh:
                if '"session_meta"' in line or '"turn_context"' in line:
                    try:
                        p = json.loads(line).get("payload") or {}
                    except ValueError:
                        continue
                    if p.get("cwd"):
                        who = classify(p["cwd"], runs, "sdk-cli")
                    continue
                if '"token_count"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                p = row.get("payload") or {}
                t = parse_ts(row.get("timestamp"))
                if t is None or t > as_of:
                    continue
                rl = p.get("rate_limits") or {}
                for slot in ("primary", "secondary"):
                    w = rl.get(slot) or {}
                    if w.get("window_minutes") == 10080 and w.get("used_percent") is not None:
                        weekly.append(GaugeReading(t, float(w["used_percent"]), w.get("resets_at")))
                tot = ((p.get("info") or {}).get("total_token_usage")) or {}
                inp, cached, outp = tot.get("input_tokens"), tot.get("cached_input_tokens"), tot.get("output_tokens")
                if not all(isinstance(x, int) for x in (inp, cached, outp)):
                    continue
                d_in, d_c, d_o = inp - prev_in, cached - prev_cached, outp - prev_out
                prev_in, prev_cached, prev_out = inp, cached, outp
                if min(d_in, d_c, d_o) < 0 or d_in + d_o == 0:
                    continue
                events.append(TokenEvent(t, "codex", None, who, (d_in - d_c) + d_o, d_c))
    events.sort(key=lambda e: e.t)
    weekly.sort(key=lambda g: g.t)
    return events, weekly


# --------------------------------------------------------------------------
# gauges


def load_claude_gauge(as_of: datetime) -> dict[str, list[GaugeReading]]:
    series: dict[str, list[GaugeReading]] = {"week": [], "session": [], "fable": []}
    seen = set()
    for root in RUN_ROOTS:
        for f in root.glob("run-*/claude-usage-levels.json"):
            try:
                j = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            t = parse_ts(j.get("updated_at"))
            if t is None or t > as_of:
                continue
            fable = (j.get("week_models") or {}).get("Fable") or {}
            for name, used, resets in (
                ("week", j.get("week_used_percentage"), j.get("week_resets_at")),
                ("session", j.get("session_used_percentage"), j.get("session_resets_at")),
                ("fable", fable.get("used_percentage"), fable.get("resets_at")),
            ):
                if used is None or (name, t) in seen:
                    continue
                seen.add((name, t))
                series[name].append(GaugeReading(t, float(used), resets))
    if USAGE_SAMPLES.exists():
        for line in USAGE_SAMPLES.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("shell") != "claude":
                continue
            t = parse_ts(r.get("at"))
            name = {10080.0: "week", 300.0: "session"}.get(r.get("window_minutes"))
            if t is None or t > as_of or name is None or (name, t) in seen:
                continue
            seen.add((name, t))
            series[name].append(GaugeReading(t, float(r["used_percent"]), r.get("resets_at")))
    for v in series.values():
        v.sort(key=lambda g: g.t)
    return series


@dataclass
class Interval:
    lo: datetime
    hi: datetime
    delta: float  # percentage points consumed in (lo, hi]
    clean: bool  # same window on both ends, no drop: safe for a ratio
    kind: str  # same | rollover | drop | unknown-window


def gauge_intervals(readings: list[GaugeReading]) -> list[Interval]:
    """Consecutive readings to consumed points, reset-aware.

    - same window (resets_at within slack, both known), used rose or held:
      delta = cur - prev. **clean**.
    - the window rolled (prev's reset instant passed): delta = cur, i.e. what
      the new window has used since the reset. The old window's tail after
      prev is lost, so it undercounts. Not clean.
    - used fell inside what claims to be the same window: a mid-window reset
      (a grant). delta = cur. Not clean.
    - a resets_at missing: rose ⇒ cur - prev, fell ⇒ cur. Not clean.
    """
    out = []
    for prev, cur in zip(readings, readings[1:]):
        if cur.t <= prev.t:
            continue
        if prev.resets_at and cur.resets_at:
            rolled = cur.t.timestamp() >= prev.resets_at - 60 or abs(cur.resets_at - prev.resets_at) > SAME_WINDOW_SLACK_S
            if rolled:
                out.append(Interval(prev.t, cur.t, cur.used, False, "rollover"))
            elif cur.used >= prev.used:
                out.append(Interval(prev.t, cur.t, cur.used - prev.used, True, "same"))
            else:
                out.append(Interval(prev.t, cur.t, cur.used, False, "drop"))
        else:
            d = cur.used - prev.used if cur.used >= prev.used else cur.used
            out.append(Interval(prev.t, cur.t, d, False, "unknown-window"))
    return out


# --------------------------------------------------------------------------
# merges


def load_merges(since: datetime, as_of: datetime, repo: str) -> list[tuple[datetime, int]]:
    fmt = "%cI%x09%s"
    res = subprocess.run(
        ["git", "-C", repo, "log", "origin/main", "--first-parent", f"--format={fmt}",
         f"--since={since.isoformat()}"],
        check=True, capture_output=True, text=True,
    )
    out = []
    for line in res.stdout.splitlines():
        ts, _, subject = line.partition("\t")
        t = parse_ts(ts)
        m = PR_RE.search(subject)
        if t and m and since <= t <= as_of:
            out.append((t, int(m.group(1) or m.group(2))))
    return sorted(out)


# --------------------------------------------------------------------------
# aggregation helpers


class Timeline:
    """Token events indexed by time, for cheap (lo, hi] sums."""

    def __init__(self, events: list[TokenEvent]):
        self.events = events
        self.ts = [e.t for e in events]

    def window(self, lo: datetime, hi: datetime):
        i = bisect.bisect_right(self.ts, lo)
        j = bisect.bisect_right(self.ts, hi)
        return self.events[i:j]


def fmt_int(n: float) -> str:
    return f"{n:,.0f}"


def fmt_m(n: float) -> str:
    return f"{n / 1e6:,.1f}M"


def fmt_f(x: float | None, nd=2) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{nd}f}"


def table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def mann_whitney_p(a: list[float], b: list[float]) -> float | None:
    """Two-sided Mann-Whitney U, normal approximation with tie-averaged ranks."""
    n1, n2 = len(a), len(b)
    if n1 < 3 or n2 < 3:
        return None
    pooled = sorted([(x, 0) for x in a] + [(x, 1) for x in b])
    ranks = [0.0] * len(pooled)
    i = 0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, pooled) if g == 0)
    u = r1 - n1 * (n1 + 1) / 2
    mu, sigma = n1 * n2 / 2, math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    if sigma == 0:
        return None
    z = (u - mu) / sigma
    return math.erfc(abs(z) / math.sqrt(2))


def median(xs):
    s = sorted(xs)
    n = len(s)
    return None if n == 0 else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


# --------------------------------------------------------------------------
# the analysis


@dataclass
class DayRatio:
    day: str
    points: float = 0.0
    tokens: int = 0
    fresh: int = 0
    intervals: int = 0
    fam: dict = field(default_factory=lambda: defaultdict(int))


def daily_ratios(intervals: list[Interval], tl: Timeline, lo: datetime, pred=lambda e: True):
    """Clean gauge intervals, bucketed by the UTC day their end falls in."""
    days: dict[str, DayRatio] = {}
    for iv in intervals:
        if not iv.clean or iv.hi < lo or (iv.hi - iv.lo) > timedelta(hours=12):
            continue
        d = days.setdefault(iv.hi.strftime("%Y-%m-%d"), DayRatio(iv.hi.strftime("%Y-%m-%d")))
        d.points += iv.delta
        d.intervals += 1
        for e in tl.window(iv.lo, iv.hi):
            if pred(e):
                d.tokens += e.total
                d.fresh += e.fresh
                if e.family:
                    d.fam[e.family] += e.fresh
    return [days[k] for k in sorted(days)]


def step_scan(days: list[DayRatio], min_side=4, min_points=3.0, use="fresh"):
    """Every split date with >= min_side usable days per side, ranked by |log ratio|."""
    usable = [d for d in days if getattr(d, use) > 0 and d.points >= 0 and (d.points >= min_points)]
    per = lambda d: d.points / (getattr(d, use) / 1e6)
    results = []
    for k in range(min_side, len(usable) - min_side + 1):
        before, after = usable[:k], usable[k:]
        pb = sum(d.points for d in before) / (sum(getattr(d, use) for d in before) / 1e6)
        pa = sum(d.points for d in after) / (sum(getattr(d, use) for d in after) / 1e6)
        rb, ra = [per(d) for d in before], [per(d) for d in after]
        results.append({
            "split": after[0].day,
            "n_before": len(before),
            "n_after": len(after),
            "samples_before": sum(d.intervals for d in before),
            "samples_after": sum(d.intervals for d in after),
            "pooled_before": pb,
            "pooled_after": pa,
            "ratio": pa / pb if pb else None,
            "median_before": median(rb),
            "median_after": median(ra),
            "p": mann_whitney_p(rb, ra),
        })
    return usable, results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--as-of", required=True, help="UTC ISO instant; later data is ignored")
    ap.add_argument("--weeks", type=int, default=4, help="complete ISO weeks before the as-of week")
    ap.add_argument("--repo", default=THIS_REPO, help="checkout whose origin/main is read (fetch it first)")
    ap.add_argument("--charts", help="directory to write PNG charts into (needs matplotlib)")
    args = ap.parse_args()

    as_of = parse_ts(args.as_of)
    this_week = week_start(as_of)
    weeks = [this_week - timedelta(weeks=n) for n in range(args.weeks, -1, -1)]
    lo = weeks[0]

    runs = load_runs()
    claude = load_claude_tokens(runs, as_of)
    codex, codex_weekly = load_codex(runs, as_of)
    gauge = load_claude_gauge(as_of)
    merges = load_merges(lo, as_of, args.repo)
    seats, open_live, dropped = seat_intervals(runs, as_of)

    week_iv = gauge_intervals(gauge["week"])
    fable_iv = gauge_intervals(gauge["fable"])
    session_iv = gauge_intervals(gauge["session"])
    codex_iv = gauge_intervals(codex_weekly)
    ctl = Timeline(claude)
    xtl = Timeline(codex)

    p = print
    p(f"# cost_of_a_week — as of {as_of.strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
    p("## Sources read\n")
    p(table(["source", "rows", "span"], [
        ["run dirs (state.md)", fmt_int(len(runs)), ""],
        ["claude token messages (deduped)", fmt_int(len(claude)),
         f"{claude[0].t:%Y-%m-%d} → {claude[-1].t:%Y-%m-%d %H:%M}" if claude else ""],
        ["codex token events", fmt_int(len(codex)),
         f"{codex[0].t:%Y-%m-%d} → {codex[-1].t:%Y-%m-%d %H:%M}" if codex else ""],
        ["claude weekly gauge readings", fmt_int(len(gauge["week"])),
         f"{gauge['week'][0].t:%Y-%m-%d} → {gauge['week'][-1].t:%Y-%m-%d %H:%M}"],
        ["claude Fable gauge readings", fmt_int(len(gauge["fable"])), ""],
        ["codex weekly gauge readings", fmt_int(len(codex_weekly)), ""],
        ["PR merges on origin/main (window)", fmt_int(len(merges)), ""],
        ["resident runs still open at as-of", str(len(open_live)), ", ".join(open_live)],
        ["resident runs with no end, not live (excluded)", str(len(dropped)), ""],
    ]))

    # --- per week -------------------------------------------------------
    p("\n## Per ISO week (UTC)\n")
    rows, chart_week = [], []
    for ws in weeks:
        we = min(ws + timedelta(weeks=1), as_of)
        label = iso_week(ws) + (" (partial)" if we < ws + timedelta(weeks=1) else "")
        hrs = hours_in(seats, ws, we)
        prs = sum(1 for t, _ in merges if ws <= t < we)
        tok = defaultdict(int)
        for e in ctl.window(ws, we) + xtl.window(ws, we):
            if e.who == "other-repo":
                continue
            bucket = "strand" if e.who.startswith("strand") else "resident"
            if e.who == "interactive":
                bucket = "interactive"
            tok[(e.shell, bucket)] += e.total
            tok[(e.shell, bucket, "fresh")] += e.fresh
        share = sum(iv.delta for iv in week_iv if ws <= iv.hi < we)
        cshare = sum(iv.delta for iv in codex_iv if ws <= iv.hi < we)
        all_tok = sum(v for k, v in tok.items() if len(k) == 2)
        all_fresh = sum(v for k, v in tok.items() if len(k) == 3)
        rows.append([
            label, fmt_f(hrs, 1), str(prs),
            fmt_m(tok[("claude", "resident")] + tok[("codex", "resident")]),
            fmt_m(tok[("claude", "strand")] + tok[("codex", "strand")]),
            fmt_m(tok[("claude", "interactive")]),
            fmt_f(share, 0), fmt_f(cshare, 0),
            fmt_m(all_tok / prs) if prs else "—",
            fmt_m(all_fresh / prs) if prs else "—",
            fmt_f(share / hrs if hrs else None, 2),
        ])
        chart_week.append((iso_week(ws), all_tok / prs if prs else None, all_fresh / prs if prs else None))
    p(table(["week", "seat-hours", "PRs merged", "tokens resident", "tokens strands", "tokens interactive",
             "claude weekly pts", "codex weekly pts", "tokens / PR", "fresh tokens / PR",
             "claude pts / seat-hour"], rows))

    p("\n### Tokens by shell and who, per week (total · fresh)\n")
    rows = []
    for ws in weeks:
        we = min(ws + timedelta(weeks=1), as_of)
        agg = defaultdict(lambda: [0, 0])
        for e in ctl.window(ws, we) + xtl.window(ws, we):
            k = f"{e.shell}:{e.who}"
            agg[k][0] += e.total
            agg[k][1] += e.fresh
        rows.append([iso_week(ws), " · ".join(f"{k} {fmt_m(v[0])}/{fmt_m(v[1])}" for k, v in sorted(agg.items()))])
    p(table(["week", "shell:who total/fresh"], rows))

    p("\n### Claude fresh tokens by model family, per week\n")
    rows = []
    for ws in weeks:
        we = min(ws + timedelta(weeks=1), as_of)
        fam = defaultdict(int)
        for e in ctl.window(ws, we):
            fam[e.family] += e.fresh
        tot = sum(fam.values()) or 1
        rows.append([iso_week(ws)] + [f"{fmt_m(fam[f])} ({100 * fam[f] / tot:.0f}%)" for f in ("fable", "opus", "sonnet", "haiku")])
    p(table(["week", "fable", "opus", "sonnet", "haiku"], rows))

    p("\n### Gauge interval kinds (how much of the share column is exact)\n")
    rows = []
    for name, ivs in (("claude week", week_iv), ("claude fable", fable_iv), ("codex week", codex_iv)):
        kinds = defaultdict(lambda: [0, 0.0])
        for iv in ivs:
            if iv.hi >= lo:
                kinds[iv.kind][0] += 1
                kinds[iv.kind][1] += iv.delta
        rows.append([name] + [f"{kinds[k][0]} ({fmt_f(kinds[k][1], 0)} pts)" for k in ("same", "rollover", "drop", "unknown-window")])
    p(table(["series", "same window", "rollover", "drop (mid-window reset)", "reset unknown"], rows))
    drops = [iv for iv in week_iv if iv.kind == "drop" and iv.hi >= lo]
    if drops:
        p("\nmid-window drops in the claude weekly gauge: " + ", ".join(f"{iv.lo:%m-%d %H:%M}→{iv.hi:%m-%d %H:%M}Z" for iv in drops))

    # --- the step ---------------------------------------------------------
    p("\n## Share per token by day (clean intervals only)\n")
    p("points = weekly-gauge percentage points consumed; per M = per million tokens. "
      "Only same-window, non-dropping gauge intervals of <= 12 h; tokens are every local "
      "transcript in those intervals (all repos, since the gauge is account-wide).\n")
    series_specs = [
        ("claude weekly", week_iv, ctl, lambda e: True),
        ("claude Fable weekly", fable_iv, ctl, lambda e: e.family == "fable"),
        ("codex weekly (control)", codex_iv, xtl, lambda e: True),
    ]
    chart_days = {}
    for name, ivs, tl, pred in series_specs:
        days = daily_ratios(ivs, tl, lo, pred)
        chart_days[name] = days
        p(f"\n### {name}\n")
        p(table(["day", "intervals", "points", "fresh tokens", "total tokens", "pts / M fresh", "pts / 100M total"],
                [[d.day, str(d.intervals), fmt_f(d.points, 0), fmt_m(d.fresh), fmt_m(d.tokens),
                  fmt_f(d.points / (d.fresh / 1e6) if d.fresh else None, 2),
                  fmt_f(d.points / (d.tokens / 1e8) if d.tokens else None, 2)] for d in days]))
        for use in ("fresh", "tokens"):
            usable, results = step_scan(days, use=use)
            p(f"\nstep scan on pts per {'fresh' if use == 'fresh' else 'total'} token — {len(usable)} usable days "
              f"(>= 3 pts, > 0 tokens), splits with >= 4 days per side:\n")
            if not results:
                p("_not enough usable days for a split._")
                continue
            ranked = sorted(results, key=lambda r: -abs(math.log(r["ratio"])) if r["ratio"] else 0)
            p(table(["split (first day after)", "days b/a", "intervals b/a", "pooled before", "pooled after",
                     "after / before", "median before", "median after", "Mann-Whitney p"],
                    [[r["split"], f"{r['n_before']}/{r['n_after']}", f"{r['samples_before']}/{r['samples_after']}",
                      fmt_f(r["pooled_before"], 3), fmt_f(r["pooled_after"], 3), fmt_f(r["ratio"], 2),
                      fmt_f(r["median_before"], 3), fmt_f(r["median_after"], 3), fmt_f(r["p"], 3)]
                     for r in ranked[:5]]))

    p("\n## Share per seat-hour by day (claude weekly)\n")
    day_rows, chart_hours = [], []
    d0 = lo
    while d0 < as_of:
        d1 = min(d0 + timedelta(days=1), as_of)
        hrs = hours_in(seats, d0, d1)
        pts = sum(iv.delta for iv in week_iv if d0 <= iv.hi < d1)
        clean = sum(iv.delta for iv in week_iv if d0 <= iv.hi < d1 and iv.clean)
        day_rows.append([f"{d0:%Y-%m-%d}", fmt_f(hrs, 1), fmt_f(pts, 0), fmt_f(clean, 0),
                         fmt_f(pts / hrs if hrs >= 1 else None, 2)])
        chart_hours.append((d0, hrs, pts))
        d0 = d1
    p(table(["day", "seat-hours", "pts (all)", "pts (clean)", "pts / seat-hour"], day_rows))

    if args.charts:
        write_charts(Path(args.charts), chart_hours, chart_week, chart_days)


def write_charts(outdir: Path, hours, weeks, days) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "svg.hashsalt": "cost", "path.simplify": False})
    ink, accent, muted = "#1f2328", "#2f6fb5", "#9aa4ae"

    # 1. share per seat-hour by day
    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=150)
    xs = [d for d, h, _ in hours if h >= 1]
    ys = [p / h for _, h, p in hours if h >= 1]
    ax.bar(xs, ys, width=0.8, color=accent)
    ax.set_title("Claude weekly-window points per resident seat-hour, by UTC day", loc="left", color=ink)
    ax.set_ylabel("pts / seat-hour")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.grid(axis="y", color="#e5e7eb")
    ax.set_axisbelow(True)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(outdir / "share-per-seat-hour-by-day.png", metadata={"Software": None})
    plt.close(fig)

    # 2. tokens per merged PR by week
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=150)
    labels = [w for w, t, _ in weeks]
    tot = [(t or 0) / 1e6 for _, t, _ in weeks]
    fresh = [(f or 0) / 1e6 for _, _, f in weeks]
    ax.bar(labels, tot, color=muted, label="total (incl. cache reads)")
    ax.bar(labels, fresh, color=accent, label="fresh (input + cache writes + output)")
    for i, (t, f) in enumerate(zip(tot, fresh)):
        ax.text(i, t, f"{t:.1f}M", ha="center", va="bottom", fontsize=9, color=ink)
    ax.set_title("Tokens per merged PR, by ISO week (last week partial)", loc="left", color=ink)
    ax.set_ylabel("million tokens / PR")
    ax.legend(frameon=False, fontsize=9)
    ax.grid(axis="y", color="#e5e7eb")
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(outdir / "tokens-per-merged-pr-by-week.png", metadata={"Software": None})
    plt.close(fig)

    # 3. points per million fresh tokens by day, claude vs codex control
    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=150)
    for name, color in (("claude weekly", accent), ("codex weekly (control)", muted)):
        ds = [d for d in days.get(name, []) if d.fresh > 0 and d.points >= 3]
        ax.plot([datetime.strptime(d.day, "%Y-%m-%d") for d in ds],
                [d.points / (d.fresh / 1e6) for d in ds], marker="o", color=color, label=name)
    ax.set_yscale("log")
    ax.set_title("Weekly-window points per million fresh tokens, by UTC day (log)", loc="left", color=ink)
    ax.set_ylabel("pts / M fresh tokens")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.legend(frameon=False, fontsize=9)
    ax.grid(axis="y", color="#e5e7eb")
    ax.set_axisbelow(True)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(outdir / "share-per-token-by-day.png", metadata={"Software": None})
    plt.close(fig)


if __name__ == "__main__":
    main()
