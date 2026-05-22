# Design: brr.run protocol — wire format between brr daemons and brr.run

**Status: proposed, not yet accepted.** Scope and contracts for the
protocol that ties brr daemons to brr.run. Covers both the
**managed-gates** path (events flow in via hosted bots, drain
through a daemon long-poll) and the **failover-compute** path
(when a user's daemon is offline, brr.run spawns a per-task
sandbox in the user's or its own cloud account). Both daemon-side
adapters and the brr.run service build against this page; once
accepted, the wire format is the boundary that lets the two sides
ship independently.

Renamed from `design-managed-gates.md` on 2026-05-22 when the
spawn-compute path joined the protocol; the gates work is now one
of several payloads the protocol carries.

## Scope

In scope:

- The daemon-side `cloud` gate adapter — protocol, lifecycle,
  configuration, failure semantics.
- The brr.run-side REST API surface the daemon adapter and the
  brr.run-internal spawn paths talk to: account / pairing
  endpoints, inbox endpoints, platform webhook endpoints (Telegram,
  GitHub App), cloud-credential storage endpoints, failover-spawn
  endpoints.
- The event-shape translation between Telegram Bot API updates /
  GH App webhook events and the brr in-process event format that
  `.brr/inbox/` consumers already understand.
- The failover dispatch decision (laptop-online → forward;
  laptop-offline → spawn) and the per-task spawn flow.
- Cloud-credential storage and scoping (per-platform tokens stored
  encrypted on brr.run for the failover-compute path).
- Failure modes (offline daemon, lost messages, spawn failure,
  replay) and the operational concerns brr.run must address (rate
  limits, multi-daemon per account, per-tenant isolation,
  per-tenant cost ceilings).

Out of scope, explicitly:

- The brr.run service implementation itself (lives in a separate
  repo; this page is its API spec, not its code).
- Payment / billing surfaces beyond per-task accounting hooks (the
  pricing model lives in
  [`decision-pricing-shape.md`](decision-pricing-shape.md)).
- The BYO Telegram / GitHub gates already shipped — those stay
  exactly as they are; the cloud gate is an additional adapter, not
  a replacement.
- Slack / Discord / GitLab adapters (same protocol; separate
  rollout per
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)).

## The protocol shape, at a glance

```
┌──────────────────────┐                ┌─────────────────────────┐
│   User's TG chat /   │                │   User's brr daemon     │
│   GH PR / GH issue   │                │   (laptop / cloud-app)  │
└──────┬──────────┬────┘                └────────┬────────────────┘
       │ user msg │                              │
       ▼          │                              │ long-poll
┌─────────────┐   │                              │ /v1/daemons/inbox
│ @brr_bot /  │   │                              │
│ brr.run app │───┴─────► brr.run dispatch ◄─────┤
└─────────────┘   webhook        │      response │
                                 │      forward  │
                                 ▼               ▼
                  ┌─────────────────────┐  POST /v1/daemons/
                  │ daemon online?      │  responses
                  │   yes → enqueue ────┘
                  │   no  → spawn ┐
                  └───────────────┤
                                  ▼
                  ┌─────────────────────────────┐
                  │ per-task ephemeral sandbox  │
                  │ (user's cloud OR brr.run's) │
                  │ executes runner; pushes     │
                  │ branch; POSTs response;     │
                  │ tears down                  │
                  └─────────────────────────────┘
```

Three primary flows, all stateless from the daemon's perspective:

1. **Ingress.** Telegram / GitHub sends a webhook to brr.run.
   brr.run translates the event to brr's wire format and decides
   the dispatch route.
2. **Dispatch.**
   - **Daemon online** → enqueue to the user's daemon inbox queue;
     the daemon long-polls
     `GET /v1/daemons/inbox?since=<cursor>` and drains it,
     writing each event to `.brr/inbox/<event-id>.json` the same
     way a BYO gate would.
   - **Daemon offline AND failover enabled** → call the user's
     configured cloud-runner adapter server-side, spawn a per-task
     sandbox, hand the event to it, await response.
