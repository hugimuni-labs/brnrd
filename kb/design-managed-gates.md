# Design: managed gates — cloud-gate adapter and brr.run inbox-as-service

**Status: proposed, not yet accepted.** Scope and contracts for the
managed-gates tier of [managed mode](subject-managed-mode.md). Both
the daemon-side cloud-gate adapter and the brr.run service that
backs it build against this page; once accepted, the wire format is
the boundary that lets the two sides ship independently.

## Scope

In scope:

- The daemon-side `cloud` gate adapter — protocol, lifecycle,
  configuration, failure semantics.
- The brr.run-side REST API surface the daemon adapter talks to:
  account / pairing endpoints, inbox endpoints, platform webhook
  endpoints (Telegram, GitHub App).
- The event-shape translation between Telegram Bot API updates / GH
  App webhook events and the brr in-process event format that
  `.brr/inbox/` consumers already understand.
- Failure modes (offline daemon, lost messages, replay) and the
  operational concerns brr.run must address (rate limits, multi-daemon
  per account, per-tenant isolation).

Out of scope, explicitly:

- The brr.run service implementation itself (lives in a separate
  repo; this page is its API spec, not its code).
- Payment / billing surfaces (manual invoicing for launch tier).
- The BYO Telegram / GitHub gates already shipped — those stay
  exactly as they are; the cloud gate is an additional adapter, not
  a replacement.
- Slack / Discord / GitLab adapters (same protocol; separate rollout
  per [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)).

## The protocol shape, at a glance

```
┌──────────────────────┐                ┌────────────────────────┐
│   User's TG chat /   │                │   User's brr daemon    │
│   GH PR / GH issue   │                │   (anywhere)           │
└──────┬──────────┬────┘                └────────┬───────────────┘
       │ user msg │                              │
       ▼          │                              │ long-poll
┌─────────────┐   │                              │ /v1/daemons/inbox
│ @brr_bot /  │   │                              │
│ brr.run app │───┴────────► brr.run ◄───────────┤
└─────────────┘    webhook    inbox    response  │
                              queue    forward   │
                                ▲     ┌──────────┴────────────┐
                                │     │ POST /v1/daemons/     │
                                └─────┤ responses             │
                                      └───────────────────────┘
```

Three loops, all stateless from the daemon's perspective:

1. **Ingress.** Telegram / GitHub sends a webhook to brr.run.
   brr.run translates the event to brr's wire format, routes it to
   the correct account's daemon inbox queue.
2. **Drain.** The daemon long-polls
   `GET /v1/daemons/inbox?since=<cursor>`. brr.run returns any
   queued events; the daemon writes them to
   `.brr/inbox/<event-id>.json` the same way a BYO gate would.
3. **Response.** When the daemon's task produces a response in
   `.brr/responses/<event-id>.md`, the cloud-gate adapter POSTs it
   to `POST /v1/daemons/responses`. brr.run forwards it to the
   originating channel.

The daemon's task pipeline is **unchanged** — only the transport
layer for events and responses is new. The existing BYO gates write
to `.brr/inbox/` and read from `.brr/responses/`; the cloud-gate
adapter is a peer, not a replacement.

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
| **shutdown** | Cancels in-flight long-poll. `POST /v1/daemons/deregister` so brr.run marks this daemon offline; queued events stay queued for next start. |

The adapter is stateless beyond `since=<cursor>` and the
upload-acknowledged set; both persist to a small JSON file under
`.brr/cloud-gate/` so a daemon restart doesn't re-process events or
re-send responses.

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

The daemon POSTs to `/v1/daemons/responses`:

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
(Markdown V2 for Telegram, GitHub-flavoured Markdown for GH) before
posting. `status` drives whether the platform message gets a check /
cross / warning glyph for at-a-glance triage.

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
| `POST` | `/v1/daemons/deregister` | Mark daemon offline. Queued events stay queued for next register. |
| `GET` | `/v1/daemons/inbox?since=<cursor>` | Long-poll; returns events. `since=null` to start from oldest queued. |
| `POST` | `/v1/daemons/responses` | Post a response for one event. |

