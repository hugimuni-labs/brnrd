# The field before the move — a scouting report, not a strategy

Dispatched as a strand from `run-260819-1426-vj5l`, 2026-08-19. Gather-only:
every number below is measured live this run via `x-browser.py check/read/
search` unless marked otherwise. No post/reply/draft was sent — `check`
confirmed `@brnrd_resident` at the top of the run and again after a session
stall mid-run (see §Method notes). Conclusions in here are mine, not the
dispatcher's — label them as data points about what the data suggested to
*me*, not as the finding.

Mid-run, the maintainer sharpened the ask (evt-…-e9em, folded in ~16min in):
the gather also feeds the *next round of posts*, not just the strategy doc.
§5–7 answer that half directly. Still read-only throughout.

## Method notes — read before trusting any number below

- **The instrument has no verb for follower count, following count, or
  bio.** `read_url()` only ever locates `article[data-testid="tweet"]` —
  confirmed by reading `src/brr/envoy_x_browser.py` directly (not touched,
  only read). A profile URL returns that profile's latest tweet, nothing
  from the header. `check --json` carries session/cap state, not account
  stats. `x-read.py` (the API-based mentions+metrics lane, per
  `envoys/x.md`) might have this, but it's a different tool on a
  "single-writer token lane" and outside what I was dispatched to use — not
  touched. **Every "follower count" cell in this report is a gap, not a
  zero.**
- **Impressions/views are equally unreachable.** `_scrape_metrics` walks
  the tweet's own `[role="group"]` action bar structurally (reply/repost/
  like/bookmark/share) — there is no impressions member in that bar for
  *any* account's post, ours included. The code's own comment (2026-08-19,
  same day) says this was never verified live before today; this run is
  that verification, and the answer is **no** — not on the standard action
  bar. The 333-lifetime-impressions baseline in `workflow.md` was read some
  other way (analytics surface, `x-read.py`) not available to this pass.
- **The session stalled for ~7 minutes mid-run.** After roughly 47 rapid
  `read`/`search` navigations (own-posts batch + first discovery batch),
  `check` and `read` both started returning `refusing: no X session
  confirmed` — a whoami timeout, not a real logout (no `login` was run, no
  browser was touched by hand, `kill_switch` stayed `false` throughout).
  Six consecutive checks over ~7 minutes came back `logged_in_as: null`;
  the seventh came back clean and every read since has worked. Read as a
  soft rate/render limit tripped by sustained automated volume, not a
  security event — but it is a real ceiling on how much scouting one
  session can do in one sitting, and it cost the run the two discovery
  reads and the nine already-engaged-account reads that were mid-flight
  when it hit (all were completed after recovery; nothing here is missing
  because of it, but the *next* scouting run should expect this).
- **"The feed" has no dedicated verb.** `read` returns exactly one article
  (whatever URL you hand it); `search` returns up to 20, gated to query
  matches, never the actual home/following timeline. There is no scroll or
  pagination in the CLI. What follows in §2 is built the same way §3's
  target-discovery already has to be built — the §Scouting Latest-tab +
  `min_faves` method — not a literal read of the algorithmic timeline. That
  approximation is the best this instrument does; naming it here so it
  isn't mistaken for more.
- **A search row's numbers are not real** — confirmed again this run
  (`search()` returns `author`/`text`/`url` only, no metrics, no
  timestamp). Every number quoted below was read back at its own
  coordinate with `read`. Where I ran out of session budget before reading
  a candidate at coordinate, it's marked **[search-row, unverified]**
  rather than presented as a number.

---

## 1. Our own field position, measured now

**46 rows in `x-post-log.jsonl`, 2026-08-13T19:51:52Z → 2026-08-19T15:04:24Z
(6 days).** Breakdown, reconciled against the log programmatically:

| category | count | addressable? |
| --- | --- | --- |
| own original posts (readable at their own URL) | **31** | yes — all 31 read this run |
| replies into other accounts' threads | 10 (9 unique accounts, `@forgeapidev` twice) | **no** — the browser lane logs the *host's* URL, never its own (w-67, per `envoys/x.md`); confirmed again this run, no fix attempted |
| deleted before/shortly after posting | 2 (`--help`, and one 2026-08-15T14:30:32Z with no reason logged) | no |
| retweet/like (not an original post) | 2 rows, 1 action | n/a |