3. **Response.** Whoever ran the task (daemon or sandbox) POSTs to
   `POST /v1/daemons/responses`. brr.run forwards it to the
   originating channel.

The daemon's task pipeline is **unchanged** — only the transport
layer for events and responses is new. The existing BYO gates write
to `.brr/inbox/` and read from `.brr/responses/`; the cloud-gate
adapter is a peer, not a replacement. The failover-spawn path
reuses the same cloud-runner adapters the daemon would use for BYO
compute, called from brr.run server-side instead of from the
daemon — same adapter code, two callers.

## Daemon side — the cloud-gate adapter

### Configuration

```ini
# .brr/config
[gate.cloud]
brr_run_url = https://api.brr.run            ; default; override for self-hosted brr.run
api_key_env = BRR_RUN_API_KEY                 ; env var name to read the token from
daemon_name = my-laptop                       ; human-readable, free-form
long_poll_seconds = 25                        ; how long each poll waits before returning empty
```

The API key is issued by brr.run at signup; the daemon never
generates one. `daemon_name` lets a user run multiple daemons under
one account (laptop, home server, cloud-hosted) and have brr.run
route events to the right one (see "Multi-daemon routing" below).

### Lifecycle

The cloud-gate is a long-running gate thread, peer to the existing
`telegram` / `slack` / `github` gates:

| Phase | What the adapter does |
|-------|----------------------|
| **start** | Authenticates to brr.run with the API key. Registers itself with `POST /v1/daemons/register` (declares `daemon_name`, capabilities). Begins long-poll loop. |
| **drain (per poll)** | `GET /v1/daemons/inbox?since=<cursor>`. Returns 0+ events. For each, writes `.brr/inbox/<event-id>.json` and advances the cursor. |
| **respond** | Watches `.brr/responses/` for new files. For each response paired to a cloud-originated event, POSTs to `/v1/daemons/responses`. |
| **shutdown** | Cancels in-flight long-poll. `POST /v1/daemons/deregister` so brr.run marks this daemon offline; queued events stay queued; failover may kick in for future events depending on user config. |

The adapter is stateless beyond `since=<cursor>` and the
upload-acknowledged set; both persist to a small JSON file under
`.brr/cloud-gate/` so a daemon restart doesn't re-process events
or re-send responses.

### Event translation

brr.run normalises each platform's webhook payload into one event
shape before queuing. The daemon writes the normalised form to
`.brr/inbox/<event-id>.json` with no further translation:

```json
{
  "event_id": "ev_01HX...",
  "kind": "telegram_message" | "github_issue_comment" | "github_pr_comment" | "github_pr_review_comment",
  "received_at": "2026-05-22T19:30:00Z",
  "source": {
    "platform": "telegram" | "github",
    "channel": "<chat-id>" | "<owner>/<repo>#<issue-or-pr-number>",
    "user": "<platform-user-id>"
  },
  "task": {
    "title": "...",
    "body": "...",
    "metadata": { ... }
  },
  "reply_to": {
    "platform": "telegram",
    "chat_id": 123456789,
    "message_id": 42
  }
}
```

`reply_to` carries everything brr.run needs to post the response
back to the right place. The daemon never sees the user's raw
platform identifier — `source.user` is an opaque account-scoped
string.

### Response shape

The daemon (or failover sandbox) POSTs to `/v1/daemons/responses`:

```json
{
  "event_id": "ev_01HX...",
  "reply_to": { ... },               ; echoed from the event
  "body_markdown": "...",
  "attachments": [],                  ; reserved for v-next
  "status": "done" | "error" | "conflict"
}
```

brr.run translates `body_markdown` to platform-native formatting
(Markdown V2 for Telegram, GitHub-flavoured Markdown for GH)
before posting. `status` drives whether the platform message gets
a check / cross / warning glyph for at-a-glance triage.

