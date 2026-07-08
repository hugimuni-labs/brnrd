# Agent visual inspection of brnrd.dev (authenticated)

Status: **shipped, option 1, 2026-07-08** (decided + verified live,
run-260708-1021-i28y). Opened 2026-07-08 (run-260708-0941-hmik) as a
discussion turn; the maintainer picked an arm the same day.

## The ask

Direct question, telegram: "I want to make the brnrd.dev available /
viable rendered for your inspection logged into my account, so you can
see what I see, when we are working on the visuals. What would it take?"

Distinct from what already works: a resident can already Playwright-
screenshot `https://brnrd.dev/` unauthenticated (used live,
[`plan-loom-realtime-build.md`](plan-loom-realtime-build.md) §Slice 1.5,
to catch the dark-theme/no-page-background bug). What's missing is the
*authenticated* dashboard — the user's own runs/PRs/quota, the actual
view he's looking at when a visual discussion is happening.

## What's actually there today (confirmed, not assumed)

- **Auth is GitHub OAuth only** — no magic link, no password, no dev
  login. PKCE flow: `src/brnrd/oauth.py:36-96`. Callback resolves
  identity, issues a session, sets a cookie:
  `src/brnrd_web/routes.py:340-365`.
- **Session = a server-validated bearer cookie**, not a self-contained
  JWT. Cookie name `brnrd_session` (`src/brnrd/config.py:53`,
  overridable via `BRNRD_SESSION_COOKIE`), `httponly`, `samesite=lax`,
  `secure` in prod, `max_age` 30 days
  (`SESSION_TTL`, `src/brnrd/routers/accounts.py:22`). The raw cookie
  value hashes (SHA-256, `src/brnrd/security.py:12-14`) into the `Token`
  table (`src/brnrd/models.py:51-64`) — `kind="session"`, `account_id`,
  `revoked`, `expires_at`. Whoever holds the raw value *is* that account,
  full stop, until it's revoked or expires — there is no secondary
  binding (IP, device, user-agent) checked anywhere.
- **No impersonation / admin / dev-account / support-token / staging
  concept exists anywhere** — grepped all of `src/brnrd*` and all of
  `kb/` for "impersonat", "admin login", "support token", "dev account",
  "view-as", "read-only viewer": zero hits. `src/brnrd/routers/dev.py`
  (`/v1/_dev/enqueue`) is a webhook stand-in behind normal
  `require_account` auth, disabled in prod
  (`.upsun/config.yaml:62`, `BRNRD_ENABLE_DEV=0`) — not a login
  mechanism.
- **A second token kind is half-designed, not built.** `Token.KIND_API_KEY`
  ("account_api_key") exists in the model/`ACCOUNT_KINDS`
  (`src/brnrd/models.py:53`) and `design-brnrd-protocol.md`'s daemon-auth
  section describes a long-lived account key / short-lived run-key bearer
  shape — but that's the *daemon-facing* `/v1/daemons/*` API auth, a
  different surface from the browser session the SvelteKit dashboard
  needs, and no route anywhere actually issues a `KIND_API_KEY` token
  today. Building on this wouldn't be a two-line change; it needs its own
  issuance endpoint either way.
- **One environment.** `.upsun/config.yaml` defines a single prod route,
  no `environments:`/staging block. No seeded/sanitized copy of the data
  to point a browser at instead of the real account.
- **The frontend is a client-side SPA calling JSON APIs** — the "visual"
  the maintainer sees is rendered browser-side from authenticated fetches
  (`src/frontend`, adapter-static). A bearer API token alone (even if one
  existed) doesn't make Playwright see the rendered page; the browser
  context needs the actual session cookie set, because that's what the
  frontend's fetch calls carry. Any token-based scheme still needs a
  token→cookie exchange step to be useful for screenshotting.

## The real options, given the above

1. **Cookie handoff (works today, zero code).** Maintainer copies the
   `brnrd_session` cookie value from his own browser (devtools →
   Application → Cookies) and hands it to a local Playwright script via
   `context.add_cookies(...)`, read from an untracked local file/env var
   — never committed, never logged. Mechanically sound (the cookie is
   already a portable bearer value with no extra binding) and needs no
   product work. The real cost: it's literally his live human session —
   the same one his own browser is using. Revoking it (logout, or a
   future "sign out everywhere") logs him out too; if the raw value ever
   leaked (git history, a stray log line, a screenshot artifact) it's a
   full account takeover with a 30-day shelf life, no separate blast
   radius from "the founder's own login."
