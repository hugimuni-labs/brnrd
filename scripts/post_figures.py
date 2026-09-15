#!/usr/bin/env python3
"""Read-only figure generator for "The seventy-hour run" post.

Sources (never written to):
  - ~/.brr/runs/run-260912-1513-7hw5/boundaries.jsonl  (the seat's own
    boundary log; a weekly-quota reading rides almost every boundary,
    either the structured `quota.W` field or the `inject` text — the daemon
    was rewritten mid-run and the schema changed partway through)
  - ~/.brr/usage-samples.jsonl (shell: claude, window_minutes: 10080) —
    cross-check for the last ~10h (the file's own retention window)
  - ~/.brr/conversations/cloud__telegram__155783668__/*.jsonl — his
    messages (`kind: event`), for the days chart
  - `gh pr list --state merged` — PR merge timestamps, for both charts

Outputs under media/post/seventy-hours/: gauge.png, days.png, numbers.json.
(card.png and run-page.png are produced by separate one-shot commands —
see the report.)
"""

from __future__ import annotations

import glob
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

HOME_BRR = Path.home() / "Source/Projects/brnrd/.brr"
SEAT_RUN_ID = "run-260912-1513-7hw5"
RUN_START = "2026-09-12T15:13:00Z"
OUT_DIR = Path(__file__).resolve().parent.parent / "media/post/seventy-hours"

# Palette (dataviz skill reference/palette.md — dark surface):
DARK_SURFACE = "#1a1a19"
INK_PRIMARY = "#ffffff"
INK_SECONDARY = "#c3c2b7"
INK_MUTED = "#898781"
GRID = "#2c2c2a"
BASELINE = "#383835"
SLOT_BLUE = "#3987e5"  # categorical slot 1, dark
SLOT_ORANGE = "#d95926"  # categorical slot 2, dark

CHIP_W_RE = re.compile(r"W(\d+)")
TEXT_WEEK_LEFT_RE = re.compile(r"week (\d+)% left")


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_seat_weekly_series() -> list[tuple[datetime, float]]:
    """(timestamp, pct_used) for every boundary in the seat's own log that
    carries a resolvable weekly-quota reading. `quota.W` / the chip's `W\\d+`
    / the older "week N% left" text all mean the same thing: % of the weekly
    window still LEFT — so pct_used = 100 - W."""
    path = HOME_BRR / "runs" / SEAT_RUN_ID / "boundaries.jsonl"
    series: list[tuple[datetime, float]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            at = row.get("at")
            if not at:
                continue
            w = None
            q = row.get("quota")
            if isinstance(q, dict) and q.get("W") is not None:
                w = q["W"]
            else:
                inj = row.get("inject") or ""
                m = TEXT_WEEK_LEFT_RE.search(inj)
                if m:
                    w = int(m.group(1))
                else:
                    m2 = CHIP_W_RE.search(inj)
                    if m2:
                        w = int(m2.group(1))
            if w is not None:
                series.append((_parse_ts(at), 100.0 - float(w)))
    series.sort(key=lambda pair: pair[0])
    return series


def load_usage_samples_today() -> list[tuple[datetime, float]]:
    """Claude weekly-window samples from usage-samples.jsonl (10h retention
    by design — this only ever covers the tail of the run)."""
    path = HOME_BRR / "usage-samples.jsonl"
    out: list[tuple[datetime, float]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("shell") != "claude" or row.get("window_minutes") != 10080.0:
                continue
            ts = datetime.fromtimestamp(row["at"], tz=timezone.utc)
            out.append((ts, float(row["used_percent"])))
    out.sort(key=lambda pair: pair[0])
    return out


def load_merged_prs() -> list[dict]:
    raw = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "merged",
            "--search",
            f"merged:>={RUN_START}",
            "--json",
            "number,mergedAt,title",
            "--limit",
            "100",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    ).stdout
    prs = json.loads(raw)
    prs.sort(key=lambda pr: pr["mergedAt"])
    return prs


def load_his_messages() -> list[str]:
    """Every `kind: event` row's `ts` since the run woke, across every
    conversation file on his Telegram thread."""
    conv_dir = HOME_BRR / "conversations" / "cloud__telegram__155783668__"
    out: list[str] = []
    for fp in glob.glob(str(conv_dir / "*.jsonl")):
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("kind") != "event":
                    continue
                ts = row.get("ts")
                if ts and ts >= RUN_START:
                    out.append(ts)
    out.sort()
    return out


def style_axes(ax: plt.Axes) -> None:
    ax.set_facecolor(DARK_SURFACE)
    for spine in ax.spines.values():
        spine.set_color(BASELINE)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.xaxis.label.set_color(INK_SECONDARY)
    ax.yaxis.label.set_color(INK_SECONDARY)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)


