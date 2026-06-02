# Plan: brnrd inbox-as-service spine

Status: in flight (started 2026-05-27)

Shipped so far:

- **Slice 1 — spine + connect (2026-05-27).** Inbox queue, accounts /
  projects / device-flow pairing, daemon register / long-poll /
  respond / deregister, dev enqueue stand-in, the shared gate runtime,
  and the `cloud` gate. See "Wire-format subset" below.
- **Slice 2 — Telegram ingress + approve page (2026-05-31).** Real
  `POST /v1/webhooks/telegram`, Telegram chat-pairing, a
  platform-dispatching forwarder, and a thin `src/brnrd_web` dashboard
  (login + the device-flow approve page) so connect is human-
  completable. See "Second slice" below.

Still ahead (tracked in [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)):
GitHub webhook ingress, the fuller dashboard (projects / tasks /
vault), subscription tiers + billing, multi-daemon routing/failover.

The first executable slice of the brnrd backend. It exists to
unblock [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md),
whose "Not started" status is gated on "a small brnrd backend
prototype demonstrating the inbox-as-service protocol
end-to-end." This page is that prototype's sequencing and the
record of what the prototype does and deliberately defers.

It builds against the accepted wire format in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) and the
package/license layout in
[`decision-monorepo-structure.md`](decision-monorepo-structure.md).

## What the slice proves

The full round trip, minus the real platform webhooks:

```
producer → brnrd inbox (queue, project-scoped)
         → daemon long-poll drain → .brr/inbox/<event>.md
         → runner → .brr/responses/<event>.md
         → cloud gate POST /v1/daemons/responses
         → brnrd forwards to the reply target, drops the body
```

A passing end-to-end test that enqueues an event on the brnrd
side, drains it through the daemon's `cloud` gate into the real
`.brr/inbox/` protocol, simulates a runner response, and sees
brnrd record the forwarded delivery (body dropped) is the
slice's done-signal.

## Scope (confirmed with the operator)

- **In:** inbox-as-service spine + the device-flow connect
  handshake that mints a project-scoped daemon token. Accounts,
  projects, pairing, daemon register/long-poll/respond/deregister,
  a dev enqueue ingress standing in for platform webhooks.
- **Storage:** SQLite via SQLAlchemy, URL-swappable to Postgres
  later (`BRNRD_DATABASE_URL`). No Alembic yet — `create_all` on
  startup; migrations land with Postgres.
- **Out (deferred, tracked in the launch plan):** GitHub webhook
  ingress + signature verification, project caps + subscription
  tiers, billing, failover compute, the *fuller* `src/brnrd_web/`
  dashboard (projects / tasks beyond login + approve), multi-daemon
  routing/failover policy, credential vault. (Telegram webhook
  ingress and the thin approve page shipped in slice 2.)

## Wire-format subset implemented

Daemon-facing (token kind `daemon`, project-scoped):

| Method | Path | Role in the spine |
|--------|------|-------------------|
| POST | `/v1/daemons/register` | declare `daemon_name` + capabilities; idempotent on `(token, daemon_name)` |
| GET | `/v1/daemons/inbox?since=<seq>&wait=<s>` | long-poll; returns queued events with `seq > since` for the token's project |
| POST | `/v1/daemons/responses` | `{event_id, body_markdown, status}`; brnrd forwards body to the event's reply target, persists metadata only |
| POST | `/v1/daemons/deregister` | mark the daemon offline |

Account-facing (token kind `account` — an API key or a session):

| Method | Path | Role |
|--------|------|------|
| POST | `/v1/accounts` | create account (email + password) → account id + initial API key (shown once) |
| POST | `/v1/accounts/sessions` | login → session token |
| POST | `/v1/accounts/projects` | create project → `project_id` |
| GET | `/v1/accounts/projects` | list projects |
| POST | `/v1/_dev/enqueue` | dev-only webhook stand-in: `{project_id, body, reply_to}` → queues an event |

Device-flow connect (the CLI half of pairing):

| Method | Path | Auth | Role |
|--------|------|------|------|
| POST | `/v1/accounts/pair` | none | start pairing → `{pair_code, pair_url, poll_secret}` |
| POST | `/v1/accounts/pair/{code}/approve` | account | approve + bind to a project → mints a daemon token |
| GET | `/v1/accounts/pair/{code}?poll_secret=` | poll_secret | poll; on `paired` returns the daemon token once |

