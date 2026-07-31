# Backend runtime contract

This is the environment contract of the `brnrd` FastAPI process. It is an
inventory of `src/brnrd/config.py` and the two direct environment reads in the
runtime entrypoints. Values belong in the deployment platform, never in this
repository. How the running deployment is wired — image, registry, rollout,
and what the platform does and does not do on its own — is
[`scaleway.md`](scaleway.md).

## Container process

| Contract | Value |
| --- | --- |
| Command | `uvicorn brnrd:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}"` |
| Port | `PORT`; defaults to `8000`. The platform's configured container port must match it. |
| Health | `GET /healthz` returns HTTP 200 with `{"status":"ok","service":"brnrd"}`. |
| Frontend | `BRNRD_FRONTEND_DIR=/app/frontend`; the image copies the SvelteKit static build there. This explicit path is required because an installed package cannot discover the source-checkout-relative `src/frontend/build`. |
| Build identity | CI passes the full Git commit as `BRNRD_BUILD_COMMIT` at image-build time. The image stamps that commit and its UTC build time into the installed package for `GET /v1/stats/version`; this build argument is not a runtime setting. |
| State | The production database is external. The container filesystem is not persistent state. |

## Core service

| Variable | Secret | Default | Consumer |
| --- | --- | --- | --- |
| `PORT` | No | `8000` | The Docker image command binds uvicorn to the port injected by the container platform. |
| `BRNRD_HOST` | No | `127.0.0.1` | `brnrd.__main__` bind address when the alternate `python -m brnrd` entrypoint is used. The image command binds `0.0.0.0` directly. |
| `BRNRD_PORT` | No | `8000` | `brnrd.__main__` bind port when the alternate `python -m brnrd` entrypoint is used. The image command uses `PORT`. |
| `BRNRD_DATABASE_URL` | **Yes** | `sqlite:///./brnrd.db` | `app.create_app` passes it to SQLAlchemy for application state and startup migrations. Production must supply the managed PostgreSQL URL. |
| `BRNRD_PUBLIC_BASE_URL` | No | `http://localhost:8000` | Pairing and approval links, OAuth callback URLs, Stripe return URLs, attachment URLs, and automatic Telegram webhook registration. |
| `BRNRD_INBOX_LONGPOLL_MAX_S` | No | `25.0` | `routers.daemons` caps a daemon inbox long-poll request. |
| `BRNRD_INBOX_POLL_INTERVAL_S` | No | `0.5` | `routers.daemons` database polling interval within a long-poll request. |
| `BRNRD_PAIR_TTL_S` | No | `600` | `routers.pairing` expiry for device and Telegram pairing codes. |
| `BRNRD_PACK_RELAY_TTL_S` | No | `3600` | `app.create_app` default lifetime of the in-memory diffense pack relay. |
| `BRNRD_ENABLE_DEV` | No | `1` | `app.create_app` registers development enqueue endpoints unless the value is exactly `0`. The image sets `0`. |
| `BRNRD_FRONTEND_DIR` | No | Source-layout discovery | `spa.resolve_frontend_dir` locates the built SvelteKit shell and assets. The image sets `/app/frontend`. |
| `BRNRD_HSTS_MAX_AGE` | No | `31536000` | `security_headers.HSTSMiddleware` emits `Strict-Transport-Security: max-age=<n>` on HTTPS responses. `0` suppresses the header for an operator whose own edge sets it; an edge-set header is never overwritten. |

Invalid numeric values use the defaults shown above.

## Telegram

| Variable | Secret | Default | Consumer |
| --- | --- | --- | --- |
| `BRNRD_TELEGRAM_BOT_TOKEN` | **Yes** | Empty | Telegram webhook registration, message/card delivery, and attachment retrieval in `app`, `inbox`, and `routers.daemons`/`routers.webhooks`. |
| `BRNRD_TELEGRAM_WEBHOOK_SECRET` | **Yes** | Empty | Telegram webhook signature check and automatic webhook registration. |
| `BRNRD_TELEGRAM_BOT_USERNAME` | No | Empty | `routers.pairing` builds the Telegram deep link. |
| `BRNRD_TELEGRAM_AUTO_WEBHOOK` | No | `true` | `app` decides whether startup registers the public Telegram webhook. **A bot has exactly one webhook: a second deployment sharing the bot token steals the registration on every instance start.** Shadow/staging deployments must set `0`. |
| `BRNRD_TELEGRAM_AUTHZ_ALLOWLIST` | No | Empty | Comma-separated Telegram user IDs allowed by `routers.webhooks` in addition to the paired principal. |
| `BRNRD_TELEGRAM_MEDIA_MAX_MB` | No | `10` | `routers.daemons` caps attachment proxy buffering. |