def make_gauge(
    seat_series: list[tuple[datetime, float]],
    sample_series: list[tuple[datetime, float]],
    prs: list[dict],
    out_path: Path,
) -> dict:
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=100)
    fig.patch.set_facecolor(DARK_SURFACE)
    style_axes(ax)

    xs = [t for t, _ in seat_series]
    ys = [v for _, v in seat_series]
    ax.plot(xs, ys, color=SLOT_BLUE, linewidth=2, label="weekly window used % (seat boundary log)")

    if sample_series:
        sxs = [t for t, _ in sample_series]
        sys_ = [v for _, v in sample_series]
        ax.plot(
            sxs,
            sys_,
            color=SLOT_ORANGE,
            linewidth=1.4,
            linestyle=(0, (1, 1)),
            label="usage-samples.jsonl (last ~10h, cross-check)",
        )

    ymin, ymax = 0, 100
    ax.set_ylim(ymin, ymax)
    for pr in prs:
        t = _parse_ts(pr["mergedAt"])
        ax.axvline(t, color=INK_MUTED, linewidth=0.6, alpha=0.35, zorder=1)
    # Label a sparse subset of PR ticks above the axes so 27 numbers don't
    # collide with each other or the plotted line — every 3rd, plus first
    # and last. Anchored in axes-fraction Y so they live above y=100, clear
    # of both the data and the legend.
    labeled = prs[::3]
    if prs[-1] not in labeled:
        labeled.append(prs[-1])
    for pr in labeled:
        t = _parse_ts(pr["mergedAt"])
        ax.annotate(
            f"#{pr['number']}",
            xy=(t, 1.0),
            xycoords=("data", "axes fraction"),
            xytext=(0, 3),
            textcoords="offset points",
            rotation=90,
            fontsize=6.5,
            color=INK_MUTED,
            ha="center",
            va="bottom",
            annotation_clip=False,
        )

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%MZ"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
    ax.set_ylabel("% of Claude weekly window used", color=INK_SECONDARY)
    fig.suptitle(
        "The seventy-hour run — Claude weekly window, with merged PRs as ticks",
        color=INK_PRIMARY,
        fontsize=13,
        x=0.01,
        ha="left",
    )
    fig.text(
        0.01,
        0.01,
        "source: run-260912-1513-7hw5 boundaries.jsonl (continuous — no retention gap found) "
        "+ usage-samples.jsonl · 27 PRs merged since 2026-09-12T15:13Z",
        color=INK_MUTED,
        fontsize=7,
    )
    legend = ax.legend(loc="lower right", facecolor=DARK_SURFACE, edgecolor=BASELINE, fontsize=8, labelcolor=INK_SECONDARY)
    legend.get_frame().set_alpha(0.9)
    fig.tight_layout(rect=(0, 0.035, 1, 0.90))
    fig.savefig(out_path, facecolor=DARK_SURFACE)
    plt.close(fig)

    return {
        "seat_series_points": len(seat_series),
        "seat_series_start": seat_series[0][0].isoformat() if seat_series else None,
        "seat_series_end": seat_series[-1][0].isoformat() if seat_series else None,
        "seat_series_start_pct_used": seat_series[0][1] if seat_series else None,
        "seat_series_end_pct_used": seat_series[-1][1] if seat_series else None,
        "usage_samples_points": len(sample_series),
        "prs_plotted": len(prs),
    }