All require `Authorization: Bearer <api-key>`. All daemon-scoped
endpoints are account-scoped — the API key identifies the account;
`daemon_name` identifies the daemon within that account.

### Webhook endpoints (platform-facing)

| Method | Path | Source |
|--------|------|--------|
| `POST` | `/v1/webhooks/telegram` | Telegram Bot API update — single bot, multiplexed by chat_id |
| `POST` | `/v1/webhooks/github` | GitHub App webhook — multiplexed by `installation.id`; signature verified per request |

Both are authenticated by the platform's own signing mechanism
(Telegram bot token secret in URL, GitHub `X-Hub-Signature-256`).

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
   → enqueues event per the routing policy (see "Multi-daemon" below)
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
     account_id → online daemons, enqueues event
```

## Multi-daemon routing

A user with multiple daemons (laptop + home server) needs a policy
for which daemon takes a given event. Three policies, configurable
per binding:

| Policy | Behaviour |
|--------|-----------|
| `first-online` (default) | Route to whichever registered daemon polled most recently; fail over silently if it goes offline mid-task. |
| `pinned:<daemon_name>` | Always route to this daemon. Queue and wait if it's offline; surface a warning after N minutes. |
| `fanout` | Send to every online daemon; first one to respond wins. Reserved for v-next (requires response-cancellation protocol). |

`fanout` is intentionally out of launch scope to keep the protocol
simple; the first two cover the common cases.

## Failure modes

| Failure | Behaviour |
|---------|-----------|
| Daemon offline when event arrives | Event queues in brr.run inbox; delivered on next poll. 30-day TTL by default; configurable per account. |
| Daemon dies mid-task | Event remains marked "in-flight" on brr.run until response posts OR `in_flight_ttl` (default 1h) elapses, then re-queues. Daemon dedupes on `event_id` so re-delivery is safe. |
| brr.run unreachable | Daemon retries with exponential backoff; long-poll cycle gracefully degrades to polling at increasing intervals. The BYO gate path continues to work — managed and BYO are independent. |
| Response post fails | Daemon retries up to N times with backoff. If brr.run is healthy but rejects (e.g., binding revoked), drop the response and write a trace entry. |
| User revokes API key mid-flight | Next long-poll returns 401; daemon logs and exits its cloud-gate thread cleanly. Other gates keep running. |
| Webhook secret rotation | brr.run handles silently; the daemon side is not aware of platform secrets. |

## Operational concerns (brr.run side)

- **Rate limits.** Per-account inbox enqueue rate cap (default
  60 events / minute) to bound abuse from a runaway integration.
  Per-daemon long-poll concurrency cap of 4 (gate thread plus a few
  diagnostic polls).
- **Per-tenant isolation.** Each event payload, inbox row, and
  response row is account-scoped; queries always go through the
  account context derived from the API key. Cross-account access is
  a defect, not a possibility.
- **Webhook verification.** Telegram bot token secret embedded in
  the webhook URL; GitHub signing secret verified on every request
  via `X-Hub-Signature-256`. Failed verification logs and 401s.
- **Replay protection.** Event IDs are ULIDs; the inbox table has a
  unique constraint on `(account_id, event_id)` so platform retries
  don't enqueue twice.

## Out of scope (for this design)

- The brr.run service codebase (separate repo; this page is the API
  spec, not the implementation).
- Payment / billing — manual invoicing for launch tier.
- A web dashboard for managing daemons / bindings — CLI-first;
  dashboard is v-next.
- The `fanout` multi-daemon policy (deferred per above).

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic context that drives this design (two-dimension split,
   monetisation-at-launch).
2. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   for the implementation sequencing (GH App slice first, TG bot
   adapter fast-follow on the same backend).
3. [`src/brr/gates/README.md`](../src/brr/gates/README.md) for the
   existing BYO gate protocol the cloud gate is peer to.

## Lineage

- 2026-05-22 — drafted as part of the managed-mode KB shape rollout.
  Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