## brr.run side — REST API surface

### Account / pairing endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/accounts` | Create account (email + password, or OAuth bind). Returns account ID + initial API key. |
| `POST` | `/v1/accounts/sessions` | Login; returns a session JWT for web / CLI use. |
| `POST` | `/v1/accounts/api-keys` | Issue an additional API key (multiple daemons, rotation). |
| `DELETE` | `/v1/accounts/api-keys/{key_id}` | Revoke. |
| `POST` | `/v1/accounts/pair/telegram` | Initiate a Telegram pairing — returns a one-time pairing code valid for 10 min. |
| `POST` | `/v1/accounts/pair/github` | Initiate a GitHub App install flow — returns the install URL with `state=` encoding the account. |

### Inbox endpoints (daemon-facing)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/daemons/register` | Register a daemon name + capabilities. Idempotent on `daemon_name`. |
| `POST` | `/v1/daemons/deregister` | Mark daemon offline. Queued events stay queued for next register; failover may kick in for future events. |
| `GET` | `/v1/daemons/inbox?since=<cursor>` | Long-poll; returns events. `since=null` to start from oldest queued. |
| `POST` | `/v1/daemons/responses` | Post a response for one event (callable by daemon OR by a failover sandbox carrying a one-shot token). |

All require `Authorization: Bearer <api-key>` (long-lived account
key) OR `Authorization: Bearer <task-key>` (short-lived per-task
token issued when a failover sandbox spawns; scoped to a single
`event_id`).

### Webhook endpoints (platform-facing)

| Method | Path | Source |
|--------|------|--------|
| `POST` | `/v1/webhooks/telegram` | Telegram Bot API update — single bot, multiplexed by chat_id |
| `POST` | `/v1/webhooks/github` | GitHub App webhook — multiplexed by `installation.id`; signature verified per request |

Both are authenticated by the platform's own signing mechanism
(Telegram bot token secret in URL, GitHub `X-Hub-Signature-256`).

### Cloud-credential endpoints (failover setup)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/accounts/cloud-credentials` | Store an encrypted credential for one platform. Body: `{platform: "fly" | "modal" | "daytona" | "e2b" | "codespaces", token: "...", scope: {...}}`. |
| `GET` | `/v1/accounts/cloud-credentials` | List stored credentials (id, platform, created_at, last_used_at, scope summary). Never returns the secret material. |
| `DELETE` | `/v1/accounts/cloud-credentials/{credential_id}` | Revoke. Stops future failover spawns on that platform; in-flight tasks complete. |
| `POST` | `/v1/accounts/failover-policy` | Set per-account failover policy: `{enabled: bool, preferred_platform: "...", monthly_spawn_cap: int, monthly_cost_cap_usd: int}`. |
| `GET` | `/v1/accounts/failover-policy` | Read current policy + usage counters. |

The credential storage shape is per-platform because each platform
has distinct token / scope semantics:

| Platform | Credential shape | Recommended scope |
|----------|------------------|-------------------|
| Fly Machines | API token | App-scoped, machine-create + machine-destroy on one `brr-failover` app |
| Modal | Token id + secret | Workspace-scoped, sandbox.create + sandbox.exec + sandbox.terminate |
| Daytona | Personal access token | Org-scoped if SaaS; URL + token for self-hosted |
| E2B | API key | Workspace-scoped, sandbox.create + sandbox.commands.run |
| Codespaces | GitHub PAT with `codespace` + `repo` scopes | Repo-scoped where possible; otherwise account-scoped with the `codespace` scope only |

Credentials are encrypted at rest using per-account envelope keys,
never logged, and never returned through any GET endpoint after
storage.

### Failover-spawn endpoints (internal-facing)

