# Design: brnrd protocol — wire format between brr daemons and brnrd

**Status: accepted 2026-05-26** (locked in PR #40 MR review,
locking pass IV — protocol-shape diagram reshaped for the
**machine-scoped multi-project daemon**, runtime profile
section added codifying async / httpx on the daemon side and
ASGI / FastAPI / asyncpg on the brnrd side; everything else
unchanged from the locking-pass-II shape). Fluid past the
contracts — the diagram + runtime-profile sections may
re-tune as implementation surfaces details, but the wire
contracts and endpoint shapes are stable. Scope and contracts
for the protocol that ties brr daemons to brnrd. Covers the
**managed-gates** path (events flow in via hosted bots, drain
through a daemon long-poll), the **failover-compute** path (when a
user's daemon is offline, brnrd spawns a per-task sandbox in
brnrd's own cloud account), **multi-project routing** (one global
bot serves many of a user's projects), and the **permission-gate**
API (ask before spawning). Both daemon-side adapters and the
brnrd service build against this page; once accepted, the wire
format is the boundary that lets the two sides ship independently.

Originally `design-managed-gates.md`; renamed to
`design-brr-run-protocol.md` on 2026-05-22 when the
spawn-compute path joined the protocol, then renamed to
`design-brnrd-protocol.md` on 2026-05-25 with the
brnrd-as-canonical-name decision. Reshaped on 2026-05-25 to
drop BYO cloud-platform tokens at launch (managed compute uses
brnrd's own cloud account), add the credential vault (AI-runner
credentials + docker-registry credentials in one store), the
multi-project routing protocol, the permission-gate API, the
data-minimization principle, the cross-gate conversation
context machinery (metadata graph + on-demand fetch + TG ring
buffer), and the subscription endpoints. Subscription-state
value names finalised on 2026-05-26 (no "Plus" branding; tier
value is `"subscribed"`, plan codes are `"monthly"` /
`"annual"`). Reshaped again on 2026-05-26 (locking pass) to
**re-introduce BYO compute as a subscriber-only feature at
launch** — the credential vault grew a third `kind`
(`cloud-platform` with a `provider` discriminator); the
dispatcher branches on BYO-cred presence at dispatch time
(same env class, two callers); BYO Fly Machines ships at
launch alongside managed Fly; subsequent clouds get BYO when
they get managed. Same BYO-for-subscribers principle pre-
applies to future agentic-secretary connectors.
On 2026-06-03, brnrd account identity pivoted from
email+password to GitHub OAuth before launch; see
[`decision-brnrd-github-oauth-identity.md`](decision-brnrd-github-oauth-identity.md).
The bearer-token scheme remains brnrd-owned.

## Scope

In scope:

- The daemon-side `cloud` gate adapter — protocol, lifecycle,
  configuration, failure semantics.
- The brnrd-side REST API surface the daemon adapter and the
  brnrd-internal spawn paths talk to: account / pairing
  endpoints, subscription endpoints, inbox endpoints, platform
  webhook endpoints (Telegram, GitHub App), credential vault
  endpoints (AI + docker-registry), failover-policy endpoints,
  permission-prompt endpoints, project endpoints.
- The event-shape translation between Telegram Bot API updates /
  GH App webhook events and the brr in-process event format that
  `.brr/inbox/` consumers already understand.
- **Multi-project routing**: how brnrd resolves
  `(event-source) → project_id` so one bot can serve many of a
  user's projects.
- The failover dispatch decision tree (laptop-online → forward;
  laptop-offline → ask-or-spawn) and the per-task spawn flow.
- The credential vault on brnrd (encrypted at rest; two
  domains in one store — AI-runner credentials with API-key
  or credential-dir-tarball shapes for Anthropic / OpenAI /
  Google / GitHub; and docker-registry credentials for
  private images on ghcr.io / docker.io / etc.).
- The **permission-prompt API** for ask-before-spawn UX.
- The **data minimization principle** (what brnrd does and
  doesn't persist).
- Failure modes (offline daemon, lost messages, spawn failure,
  replay) and the operational concerns brnrd must address (rate
  limits, multi-daemon per account, per-tenant isolation,
  per-tenant cost ceilings, audit-log shape).

Out of scope, explicitly:

- The brnrd service implementation itself (lives at
  `src/brnrd/` in the monorepo per
  [`decision-monorepo-structure.md`](decision-monorepo-structure.md);
  this page is its API spec, not its code).
- Wallet / Stripe / debit / refund mechanics — these are spec'd
  in [`design-billing.md`](design-billing.md); this page only
  exposes the per-task accounting hooks the billing design
  consumes. Pricing model lives in
  [`decision-pricing-shape.md`](decision-pricing-shape.md)).
- **BYO cloud-platform tokens for non-Fly clouds** at launch
  (Modal / Daytona / etc. tokens). Each cloud's BYO ships in
  the same release as its managed support; only Fly ships at
  launch, so only BYO Fly ships at launch. Subscriber-only;
  documented in "BYO compute — subscriber feature, parallel-
  shipped with managed" below. Daemon-side cloud-runner
  adapters (user's daemon fans out to user's cloud) remain
  independent of managed mode — those are user-driven plugin work,
  not part of brnrd.
- The BYO Telegram / GitHub gates already shipped — those stay
  exactly as they are; the cloud gate is an additional adapter,
  not a replacement.
- Slack / Discord / GitLab adapters (same protocol; separate
  rollout per
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)).

## Data minimization — the load-bearing principle

brnrd is a thin dispatcher + a credential vault. User content
lives on the daemon. Bake the following into every endpoint:

- **Event content is transient.** Body kept only until dispatched
  to a daemon OR a failover sandbox. After dispatch: drop body,
  keep metadata (event_id, timestamp, account, source platform,
  project_id, dispatch outcome). Audit trail, not a content
  archive.
- **Response bodies pass through, not stored.** brnrd forwards
  the response to the originating gate and logs metadata only
  (status, length, ms-to-respond).
- **Conversation history lives on the daemon side**, never
  mirrored to brnrd. The dashboard renders live by querying the
  daemon when online; no shadow copy on brnrd.
- **Credentials encrypted at rest** with per-account envelope
  keys + a separately-held KMS root key. Same scheme covers AI
  credentials AND docker-registry credentials (per the
  generalised credential vault). Decrypted in process memory at
  spawn time only; cleared immediately after spawn hand-off
  (AI creds) or after `docker login` completes (registry
  creds).
- **Audit log is metadata-only** — who, when, what platform, what
  outcome, what cost. Never task contents.
- **GitHub identity separated from credential storage** — account
  identity/contact fields and encrypted credentials live in different
  tables, joined only at the API surface, so a partial DB leak
  doesn't compound.

This shapes trust ("brnrd doesn't have your code"), bounds breach
blast radius, and matches the OSS-self-hostable framing (we hold
less; users hold their data). Each endpoint below is annotated
with what it persists and for how long.

### What we DO hold (named, scoped, accounted)

For honesty about the edges, these are the things brnrd does
hold on the user's behalf. Each is bounded, TTL'd where it makes
sense, and listed in the audit log:

| Held | Scope | TTL | Why |
|------|-------|-----|-----|
| GitHub identity (`github_id`, current login, optional verified email) | Per account | Lifetime of account | Auth + billing contact; no brnrd password hash |
| Credentials (encrypted at rest) | Per account | Until user revokes | Two domains in one vault: (a) AI-runner credentials (Anthropic / OpenAI / Google / GitHub — API key OR `~/.claude`-style dir tarball for subscription-auth users); (b) Docker-registry credentials for private images. Both required for managed-compute spawns that use them. See "Credential vault endpoints" below. |
| Subscription state (tier, plan, period_end, Stripe customer/subscription IDs) | Per account | Lifetime of account | Subscription leg of billing; see [`design-billing.md`](design-billing.md). Mirrored to account-scope settings as `subscription.tier` for in-band reads. |
| Cumulative-purchase counters (`cumulative_purchased_credits_lifetime`, `cumulative_purchased_usd_lifetime`, `project_cap_unlocked`) | Per account | Lifetime of account; monotonic | Drives the subscriber project-cap unlock (25 → unlimited at $10 cumulative top-ups). Mirrored to account-scope settings as `subscription.project_cap` (3 / 25 / unlimited) and `subscription.project_cap_unlocked` (bool) for in-band reads by the dashboard + daemon. |
| Project bindings (chat ↔ project, repo ↔ project) | Per account | Until unbind / delete | Multi-project routing |
| Event metadata (event_id, gate, source_channel, project_id, conversation_id, branch_name, received_at) | Per account | 30 days live, count-only aggregates after | Cross-gate conversation graph for failover continuity. **No body, no preview, no participant names.** See "Conversation context for failover and dashboard" below |
| Telegram per-chat ring buffer (50 msgs × 72h) | Per chat | 72h | One concession: TG bot API lacks a retroactive `getChatHistory` for our use; the ring buffer makes failover spawns and dashboard rendering work on TG without needing to push history into the user's own infra. Slack / Discord don't need this — their APIs expose history natively |
| Audit log (metadata only) | Per account | 90 days | Cost / spawn / credential-read / prompt-resolution transparency |
| Spawn outcomes (cost, duration, exit code, project_id) | Per account | 12 months (for billing) | Billing accounting + cap enforcement |

Things we explicitly do **not** hold:

- Event bodies (dropped after dispatch)
- Response bodies (pass-through to gate; never persisted)
- Conversation contents beyond the TG ring buffer (rendered live from platform APIs or git on demand)
- Source code, prompts, agent traces, repo state (lives on daemon + git remote)
- Plain-text cloud-platform tokens (Fly / Modal / etc.) — only ever held encrypted at rest in the credential vault for subscribers who opt into BYO compute, never in cleartext logs / metrics / DB
- Per-user OAuth refresh tokens that grant broad provider access
- Password hashes, password reset state, or email-verification state

## The protocol shape, at a glance

