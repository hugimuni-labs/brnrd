# Design: the boundary — one envelope, two rails, and the runner vocabulary

Status: active synthesis on 2026-06-27. Reconciles a maintainer design message
(Telegram, evt-9dp2/slhg) against the three pages shipped the same day —
[`design-runner-back-channel.md`](design-runner-back-channel.md) (the boundary
mechanism), [`design-runner-cores.md`](design-runner-cores.md) (Shell/Core selection
layer), and [`plan-cost-aware-runner.md`](plan-cost-aware-runner.md) (the
cost/notification braid). It carries the conversation's settled answers and the
two open forks that still need a maintainer nod.

The message braided several threads; this page keeps them in one place so a
future wake resumes the frame instead of re-deriving it.

## 1. The boundary is one concept — two rails of different density

**Question (maintainer):** *"Do the boundary portal and hooks want to be one
concept? Why do we still want `portal-state.json` separate from the hooked
stats — should they show identical data, just that the file is more 'live'?"*

**Answer: one concept, one source, two delivery rails — and deliberately not
identical at every instant.**

The *boundary* is the resident's perception of its operating envelope: pending
events, delivery/budget posture, SCM state, and the work-status resources facet
(quota / spend / context window / coexisting runs / remote SCM). There is one
concept and one source of truth. It reaches the resident on two rails:

- **The snapshot rail** — `portal-state.json` / `inbox.json`, daemon-written
  every heartbeat. *Always complete*, queryable, daemon-owned. It is (a) the
  source the injection rail reads from, and (b) the fallback for Tier-0/1
  runners that have no hook at all. This is the *query* tier of the
  perception model (large/complete state, fetched on demand).
- **The injection rail** — the hook capsule (`brr hook <phase>` →
  `format_delta`), woven into the scroll at runner boundaries. It is a
  **salience-gated delta**: it renders only what is worth spending a turn on,
  and (mid-run) only when `change_token` moved. The resources line is always
  present at seed/stop and also rides post-tool injections when portal-state
  changed, so a quota/spend/context wall can enter the weave without turning
  every tool boundary into a dashboard repaint.

So the file is **not** "the same data, just more live." It is the complete-state
rail; the hook is the gated-projection rail. Collapsing them into one
byte-identical surface would re-introduce the firehose we cut once already
(see the volatility × relevance × size placement rule in
[`design-runner-back-channel.md`](design-runner-back-channel.md) and the
perception model: inject what is small-or-volatile, query what is
large-and-complete). The portal json is not redundant; it is the snapshot the
gated injection reads from and the no-hook fallback.

**The concrete divergence the maintainer spotted is real but expected.** Today
two renderers project the same resources data: `daemon._resources_facet` builds
the JSON snapshot; `hooks._format_resources` builds the woven one-liner. They
already agree on the facet schema (quota / spend / context-window /
coexisting-runs / remote-scm), but the woven line is gated by the boundary
projection rather than byte-identical to the file. A mid-run boundary shows it
only when there is an injected portal update. That asymmetry is the tier-2/tier-3
design, not a bug.

**The genuine improvement** is not "make them identical" but **"let a
*salience-relevant* resource change ride the post-tool delta"** — e.g. quota
crossing near-empty, a coexisting run appearing, a relay cap approaching. Those
are exactly the moments the boundary should interrupt with the resource line
mid-run. The plumbing already supports it (`change_token` gating); what is
now shipped is the first version of that rule: resources ride post-tool
injections when portal-state changed, and the wall facets are populated by real
collectors for Codex + Claude. So the work is *keep populating the facets*, not
*merge the rails*. The cheap hardening also shipped: JSON and woven projections
read from the single `facets.py` schema, so they do not drift in *which* facets
they carry.

**How to choose the facets — the selection principle (evt-e1gl, 2026-06-28).**
The maintainer asked, fairly: "agreeing by convention — I don't understand how to
*choose* them; what would you prefer?" The answer is to stop choosing them by
editorial taste (three lists that happen to match) and **derive them from the
spine we already agreed — distance-from-envelope (§7)**. A slot earns facet status
iff it is one of:

- **a wall** the run can hit, with a distance the resident plans against —
  wall-clock `budget`, `spend`, subscription `quota`, and `context_window`. These
  are the *level* facets; the card headline is the minimum distance across them.
  Which level a given Shell can actually read is per-Shell (§8): Codex exposes
  `quota` + `context_window` from its rollout file (wired); Claude exposes
  `spend` + `context_window` via result JSON and subscription `quota` via a
  cached interactive `/usage` PTY scrape (wired 2026-06-28).