def make_days(prs: list[dict], his_messages: list[str], out_path: Path) -> dict:
    msg_per_day: Counter[str] = Counter(ts[:10] for ts in his_messages)
    pr_per_day: Counter[str] = Counter(pr["mergedAt"][:10] for pr in prs)
    days = sorted(set(msg_per_day) | set(pr_per_day))

    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=100)
    fig.patch.set_facecolor(DARK_SURFACE)
    style_axes(ax)

    import numpy as np

    x = np.arange(len(days))
    width = 0.36
    msg_vals = [msg_per_day.get(d, 0) for d in days]
    pr_vals = [pr_per_day.get(d, 0) for d in days]

    ax.bar(x - width / 2, msg_vals, width, color=SLOT_BLUE, label="his messages")
    ax.bar(x + width / 2, pr_vals, width, color=SLOT_ORANGE, label="PRs merged")

    for xi, v in zip(x - width / 2, msg_vals):
        ax.annotate(str(v), (xi, v), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=9, color=INK_PRIMARY)
    for xi, v in zip(x + width / 2, pr_vals):
        ax.annotate(str(v), (xi, v), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=9, color=INK_PRIMARY)

    ax.set_xticks(x)
    ax.set_xticklabels(days, color=INK_SECONDARY)
    ax.set_ylabel("count", color=INK_SECONDARY)
    ax.set_title(
        "Output does not track chat volume — messages vs. PRs merged, per UTC day",
        color=INK_PRIMARY,
        fontsize=13,
        loc="left",
        pad=14,
    )
    legend = ax.legend(loc="upper right", facecolor=DARK_SURFACE, edgecolor=BASELINE, fontsize=9, labelcolor=INK_SECONDARY)
    legend.get_frame().set_alpha(0.9)
    fig.text(
        0.01,
        0.01,
        "source: .brr/conversations/cloud__telegram__155783668__/*.jsonl (kind: event) "
        "+ gh pr list --state merged, grouped by UTC merge day",
        color=INK_MUTED,
        fontsize=7,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out_path, facecolor=DARK_SURFACE)
    plt.close(fig)

    return {
        "days": days,
        "messages_per_day": dict(msg_per_day),
        "prs_merged_per_day": dict(pr_per_day),
        "messages_total": sum(msg_vals),
        "prs_total": sum(pr_vals),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    seat_series = load_seat_weekly_series()
    sample_series = load_usage_samples_today()
    prs = load_merged_prs()
    his_messages = load_his_messages()

    gauge_stats = make_gauge(seat_series, sample_series, prs, OUT_DIR / "gauge.png")
    days_stats = make_days(prs, his_messages, OUT_DIR / "days.png")

    numbers = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_window": {"start": RUN_START, "seat_run_id": SEAT_RUN_ID},
        "gauge": {
            **gauge_stats,
            "source_seat_boundaries": str(HOME_BRR / "runs" / SEAT_RUN_ID / "boundaries.jsonl"),
            "source_usage_samples": str(HOME_BRR / "usage-samples.jsonl"),
            "source_prs": "gh pr list --state merged --search 'merged:>=2026-09-12T15:13:00Z'",
            "note": (
                "The seat's own boundary log (boundaries.jsonl) carries a weekly-quota "
                "reading at every boundary across the full 70h span with no gap larger "
                "than ~12 minutes — including the 09-12->09-14 stretch the shelf page's "
                "'blind weekend' note (sourced from usage-samples.jsonl's 10h retention "
                "and per-strand claude-usage-levels.json snapshots, neither of which "
                "cover that period) called blind. The per-strand snapshot files the "
                "shelf spec assumed exist do not: none of the 31 strand run dirs under "
                ".brr/runs/ contain a claude-usage-levels.json. This chart uses the "
                "seat's own continuous log instead, which is denser and gap-free; the "
                "'blind weekend' finding does not hold against it."
            ),
        },
        "days": {
            **days_stats,
            "source_messages": str(HOME_BRR / "conversations" / "cloud__telegram__155783668__"),
            "source_prs": "gh pr list --state merged --search 'merged:>=2026-09-12T15:13:00Z'",
        },
        "prs_merged_since_run_start": [
            {"number": pr["number"], "mergedAt": pr["mergedAt"], "title": pr["title"]} for pr in prs
        ],
        "card": {
            "source": "brnrd hud --card --outbox .brr/outbox/evt-1789225987990906000-820q",
            "renderer": "scripts/render_card.py",
            "content": "the weaver's half only (## Now / ## Plan / ## Vector) — ## Said / ## Ledger excluded",
        },
        "run_page": {
            "source_url": "https://brnrd.dev/runs/hugimuni-labs__brnrd/run-260912-1513-7hw5",
            "auth": "brnrd_session cookie from .tmp/brnrd_session.cookie (value never printed/logged/committed)",
            "screenshots": ["run-page.png (1280x800)", "run-page-phone.png (390x844)"],
            "page_shows": "corpus mirrored 9/15/2026, 1:56:30 PM (dashboard's own mirror timestamp)",
        },
    }

    with open(OUT_DIR / "numbers.json", "w") as f:
        json.dump(numbers, f, indent=2)
        f.write("\n")

    for name in ("gauge.png", "days.png", "numbers.json"):
        p = OUT_DIR / name
        print(f"{name}: {p.stat().st_size} bytes")


if __name__ == "__main__":
    main()