```
                       User's local machine
┌─────────────────────────────────────────────────────────────────┐
│  ~/.config/brr/projects.toml      (registry of brr-init'd repos)│
│  ~/.local/state/brr/account/      (brnrd binding, sub status,   │
│                                    cached account-scope config) │
│                                                                 │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐             │
│  │ Project A  │    │ Project B  │    │ Project C  │   …         │
│  │ .brr/inbox │    │ .brr/inbox │    │ .brr/inbox │             │
│  │ brr.toml   │    │ brr.toml   │    │ brr.toml   │             │
│  └─────┬──────┘    └─────┬──────┘    └─────┬──────┘             │
│        │                 │                 │                    │
│        │ inbox / response files            │                    │
│        ▼                 ▼                 ▼                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │     brr daemon  (1 process per machine; multi-project)    │  │
│  │  - per-project inbox poller (asyncio task each)           │  │
│  │  - shared httpx.AsyncClient → brnrd (one HTTP/2 conn)     │  │
│  │  - per-project runner subprocess (env-backend dispatch)   │  │
│  │  - supervised by systemd (Linux) / launchd (macOS)        │  │
│  └────────────────────┬──────────────────────────────────────┘  │
└───────────────────────┼─────────────────────────────────────────┘
                        │ HTTPS, single connection, fans out for
                        │ all projects under this account
                        │   GET  /v1/daemons/inbox?since=<cursor>
                        │   POST /v1/daemons/responses
                        ▼
            ┌──────────────────────────────────┐         ┌─────────────┐
            │  brnrd dispatch (ASGI worker)    │◄────────┤ Telegram /  │
            │  - per-project_id event routing  │ webhook │ GitHub bot  │
            │  - failover-policy decision      │         │ (managed    │
            │  - forward response → gate       │         │  gate)      │
            └──────────────┬───────────────────┘         └──────┬──────┘
                           │                                    ▲
                           │ on daemon offline:                 │
                           │   ask?  → permission prompt via gate
                           │   spawn? → managed-compute Fly Machine
                           ▼
            ┌─────────────────────────────────┐
            │ per-task ephemeral sandbox      │
            │ (brnrd's Fly account, OR        │
            │  subscriber's BYO Fly account   │
            │  via cloud-platform creds)      │
            │ AI creds from vault             │
            │ git access via GH App           │
            │ runs runner; pushes branch;     │
            │ POSTs response; tears down      │
            └─────────────────────────────────┘
```

**Five things to note** about this shape (changes vs the
pre-pass-IV per-project-daemon framing):

1. **One daemon per machine, not per project.** The daemon
   process is a multi-project multiplexer; it discovers
   brr-init'd repos via `~/.config/brr/projects.toml`
   (appended by `brr init`) and runs one asyncio inbox-poller
   task per project. A single supervised systemd / launchd
   unit covers all of them. See
   [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md)
   for the install / discovery / supervisor shape.
2. **One HTTP connection to brnrd, fans out for all projects.**
   A single `httpx.AsyncClient` instance owns the connection
   pool; HTTP/2 multiplexing lets all per-project pollers
   share the connection. Outbound POSTs (responses) ride the
   same client. Means brnrd sees one TCP/TLS connection per
   daemon, not one per project.
3. **Account binding is machine-scoped.** The brnrd account
   binding (auth token, subscription status, brnrd URL,
   cached account-scope config) lives at
   `~/.local/state/brr/account/` — not per repo. When the
   user runs `brnrd connect` from a second project, the
   binding is already there; only the per-project project_id
   binding gets added. See
   [`design-config-layout.md`](design-config-layout.md) §
   "Account scope" for the file layout.
4. **The daemon's task pipeline is unchanged.** The only new
   thing is the transport layer (cloud-gate inbox poller +
   response POSTs). Existing BYO gates write to `.brr/inbox/`
   and read from `.brr/responses/` exactly as before; the
   cloud-gate adapter is a peer, not a replacement.
5. **Failover-spawn reuses the env class.** Cloud envs are
   envs — see
   [`research-cloud-envs.md`](research-cloud-envs.md) →
   "Caller axis." The same Fly-Machines env class that the
   daemon would invoke if BYO is configured gets invoked
   from brnrd's caller axis with brnrd's account credentials
   (managed) OR the subscriber's cloud-platform credentials
   from the vault (BYO subscriber).

Four flows, all stateless from the daemon's perspective:

1. **Ingress.** Telegram / GitHub sends a webhook to brnrd.
   brnrd translates the event to brr's wire format, **resolves
   the project_id** (per-platform rules below), and proceeds to
   dispatch.
2. **Dispatch — daemon online.** Enqueue to the per-project
   inbox queue keyed by project_id; the daemon's long-poll
   on `GET /v1/daemons/inbox?since=<cursor>` returns events
   for **all** the daemon's projects in a single batch,
   tagged with their project_id. The daemon dispatches each
   to the right per-project asyncio task, which writes
   `.brr/inbox/<event-id>.json` in the right repo the same
   way a BYO gate would.
3. **Dispatch — daemon offline.** Walk the failover-policy
   decision tree (see "Failover dispatch" below). Outcome is one
   of: auto-spawn now; post permission prompt via the gate and
   await user; queue until daemon returns.
4. **Response.** Whoever ran the task (daemon or sandbox) POSTs
   to `POST /v1/daemons/responses`. brnrd forwards it to the
   originating channel, logs metadata, drops the body.

## Runtime profile: async, httpx, ASGI

The protocol shape implies a specific runtime profile on
both sides, captured here so the implementation slices know
what they're building against.

### Daemon side (brr — must run on user laptops)

- **`httpx.AsyncClient` for outbound HTTP** to brnrd: one
  client instance per daemon process, connection pool size
  ~2-4 (HTTP/2 multiplexing means more pooled connections
  rarely help). Long-poll uses `client.stream("GET", …)`
  with a long timeout; response POSTs use `client.post(…)`.
- **`asyncio` event loop** owns the daemon's main thread.
  Per-project inbox pollers are asyncio tasks; runner
  subprocesses are spawned via `asyncio.subprocess` so
  stdout / stderr piping doesn't block the loop.
- **`uvloop`** as a soft dep on Linux / macOS (drops in via
  `asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())`
  if importable; falls back to stdlib loop otherwise) for
  the 2-3× speedup on socket-heavy work. No-op on Windows.
- **No web framework on the daemon side.** The daemon doesn't
  serve HTTP itself; it only consumes. Local IPC (CLI ↔
  daemon) is a small asyncio server on a unix socket /
  loopback port, written directly against
  `asyncio.start_unix_server` / `asyncio.start_server`. Keeps
  the dep surface minimal.
- **Constraint: easily pip-installable on stock Python.**
  Any cpython 3.11+ on macOS / Linux / WSL must be enough.
  No native compilation, no big transitive footprint.
  `httpx`, `aiofiles` (optional), `uvloop` (optional),
  `pydantic` for schemas — that's roughly the whole new
  dep set on top of what's already there.

The current daemon is sync (`requests` + thread workers).
The async migration lands as a single slice alongside the
machine-scoped multi-project reshape — both changes touch
the same code paths, doing them together avoids a transitional
shape that's neither one nor the other.

### Brnrd side (must be efficient, can use packages)

- **ASGI via FastAPI (or starlette directly).** brnrd is a
  multi-tenant web service; FastAPI's pydantic
  request/response validation + dependency injection +
  OpenAPI generation are worth their weight. starlette
  directly is the fallback if we ever want zero-FastAPI
  for some reason.