`POST /v1/_dev/enqueue` is the only non-protocol endpoint: it
stands in for `/v1/webhooks/{telegram,github}` so the queue /
drain / respond loop is testable without a real platform. It is
clearly named and disabled outside the prototype.

## Data minimization, demonstrated

The slice honors the protocol's "you own your data" stance on
the irreversible path: `POST /v1/daemons/responses` records only
metadata (status, body length, latency ms) on the event row and
forwards the body to the reply target — it never persists the
response body. Event bodies are retained only while queued for
their own daemon to drain (the user's own task text, for the
user's own daemon); dropping queued bodies after ack is a noted
follow-up, not part of the spine.

## Second slice — Telegram ingress + approve page

A single managed bot serves every account, multiplexed by `chat_id`.
The webhook is authenticated by the secret-token header Telegram
echoes from `setWebhook` (`X-Telegram-Bot-Api-Secret-Token`,
constant-time compared) — not a bearer, since Telegram can't carry
one.

| Method | Path | Auth | Role |
|--------|------|------|------|
| POST | `/v1/accounts/pair/telegram` | account | issue a one-time `TG-…` code bound to a project |
| POST | `/v1/webhooks/telegram` | secret header | `/start <code>` binds the chat; a bound chat's message enqueues; an unbound chat is ignored |
| GET/POST | `/login` | — | web session login (sets the `brnrd_session` cookie) |
| GET/POST | `/connect/{code}` | session cookie | the device-flow approve page (lists projects, calls `approve_core`) |

Routing home: a bound chat's message enqueues with an opaque
`reply_to = {platform, chat_id, topic_id, message_id}`. When the
runner's response comes back through `POST /v1/daemons/responses`, the
**platform-dispatching forwarder** (`inbox.make_default_forwarder`)
reads `reply_to['platform']` and posts the body via the Telegram Bot
API, threaded under the source message — still without persisting it.

Chat→project binding is global-unique on `(platform, chat_id)`: a
`/start` from a chat already bound to a *different* account is refused
(the bot replies), so one chat can't be silently re-pointed.

Code-reuse notes for the slice:

- `approve_core(db, account_id, code, project_id)` was factored out of
  the API approve endpoint so the web `/connect/{code}` page mints the
  exact same daemon token by the exact same path.
- `authenticate` / `issue_session_token` were factored out of the API
  login endpoint and reused by the web `/login`.
- `src/brnrd_web/` is its own AGPLv3 package (own `LICENSE`), bundled
  by the `brr[backend]` extra and `include_router`-ed by the app;
  forms pull in `python-multipart`. Hand-rolled HTML for now — a
  template engine lands with the fuller dashboard.
- Latent bug fixed in passing: `auth._resolve` compared a naive
  SQLite `expires_at` against an aware `now`; session tokens (the
  first expiring bearer exercised) tripped it. Stored times are now
  coerced to UTC before comparison.

## Build order

1. **Bootstrap (monorepo + license).** `pyproject.toml` gains a
   `backend` extra (FastAPI, uvicorn, SQLAlchemy, httpx) folded
   into `dev` so the suite exercises brnrd. Per-package
   `LICENSE` files (`src/brr/LICENSE` MIT, `src/brnrd/LICENSE`
   AGPLv3), a top-level `LICENSE-OVERVIEW.md`, and a `src/brnrd/`
   app factory with `GET /healthz`.
2. **Storage + accounts/projects/connect.** `db.py` (engine +
   session), `models.py` (account, token, project, event,
   pair_request), `auth.py` (bearer dependency resolving token
   kind/scope), and the account + pairing routers.
3. **Inbox endpoints.** The daemon-facing router (register /
   long-poll inbox / responses / deregister) plus the dev
   enqueue ingress.
4. **Code reuse + cloud gate.** Extract the shared gate runtime
   (below), migrate Slack + Telegram onto it behavior-preservingly,
   then build `src/brr/gates/cloud.py` as a thin gate on that
   runtime, plus `brr brnrd connect` and registration in
   `_BUILTIN_GATES`.

## Code reuse — shared gate runtime

Slack and Telegram duplicate four things nearly verbatim:
`_load_state` / `_save_state`, the per-task progress-card state
file helpers, the backoff `run_loop` wrapper, and the
`_deliver_responses` skeleton (iterate `list_done` → read
response → post → `cleanup`). These move to
`src/brr/gates/runtime.py` as gate-name-parameterized helpers:

- `load_state` / `save_state(brr_dir, gate, ...)`
- `load_task_card` / `save_task_card(brr_dir, gate, task_id, ...)`
- `run_loop(loop_once, *, label, poll_interval=0, backoff_max=120)`
- `deliver_responses(inbox_dir, responses_dir, source, deliver)`
  — `deliver(event, body)` raises to signal a per-event delivery
  failure (logged + skipped, no cleanup), mirroring today.

Slack and Telegram keep their existing module-level names
(`_load_state`, `_loop_once`, `_deliver_responses`, …) as thin
delegators so their tests stay green unchanged; the per-platform
chat/thread resolution stays in each gate's `deliver` closure.
`cloud.py` then reuses the same runtime, making it a thin
wrapper over the daemon-facing endpoints. The GitHub gate
(`src/brr/gates/github/`) is webhook/PR-shaped, not the
poll/deliver skeleton, so it stays out of this extraction; its
`state.py` / `delivery.py` are a possible later reuse candidate,
not forced here.

## Test plan

- brnrd endpoints via FastAPI `TestClient`: account create →
  project create → pair → approve → poll (token minted);
  register → enqueue → inbox drains it → respond (body dropped,
  metadata kept) → deregister.
- Long-poll returns promptly when an event is already queued and
  returns empty after `wait` with none; `since` cursor advances
  and is idempotent on re-poll.
- 401 on missing/wrong token; daemon endpoints reject account
  tokens and vice-versa; project isolation (daemon A never sees
  project B's events).
- `cloud` gate loop in isolation (HTTP chokepoint monkeypatched
  to the `TestClient`): drains into `.brr/inbox/*.md`, delivers a
  response, persists its `since` cursor across a restart.
- Shared-runtime extraction: the full existing Slack + Telegram
  suites stay green.
- Slice 2 (`test_brnrd_telegram.py`): bad/absent webhook secret →
  403; `/start <code>` binds the chat and confirms; an invalid code
  is reported; a bound chat enqueues with the right `reply_to`; an
  unbound chat is ignored; a runner response is forwarded back to the
  originating chat, threaded under the source message.
- Slice 2 (`test_brnrd_web.py`): login sets the session cookie and
  rejects bad credentials; `/connect/{code}` redirects to login when
  unauthenticated; the page lists the account's projects; approving
  makes the CLI poll return the minted daemon token.

## Deployment (Upsun)

brnrd deploys to Upsun; the brr daemon stays local and dials out, so
only brnrd is hosted. `.upsun/config.yaml` + `.environment` configure a
`python:3.12` app with a PostgreSQL relationship: build
`pip install .[backend,postgres]` (psycopg3, wheels-only), start
`uvicorn brnrd:create_app --factory --host 0.0.0.0 --port $PORT`
(tcp/`$PORT`). `BRNRD_DATABASE_URL` (`postgresql+psycopg://`) and
`BRNRD_PUBLIC_BASE_URL` (the primary route) are derived in
`.environment` from the platform's relationship/route vars; secrets
(bot token, webhook secret) ride `upsun variable:create` and are never
committed. Postgres is a clean drop-in: `db.make_engine` gates its
SQLite-only `connect_args` and `Event.seq` is an autoincrement PK
(→ `SERIAL`), so `create_all` on startup suffices (no Alembic yet).

Gotcha: a Telegram bot takes exactly one consumer — `getUpdates` (the
local `telegram` gate) and `setWebhook` (brnrd) are mutually exclusive
on the same token. Run brnrd on its own bot, or migrate the bot off
the local gate; you can't do both at once.

## Read next

1. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) — the
   full wire format this slice implements a subset of.
2. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   — the Surface A launch this prototype unblocks.
3. [`subject-managed-mode.md`](subject-managed-mode.md) — the hub
   for the managed-mode design.
4. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   — the package + license layout the bootstrap follows.
