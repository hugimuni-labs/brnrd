# The issues they already filed
Status: complete, with one caveat up top — GitHub search is a loose full-text match (`in:title,body`), so the counts below are an **upper bound on noise, not a count of complainers**. Only the 22 threads in the top-20 table were read for fit; the rest of each count was not.

## Method
`gh api -X GET search/issues -f q='repo:<repo> is:issue <query> in:title,body'`, sorted by reactions (top 50 kept), plus `is:open` for the open count, and `created asc/desc` for oldest/newest. closed = total − open. "👍 top5" = sum of the `+1` reaction on the five most-reacted results for that query (`+1` only, not all reactions). Raw results: `the-issues-they-already-filed.raw.json` beside this file. Queries were run 2026-09-20 on `anthropics/claude-code` and `openai/codex`. Early attempts with long phrases (`--limit 100`, multi-word AND) returned mostly empty sets and were discarded; codex has almost no hits on memory phrasings.

## Counts table
class 1 = memory · 2 = background dies · 3 = remote/channels · 4 = cost/quota. Every cell is a measured value; a query that failed is marked, never zero.

| class | repo: query | open | closed | total | oldest | newest | 👍 top5 |
|---|---|---|---|---|---|---|---|
| 1 | claude-code: `memory machines` | 43 | 160 | 203 | 2025-10-08 | 2026-09-14 | 103 |
| 1 | claude-code: `memory compaction lost` | 30 | 139 | 169 | 2025-03-29 | 2026-09-18 | 176 |
| 1 | claude-code: `forgets sessions` | 5 | 87 | 92 | 2025-05-09 | 2026-09-12 | 46 |
| 1 | claude-code: `memory sync` | 61 | 303 | 364 | 2025-05-31 | 2026-09-18 | 316 |
| 1 | claude-code: `persistent memory sessions` | 72 | 440 | 512 | 2025-02-25 | 2026-09-17 | 395 |
| 2 | claude-code: `background restart` | 260 | 575 | 835 | 2025-03-26 | 2026-09-19 | 455 |
| 2 | claude-code: `scheduled laptop` | 21 | 30 | 51 | 2026-01-09 | 2026-09-16 | 63 |
| 2 | claude-code: `routine sleep` | 17 | 30 | 47 | 2025-07-01 | 2026-09-19 | 39 |
| 2 | claude-code: `background shutdown` | 49 | 108 | 157 | 2025-07-29 | 2026-09-17 | 38 |
| 2 | claude-code: `survive reboot` | 64 | 147 | 211 | 2025-09-11 | 2026-09-19 | 146 |
| 2 | claude-code: `cron scheduled` | 134 | 379 | 513 | 2025-07-01 | 2026-09-20 | 161 |
| 3 | claude-code: `remote control disconnected` | 76 | 102 | 178 | 2026-01-14 | 2026-09-19 | 114 |
| 3 | claude-code: `remote control mobile` | 244 | 629 | 873 | 2025-10-21 | 2026-09-20 | 463 |
| 3 | claude-code: `channels telegram` | 34 | 307 | 341 | 2026-02-01 | 2026-09-18 | 118 |
| 3 | claude-code: `channels discord` | 13 | 107 | 120 | 2025-10-21 | 2026-09-14 | 89 |
| 3 | claude-code: `remote control silent` | 249 | 482 | 731 | 2025-09-05 | 2026-09-19 | 348 |
| 4 | claude-code: `usage limit accounts` | 45 | 132 | 177 | 2025-06-18 | 2026-09-18 | 860 |
| 4 | claude-code: `two subscriptions` | 31 | 51 | 82 | 2025-10-04 | 2026-09-09 | 199 |
| 4 | claude-code: `usage limit reached` | 100 | 738 | 838 | 2025-05-11 | 2026-09-19 | 869 |
| 4 | claude-code: `switch accounts limit` | 24 | 76 | 100 | 2025-06-05 | 2026-09-18 | 361 |
| 4 | claude-code: `weekly limit usage` | 156 | 811 | 967 | 2025-07-01 | 2026-09-19 | 532 |
| 1 | codex: `memory machines` | 23 | 6 | 29 | 2025-10-28 | 2026-09-16 | 18 |
| 1 | codex: `memory compaction lost` | 29 | 6 | 35 | 2025-10-30 | 2026-09-18 | 32 |
| 1 | codex: `forgets sessions` | 6 | 3 | 9 | 2025-09-01 | 2026-09-11 | 20 |
| 1 | codex: `memory sync` | 67 | 30 | 97 | 2025-09-18 | 2026-09-20 | 77 |
| 1 | codex: `persistent memory sessions` | 73 | 15 | 88 | 2025-05-11 | 2026-09-19 | 41 |
| 2 | codex: `background restart` | 302 | 72 | 374 | 2025-09-05 | 2026-09-20 | 112 |
| 2 | codex: `scheduled laptop` | 9 | 1 | 10 | 2026-05-24 | 2026-08-15 | 38 |
| 2 | codex: `routine sleep` | 4 | 0 | 4 | 2026-03-06 | 2026-09-10 | 43 |
| 2 | codex: `background shutdown` | 58 | 10 | 68 | 2026-03-14 | 2026-09-16 | 47 |
| 2 | codex: `survive reboot` | 30 | 6 | 36 | 2026-04-09 | 2026-09-19 | 29 |
| 2 | codex: `cron scheduled` | 55 | 4 | 59 | 2025-12-19 | 2026-09-20 | 79 |
| 3 | codex: `remote control disconnected` | 71 | 13 | 84 | 2026-04-21 | 2026-09-20 | 94 |
| 3 | codex: `remote control mobile` | 287 | 38 | 325 | 2025-08-23 | 2026-09-19 | 576 |
| 3 | codex: `channels telegram` | 2 | 1 | 3 | 2026-02-04 | 2026-07-24 | 16 |
| 3 | codex: `channels discord` | 4 | 1 | 5 | 2026-02-16 | 2026-08-08 | 12 |
| 3 | codex: `remote control silent` | 148 | 23 | 171 | 2025-09-17 | 2026-09-19 | 157 |
| 4 | codex: `usage limit accounts` | 67 | 19 | 86 | 2025-05-24 | 2026-09-19 | 70 |
| 4 | codex: `two subscriptions` | 15 | 5 | 20 | 2026-02-10 | 2026-09-18 | 45 |
| 4 | codex: `usage limit reached` | 279 | 90 | 369 | 2025-04-18 | 2026-09-20 | 156 |
| 4 | codex: `switch accounts limit` | 29 | 10 | 39 | 2025-08-31 | 2026-09-19 | 35 |
| 4 | codex: `weekly limit usage` | 377 | 158 | 535 | 2025-08-08 | 2026-09-20 | 395 |

