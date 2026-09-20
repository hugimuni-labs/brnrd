# The two checks the growth report owed — 2026-09-21

**Status: complete, with one premise miss in Check 2.** Strand `run-260920-2335-50x5`, branch `brr/the-two-checks-the-report-owed`. Read-only; no source changes. All fetches 2026-09-21. Raw Reddit JSON in `/tmp/rd/` (not kept).

---

## Check 1 — does Claude Code Remote Control run on a server with no terminal attached?

**Verdict: PARTLY.** The snippet fuses three different products. Remote Control itself: **false** (needs a persistent local `claude` process). But first-party "runs while you're away, no terminal" **does exist** in two other forms: local *background sessions* (supervisor daemon) and *cloud routines/sessions* (Anthropic's machine, cron-capable).

### Remote Control — false for "no terminal / no process"
Source: https://code.claude.com/docs/en/remote-control (read 2026-09-21)
- Server mode exists (`claude remote-control`) but: *"The process stays running in your terminal in server mode, waiting for remote connections."*
- *"**Local process must keep running**: Remote Control runs as a local process. If you close the terminal, quit VS Code, or otherwise stop the `claude` process, the session goes offline until you bring it back. … To keep a session running on a remote machine after you disconnect from SSH, start it inside `tmux` or `screen`."*
- Outage: *"**Server mode**: Claude Code gives up after roughly 10 minutes and the `claude remote-control` process exits."*
- Resume window: *"These commands work for about four hours after the server stopped. After that, run `claude remote-control` to start a new session."*
- Survives sleep: *"if your laptop sleeps or your network drops, Claude Code reconnects automatically when your machine comes back online."*
- Execution stays local; transcript stored server-side: *"Execution and filesystem access stay on your machine … the session transcript … is stored on Anthropic servers."*
- CI: no sentence on the page says it runs in CI or without a terminal. `grep` of the raw CHANGELOG (2.1.278 is head; file carries no dates) shows `claude remote-control` added in **2.1.51** ("local environment serving") and fixes since (server credential ~30-day re-registration, crashed-session revival) — nothing about headless/CI hosting.

### Background sessions — TRUE for "no terminal", local machine, dies on shutdown
Source: https://code.claude.com/docs/en/agent-view (read 2026-09-21); CHANGELOG 2.1.139 "Added agent view (Research Preview)", 2.1.144 `/resume` support for `--bg` sessions.
- *"Background sessions don't need any terminal open to keep working. A separate supervisor process runs them, so you can close agent view, close your shell, or start a new interactive session and your dispatched work keeps going."*
- *"Sessions are also preserved when your machine sleeps. Their processes resume on wake."*
- *"**Shutting down still stops running sessions**"*; after reboot they show `failed` (<48h) or `stopped`, recover with `claude attach <id>`.
- Idle sessions are stopped by the supervisor after ~1h unattended; *conversation persists on disk*.
- scheduled-tasks page: *"Backgrounding the session carries `/loop` tasks over to a background session, which keeps running without a terminal."* (https://code.claude.com/docs/en/scheduled-tasks)

### Routines / cloud sessions — TRUE for "no machine at all", but a fresh clone each run
Sources: https://code.claude.com/docs/en/routines · /scheduled-tasks · /claude-code-on-the-web (read 2026-09-21)
- *"Routines execute on Anthropic-managed cloud infrastructure, or on your organization's self-hosted environment when routed there, so they keep working when your laptop is closed."* Triggers: schedule (cron, **minimum 1 hour**), API POST, GitHub events. Research preview.
- Comparison table (scheduled-tasks): Cloud — *Requires machine on: No · Requires open session: No · Persistent across restarts: Yes · Access to local files: No (fresh clone)*. `/loop` — *Requires machine on: Yes · Requires open session: Yes*; recurring tasks *"expire 7 days after creation."*
- Amnesia: *"Each run creates a new session"*; GitHub triggers: *"Claude Code doesn't reuse sessions across events."* Memory is whatever is in the cloned repo or connectors. Daily run cap per account; runs draw down the same subscription limits.
- Cloud sessions: *"The session keeps running after you close your laptop."* Idle VMs are reclaimed (*"Cloud sessions stop after a period of inactivity … Background work that was still running when the VM was reclaimed … isn't restored"*).

### What it means for W1 (uptime wedge)
"Runs while you're away with no terminal" **now exists first-party**, so W1 can no longer be sold on that sentence alone: background sessions (own box, no terminal, survives sleep) and routines (Anthropic's box, hourly cron) both do it. What still holds: background sessions die on shutdown and need manual `attach` recovery, Remote Control needs a live process (docs' own advice is tmux), routines are stateless fresh clones on Anthropic's machine with 1h minimum and a daily cap, and none of them is cross-vendor (no Codex, no two-subscription quota routing).
Reframe: W1 = "survives shutdown, keeps memory as files, on your own hardware and both vendors' subscriptions" — not "runs without a terminal".

**Caveat, not measured:** I did not run `claude --bg` through a reboot; "dies on shutdown" is the vendor's sentence, not my test. The docs page for agent-view says nothing about Remote Control/server use; I did not find first-party text tying `claude remote-control` to a supervisor.

---

## Check 2 — Reddit threads, by the cookie path

**Cookie path: works.** `/Users/gurio/Source/Projects/brnrd/.tmp/reddit_session.cookie` (playbook §"reddit cookie") + browser UA → `api/me.json` HTTP 200; search + thread `.json` all 200. Read only, nothing created in `home/account/` (no reddit file lives there; the cookie is in the repo `.tmp/`).

**Premise miss:** the growth report (§1 and §Open) names **no specific r/selfhosted / r/homelab / r/macmini threads** — only subreddit-level rows (sizes from gummysearch) and the Open bullet saying no thread-level quote exists. So I could not fetch "the three it names". Instead I ran Reddit search in each sub (`claude code`, `codex OR claude agent 24/7`, past year) and picked, **by my own judgement, the on-topic thread per sub**. Say so if you want different ones; the alternates are listed. Upvote counts are net score at fetch time; "top commenter" = highest-scored top-level comment (which is often off-topic — I add the most on-topic commenter).

### r/selfhosted
- URL: https://www.reddit.com/r/selfhosted/comments/1wak38t/
- Title: *Used Claude Code remotely from my phone to deploy and debug my home server - it's a game changer* · u/ghuntdo · 2026-09-08 · score 0 (ratio 0.11) · 18 comments
- Quotes:
  1. "Moved the stacks from `/home/user` to `/srv/stacks` - Claude Code's remote mode won't operate on a home directory." (OP)
  2. "the agent has Docker access (basically root) and a live Cloudflare token sitting on disk." (OP)
  3. "when you lose signal on the train, does remote-control leave the current Claude command running on the server or cancel it with the phone session?" (u/kantorcodes1)
- Top commenter: **u/Medium_Chemist_4032** (score 4) — off-topic-ish praise of agents as admin. Most on-topic: u/kantorcodes1 (score 1).
- Fit: Remote-control-on-a-home-server user, no persistence story; the DIY workarounds are a dedicated user, deny rules, a scoped token.
- Alternate, same sub: https://www.reddit.com/r/selfhosted/comments/1w65cmy/ (u/Lomchat, 2026-09-03, "Charon", 0 / 21 comments). u/MaxLo85 there: "if they fail, timeout, I need to access a file, a terminal, etc - I'm shit out of luck." — a Remote Control user hitting exactly the timeout/state wall.

### r/homelab
- URL: https://www.reddit.com/r/homelab/comments/1s0e8lp/
- Title: *been running claude code on my home server in docker, heres the setup* · u/CoderLuii · 2026-03-22 · score 0 (ratio 0.27) · 7 comments
- Quotes:
  1. "wanted to share my setup since i spent way too long getting this right." (OP)
  2. "credentials survive container rebuilds because they live in the bind mount. the only thing you redo after an update is the web ui account which takes about 10 seconds." (OP)
  3. "I run Claude Code on a Hetzner VPS with Tailscale and use tmux to keep sessions alive." (u/rjyo)
- Top commenter: **u/zer00eyz** (score 2) — describes a gitea + monorepo workflow, not persistence. Most on-topic: u/rjyo (score 1, builds Moshi, an iOS Mosh terminal — a competitor-adjacent DIY, promotes own app).
- Fit: weaker — six months old, low score, container-setup rather than a death complaint. r/homelab's search returned nothing better on "agent stays alive"; the highest-scoring Claude hits (1sjiyik 4304 pts, 1svgfob 1825) are about homelabs in general, not agents.

### r/macmini
- URL: https://www.reddit.com/r/macmini/comments/1sct1zf/
- Title: *Using my mac mini as a dedicated AI agent server and its honestly perfect for it* · u/virtualunc · 2026-04-05 · score 84 (ratio 0.82) · 41 comments
- Quotes:
  1. "install amphetamine to prevent sleep, grab an hdmi dummy plug from amazon if youre running headless (macos throttles performance with no display connected for some reason)" (OP)
  2. "the agent remembers everything across sessions and builds reusable skills from tasks its done before so it gets more useful the longer you leave it running." (OP)
  3. "What is something you can't do just by having Claude Code write python scripts and call models of your choice via local/openrouter llm?" (u/jambon3, score 13 — the sceptic)
- Top commenter: **u/Any_Weekend2084** (score 14, "What are you guys using these agents for?"). Most on-topic: u/Deep_Ad1959 (score 9, the WindowServer/dummy-plug explanation).
- Fit: the always-on Mac mini is the canonical host, but the agent here is **Hermes**, not Claude Code/Codex; memory-across-sessions is what the OP praises. Alternates: /comments/1qzxvcz (OpenClaw, 45 pts, 57 comments, u/118fearless, 2026-02-09 — top comment "Cheap on power offset by API costs", u/The_Airwolf_Theme, 12); /comments/1rrucj7 (u/Ugara95, "Moved my AI agents to a dedicated mini-box 24/7").

### What Check 2 does not settle
None of the three is a "my agent died / lost memory" complaint in the R8/R14 register from 09-07; they are setup showcases with DIY workarounds (tmux+Tailscale, dummy plug, deny rules). The 09-07 page's r/ClaudeAI / r/ClaudeCode threads remain the stronger problem-statement evidence; these three are better as *where the qualifier is met* than as *pain quotes*.

---

## Open

- **Which three threads did the growth report mean?** It names none. If a specific list existed in the commissioning brief, send the URLs; the fetch path is proven and takes seconds.
- **Reboot behaviour of `claude --bg` not measured** (only quoted). One test on the Mac mini would turn "shutdown stops them" from vendor claim to receipt.
- **No first-party statement found on memory continuity between background sessions** beyond "conversation persists on disk" — check `claude attach` after reboot before claiming brnrd's memory is the differentiator there.
- **Routine self-hosted environments** (org-routed, "self-hosted-environments" page) were not read; they may narrow the "Anthropic's box only" claim for Team/Enterprise. Worth one fetch before the positioning decision.
- **Score/date drift:** Reddit numbers are live at fetch time; the r/selfhosted post 1wak38t shows ratio 0.11 (heavily downvoted, likely self-promo-adjacent) — don't cite it as endorsement.
- Report §Open's other items (five unverified subscriber counts, handles) untouched.