- **`asyncpg` for postgres** (not SQLAlchemy's sync driver).
  Postgres holds the ledger, accounts table, project
  bindings, audit log, conversation metadata graph. All
  hot paths are async.
- **`redis-py` (async client) for the inbox queue + rate
  limiter state.** Daemons long-poll an in-memory + redis-
  backed per-project queue; redis also holds the soft-throttle
  state and the per-IP / per-account rate-limit counters.
- **`httpx` for outbound** (Stripe, GitHub App calls,
  Telegram Bot API, etc.).
- **`structlog` + `sentry-sdk`** for observability;
  **`stripe` SDK** for billing.
- **Read-only-rootfs friendly.** All state in postgres + redis
  + S3-compatible blob storage (for the audit-log archive +
  vault encrypted blobs). No writes to disk except `/tmp`.
  Means brnrd runs cleanly on **Fly Machines, Modal, upsun,
  Render, Railway** — anything that serves a Python ASGI
  worker behind a load balancer.
- **Single process per worker container; horizontal scale
  via container replicas, not threads.** A typical
  deployment is N replicas of the same ASGI image behind
  Fly's anycast proxy or upsun's router; each replica
  handles its share of the long-poll connections and
  webhook ingress. Postgres + redis are the shared state.

The asymmetry — brr lightweight, brnrd richer — is
deliberate. brr is software users install; brnrd is
software we operate. The two deps lists don't need to
match; they just need to talk the same HTTP protocol.

## Multi-project routing

One managed bot per platform serves all of a user's projects. The
event needs to land in the right project's inbox. Resolution is
per-source:

| Source | Resolution |
|--------|-----------|
| GitHub App webhook | `(installation_id, repo_full_name) → project_id` via `project_bindings` table. Naturally per-repo; no UX needed. |
| Telegram message | `(account_id, chat_id) → sticky_project_id` from `chat_project_bindings`, with `/project <name>` sticky selection and per-message `@project ...` or `/project <name> <task>` prefix override. |
| Slack message | Same shape as Telegram (`(account_id, channel_id) → sticky_project_id` + prefix override). |
| Discord message | Same shape as Telegram. |
| GitLab MR comment (future) | Same shape as GH (`(installation_id, project_path) → project_id`). |

TG / Slack / Discord command surface for managing bindings:

| Command | Behaviour |
|---------|-----------|
| `/connect <project-name>` | Bind current chat to project. Replaces any previous binding for that chat. |
| `/project <name>` | Select the sticky project for subsequent messages in this chat. |
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
brnrd_url = https://api.brnrd            ; default; override for self-hosted brnrd
api_key_env = BRR_RUN_API_KEY                 ; env var name to read the token from
daemon_name = my-laptop                       ; human-readable, free-form
project_id  = prj_01HX...                     ; this daemon belongs to which project
long_poll_seconds = 25                        ; how long each poll waits before returning empty
```

One daemon = one project. Users with multiple projects run one
daemon per project (on the same host or different hosts). The
`project_id` is the daemon's anchor — brnrd uses it for routing.
For users running their daemon on a small box with N projects in
parallel, N daemons is fine (they share the host but stay
independently configured).

The daemon token is minted by brnrd after the GitHub-backed browser
approval flow; the daemon never generates one. `daemon_name` lets a
user run multiple daemons under one account (laptop, home server)
and have brnrd route events to the right one (see "Multi-daemon
routing" below).

### Lifecycle

The cloud-gate is a long-running gate thread, peer to the existing
`telegram` / `slack` / `github` gates:

| Phase | What the adapter does |
|-------|----------------------|
| **start** | Authenticates to brnrd with the API key. Registers itself with `POST /v1/daemons/register` (declares `daemon_name`, `project_id`, capabilities). Begins long-poll loop. |
| **drain (per poll)** | `GET /v1/daemons/inbox?since=<cursor>`. Returns 0+ events scoped to this daemon's project. For each, writes `.brr/inbox/<event-id>.json` and advances the cursor. |
| **respond** | Watches `.brr/responses/` for new files. For each response paired to a cloud-originated event, POSTs to `/v1/daemons/responses`. |
| **shutdown** | Cancels in-flight long-poll. `POST /v1/daemons/deregister` so brnrd marks this daemon offline; queued events stay queued; failover may kick in for future events depending on policy. |

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

brnrd translates `body_markdown` to platform-native formatting
(Markdown V2 for Telegram, GitHub-flavoured Markdown for GH)
before posting, then drops the body. `status` drives whether the
platform message gets a check / cross / warning glyph for
at-a-glance triage.

**Overflow is handled daemon-side, before the POST** (delivery
shape H; see
[`design-managed-delivery.md`](design-managed-delivery.md)). The
daemon runs the shared delivery driver's `overflow()` step — gist
via the user's own `gh`, else truncate — so `body_markdown` always
fits the origin platform's single-message limit. brnrd therefore
never chunks and never needs gist credentials; large content stays
on the user's own GitHub as a gist link, never on brnrd. (Earlier
the brnrd forwarder chunked over-long bodies itself — a stopgap
for Telegram's 4096-char limit added 2026-06-01; the driver makes
that a removable safety net, since the daemon is the side that has
`gh`.)

## brnrd side — REST API surface

### Account / pairing / project endpoints

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `GET` | `/auth/github/start` | Begin "Sign in with GitHub"; redirects to GitHub with state + PKCE challenge. | OAuth state / PKCE cookies (TTL) |
| `GET` | `/auth/github/callback` | Resolve GitHub identity, create/update the brnrd account, seed the default project on first login, issue the brnrd session cookie. | Account row keyed by `github_id`; session-token hash (TTL) |
| `POST` | `/v1/accounts/api-keys` | Issue an additional API key. | API-key hash + metadata |
| `DELETE` | `/v1/accounts/api-keys/{key_id}` | Revoke. | Mark revoked |
| `POST` | `/v1/accounts/projects` | Create a project. Body: `{name}`. Returns `project_id`. **Enforced against the account's effective project cap** (3 on Free; 25 on Subscribed without unlock; unlimited on Subscribed with unlock per `cumulative_purchased_usd_lifetime >= 10`, per [`design-billing.md`](design-billing.md) § "Cumulative purchase tracking and the subscriber project cap unlock"); 409 with `subscription_hint` body when at cap, populated by tier: Free → "subscribe for 25"; Subscribed-not-unlocked → "top up $X.XX more to unlock unlimited". | Project row (name, account_id) |
| `GET` | `/v1/accounts/projects` | List the account's projects (id, name, daemon count, last activity). | Read-only |
| `DELETE` | `/v1/accounts/projects/{project_id}` | Delete project. Cascades to bindings; in-flight events drain. | Hard delete |
| `POST` | `/v1/accounts/pair/telegram` | Initiate a Telegram pairing — returns a one-time pairing code valid for 10 min. | Pairing row (TTL) |
| `POST` | `/v1/accounts/pair/github` | Initiate a GitHub App install flow — returns the install URL with `state=` encoding the account. | Install-intent row (TTL) |

### Subscription endpoints

The platform-subscription leg of the billing model. Full
mechanics live in [`design-billing.md`](design-billing.md); the
endpoint shape is summarised here because it's part of the
brnrd protocol surface that the CLI consumes (`brr brnrd
subscription [status | start | cancel | resume | portal]`, with
`brr brnrd subscribe` as a shortcut for `subscription start`).

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `GET` | `/v1/accounts/subscription` | Current state: `tier` (`free` / `subscribed` / `subscribed_past_due`), `plan` (`monthly` / `annual`), `period_end`, `cancel_at_period_end`, last 6 invoices summary. | Read-only |
| `POST` | `/v1/accounts/subscription/checkout` | Create Stripe Checkout session for the subscription product. Body: `{plan: "monthly" | "annual"}`. Returns `checkout_url`. | Stripe customer + checkout session |
| `POST` | `/v1/accounts/subscription/cancel` | Mark `cancel_at_period_end=true` on the Stripe subscription. | Stripe subscription update |
| `POST` | `/v1/accounts/subscription/resume` | Clear `cancel_at_period_end` (re-activate a subscription marked for cancellation that hasn't expired yet). | Stripe subscription update |
| `POST` | `/v1/accounts/subscription/portal` | Create Stripe Customer Portal session for card-update / invoice-download / plan-switch. Returns `portal_url`. | Stripe portal session |

Subscription state is **also** mirrored to the account-scope
settings store as `subscription.tier` (read-only from clients;
written by brnrd on Stripe webhook events). This is what the
daemon + brnrd-side dispatcher consult to know which tier caps
apply, without having to call the dedicated subscription
endpoint on every dispatch decision.

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

### Live progress card relay (daemon-facing)

Additive to the response shape above (added 2026-06-01, delivery
shape **H** — see
[`design-managed-delivery.md`](design-managed-delivery.md)). The
OSS gates render a live progress card from local `run_progress`
and edit it in place as a task moves (`task_created → running →
finalizing → done`). That view is **daemon-local** — `run_progress`
reads `.brr/tasks/` — so brnrd cannot render it; the daemon must.
In managed mode the cloud gate renders the card text (via the
shared delivery driver, styled per the event's origin platform)
and relays it for the managed bot to post / edit in place:

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/daemons/card` | Upsert the live progress card for an in-flight event. Body: `{event_id, reply_to, text}` (`reply_to` echoed from the event as on the response POST; `text` already platform-formatted by the daemon). brnrd verifies `reply_to` against the event's chat/repo binding, sends the card on the first call (storing the returned platform `message_id` keyed by `event_id`) and edits it in place thereafter. | Card `message_id` per `event_id` (routing metadata, dropped when the event is responded / TTL'd); **never the text** |

Same auth as the inbox endpoints. The card text passes through
like a response body — relayed to the platform, never stored;
brnrd holds only the `message_id` it needs to edit in place
(routing metadata, mirroring how it retains the echoed `reply_to`
to forward the final response). The binding check on `reply_to` is
the clamp that stops the relay from becoming an open send-proxy.
The skip-if-unchanged optimisation lives in the driver daemon-side,
so an unchanged card is never re-POSTed.

### Transient review-pack relay

The resident publishes its PR body from the diffense pack projection
(see [`design-diffense.md`](design-diffense.md)). This pair of endpoints
backs the **rich** rendered view linked from that body, for oversized
packs or remote reviewers who want the zoomable surface:

| Method | Path | Auth | Description | Persists |
|--------|------|------|-------------|----------|
| `POST` | `/v1/daemons/pack` | daemon bearer | Relay a review pack. Body: `{pack, ttl_s?}`. brnrd stashes it in a RAM-only TTL store behind an unguessable token and returns `{token, render_url, expires_at}`. Size-capped (413 over the cap). | **Nothing** — RAM only, dropped on TTL/restart |
| `GET` | `/r/{token}` | **none** (capability URL) | Render the pack as the self-contained diffense HTML (reuses `brr.diffense.render`). 404 once expired. | — |

This is the pack's "transient relay, never a store" stance made
concrete (the data-ownership line that also governs event/response
bodies): a pack is derived from the user's diff + conversation, so
brnrd renders it but never writes it to the database or disk. The
render route is unauthenticated by design — a reviewer opening the link
from a PR body isn't necessarily a brnrd user; the token is the
capability and the TTL bounds exposure, matching the user publishing
their own data to their own PR. A horizontally-scaled deployment would
swap the in-process store for a shared *ephemeral* one (Redis-with-TTL),
never a durable table. (Self-hosted mode skips this entirely — the local
`brr review` is the rich surface, and the PR body still carries the
projection + the embedded pack.)

This is the **one** place shape H extends the protocol; the
final-response path above is unchanged. Shape U (daemon renders
everything; brnrd a formatting-free send/edit relay for the
response too) was weighed and deferred — it reshapes the accepted
response shape for a mostly-philosophical data-min gain where H is
purely additive. See
[`design-managed-delivery.md`](design-managed-delivery.md) → "Why
H, and what U would change".

### Webhook endpoints (platform-facing)

| Method | Path | Source | Persists |
|--------|------|--------|----------|
| `POST` | `/v1/webhooks/telegram` | Telegram Bot API update — single bot, multiplexed by chat_id | Event metadata only after dispatch |
| `POST` | `/v1/webhooks/github` | GitHub App webhook — multiplexed by `installation.id`; signature verified per request | Event metadata only after dispatch |

Both are authenticated by the platform's own signing mechanism
(Telegram bot token secret in URL, GitHub `X-Hub-Signature-256`).
Event body dropped from brnrd after dispatch.

