# Grok /usage quota collector

Status: published; PR [#2239](https://github.com/hugimuni-labs/brnrd/pull/2239).

Branch: `brr/grok-usage`, seeded from `origin/main` (57f9f679); maintainer-only f36a7c9a excluded.

## Fixture provenance

Captured on 2026-10-09 with host Grok Build `1.0.50 (c58f321264ba) [stable]`, stdlib `pty.openpty`, 50×160 terminal, `TERM=xterm-256color`, `NO_COLOR=1`, an isolated temporary cwd, no model prompt.

- `tests/fixtures/grok_usage_screen_exhausted.txt`: complete, byte-for-byte 8,160-byte PTY stream from `grok --fullscreen --no-alt-screen --no-subagents --no-auto-update`; `/usage\r` sent at 6 seconds, terminated at 16 seconds. Includes ANSI cursor positioning and subscription footer. SHA-256: `d544dfe5e9e6166c01dde5c98e1787e74b1abfb3b427712df70b33bbae057905`.
- `tests/fixtures/grok_usage_screen_starting.txt`: exact raw byte slice from an earlier `grok --minimal --no-alt-screen --no-subagents` capture; `/usage` sent at 3 seconds, before session startup finished. This exposes “Session usage is unavailable until the session starts.” The full minimal stream was 8.5 MB of mostly repaints and is not committed.

The panel reports `Weekly limit (SuperGrok)`, `100%`, `Resets: October 12, 13:59`. The footer independently reports `Weekly limit left: 0%`; hence the panel's percentage is **used**, not remaining. No model calls occurred in the interactive session. No session quota is shown.

## Live snapshot

Read once for real from this worktree's `src/brr/grok_usage.py` on the host. The collector opened a fresh temporary cwd and sent only `/usage`; no model prompt or alternate Shell was used.

```json
{
  "source": "grok /usage PTY",
  "updated_at": "2026-10-09T16:04:08Z",
  "plan_type": "SuperGrok",
  "week_used_percentage": 100.0,
  "week_reset": "October 12, 13:59",
  "week_resets_at": 1791806340.0,
  "quota": {
    "summary": "week 0% left (resets Oct 12, 11:59am (UTC))",
    "buckets": {
      "week": {
        "remaining_percentage": 0.0
      }
    },
    "week_resets_at": 1791806340.0,
    "reset_timezone": "host local time (not printed by Grok)"
  }
}
```

`week_resets_at = 1791806340` is 2026-10-12 11:59 UTC (13:59 Europe/Paris on this host). The reset epoch is inferred from the host local timezone because the panel omits a zone.

## Implementation and verification

- `grok_usage.py` parses the captured ANSI panel, derives remaining percentage, records the plan and raw reset, normalizes the summary clock to UTC for the existing relative-reset chip parser, and writes `.grok-usage-levels.json` atomically. It refreshes one account cache with a 30-second TTL (`BRR_GROK_USAGE_TTL` override), explicit missing-quota/error snapshots on failed probes, and a bounded 12-second PTY lifetime.
- Daemon heartbeat refreshes quota and merges it with Grok's own result envelope. Boundary flushes read the cache only. Own-run token/model fields survive the merge; a previous run's spend remains attributed to that previous session and its token totals are discarded.
- Dashboard publisher includes a Grok weekly window and quota labels for runner profiles; the run ledger reads quota on entry/exit. Existing `runner_quota`, facets, fuel, and status-line consumers understand the Claude-compatible weekly bucket without new parser branches.
- Unit tests disable real Grok PTY sessions through an autouse fixture. Parser value checks use the captured bytes; a fake executable verifies actual `/usage` input, isolated cwd, and cleared git discovery pins.

Tests: 189 passed in the collector, Grok adapter, quota, Claude collector, ledger, and facets files; 66 passed in targeted daemon/cloud quota and control-capture tests. Full suite left to CI per dispatch.

One discovery from the manual probe: a temporary cwd alone is insufficient when `GIT_DIR`/`GIT_WORK_TREE` still pin the strand repo. Grok startup detached this strand's HEAD even from that temporary directory. The branch was restored immediately with the captured-fixture commit retained; no maintainer branch was modified. The production collector strips those pins before spawning, and its isolated-cwd test covers the seam.

## Unverified

A non-exhausted account has not been observed. Reset text lacks a timezone and year; Grok's displayed clock is interpreted in the host's local timezone, with that assumption stated in the snapshot. No claim of a session quota or context-window capacity from this collector.