Reading it: class 4 has by far the strongest reaction mass (e.g. `usage limit reached` top-5 = 869 👍) but most of it is "limits drained too fast" bug reports — a complaint brnrd does not fix. The thread-level signal is in the next table.

## Top 20 threads (relevant only; ranked by 👍, then comments)
Search results were filtered by hand: high-👍 threads that are not about the four classes (e.g. cache-TTL regression #46829, verbose comments #65961) are excluded. Quotes are verbatim from the issue body.

| # | thread | 👍/💬 | class | quote | what brnrd does | fit |
|---|---|---|---|---|---|---|
| 1 | https://github.com/openai/codex/issues/9224 | 411/58 (closed) | 3 | "The ability to remotely control `codex` cli running on my desktop PC from my phone" | Telegram/Slack/Signal gates (`src/brr/gates/`) drive a daemon-run Codex or Claude Shell from a phone | strong |
| 2 | https://github.com/openai/codex/issues/1986 | 139/6 (closed) | 4 | "Global usage within the current window (not just the current session)" | quota reading per Shell in the wake/portal state (`resources.quota`); `[verify]` whether it aggregates across sessions | partial |
| 3 | https://github.com/anthropics/claude-code/issues/29006 | 123/40 (closed) | 3 | RC inside Claude Desktop — body not quoted (read only the checklist) | none: brnrd has no Desktop surface | none |
| 4 | https://github.com/anthropics/claude-code/issues/13585 | 122/27 | 4 | "Add Quota Information Access to Claude Code CLI" (title) | reads claude/codex quota (`claude_usage.py`, `codex_usage.py`) and shows it on the bar and card | strong |
| 5 | https://github.com/anthropics/claude-code/issues/20131 | 114/43 (closed) | 4 | "Claude Code currently only supports a single authenticated account at a time." | a runner catalog with several Shell+Core profiles and per-quota routing | partial |
| 6 | https://github.com/anthropics/claude-code/issues/34255 | 108/72 | 3 | "Remote Control: automatic reconnection doesn't work -- connection drops silently" (title) | brnrd's link is a daemon-owned queue, not a terminal session, so a dropped phone doesn't drop the run; `[verify]` behaviour on relay outage | strong |
| 7 | https://github.com/anthropics/claude-code/issues/35744 | 99/21 | 4 | "the session pauses and the user must manually wait and type "continue" to resume" | starvation park: `hold: true`/`resume: refill` thaws on a measured refill, no typing | strong |
| 8 | https://github.com/anthropics/claude-code/issues/17428 | 92/44 | 1 | "enhancing the `/compact` command to use a **file-backed summary approach**" | the dominion is file-backed memory (notes, pitfalls, playbook) committed to git and re-read every wake | strong |
| 9 | https://github.com/anthropics/claude-code/issues/32982 | 85/20 | 3 | "Remote Control sessions die after ~20 min idle" (title) | `brnrd await` holds a seat open with no TTL; parks are opt-in | strong |
| 10 | https://github.com/anthropics/claude-code/issues/2944 | 79/17 (closed) | 4 | "seamlessly fallback to using the Claude API with their own API key" | none — brnrd reroutes to another subscription Shell, not an API key `[verify]` | partial |
| 11 | https://github.com/openai/codex/issues/21073 | 62/15 | 4 | "the error already tells me exactly when the quota resets" | same refill-park as #7; `[verify]` Codex reset parsing | strong |
| 12 | https://github.com/openai/codex/issues/23200 | 59/22 | 3 | "depends on a personal desktop machine staying online" | daemon runs as a launchd/systemd service (`daemon_install/`) on any always-on box | strong |
| 13 | https://github.com/anthropics/claude-code/issues/20636 | 59/11 (closed) | 4 | "Expose rate limit usage (session %, weekly %) to statusLine" (title) | the bar carries `q S85·W82` per boot | strong |
| 14 | https://github.com/anthropics/claude-code/issues/28571 | 53/18 | 3 | "No indication the connection is lost" | dashboard/card projections show run state; `[verify]` phone-side indicator | partial |
| 15 | https://github.com/anthropics/claude-code/issues/21943 | 47/12 | 4 | "programmatically access Claude Code Pro/Max subscription usage data" | quota rides `portal-state.json` for the resident | partial |
| 16 | https://github.com/anthropics/claude-code/issues/87 | 42/12 (closed) | 1 | "a more sophisticated memory system for code assistants" | dominion + repo kb, per-repo and account-global | strong |
| 17 | https://github.com/anthropics/claude-code/issues/2794 | 38/5 (closed) | 2 | "Scheduled Actions in Claude Code" (title) | dominion `schedule.md` with `at:`/`every:` | strong |
| 18 | https://github.com/openai/codex/issues/8317 | 38/8 | 2 | "no first-class way to schedule a task to run later or repeatedly" | same `schedule.md`, runs Codex as a Core | strong |
| 19 | https://github.com/anthropics/claude-code/issues/30447 | 37/7 | 3 | "always-on, remotely-accessible Claude Code instances on headless" servers | the whole design; the daemon is headless-first | strong |
| 20 | https://github.com/anthropics/claude-code/issues/36503 | 37/49 | 3 | "Channels are not currently available" (title) | own Telegram gate, no plugin gating | partial |

Also read, not ranked: claude-code#25739 (portable project memory across machines, 23👍/27💬, open) — the exact class-1 headline ask; brnrd's answer is the dominion in a git repo (fit: strong, `[verify]` cross-machine sync path — the dominion consolidation work, w-80, is in flight).

## Five drafted replies (fill in the real link at send; not posted)
**#30447 (headless remote control).** "I ran into this exact wall, so I built around it: a small daemon that owns the Claude Code / Codex process on an always-on box, restarts with the machine (launchd/systemd), and takes messages from Telegram or Slack rather than a terminal session. Sessions don't have a TTL because the chat is a queue, not a connection. It's early and it's mine, so expect rough edges. https://github.com/hugimuni-labs/brnrd"

**#35744 (auto-continue after limit reset).** "Same annoyance here. What worked: read the quota, and when it drops under a floor, park the run and thaw it on a measured refill instead of waiting for someone to type continue. It's not magic — it only resumes what was parked. I did it in a daemon around the CLI: https://github.com/hugimuni-labs/brnrd"

**#17428 (file-backed compact).** "File-backed beats summaries for me too. I keep the agent's working memory as files in a git repo — notes, a list of past mistakes, a schedule — and every wake reads them, so nothing depends on the context window surviving. The cost is discipline about what gets written down. https://github.com/hugimuni-labs/brnrd"

**#13585 (quota in the CLI).** "You can get the numbers today: the usage endpoint the CLI itself uses is readable, and the same for Codex. I read both on a timer and show 'session 85% / week 82%' next to each run, which is enough to route work to whichever subscription has room. `[verify]` endpoint stability — it 429'd for some users (#30930). https://github.com/hugimuni-labs/brnrd"

**#23200 (Codex mobile needs desktop online).** "The desktop-must-stay-awake limit is real. I run the agent as a service on a machine that's always on and message it from my phone through Telegram; Codex and Claude are both just backends. Doesn't replace the official app's UI. https://github.com/hugimuni-labs/brnrd"

## What we do worse (vs. the native answer; from the 2026-09-07 scan, unre-verified today)
- **Memory:** native auto memory is on by default, zero setup, and in the model's training loop; brnrd's dominion needs a daemon and a git repo, and it's the agent's own discipline, not a guarantee. Cross-machine sync is not shipped natively either, but ours is only designed (w-80).
- **Background:** `--bg`, Routines (cloud, fresh clone, no machine needed) and Desktop scheduled tasks need no always-on host; brnrd needs one.
- **Phone:** Remote Control has a first-party iOS/Android app, push notifications and permission prompts; brnrd's phone is a chat bot with none of that UI. Happy Coder is a better pure phone client.
- **Cost:** `/usage`, `--max-budget-usd` and `autoContinueAtUsageLimit` are built in and exact; brnrd's numbers are read from the same endpoints, so they inherit its outages.

## Open
- Search is not semantic: threads phrased without our keywords are missed; the counts undercount, and long-tail topical noise inflates them. A label-based pass (`enhancement`/`memory`) would tighten it.
- Reactions for #3 and #14 quotes: body text was only partially read; quotes marked (title) are titles, not body.
- Does brnrd's quota view aggregate across sessions (codex#1986)? Not checked in source.
- Cost of this run: ~370k of a 400k allowance, mostly context re-reads, not the search calls.
