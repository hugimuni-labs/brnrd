# Design: brr.run protocol — wire format between brr daemons and brr.run

**Status: proposed, not yet accepted.** Scope and contracts for the
protocol that ties brr daemons to brr.run. Covers the
**managed-gates** path (events flow in via hosted bots, drain
through a daemon long-poll), the **failover-compute** path (when a
user's daemon is offline, brr.run spawns a per-task sandbox in
brr.run's own cloud account), **multi-project routing** (one global
bot serves many of a user's projects), and the **permission-gate**
API (ask before spawning). Both daemon-side adapters and the
brr.run service build against this page; once accepted, the wire
format is the boundary that lets the two sides ship independently.

Renamed from `design-managed-gates.md` on 2026-05-22 when the
spawn-compute path joined the protocol. Reshaped on 2026-05-25 to
drop BYO cloud-platform tokens (managed compute uses brr.run's own
cloud account), add the AI-credential vault, the multi-project
routing protocol, the permission-gate API, and the data-
minimization principle.

## Scope

In scope:

- The daemon-side `cloud` gate adapter — protocol, lifecycle,
  configuration, failure semantics.
- The brr.run-side REST API surface the daemon adapter and the
  brr.run-internal spawn paths talk to: account / pairing
  endpoints, inbox endpoints, platform webhook endpoints (Telegram,
  GitHub App), AI-credential storage endpoints, failover-policy
  endpoints, permission-prompt endpoints, project endpoints.
- The event-shape translation between Telegram Bot API updates /
  GH App webhook events and the brr in-process event format that
  `.brr/inbox/` consumers already understand.
- **Multi-project routing**: how brr.run resolves
  `(event-source) → project_id` so one bot can serve many of a
  user's projects.
- The failover dispatch decision tree (laptop-online → forward;
  laptop-offline → ask-or-spawn) and the per-task spawn flow.
- The AI-credential vault on brr.run (encrypted at rest;
  API-key and credential-dir-upload payload shapes; used at
  failover spawn time).
- The **permission-prompt API** for ask-before-spawn UX.
- The **data minimization principle** (what brr.run does and
  doesn't persist).
- Failure modes (offline daemon, lost messages, spawn failure,
  replay) and the operational concerns brr.run must address (rate
  limits, multi-daemon per account, per-tenant isolation,
  per-tenant cost ceilings, audit-log shape).

Out of scope, explicitly:

- The brr.run service implementation itself (lives at
  `src/brr_run/` in the monorepo per
  [`decision-monorepo-structure.md`](decision-monorepo-structure.md);
  this page is its API spec, not its code).
- Payment / billing surfaces beyond per-task accounting hooks (the
  pricing model lives in
  [`decision-pricing-shape.md`](decision-pricing-shape.md)).
- **BYO cloud-platform tokens** for failover spawn (Fly / Modal /
  Daytona / etc. tokens stored on brr.run). Designed shape
  preserved as a *deferred* sketch in "BYO compute — designed,
  deferred" below; not built at launch. Daemon-side cloud-runner
  adapters (user's daemon fans out to user's cloud) remain
  independent of managed mode — those are user-driven plugin work,
  not part of brr.run.
- The BYO Telegram / GitHub gates already shipped — those stay
  exactly as they are; the cloud gate is an additional adapter,
  not a replacement.
- Slack / Discord / GitLab adapters (same protocol; separate
  rollout per
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)).

## Data minimization — the load-bearing principle

brr.run is a thin dispatcher + a credential vault. User content
lives on the daemon. Bake the following into every endpoint:

- **Event content is transient.** Body kept only until dispatched
  to a daemon OR a failover sandbox. After dispatch: drop body,
  keep metadata (event_id, timestamp, account, source platform,
  project_id, dispatch outcome). Audit trail, not a content
  archive.
- **Response bodies pass through, not stored.** brr.run forwards
  the response to the originating gate and logs metadata only
  (status, length, ms-to-respond).
- **Conversation history lives on the daemon side**, never
  mirrored to brr.run. The dashboard renders live by querying the
  daemon when online; no shadow copy on brr.run.
- **AI credentials encrypted at rest** with per-account envelope
  keys + a separately-held KMS root key. Decrypted in process
  memory at spawn time only; cleared immediately after spawn
  hand-off.
- **Audit log is metadata-only** — who, when, what platform, what
  outcome, what cost. Never task contents.
- **Account email separated from credential storage** — different
  tables, joined only at the API surface, so a partial DB leak
  doesn't compound.

This shapes trust ("brr.run doesn't have your code"), bounds breach
blast radius, and matches the OSS-self-hostable framing (we hold
less; users hold their data). Each endpoint below is annotated
with what it persists and for how long.

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
│ brr.run app │───┴────►  brr.run dispatch ◄─────┤
└─────────────┘   webhook        │      response │
                                 │      forward  │
                                 ▼               ▼
                  ┌─────────────────────┐  POST /v1/daemons/
                  │ resolve project_id  │  responses
                  │ daemon online?      │
                  │   yes → enqueue ────┘
                  │   no  → policy check
                  │         ask?  → permission-prompt via gate
                  │         spawn? → managed-compute Fly Machine
                  └────────────────────┐
                                       ▼
                  ┌─────────────────────────────┐
                  │ per-task ephemeral sandbox  │
                  │ (brr.run's Fly account)     │
                  │ AI creds from vault         │
                  │ git access via GH App       │
                  │ runs runner; pushes branch; │
                  │ POSTs response; tears down  │
                  └─────────────────────────────┘
```

Four flows, all stateless from the daemon's perspective:

1. **Ingress.** Telegram / GitHub sends a webhook to brr.run.
   brr.run translates the event to brr's wire format, **resolves
   the project_id** (per-platform rules below), and proceeds to
   dispatch.
2. **Dispatch — daemon online.** Enqueue to the user's daemon
   inbox queue; the daemon long-polls
   `GET /v1/daemons/inbox?since=<cursor>` and drains it, writing
   each event to `.brr/inbox/<event-id>.json` the same way a BYO
   gate would.
3. **Dispatch — daemon offline.** Walk the failover-policy
   decision tree (see "Failover dispatch" below). Outcome is one
   of: auto-spawn now; post permission prompt via the gate and
   await user; queue until daemon returns.
4. **Response.** Whoever ran the task (daemon or sandbox) POSTs
   to `POST /v1/daemons/responses`. brr.run forwards it to the
   originating channel, logs metadata, drops the body.

The daemon's task pipeline is **unchanged** — only the transport
layer for events and responses is new. The existing BYO gates write
to `.brr/inbox/` and read from `.brr/responses/`; the cloud-gate
adapter is a peer, not a replacement. The failover-spawn path
reuses the same cloud-runner adapter the daemon would use if it
were running, called from brr.run server-side against brr.run's
own cloud account — same adapter code, different caller, different
token.

## Multi-project routing

One managed bot per platform serves all of a user's projects. The
event needs to land in the right project's inbox. Resolution is
per-source:

| Source | Resolution |
|--------|-----------|
| GitHub App webhook | `(installation_id, repo_full_name) → project_id` via `project_bindings` table. Naturally per-repo; no UX needed. |
| Telegram message | `(account_id, chat_id) → sticky_project_id` from `chat_project_bindings`, with per-message `@project ...` or `/project <name> <task>` prefix override. |
| Slack message | Same shape as Telegram (`(account_id, channel_id) → sticky_project_id` + prefix override). |
| Discord message | Same shape as Telegram. |
| GitLab MR comment (future) | Same shape as GH (`(installation_id, project_path) → project_id`). |

TG / Slack / Discord command surface for managing bindings:

| Command | Behaviour |
|---------|-----------|
| `/connect <project-name>` | Bind current chat to project. Replaces any previous binding for that chat. |
| `/project <name> <task>` | Per-message override; routes this task to a different project without changing the sticky binding. |
| `@project-name <task>` | Same as above, terse form. |
| `/projects` | List the account's projects with their bound chats and daemon status. |
| `/status` | Show current chat's project: name, daemon online/offline, queue depth, recent activity. |

Resolution failure (no binding, ambiguous prefix, unknown project)
returns a friendly error via the gate, never silently drops the
event. The full normalised event always carries the resolved
`project_id`:

```json
{
  "event_id": "ev_01HX...",
  "kind": "telegram_message" | "github_issue_comment" | ...,
  "received_at": "2026-05-25T01:30:00Z",
  "account_id": "acc_01HX...",
  "project_id": "prj_01HX...",
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

`source.user` is an opaque account-scoped string; the daemon never
sees raw platform IDs.

## Daemon side — the cloud-gate adapter

### Configuration

```ini
# .brr/config
[gate.cloud]
brr_run_url = https://api.brr.run            ; default; override for self-hosted brr.run
api_key_env = BRR_RUN_API_KEY                 ; env var name to read the token from
daemon_name = my-laptop                       ; human-readable, free-form
project_id  = prj_01HX...                     ; this daemon belongs to which project
long_poll_seconds = 25                        ; how long each poll waits before returning empty
```

One daemon = one project. Users with multiple projects run one
daemon per project (on the same host or different hosts). The
`project_id` is the daemon's anchor — brr.run uses it for routing.
For users running their daemon on a small box with N projects in
parallel, N daemons is fine (they share the host but stay
independently configured).

The API key is issued by brr.run at signup; the daemon never
generates one. `daemon_name` lets a user run multiple daemons under
one account (laptop, home server) and have brr.run route events to
the right one (see "Multi-daemon routing" below).

### Lifecycle

The cloud-gate is a long-running gate thread, peer to the existing
`telegram` / `slack` / `github` gates:

| Phase | What the adapter does |
|-------|----------------------|
| **start** | Authenticates to brr.run with the API key. Registers itself with `POST /v1/daemons/register` (declares `daemon_name`, `project_id`, capabilities). Begins long-poll loop. |
| **drain (per poll)** | `GET /v1/daemons/inbox?since=<cursor>`. Returns 0+ events scoped to this daemon's project. For each, writes `.brr/inbox/<event-id>.json` and advances the cursor. |
| **respond** | Watches `.brr/responses/` for new files. For each response paired to a cloud-originated event, POSTs to `/v1/daemons/responses`. |
| **shutdown** | Cancels in-flight long-poll. `POST /v1/daemons/deregister` so brr.run marks this daemon offline; queued events stay queued; failover may kick in for future events depending on policy. |

The adapter is stateless beyond `since=<cursor>` and the
upload-acknowledged set; both persist to a small JSON file under
`.brr/cloud-gate/` so a daemon restart doesn't re-process events
or re-send responses.

### Response shape

The daemon (or failover sandbox) POSTs to `/v1/daemons/responses`:

```json
{
  "event_id": "ev_01HX...",
  "reply_to": { ... },               ; echoed from the event
  "body_markdown": "...",
  "status": "done" | "error" | "conflict"
}
```

brr.run translates `body_markdown` to platform-native formatting
(Markdown V2 for Telegram, GitHub-flavoured Markdown for GH)
before posting, then drops the body. `status` drives whether the
platform message gets a check / cross / warning glyph for
at-a-glance triage.

## brr.run side — REST API surface

### Account / pairing / project endpoints

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/accounts` | Create account (email + password, or OAuth bind). Returns account ID + initial API key. | Email (hashed), password hash, account row |
| `POST` | `/v1/accounts/sessions` | Login; returns a session JWT for web / CLI use. | Session row (TTL) |
| `POST` | `/v1/accounts/api-keys` | Issue an additional API key. | API-key hash + metadata |
| `DELETE` | `/v1/accounts/api-keys/{key_id}` | Revoke. | Mark revoked |
| `POST` | `/v1/accounts/projects` | Create a project. Body: `{name}`. Returns `project_id`. | Project row (name, account_id) |
| `GET` | `/v1/accounts/projects` | List the account's projects (id, name, daemon count, last activity). | Read-only |
| `DELETE` | `/v1/accounts/projects/{project_id}` | Delete project. Cascades to bindings; in-flight events drain. | Hard delete |
| `POST` | `/v1/accounts/pair/telegram` | Initiate a Telegram pairing — returns a one-time pairing code valid for 10 min. | Pairing row (TTL) |
| `POST` | `/v1/accounts/pair/github` | Initiate a GitHub App install flow — returns the install URL with `state=` encoding the account. | Install-intent row (TTL) |

### Inbox endpoints (daemon-facing)

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/daemons/register` | Register a daemon name + project_id + capabilities. Idempotent on `(daemon_name, project_id)`. | Daemon row (online state, last-seen) |
| `POST` | `/v1/daemons/deregister` | Mark daemon offline. Queued events stay queued; failover may kick in for future events. | Daemon row update |
| `GET` | `/v1/daemons/inbox?since=<cursor>` | Long-poll; returns events scoped to this daemon's project. `since=null` to start from oldest queued. | Read-only; advances cursor |
| `POST` | `/v1/daemons/responses` | Post a response for one event (callable by daemon OR by a failover sandbox carrying a one-shot token). Body forwarded to gate, dropped. | Metadata row (status, length, ms); no body |

All require `Authorization: Bearer <api-key>` (long-lived account
key) OR `Authorization: Bearer <task-key>` (short-lived per-task
token issued when a failover sandbox spawns; scoped to a single
`event_id`).

### Webhook endpoints (platform-facing)

| Method | Path | Source | Persists |
|--------|------|--------|----------|
| `POST` | `/v1/webhooks/telegram` | Telegram Bot API update — single bot, multiplexed by chat_id | Event metadata only after dispatch |
| `POST` | `/v1/webhooks/github` | GitHub App webhook — multiplexed by `installation.id`; signature verified per request | Event metadata only after dispatch |

Both are authenticated by the platform's own signing mechanism
(Telegram bot token secret in URL, GitHub `X-Hub-Signature-256`).
Event body dropped from brr.run after dispatch.

### Project-binding endpoints

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/accounts/bindings/chat` | Bind a TG/Slack/Discord chat to a project. Body: `{platform, chat_id, project_id}`. Replaces any previous binding for that chat. | chat_project_bindings row |
| `GET` | `/v1/accounts/bindings/chat` | List the account's chat bindings. | Read-only |
| `DELETE` | `/v1/accounts/bindings/chat/{binding_id}` | Remove. | Hard delete |
| `POST` | `/v1/accounts/bindings/repo` | Bind a GH installation+repo to a project. Body: `{installation_id, repo_full_name, project_id}`. Auto-created on GH App install per default policy; this endpoint exists for re-binding / re-routing. | repo_project_bindings row |
| `GET` | `/v1/accounts/bindings/repo` | List the account's repo bindings. | Read-only |
| `DELETE` | `/v1/accounts/bindings/repo/{binding_id}` | Remove. | Hard delete |

### AI-credential vault endpoints

For failover spawns: brr.run needs the user's AI-runner
credentials to run Claude / Codex / Gemini in the sandbox. The
vault supports two payload shapes — API key or credential
directory tarball — both end up as encrypted blobs in the same
store.

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/accounts/ai-credentials` | Store an encrypted AI credential. Body: `{provider: "anthropic" | "openai" | "google" | "github", shape: "api-key" | "dir-tarball", payload: "..."}`. | Encrypted blob, metadata (provider, shape, created_at) |
| `GET` | `/v1/accounts/ai-credentials` | List stored credentials (id, provider, shape, created_at, last_used_at). Never returns secret material. | Read-only |
| `DELETE` | `/v1/accounts/ai-credentials/{credential_id}` | Revoke. In-flight spawns complete; new spawns refuse. | Hard delete |

CLI surface:

```
brr accounts add-credential anthropic --key sk-ant-...
brr accounts add-credential anthropic --dir ~/.claude
brr accounts add-credential openai --key sk-...
brr accounts add-credential github --key ghp_...
brr accounts list-credentials
brr accounts remove-credential <id>
```

The `--dir` path tars the directory, base64-encodes, uploads. At
spawn time the tarball is decoded into the sandbox's
`$HOME/.claude/` (or wherever the provider expects). This
preserves subscription-auth flows (Claude Pro, Codex Plus, Gemini
OAuth) for users who don't want to provision API keys.

### Failover-policy endpoints

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/accounts/failover-policy` | Set policy: `{enabled: bool, mode: "ask" | "auto-approve-always" | "auto-approve-under-usd" | "auto-approve-under-per-day" | "never", auto_approve_threshold_usd, auto_approve_threshold_per_day, monthly_spawn_cap, monthly_cost_cap_usd}`. | failover_policy row |
| `GET` | `/v1/accounts/failover-policy` | Read current policy + usage counters (spawns-this-month, cost-this-month). | Read-only |

### Permission-prompt endpoints

When `mode = "ask"` and a spawn is pending, brr.run posts a
permission prompt via the gate and awaits user response. Internal
endpoints (called by the dispatcher, not externally):

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/internal/prompts` | Create a permission prompt for an event. Surfaces it via the gate. | prompt row (TTL, status=pending) |
| `PATCH` | `/v1/internal/prompts/{prompt_id}` | Resolve from gate callback (`approve` / `queue` / `never`). | prompt row update |

External callback path the gates use to feed prompt resolutions:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/webhooks/prompts/{platform}/{prompt_id}/{outcome}` | Hit when the user taps Approve / Queue in the gate UI. Platform-specific signing verified. |

The prompt payload posted via the gate carries:

- Estimated runtime (per-machine-size empiric)
- Estimated cost (per-machine per-platform rate table)
- Current free-tier usage (`<percentage>% of monthly cap`)
- Two action buttons: Approve / Queue
- Optional third: Never-ask-again-under-$X (raises auto-approve
  threshold)

### Internal spawn endpoints

These are called by brr.run's dispatcher, not directly by clients;
documented here because they're part of the protocol surface that
the cloud-runner adapter consumes.

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/internal/dispatch/{event_id}` | Internal dispatcher entry point. Decides online → enqueue OR offline → policy → prompt-or-spawn. | Dispatch log row |
| `POST` | `/v1/internal/spawns` | Record a spawn attempt (account_id, project_id, event_id, started_at, est_cost_usd). | spawn row |
| `PATCH` | `/v1/internal/spawns/{spawn_id}` | Update spawn outcome (finished_at, exit_code, actual_cost_usd). Drives billing accounting per [`decision-pricing-shape.md`](decision-pricing-shape.md). | spawn row update |

## Pairing flow

### Telegram

```
1. User: `brr accounts pair telegram --project <project_id>` on the box running their daemon
   → CLI calls POST /v1/accounts/pair/telegram with project_id, gets `pairing_code = "BR1234"`
   → CLI prints: "Send `/start BR1234` to @brr_bot"

2. User: messages @brr_bot with `/start BR1234`
   → Telegram delivers update to brr.run via webhook
   → brr.run matches BR1234 to the pending pair request, binds
     (account_id, chat_id, project_id) into chat_project_bindings
   → @brr_bot replies "Paired with <project_name>. Send me tasks anytime.
                       Switch projects with /project <name> or @<name>."

3. User: sends a real task to @brr_bot
   → brr.run looks up chat_id → (account_id, project_id) → list of online daemons
   → if any daemon is online for that project: enqueues event per the routing policy
   → if none online AND failover enabled: walks the policy decision tree
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
     (account_id, installation_id) and auto-creates one project
     per repo (or prompts in the dashboard if multi-repo install:
     "which projects should these repos belong to?")
   → user adjusts bindings in the dashboard or via
     `brr accounts bind-repo` CLI if defaults don't suit

3. User: opens a PR / issue, comments `@brr <task>`
   → GitHub delivers issue_comment webhook to brr.run
   → brr.run validates @brr mention, looks up
     (installation_id, repo_full_name) → (account_id, project_id) → dispatch
```

### AI-credential setup

```
1. User: `brr accounts add-credential anthropic --key sk-ant-...`
   OR     `brr accounts add-credential anthropic --dir ~/.claude`
   → CLI POSTs to /v1/accounts/ai-credentials with the chosen shape
   → brr.run encrypts and stores; returns credential_id

2. User: repeats for openai / google / github as needed

3. User: `brr accounts failover --enable --mode ask --monthly-cap 100`
   → CLI POSTs to /v1/accounts/failover-policy
   → brr.run flips failover_enabled = true for the account, sets caps
   → Now: any event arriving while no daemon is online and the
     spawn-count is under cap triggers either auto-spawn or a
     permission prompt, per policy.
```

## Failover dispatch

When an event arrives at brr.run, the dispatcher walks this
decision tree:

```
1. Daemon online for this project?
     yes → enqueue (existing path); done
     no  → continue

2. Failover enabled for this account?
     no  → enqueue + notify ("daemon offline; event queued"); done
     yes → continue

3. Under monthly spawn cap AND monthly cost cap?
     no  → enqueue + notify ("failover cap hit, raise cap or run daemon"); done
     yes → continue

4. Required AI credentials present (Anthropic / OpenAI / Google
   matching what this user's project uses; gh token present if
   non-GitHub remote)?
     no  → enqueue + notify ("missing AI credential, add via dashboard");
            done
     yes → continue

5. Policy mode?
     "auto-approve-always"        → spawn
     "auto-approve-under-usd"     → spawn if est_cost < threshold;
                                    else prompt
     "auto-approve-under-per-day" → spawn if today's spawn count
                                    < threshold; else prompt
     "ask"                        → prompt
     "never"                      → enqueue; done

6. (Spawn path) Issue a one-shot task-key, decrypt AI creds into
   process memory, invoke the cloud-runner adapter server-side
   against brr.run's Fly Machines pool with:
     - the AI credentials (env vars or dir-tarball expansion)
     - a per-spawn GH App installation token (push permission)
     - the event payload + project_id
     - the task-key (Bearer scoped to this event_id, 1h TTL,
       single use for POST /v1/daemons/responses)
   Clear AI cred material from memory after hand-off.

7. (Sandbox runs) Sandbox:
     - clones the repo via the GH token
     - runs the runner CLI on the task body
     - pushes the resulting branch back via the GH token
     - POSTs the response with the task-key
     - tears itself down on clean exit

8. brr.run records the spawn outcome (cost, duration, exit code,
   project_id) for billing accounting and audit log.
```

The decision is per-event, not per-account-session — the user can
have failover enabled and still have their daemon take the next
event after this one if they come back online mid-decision.

## Multi-daemon routing

A user with multiple daemons for the same project (laptop + home
server) needs a policy for which daemon takes a given event. Three
policies, configurable per project binding:

| Policy | Behaviour |
|--------|-----------|
| `first-online` (default) | Route to whichever registered daemon polled most recently; fail over silently if it goes offline mid-task. |
| `pinned:<daemon_name>` | Always route to this daemon. Walk failover-policy if it's offline; surface a warning after N minutes. |
| `fanout` | Send to every online daemon; first one to respond wins. Reserved for v-next (requires response-cancellation protocol). |

`fanout` is intentionally out of launch scope to keep the protocol
simple; the first two cover the common cases.

## Failure modes

| Failure | Behaviour |
|---------|-----------|
| Daemon offline when event arrives; failover disabled | Event queues in brr.run inbox; delivered on next poll. 30-day TTL by default; configurable per account. |
| Daemon offline; failover enabled and under caps; policy=ask | Permission prompt posted via gate; resolution drives spawn-or-queue. Prompt TTL 6h (configurable). |
| Daemon offline; failover enabled and under caps; policy=auto-approve | Per-task sandbox spawned; result returned via gate; daemon sees the branch on next pull. |
| Daemon offline; failover enabled but cap hit | Event queues; user notified via gate ("cap hit, raise cap or run daemon to resume"). |
| Daemon dies mid-task | Event remains marked "in-flight" on brr.run until response posts OR `in_flight_ttl` (default 1h) elapses, then re-queues. Daemon dedupes on `event_id` so re-delivery is safe. |
| Failover sandbox dies mid-task | Same `in_flight_ttl` behaviour; re-spawn on retry up to 2 attempts before queuing for daemon return. |
| brr.run unreachable | Daemon retries with exponential backoff; long-poll cycle gracefully degrades. The BYO gate path continues to work — managed and BYO are independent. |
| Response post fails (from daemon) | Daemon retries up to N times with backoff. If brr.run is healthy but rejects, drop the response and write a trace entry. |
| Response post fails (from failover sandbox) | Sandbox retries up to 3 times; on final failure, writes the response to the user's git remote as `.brr/failover-orphans/<event-id>.md` so it isn't lost. |
| User revokes API key mid-flight | Next long-poll returns 401; daemon logs and exits its cloud-gate thread cleanly. Other gates keep running. |
| User revokes AI credential mid-flight | In-flight spawns complete; new spawns refuse with "missing credential" notification. |
| Permission prompt expires (TTL) | Auto-queue with a "permission timed out, event queued" notification. User can run the task later by sending it again from the daemon-side. |
| Webhook secret rotation | brr.run handles silently; the daemon side is not aware of platform secrets. |

## Operational concerns (brr.run side)

- **Rate limits.** Per-account inbox enqueue rate cap (default
  60 events / minute) to bound abuse from a runaway integration.
  Per-daemon long-poll concurrency cap of 4 (gate thread plus a
  few diagnostic polls). Per-account failover spawn rate cap
  (default 3 spawns / minute) on top of the monthly caps from the
  failover policy.
- **Per-tenant isolation.** Each event payload, inbox row,
  response row, project, binding, and stored credential is
  account-scoped; queries always go through the account context
  derived from the API key. Cross-account access is a defect, not
  a possibility.
- **Per-tenant cost ceilings.** The failover-policy monthly cost
  cap is enforced before spawn; cost-estimate of each spawn is
  computed from the platform's pricing (`shared-cpu-1x@1GB` *
  minutes for Fly) and rolled into a running monthly counter.
  Hard stop at cap; user must raise cap or wait for monthly reset.
- **Webhook verification.** Telegram bot token secret embedded in
  the webhook URL; GitHub signing secret verified on every request
  via `X-Hub-Signature-256`. Failed verification logs and 401s.
- **Replay protection.** Event IDs are ULIDs; the inbox table has
  a unique constraint on `(account_id, event_id)` so platform
  retries don't enqueue twice. Spawns are idempotent on
  `(account_id, event_id)` for the same reason.
- **AI-credential encryption.** Per-account envelope keys;
  envelope keys wrapped by a brr.run-side KMS root key.
  Decrypted only in process memory at spawn time; cleared after
  spawn completes.
- **Audit log.** Every credential write, credential read at
  spawn time, failover spawn attempt (with outcome), permission
  prompt resolution, project-binding change, and policy change is
  recorded in an append-only `account_audit` table queryable via
  account-scoped CLI / dashboard. Metadata-only, never task
  contents.

## AI-credential security model

The trust model:

- **Scope minimisation in onboarding.** brr.run's onboarding
  documentation walks users through generating the minimum-scope
  AI token per provider (Anthropic API key with usage limit, GH
  PAT with `repo` + `read:user` only, etc.). The provider's own
  scoping is the load-bearing layer; brr.run's encryption is
  defense-in-depth.
- **Encryption at rest.** Per-account envelope keys; root key in
  a KMS managed separately from the application database.
- **Encryption in transit.** TLS only; HTTP redirects refuse.
- **No logs.** Token material never enters any log line.
  Spawn-time decryption happens in process memory; the cleartext
  token is passed to the runner CLI / API call and immediately
  cleared.
- **Easy revoke.** `brr accounts remove-credential <id>` and
  `brr accounts failover --disable` both work without affecting
  in-flight tasks. In-flight tasks complete; new spawns refuse.
- **Per-account audit log.** Every spawn surfaced in
  `brr accounts audit` with timestamp, event_id, project_id,
  cost estimate, exit status, AI provider used.
- **Blast-radius bound.** Even if brr.run's database is
  compromised, the per-provider tokens grant only what their
  scopes permit (Anthropic API usage; GH push to specific repos;
  etc.). Exposure shape ~ a leaked AI API key — bad, but bounded
  and quickly revocable from the provider side.

What we do NOT do:

- Store user OAuth refresh tokens that grant broad provider
  access. Per-provider scoped tokens only.
- Hold git-write tokens beyond the duration of one spawn (the GH
  App install delegates this naturally; non-GitHub remotes use a
  per-spawn deploy key the user installs once).
- Allow credential read after write — write-only API surface for
  the secret material itself.
- Persist event/response bodies, conversation history, or repo
  contents. See "Data minimization" above.

## BYO compute — designed, deferred

The earlier draft of this page had BYO cloud-platform tokens
(Fly / Modal / Daytona / etc. stored on brr.run, used to spawn in
the user's account) as a launch surface. Dropped from launch on
2026-05-25 because the implementation cost was disproportionate
to the user value at launch — see
[`decision-pricing-shape.md`](decision-pricing-shape.md) for the
rationale.

The wire shape that supports BYO is small and additive when we
come back to it:

- New endpoint family `/v1/accounts/cloud-credentials`
  (POST/GET/DELETE) parallel to the AI-credential vault, storing
  per-platform tokens encrypted.
- One new field in failover-policy: `compute_target:
  "brr-managed" | "fly:user" | "modal:user" | …` defaulting to
  `"brr-managed"`.
- One branch in the dispatcher's spawn step: select adapter +
  token based on `compute_target`.

Adapter code is identical (same cloud-runner plugin called either
way); only the token source and the cost-accounting side differ
(BYO doesn't bill brr.run-side compute; user pays own cloud bill).

Daemon-side cloud-runner adapters (a laptop daemon fans out to
the user's cloud via a `brr-env-*` plugin) remain independent of
managed mode entirely. Those are user-driven plugin work, not
part of brr.run, and ship per
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
on their own clock.

## Upsun deployment notes (brr.run backend)

brr.run hosts on Upsun for the launch prototype (per
[`decision-monorepo-structure.md`](decision-monorepo-structure.md);
self-hosters can target Fly / Render / Heroku / etc. via
parallel templates). Upsun's read-only-application-container
shape imposes constraints that the design accommodates:

- **No `/app` runtime writes.** All persistent state lives in
  postgres (provided by Upsun add-on) or a writable mount (e.g.
  `/data/state/` for any file-based scratch, audit log
  flush buffers).
- **Build phase ≠ deploy phase.** `build:` step installs deps
  with no DB access. `deploy:` step runs alembic migrations,
  injects route-dependent env vars (webhook URL, dashboard URL),
  primes any caches. Routes-yaml declares the webhook + API + web
  service URLs.
- **Workers**: the dispatcher runs as a separate worker process
  in `.upsun/config.yaml`; same image as the web service. Spawn
  invocations happen from the worker, not the web tier.
- **Postgres add-on** is the primary state store. No connection
  pooler needed at launch volumes; revisit if connection counts
  climb.
- **No docker-in-docker.** Sandbox spawns reach out to Fly
  Machines via REST API; no local docker on Upsun.
- **Secrets**: KMS root key, GH App private key, Telegram bot
  token, Fly Machines pool token all in Upsun's encrypted secret
  store, read at runtime via env vars.

The brr daemon-hosting Upsun template (in
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md))
shares the same constraints and benefits from the same patterns —
write the Upsun shape once, use it twice.

## Out of scope (for this design)

- The brr.run service codebase (lives at `src/brr_run/` in the
  monorepo; this page is the API spec, not the implementation).
- Detailed billing / invoicing surfaces — the per-task accounting
  hooks above feed into them, but the user-facing billing UI is a
  separate design.
- The brr.run dashboard (covered in
  [`plan-brr-run-dashboard-mvp.md`](plan-brr-run-dashboard-mvp.md)
  — uses these REST endpoints as a client).
- The `fanout` multi-daemon policy (deferred per above).
- Server-side spawn for *online* daemons as a convenience layer
  (i.e. "brr.run takes the task even though my daemon is up,
  because the daemon is busy"). Possibly worth doing as a
  load-shedding feature; explicitly deferred until usage shows
  whether it matters.
- The agentic-mode upgrade to brr.run (proactive scheduling,
  cross-project secretary behaviours, platform-level connectors).
  See
  [`decision-connectors-layering.md`](decision-connectors-layering.md)
  for the layering that makes that upgrade path coherent.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic context (two surfaces, work continuity, brr.run as
   thin dispatcher + credential vault).
2. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the pricing model the per-task accounting hooks feed.
3. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   for the gate-adapter implementation sequencing (GH App slice
   first, TG bot fast-follow on the same backend).
4. [`plan-failover-compute.md`](plan-failover-compute.md) for
   the failover-spawn implementation sequencing (AI-credential
   vault, dispatcher decision tree, brr.run-owned Fly pool,
   permission gate API).
5. [`plan-brr-run-dashboard-mvp.md`](plan-brr-run-dashboard-mvp.md)
   for the dashboard built on top of these endpoints.
6. [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   for the cross-adapter patterns the server-side caller uses.
7. [`decision-connectors-layering.md`](decision-connectors-layering.md)
   for why gates stay per-project and connectors live at the
   platform level.
8. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   for where `src/brr_run/` lives and how the dashboard / plugins
   relate.
9. [`src/brr/gates/README.md`](../src/brr/gates/README.md) for the
   existing BYO gate protocol the cloud gate is peer to.

## Lineage

- 2026-05-22 — drafted (as `design-managed-gates.md`) as part of
  the managed-mode KB shape rollout. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-22 — renamed to `design-brr-run-protocol.md` and grown
  with the spawn-compute / failover-dispatch path when the
  work-continuity reframe shifted the always-on-box answer to
  brr.run-as-failover-dispatcher; cloud-credential storage and
  the dispatcher decision tree added.
- 2026-05-25 — reshaped: BYO cloud-platform tokens dropped from
  launch (preserved as a "designed, deferred" section); managed
  compute consolidates on brr.run-owned cloud; AI-credential
  vault added (api-key + dir-tarball shapes); multi-project
  routing protocol added (project_id resolution, sticky/prefix
  for TG/Slack/Discord); permission-prompt API added
  (`/v1/internal/prompts` + gate-side webhooks); data
  minimization principle added as a load-bearing section
  governing every endpoint; Upsun deployment notes added.
  Pondering follow-up in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1 (third
  reframe breadcrumb).
