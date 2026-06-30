# Plan: repo gardening — initial context, respawn model, imagery, kb/code sweep

**Status: executing — Tasks 1, 3.3, 4A, 4B done 2026-06-28/29; Task 2 partial (new slice + 2A + corrected 2B + live help probe + 2C substrate + 2D respawn consumer + 2E Activity implementation + 2F runner portal metadata + failure classification + automatic local fallback + quality escalation selector) done 2026-06-29; Task 4 proposal-scaffolding cleanup done for `decision-licensing-and-defense`, `decision-monorepo-structure`, `plan-env-fly-machines`, and `design-diffense` by 2026-06-30; Task 5 CS1-CS4 shipped by 2026-06-30.** The maintainer
asked this run to *evaluate and plan only*; a later run on a cheaper-but-capable
model (Sonnet) executes the plan. We are at an architecture crossroads where
**vessel / medium / runner / core** are mixed across configs, kb, prompts, and
code, plus a leaning to settle the imagery on *Armored Core* (Core/Shell) and to
build a cost-and-capability-aware respawn model. This hub holds four tasks.

- **Task 1 — initial-context reweave:** see
  [`plan-initial-context-reweave.md`](plan-initial-context-reweave.md) (the
  detailed file-by-file spec; the maintainer's most-important task).
- **Task 2 — informed respawn model:** Part 2 below; extends
  [`design-runner-cores.md`](design-runner-cores.md).
- **Task 3 — imagery / vocabulary:** Part 3 below (a naming decision; the
  maintainer invited pushback — given here).
- **Task 4 — kb + code gardening sweep:** Part 4 below.
- **Task 5 — control surface over the engine (the "dashboard"):** Part 5 below.
  The execution-model review found Task 2 shipped the *engine* without the
  *dashboard*; the two architecture forks it parked are now resolved
  ([`decision-account-centered-daemon.md`](decision-account-centered-daemon.md))
  and the work is sequenced in [`plan-control-surface.md`](plan-control-surface.md).

Companions: [`design-portal-grammar.md`](design-portal-grammar.md),
[`design-resident-boundary.md`](design-resident-boundary.md),
[`plan-cost-aware-runner.md`](plan-cost-aware-runner.md) (renamed from
`plan-cost-aware-cockpit.md`; Part 3), [`design-runner-back-channel.md`](design-runner-back-channel.md).

## Resolved by the maintainer (2026-06-28, evt-zyu6)

The three forks below ("What needs the maintainer") are now answered, plus one
new agreement on boundary quota. The Sonnet execution run builds on these — they
are decisions, not open questions.

1. **Vocabulary — adopted, with a refinement (Part 3.1).** Runner stays as the
   *whole entity behind a run* — "a resident, via a Shell and a Core on a wake,
   is the Runner." So most of the 271 `runner` code uses **stay** (the umbrella
   is real). Retire vessel + medium; adopt Shell (CLI) + Core (model); keep
   portal. **The one override of my earlier draft: the `runner=` *config toggle*
   goes.** We don't let the user *define a Runner*; the user defines **Cores and
   Shells**. So the user-facing knobs become `shell=` / `core=` (or a combined
   pin), not a `runner=` profile selector. Internal Runner/profile code and the
   umbrella concept remain; only the user-facing toggle is retired.
2. **Dispatcher hop (Part 2A) — confirmed the conservative shape.** The routing
   step is **skipped when a specific Shell (or Shell+Core) is pinned**. That
   exposes a simple interface for the main case — Telegram over a local CLI
   agent: pin the Shell, no routing hop. The cheap dispatcher runs only when
   nothing specific is set.
3. **Sequencing / chunking — confirmed.** Task 4 (and any large task) is chunked
   across follow-up wakes rather than one giant sweep.
4. **Boundary quota refresh + inject (new) — agreed, mostly already shipped; one
   latency fix remains.** See the next section.

### New slice: cheap boundary quota injection (the maintainer's point 1)

