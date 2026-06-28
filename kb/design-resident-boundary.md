# Design: the boundary — one envelope, two rails, and the medium vocabulary

Status: active synthesis on 2026-06-27. Reconciles a maintainer design message
(Telegram, evt-9dp2/slhg) against the three pages shipped the same day —
[`design-runner-back-channel.md`](design-runner-back-channel.md) (the boundary
mechanism), [`design-runner-media.md`](design-runner-media.md) (cost/medium
layer), and [`plan-cost-aware-cockpit.md`](plan-cost-aware-cockpit.md) (the
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
(quota / cost / coexisting runs / remote SCM). There is one concept and one
source of truth. It reaches the resident on two rails:

- **The snapshot rail** — `portal-state.json` / `inbox.json`, daemon-written
  every heartbeat. *Always complete*, queryable, daemon-owned. It is (a) the
  source the injection rail reads from, and (b) the fallback for Tier-0/1
  runners that have no hook at all. This is the *query* tier of the
  perception model (large/complete state, fetched on demand).
- **The injection rail** — the hook capsule (`brr hook <phase>` →
  `format_delta`), woven into the scroll at runner boundaries. It is a
  **salience-gated delta**: it renders only what is worth spending a turn on,
  and (mid-run) only when `change_token` moved. The resources line is rendered
  only at the **seed/stop** boundaries, not on every post-tool tick, precisely
  so editing churn injects no noise.

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
already agree on the four facets (quota / cost / coexisting-runs / remote-scm),
but the woven line is seed/stop-gated, so a mid-run boundary shows the rich
snapshot in the file and a quieter capsule in the scroll. That asymmetry is the
tier-2/tier-3 design, not a bug.

**The genuine improvement** is not "make them identical" but **"let a
*salience-relevant* resource change ride the post-tool delta"** — e.g. quota
crossing near-empty, a coexisting run appearing, a relay cap approaching. Those
are exactly the moments the boundary should interrupt with the resource line
mid-run. The plumbing already supports it (`change_token` gating); what is
missing is the collectors that make any resource facet move at all. So the work
is *populate the facets*, not *merge the rails*. The one cheap hardening worth
doing regardless: keep the JSON and the woven projection reading from a **single
projection helper** so they can never drift in *which* facets they carry (still
open — there are now three renderers: `_resources_facet`, `_format_resources`,
`_format_portal_state`, agreeing on the same four keys by convention).

**How to choose the facets — the selection principle (evt-e1gl, 2026-06-28).**
The maintainer asked, fairly: "agreeing by convention — I don't understand how to
*choose* them; what would you prefer?" The answer is to stop choosing them by
editorial taste (three lists that happen to match) and **derive them from the
spine we already agreed — distance-from-envelope (§7)**. A slot earns facet status
iff it is one of:

- **a wall** the run can hit, with a distance the resident plans against —
  wall-clock `budget`, `spend`, subscription `quota`, and now `context_window`
  (unlocked by the statusLine finding, §8). These are the *level* facets; the
  card headline is the minimum distance across them.
- **an actionable operational state** that changes a decision without being a wall
  — `coexisting_runs` (presence/liveness), `remote_scm` (PR/push posture).

Everything else is detail to inspect on demand, not a facet. And the *convention*
that stops drift is to make that set **explicit, once**: the single projection
helper defines the facet list, each as a uniform three-state record
(`status: known|absent|unimplemented`, `source`, `freshness`, `summary`,
`required?`); the three renderers project from it rather than re-listing keys. So
"by convention" (implicit agreement) → "by schema" (one definition). My
preference, concretely: the wall-derived set above, which **adds `context_window`
and promotes subscription `quota` from edge-only to a real level facet for the
Claude vessel** (§8).

**Shipped 2026-06-27 (evt-go5z): three-state facet honesty.** The maintainer
agreed the rails are not identical *but* asked the boundary to "show
substantially more missing data" than the old flat `unavailable`. The fix
distinguishes the two kinds of "missing" the resident must not conflate:

- `known` — proven value this heartbeat.
- `absent` — the collector ran and there is genuinely nothing: **no PR for this
  branch yet**, no quota snapshot the medium exposes, **no outbound message
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

**Question (maintainer):** *"Self-deployed / brr daemon handles CLI medium,
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

## 3. Vocabulary — runner / run / weave / medium (SETTLED → `medium`)

**Maintainer's clarification:** stop conflating the *entity* (the weaver) with
the *executor type* (Codex / Claude / Gemini). Proposed:

- **runner = the resident & LLM weaving** (the weaver/entity).
- **run = the weave** (one wake's work).
- the executor (Codex / Claude / Gemini / custom) = **medium / substrate /
  shell** — he is reaching for the right noun.

**Recommendation: adopt `medium` for the executor.** Reasons:

1. The codebase already glosses it that way — the Mode block literally renders
   *"Runner: claude — the compute medium this thought runs on."* The word is
   already drifting into place.
2. [`design-runner-media.md`](design-runner-media.md) already uses "medium" for
   the layer above static profiles.
3. **Séance resonance.** A medium *channels a spirit*. The maintainer's own
   phrase was "the tools we use to invoke the spirit from remote LLMs," and the
   playbook frames the resident as a spirit of air/fire. "Medium" is the
   poetically-true noun, and it ties to the *ornamented-scroll* register the
   portal reshape is converging on. "Shell" is evocative but overloaded (Unix
   shell); "substrate" is a fine clinical synonym to keep for technical prose.

With `medium` as the executor, **`runner` largely dissolves** — the weaver is
"the resident" (our existing word) and the wake's work is the "run/weave." That
is a satisfying *cut*, not just a rename, and fits the pre-release bias toward
collapsing names that no longer carry their weight.

**Resolved 2026-06-27 (evt-go5z): the maintainer picked `medium`** ("let's do
medium"). So the noun is fixed: the executor (Codex / Claude / Gemini / custom)
is the **medium**, `run`/`weave` is the wake's work, and `runner` dissolves into
"the resident". `substrate` stays available as a clinical synonym for technical
prose.

**Reopened 2026-06-28 (evt-tw6t): the maintainer has second thoughts on
`medium`.** He's reaching for "an artificial body, replaceable/switchable —
maybe `chassis`?" and asked for the cyberpunk/cultural analogy. The sharpening
this wake: `medium` and `chassis` answer *two different metaphors*, and the
choice is really between them, not between words.

- **The summoning metaphor** — *medium / conduit / channel*. The resident
  (spirit) is *invoked through* it. Momentary, séance-flavoured. This is what
  `medium` carries.
- **The incarnation metaphor** — *vessel / chassis / frame / body / shell*. The
  resident *rides / is housed in* it for the whole run. Persistent, swappable.
  This is what the maintainer's words ("artificial body, ride, replaceable")
  actually describe.

The **incarnation** reading is truer to what the executor *is for brr*: you
don't speak through it once, you *act through it* (tools, edits, weave) for the
duration of a wake, and the cost/failover model — swap to cheaper/stronger, fail
over to another — is literally *changing bodies*, not switching séance channels.
The cleanest single word that keeps the spirit register **and** the swap
connotation is **`vessel`** (a spirit occupies a vessel; it can be moved to
another). `chassis`/`frame` carry the swap but go cold-mechanical, off the
ornamented-scroll register. The exact cultural touchstone is *Ghost in the
Shell* — the Ghost rides a replaceable **shell** — and the tragedy is the
perfect word, `shell`, is taken by Unix; `vessel` is `shell` without that
collision. Still a genuine fork (aesthetic/values), so it stays the
maintainer's call: `medium` (summon, lower-friction, already glossed in code) vs
`vessel` (incarnate, truer to ride/swap). No rename run until the noun settles.

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
and ~2 strong respawns before *some* wall"). The current woven line already has
the budget half (`budget: Xs of Ys used` + `running long`, seed/stop-gated in
`hooks._format_resources`); the card is that line *reframed as distance-to-wall*
and extended to the other dimensions as their collectors land.

**The brr-specific catch on "still exists":** a coexisting run that *persists*
requires **sibling-liveness tracking** (heartbeat freshness of other runs), which
doesn't exist yet — brr is single-flight *per dominion*, so `coexisting_runs`
renders `unimplemented`. "Appeared" is an event; "still exists" is a level that
needs a liveness collector. It's the right target, but it's a build, not a
reframe of existing data. Until then the honest card says `coexisting-runs=
unimplemented`, which is already the three-state honesty doing its job.

## 8. What the medium actually exposes for live cost/quota (corrected 2026-06-28)

**Question (maintainer):** before building the collectors, clarify exactly what
we can get from the medium re: live cost and quota. This is load-bearing for the
card (§7), because a card promising a smooth quota gauge it can't fill is a lie
the resident learns to distrust.

**Two corrections landed (evt-e1gl, 2026-06-28) that overturn this section's
first answer.** Both came from the maintainer, and both reconcile the live-cost
question *back onto the hooks rail* — there is no streaming dependency here.

**(a) The `stream-json` loom is retired; the source is hooks.** The earlier
answer routed live consumption through "brr holding the stream-json loom (not
yet built)." That medium was *abandoned* on 2026-06-27 in favour of native hooks
as the one boundary abstraction over vessels (see
[`plan-streaming-runner-injection.md`](plan-streaming-runner-injection.md),
status; [`design-runner-media.md`](design-runner-media.md) §Reconciled). So any
"distance-from-spend-wall depends on the stream" reasoning is dead. The live cost
signal must arrive through the same injected-JSON surfaces the hooks rail already
owns — which is exactly what (b) provides.

**(b) Claude Code DOES expose subscription quota as a readable level — via the
status line.** Claude Code's `statusLine` feature invokes a configured command
and hands it **session JSON on stdin**. That JSON carries (maintainer's finding,
to be smoke-verified per the "fire it before you rule on it" pitfall):

- `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage`
  — subscription quota **consumed**, as a readable gauge;
- `rate_limits.five_hour.resets_at`, `rate_limits.seven_day.resets_at` — reset
  windows (unix epoch), i.e. *when the wall moves*;
- `context_window.remaining_percentage` — a **new wall** (context headroom);
- `cost.total_cost_usd` — the estimated session **spend tally**, handed over by
  the CLI rather than computed by brr.

Structurally, **the status line is just another hook**: a command brr registers
in the same `.claude/settings.local.json` it already writes the `PostToolBatch` /
`Stop` / `SessionStart` hooks into, receiving JSON brr captures into the
portal-state quota/cost collector. No streaming, no brnrd-owned key, no API-key
auth required for Claude Code subscription runs.

**Corrected data map.** The earlier "spend live / quota edge-only" split was a
*per-signal* law; it's really a **per-vessel** asymmetry:

| Vessel | Spend tally | Subscription quota level | Context window | How it arrives |
| --- | --- | --- | --- | --- |
| Claude Code (subscription) | `cost.total_cost_usd` (handed over) | `rate_limits.*` (used % + resets_at) | `context_window.remaining_percentage` | **statusLine JSON** (a hook-shaped collector) |
| Codex (subscription) | **derived**, not handed over (tokens × price table) | edge-only / TUI-only (not exposed to headless `exec`) | computable from token counts | **`token_count` events** — live via `codex exec --json`, post-hoc via `$CODEX_HOME/sessions/*.jsonl`; **no external statusLine command** |
| Any API-key auth | response usage | `anthropic-ratelimit-*` headers | — | per-call headers |
| brnrd-owned key | authoritative | authoritative | — | brnrd reads it (the live rail brnrd sells, §2) |

So for Claude Code — brr's own default vessel — **both walls and the context
window are level-readable today**, cheaply, off one collector. The honest
caveats remain: `cost.total_cost_usd` is *estimated* and `rate_limits` is
*consumed%* (so headroom = `100 − used`); historical org/admin usage APIs stay
async → pre-analysis, never the live card.

**(c) The Codex vessel exposes usage too — but in a different shape, derived not
handed over (researched 2026-06-28, evt-9yvh).** The maintainer asked to find
Codex's analog to the Claude statusLine finding ("I am sure they have
something"). They do — and the shape matters for the build:

- **No external statusLine command.** Codex's status line is *declarative* —
  `[tui] status_line = ["context-usage", "used-tokens", "five-hour-limit",
  "weekly-limit", …]` in `~/.codex/config.toml`, rendered inside Codex's own
  TUI. It "does not currently use the same external statusline command model as
  Claude Code," so there is **no command seam for brr to register** the way
  `brr statusline` plugs into Claude. The statusLine-collector pattern does not
  port.
- **Token usage *is* emitted, via `token_count` events.** Codex writes
  `event_msg` entries with `payload.type == "token_count"` carrying cumulative
  input / cached-input / output / reasoning token counts (since codex commit
  0269096, 2025-09-06). Two rails to read them: **live** off the
  `codex exec --json` / `--experimental-json` NDJSON stream (brr drives codex
  headless via `codex exec`, but does *not* pass `--json` today, so this is a
  real change to how brr parses codex output, not a tweak); **post-hoc** off the
  session rollout logs in `$CODEX_HOME/sessions/` + `archived_sessions/` (this
  is what `ccusage` reads).
- **Spend is derived, not handed over.** Unlike Claude's `cost.total_cost_usd`,
  Codex gives token *counts* — you price them yourself (tokens × a per-model
  rate table, the way ccusage applies the LiteLLM dataset). So the Codex spend
  facet is a *computed* number with its own price-table dependency, a heavier
  collector than Claude's read-the-number one. Context-window headroom is
  likewise computable from `used-tokens` vs the model's context size.
- **Subscription quota stays edge-only.** The 5-hour / weekly limits surface in
  Codex's interactive TUI and via `/status` (interactive only — not available
  to a headless `codex exec` run). Programmatic exposure is *requested but not
  shipped*: open OpenAI issues #15281 ("expose full usage/limits data in CLI"),
  #19555 ("show remaining credits/usage in statusline"), #17827 ("customizable
  status line"). So §8's "Codex quota = edge-only" holds — the only headless
  signal is near-limit error/suggestion text at the wall, until OpenAI exposes
  it or brnrd owns the key.

**Consequence for the build.** A Codex level collector is a *second shape*, not a
port of the Claude one: parse `token_count` events (verify the `--json` event
schema first — pitfall: fire it before you rule on it) and apply a price table
for spend, compute context headroom from token counts, and accept quota as
edge-only. It also partly *reopens* the stream question for Codex specifically:
hooks can carry Claude's usage because Claude has a usage hook; Codex has none,
so its live-usage substrate is the `--json` `token_count` stream (or the session
log tail) — the retired loom's mechanism, scoped to the one vessel that needs
it. Lower priority than the Claude collector that is now wired.

**Design consequence for the build.** Build order is now **statusLine collector →
populate the quota/cost/context facets → distance-card** — all on the hooks rail,
no streaming prerequisite. Keep the §4 guardrail: the card shows *consumption-so-
far + reset windows + historical rank*, **never a forward dollar promise**
(`cost.total_cost_usd` is past spend, not a projection — safe).

**The rename is now a sanctioned follow-up run, deliberately not folded into the
boundary work.** `runner` is embedded across config keys (`runner`,
`runner_cmd`), prompts (`runners.md`), kb page names (`design-runner-*`), and
code (`resolve_runner`, runner profiles). It is a wide, mechanical blast that
earns its own dedicated run with a migration shim for live config — kept
separate so a behavioural change (this boundary enrichment) and a pure rename
do not tangle in one diff.

## 4. Cost manifests per medium, and the respawn navigation matrix

**Maintainer:** *"Cost manifests per medium (not sure how)"* and the respawn
matrix — *"a sorted / heat-mapped matrix giving clear navigation by price per
token, grouped by medium type, noting whether already successfully used,
followed by the subscription quotas ranked beside the matrix."*

This is the structured `runner_media` portal facet already sketched in
[`design-runner-media.md`](design-runner-media.md) §Quota and credit signals,
read as a *navigation surface* rather than a flat string. The manifest per
medium = the medium's row: model, provider, owner, cost class, cost_rank
(price-per-token proxy), quota source + freshness, hook capability, billing
posture, and **whether it was already used successfully this thread**. The
matrix = those rows sorted/heat-mapped by cost_rank and grouped by medium type;
the quota rankings sit beside it as the subscription view.

Crucial guardrail from [`plan-cost-aware-cockpit.md`](plan-cost-aware-cockpit.md):
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
[`design-runner-media.md`](design-runner-media.md) §Implementation sequence step
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
need for a stronger medium). The fairness contract is *transparency*: provider
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
- **Vocabulary:** `medium` was picked (evt-go5z) but **reopened** evt-tw6t —
  the live fork is `medium` (summon) vs `vessel` (incarnate); rename run holds
  until it settles. (§3)