### Project-binding endpoints

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/accounts/bindings/chat` | Bind a TG/Slack/Discord chat to a project. Body: `{platform, chat_id, project_id}`. **Unique per `(platform, chat_id)` across ALL accounts**: if the chat is already bound to a different account, returns 409 with `bound_to_account: <obfuscated_id>` (no PII leaked) and a message telling the user to have the existing owner unbind first. Within the same account, replaces any previous binding for that chat. | chat_project_bindings row |
| `GET` | `/v1/accounts/bindings/chat` | List the account's chat bindings. | Read-only |
| `DELETE` | `/v1/accounts/bindings/chat/{binding_id}` | Remove. | Hard delete |
| `POST` | `/v1/accounts/bindings/repo` | Bind a GH installation+repo to a project. Body: `{installation_id, repo_full_name, project_id}`. **Unique per `repo_full_name` across ALL accounts**: if the repo is already bound to a different account, returns 409 with `bound_to_account: <obfuscated_id>`. Within the same account, auto-created on GH App install per default policy; this endpoint exists for re-binding / re-routing. | repo_project_bindings row |
| `GET` | `/v1/accounts/bindings/repo` | List the account's repo bindings. | Read-only |
| `DELETE` | `/v1/accounts/bindings/repo/{binding_id}` | Remove. | Hard delete |

### Binding uniqueness — correctness + abuse-mitigation

Both binding endpoint families enforce **global uniqueness on
the resource side** (the chat / repo identity), not just per-
account. This is enforced at the database layer with a UNIQUE
constraint on `(platform, chat_id)` for chat bindings and on
`repo_full_name` for repo bindings (or `(installation_id,
repo_full_name)` if we keep installation as a routing key —
either way the resource identity must be unique).

Two reasons it's the right shape:

1. **Routing correctness.** A single chat can't dispatch to
   two different (account, project) pairs without colliding
   responses. A single GH repo can't have two different
   accounts receiving its events. The uniqueness constraint
   is needed for the dispatcher to work correctly.
2. **Multi-account abuse mitigation.** Without binding
   uniqueness, a user could create N Free accounts, each
   binding the same repo / chat, and effectively get N × the
   Free tier's caps (events, signup bonuses, project slots).
   Uniqueness reduces this to "you can have N accounts but only
   ONE of them at a time receives events from your
   repo/chat." The marginal abuse value of additional accounts
   drops to near-zero — the extra accounts can only create
   "projects" with no incoming gate routing, which has
   approximately no value.

Conflict response shape (409):

```json
{
  "error": "binding_conflict",
  "message": "this <chat | repo> is already bound to another account.
              have the existing owner unbind first, OR contact support
              if you believe this is incorrect.",
  "bound_to_account": "acc_obfuscated_xyz",
  "bound_at": "2026-03-12T..."
}
```

The `bound_to_account` is an obfuscated ID, not a real
account_id (no email / no name / no PII leak). Support can
match it on the backend.

What we **don't** do at launch:

- No fingerprinting (browser / device).
- No IP-based velocity limits beyond standard DDoS protection.
- No email-domain blacklist / "suspicious signup" flagging.
- No ML anti-abuse.

All overengineering at our scale. The leverage from a
duplicate Free account is ≤ $0.10 of compute (the signup
bonus) + zero managed-gate routing (uniqueness blocks it).
Revisit only if real abuse signal appears in production data.

### Credential vault endpoints

Generalised credential vault. **Three domains** share the same
encryption / audit / revoke infrastructure:

1. **AI-runner credentials** (Anthropic / OpenAI / Google /
   GitHub) — needed to run Claude / Codex / Gemini in the
   spawn sandbox. Two payload shapes: API key or credential
   directory tarball (preserves Claude Pro / Codex Plus /
   Gemini OAuth subscription-auth for users who'd rather not
   provision API keys). Available on all tiers (Free /
   Subscribed).
2. **Docker-registry credentials** (ghcr.io / docker.io /
   quay.io / private registries) — needed when the project's
   `brr.toml` declares a private image. Single payload shape:
   username + password/token. Used by the spawn bootstrap to
   `docker login <host>` before `docker pull`. Available on
   all tiers (Free / Subscribed).
3. **Cloud-platform credentials** (Fly Machines at launch;
   Modal / Daytona / etc. as managed support ships) — needed
   for **BYO compute**: subscribers' spawns are dispatched to
   their own cloud account using these credentials. Single
   payload shape: an API token scoped to the platform's
   spawn-relevant operations (Fly: org/app create + machine
   start/stop). **Subscriber-only** — vault writes for this
   kind require `subscription.tier == "subscribed"`, vault
   reads happen only on dispatch and check the same gate.

All three live in the same `credentials` table with a `kind`
discriminator; identical encryption-at-rest (per-account
envelope keys), identical audit-log shape, identical revoke
flow. The subscriber gate is a single conditional on the
write + read paths, not a separate schema.

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/accounts/credentials` | Store an encrypted credential. Body: `{kind: "ai-anthropic" | "ai-openai" | "ai-google" | "ai-github" | "docker-registry" | "cloud-platform", shape: "api-key" | "dir-tarball" | "registry-userpass" | "cloud-token", provider?: "fly" | "modal" | ..., payload: "...", host?: "ghcr.io"}`. `host` required for `docker-registry`; `provider` required for `cloud-platform`. **`kind=cloud-platform` rejects with 403 if subscription.tier != "subscribed".** | Encrypted blob, metadata (kind, shape, host, provider, created_at) |
| `GET` | `/v1/accounts/credentials` | List stored credentials (id, kind, shape, host, provider, created_at, last_used_at). Never returns secret material. Filterable: `?kind=ai-*` or `?kind=docker-registry` or `?kind=cloud-platform`. | Read-only |
| `DELETE` | `/v1/accounts/credentials/{credential_id}` | Revoke. In-flight spawns complete; new spawns refuse if they would have used this credential. For cloud-platform creds: subsequent BYO spawns fall back to managed (if subscriber) or fail with "missing cloud credential, top up the wallet or restore the credential" (if managed compute is also unavailable). | Hard delete |

CLI surface:

```
# AI credentials (all tiers)
brr brnrd creds add anthropic --key sk-ant-...
brr brnrd creds add anthropic --dir ~/.claude        # preserves subscription auth
brr brnrd creds add openai    --key sk-...
brr brnrd creds add google    --key AIza-...
brr brnrd creds add github    --key ghp_...

# Docker-registry credentials (all tiers)
brr brnrd creds add docker-registry --registry ghcr.io \
  --username myorg --token <ghcr-pat>
brr brnrd creds add docker-registry --registry docker.io \
  --username myuser --password <pat>

# Cloud-platform credentials (subscriber-only; BYO compute)
brr brnrd creds add cloud-platform --provider fly --token <fly-pat>
# Future, when managed support ships:
brr brnrd creds add cloud-platform --provider modal --token <modal-pat>
brr brnrd creds add cloud-platform --provider daytona --token <daytona-pat>

# Common
brr brnrd creds list                              # all kinds
brr brnrd creds list --kind docker-registry
brr brnrd creds list --kind cloud-platform        # 403 if not subscribed
brr brnrd creds remove <id>
```

Storage detail (informational, not normative):

- `credentials` table columns: `id`, `account_id`, `kind`,
  `shape`, `host` (nullable, for `docker-registry`), `provider`
  (nullable, for `cloud-platform`), `encrypted_payload`,
  `envelope_key_id`, `created_at`, `last_used_at`,
  `revoked_at` (nullable).
- Encryption: per-account envelope key wraps a per-credential
  data key; payloads encrypted with AES-256-GCM. Envelope keys
  rotated via a v-next admin tool (out of scope for launch).
- `last_used_at` updated at credential read (spawn-bootstrap
  time); used for the dashboard to show "this cred hasn't been
  used in 90 days, consider revoking."

The `--dir` path tars the directory, base64-encodes, uploads
under shape `dir-tarball`. At spawn time the tarball is decoded
into the sandbox's `$HOME/.claude/` (or wherever the provider
expects). Subscription-auth flows for Claude Pro, Codex Plus,
Gemini OAuth preserved exactly as before — the generalisation
doesn't touch this shape.

Docker-registry credentials match by `host` at spawn time:
when the project's `brr.toml` declares `docker.image =
"ghcr.io/myorg/foo"`, brnrd extracts the registry host
(`ghcr.io`), looks up a `docker-registry` cred for this account
matching that host, decrypts it, runs `docker login ghcr.io -u
<user> --password-stdin <token>`, then `docker pull
ghcr.io/myorg/foo`. Cred material is cleared from sandbox env
after `docker login` (the credential lives only in the
Docker daemon's auth.json for the duration of the spawn).

Public images bypass the lookup (no `docker login` step).
Private images with no matching cred fail with a clear gate-side
message: "private image `<host>/...`; add registry creds with
`brr brnrd creds add docker-registry --registry <host> ...`".

Cloud-platform credentials match by `provider` at dispatch
time: when a spawn is about to start AND the account is
`subscribed` AND a `cloud-platform` credential exists for the
target env's provider, the dispatcher invokes the env class
with the user's token instead of the brnrd-side managed token.
Same `EnvBackend` env class, two callers — exactly the "Caller
axis" pattern documented in
[`research-cloud-envs.md`](research-cloud-envs.md). The
credential is decrypted into the dispatcher's process memory at
dispatch time and zeroed immediately after the env class
returns the running machine handle.

BYO-compute spawn path (subscriber, has `cloud-platform` cred):

```
Dispatcher receives a permission-resolved task to spawn:
  if account.tier == "subscribed":
    cred = credentials.find(kind=cloud_platform, provider=target_env.provider)
    if cred:
      # BYO path
      decrypted = vault.decrypt(cred)
      machine = target_env_class.start(token=decrypted, ...)
      audit_log.append("spawn_byo", account_id, spawn_id, provider, machine_id)
      # No wallet debit; the user pays the cloud provider directly
    else:
      # Managed path
      machine = target_env_class.start(token=brnrd_managed_token, ...)
      wallet.debit(at_finalize=cost_credits(machine))
      audit_log.append("debit_spawn", account_id, spawn_id, ..., sub_bucket)
  else:
    # Free: managed-only by policy
    machine = target_env_class.start(token=brnrd_managed_token, ...)
    wallet.debit(at_finalize=cost_credits(machine))
```

The `audit_log.append("spawn_byo", ...)` operation is documented
in [`design-billing.md`](design-billing.md) →
"BYO-compute spawns — wallet bypass" alongside the wallet-side
implication (no debit; estimated cost surfaced on the dashboard
for the user's own visibility).

### Failover-policy endpoints

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/accounts/failover-policy` | Set policy: `{enabled: bool, mode: "ask" | "auto-approve-always" | "auto-approve-under-usd" | "auto-approve-under-per-day" | "never", auto_approve_threshold_usd, auto_approve_threshold_per_day, monthly_spawn_cap, monthly_cost_cap_usd}`. | failover_policy row |
| `GET` | `/v1/accounts/failover-policy` | Read current policy + usage counters (spawns-this-month, cost-this-month). | Read-only |

### Account-scope settings endpoints

Per [`design-config-layout.md`](design-config-layout.md), the
config model has three scopes (project / local / account). The
**account** scope lives here on brnrd — settings that apply
across all the user's daemons and spawns (e.g. user-wide runner
preference, default failover thresholds, account-wide kb
preferences). Distinct from `failover-policy` above because that
endpoint groups the failover-specific knobs into one PUT for
atomicity; this endpoint family is for individual key/value
account-scope settings the schema declares.

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `GET` | `/v1/accounts/settings` | Read all account-scope settings for this account. Returns `{key: value}` map. | Read-only |
| `GET` | `/v1/accounts/settings/{key}` | Read one setting. 404 if not set (falls back to schema default on the client side). | Read-only |
| `PUT` | `/v1/accounts/settings/{key}` | Write one setting. Body: `{value: ...}`. Schema-validated on the brnrd side. | settings row |
| `DELETE` | `/v1/accounts/settings/{key}` | Reset to default (deletes the row; subsequent reads return schema default). | settings row removed |

Daemons fetch `/v1/accounts/settings` at startup and refresh
every 5 minutes while connected. Brnrd-side spawns fetch at
bootstrap as part of the daemon-equivalent bootstrap (see
"Failover dispatch" step 6 below). Push-style invalidation
(brnrd notifies daemons of changes via the inbox long-poll) is
a v-next refinement; polling is fine at launch volumes.

Account-scope keys are schema-declared on the client side; brnrd
stores them as opaque blobs (TOML-serialised) per `key`. Schema
versioning is the client's responsibility — the brnrd side
treats the store as a key-value bag scoped to `account_id`.

### Permission-prompt endpoints

When `mode = "ask"` and a spawn is pending, brnrd posts a
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