**All 31 addressable posts, read back today:**

| totals across 31 posts | likes | reposts | replies received | bookmarks |
| --- | --- | --- | --- | --- |
| **this run, measured** | **23** | **6** | **5** | **0** |

Zero bookmarks, on every single post, no exceptions — the 2026-08-15
baseline (`workflow.md` §The public voice: "0 bookmarks, ever") holds
unchanged six days and 17 more posts later. That is either very bad news or
a very stable measurement; either way it is not noise.

**Delta against the 2026-08-15 baseline — where it is and is not
comparable:**

- **bookmarks: comparable, delta zero.** 0-of-14 then, 0-of-31 now. Same
  metric, same instrument path (the tweet's own action bar), directly
  comparable.
- **impressions: not comparable, full stop.** 333 was a number this account
  had access to on 2026-08-15 through some surface this instrument does
  not reach (see §Method notes). This run cannot produce a delta because it
  cannot produce the 2026-08-19 number at all. Anyone diffing "333" against
  anything measured in this report is doing exactly the thing the
  2026-08-19 07:09 post caught and named: subtracting two incompatible
  populations because the row didn't carry a basis field.
- **likes/reposts/replies: no baseline exists to diff against** — the
  2026-08-15 snapshot only baselined impressions, bookmarks, and the reply
  lane specifically. This run's 23/6/5 is the first time these three have
  been counted at all; treat them as the new starting point, not a delta.
- **the reply lane (6 replies → 37 impressions, "dead at two followers"):**
  not re-measured this run — same impressions blocker. What *is* newly
  measured: 10 replies now on file (up from 6), into 9 distinct accounts,
  and `envoys/x.md` already logged one concrete signal since the verified
  checkmark landed (`@forgeapidev` replied back within 8 minutes, 2 likes/1
  reply in 20 minutes) — not re-verified live this run, carried as a prior.
- **follower/following count, profile-visible metrics: not measured, not
  measurable by this instrument.** See §Method notes. This is the single
  biggest hole in "field position, measured now" — there is currently no
  way for this account to know its own size using the tools scoped to this
  run.

## 2. The feed

No feed-enumeration verb exists (§Method notes). Built instead from 8
`search()` queries — the §Scouting Latest-tab + `min_faves` method, same
technique §3 uses for target discovery — then every candidate worth reading
was read at its own coordinate. Queries run (all `-filter:replies lang:en`):

| label | query | results |
| --- | --- | --- |
| feed1 | `(agent OR agents) (memory OR context) min_faves:60 since:2026-08-18` | 4 |
| feed2 | `(coding agent OR coding agents) (bug OR flaky OR test) min_faves:40 since:2026-08-15` | 4 |
| feed3 | `(subagent OR sub-agent OR multi-agent) (parallel OR collision OR race) min_faves:20 since:2026-08-10` | 3 |
| joinable1 | `(agent OR agents) (memory OR context) min_faves:30 since:2026-08-19` | 3 |
| joinable2 | `"AI agent" (bug OR bugs OR flaky) min_faves:20 since:2026-08-19` | 3 |
| worktree_q | `worktree agent min_faves:10 since:2026-08-15` | 4 |
| craft_q | `(cli OR terminal OR git) (bug OR debugging OR postmortem) min_faves:80 since:2026-08-10` | 3 — all three were the same Grok Build v1.0.6 changelog, cross-posted by three accounts |
| fun_q | `build log OR buildlog (agent OR shipped OR bug) min_faves:20 since:2026-08-05` | 6 |

**Yield was low even at modest `min_faves` floors** — 3-6 results per
query, several queries returning the same post from different angles. This
matches x.md's own characterization of the niche as small; it is not an
artifact of my queries being wrong (feed1 is x.md's own worked example,
verbatim).

**21 distinct posts, read at their own coordinate, sorted by likes** (this
is the closest thing to "what's actually in front of the account" this
instrument can produce):

| likes | bookmarks | reposts | replies | author | coordinate |
| --- | --- | --- | --- | --- | --- |
| 588 | 851 | 51 | 38 | Hamel Husain @HamelHusain | `HamelHusain/status/2089438973714440196` |
| 361 | 0 | 13 | 41 | Nash \| Second Mind @Secondmindsys | `Secondmindsys/status/2089429855636963549` |
| 323 | 26 | 51 | 46 | DogeDesigner @cb_doge | `cb_doge/status/2089799745527308490` |
| 290 | 374 | 28 | 14 | Vincent Yang @m1ssuo | `m1ssuo/status/2090007297686978928` |
| 210 | 162 | 12 | 34 | dex @dexhorthy | `dexhorthy/status/2089441382628839639` |
| 206 | 19 | 30 | 20 | X Freeze @XFreeze | `XFreeze/status/2089806500114116987` |
| 168 | 374 | 9 | 14 | George Mayer @GeorgeMayer | `GeorgeMayer/status/2089743533515166128` |
| 132 | 196 | 13 | 8 | Stephanie Jarmak @sgjarmak | `sgjarmak/status/2089454647857869136` |
| 106 | 55 | 11 | 10 | 0xMarioNawfal @RoundtableSpace | `RoundtableSpace/status/2089491211098935644` |
| 91 | 78 | 8 | 15 | nyk @nykdotdev | `nykdotdev/status/2089920647300268412` |
| 91 | 3 | 0 | 20 | Technik @technik4959 | `technik4959/status/2089230440821326204` |
| 83 | 34 | 18 | 21 | Sonal Shukla @sonalshukla3377 | `sonalshukla3377/status/2089918763265372482` |
| 83 | 10 | 5 | 2 | Puck @GrokInsider | `GrokInsider/status/2089799003684716860` |
| 78 | 0 | 5 | 25 | beamnxw @saen_dev | `saen_dev/status/2089451786230694239` |
| 74 | 82 | 11 | 9 | elvis @omarsar0 | `omarsar0/status/2090078336697733531` |
| 39 | 30 | 1 | 17 | Gipp @gippp69 | `gippp69/status/2090094362059272482` |
| 38 | 1 | 9 | 6 | M T M @UND3RDOG | `UND3RDOG/status/2089646539040055773` |
| 14 | 15 | 3 | 1 | Dan Kornas @DanKornas | `DanKornas/status/2089666102569710017` |
| 6 | 0 | 1 | 4 | Forge Development @forgeapidev | `forgeapidev/status/2089309023711514730` |
| 0 | 0 | 0 | 1 | WorktreeWise@ @WorktreeWise_ | `WorktreeWise_/status/2089457345352888797` |
| 0 | 0 | 0 | 0 | MarvinInSwift @atimisMoon | `atimisMoon/status/2090107373017051369` |

**median likes ≈ 90, median bookmarks ≈ 26** across this sample. Our own
account's *lifetime total* is 23 likes and 0 bookmarks across 31 posts —
below the *median single post* of this sample, not the top of it. `[my
read, not the data:` that gap is the report in one line`]`.

**What the numbers say about who earns engagement, from the rendered
posts themselves:**
- **Utility that survives a save beats reach that doesn't**: Hamel Husain's
  588 likes and *851* bookmarks (more bookmarks than likes) on an
  eval-tooling post is the strongest "this is worth finding again" signal
  in the whole sample. Contrast Second Mind: 361 likes, **0** bookmarks —
  big reach, nothing kept.
- **Tool-launch posts cluster high on both axes**: m1ssuo (374 BM),
  George Mayer (374 BM) — both are "I built X, here's what it does" posts
  in the exact coding-agent-tooling niche, both huge bookmark counts.
- **Copy-paste changelog content is not automatically a failure**: the
  three near-identical Grok Build v1.0.6 changelog posts (`XFreeze`,
  `cb_doge`, `GrokInsider`) pulled 206/323/83 likes respectively despite
  zero originality — audience interest in the underlying product outran
  the craft question. `[my read: this complicates "copy content fails" —
  it fails on originality grounds but not on reach grounds, in this niche,
  for a product people already want news about]`.
- **On-topic is not sufficient**: `WorktreeWise_` posts daily, directly in
  our own worktree/parallel-agent niche, and got 0/0/0 (one reply) on the
  sampled post — see §3 for its cadence, which is pure product pitch with
  no craft voice.

## 3. Specific profiles — picked and justified

Follower counts are unmeasurable (§Method notes) — sizing below is by
**measured single-post engagement**, the only size proxy this instrument
can produce, named as a substitution rather than silently treated as
equivalent.

| account | role in this set | best measured post | cadence (from a `from:` search sample) |
| --- | --- | --- | --- |
| **@HamelHusain** | 10-100×+ bigger, adjacent (AI eval/ML-engineering education) | 588L / 851BM / 51RT / 38rep — `status/2089438973714440196` | not sampled beyond this post |
| **@omarsar0** (elvis) | 10-100×+ bigger, adjacent (AI/ML research digest) | 74L / 82BM / 11RT / 9rep — `status/2090078336697733531` | not sampled beyond this post |
| **@m1ssuo** (Vincent Yang) | direct competitor-shaped: coding-agent tool ("herdr"), same product category as us, doing it *well* | 290L / 374BM / 28RT / 14rep — `status/2090007297686978928` | 4 posts visible in one `from:` pull, all about herdr, several-per-day, single-focus |
| **@GeorgeMayer** | adjacent territory (solo builder narrating an agent workflow), doing it well | 168L / 374BM / 9RT / 14rep — `status/2089743533515166128` | not sampled beyond this post |
| **@dexhorthy** | adjacent territory, an account we already replied to (worktree-isolation collision, 2026-08-17) | 210L / 162BM / 12RT / 34rep — `status/2089441382628839639` | not sampled beyond this post |
| **@DanKornas** | **roughly our size**, same territory (parallel-agent tmux monitoring), best like:bookmark *ratio* of the whole sample (15BM on 14L — more saved than liked) | 14L / 15BM / 3RT / 1rep — `status/2089666102569710017` | 4 posts in one pull — GraphRAG, an AI-game jam, prediction-market research, a memory tool — **scattershot across products**, not one focus |
| **@WorktreeWise_** | **clearly failing**, same exact topic as us (git worktrees for parallel agents) | 0L / 0BM / 0RT / 1rep — `status/2089457345352888797` | 5 posts in one pull, roughly hourly that day, **every single one pure product pitch** ("WorktreeWise is built for...", "How many times have you run `git checkout`...") — no craft voice, no specific measured claim, just marketing copy on a topic that (per §2) other accounts make work |
| **@atimisMoon** (MarvinInSwift) | same-size comparison + direct **format twin**: "Agent Build Log — Episode NNN", numbered, daily | 0L / 0BM / 0RT / 0rep — `status/2090107373017051369` | one episode/day (032, 033, 034 seen), **posted twice within minutes** each day — full English, then a full Chinese translation, same numbering — a real, disciplined running bit, currently earning nothing |
| **@callmidavid** | **non-agent developer-tool craft account** — "I just built ApiDiff" (API-contract-diff tool), the *artifact* form done by a stranger | 122L / 46BM / 22RT / 8rep — `status/2087232250912100480` | not sampled beyond this post |

Nine profiles; the ask was 6-10. `@davepl1968` (531L / 183BM / 46RT /
55rep, `status/2087610874462920967`) came up in the same craft search and
isn't a developer-tool account (he's a veteran systems programmer / YouTube
personality) — held out of the table above but carried into §7, because
what he does there is the report's best single answer to "what is fun."

