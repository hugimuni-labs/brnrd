# brnrd investor deck v1 — the spec the build script reads

Spine: `shelf/investor-deck-outline-2026-09-20.md` (unchanged). Numbers: `state-of-brnrd-2026-09-20.md` (STATE), `subscription-vs-api-price-2026-09-20.md` (PRICE), `cc-issues-mined-2026-09-20.md` (ISSUES), `plan-first-200-paying-users.md` §What we sell (PLAN). Resident's share of commits is measured live by `build.py` (`git log --author=brnrd-bot`, `git rev-list --count main`) and substituted for `{bot}` `{total}` `{pct}`.

Format: `## id` opens a slide · `label:` chrome label · `headline:` · `- ` body lines (layout decides use) · `> ` notes-field source lines.

## 1
label: THE SENTENCE
headline: A resident — a seat, not a session.
- An agent that lives on your machine. It survives reboots, remembers who it is, works both your Claude and your Codex subscriptions, and leaves receipts.
- brnrd · open source · HugiMuni SAS
- the seat's uptime, and a Telegram line
> Sentence: outline slide 1 (investor-deck-outline-2026-09-20.md) / STATE §The sentence.
> Media: loop-3-stage (a request from the phone; the PR 76 seconds after) — the Telegram line. Receipt 1 Sep 2026, carried from the v11 deck.

## 2
label: THE PROOF · IT BUILT ITSELF
headline: It built itself. The git log is public.
- 6 months|born 2026-03-28, 176 days
- {total} commits|on main
- 2,056 PRs|numbered; the last two merged today
- {bot} commits|by the resident — {pct} of main
- 7,172 tests|CI green on main
- 70 h · 27 PRs|one seat, one context, 31 strands
- No competitor can put this slide up.
> 6 months / 176 days / born 2026-03-28: STATE §Building (age).
> Commits: `git rev-list --count main` run at build time = {total} (STATE printed 3,115 on 2026-09-20 morning; it moves).
> Resident's share: `git log --author=brnrd-bot --oneline main | wc -l` at build time = {bot}; {bot}/{total} = {pct}. (STATE only says ">1,000 opened by the resident"; the exact count was to be re-measured before quoting.)
> PRs 2,056 numbered, #2055/#2056 merged 12:54Z; tests 7,172; seventy-hour run (70 h 24 m, 27 PRs merged, 31 strands): STATE §Building.