These are called by brnrd's dispatcher, not directly by clients;
documented here because they're part of the protocol surface that
the server-side env-invocation path consumes.

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/internal/dispatch/{event_id}` | Internal dispatcher entry point. Decides online → enqueue OR offline → policy → prompt-or-spawn. | Dispatch log row |
| `POST` | `/v1/internal/spawns` | Record a spawn attempt (account_id, project_id, event_id, started_at, est_cost_usd). | spawn row |
| `PATCH` | `/v1/internal/spawns/{spawn_id}` | Update spawn outcome (finished_at, exit_code, actual_cost_usd). Triggers the wallet debit per [`design-billing.md`](design-billing.md) ("Debit mechanics") — USD converted to credits in the billing layer. | spawn row update; wallet ledger row |

## Pairing flow

### `brr brnrd connect` — three-layer smart bootstrap

The user-facing entry point for managed-mode setup. Walks
**three layers** (account-pair → project-create → gate-pair),
each skippable if already done, each prompting before acting.
Each layer is also a separately-callable verb (`brr brnrd
pair <gate>`, `brr brnrd projects bind`, etc.) — the bootstrap
just sequences the same code paths behind one entry point. CLI
shape lives at
[`decision-cli-shape.md`](decision-cli-shape.md) → "three-layer
smart bootstrap"; this section describes the protocol-side
endpoints each layer hits.

Layer-by-layer:

```
Layer 1 — account pair (one-time per machine)
  → POST /v1/accounts/pair             { machine_hostname }
    ← { pair_code, pair_url, account_id_when_done }
  → CLI prints `pair_url`; user opens it in the browser, signs in
    with GitHub, and approves the pairing
  → CLI long-polls GET /v1/accounts/pair/{pair_code} until
    status: paired
  → CLI stores the account-scoped daemon token in
    `~/.config/brr/brnrd.token`

Layer 2 — project create (per repo)
  → CLI inspects the cwd: reads `.git/config` for origin URL,
    derives a default project name from the repo basename
  → CLI prompts "Create project <name>? [Y/n]"
  → POST /v1/accounts/projects         { name, git_remote? }
    ← { project_id }
  → CLI stores `project_id` in `.brr/config` so subsequent
    commands resolve to the right project without asking

Layer 3 — gate pair (per project per gate; runs only if not
already wired)
  → CLI walks each managed-gate "detector":
      • GitHub detector — fires when `git remote get-url origin`
        matches a GH URL. Hits POST /v1/accounts/pair/github
        with { project_id, repo_full_name }. If a GH App
        installation already exists for the org, the response
        contains `install_already_present: true` and the CLI
        offers auto-bind (POST /v1/accounts/projects/{project_id}/
        gates/github { installation_id, repo_full_name }).
        Otherwise, the CLI opens the install URL and polls for
        the installation webhook.
      • Telegram detector — fires if `.brr/config` had a
        legacy TG token (migration path) OR if the user
        explicitly opted in. Hits POST /v1/accounts/pair/
        telegram { project_id }, gets a pairing code, prints
        the `/start <code>` instruction.
      • GitLab / Slack / Discord — same shape, added when each
        gate ships.
  → Each detector is independently skippable via [y/N] /
    [Y/n] prompts; bare `brr brnrd connect` defaults the
    detected-and-likely ones to Y and the others to N.

Idempotency:
  • Layer 1 skipped if `~/.config/brr/brnrd.token` is valid.
  • Layer 2 skipped if `.brr/config` already has a
    `brnrd.project_id` resolving on the brnrd side.
  • Layer 3 detector entries skipped if the
    (project_id, gate_kind, repo_or_chat) binding already
    exists.
```

Endpoints introduced or extended by this flow:

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `POST` | `/v1/accounts/pair` | Layer 1: start account-pair; returns `pair_code`, `pair_url`, polling key. | pair_request row, TTL 10 min |
| `GET` | `/v1/accounts/pair/{pair_code}` | Layer 1: poll for pair status; on `paired`, returns daemon token. | Read-only on pair_request |
| `POST` | `/v1/accounts/projects` | Layer 2: create a project for this account. Idempotent on `(account_id, name)`. | project row |
| `POST` | `/v1/accounts/projects/{project_id}/gates/{kind}` | Layer 3: bind an existing gate (GH installation, TG chat, ...) to a project without going through the pair-code dance. Used when the detector finds an already-installed App. | project_gate_binding row |

### Telegram (Layer 3 detector — explicit pair)

```
1. CLI (via the Layer-3 detector OR explicit `brr brnrd pair
   telegram --project <project_id>`):
   → POST /v1/accounts/pair/telegram with project_id, gets `pairing_code = "BR1234"`
   → CLI prints: "Send `/start BR1234` to @brr_bot"

2. User: messages @brr_bot with `/start BR1234`
   → Telegram delivers update to brnrd via webhook
   → brnrd matches BR1234 to the pending pair request, binds
     (account_id, chat_id, project_id) into chat_project_bindings
   → @brr_bot replies "Paired with <project_name>. Send me tasks anytime.
                       Switch projects with /project <name> or @<name>."

3. User: sends a real task to @brr_bot
   → brnrd looks up chat_id → (account_id, project_id) → list of online daemons
   → if any daemon is online for that project: enqueues event per the routing policy
   → if none online AND failover enabled: walks the policy decision tree
   → if none online AND failover disabled: queues until a daemon returns
```

### GitHub (Layer 3 detector — install + auto-bind, or explicit pair)

```
1. CLI (via the Layer-3 detector OR explicit `brr brnrd pair
   github`):
   → POST /v1/accounts/pair/github, gets one of:
       a) `install_already_present: true` + installation_id for
          the org matching the detected remote → CLI prompts
          "Auto-bind this repo to project <name>? [Y/n]" → POST
          /v1/accounts/projects/{project_id}/gates/github
          { installation_id, repo_full_name }. Done.
       b) GitHub App install URL with `state=` encoding
          (account_id, project_id) → CLI opens in browser; user
          installs on selected repos.

2. (Path b only) GitHub: POSTs installation webhook to brnrd
   → brnrd reads `state` from the install event payload, binds
     (account_id, installation_id, project_id) for the matched
     repo (and auto-creates one project per other repo in the
     install if the user picked multiple — surfaced for review
     in the dashboard)
   → CLI's poll on GET /v1/accounts/pair/github/{state} returns
     paired; CLI prints "✓ Installed on <repo>; bound to
     project <name>."

3. User: opens a PR / issue, comments `@brr <task>`
   → GitHub delivers issue_comment webhook to brnrd
   → brnrd validates @brr mention, looks up
     (installation_id, repo_full_name) → (account_id, project_id) → dispatch
```

### Credential setup

```
1. User: `brr brnrd creds add anthropic --key sk-ant-...`
   OR     `brr brnrd creds add anthropic --dir ~/.claude`
   → CLI POSTs to /v1/accounts/credentials with the chosen shape
   → brnrd encrypts and stores; returns credential_id

2. User: repeats for openai / google / github as needed

3. (Optional) User declares a private Docker image in `brr.toml`:
   → `brr brnrd creds add docker-registry --registry ghcr.io \
        --username myorg --token <ghcr-pat>`
   → CLI POSTs to /v1/accounts/credentials with kind=docker-registry,
     host=ghcr.io, shape=registry-userpass
   → brnrd encrypts and stores

4. User: `brr brnrd policy set --enable --mode ask --monthly-cap 100`
   → CLI POSTs to /v1/accounts/failover-policy
   → brnrd flips failover_enabled = true for the account, sets caps
   → Now: any event arriving while no daemon is online and the
     spawn-count is under cap triggers either auto-spawn or a
     permission prompt, per policy.
```

## Failover dispatch

When an event arrives at brnrd, the dispatcher walks this
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

6. (Spawn path) Issue a one-shot task-key, decrypt credentials
   into process memory, run the **daemon-equivalent bootstrap**:
     - clone repo with the per-spawn GH App token
     - **read `brr.toml` from the cloned repo** for project-scope
       config (Docker image, runner choice, env preference, etc.
       — per
       [`design-config-layout.md`](design-config-layout.md))
     - fetch account-scope settings from
       `GET /v1/accounts/settings` (user-wide runner preference,
       failover thresholds)
     - build the effective config: account < project (local
       scope is intentionally ignored — it's not in the repo,
       by design)
     - **if `docker.image` references a private registry**:
       extract host → look up `docker-registry` credential for
       this `(account_id, host)` → if present, decrypt and
       `docker login <host> -u <user> --password-stdin <token>`
       on the build/host worker; if absent, fail the spawn with
       a clear gate-side message ("private image
       `<host>/<repo>`; add registry creds with `brr brnrd
       creds add docker-registry --registry <host> ...`").
       Public images skip this step.
     - `docker pull <image>` (now succeeds for private images
       with valid creds)
     - materialise AI creds into the sandbox layout the runner
       expects (env vars or `$HOME/.claude/` dir-tarball
       expansion)
     - construct a `RunContext` using the effective config
   Then invoke the `fly_machines` env class (same class the
   daemon would use) against brnrd's Fly Machines pool with:
     - the `RunContext` (carries Docker image, runner choice,
       env config, AI creds)
     - a per-spawn GH App installation token (push permission)
     - the event payload + project_id
     - the task-key (Bearer scoped to this event_id, 1h TTL,
       single use for POST /v1/daemons/responses)
   Clear all credential material (AI + registry) from memory
   after hand-off. Docker registry credentials live only in the
   build/host worker's `~/.docker/config.json` for the duration
   of the spawn; the sandbox itself doesn't receive them.

7. (Sandbox runs) Sandbox:
     - clones the repo via the GH token
     - runs the runner CLI on the task body
     - pushes the resulting branch back via the GH token
     - POSTs the response with the task-key
     - tears itself down on clean exit

8. brnrd records the spawn outcome (cost USD, duration, exit
   code, project_id), the billing layer converts to credits and
   debits the wallet per
   [`design-billing.md`](design-billing.md) → "Debit mechanics"
   (free credits drawn first; paid drawn only after; spawn
   completes even if it overshoots, but next spawn blocks
   pending top-up), and the audit log appends a metadata row.
```

The decision is per-event, not per-account-session — the user can
have failover enabled and still have their daemon take the next
event after this one if they come back online mid-decision.

## Conversation context for failover and dashboard

The daemon today keeps per-conversation history locally (in
`.brr/conversations/`) and uses it to give the runner continuity
between related tasks. When the daemon is offline and a failover
spawn fires — or when the dashboard wants to render the
conversation view without a live daemon — brnrd needs to
assemble enough context for the runner / dashboard to be useful,
**without persisting conversation contents on brnrd** beyond
what the data-minimization principle allows.

The shape: **brnrd holds a metadata-only conversation graph;
content lives on the platforms and in git remotes and is fetched
on demand at spawn / render time.**

### What brnrd holds (metadata only)

```
event_metadata(
  event_id,            ulid
  gate,                "github" | "telegram" | "slack" | ...
  source_channel,      opaque platform id (chat_id, repo+issue_number)
  project_id,          ulid
  conversation_id,     ulid; from daemon or inferred (see below)
  branch_name,         "brr/ev_01HX…" once a daemon posts a response
  received_at,         timestamp
  -- no body, no preview, no participant names
)
```

