# Decision: gates vs connectors — the layering split

**Status: proposed, not yet accepted on 2026-05-25.** Names two
distinct kinds of integration that look similar at first glance
(both speak to platforms; both carry messages) but live at
different layers of the brr stack and serve different purposes.
Locking the split now keeps future agentic-mode work from
collapsing them into one undifferentiated category.

## Decision

Two integration kinds, two layers, one stack:

| | **Gates** | **Connectors** |
|--|----------|----------------|
| Scope | Per-project (one binding = one project) | Per-account (platform-wide; agent uses across projects) |
| Direction | Inbound (events → daemon) and reactive outbound (responses to those events) | Outbound (agent reaches out) and pull (agent reads), proactive |
| Layer | brr daemon (BYO) or brnrd (managed dispatcher) | brnrd platform — used by the agentic-mode layer when it lands |
| Trust posture | Per-project secret scope; project owns the secret | Per-account secret scope; account owns the secret; finer-grained per-action consent |
| Example | Telegram bot, GitHub App, Slack bot, Discord bot | Linear, Notion, Google Calendar, Gmail, Stripe billing, Sentry, internal HTTP webhooks |
| Today | Shipped (BYO TG / GH / Slack); managed variant in progress | Doesn't exist; placeholder for the agentic-mode upgrade path |

Gates ship at launch (managed-mode variant). Connectors don't —
they're the upgrade path for when brnrd grows an agentic
secretary layer, and are named here so the layering doesn't
collapse later under "everything is a gate."

## Why the split

The framing came from noticing that the "what if brnrd had a
Google Apps integration" question was ambiguous: was it a gate (a
new way to receive tasks, scoped to one project) or a connector
(a tool the agentic layer reaches into across projects)? The
answer depends on the use case:

- **"Send the deploy log to the project's Slack channel after a
  successful run"** — gate-like. Per-project. Scoped to one
  channel. Lives on the daemon today; would be a per-project
  managed-mode binding if hosted.
- **"Notice that a stripe payment failed on Tuesday, ping the
  three projects whose code is in the failing payment flow"** —
  connector-like. Platform-wide. Crosses projects. Requires the
  agentic layer to know all of the user's projects and reach out
  to Stripe directly with the user's auth.

Conflating the two creates two bad failure modes:

1. **Gate-shaped connector**: configuring Google Calendar as a
   per-project gate creates N copies of the same calendar
   binding, one per project, each with its own OAuth dance —
   redundant, confusing, security-fragile.
2. **Connector-shaped gate**: making Telegram a platform-wide
   resource that any project can post to means a per-project
   permission boundary issue when projects share an account but
   shouldn't share each other's chat output.

The split keeps the per-project security and ergonomic story
clean (one bot = one project's voice; one chat-binding = one
project) while leaving room for a proactive secretary that
naturally crosses projects without each gate needing to be
re-bound per project.

## What "agentic mode" actually means here

A future brnrd capability — *not in launch scope* — where
brnrd runs a small persistent agent on the account level
(distinct from the per-task runners) that:

- knows the user's projects (from the dispatcher state)
- knows their bindings (from the dispatcher state)
- knows their AI credentials (from the vault, scoped use)
- has access to **connectors** for proactive behaviours: read
  calendar, schedule, file tickets in Linear, post weekly
  summaries to a chat, react to webhook events, etc.

The connectors are how this layer reaches *out* into the rest of
the user's world without sitting in each project's daemon. The
projects keep their existing per-project gates for *inbound*
event-driven work.

This shape avoids the "everything is a gate" anti-pattern where
each project would have to wire up the same calendar / ticket-
tracker / log-aggregator binding redundantly.

## Connector candidates (not exhaustive; for sizing only)

- **Linear** — read issues, write issues, transition tickets.
  High value for a project-management secretary use case.
- **Notion** — read pages, write pages. Good for "summarise the
  week and update the wiki" use cases.
- **Google Calendar / Outlook** — read events, propose blocks,
  remind. The secretarial bread-and-butter.
- **Gmail / Outlook mail** — read selectively, send notifications.
- **GitHub Projects / GitLab boards** — read state, transition
  cards. (Note: GitHub *App* is a gate; GitHub *Projects API*
  used proactively is a connector. Same auth, different layer.)
- **Stripe** — read transactions, surface anomalies. Finance
  awareness for solo founders.
- **Sentry / Logtail** — read error feeds, propose fixes
  proactively.
- **Internal HTTP webhook** — generic outbound hook the user
  configures with their own URL + auth shape; the swiss-army for
  anything not on the list above.

None of these ship at launch. The list exists to validate that
"connector" is a coherent category before we accept the split.

## What this means for managed mode at launch

Concretely, at launch:

- Only gates exist. No connector concept implemented.
- The brnrd protocol's project-binding API is built for the
  per-project shape (chat ↔ project, repo ↔ project) — see
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
  "Multi-project routing".