The maintainer: "daemon quota is wired into `portal-state.json` already; refresh
it on a boundary interweave trigger (hook fired) and inject into the boundary —
cost negligible, benefit substantial." **Agreed on principle and benefit, and
the *injection* half is already wired** — but with one correction worth acting
on:

- **Already done (injection):** `_emit_flush` (the boundary back-channel
  `.flush` trigger) already calls `_write_live_portal_state`, and the post-tool
  hook injects the `resources:` line — quota included — whenever
  `change_token` moved (`hooks.py` lines 232-242). So fresh quota *already*
  enters the weave mid-run, not only at seed/stop.
- **The correction (cost is not uniformly negligible):** the *negligible* part
  is **reading the cached snapshot**. The *refresh* itself is an ~18s **blocking
  PTY `/usage` scrape** (`claude_usage.load_or_refresh_snapshot`, TTL 300s). And
  that blocking scrape currently sits **on the boundary-flush critical path**:
  `_emit_flush` → `_write_live_portal_state` → `_collect_levels` →
  `load_or_refresh_snapshot`. So a tool-boundary flush can occasionally block the
  daemon ~18s when the cache is stale — which contradicts `_emit_flush`'s own
  "lighter / prompt, doesn't spam the card" design intent, and the pitfall rule
  ("hooks read the cached projection, never run the scrape").
- **The slice for the execution run:** keep injecting on the boundary (free);
  move the ~18s scrape **off** the flush critical path. Refresh the
  `claude_usage` cache on the heartbeat cadence (or a small background worker /
  the next-wake seed), and let `_emit_flush` only *read* the cached snapshot.
  Then "negligible cost at the boundary" is genuinely true. Net: point 1 is
  ~80% shipped; what remains is this latency-correctness decoupling, not new
  plumbing.

## Daemon-quota check (the maintainer's side-ask)