## GitHub, web sessions, and OAuth

The unprefixed `GITHUB_*` names below are compatibility aliases. When both are
set, the `BRNRD_*` name wins.

**The `Secret` column is not a required column.** `BRNRD_GITHUB_APP_ID` and
`BRNRD_GITHUB_APP_PRIVATE_KEY_B64` are jointly load-bearing: `platforms.github_app.app_jwt`
raises on either one, so a deployment missing *either* cannot mint a single
installation token — no managed runner can push, and no managed reply reaches
an issue. The App id is not a secret, which is exactly why a migration that
copies the **Yes** rows carries the key and drops the id. Verify both after
any host move: `GET /v1/daemons/inbox` returns `server.github.app_id_set` and
`app_key_set` (2026-07-31, the Scaleway cutover).

| Variable | Secret | Default | Consumer |
| --- | --- | --- | --- |
| `BRNRD_SESSION_COOKIE` | No | `brnrd_session` | Cookie name used by `auth` and `routers.web_auth`; this is a name, not a cookie value. |
| `BRNRD_GITHUB_OAUTH_CLIENT_ID` / `GITHUB_CLIENT_ID` | No | Empty | `oauth` and `routers.web_auth` enable GitHub OAuth and construct authorization/token requests. |
| `BRNRD_GITHUB_OAUTH_CLIENT_SECRET` / `GITHUB_CLIENT_SECRET` | **Yes** | Empty | `oauth` exchanges the authorization code. |
| `BRNRD_GITHUB_OAUTH_SCOPE` | No | `user:email` | `oauth` authorization request scope. |
| `BRNRD_GITHUB_OAUTH_AUTHORIZE_URL` | No | `https://github.com/login/oauth/authorize` | `oauth` authorization endpoint. |
| `BRNRD_GITHUB_OAUTH_TOKEN_URL` | No | `https://github.com/login/oauth/access_token` | `oauth` token endpoint. |
| `BRNRD_GITHUB_API_BASE_URL` | No | `https://api.github.com` | OAuth user lookup, GitHub App calls, webhook enrichment, and replies. |
| `BRNRD_GITHUB_API_VERSION` | No | `2026-03-10` | GitHub REST request version header. |
| `BRNRD_GITHUB_APP_ID` / `GITHUB_APP_ID` | No | Empty | `platforms.github_app` signs App JWTs and enables managed App sessions. |
| `BRNRD_GITHUB_APP_PRIVATE_KEY_B64` / `GITHUB_APP_PRIVATE_KEY_B64` | **Yes** | Empty | `platforms.github_app` decodes the App signing key. |
| `BRNRD_GITHUB_APP_SLUG` / `GITHUB_APP_SLUG` | No | `brnrd-dev` | Dashboard/App identity, managed-bot login construction, and webhook mention parsing. |
| `BRNRD_GITHUB_INSTALL_URL` / `GITHUB_INSTALL_URL` | No | `https://github.com/apps/brnrd-dev/installations/new` | Dashboard GitHub App installation link. |
| `BRNRD_GITHUB_WEBHOOK_SECRET` / `GITHUB_WEBHOOK_SECRET` | **Yes** | Empty | Both GitHub webhook routers reject signatures unless this is configured. |
| `BRNRD_GITHUB_BOT_LOGIN` | No | `brnrd-bot` | Managed webhook mention matching, authorization, and dashboard display. |
| `BRNRD_GITHUB_TRIGGER_ALIASES` | No | `brnrd,brr` | `routers.webhooks` command/mention aliases. |
| `BRNRD_GITHUB_BOT_TOKEN` | **Yes** | Empty | Fallback credential for GitHub comments, review replies, and PR metadata calls. |
| `BRNRD_GITHUB_AUTHZ_ALLOWLIST` | No | Empty | Comma-separated lowercased GitHub logins allowed by the managed webhook beyond repository-role authorization. |
| `BRNRD_OAUTH_STATE_COOKIE` | No | `brnrd_oauth_state` | OAuth state-cookie name in `routers.web_auth`. |
| `BRNRD_OAUTH_PKCE_COOKIE` | No | `brnrd_oauth_pkce` | OAuth PKCE verifier-cookie name in `routers.web_auth`. |
| `BRNRD_OAUTH_NEXT_COOKIE` | No | `brnrd_oauth_next` | Post-login destination-cookie name in `routers.web_auth`. |
| `BRNRD_OAUTH_STATE_TTL_S` | No | `600` | Lifetime of the three temporary OAuth cookies. |