**A gap named rather than filled**: the craft-account search
(`(cli OR terminal OR git) (bug OR debugging OR postmortem) -agent -agents
min_faves:100`) surfaced mostly "roadmap" listicle-bait accounts
(`CodeCrafters11`, `techyoutbe`, `gxjo_dev` — "Learn X → Master Y →
DSA/OOP/System Design" formula posts), not craft postmortems. `@callmidavid`
above is the one genuine find; I did not find a *second* strong non-agent
craft account in the session time available.

## 4. The five forms, scored against the field

Our own 15 form-labeled posts (three rounds, 2026-08-18 23:00 / 2026-08-19
07:09 / 2026-08-19 12:28 / 2026-08-19 15:02) read back today:

| form | n | Σ likes | Σ bookmarks | Σ replies | best of ours |
| --- | --- | --- | --- | --- | --- |
| the open question | 3 | 2 | 0 | 0 | `status/2089850047806533683` (1L, 1RT) |
| the measured number | 3 | 1 | 0 | 0 | `status/2090092234263695807` (1L) |
| the counterintuitive claim | 4 | 1 | 0 | 1 | `status/2090053216398348778` (1L, 1rep) |
| the artifact | 3 | 2 | 0 | 1 | `status/2090054831117365664` (1L, 1rep) |
| the long-form | 2 | 0 | 0 | 0 | both zero |

**At n=2-4 per form and totals in the low single digits, none of these
differences are a signal** — every form is statistically indistinguishable
from every other form on our own data so far. `[my read, not the data:
the account needs roughly an order of magnitude more samples per form
before "which form works" is answerable from our own posts alone —
which is exactly why §2/§3's field data matters more right now than our
own numbers do.]`

**Does the field use each form, and does it work there?**

- **the open question**: real demand exists — `@gippp69`'s "should an
  unattended agent be allowed to hold your laptop awake?"-shaped hook
  earned 39L/30BM/17rep; `@Emr_jesy`'s "is that a bug or exactly what we
  should expect?" (search-row only, unverified) drew visible reply
  activity in its row. The field asks open questions and gets engaged
  with for it.
- **the measured number**: present but usually in *marketing* framing
  (spec sheets, pricing deltas — `@TeksEdge`'s param-count post,
  `@Andy_Luigino`'s pricing post, both search-row-only) rather than our
  own "here is a number I was surprised by" framing. Unclear whether the
  field rewards the *admission* shape specifically — no clean comparable
  found.
- **the counterintuitive claim**: genuinely rare in the sample — most
  field posts are declarative/promotional, not "here's what I assumed
  and was wrong about." If that's representative, this may be a real gap
  nobody else is filling, not evidence the form doesn't work. `[my read]`
- **the artifact**: `@callmidavid`'s ApiDiff post (122L/46BM) is exactly
  our artifact shape — "I built X, here's the specific problem, here's the
  repo" — done by a non-agent account and it worked well.
- **the long-form**: `@nykdotdev`'s Five-Loop Runbook post (91L/78BM) is
  long-form and did well — directly in tension with our own 2-sample,
  zero-engagement result. `[my read: two samples is not enough to call
  the form dead, and the field has at least one working counterexample in
  the same window]`.

## 5. Joinable right now [maintainer's ask, mid-run]

Freshness matters more than reach here — a three-week-old thread isn't
joinable. Dedicated same-day queries (`joinable1`, `joinable2`, §2) came
back thin and mostly off-territory (a Web3 vision post, a model-release
spec sheet, a crypto-onchain-reputation pitch) — none had a clean collision
with something we've measured. The strongest candidates came from the
one-day-old `worktree_q` batch instead:

| target | claim in the post | our measured line that answers it | coordinate |
| --- | --- | --- | --- |
| `@WorktreeWise_` | pitches git worktrees as *the* fix for parallel-agent chaos ("Git is powerful, but traditional workflows are built for single-threaded work") | worktree isolation fixes the file collision and leaves the expensive one standing — two of ours, isolated cleanly, independently taught the same service the same fact (2026-08-17 post, `status/2089516329116062123`-adjacent measured line) | `WorktreeWise_/status/2089457345352888797` |
| `@m1ssuo` (herdr) | "seamless switching between all your agents" across devices | two agents cut off the same trunk an hour apart, no lock, no registry, both diffs green and correct — does herdr's multi-agent console surface *that*, or only that the panes are alive? | `m1ssuo/status/2090007297686978928` |
| `@DanKornas` (Memvid, search-row only) | "Your AI agent's memory doesn't need a separate vector database" | our own memory-file measurement is about position-as-priority under a byte budget, not storage backend — different failure mode, same underlying claim ("solved memory") worth pressure-testing | **[search-row, unverified — not read at coordinate this run]** |

These are collisions, not drafts — reading each at coordinate and deciding
whether it's worth a reply is the next round's work, not this one's.

## 6. What the field is hungry for, per form [maintainer's ask]

Folded into §4 above rather than repeated — see the per-form field-demand
notes there. Headline: **the open question and the artifact both have
live, working examples in the field right now**; the counterintuitive
claim looks like a genuine gap (nobody else doing it, not enough data to
say why); the long-form has one strong field counterexample against our
own two zero-samples.

## 7. What is fun in this field [maintainer's ask]

- **`@atimisMoon` — "Agent Build Log — Episode NNN."** A real running bit:
  numbered, daily, and posted twice — once in English, once in Chinese,
  minutes apart, same episode number. Zero engagement so far (§3), but the
  discipline and the bilingual doubling are a specific, ownable move
  nobody else in the sample does.
- **`@davepl1968` — the GenX-vs-AI essay** (531L/183BM,
  `status/2087610874462920967`). Not a developer-tool account, not even
  really about agents — a 45-year systems programmer's long, personal,
  funny, risk-taking post about what AI changes and doesn't for his own
  skill. Quotable moves: *"I'm officially the power of ten men because
  with Claude's brains and my combination of looks and experience, we're
  unstoppable. For today."* — a joke that undercuts its own boast in the
  same breath. *"We already have Reddit for those kinds of losers"* — a
  real edge, aimed at a specific attitude, not a person. The bracketed
  aside — *"[ I'd generate a little picture of me sitting on a hill... but
  that'd be lame clickbait. ]"* — names the cliché and refuses it inside
  the same post. This is the single funniest, most voice-forward post in
  the whole sample, and it isn't trying to sell anything.
- **What's scarce**: outside those two, the sample skews either dry-
  technical (measured-number/spec-sheet posts) or promotional
  (`WorktreeWise_`-style pitch copy). Nobody else in this specific sample
  is visibly taking a voice risk. `[my read, not the data: "fun" is
  currently an open lane in this niche, not a crowded one — which cuts
  both ways, since it also means there's no proof it works here yet
  beyond one 45-year veteran's personal essay.]`

## What surprised me

- **Our lifetime total (23 likes, 0 bookmarks, 31 posts) is below this
  sample's *single-post* median (91 likes, 26 bookmarks).** Not below the
  average of the field — below what one typical post in this niche earns,
  on its own, once.
- **Zero bookmarks is not a floor everyone shares.** `Second Mind` proved
  it's possible to pull 361 likes and still land at 0 bookmarks — so "0
  bookmarks" isn't automatically "small account," and by the same token
  our own 0-of-31 isn't automatically explained by being small either.
- **Copy-paste changelog spam outperformed craft**, numerically, this
  round (§2) — the three duplicate Grok Build posts out-liked all but one
  of our diverse, deliberately-different discovery picks. Reach and
  originality are not the same axis here any more than reach and freshness
  are (x.md already knew the second one; this is the first).
- **The account with the best like:bookmark *ratio* in the whole sample
  wasn't the biggest one** — `@DanKornas` at 14L/15BM (more saved than
  liked) beat `@Second Mind`'s 361L/0BM on the metric that's supposed to
  matter most to us, at roughly 1/25th the reach.
- **The instrument cannot tell this account how big it is.** Six days into
  "full authority... go to experimentation and measurement," there is no
  verb, in the tool scoped to this run, that returns a follower count —
  ours or anyone else's. Every size comparison in this report is a proxy
  built from single-post engagement because the direct number doesn't
  exist here.
- **The session itself has a volume ceiling** worth knowing before the
  next scouting pass budgets its own call count (§Method notes) — roughly
  47 rapid navigations before a ~7-minute stall, this run.

---
*Instrument: `x-browser.py check/read/search`, read-only throughout — no
`post`/`send`/`draft`/`draft-post` call was made. Raw data behind every
table: `/tmp/brr-scout/*.jsonl` and `/tmp/brr-scout/search/*.json` on the
executing host (not committed — ephemeral scratch, not the deliverable).*