~200 bytes per event row. At the free-tier cap of 1000 events /
month per account, ~200 KB / user / month of metadata. **TTL: 30
days** on the live graph; aggregated past that into monthly
count-only summaries for the audit log.

This is the cross-gate "table of contents" — which events belong
to which conversation, which branches they landed on. It is the
load-bearing piece that lets failover spawns reconstruct
cross-gate continuity without brnrd holding any conversation
text.

### Conversation_id sources (two, ordered)

1. **Daemon writes a `Brnrd-Conversation-Id` git trailer** on
   every commit it makes during a task. Brr.run can re-derive the
   linkage by walking `git log --format='%(trailers)' <branch>`
   on a fetched branch — git is the source of truth; brnrd's
   metadata index is a cache.
2. **Daemon POSTs the `conversation_id` field** alongside the
   response on `/v1/daemons/responses`, so the metadata index
   stays current without needing to re-walk git on every event.

(Daemon-side propagation work is a small slice; see
[`plan-conversation-id-propagation.md`](plan-conversation-id-propagation.md).)

### Conversation_id inference rules (when neither source is available)

In priority order, brnrd assigns a conversation_id to a new
incoming event:

1. *Event is on a known branch* (GH PR comment, GH issue with a
   brr-created branch linked): fetch `git log` trailer →
   conversation_id.
2. *Event is a platform reply-to of a prior event* whose
   metadata is in the index: inherit that event's
   conversation_id.
3. *Event is in a chat with an active conversation in the last
   30 minutes* (sticky per chat_id with a topical-similarity
   gate): inherit; otherwise start new.
4. *Otherwise*: new conversation_id, fresh thread.

### Context sources fetched on demand