These are called by brr.run's dispatcher, not directly by clients;
documented here because they're part of the protocol surface that
the failover-compute adapters consume.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/internal/dispatch/{event_id}` | Internal dispatcher entry point. Decides daemon-online → enqueue OR daemon-offline → spawn. |
| `POST` | `/v1/internal/spawns` | Record a spawn attempt (account_id, event_id, platform, started_at). |
| `PATCH` | `/v1/internal/spawns/{spawn_id}` | Update spawn outcome (finished_at, exit_code, cost_estimate_usd). Drives billing accounting per [`decision-pricing-shape.md`](decision-pricing-shape.md). |

## Pairing flow

### Telegram

```
1. User: `brr accounts pair telegram` on the box running their daemon
   → CLI calls POST /v1/accounts/pair/telegram, gets `pairing_code = "BR1234"`
   → CLI prints: "Send `/start BR1234` to @brr_bot"

2. User: messages @brr_bot with `/start BR1234`
   → Telegram delivers update to brr.run via webhook
   → brr.run matches BR1234 to the pending pair request, binds
     (account_id, chat_id) into chat_bindings table
   → @brr_bot replies "Paired. Send me tasks anytime."

3. User: sends a real task to @brr_bot
   → brr.run looks up chat_id → account_id → list of online daemons
   → if any daemon is online: enqueues event per the routing policy
   → if none online AND failover enabled: spawns per-task sandbox
   → if none online AND failover disabled: queues until a daemon returns
```

### GitHub

```
1. User: `brr accounts pair github`
   → CLI calls POST /v1/accounts/pair/github, gets the GitHub App
     install URL with `state=` encoding account_id
   → CLI opens the URL in browser; user installs the brr.run App on
     selected repos

2. GitHub: POSTs installation webhook to brr.run
   → brr.run reads `state` from the install event payload, binds
     (account_id, installation_id) and (account_id, repo_full_name)
     for each selected repo into chat_bindings

3. User: opens a PR / issue, comments `@brr <task>`
   → GitHub delivers issue_comment webhook to brr.run
   → brr.run validates @brr mention, looks up installation_id →
     account_id → dispatch (online daemon OR failover spawn)
```

### Cloud-credential setup (failover)

```
1. User: `brr accounts add-credential fly`
   → CLI prompts for token (paste from `flyctl auth token`)
   → CLI POSTs to /v1/accounts/cloud-credentials with platform="fly"
   → brr.run encrypts and stores; returns credential_id

2. User: `brr accounts failover --enable --platform fly --monthly-cap 100`
   → CLI POSTs to /v1/accounts/failover-policy
   → brr.run flips failover_enabled = true for the account, sets caps

3. From now on: any event arriving while no daemon is online and
   spawn-count/cost-this-month is under cap triggers a per-task
   spawn on the configured platform.
```

## Failover dispatch

When an event arrives at brr.run and no daemon is online, the
dispatcher walks this decision tree:

```
1. Is failover enabled for this account?
     no  → enqueue event; wait for daemon to come back (existing path)
     yes → continue