The maintainer restarted the daemon with the Claude quota-awareness changes and
asked me to verify. **It works, partially as designed:** this wake's
`portal-state.json` carries `resources.quota` = `known`:
"session 100% left (resets 12am Berlin); week 55% left (resets Jul 3)" — the
cached `/usage` PTY scrape rides into the wake. But `resources.spend` and
`resources.context_window` are **`absent`** at wake ("no … reading from this
medium yet"). That is the **structural boundary** already recorded
(`design-resident-boundary.md`): Claude spend/context are *terminal* (written
to the per-event outbox after `claude --print` exits), so they appear on a
run's closeout card, never in the *next* run's opening bundle. Codex, by
contrast, exposes live subscription quota from the on-disk session rollout.
**Net: Claude quota now rides the wake (good); Claude spend/context remain
closeout-only (by Anthropic's surface, not a brr bug).** Part 2 below treats
this asymmetry as a v1 constraint, not a thing to fix.

## Part 2 — Informed respawn model (Task 2)

The foundation shipped 2026-06-28 (`runner_media.py`: schema,
`implicit_medium`, conservative `select_medium`, `RespawnRequest`,
profile-borne metadata). The maintainer's Task-2 asks add five requirements on
top of `design-runner-cores.md`. Plan each as a slice for the execution run.

### 2A — Cheap dispatcher runner owns the user-facing knobs, then respawns
The maintainer's shape: **the initial wake runs on a cheap Shell/Core**; it
parses the user's intent and execution preferences ("run on Opus", "in half an
hour on Codex"), then **respawns** the real work onto the chosen Runner.
- This is the "first selector is deterministic and conservative; the resident
  escalates after reading the repo" principle already in
  `design-runner-cores.md` §Dispatch — but extended: the cheap runner is also
  the **knob parser**, not only a fallback.
- v1 keeps the *parked* `RespawnRequest` (no auto-chain until #128's
  `defer_until`/re-claim). The cheap runner emits a respawn request naming the
  target Runner + carry-forward context; the daemon (or a user nod) starts it.
- **Resolved (maintainer, evt-zyu6):** the dispatcher hop is **skipped when a
  specific Shell (or Shell+Core) is pinned** — the simple-interface main case
  (Telegram over a local CLI agent). The cheap dispatcher runs only when nothing
  specific is set. Keep low cognitive load: the user pins `shell=`/`core=` for
  the direct case, or sets intent in plain words and brr routes.

### 2B — Extract available models from the Shell itself (no hardcoded staleness) — **corrected 2026-06-29**
The maintainer wants brr to pick up a new model release on an installed Shell
without a brr update. Plan:
- Shipped foundation: `runner_cores.py` holds the bundled Core registry, tagged
  with model/provider/class/cost/freshness, plus `available_cores()` for CLI
  display and `cores_for_shell()` for Shell-filtered inspection.
- Corrected selector wiring: `runner.resolve_runner()` now reads a merged
  selection view: active `runners.md` profiles plus generated invokable Core
  profiles derived from the registry (`claude-haiku`, `codex-mini`, etc.).
  Generated profiles are created only for Shells declared in the active
  `runners.md`, so a project-owned profile file does not unexpectedly re-enable
  bundled Shells it omitted. Auto mode prefers those concrete Core profiles over
  model-less base Shells; `shell=` still exact-pins the base Shell/profile; and
  short `core=` aliases such as `core=haiku` match generated Core profile names.
- Generated Core profiles carry real commands: the model flag is inserted into
  the base Shell command, hooks/quota metadata are inherited from the Shell, and
  the daemon uses the same metadata for the `resources.runner` portal block.
- **Best-effort live probe shipped 2026-06-29:** `runner_cores.py` now runs a
  short local help probe for declared Shells and materializes any model-ish
  tokens it exposes as generated Core profiles. The bundled registry and
  project overrides remain the fallback and authority. Still not done: a stable
  per-Shell model-list command/API; when a CLI's help does not expose model
  choices, brr cannot discover new Cores without registry/project data.

### 2C — Capability-aware selection (swe-bench / terminal-bench), cached — **substrate shipped 2026-06-29**
The maintainer wants cost **and capability** awareness, ideally from a
benchmark. Plan:
- **Shipped substrate:** `runner-capabilities.json` is packaged as a small
  source/freshness-tagged cache keyed by model id; `runner_capabilities.py`
  loads it without network I/O and can derive the coarse
  economy/balanced/strong class when benchmark scores exist.
- Generated Core profiles now carry capability metadata
  (`capability_score`, `capability_source`, `capability_freshness`) through
  `RunnerProfile` and the `resources.runner` portal block. Hand-set `class`
  remains authoritative; capability-derived class is only the fallback when the
  Core entry has no explicit class.
- **Trusted score population shipped 2026-06-29:** bundled rows now carry exact
  Vals SWE-bench Verified scores where the benchmark row matches the Core, plus
  verified Terminal-Bench 2.0 rows only when the agent matches the Shell
  (`Codex CLI` for `gpt-5-codex`, `Claude Code` for Haiku). Non-exact,
  unverified, or missing rows stay `null` with provenance explaining the gap.
  Refresh policy remains open.
- **Pushback/caution:** benchmarks go stale and game-able; treat them as a
  *hint to the class assignment*, never a hard selector. The deterministic,
  conservative selector stays the floor (no revived LLM triage). Recommend
  shipping 2B (model discovery) before 2C (scoring) — discovery is the
  load-bearing half; scoring is polish.

### 2D — Scheduling-aware respawn — **contract + daemon consumer shipped 2026-06-29**
"Run in half an hour on Codex" = a scheduled respawn. This already has a home:
the dominion `schedule.md` (`at:`/`every:`) and #128's `defer_until`. Plan: the
`RespawnRequest` gains an optional `at:`/`defer_until` so a respawn can be both
Runner-routed and time-deferred. No new mechanism — compose the two existing
ones. **Shipped:** `RespawnRequest` now carries optional `at` and `defer_until`
fields, the outbox parser recognises `respawn: true`, and the daemon queues a
new event carrying the requested `shell=` / `core=` plus optional
`defer_until`. The current run records `respawn` as a success signal instead of
falling into no-output failure.

### 2E — Show running + scheduled runs on the brnrd overview — **read-only implementation shipped 2026-06-29**
`plan-brnrd-dashboard-mvp.md` has **no run-listing view today** (grep: none).
The presence registry (`presence.py`) and schedule (`schedule.py`) hold the
data locally. Plan: add an **"Activity" view** to the dashboard inventory
(running runs from the presence/run registry; scheduled wakes from schedule
entries + parked `RespawnRequest`s). This is a dashboard slice to add to
`plan-brnrd-dashboard-mvp.md`'s view inventory, consuming the brnrd protocol —
flag it there so the dashboard plan owns the UI and this plan owns the data
contract (what a run/scheduled-wake record must expose). **Shipped:** brnrd now
has `PUT /v1/daemons/activity` for daemon snapshots,
`GET /v1/accounts/activity` for account reads, `/activity` in `brnrd_web`, and
cloud gate snapshot publishing from run manifests, resident schedule entries,
and parked respawn events. Activity uses the accepted repo-first `repo_id`
vocabulary. Later mutation actions (cancel / reschedule / approve respawn)
remain future protocol slices.

### 2F — Portal/structured-state upgrade (already sequenced) — **runner metadata wired**
`design-runner-cores.md` step 3 ("replace flat `resources.quota` string with
structured `runner_media`") and its "Standing portal candidates" are the
governance-exposure half (the maintainer's "expose selected medium/cost/quota
in the card"). Keep that sequence; rename `runner_media` → `runner`/`core` per
Part 3. **Current state:** `portal-state.json` already carries
`resources.runner`; this run fixed the generated-Core path so the block can show
the selected Core's model/class/provider/hooks/cost metadata instead of only the
base Shell name. The compact hook line still renders the wall/state facets, not
the governance block.

### 2G — Failure classifier — **shipped 2026-06-29**
The daemon now classifies runner failures into timeout, quota exhaustion,
auth error, provider error, generic runner error, or clean no-output. The
classification rides both `attempt_failed` and terminal `failed` packets, and
the card labels quota/auth/provider failures distinctly instead of collapsing
them into `runner_error`. The prior "session limit" failure mode is now
`quota_exhausted`.

### 2H — Automatic local fallback policy — **shipped 2026-06-29**
The daemon now consumes the 2G classifier for a first automatic recovery loop.
When a runner fails with `quota_exhausted`, `auth_error`, or `provider_error`,
brr retries the same run in the same prepared worktree on a conservative local
fallback Runner when one exists. The policy excludes paid relay profiles, avoids
silent cost escalation by requiring the fallback to be in the same or a cheaper
class than the failed Runner, and avoids likely-repeat domains (quota/auth
failures use quota source first, provider second; provider failures require a
different provider). The run packets record the decision:
`attempt_failed` carries `will_fallback` + `fallback_runner`, and the following
`retrying` packet carries `from_runner` + `runner`, so cards can show the switch
instead of a generic retry. Paid relay consent, wallet/cap enforcement,
quota-reset deferral remains a separate slice — see
[`plan-relay-spend-consent.md`](plan-relay-spend-consent.md) for the spend-plan
data model, consent gate, and the deferred wallet/billing slices (5–7).

### 2I — Resident-authored quality escalation — **shipped 2026-06-29**

Quality escalation now stays in the intended lane: the daemon does **not** infer
task difficulty from event text and does not silently spend more during first
selection. Instead, a resident that has read the repo can park a respawn with
`respawn: true` and either an explicit `shell:` / `core:` or
`quality: escalate` / `quality: strong`. The daemon resolves that quality request
through a deterministic local selector: prefer the cheapest available strong
local Runner, fall back to the cheapest strictly stronger local Runner when no
strong profile exists, and never select paid relay.

`portal-state.json` now exposes the resolved candidate at
`resources.runner.quality_escalation`, so the running resident can see the
policy-owned target rather than inventing a profile name from memory. The
respawned event records `respawn_quality=strong`, keeps the same conversation
metadata, and remains a normal parked handoff rather than an automatic retry.

## Part 3 — Imagery & vocabulary (Task 3) — decision + pushback

The maintainer invited pushback on two fronts. Here is my judgement (he sent
"judgement." as the trust mandate). **These are reversible naming calls; I
recommend adopting them and flag them for veto.**

### Term sprawl, measured (2026-06-28)
| term | code | prompts | kb | verdict |
| --- | --- | --- | --- | --- |
| runner | 271 | 35 | 1056 | **keep** (umbrella) |
| medium | 35 | 8 | 174 | **retire** → Runner/Core |
| vessel | 20 | 2 | 17 | **retire** → Runner/Core |
| core | 8 | 0 | 92* | **adopt** = the model |
| shell | 14 | 0 | 60* | **adopt** = the CLI |
| portal | 71 | 8 | 286 | **keep** (genus) |
| viewport | 0 | 0 | 0 | **adopt only as the inbound sub-type** |
| cockpit | 1 | 0 | 138 | **retire** (already settled, never swept) |

\* `core`/`shell` counts are mostly incidental ("core idea", shell commands) —
confirm with context-grep before mass-rename so we don't clobber unrelated use.

### Recommendation 3.1 — Runner = Shell + Core; retire vessel & medium
Adopt the Armored Core frame, with one correction to the maintainer's phrasing:
- **Resident** = the persistent spirit/identity (the "semantic silkworm").
  *Keep this word* — it already names the entity the maintainer called the
  silkworm. **Do not move "runner" onto the spirit:** we already have
  "resident", and overloading "runner" would orphan 271 code uses and the
  `runner=` knob.
- **Runner** = the *executing body* for one thought (the mech). It is composed
  of:
  - **Shell** = the CLI program on PATH (`claude`/`codex`/`gemini`) — the
    carapace that gives the Core hands (file ops, tools, hooks).
  - **Core** = the model (`opus`/`sonnet`/`gpt-5-codex`) — the swappable reactor.
- A **profile** in `runners.md` names a Runner = a Shell (+ optional pinned
  Core) + selection metadata. The cost-aware layer selects **Cores within
  Shells**. Rename `runner_media.py` → `runner_select.py` (and the page
  `design-runner-cores.md` → `design-runner-cores.md`); `RunnerMedium` →
  `RunnerProfile` or `Runner`.
- **Config (maintainer override, evt-zyu6):** the `runner=` *user-facing toggle*
  is **retired**. We don't let the user define a Runner; the user defines
  **Cores and Shells**, so the knobs become `shell=` / `core=` (or a combined
  pin). The internal Runner/profile code and the umbrella concept **stay** (the
  Runner is the whole entity behind a run — resident · Shell · Core). The
  `model:` field is "the Core". Pinning a `shell=`/`core=` is also what skips the
  dispatcher hop (Part 2A) — the two decisions compose into one user knob-set.

This dissolves D1 (the triple-naming) with the least churn: "runner" — the most
entrenched, accurate-enough word — survives; only the two redundant
imports (vessel, medium) die, and Shell/Core fill the two real sub-layers that
were previously unnamed or called "medium".

### Recommendation 3.2 — Keep "portal"; "viewport" only as the inbound kind
**Pushback against renaming portal → viewport wholesale.** The maintainer's own
instinct ("a portal lets you move *through*") is exactly why portal is the right
genus: `design-portal-grammar.md` defines a portal as a seam where the stream
**turns to the world**, and it has *three* directions — **inbound** (state
flows in), **outbound** (you emit out), **parked** (you emit and the
continuation waits). A *viewport / magic mirror / illuminator* is
**perception-only**: you can look, things come to you, but a mirror cannot
*send* and cannot *park*. Renaming the genus to viewport would silently drop the
outbound and parked semantics that the outbox, `gate:` sends, `.card`, and
PLAN→approve depend on.

So: **portal stays the genus.** Where the maintainer's mirror instinct is
*correct* is the **inbound** portal specifically — `portal-state.json`,
`inbox.json`: you look in, state flows to you. Name that sub-type a **viewport**
(or keep "inbound portal"; "viewport" is a fine, evocative label for it). This
honours both the instinct and the design:
- inbound portal = **viewport** (perception) — ties to *injection = perception*;
- outbound portal = emission seam (action) — ties to *emission = action*;
- parked portal = a threshold that holds the continuation.

This also lands the dominion's `portal-reshape-synthesis` frame:
**perception = injection (free, woven into the scroll), action = emission;** the
retired *cockpit* was the polling/queryable surface. Pushing inbound state from
"a file you `cat`" (viewport-as-cockpit) toward "woven into the wake"
(viewport-as-injection) is the standing direction — see Part 2F.

### Recommendation 3.3 — Finish the cockpit retirement ✓ done (Task 3.3 commit)
"cockpit" was disowned in `design-portal-grammar.md` §3 but never swept.
Files renamed and links swept across index, design-resident-boundary, design-portal-grammar,
design-runner-cores (née design-runner-media), design-runner-management, decision-bundled-docs, and the plans
themselves. In-code link in `prompts.py` (D2) was fixed in Task 1 (commit `1ae9202`).
Remaining: body-prose sweep of "cockpit" within the plan files (historical context —
low priority) and full vocabulary sweep of vessel/medium/cockpit across kb (Task 4A).

### The unifying register (keep)
The **ornamented magic scroll** the resident turns to the world through, Ummon
tone — already committed (dominion `portal-reshape-synthesis`, the `run.md`/
introspection voice reshape). The vocabulary above sits inside it cleanly:
the resident (spirit) weaves the scroll; portals are the ornamented seams; the
Runner (Shell+Core) is the body the spirit is given for a wake. No conflict.

## Part 5 — Control surface over the engine (Task 5)

The execution-model review (2026-06-29) found Task 2 sound but **scrambled-feeling
because the engine shipped without a control surface over it** — "the engine
without the dashboard." The maintainer's correction: ship them together. The two
architecture forks the review parked are now **resolved by the maintainer
(evt-ogga, 2026-06-29)**:

1. **Daemon-per-account + cheap dispatcher** — *resolved: account daemon is the
   right shape.* One daemon per account (forge identity + laptop); repo-scoped
   runs underneath. The cheap dispatcher stays repo-based (default-repo selector)
   but can return a **respawn-in-another-repo** request the account daemon
   dispatches. Status cards show the repo. Routing splits into two axes: *which
   repo* (forge events are repo-addressed at the gate and never touch the
   dispatcher; message events route via the dispatcher's output) and *which
   Runner* (the 2A Shell/Core pin-skip). Repo routing is the dispatcher's output,
   not a bypass. **OSS self-deploy invariant holds:** account is an organizing
   concept, not a cloud dependency.
2. **Where inter-run plans live** — *resolved: in the repo, known and visible* —
   web-visible, referenced in status cards, auto-injected/preloaded by the daemon
   between wakes. Intra-run plans stay wherever the runner wants. Cross-repo plans
   are the case that needs the account daemon (so the two forks are one shape).

Full architecture: [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md).
Executable slices (CS1–CS7, the projection surfaces first): [`plan-control-surface.md`](plan-control-surface.md).
The one open sub-fork is the *physical* inter-run plan location (tracked file vs
orphaned branch vs gist) — recommended tracked file; confirm before building CS5.

## Part 4 — kb + code gardening sweep (Task 4)

The edit/research-heavy pass: read broadly, resolve the resolvable, surface the
unresolvable. Method for the execution run:

### 4A — Mechanical, deterministic first (cheap, high-confidence)
From this wake's kb-health preflight + greps, the known backlog:
- **Vocabulary sweep** (after Part 3 is confirmed): retire vessel/medium/cockpit
  across kb + code + prompts; introduce Shell/Core. Rename the 2 cockpit plan
  files and `runner_media.py` → `runner_select.py`.
- **Index hygiene:** 4 pages missing from `kb/index.md`
  (`decision-brnrd-repo-first-model`, `design-brnrd-channel-routing`,
  `design-brnrd-github-bot-user`, `design-brnrd-github-installation-sync`) —
  add index entries.
- **Oversized pages** (>32KB): `design-brnrd-protocol` (101KB!),
  `design-diffense` (87KB), `index` (57KB), `subject-managed-mode`,
  `design-agent-dominion`, `design-agent-ergonomics`,
  `design-resident-boundary`, `plan-failover-compute` — split into hub +
  daughters or compress accreted history to a lineage breadcrumb. The index
  being oversized is itself a signal it should become a hub-of-hubs.
- **Proposal-scaffolding cleanup:** ✓ `decision-licensing-and-defense`,
  `decision-monorepo-structure`, and `plan-env-fly-machines` compressed to
  current-state synthesis on 2026-06-29. ✓ `design-diffense` cleaned on
  2026-06-30: the accepted page now keeps rejected approaches as an appendix and
  describes remaining work as implementation edges rather than proposal
  questions.
- **Hub coverage:** `index §Research` and `index §Reviews` lack `subject-*`
  hubs — consider writing them.

### 4B — Semantic reconciliation (judgement, do carefully) — **executed 2026-06-29**
- ✓ `design-runner-management.md` status re-pointed to `design-runner-cores.md`
  + portals plan (cockpit label retired from the header).
- ✓ `design-runner-cores.md` — full vocab sweep: `runner_media.py`→`runner_select.py`,
  `select_medium`→`select_runner`, `implicit_medium`→`implicit_runner`,
  `proposed_medium`→`proposed_runner`, "runner medium"/"medium" → "Shell/Core",
  `[[runner.media]]`→`[[runner.profiles]]`, `runner_media` portal key → `runner`.
- ✓ `design-resident-boundary.md` — title updated, §3 rewritten to record
  three-step vocabulary lineage (medium→vessel→Shell/Core, final resolution
  evt-zyu6), §8 title and table "Vessel" → "Shell", remaining active vocab swept.
- ✓ `design-portal-grammar.md` — "runner medium/quota" → "Shell/Core and quota
  posture", "the medium failed" → "the Shell/Core failed" (Step 9 concept sweep
  aligned).
- ✓ `kb/index.md` — boundary page title entry updated; gardening plan status
  updated to "executing".
- `design-runner-back-channel.md` — no stale vocab found (clean).

### 4C — Surface, don't force (the unresolvable)
The execution run should **not** invent resolutions for genuine forks. Where a
page records an open product/values decision (relay billing specifics, parallel
execution, #128 claim model, capability-benchmark trust), leave it flagged and
report it to the maintainer rather than papering it with a guess. The gardening
is "leave the graph no worse, ideally clearer," not "decide everything."

### 4D — Method
Read in dependency order (most-referenced first: `design-brnrd-protocol`,
`decision-pricing-shape`, `subject-managed-mode`, `design-billing`,
`notes-pondering-fleet`). Keep a running conflict ledger in the dominion;
promote settled resolutions to kb; commit per theme. Budget-aware: this is the
largest task — the execution run should chunk it and may want its own follow-up
wakes per area rather than one giant sweep.

## Maintainer decisions (resolved 2026-06-28, evt-zyu6) — formerly open forks
All three pre-execution forks are answered (full text in "Resolved by the
maintainer" near the top):
1. **Vocabulary — adopted**, with the refinement that Runner stays as the
   umbrella entity (code mostly stays) and the `runner=` *config toggle* is
   retired in favour of `shell=`/`core=`. Task 1 is **unblocked**.
2. **Dispatcher hop — skipped when a specific Shell/Shell+Core is pinned.**
3. **Sequencing — Task 1 first, then Task 2 slices; Task 4 chunked; large tasks
   chunked across follow-up wakes.**

Plus a new point-1 slice: cheap boundary quota injection (decouple the ~18s
`/usage` scrape from the flush critical path). See the top section.
