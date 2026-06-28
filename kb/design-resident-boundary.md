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

## 8. What the medium actually exposes for live cost/quota (pre-build finding)

**Question (maintainer):** before building the collectors, clarify exactly what
we can get from the medium re: live cost and quota. This is load-bearing for the
card (§7), because a card promising a smooth quota gauge it can't fill is a lie
the resident learns to distrust.

The honest answer splits into two signals of **very different data quality**, and
that asymmetry *is* the open-source/brnrd boundary (§2) showing up again:

- **Live consumption (this run, as it goes) — meterable, but only as a tally brr
  computes, and only via the streaming driver.** When brr holds the
  `stream-json` loom (the streaming medium in `plan-streaming-runner-injection.md`,
  **not yet built**), each message carries usage — input/output/cache token
  counts, and for Claude a `costUSD` (with caveats). That's the real "as you go"
  cost signal: a *running consumption tally brr accumulates*, not a balance the
  CLI hands over. This is the realistic source of "distance from the spend wall."
  Crucial nuance: it measures **consumption, not remaining** — brr counts what's
  spent, it doesn't read a headroom number.
- **Quota / credit *level* (subscription headroom) — mostly NOT exposed.** This
  is the hard part to be honest about. Claude Code and Codex **subscription**
  quotas surface chiefly as **error text and near-limit suggestions**, not as a
  readable gauge (confirmed in the CLI probe table in `design-runner-media.md`).
  So for subscription media the quota wall is **edge-triggered** (you learn you're
  near it when the CLI warns/errors), not a smooth level. A clean readable
  *remaining* number exists only for: **API-key auth** (rate-limit headers),
  **brnrd-owned keys** (brnrd reads them authoritatively), and **managed cloud**
  (provider quota API). Historical org/admin usage APIs (OpenAI, Anthropic) are
  async + admin-cred → they feed pre-analysis, never the live card.

**Design consequence for the build.** The card's two walls have asymmetric data:
the **spend wall is live-meterable** (token tally via streaming) and can show a
real running number; the **subscription-quota wall is edge-only** and can show
`ok / warned / hit` but not a smooth gauge — unless brnrd owns the key, which is
exactly the live authoritative rail brnrd sells (§2). So: **build the consumption
tally first** (it has real data and directly feeds distance-from-spend-wall);
treat subscription quota as edge-triggered until brnrd/API-key auth makes it
level-readable. And keep the §4 guardrail: the card shows *consumption-so-far +
historical rank*, **never a forward dollar promise**. Build order is therefore
streaming medium → consumption tally → distance-card, because the *live* half of
the card depends on brr holding the stream.

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
- **Cost-data asymmetry:** live consumption is meterable via the streaming
  medium; subscription quota is edge-only unless brnrd/API-key auth. (§8,
  evt-tw6t)
- Failover = cheap-recovery + visible receipt + honest escalation, not a perfect
  classifier; PR posture (incl. not-yet-created) joins the boundary. (§5)
- Business posture reconciles with open-source via transparent
  paid-everywhere-it-fits. (§6)

**Shipped (evt-go5z):**
- Three-state facet honesty (`known`/`absent`/`unimplemented` + `required`),
  PR-not-created posture, `long_running`, and no-outbound-at-closeout — across
  the JSON portal, the woven hook line, and `brr portal state`. (§1)

**Open forks / next builds:**
- **The rename run** (§3) — `runner` → `medium`/`resident`, its own dedicated run.
- **Populate the `known` values:** live quota/cost collectors so a facet carries
  a real number, not just an honest `absent`. The matrix (§4) and the
  near-empty-quota mid-run injection both depend on it.
- **Single projection helper** so the three renderers can never drift in *which*
  facets they carry. (§1)

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
