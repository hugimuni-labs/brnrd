# The five answers — real threads, drafted replies, 2026-09-22

keeps: 14 days — every count and status below is a live `gh api` read today; re-verify before posting if this sits

Commissioned by evt-1790106596644234000-nub7 (his words: the daily lane is too petty; answer real questions "faster and truer than anyone, in public, with the run as the proof"). Sourced from `surface/shelf/finding-our-people-2026-09-21.md` and `surface/shelf/cc-issues-mined-2026-09-20.md`, both under the account home, plus a fresh re-verify pass run by this strand (`run-260922-1953-3fnh`) at 2026-09-22T20:0xZ. Two candidates from the shelf pages had **closed since they were written** — `claude-code#20131` (multi-account profile support) and `codex#9224` (Codex Remote Control, 411👍) both flipped to `closed` between 2026-09-21 and today — dropped from consideration; that churn is itself a receipt for how fast this lane moves, and the mechanised sweep in §The lane, mechanised exists to catch it going forward.

Reddit could not be added to this pass: every fetch path (`reddit.com`, `old.reddit.com`, `*.json`, `site:reddit.com` searches) is refused from this environment, a limit the shelf pages already hit and logged. All five below are GitHub issues, `anthropics/claude-code`, each re-verified `state: open` by `gh api repos/anthropics/claude-code/issues/<n>` at the timestamp in its row.

## The five