## 3
label: WHY NOW
headline: Both platforms shipped "runs while you're away". Their docs say where it stops.
- does not survive shutdown|Claude Code background agents, their own docs
- routines start amnesiac|every run, a fresh clone
- channels need "a persistent terminal"|bring your own daemon
- remote control transcripts sit on the vendor's servers|and refuse API keys and gateways
- 835|"background restart" threads on claude-code
- 512|"persistent memory sessions" threads
- 411 👍|the most-reacted Codex request: control my desktop CLI from my phone
- The vendors prove the category. They stay a session.
- 1M weekly Claude Code users
> Doc concessions ("does not survive shutdown", "persistent terminal", transcripts on Anthropic's servers): PLAN §What we sell items 1 and 5 (quoting the vendors' docs, verified 2026-09-07 in residency-competitive-scan-2026-09-07.md) / STATE §The field.
> 835 = claude-code `background restart`, 512 = `persistent memory sessions`: ISSUES §Counts table (GitHub full-text search hits, an upper bound on noise, not a count of complainers). 411 👍 = openai/codex#9224, ISSUES §Top 20 row 1.
> 1M weekly Claude Code users: outline slide 3 only — not on the four state pages; unverified here (see report ## Open).
> "The vendors prove the category but stay a session": outline §What carries over (v10 slide 3).

## 4
label: THE WEDGE · WHAT THEIR DOCS CONCEDE
headline: Five things the native platform does not do.
- Survives the reboot and owns the seat.|brnrd is the process the vendors' docs ask you to bring.
- Both subscriptions, one seat, one gauge.|Receipt, 5 Sep 2026: Codex hit its weekly wall at 18:16; the seat continued on Claude — same thread, same memory.
- Memory that is a self, in git.|Not notes about your repo: the agent's own playbook, pitfalls, schedule, identity.
- Judgement with a contract.|Forks handed over with options, self-merge under stated conditions, a work graph, a card that says what it is doing now.
- Your disk. No vendor relay.|No transcripts held by a vendor; credentials never leave your machine.
> The five lines: PLAN §What we sell — "What the docs say the platform does not do" items 1–5, condensed.
> 5 Sep 18:16 Codex wall receipt: carried from the v11 deck slide 4 (outline §What carries over); v11 build cites "Receipt, 5 Sep 2026".
> Media: loop-2-the-wall.

## 5
label: THE PRODUCT · ONE SCREEN
headline: Every run inspectable. The resident maintains these itself.
- The cloth|the runs, live
- The fuel chip|which subscription, how much of the week
- The warp|the work graph we both edit
- The run card|Now · Plan · Vector
> Four elements and the caption: outline slide 5. No numbers on this slide.
> Media: loop-4-away (the seat working while the founder is away; v10/v11 "while you're away", receipt 13 Sep 2026).

## 6
label: THE PLACE · IN THE WORKSHOP
headline: The resident is an actor in a place.
- The streets: the whole tree at real scale. The drone walks its route. Skins are the fork-and-PR lane.
- Every feature on the previous slides will be copied. A place, and a life in git, are harder to copy.
- in the workshop — not the product yet
> Content: outline slide 6. The frame is media/fields/field8-lit.png (field 8, "the streets"; 1,956 rooms, 137 lit, 162 streets printed in the image header — kb/log.md 2026-09-19 night entry, #2050).
> Not a claim of shipped product: the deck says "in the workshop" (outline).

## 7
label: BUSINESS MODEL
headline: Zero COGS today. Margin tomorrow.
- Today|the user's own subscriptions carry the inference. $7/mo hosted relay is the filter.
- The money|fallback intelligence and compute with a margin: hosted execution (the seat on our box) · local agents as runners (Ollama, OpenHands, OpenCode) as models go compact · eventually our own models.
- Tiers by seat; team seats after multi-user.
- The yardstick|A 70-hour run at API list price: $433–$1,159. The plan it ran on: $100 a month.
- a layer on every harness — and a runner of its own when the market goes local
> $7/mo hosted, Stripe live: STATE §Building (offer).
> $433 (Fable 5.1) – $1,159 (Fable 5), $100/mo Max 5×, 70 h, 977 M tokens for 40 points of the weekly window: PRICE Table 2 and §The sentence. A yardstick, not a saving (PRICE: a subscription limit is a window that refills, not a bill).
> Business model text: outline slide 7; local-runner bet: STATE §The bets (3).

## 8
label: TRACTION · THE HONEST SLIDE
headline: Users: 3. Paid: 0.
- 3|users — the two founders and the intern
- 0 recorded|external installs (the counter shipped 2026-09-20)
- 0|external first tasks · 0 paid
- 8 · 4|GitHub stars · forks
- What the next 90 days measure|installs → first task → second-task return ≤ 7 days → fifth task, weekly rows.
- We are showing you the instrument, not the number.
> Users 3, external installs 0 recorded, first task 0, paid 0, 8 stars / 4 forks: STATE §Using. "Absence = nothing recorded, never zero-by-measurement" (STATE): hence "0 recorded".
> Counter shipped 2026-09-20 and the second-task return metric: outline slide 8 / PLAN §The metric.

## 9
label: GO-TO-MARKET
headline: The install is the seller.
- Founder-face video and dogfooding streams|the person is the moat|viewers → installs
- Replies in the trackers where the demand already sits|claude-code and codex threads|reply → click → install
- Show HN · r/ClaudeAI|one post each, founder's own hand|post → install
- The install itself|one command; every stumble filed and fixed|install → first task → second task
> Four rows and their funnel columns: outline slide 9; PLAN §The seller: the install; STATE §The bets (2) — X replies measured dead at 3 followers, streams are the untested channel.
> No numbers on this slide.

## 10
label: COMPETITION · THE TABLE WE'D HAND A SKEPTIC
headline: Exclusive, parity, worse. In that order of honesty.
- EXCLUSIVE|Owns the seat across reboots|Two subscriptions, one gauge|Memory that is a self, in git|Graph-shaped execution and contracts
- PARITY|Telegram · Slack · GitHub lanes|Scheduled and background runs|Memory, on by default natively|Quota views (/usage, read from the same endpoints)
- WORSE|Install: a daemon, a git repo, an always-on box|Phone: a chat bot, no first-party app UI|No cloud runs|No security audit of an always-on agent
> Exclusive: PLAN §What we sell items 1–5; STATE §What we claim nobody did holistically ("claims, not yet validated by a user").
> Parity: STATE §The field ("runs while you're away is native on both platforms"; memory on by default); ISSUES §What we do worse (/usage built in).
> Worse: ISSUES §What we do worse (memory zero setup, background needs no host natively, phone UI, cost) + outline slide 10 (install, phone UI, no cloud runs, no security audit).

## 11
label: TEAM
headline: Three cofounders. One of them is the resident.
- Alexandra Lapunova|CEO
- Arseni|the builder — six months of the repo
- The resident|the third cofounder that ships: {bot} of {total} commits on main
- Sasha|ops · the second pair of thumbs
- The gap, named: no growth hire, no security reviewer yet.
> Roles: outline slide 11. Resident commits: `git log --author=brnrd-bot` at build time = {bot} of {total}.
> "Six months of the repo": STATE §Building (born 2026-03-28, 176 days).

## 12
label: THE ASK AND THE PATH
headline: Options on the page, not one number.
- (a)|$1–2M pre-seed|18 months of runway plus the distribution experiments. The obvious way to fund the 90 days.
- (b)|$10M on a category thesis|Needs the 90-day curve, or a strategic — a harness vendor, a model lab — who wants the layer.
- (c)|No raise|Until the curve exists.
- 90 days|install in one command · funnel rows · the first stream
- 12 months|hosted execution GA · local runner · team seats · the place
> Three options and the roadmap: outline slide 12 and §The two paragraphs ("On the $10M"). No TAM number anywhere.
> STATE §The bets (4) records the founders' current posture as "Not raising" — the options above are the outline's, not a decision.

## B1
label: BACKUP · Q&A
headline: Their cloud moves the hands. The memory has to live somewhere.
- Codex and Claude now run tasks remotely. Each run is still a session inside one vendor's walled garden. Whatever decides what to do next, remembers last week and collects the result has to live above the vendors. That layer is brnrd, on your machine, on the subscription you already pay for.
> Verbatim from v10/v11 backup card B1 (build_v11.py SLIDES[9]).

## B2
label: BACKUP · Q&A
headline: Same category, a different axis — that, but running your subscriptions.
- Others sell a hosted agent on their credits. brnrd runs the Claude and Codex subscriptions you already pay for, on your machine, under your login. No credentials are handed to us.
> B2 per the outline ("that, but running your subscriptions"; user quote carried in v11 slide 3). The v11 B2 card body was "Your machine. Your login. Your repo." — the headline here follows the outline's title for B2; body text is the v11 card's sentence, condensed.