- No agentic-secretary endpoint family. No connector vault. No
  cross-project agent state.
- The dashboard MVP doesn't have a "connectors" view.
  [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
  is gate-shaped through and through.

The split lives only in this decision page and in a one-paragraph
mention in `subject-managed-mode.md`. When the agentic-mode upgrade
path is picked up, the connector protocol can be designed cleanly
without retro-fitting it onto the gate API.

## Alternatives considered

### Alt 1 — Single integration kind ("everything is a gate")

Treat Google Calendar / Linear / etc. as just-another-gate at the
per-project level. Rejected because:

- Forces redundant per-project bindings for resources that are
  conceptually account-level (one calendar serves all the user's
  projects).
- Conflates inbound and outbound trust posture. Inbound events
  go through a per-project secret scope (a TG bot token bound to
  one project's chat); outbound actions naturally want
  per-account scope (one Linear token, used by the secretary
  across projects).
- Makes the eventual agentic-mode upgrade messy — the secretary
  needs cross-project view, but the gate API is project-scoped
  by construction.

### Alt 2 — Single integration kind ("everything is a connector")

Treat the TG bot / GH App as account-level connectors with
per-project routing on top. Rejected because:

- Inverts the natural project-as-trust-boundary story: a TG bot
  is *most naturally* bound to one project (it speaks for that
  project's daemon).
- Hides the security shape: a bug in routing means one project's
  daemon talks to another project's chat. With per-project gate
  binding this is impossible by construction.
- The BYO gates we already ship are per-project; flipping them
  to account-level for the managed variant would be inconsistent
  and confusing.

### Alt 3 — Three layers (gates / connectors / per-task tools)

Add a third layer for "tools the runner has during a single task"
(MCP servers, etc.). Rejected because:

- Per-task tooling is already covered by the runner CLI's tool
  configuration (MCP servers in `claude-cli` config, etc.); not
  a brr-level concern.
- Adding a third layer at the brnrd level confuses the
  agentic-mode upgrade path with the per-task runner story.

The two-layer split (gates + connectors) is the load-bearing
distinction; per-task tooling lives entirely in the runner.

## BYO-for-subscribers applies to connectors

When the agentic-secretary layer lands and brings hosted
connectors (Google Calendar / Linear / Notion / Stripe-
billing-read / etc.), the same BYO-for-subscribers principle
that governs cloud compute (per
[`decision-pricing-shape.md`](decision-pricing-shape.md)
§ "Compute: managed vs BYO") applies to connectors:

- **Subscribers can BYO their own OAuth credentials** for any
  connector we ship managed. Same `credentials` table on
  brnrd, new `kind` value (e.g. `connector-oauth` with a
  `provider` discriminator: `google` / `linear` / `notion` /
  …), same per-account envelope-key encryption, same
  subscriber gate on the write + read paths.
- **Free users get managed-only connectors** with brnrd-side
  credentials (which is why connectors are subscriber-only in
  the first place — brnrd carries the connector's per-provider
  OAuth app setup + per-account token storage + provider
  rate-limit pool on behalf of Free users).
- **Self-hosters** run their own connector OAuth apps under
  their own deployment, regardless of subscription tier (the
  "self-hosted brnrd stays always-free with full feature
  parity" promise from
  [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md)).

The mechanic is one pattern across cloud envs (per
[`design-brnrd-protocol.md`](design-brnrd-protocol.md)
§ "BYO compute") AND connectors AND any future subscriber-
only surface where the user has a credential they could
plausibly own — the vault grows a `kind` value, the write +
read paths gate on `subscription.tier == "subscribed"`,
dispatch / call-out chooses the user's credential over the
brnrd-side one when present. One rule, many surfaces, one
vault.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for how
   gates fit into the launch shape.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for
   the per-project binding mechanics that the gate-shape rests
   on.
3. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   for the gate-shaped dashboard that ships at launch (no
   connectors view).
4. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1 for
   the original "agentic secretary" pondering that this decision
   is a layering response to.

## Lineage

- 2026-05-25 — drafted as part of the brnrd reshape that
  collapsed "brnrd" as a separate name into brnrd. The
  agentic-secretary pondering from
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) needed
  a coherent place to live; this page is that place's frame.
- 2026-05-26 (locking pass — BYO connectors) — new "BYO-for-
  subscribers applies to connectors" section pre-applies the
  cloud-compute BYO posture to connectors when the agentic-
  secretary layer lands. Same vault, new `kind` value
  (`connector-oauth` with `provider` discriminator), same
  subscriber gate on write + read. One pattern across compute
  envs + connectors + any future subscriber-only credential
  surface. Driven by the user's "since we charge per paying
  customer we can allow byo everything on top of that, same
  with future agentic secretary feature" framing.