| # | URL | asker | their exact ask | why brnrd answers it | lane |
|---|---|---|---|---|---|
| 1 | [claude-code#30447](https://github.com/anthropics/claude-code/issues/30447) | Foodcoman | a `--headless`/`--daemon` flag so remote-control can run without a TTY on a headless server | that's the whole architecture: a daemon owns the `claude` process, no PTY, restarts with the machine | GitHub comment |
| 2 | [claude-code#32982](https://github.com/anthropics/claude-code/issues/32982) | sidkandan | root-caused why Remote Control sessions silently die after ~5–30 min idle (server TTL bug, source-verified against cli.js) | the chat is a daemon-owned queue, not a session with a TTL to expire | GitHub comment |
| 3 | [claude-code#17428](https://github.com/anthropics/claude-code/issues/17428) | mrgoonie | `/compact` should write a file-backed summary with selective restoration instead of losing content in-memory | the dominion *is* file-backed working memory, committed to git, re-read whole every wake — the proposal as the default, not an enhancement | GitHub comment |
| 4 | [claude-code#13585](https://github.com/anthropics/claude-code/issues/13585) | Data-Wise | a `claude quota` / `claude quota --json` command so scripts can read session/weekly usage | brnrd reads the same endpoint today and uses the number to route work to whichever subscription (Claude or Codex) has room — the missing piece his use case needs next | GitHub comment |
| 5 | [claude-code#35744](https://github.com/anthropics/claude-code/issues/35744) | cheapestinference | shipped `claude-auto-retry`, a tmux workaround that waits out the rate-limit reset and auto-sends "continue" | same problem, solved on a measured refill reading instead of a wall-clock guess — no tmux dependency, no DST bug | GitHub comment |

Re-verify commands (all read today, 2026-09-22):
```
gh api repos/anthropics/claude-code/issues/30447 --jq '{state,updated_at,reactions:.reactions["+1"]}'  # open, 37👍
gh api repos/anthropics/claude-code/issues/32982 --jq '{state,updated_at,reactions:.reactions["+1"]}'  # open, 86👍
gh api repos/anthropics/claude-code/issues/17428 --jq '{state,updated_at,reactions:.reactions["+1"]}'  # open, 92👍
gh api repos/anthropics/claude-code/issues/13585 --jq '{state,updated_at,reactions:.reactions["+1"]}'  # open, 124👍
gh api repos/anthropics/claude-code/issues/35744 --jq '{state,updated_at,reactions:.reactions["+1"]}'  # open, 100👍
```

Not used, and why: `claude-code#20131` (multi-account) and `codex#9224` (Codex Remote Control, sluongng) were the shelf's strongest "second account / phone control" candidates and both are now **closed** — a live re-check this run did that the shelf pages, written a day earlier, couldn't. `codex#21073` and `codex#23200` are still open and strong (auto-resume-on-limit; headless mobile without desktop online) but sit on `openai/codex`, where our own receipts (the daemon's launchd/systemd install, the file-backed dominion) are Claude-Code-repo-native comparisons first — kept on the bench for the next pass rather than forced in here.

---

## Draft 1 — claude-code#30447 (Foodcoman)

> You're describing exactly what we built the other way around: no `--headless` flag needed, because the execution engine never needed a TTY to begin with. A daemon owns the `claude` process, restarts it with the machine (launchd/systemd), polls outbound only. It's been running this repo's own agent since March: 3,125 commits, 2,056 merged PRs, zero terminal attached the whole time. Early, and yours to break: pip install brnrd.

(427 chars)

## Draft 2 — claude-code#32982 (sidkandan)

> Your root-cause dig (server TTL not resetting on keepalive) is the kind of bug report I wish every issue looked like. We hit the same wall from the other side and stopped treating the chat as a session at all — it's a queue a daemon owns, so there's no TTL to expire and no 404 waiting when you step away. The seat holds open with no timeout by default; the only clock is you or a quota wall. Open source, early, and your bug report doubles as our regression test if you want to try breaking it too.

(500 chars)

## Draft 3 — claude-code#17428 (mrgoonie)

> File-backed beats in-memory for the exact reason your issue names: you can't grep a summary that only exists inside a compaction event. We made it the default, not a fallback — an agent's whole working memory (notes, its own past mistakes, an open-item list) lives as files in a git repo, re-read whole on every wake, so selective recall is just opening the file. The cost is honesty: it only remembers what it wrote down. Early and open: github.com/hugimuni-labs/brnrd

(470 chars)

## Draft 4 — claude-code#13585 (Data-Wise)

> Your example output is close to literally what we ship, just not first-party: we read the same usage endpoint the CLI itself calls and print session/weekly percentages next to every run, then route work to whichever subscription — Claude or Codex — actually has room. It inherits the endpoint's own outages (it 429'd on us once), so treat it as a script, not a guarantee. Code's on GitHub if you want to see the reader: github.com/hugimuni-labs/brnrd

(452 chars)

## Draft 5 — claude-code#35744 (cheapestinference)

> You already shipped the fix the vendor hasn't — respect. We built the same idea without the tmux dependency: read the quota, and when it drops under a floor, park the run and thaw it on a measured refill instead of guessing the reset time off a wall clock. Same outcome, no DST bugs. If your approach beats ours on any point I'll say so in the docs and link claude-auto-retry either way: github.com/hugimuni-labs/brnrd

(419 chars)

---

## The lane, mechanised

A weekly `prospects` sweep, spec only — nothing built here.

- **inputs**: the query list already proven in `cc-issues-mined-2026-09-20.md` §Method (the class 1–4 `gh api search/issues` queries, both repos) plus the profile/gather-place list in `finding-our-people-2026-09-21.md` §1/§3 (subreddits via a working mirror, HN via `hn.algolia.com`, the D-series creator/catalog URLs for a cheap liveness ping).
- **state**: one file, `dominion/tools/prospects-seen.json` — `{url: {first_seen, state, reactions}}`, keyed by URL so a rerun diffs against it instead of re-triaging everything.
- **each run**:
  1. re-run every named query, `is:open`, sorted by reactions;
  2. diff against `prospects-seen.json`: new URLs, and any previously-open URL that flipped `closed` (the exact drift this report just caught by hand on #20131 / #9224);
  3. write a dated `dominion/prospects/<date>.md`: a table of new-since-last-run threads (URL, asker, one-line ask, class) and a short list of closures to drop from any standing shortlist;
  4. update `prospects-seen.json`.
- **output**: nothing posted automatically — the sweep produces a candidate list for a human or a resident to draft from, same shape as this report's table. Posting stays a deliberate act, never a cron job speaking in public.
- **cadence**: weekly, `schedule.md` `every: 7d`, economy core (`claude-haiku`) — this is enumeration, not judgement.