- **The boundary card is a standing level capsule with edge-gated re-injection;**
  distance-from-envelope (min across walls) is its spine. (§7, evt-tw6t)
- **Cost-data source (corrected evt-e1gl):** the stream-json loom is retired; the
  live cost/quota source is the **hooks rail**. For the Claude vessel, the
  `statusLine` JSON (a hook-shaped collector) hands over spend
  (`cost.total_cost_usd`), subscription quota level (`rate_limits.*` + resets),
  and context-window headroom — so the old "spend-live / quota-edge-only" split is
  really **per-vessel**, and Claude is level-readable on both walls today. (§8)
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
- **`cost`→`spend` rename + new `context_window` level facet**, plus a per-vessel
  `levels_collector` switch (empty slot reads `absent` on a medium with a
  collector, `unimplemented` without one). (§1, §8)
- **`brr portal facets`** — operator-inspectable catalogue of the implemented
  facets (schema-only outside a wake; live status folded in inside one). Fixed
  `_portal_state_path` to honour `BRR_PORTAL_STATE` so on-demand inspection
  resolves the live portal without `--path`.
- **Claude statusLine collector** (`statusline.py` + `brr statusline`): registered
  in the same `.claude/settings.local.json` as the hooks; each footer fire
  normalizes the session JSON into a level snapshot the daemon folds into the
  facets. Parse is defensive — the JSON field schema is still **unverified
  against a live Claude run** (pitfall: fire it before you rule on it).