2. **Finish the half-built account API key, plus a token→session
   exchange.** Ship an issuance endpoint + minimal settings-page UI for
   `KIND_API_KEY` (schema already there), and a small
   `/auth/token-login?key=...`-shaped route that exchanges a valid API
   key for a normal session cookie so Playwright can still drive the
   real rendered SPA. Independently revocable from the human's own login
   session; a real, if small, feature (new endpoint, new UI surface,
   auth-path testing) rather than a credential you hand over by hand.
3. **A dedicated scoped "agent/dev viewer" identity** — the clean-room
   answer: a separate account-scoped principal with its own session,
   ideally read-only-enforced at the route layer, independently
   revocable, never touches the human's own credential at all. Correct
   shape, most new surface: an impersonation/view-as concept doesn't
   exist in this codebase in any form today, so this is a new auth
   primitive, not a token flavor — the biggest lift of the three.

## Read as a fork, not decided here

This is a live security/product call about credential handling on a
real (if pre-revenue, single-founder) production account, not a
reversible resident-scoped judgment call — flagged back rather than
picked. Given the project's actual shape right now (solo founder, one
GitHub-connected account, pre-release, `~€1k` legal/ops budget per
[`decision-hosted-execution-liability.md`](decision-hosted-execution-liability.md)),
option 1 is the pragmatic near-term answer — it costs nothing to build
and the "blast radius" concern is largely theoretical for an account
that's currently just the founder's own — but it is a real trade the
maintainer should make with eyes open, not one a resident should quietly
default into. Option 2 is the right move if this becomes a recurring
need rather than a one-off ("let's look at this together" happening
often) — it's a bounded, already-half-designed feature. Option 3 is
worth naming but not worth building until there's more than one human
account in play (a real support/ops need), since it solves a
multi-tenant problem this product doesn't have yet.

## Decided + verified live (2026-07-08, run-260708-1021-i28y)

Maintainer picked option 1 directly: "ok, I didn't merge the PR yet
(situational information anyway)... I settled on option 1, yes. Made a
file with the cookie: `.tmp/brnrd-session.cookie` hope it works, lemme
know if you can see the browser page."

Verified end to end, not just plausible:

- `.tmp/brnrd-session.cookie` (untracked; `*.cookie` added to
  `.gitignore` this same run so a future `git add -A` can't sweep it up)
  holds the raw `brnrd_session` value.
- Local Playwright (Chromium, already installed on this host from the
  Slice 1.5 unauthenticated work): `context.addCookies([{name:
  "brnrd_session", value: <read from the file>, domain: "brnrd.dev",
  path: "/", httpOnly: true, secure: true, sameSite: "Lax"}])`, then
  `page.goto("https://brnrd.dev/")`.
- **HTTP 200**, and the rendered body is the real resident dashboard —
  window-track quota bars, live runs (this very run's original-event
  text visible in the LIVE RUNS card), PR review queue showing #284
  itself, run receipts — not the logged-out landing page. Screenshot
  taken locally (not committed; it's a live account view, same handling
  as the cookie itself).
- The cookie value itself was never printed, logged, or written
  anywhere but the one untracked local file it already lived in.

Net: option 1 works exactly as scoped in the fork above — zero code,
the maintainer's live session, no separate blast radius. Nothing left
to build for this to keep working; PR #284 carries this update.

## Read next

- [`design-dashboard-live-surface.md`](design-dashboard-live-surface.md)
  — the dashboard visual work this capability would actually serve;
  §Current-state audit is the precedent for screenshot-driven bug-finding.
- [`plan-loom-realtime-build.md`](plan-loom-realtime-build.md) §Slice 1.5
  — the existing unauthenticated-Playwright-screenshot workflow this
  would extend.
- [`design-brnrd-protocol.md`](design-brnrd-protocol.md) — the daemon-
  facing api-key/run-key bearer shape `KIND_API_KEY` partially mirrors;
  a real account-facing API key issuance route should read this first so
  the two don't drift into two different "api key" meanings.