## Stripe and billing

| Variable | Secret | Default | Consumer |
| --- | --- | --- | --- |
| `BRNRD_STRIPE_API_KEY` | **Yes** | Empty | `stripe_api` authenticates Stripe API calls; billing endpoints are unavailable when empty. |
| `BRNRD_STRIPE_WEBHOOK_SECRET` | **Yes** | Empty | `routers.webhooks` verifies Stripe webhook signatures. |
| `BRNRD_STRIPE_API_BASE_URL` | No | `https://api.stripe.com` | `stripe_api` request base URL. |
| `BRNRD_STRIPE_PRICE_SUPPORTER_MONTHLY` | No | Empty | `billing` selects and classifies the monthly supporter Stripe price. |
| `BRNRD_STRIPE_PRICE_SUPPORTER_ANNUAL` | No | Empty | `billing` selects and classifies the annual supporter Stripe price. |
| `BRNRD_STRIPE_PRICE_PUBLIC_MONTHLY` | No | Empty | `billing` selects and classifies the monthly public Stripe price. |
| `BRNRD_STRIPE_PRICE_PUBLIC_ANNUAL` | No | Empty | `billing` selects and classifies the annual public Stripe price. |
| `BRNRD_SUBSCRIBER_MONTHLY_CREDITS` | No | `300` | `billing` grants the recurring subscriber credit amount. |
| `BRNRD_SUPPORTER_COHORT_SIZE` | No | `200` | `billing` cohort eligibility and `routers.stats` public seat count. |
| `BRNRD_SUPPORTER_COHORT_DEADLINE` | No | Empty | Optional ISO date cutoff used by `billing`; empty closes only on cohort size. |
| `BRNRD_TOPUP_MIN_USD` | No | `5` | `routers.billing` minimum accepted top-up. |
| `BRNRD_TOPUP_MAX_USD` | No | `500` | `routers.billing` maximum accepted top-up. |

## Service limits

All values are non-secret integers consumed by `limits.py`.

| Variable | Default | Consumer |
| --- | --- | --- |
| `BRNRD_LIMIT_FREE_REPOS` | `3` | Connected-repository cap for free accounts. |
| `BRNRD_LIMIT_FREE_EVENTS_PER_MINUTE` | `6` | Free-account event burst cap. |
| `BRNRD_LIMIT_FREE_EVENTS_PER_DAY` | `200` | Free-account daily event cap. |
| `BRNRD_LIMIT_ABUSE_REPOS` | `100` | All-tier abuse ceiling for connected repositories. |
| `BRNRD_LIMIT_ABUSE_EVENTS_PER_MINUTE` | `60` | All-tier event-rate abuse ceiling. |
| `BRNRD_LIMIT_ABUSE_EVENTS_PER_DAY` | `5000` | All-tier daily event abuse ceiling. |
| `BRNRD_LIMIT_MAX_EVENT_BODY_BYTES` | `100000` | Maximum inbound event body size. |
| `BRNRD_LIMIT_MAX_EVENT_ATTACHMENTS` | `10` | Maximum attachments on one inbound event. |

## Platform adapters

None. Every variable above is read directly from the process environment, so
the contract is the same on a container host, a VM, and a laptop.

This section used to list the Upsun-provided `POSTGRESQL_*` / `PLATFORM_*`
variables that a repo-root `.environment` shell script translated into the
portable `BRNRD_*` names. That adapter, `.upsun/config.yaml`, and the
`PLATFORM_TREE_ID` build-identity fallback were deleted on 2026-07-31 with the
move to a Scaleway Serverless Container. Keeping a translation layer for a host
nobody deploys to is how a contract acquires a second, unchecked copy of itself
— the failure `spa.py` already carries a comment about.

The *knowledge* was kept: [`scaleway.md`](scaleway.md) → "Running it on a PaaS
instead" carries the shape that worked and the four things that bit. A future
PaaS gets its own adapter written against the table above, from that page —
not this one revived from git.