**Open forks / next builds:**
- **The rename run** (§3) — `runner` → `medium`/`resident`, its own dedicated run.
- **Smoke-verify the Claude statusLine schema** against a live run and tighten
  `statusline.parse_session` (drop the defensive fallbacks once the real field
  nesting of `rate_limits` / `cost` / `context_window` is confirmed).
- **Codex level collector** (§8c, researched evt-9yvh) — a *second shape*: parse
  `token_count` events (live via `codex exec --json`, or the
  `$CODEX_HOME/sessions/*.jsonl` tail) and apply a per-model price table for
  spend; compute context headroom from token counts; quota stays edge-only.
  Heavier than the Claude collector (derived, not handed over); lower priority.
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
already named in `design-runner-media.md` step 5), the daemon spawns one fresh
run seeded from the **committed run branch** (the interim work receipt always
survives, §5), carrying a respawn-depth counter in run meta and a hard cap
(e.g. ≤2) so a persistently-walling task escalates to the user instead of
looping. *Ambiguous* failures never auto-respawn — they surface with the receipt
attached. The guard is the point: the respawn exists to survive a breach cheaply,
not to retry blindly.

## See also

- [`design-runner-back-channel.md`](design-runner-back-channel.md) — the boundary
  mechanism (native hooks; the injection rail).
- [`design-runner-media.md`](design-runner-media.md) — the medium/cost layer and
  the structured `runner_media` facet behind the matrix.
- [`plan-cost-aware-cockpit.md`](plan-cost-aware-cockpit.md) — cost
  self-awareness, the historical-pre-analysis guardrail, operator legibility.
- [`decision-llm-relay.md`](decision-llm-relay.md) — BYO-free / paid-relay
  pricing spine.
- [`plan-failover-compute.md`](plan-failover-compute.md) — compute-host failover,
  the sibling axis to medium failover.