2. Are we under the monthly spawn cap AND monthly cost cap?
     no  → enqueue event + notify user via gate ("failover cap hit,
            event queued; raise cap or run daemon to resume")
     yes → continue
3. Is the configured platform's stored credential present AND
   last validation < N hours old?
     no  → enqueue event + notify user via gate ("failover credential
            invalid; reconnect to resume")
     yes → continue
4. Spawn per-task sandbox via the platform's cloud-runner adapter,
   server-side, with:
     - the stored token (decrypted in memory only)
     - the event payload
     - a one-shot task-key (Bearer token scoped to this event_id,
       valid 1 hour, single use for POST /v1/daemons/responses)
5. The sandbox:
     - clones the repo (git token from the GH App install OR a
       per-account ssh deploy key, depending on user setup)
     - runs the runner CLI on the task
     - pushes the resulting branch back to the user's remote
     - POSTs the response with the task-key
     - tears itself down on clean exit
6. brr.run records the spawn outcome (cost, duration, exit code)
   for billing accounting.
```

The decision is per-event, not per-account-session — the user can
have failover enabled and still have their daemon take the next
event after this one if they come back online.

## Multi-daemon routing

A user with multiple daemons (laptop + home server) needs a policy
for which daemon takes a given event. Three policies, configurable
per binding:

| Policy | Behaviour |
|--------|-----------|
| `first-online` (default) | Route to whichever registered daemon polled most recently; fail over silently if it goes offline mid-task. |
| `pinned:<daemon_name>` | Always route to this daemon. Queue (or failover-spawn, per policy) if it's offline; surface a warning after N minutes. |
| `fanout` | Send to every online daemon; first one to respond wins. Reserved for v-next (requires response-cancellation protocol). |

`fanout` is intentionally out of launch scope to keep the protocol
simple; the first two cover the common cases.

## Failure modes

| Failure | Behaviour |
|---------|-----------|
| Daemon offline when event arrives; failover disabled | Event queues in brr.run inbox; delivered on next poll. 30-day TTL by default; configurable per account. |
| Daemon offline; failover enabled and under caps | Per-task sandbox spawned; result returned via gate; daemon sees the branch on next pull. |
| Daemon offline; failover enabled but cap hit | Event queues; user notified via gate ("cap hit, event queued; raise cap or run daemon to resume"). |
| Daemon dies mid-task | Event remains marked "in-flight" on brr.run until response posts OR `in_flight_ttl` (default 1h) elapses, then re-queues. Daemon dedupes on `event_id` so re-delivery is safe. |
| Failover sandbox dies mid-task | Same `in_flight_ttl` behaviour; re-spawn on retry up to 2 attempts before queuing for daemon return. |
| brr.run unreachable | Daemon retries with exponential backoff; long-poll cycle gracefully degrades. The BYO gate path continues to work — managed and BYO are independent. |
| Response post fails (from daemon) | Daemon retries up to N times with backoff. If brr.run is healthy but rejects (e.g., binding revoked), drop the response and write a trace entry. |
| Response post fails (from failover sandbox) | Sandbox retries up to 3 times; on final failure, writes the response to the user's git remote as a markdown file in `.brr/failover-orphans/<event-id>.md` so it isn't lost. |
| User revokes API key mid-flight | Next long-poll returns 401; daemon logs and exits its cloud-gate thread cleanly. Other gates keep running. |
| User revokes cloud credential mid-flight | In-flight spawns complete; new spawns refuse; events queue with the "credential invalid" notification. |
| Webhook secret rotation | brr.run handles silently; the daemon side is not aware of platform secrets. |

## Operational concerns (brr.run side)

- **Rate limits.** Per-account inbox enqueue rate cap (default
  60 events / minute) to bound abuse from a runaway integration.
  Per-daemon long-poll concurrency cap of 4 (gate thread plus a
  few diagnostic polls). Per-account failover spawn rate cap
  (default 5 spawns / minute) on top of the monthly caps from the
  failover policy.
- **Per-tenant isolation.** Each event payload, inbox row,
  response row, and stored credential is account-scoped; queries
  always go through the account context derived from the API key.
  Cross-account access is a defect, not a possibility.
- **Per-tenant cost ceilings.** The failover-policy monthly cost
  cap is enforced before spawn; cost-estimate of each spawn is
  computed from the platform's pricing (`shared-cpu-1x` * minutes
  for Fly, etc.) and rolled into a running monthly counter. Hard
  stop at cap; user must raise cap or wait for monthly reset.
- **Webhook verification.** Telegram bot token secret embedded in
  the webhook URL; GitHub signing secret verified on every request
  via `X-Hub-Signature-256`. Failed verification logs and 401s.
- **Replay protection.** Event IDs are ULIDs; the inbox table has
  a unique constraint on `(account_id, event_id)` so platform
  retries don't enqueue twice. Spawns are idempotent on
  `(account_id, event_id)` for the same reason.
- **Credential encryption.** Per-account envelope keys; envelope
  keys wrapped by a brr.run-side KMS root key. Decrypted only in
  process memory at spawn time; cleared after spawn completes.
- **Audit log.** Every credential write, credential read at spawn
  time, failover spawn attempt (with outcome), and policy change
  is recorded in an append-only `account_audit` table queryable
  via account-scoped CLI.

## Cloud-token security model

The failover-compute path is the first time brr.run holds
user-owned credentials at all. The trust model:

- **Scope minimisation.** brr.run's onboarding documentation walks
  users through generating the minimum-scope token per platform
  (e.g., Fly app-scoped token, not user-scoped; Codespaces PAT
  scoped to `codespace` only, not `repo:write`). The platform's
  own scoping is the load-bearing layer; brr.run's encryption is
  defense-in-depth.
- **Encryption at rest.** Per-account envelope keys; root key in a
  KMS managed separately from the application database.
- **Encryption in transit.** TLS only; HTTP redirects refuse.
- **No logs.** Token material never enters any log line. Spawn-time
  decryption happens in process memory; the cleartext token is
  passed to the cloud platform SDK / REST call and immediately
  cleared.
- **Easy revoke.** `brr accounts remove-credential <id>` and
  `brr accounts failover --disable` both work without affecting
  in-flight tasks. In-flight tasks complete; new spawns refuse.
- **Per-account audit log.** Every spawn surfaced in
  `brr accounts audit` with timestamp, event id, platform, cost
  estimate, exit status.
- **Blast-radius bound.** Even if brr.run's database is
  compromised, the per-platform tokens grant only the actions
  their scopes permit (spawn / destroy specific resource types in
  specific projects). A fully BYO-cloud user's exposure is the
  same shape as a leaked Fly token — bad, but bounded to one
  platform.

What we do NOT do:

- Store user OAuth refresh tokens that grant broad cloud-account
  access. Per-platform scoped tokens only.
- Hold git-write tokens beyond the duration of one spawn (the GH
  App install delegates this naturally; non-GitHub remotes use a
  per-spawn deploy key the user installs once).
- Allow credential read after write — write-only API surface for
  the secret material itself.

## Out of scope (for this design)

- The brr.run service codebase (separate repo; this page is the
  API spec, not the implementation).
- Detailed billing / invoicing surfaces — the per-task accounting
  hooks above feed into them, but the user-facing billing UI is a
  separate design.
- A web dashboard for managing daemons / bindings / credentials —
  CLI-first; dashboard is v-next.
- The `fanout` multi-daemon policy (deferred per above).
- Server-side spawn for *online* daemons as a convenience layer
  (i.e. "brr.run takes the task even though my daemon is up,
  because the daemon is busy"). Possibly worth doing as a
  load-shedding feature; explicitly deferred until usage shows
  whether it matters.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic context that drives this design (work continuity,
   brr.run-as-failover-dispatcher, three paid surfaces).
2. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   for the gate-adapter implementation sequencing (GH App slice
   first, TG bot adapter fast-follow on the same backend).
3. [`plan-failover-compute.md`](plan-failover-compute.md) for the
   failover-spawn implementation sequencing (cloud-credential
   storage, dispatcher decision tree, the cloud-runner adapter
   server-side caller).
4. [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   for the cross-adapter patterns the failover spawn shares with
   the daemon-side BYO-compute path.
5. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the pricing model the per-task accounting hooks feed.
6. [`src/brr/gates/README.md`](../src/brr/gates/README.md) for the
   existing BYO gate protocol the cloud gate is peer to.

## Lineage

- 2026-05-22 — drafted (as `design-managed-gates.md`) as part of
  the managed-mode KB shape rollout. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-22 — renamed to `design-brr-run-protocol.md` and grown
  with the spawn-compute / failover-dispatch path when the
  work-continuity reframe shifted the always-on-box answer to
  brr.run-as-failover-dispatcher; cloud-credential storage and
  the dispatcher decision tree added. Pondering follow-up in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (reframe breadcrumb).