- **an actionable operational state** that changes a decision without being a wall
  — `coexisting_runs` (presence/liveness), `remote_scm` (PR/push posture).

Everything else is detail to inspect on demand, not a facet. And the *convention*
that stops drift is to make that set **explicit, once**: the single projection
helper defines the facet list, each as a uniform three-state record
(`status: known|absent|unimplemented`, `source`, `freshness`, `summary`,
`required?`); the three renderers project from it rather than re-listing keys. So
"by convention" (implicit agreement) → "by schema" (one definition). My
preference, concretely: the wall-derived set above, which **adds `context_window`
and makes subscription `quota` a real level facet — natively readable for Codex
and best-effort probeable for Claude** (§8; the Claude `statusLine` route that
earlier promised this still does not fire under `--print`).

**Shipped 2026-06-27 (evt-go5z): three-state facet honesty.** The maintainer
agreed the rails are not identical *but* asked the boundary to "show
substantially more missing data" than the old flat `unavailable`. The fix
distinguishes the two kinds of "missing" the resident must not conflate:

- `known` — proven value this heartbeat.
- `absent` — the collector ran and there is genuinely nothing: **no PR for this
  branch yet**, no quota snapshot the Shell/Core exposes, **no outbound message
  sent**. Affirmative-empty — the same logic the closeout capsule uses for "0
  pending events". Absence is data, surfaced on purpose.
- `unimplemented` — the collector is not built (cost metering, coexisting runs),
  with a `required` flag separating expected-to-grow from someday-niceties.

The same wake also surfaced **"running long"** (elapsed past the soft budget,
flagged in `budget.long_running`) and the **no-outbound-at-closeout** receipt,
across all three rails (JSON portal, woven hook line, `brr portal state` CLI).
This is the visible half of §5's PR posture and the first concrete step of
"populate the facets"; the *values* behind `known` (live quota/cost numbers)
still need their collectors.

## 2. The open-source vs brnrd split — the static envelope is not too limiting

**Question (maintainer):** *"Self-deployed / brr daemon handles CLI Shell/Core,
quotas and credits data; brnrd (subscription) handles the boundary. Self-deployed
defines the boundary statically — isn't that too limiting? Still
open-source-friendly?"*

The earlier "limits" model is the resolution: the user sets an **envelope**, and
the resident **acts freely, attentively, and analytically within it**. Split
that into mechanism vs data source and the open-source worry dissolves:

- **The envelope mechanism is open-source.** A self-deployed user defines the
  boundary in config — allowed media, per-run/per-day caps, fallback policy,
  which providers to probe. The resident reads that static envelope and acts
  freely inside it. Static does **not** mean limiting: the runner is not asking
  permission for every step; it is operating analytically within a declared
  envelope, exactly the agreed model.
- **The live/authoritative data source is the brnrd value-add.** brnrd owns the
  wallet and the relay keys, so it can supply *authoritative live* quota/credit
  signals and *remote* envelope control (adjust caps, top-up, pause from the
  service side) without the user editing a file. That is the "service helps you
  with remote controls" half — a paid convenience layer over an open mechanism,
  not a gate on the open mechanism.

So the boundary is **not** "brnrd-only." Self-deployed gets the full boundary
concept with a static envelope + best-effort local signals (CLI error text,
manual snapshots, response headers for owned keys). brnrd adds the live
authoritative rail and remote control on top. This keeps brr genuinely
open-source-friendly while giving the service a real, fair value-add — consistent
with [`decision-llm-relay.md`](decision-llm-relay.md) (BYO stays free/default;
brnrd-owned intelligence pays provider cost + a transparent service fee).

## 3. Vocabulary — runner / run / weave / medium (SETTLED → `Shell + Core`)

*Lineage breadcrumb: this section records a three-step decision that opened
and re-opened before settling. The final resolution is at the foot.*

**Maintainer's clarification (2026-06-27, evt-go5z):** stop conflating the
*entity* (the weaver) with the *executor type* (Codex / Claude / Gemini). He
proposed:

- **runner = the resident & LLM weaving** (the weaver/entity).
- **run = the weave** (one wake's work).
- the executor (Codex / Claude / Gemini / custom) = **medium / substrate /
  shell** — he is reaching for the right noun.

*Recommendation at the time: adopt `medium` for the executor.* Reasons included
séance resonance ("a medium channels a spirit"), alignment with existing code
glosses, and the word already drifting into place. The maintainer picked
`medium` ("let's do medium"), the noun was declared fixed.

**Reopened 2026-06-28 (evt-tw6t): second thoughts.** The maintainer reached for
"an artificial body, replaceable/switchable — maybe `chassis`?" The analysis
surfaced two competing metaphors:

- **The summoning metaphor** — *medium / conduit / channel*. Momentary,
  séance-flavoured — the resident is *invoked through* it.
- **The incarnation metaphor** — *vessel / chassis / shell*. Persistent,
  swappable — the resident *acts through it* for the full duration of a wake.
  The cost/failover model (swap to cheaper/stronger) is literally *changing
  bodies*, truer to how brr actually uses it. The cleanest word in this register
  was **`vessel`** (`shell` without the Unix collision).

No rename ran while the noun was unsettled.

**Finally settled 2026-06-28 (evt-zyu6, `plan-repo-gardening.md` §3.1):**
the maintainer adopted the **Armored Core** frame. The vocabulary is now:

- **Shell** = the CLI program on PATH (`claude`/`codex`/`gemini`) — the
  carapace that gives the Core hands (file ops, tools, hooks).
- **Core** = the model (`opus`/`sonnet`/`gpt-5-codex`) — the swappable reactor.
- **Runner** = the whole executing body for one thought (Shell + Core together).
- `vessel` and `medium` are **retired** across kb, code, and prompts.

The `runner` umbrella stays (271 code uses; "a resident, via a Shell and a Core
on a wake, is the Runner"). The user-facing knobs became `shell=`/`core=` (not
`runner=`). See [`design-runner-cores.md`](design-runner-cores.md) for the
implementation shape and [`plan-repo-gardening.md`](plan-repo-gardening.md) §3
for the full vocabulary rationale.

## 7. The boundary state card — level vs edge, and distance-from-envelope

**Question (maintainer, evt-tw6t):** the inject rail shouldn't only report
*warnings*. Quota *as you go* helps the resident plan ahead; knowing a coexisting
run not only *appeared* but *still exists* is presence, not an edge-event. The
cockpit was the wrong abstraction (drift + his/my attention-model difference),
but **world-state woven into the stream at breakpoints** is the right model for
temporal awareness. Injections must **not** duplicate the snapshot — a **crisp
state card ornamenting the stream**, especially highlighting **distance from the
envelope boundary.** Agreed?

**Answer: agreed — with one sharpening that keeps it from collapsing back into
the firehose.** The card carries two kinds of signal that must not be conflated:

- **Level (standing).** The current *position relative to every wall*: distance
  from the budget wall, quota headroom, live sibling count. This is always-true
  state, and it's what "as you go" and "still exists" are asking for — presence,
  not just transitions. The card *always* reflects the current level.
- **Edge (gated re-weave).** *When is it worth spending tokens + attention to
  re-lay the card into the scroll.* This is the `change_token` salience gate from
  the perception model. It governs the **cost of re-injection**, not whether the
  level is tracked.

So the reconciliation of "as you go" with "don't duplicate the snapshot": the
card is a **standing level capsule** whose *re-injection* is edge-gated. It
shows the current distance-from-walls; it gets re-woven at a breakpoint only when
something moved enough to earn the turn. That's how you get temporal awareness
without re-paying for an unchanged card every tool tick — the exact dilution trap
that killed `portal wrap`.

**Distance-from-envelope is the spine, and it's multi-dimensional.** The envelope
has several walls — wall-clock budget, quota, spend cap — and the card's headline
should be the *minimum distance across the active walls*: whichever wall you'll
hit first. That's the single decision-useful number ("you have ~18m of runtime
and ~2 strong respawns before *some* wall"). The current woven capsule already
carries the wall ingredients (`budget: Xs of Ys used` plus `resources:` for
quota/spend/context when injected); the card is that data *reframed as
distance-to-wall* and extended as more collectors land.

**The brr-specific catch on "still exists":** a coexisting run that *persists*
requires **sibling-liveness tracking** (heartbeat freshness of other runs), which
doesn't exist yet — brr is single-flight *per dominion*, so `coexisting_runs`
renders `unimplemented`. "Appeared" is an event; "still exists" is a level that
needs a liveness collector. It's the right target, but it's a build, not a
reframe of existing data. Until then the honest card says `coexisting-runs=
unimplemented`, which is already the three-state honesty doing its job.

## 8. What each Shell exposes for live cost/quota (corrected 2026-06-28)

**Question (maintainer):** before building the collectors, clarify exactly what
we can get from each Shell re: live cost and quota. This is load-bearing for the
card (§7), because a card promising a smooth quota gauge it can't fill is a lie
the resident learns to distrust.

**Fire-verified 2026-06-28 (current state).** Two earlier conclusions were
partly wrong in opposite directions. First, evt-e1gl recorded "Claude
`statusLine` hands over quota head-less; Codex quota is edge-only" and built
`statusline.py` on it. A live probe overturned both halves: Claude `statusLine`
does not fire under `--print`, while Codex writes subscription quota to its
rollout file. Later the maintainer pushed on `/usage`: the Claude quota surface
is indeed reachable, but only by driving the interactive TUI through a PTY and
scraping the `/usage` panel. The true map:

**(a) The `stream-json` loom stays retired** (abandoned 2026-06-27; see
[`plan-streaming-runner-injection.md`](plan-streaming-runner-injection.md),
[`design-runner-cores.md`](design-runner-cores.md) §Reconciled). Cost/quota does
**not** ride native hook payloads — hooks carry portal-state, not provider usage
internals. The real source is per-Shell **post-result / on-disk / PTY-scraped**
data, below; hooks then inject the portal-state projection of that data.

**(b) Claude `statusLine` does NOT fire head-less — the Claude quota path is
not the quota path.** `statusLine` is a TUI footer; under `claude --print` (the
mode brr's runner uses) it is **never invoked** (probe: a statusLine command set
in `.claude/settings.local.json` never fired under `--print`, while settings-file
*hooks* fired the same run with a clean env). So the `rate_limits` / `cost` /
`context_window` JSON it would carry never reaches brr. `statusline.py` is only
for a human-watched interactive footer.

Claude has two other usable seams:

- `claude --print --output-format json` carries `total_cost_usd` (spend), token
  `usage`, and `modelUsage[model].contextWindow` (context), but **not**
  subscription reset windows. brr opts bundled Claude profiles into result JSON,
  unwraps `.result` back into the plain response file, and writes `spend` +
  `context_window` levels to the portal at terminal refresh.
- `claude_usage.py` starts a short-lived interactive Claude in `--safe-mode`,
  types `/usage` in a PTY, captures the terminal screen, and parses Claude's own
  **Current session** + **Current week (all models)** buckets into a quota
  snapshot. The probe does not send a model prompt, but it is still a TUI scrape
  (~15s in live tests), so the daemon caches it (`.claude-usage-levels.json`,
  5-minute TTL) and hooks merely read the portal-state projection. This is
  best-effort local telemetry, not a first-class head-less API; preserve
  Claude's labels rather than relabelling them as "5h" unless Claude does.

**(c) Codex DOES expose subscription quota head-less — and brr now reads it.**
Every `token_count` event in a Codex session rollout
(`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`) carries a `rate_limits`
block: `primary` (5h: `used_percent`, `window_minutes:300`, `resets_at`),
`secondary` (weekly: `window_minutes:10080`), `plan_type`, plus
`info.model_context_window` and token usage. **This is exactly what `/status`
prints** — written to disk continuously, no `/status` call, no extra credits.
`codex_status.py` (wired into the facets 2026-06-28) reads the newest rollout's
last `token_count` event → `quota` + `context_window` facets. Spend in $ is not
handed over (subscription), so Codex `spend` is honestly `unimplemented`. The
`codex exec --json` *stdout stream* uses a newer event schema (`turn.completed`
`usage`) that does **not** carry `rate_limits` — the quota lives only in the
rollout file, so the on-disk path is the one that works.

**Corrected data map** (what brr's daemon can actually read):

| Shell | Spend ($) | Subscription quota | Context window | Source |
| --- | --- | --- | --- | --- |
| Claude Code (subscription) | `total_cost_usd` ✓ | ✓ best-effort `Current session` + `Current week` from `/usage` TUI | `modelUsage.contextWindow` ✓ | Result JSON for spend/context (`claude_status.py`) + cached interactive `/usage` PTY scrape for quota (`claude_usage.py`) |
| Codex (subscription) | ✗ (no $ gauge; tokens only) | ✓ `rate_limits.{primary,secondary}` | ✓ `model_context_window` + last `input_tokens` (est) | **session rollout `token_count` events** — *wired* (`codex_status.py`) |
| Any API-key auth | response usage | `anthropic-ratelimit-*` headers | — | per-call headers |
| brnrd-owned key | authoritative | authoritative | — | brnrd reads it (§2) |

So the durable asymmetry is no longer "Codex has quota and Claude doesn't"; it is
**cheap native gauge vs. expensive scraped gauge**. Codex quota is on disk and
cheap enough to read every boundary. Claude quota is local and useful, but the
source is a cached PTY scrape, so it should be treated as best-effort telemetry
with freshness, not as a synchronous hook action. The `facets.build`
`levels_collector` arg stays a per-slot set, so each Shell marks only the slots
it truly collects (`known`/`absent`) and leaves the rest `unimplemented` — Codex
`spend` no longer lies as `absent`.

**Build consequence.** Codex quota collector: **done** (read-only, on-disk, no
credits). Hardening + the upstream quota-seam ask are tracked in
[brr#195](https://github.com/Gurio/brr/issues/195). Claude result-JSON collector:
**done** for `spend` + `context_window` terminal accounting. Claude `/usage` PTY
collector: **done** for cached subscription quota/reset visibility. The old
`statusline.py` helper is no longer registered by brr's daemon hooks; it only
serves a manually wired interactive TUI footer. Hook deltas now include the
resources line on post-tool injections too, so a changed quota wall can enter the
weave before closeout. Keep the §4 guardrail: the card shows
*consumption-so-far + reset windows when known*, **never a forward dollar
promise**.

> **Lineage breadcrumb.** 2026-06-28: live probes overturned evt-e1gl in two
> steps. First, Claude `statusLine` did not fire under `--print` and Codex quota
> was found in rollout files. Later, the maintainer's `/usage` push proved
> Claude subscription quota is reachable after all through an interactive PTY
> scrape, not through a head-less JSON seam. Current code uses Codex rollout
> (`codex_status.py`), Claude result JSON (`claude_status.py`), and Claude
> `/usage` PTY (`claude_usage.py`) as separate collectors.

**The rename is now a sanctioned follow-up run, deliberately not folded into the
boundary work.** `runner` is embedded across config keys (`runner`,
`runner_cmd`), prompts (`runners.md`), kb page names (`design-runner-*`), and
code (`resolve_runner`, runner profiles). It is a wide, mechanical blast that
earns its own dedicated run with a migration shim for live config — kept
separate so a behavioural change (this boundary enrichment) and a pure rename
do not tangle in one diff.

## 4. Cost manifests per Shell/Core, and the respawn navigation matrix

**Maintainer:** *"Cost manifests per Shell/Core (not sure how)"* and the respawn
matrix — *"a sorted / heat-mapped matrix giving clear navigation by price per
token, grouped by Shell/Core type, noting whether already successfully used,
followed by the subscription quotas ranked beside the matrix."*

This is the structured `runner` portal facet already sketched in
[`design-runner-cores.md`](design-runner-cores.md) §Quota and credit signals,
read as a *navigation surface* rather than a flat string. The manifest per
Shell/Core = the runner's row: model, provider, owner, cost class, cost_rank
(price-per-token proxy), quota source + freshness, hook capability, billing
posture, and **whether it was already used successfully this thread**. The
matrix = those rows sorted/heat-mapped by cost_rank and grouped by Shell/Core
type; the quota rankings sit beside it as the subscription view.

Crucial guardrail from [`plan-cost-aware-runner.md`](plan-cost-aware-runner.md):
this is **historical pre-analysis, never a forward dollar estimate**. The matrix
shows price *rank* and what comparable past weaves consumed; it does not quote a
projected total for this run. The "crisp visualization → simpler decision"
intuition is right, and it is a *boundary* surface (a perception the resident
weaves), not a separate dashboard.

This is also the substrate of the society-of-mind concurrency the maintainer
described: cheap respawns chosen off the matrix + live consumption stats on the
injection rail let a weave spawn siblings, block on their output files / events,
or continue — and see the ready ones arrive on the boundary. brr is single-flight
*per dominion* today, so `coexisting_runs` renders `unavailable`; the matrix is
the precondition for lighting it up.

## 5. Failover as a receipt, not a perfect classifier — and PR stats on the boundary

**Maintainer (named honestly):** deciding whether an agent *legitimately* failed
is "quite problematic to situationally triage," and "we gotta release the product,
avoiding this rabbit hole."

**Stance: do not build a perfect failure classifier before release. Make failure
cheap to recover from, and make the recovery state *visible on the boundary*.**

- **Interim work receipt.** Every run commits early and keeps a continuously
  updated branch (the diff is the receipt that survives a kill — already the
  cost-aware chunking discipline). A crashed or exhausted weave leaves a real,
  resumable artifact, not nothing.
- **Paid cloud failover** (pass-through-billed agents) is the *smooth* recovery
  path when a self-deployed daemon dies — consistent with
  [`plan-failover-compute.md`](plan-failover-compute.md) and the relay decision.
  It is the easiest fallback, not the only one (the user can also wait for quota
  reset, fix the daemon, or clarify).
- **PR stats belong on the boundary interweave.** The boundary already carries
  the local SCM facet (`scm`: unpushed/modified on the worktree). Extend the
  resources facet's `remote_scm` to carry the **PR posture** — branch pushed?,
  **PR open / not yet created**, checks state — so a weave perceives "your work
  has a branch but no PR yet" as woven context. Especially the *not-yet-created*
  case the maintainer called out: the receipt is most valuable exactly when the
  PR does not exist yet, because that is when the work is at risk of being
  invisible. This is the same "affirmative-empty signal" logic the closeout
  capsule already uses for pending events.

The triage minimum brr *does* need is the failure-class distinction already in
[`design-runner-cores.md`](design-runner-cores.md) §Implementation sequence step
5 (quota / auth / provider-outage / quality-escalation / no-response) — enough to
route automatic fallback for the *unambiguous* operational failures, while
*ambiguous* failures surface to the user with the receipt attached rather than
being auto-adjudicated. That is the release-able shape: cheap recovery + honest
escalation, not a perfect judge.

## 6. Fairness / business posture — BYO free, paid-through-the-house everywhere it fits

**Maintainer:** *"Don't cling to previously planned shapes; don't gate
open-source users, but offer as much as possible paid through the house. Pricing
fair, but everywhere we can offer. Make a viable business — be fair with me."*

This does not contradict the open-source posture in §2; it sharpens it.
[`decision-llm-relay.md`](decision-llm-relay.md) already holds the spine: **BYO
stays free/default; the house offers a paid path everywhere a user would
otherwise hit friction** (no local quota, no credentials, a crashed daemon, a
need for a stronger Shell/Core). The fairness contract is *transparency*: provider
cost and the relay/service fee shown as separate line items, per-run caps, no
silent card-on-file top-ups. The product line can call it "intelligence credits,"
but the ledger keeps `llm_provider_cost`, `llm_relay_service_fee`, and
`managed_compute_ops` distinct. The viable-business requirement and the
open-source requirement meet at "fair, transparent, everywhere — never a gate on
the free mechanism, always an offered convenience over it."

## Settled vs open

**Settled this conversation:**
- The boundary is one concept, two rails of different density; the portal json
  is the snapshot/fallback rail, not a redundant copy. (§1)
- Self-deployed static envelope + best-effort local signals is the open
  mechanism; brnrd adds the live authoritative rail + remote control. (§2)
- **Vocabulary:** `medium` was picked (evt-go5z), reopened (evt-tw6t), and
  **finally settled** on `Shell + Core` (evt-zyu6, 2026-06-28). `vessel` and
  `medium` are retired. See §3 for the full lineage. (§3)
- **The boundary card is a standing level capsule with edge-gated re-injection;**
  distance-from-envelope (min across walls) is its spine. (§7, evt-tw6t)
- **Cost-data source (corrected 2026-06-28):** the stream-json loom is retired.
  Codex live quota/context comes from session-rollout `token_count` events.
  Claude spend/context comes from terminal `--output-format json` results, while
  Claude subscription quota/reset windows come from a cached interactive
  `/usage` PTY scrape (`statusLine` still never fires under `--print`). (§8)
- **Facet selection (evt-e1gl):** facets are *derived from the walls* (budget,
  spend, quota, context_window) plus actionable state (coexisting_runs,
  remote_scm), defined once in the single projection helper — "by schema," not
  "by convention." (§1)
- Failover = cheap-recovery + visible receipt + honest escalation, not a perfect
  classifier; PR posture (incl. not-yet-created) joins the boundary. (§5)
- Business posture reconciles with open-source via transparent
  paid-everywhere-it-fits. (§6)

**Shipped (evt-go5z):**
- Three-state facet honesty (`known`/`absent`/`unimplemented` + `required`),
  PR-not-created posture, `long_running`, and no-outbound-at-closeout — across
  the JSON portal, the woven hook line, and `brr portal state`. (§1)

**Shipped (evt-1uwp, 2026-06-28):**
- **Single projection helper** (`facets.py`): the wall-derived facet set is
  defined *once* as a schema (quota / spend / context_window levels +
  coexisting_runs / remote_scm state, each a three-state record); all three
  renderers project from it — "by schema, not by convention." (§1)
- **`cost`→`spend` rename + new `context_window` level facet**, plus a per-Shell
  `levels_collector` switch (empty slot reads `absent` on a Shell with a
  collector, `unimplemented` without one). (§1, §8)
- **`brr portal facets`** — operator-inspectable catalogue of the implemented
  facets (schema-only outside a wake; live status folded in inside one). Fixed
  `_portal_state_path` to honour `BRR_PORTAL_STATE` so on-demand inspection
  resolves the live portal without `--path`.
- **Claude result-JSON collector** (`claude_status.py`): bundled Claude profiles
  request `--output-format json`; runner capture unwraps `.result` back into the
  response file and writes terminal `spend` + `context_window` levels for the
  final portal refresh. The old `statusline.py` helper is not daemon-registered.
- **Claude `/usage` PTY collector** (`claude_usage.py`): daemon-side, cached
  interactive scrape of Claude's own subscription buckets (`Current session` +
  `Current week`), projected as the `quota` level. Hook deltas now carry the
  resources line on post-tool injections too, so a changed wall can enter the
  weave mid-run.

**Open forks / next builds:**
- **The rename run** (§3) — ~~`runner` → `medium`/`resident`~~ — **completed**:
  vocabulary settled on Shell/Core; `vessel` and `medium` retired (Task 4 of
  `plan-repo-gardening.md`).
- **Hardening the level collectors:** correlate Codex rollout files to the active
  session instead of relying on newest-file, confirm context math, and replace
  Claude's PTY scrape if Anthropic exposes a first-class quota/reset seam. Codex
  spend still needs a price table if it ever graduates beyond `unimplemented`.
- **The distance-card** (§7) — reframe the budget line as min-distance-across-walls
  now that spend / quota / context_window can carry live levels.
- **Respawn-on-wall** (§5, deferred evt-1uwp) — see the cautious-deferral note
  below; design is sketched, active implementation waits for a supervised wake.

**Respawn-on-wall — designed, deliberately not shipped tonight (evt-1uwp).** The
maintainer green-lit building respawn "as the last step to work around the
possible quota breach," then clarified the breach is unlikely (quota <50%) and
signed off for the night, asking for *cautious* autonomous progress. Auto-respawn
lives in the daemon worker loop and, done wrong, is exactly a runaway
quota-burning loop — the wide-blast/irreversible case that wants a supervised
wake, not an unattended one. So the shape is recorded here and the active build
held: **on a wall-class exit** (quota/auth/provider-outage — the failure classes
already named in `design-runner-cores.md` step 5), the daemon spawns one fresh
run seeded from the **committed run branch** (the interim work receipt always
survives, §5), carrying a respawn-depth counter in run meta and a hard cap
(e.g. ≤2) so a persistently-walling task escalates to the user instead of
looping. *Ambiguous* failures never auto-respawn — they surface with the receipt
attached. The guard is the point: the respawn exists to survive a breach cheaply,
not to retry blindly.

## See also

- [`design-runner-back-channel.md`](design-runner-back-channel.md) — the boundary
  mechanism (native hooks; the injection rail).
- [`design-runner-cores.md`](design-runner-cores.md) — the Shell/Core selection
  layer and the structured `runner` facet behind the matrix.
- [`plan-cost-aware-runner.md`](plan-cost-aware-runner.md) — cost
  self-awareness, the historical-pre-analysis guardrail, operator legibility.
- [`decision-llm-relay.md`](decision-llm-relay.md) — BYO-free / paid-relay
  pricing spine.
- [`plan-failover-compute.md`](plan-failover-compute.md) — compute-host failover,
  the sibling axis to Shell/Core failover.