Three sources, called by the failover spawn invocation (and by
the dashboard's per-event view when the daemon is offline):

| # | Source | What it returns | brnrd-held? |
|---|--------|-----------------|---------------|
| 1 | **Originating event payload** | Body + inline parent context already on the webhook (issue body, PR title + first comment, reply-to chain) | No — held in dispatch memory only, never persisted |
| 2 | **Gate-side history fetch** | Recent messages / comments around the event from the platform itself | No — fetched on demand. **One exception**: Telegram per-chat ring buffer (see below) |
| 3 | **Git remote replay** | `git log -n 50 <branch>` + `kb/log.md` tail if present + branch's most recent commits | No — fetched on demand using the per-spawn GH App installation token |

Per-gate history-fetch mechanics:

- **GitHub** — `GET /repos/{owner}/{repo}/issues/{issue_number}/comments`
  (and `/pulls/{pull_number}/comments` for PR review threads).
  The GH App token already has scope to read these. No new
  permissions, no new storage.
- **Telegram** — Telegram's bot API does NOT expose
  retroactive `getChatHistory` for arbitrary messages; it only
  exposes the live `getUpdates` stream. To cover this gap,
  brnrd rolls a **per-chat ring buffer** of recent message
  metadata as updates arrive. See "Telegram ring buffer" below.
- **Slack** (when it ships) — `conversations.history` with
  channel scope. No retention by brnrd.
- **Discord** (when it ships) — channel message history API.
  No retention by brnrd.

### Cross-gate continuity (the metadata graph in action)

When a failover spawn fires for event `ev_NEW` with resolved
`conversation_id = cv_X`:

```
1. Originating event payload (already in dispatch memory)
2. Gate-side history fetch for ev_NEW's source (per-gate as above)
3. Git context for the resolved branch (log + recent commits)
4. CROSS-GATE: query event_metadata WHERE conversation_id = cv_X
   AND received_at > now() - 30d ORDER BY received_at DESC LIMIT 20
   For each linked event:
     - if gate has a history API → fetch recent messages around
       that event's timestamp from the platform
     - if not (TG) → read from brnrd's per-chat ring buffer if
       still in TTL
   Annotate each entry with its gate + timestamp so the runner
   sees "you were also asked X on TG 2 days ago" with provenance
```

The runner sees a context prologue assembled from platform-side
fetches + git, *not* from a brnrd-held conversation store.
Cross-gate continuity is preserved; brnrd's data ownership
stays at the metadata-graph level.

### Telegram ring buffer (the one exception)

Telegram's Bot API doesn't expose `getChatHistory` for bot-side
retroactive reads. To preserve "you said X earlier in this chat"
context for failover spawns and dashboard rendering, brnrd
holds a small per-chat ring buffer:

| Constraint | Value |
|-----------|-------|
| Scope | Per-chat (per `(account_id, chat_id)`) |
| Size cap | 50 messages |
| TTL | 72 hours |
| Content | `{sender, ts, text}` — encrypted at rest with the per-account envelope key |
| Drop triggers | `/disconnect` (chat unbinding), account deletion, TTL expiry |
| Read accounting | Every read by a failover spawn appears in `account_audit` |
| Listed in trust signals | Yes — explicitly called out in the pricing page's "what we hold" section, not buried |

This is a real concession on the "we don't have your code"
trust line. Surfacing it explicitly (rather than hand-waving)
keeps the trust story honest: we hold the minimum viable set,
named, scoped, TTL'd, in the audit log.

The Slack / Discord adapters do **not** need an equivalent —
their APIs expose retroactive history natively. The ring buffer
exists for one platform only.

### Dashboard rendering split

The dashboard's per-conversation view follows the same shape:

- **Daemon online** → proxy live to the daemon's
  `.brr/conversations/<id>.md` (no brnrd-held copy; the
  dashboard is a passthrough).
- **Daemon offline** → render from gate-side history fetch + git
  log + ring buffer (TG only). Marked clearly in the UI as
  "live from <platform>; daemon offline" so the user knows the
  source — not a stored mirror.

### Cross-gate continuity context endpoint

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `GET` | `/v1/internal/context/{event_id}` | Returns the assembled context block (sources 1+2+3 + cross-gate fetch) for a given event. Called by the spawn invocation and by the dashboard's per-event renderer. | Read-only; no persistence; logs metadata-only access entry |

The endpoint composes the fetches; callers receive a single
context blob ready to feed the runner's prompt prologue.

### What this approach loses (and accepts)

| Concern | Status |
|---------|--------|
| Cross-gate task continuity for failover spawns | **Recovered** via the metadata graph + git trailers + on-demand fetch ✓ |
| Pre-30-day conversation context | Lost (graph TTL). Acceptable — git history shows what landed. |
| Task-internal agent reasoning trace | Lost in failover (lives only in daemon's trace dir). Acceptable — the resulting branch shows what landed; the trace is a debugging surface, not a user-facing one. |
| Aggregated per-user preferences inferred from past tasks | Lost. Was always fragile signal; not load-bearing. |

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
| Daemon offline when event arrives; failover disabled | Event queues in brnrd inbox; delivered on next poll. 30-day TTL by default; configurable per account. |
| Daemon offline; failover enabled and under caps; policy=ask | Permission prompt posted via gate; resolution drives spawn-or-queue. Prompt TTL 6h (configurable). |
| Daemon offline; failover enabled and under caps; policy=auto-approve | Per-task sandbox spawned; result returned via gate; daemon sees the branch on next pull. |
| Daemon offline; failover enabled but cap hit | Event queues; user notified via gate ("cap hit, raise cap or run daemon to resume"). |
| Daemon dies mid-task | Event remains marked "in-flight" on brnrd until response posts OR `in_flight_ttl` (default 1h) elapses, then re-queues. Daemon dedupes on `event_id` so re-delivery is safe. |
| Failover sandbox dies mid-task | Same `in_flight_ttl` behaviour; re-spawn on retry up to 2 attempts before queuing for daemon return. |
| brnrd unreachable | Daemon retries with exponential backoff; long-poll cycle gracefully degrades. The BYO gate path continues to work — managed and BYO are independent. |
| Response post fails (from daemon) | Daemon retries up to N times with backoff. If brnrd is healthy but rejects, drop the response and write a trace entry. |
| Response post fails (from failover sandbox) | Sandbox retries up to 3 times; on final failure, writes the response to the user's git remote as `.brr/failover-orphans/<event-id>.md` so it isn't lost. |
| User revokes API key mid-flight | Next long-poll returns 401; daemon logs and exits its cloud-gate thread cleanly. Other gates keep running. |
| User revokes AI credential mid-flight | In-flight spawns complete; new spawns refuse with "missing credential" notification. |
| User revokes docker-registry credential mid-flight | In-flight spawns complete (image already pulled). Next spawn needing the same private image fails with "missing registry credential" notification. |
| Permission prompt expires (TTL) | Auto-queue with a "permission timed out, event queued" notification. User can run the task later by sending it again from the daemon-side. |
| Webhook secret rotation | brnrd handles silently; the daemon side is not aware of platform secrets. |

## Operational concerns (brnrd side)

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
- **Credential encryption.** Per-account envelope keys; envelope
  keys wrapped by a brnrd-side KMS root key. Same scheme covers
  AI-runner credentials AND docker-registry credentials.
  Decrypted only in process memory at spawn time; cleared after
  spawn completes (AI creds) or after `docker login` (registry
  creds — material lives in the build worker's auth.json for
  the duration of the spawn).
- **Audit log.** Every credential write, credential read at
  spawn time, failover spawn attempt (with outcome), permission
  prompt resolution, project-binding change, policy change, and
  context-fetch (cross-gate or TG-ring-buffer read at spawn /
  dashboard time) is recorded in an append-only `account_audit`
  table queryable via account-scoped CLI / dashboard.
  Metadata-only, never task contents.

## Credential security model

Covers both AI-runner credentials and docker-registry
credentials — they share the same vault, the same encryption,
and the same trust model.

The trust model:

- **Scope minimisation in onboarding.** brnrd's onboarding
  documentation walks users through generating the
  minimum-scope token per provider:
  - Anthropic API key with usage limit; GH PAT with `repo` +
    `read:user` only; ghcr.io PAT with `read:packages` only
    (no `write:packages`); docker.io access token scoped to
    read-only pulls on the specific repo, etc.
  The provider's own scoping is the load-bearing layer; brnrd's
  encryption is defense-in-depth.
- **Encryption at rest.** Per-account envelope keys; root key in
  a KMS managed separately from the application database. Same
  scheme for AI creds and docker-registry creds.
- **Encryption in transit.** TLS only; HTTP redirects refuse.
- **No logs.** Token material never enters any log line. Spawn-
  time decryption happens in process memory; AI cleartext is
  passed to the runner CLI / API call and immediately cleared;
  registry cleartext is passed to `docker login` via
  `--password-stdin` and immediately cleared (lives only in the
  build worker's `~/.docker/config.json` for the duration of
  the spawn).
- **Easy revoke.** `brr brnrd creds remove <id>` and
  `brr brnrd policy set --disable` both work without affecting
  in-flight tasks. In-flight tasks complete; new spawns refuse.
- **Per-account audit log.** Every spawn surfaced in
  `brr brnrd audit` with timestamp, event_id, project_id,
  cost estimate, exit status, AI provider used, docker
  registry used (if applicable).
- **Blast-radius bound.** Even if brnrd's database is
  compromised, the per-provider tokens grant only what their
  scopes permit (Anthropic API usage; GH push to specific
  repos; docker pull from the specific registry+repo; etc.).
  Exposure shape ~ a leaked provider token — bad, but bounded
  and quickly revocable from the provider side.

What we do NOT do:

- Store user OAuth refresh tokens that grant broad provider
  access. Per-provider scoped tokens only.
- Hold git-write tokens beyond the duration of one spawn (the
  GH App install delegates this naturally; non-GitHub remotes
  use a per-spawn deploy key the user installs once).
- Allow credential read after write — write-only API surface
  for the secret material itself.
- Persist event/response bodies, conversation history, or repo
  contents. See "Data minimization" above.
- Pass docker-registry credentials to the spawn sandbox itself.
  The credential lives only on the build/host worker that runs
  `docker pull`; the resulting image is what the sandbox sees.

## BYO compute — subscriber feature, parallel-shipped with managed

Reframed on 2026-05-26 from the earlier "designed, deferred"
posture. The earlier draft framed BYO as a Free-tier feature
dropped from launch entirely; the current shape lands BYO at
launch as a **subscriber-only** feature that ships alongside
each cloud's managed support. Full policy rationale in
[`decision-pricing-shape.md`](decision-pricing-shape.md)
§ "Compute: managed vs BYO"; the protocol shape is in this
section.

### What the wire actually does at launch

- **One new credential `kind`**: `cloud-platform` with a
  `provider` discriminator (`fly` at launch). Token encrypted
  via the same envelope-key infrastructure as AI + docker-
  registry creds. **Vault writes require
  `subscription.tier == "subscribed"`** (403 otherwise);
  vault reads check the same gate at dispatch time.
- **Failover-policy adds an effective compute target** per
  account (not stored as a new field; derived at dispatch
  time): if the account is `subscribed` AND has a
  `cloud-platform` cred for the target env's provider, BYO is
  chosen; otherwise managed is chosen. No new policy knob —
  the credential's presence is the signal. (A user opting back
  to managed deletes the cred or adds a project-level override
  in `brr.toml`, both of which are existing surfaces.)
- **One branch in the dispatcher's spawn step**: select token
  source (brnrd-managed vs vault-decrypted user token) and
  audit-log shape (`debit_spawn` vs `spawn_byo`) based on
  whether a BYO cred is in play. The env class itself is
  unchanged — same `EnvBackend.start(token=...)` invocation,
  different `token`.

### What ships when

- **Launch**: BYO Fly Machines (subscriber-only). The
  `cloud-platform` kind + `fly` provider both ship; one
  managed cloud, one BYO option, same env class.
- **Post-launch**: each new managed cloud env (Modal /
  Daytona / Codespaces / VPS / …) ships BYO support for the
  same provider in the same release. BYO's per-cloud cost is
  small over managed (the env class is shared); we never
  bottom-up build a BYO path without a managed path.
- **Never**: BYO-only-for-clouds-we-don't-ship-managed. If we
  don't manage it, we don't BYO it. Avoids unbounded
  per-platform support tail.

### Same model applies to future agentic-secretary connectors

Per
[`decision-connectors-layering.md`](decision-connectors-layering.md),
when the agentic-secretary layer lands and brings hosted
Google / Linear / Notion / Stripe-billing-read / etc.
connectors, the same BYO-for-subscribers principle applies:
the `credentials` table grows a fourth `kind` (e.g.
`connector-oauth` with a `provider` discriminator), the
subscriber gate sits on the same write/read paths, and Free
users get managed-only connectors with brnrd-side credentials
(which is itself a reason connectors are subscriber-only in
the first place). One pattern, multiple subscriber-only
surfaces, one vault.

### Daemon-side cloud envs remain independent

Daemon-side cloud envs (a laptop daemon fans out to the user's
cloud via a first-party env extra like `brr[fly]` or a
third-party `brr-env-<name>` registered via the `brr.envs`
entry point) remain independent of managed mode entirely.
Those are part of the env work, not part of brnrd, and ship
per [`research-cloud-envs.md`](research-cloud-envs.md) on
their own clock. The "BYO at the brnrd layer" described here
is specifically about the **managed dispatcher routing spawns
to the subscriber's cloud account** — the dispatcher and the
vault are brnrd-side; only the compute target shifts.

## Upsun deployment notes (brnrd backend)

brnrd hosts on Upsun for the launch prototype (per
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

The live config itself lives on a public **`deploy` branch** (root
`.upsun/` symlinked to the in-tree `deploy/upsun/` template, kept in
sync by a clean `main`→`deploy` merge Action), not on `main` — see
[`decision-monorepo-structure.md`](decision-monorepo-structure.md) →
"The live brnrd.dev deployment runs from a `deploy` branch" for the
branch + symlink + autosync shape.

## Out of scope (for this design)

- The brnrd service codebase (lives at `src/brnrd/` in the
  monorepo; this page is the API spec, not the implementation).
- Detailed billing / invoicing surfaces — the per-task accounting
  hooks above feed into them, but the user-facing billing UI is a
  separate design.
- The brnrd dashboard (covered in
  [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
  — uses these REST endpoints as a client).
- The **ergonomics ingestion endpoint** (the brnrd-side sink for
  back-channel observability records from daemons) — specced in
  [`design-agent-ergonomics.md`](design-agent-ergonomics.md);
  joins this protocol as `POST /v1/daemons/ergonomics` when that
  design's brnrd-sink slice lands.
- The `fanout` multi-daemon policy (deferred per above).
- Server-side spawn for *online* daemons as a convenience layer
  (i.e. "brnrd takes the task even though my daemon is up,
  because the daemon is busy"). Possibly worth doing as a
  load-shedding feature; explicitly deferred until usage shows
  whether it matters.
- The agentic-mode upgrade to brnrd (proactive scheduling,
  cross-project secretary behaviours, platform-level connectors).
  See
  [`decision-connectors-layering.md`](decision-connectors-layering.md)
  for the layering that makes that upgrade path coherent.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic context (two surfaces, work continuity, brnrd as
   thin dispatcher + credential vault).
2. [`plan-conversation-id-propagation.md`](plan-conversation-id-propagation.md)
   for the small daemon-side change (commit trailer +
   conversation_id POST on response) that powers the cross-gate
   conversation graph.
3. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the pricing model the per-task accounting hooks feed.
4. [`design-billing.md`](design-billing.md) for the wallet /
   Stripe / debit-at-finalize / refund mechanics that turn
   spawn outcomes from this design into actual billing
   operations.
5. [`decision-cli-shape.md`](decision-cli-shape.md) for the
   `brr brnrd <subcommand>` CLI verbs that wrap these
   endpoints, and for the seventh top-level verb (`brr kb`)
   and the `brr config` sub-verbs (template / validate) added
   in the same pass.
5a. [`design-config-layout.md`](design-config-layout.md) for
   the three-scope config model that the account-scope
   settings endpoints back, and for the `brr.toml` project-
   scope file that the daemon-equivalent bootstrap reads at
   spawn time.
6. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   for the gate-adapter implementation sequencing (GH App slice
   first, TG bot fast-follow on the same backend).
7. [`plan-failover-compute.md`](plan-failover-compute.md) for
   the failover-spawn implementation sequencing (AI-credential
   vault, dispatcher decision tree, brnrd-owned Fly pool,
   permission gate API).
8. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   for the dashboard built on top of these endpoints.
9. [`research-cloud-envs.md`](research-cloud-envs.md)
   for the cross-env patterns the server-side caller uses
   (cloud runs are envs).
10. [`decision-connectors-layering.md`](decision-connectors-layering.md)
    for why gates stay per-project and connectors live at the
    platform level.
11. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
    for where `src/brnrd/` lives and how the dashboard / envs
    relate.
12. [`src/brr/gates/README.md`](../src/brr/gates/README.md) for the
    existing BYO gate protocol the cloud gate is peer to.

## Lineage

- 2026-05-22 — drafted (as `design-managed-gates.md`) as part of
  the managed-mode KB shape rollout. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-22 — renamed to `design-brr-run-protocol.md` and
  grown with the spawn-compute / failover-dispatch path when
  the work-continuity reframe shifted the always-on-box answer
  to brr.run-as-failover-dispatcher; cloud-credential storage
  and the dispatcher decision tree added.
- 2026-05-25 (pass 3) — added the cross-gate conversation
  context machinery: metadata-only event_metadata graph
  (event_id ↔ conversation_id ↔ branch_name, no body, 30-day
  TTL), `Brnrd-Conversation-Id` git commit trailer as the
  conversation_id source-of-truth, conversation_id POST on
  responses, conversation_id inference rules for events arriving
  without an upstream id, three-source spawn-context assembly
  (originating event + gate-side history fetch + git replay),
  Telegram per-chat ring buffer (50 msgs × 72h) as the one
  named concession on data minimization with full audit-log
  visibility, dashboard rendering split (proxy when daemon
  online, gate-replay when offline), `GET /v1/internal/context/
  {event_id}` endpoint that composes the fetches. "What we DO
  hold" table promoted to a load-bearing subsection inside the
  data-minimization principle, listing every persistent surface
  with scope + TTL + reason. Fourth reframe breadcrumb in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-25 — reshaped: BYO cloud-platform tokens dropped from
  launch (preserved as a "designed, deferred" section); managed
  compute consolidates on brr.run-owned cloud (this page still
  used `brr.run` as the product name at this stage);
  AI-credential vault added (api-key + dir-tarball shapes);
  multi-project routing protocol added (project_id resolution,
  sticky/prefix for TG/Slack/Discord); permission-prompt API
  added (`/v1/internal/prompts` + gate-side webhooks); data
  minimization principle added as a load-bearing section
  governing every endpoint; Upsun deployment notes added.
  Pondering follow-up in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1 (third
  reframe breadcrumb).
- 2026-05-25 — renamed `design-brr-run-protocol.md` →
  `design-brnrd-protocol.md` when the hosted-product name
  settled on `brnrd` (canonical domain `brnrd.dev`). All
  product-name references in this page updated from `brr.run`
  to `brnrd`; the API surface, endpoint paths, and protocol
  contract are unchanged.
- 2026-05-25 (pass 4) — spawn-finalize accounting hook now
  triggers a wallet debit per the new
  [`design-billing.md`](design-billing.md) (the page that now
  owns wallet / Stripe / refund mechanics; this page only
  exposes the per-task accounting hooks the billing design
  consumes). Cost fields stay USD on the API surface;
  conversion to credits happens at debit time in the billing
  layer. Failover-spawn step 6 description updated to call the
  env class directly (since "cloud runners are envs" per
  [`research-cloud-envs.md`](research-cloud-envs.md), the
  failover path is a daemon-equivalent bootstrap +
  `envs.get_env("fly_machines")` invocation, not a separate
  "cloud-runner adapter"). Cloud-runner-adapter framing dropped
  in the spawn step + the BYO-deferred section. Fourth
  2026-05-25 reframe breadcrumb in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-25 (pass 4 follow-up) — "Pairing flow" section
  reorganised around a new top-level
  **"`brr brnrd connect` — three-layer smart bootstrap"**
  description. Layer 1 (account pair) introduces
  `POST /v1/accounts/pair` + `GET /v1/accounts/pair/{pair_code}`
  endpoints; Layer 2 (project create) introduces
  `POST /v1/accounts/projects`; Layer 3 (gate pair via
  detectors) introduces
  `POST /v1/accounts/projects/{project_id}/gates/{kind}` for
  auto-bind when the detector finds an already-installed App
  (avoiding the pair-code dance). Telegram + GitHub sections
  now framed as Layer-3 detectors that can also be invoked
  explicitly via `brr brnrd pair <gate>`. Idempotency rules
  documented (each layer skipped if already satisfied). Drove
  by the user's "we should autosetup gates when
  `brr brnrd connect`" feedback.
- 2026-05-25 (pass 4 follow-up — second wave) — two additions
  driven by the user's "could we sync local config with remote
  runs / use the user's docker image in fly machines too?"
  feedback:
  1. New **account-scope settings endpoint family**
     (`GET / PUT / DELETE /v1/accounts/settings[/{key}]`) for
     the account scope of the three-scope config model. Daemons
     fetch at startup + every 5 min; brnrd-side spawns fetch
     at bootstrap. Schema lives client-side (see
     [`design-config-layout.md`](design-config-layout.md));
     brnrd treats values as opaque TOML-serialised blobs per
     `(account_id, key)`.
  2. **Daemon-equivalent bootstrap now reads `brr.toml`** from
     the cloned repo (failover dispatch step 6) and layers it
     with account-scope settings to build the effective config
     before invoking the env. Project-level preferences
     (docker image, runner choice, env default) now flow from
     the repo to brnrd-side spawns automatically — no protocol
     push, the repo is the message. Private docker images
     flagged as a launch-blocker for the spawn path with a
     clear gate-side error; generic credential-vault extension
     tracked as an open question — **resolved in the third
     wave below**.
- 2026-05-25 (pass 4 follow-up — third wave) — two additions
  driven by the user's "actually want private images and
  encrypted credential dir mounting" + "current pricing won't
  make this project successful" feedback:
  1. **Credential vault generalised**. The earlier
     `/v1/accounts/ai-credentials` endpoint family renamed to
     `/v1/accounts/credentials` with a `kind` discriminator
     covering both AI-runner credentials (Anthropic / OpenAI /
     Google / GitHub — preserving the `dir-tarball` shape for
     Claude Pro / Codex Plus / Gemini OAuth) AND
     docker-registry credentials (ghcr.io / docker.io / etc.).
     Same encryption, same audit-log shape, same revoke
     semantics. Failover dispatch step 6 now performs
     `docker login` before `docker pull` when the project's
     `brr.toml` declares a private image — resolves the
     "private image launch-blocker" open question. AI-credential
     security model renamed to "Credential security model" and
     extended to cover registry credentials. CLI surface
     extended with `brr brnrd creds add docker-registry
     --registry <host> --username --token`. Audit-log entries
     for docker-registry credential reads added. BYO compute
     "designed, deferred" rewrite updated to use the same
     credential vault for cloud-platform tokens when BYO
     comes back (new `kind` value, not a new endpoint family).
  2. **Subscription endpoints** added to the API surface
     (`/v1/accounts/subscription[/checkout|cancel|resume|portal]`)
     to back the new billing leg in
     [`design-billing.md`](design-billing.md). Project-creation
     endpoint now enforces tier-based project cap. "What we DO
     hold" table gains a subscription state row (mirrored to
     account-scope settings as `subscription.tier` for in-band
     reads by daemon + dispatcher). Stripe webhook contract on
     `/v1/internal/stripe/webhook` extended to handle
     `customer.subscription.*` and `invoice.*` events
     alongside the existing one-shot top-up events. Initial
     third-wave shape used `tier="plus"` / `plan="plus_monthly"`
     and a tier cap of 1 project on Free / 10 on Plus.
- 2026-05-26 (third-wave follow-up) — **subscription state
  values renamed** to drop the "Plus" branding (tier value
  `"plus"` → `"subscribed"`; past-due `"plus_past_due"` →
  `"subscribed_past_due"`; plan codes `"plus_monthly"` /
  `"plus_annual"` → `"monthly"` / `"annual"`). **Free
  project cap raised from 1 → 3**; subscriber cap unchanged
  at 10; project-creation endpoint enforcement updated
  accordingly. CLI verb at the wrapper level reshaped from
  `brr brnrd plus [upgrade | downgrade | status]` to
  noun-first `brr brnrd subscription [status | start | cancel
  | resume | portal]` (with `brr brnrd subscribe` shortcut).
  Endpoint paths themselves unchanged. Driven by the user's
  "I don't like Plus as a name or verb; tweaked Free might
  not need the 1-project cap" feedback.
- 2026-05-26 (locking pass — BYO compute at launch + credit
  buckets). **Credential vault grew a third domain** —
  `cloud-platform` with a `provider` discriminator (`fly` at
  launch; Modal / Daytona / etc. when their managed support
  ships). Write + read paths gate on
  `subscription.tier == "subscribed"`; Free accounts can't
  store cloud-platform creds. Vault internals (table schema,
  encryption, audit log shape) unchanged — the new kind sits
  in the same row layout with the existing per-account
  envelope-key encryption + audit shape. **Dispatcher
  branches on BYO-cred presence**: if the subscribed account
  has a `cloud-platform` cred for the target env's provider,
  the env class is invoked with the user's token and the spawn
  emits `spawn_byo` to the audit log (no wallet debit; user
  pays cloud provider directly); otherwise managed path runs
  unchanged. Same env class, two callers — the "Caller axis"
  pattern from
  [`research-cloud-envs.md`](research-cloud-envs.md). **"BYO
  compute — designed, deferred" section rewritten** as "BYO
  compute — subscriber feature, parallel-shipped with managed":
  policy is "if we ship a cloud managed, BYO ships in the same
  release; never BYO-only-for-clouds-we-don't-manage." At
  launch only Fly is managed, so only BYO Fly ships. Same BYO-
  for-subscribers principle pre-applies to future agentic-
  secretary connectors via the same `credentials` table
  (different `kind`, same gate, same vault). Driven by the
  user's "since we charge per paying customer anyway we can
  actually allow byo everything on top of that" framing,
  combined with the credit-bucket / per-source-expiry lock-in
  in [`design-billing.md`](design-billing.md).
- 2026-05-26 (locking pass II — project cap unlock, binding
  uniqueness, Free signup bonus reflection). **Project-
  creation endpoint enforcement updated** for the new
  tiered cap (3 Free / 25 Subscribed-not-unlocked / unlimited
  Subscribed-unlocked); 409 response body carries a
  `subscription_hint` populated per-tier ("subscribe for 25"
  vs "top up $X.XX more to unlock unlimited"). **New "Binding
  uniqueness — correctness + abuse-mitigation" section** below
  the bindings endpoint table: global uniqueness on
  `(platform, chat_id)` for chat bindings and on
  `repo_full_name` for repo bindings, enforced at the DB
  layer with UNIQUE constraints; 409 response with
  obfuscated `bound_to_account` (no PII leak). Same
  enforcement serves routing correctness AND multi-account
  abuse mitigation — without it, a Free user could create N
  accounts and bind the same repo / chat to all of them. We
  don't add fingerprinting / IP velocity / "suspicious
  account" flagging at launch (overengineering at our
  scale). **"What we DO hold" table grew a row** for the
  cumulative-purchase counters (`cumulative_purchased_credits_lifetime`,
  `cumulative_purchased_usd_lifetime`, `project_cap_unlocked`)
  + their mirror keys (`subscription.project_cap`,
  `subscription.project_cap_unlocked`) in account-scope
  settings. Driven by the user's "capped at smth high like 25,
  unlimited as soon as they spent smth small but reasonable on
  credits" + "we maybe need to implement project ownership, so
  a user wouldn't go creating multiple accounts to get more
  credits on the same project."
- 2026-05-26 (locking pass IV — machine-scoped daemon
  reshape + runtime profile). Two changes, both diagram /
  framing rather than protocol-contract:
  1. **"The protocol shape, at a glance" diagram redrawn**
     to show the machine-scoped multi-project daemon shape:
     one `brr daemon` process per machine serves N
     brr-init'd repos (each with its own `.brr/inbox/` +
     `brr.toml`), discovered via `~/.config/brr/projects.toml`;
     one `httpx.AsyncClient` (HTTP/2-pooled) carries traffic
     for all projects to brnrd; account binding lives at
     `~/.local/state/brr/account/`, not per repo. Inbox
     long-poll returns events for ALL the daemon's projects
     in one batch, tagged with project_id; the daemon
     dispatches each to the right per-project asyncio task.
     A new "Five things to note" callout under the diagram
     names the differences from the pre-pass-IV per-project-
     daemon framing.
  2. **New "Runtime profile: async, httpx, ASGI" section**
     codifies the runtime profile both sides ship against.
     Daemon side: `httpx.AsyncClient`, `asyncio` event loop,
     `uvloop` soft-dep, no web framework (local IPC is
     `asyncio.start_unix_server`), constraint of
     easy-pip-installability on stock Python 3.11+.
     Brnrd side: FastAPI / starlette ASGI, `asyncpg`,
     `redis-py` async client, `httpx` outbound, `structlog`
     + `sentry-sdk` + `stripe` SDK, read-only-rootfs friendly
     (state in postgres + redis + S3-blob; deploys cleanly
     on Fly / Modal / upsun / Render / Railway), horizontal
     scale via container replicas not threads. The
     asymmetry is deliberate — brr lightweight, brnrd
     richer. Current daemon is sync; the async migration
     lands as one slice alongside the multi-project reshape
     to avoid a transitional shape. Driven by the user's
     "the local daemon should serve all local projects and
     connect to the brnrd... if a user has configured brnrd
     once for a project already, we should pickup at least
     the account binding, subscription status, brnrd url"
     + "do you think we need to add async/httpx to the local
     daemon to let it talk to brnrd effectively (the brnrd
     itself likely has to be reactive, and efficient, and
     may use packages, it is not as important to be easily
     installed as brr, but smart enough to be able to run
     on read only app container services like upsun." Status
     promoted from "proposed" to "accepted."
