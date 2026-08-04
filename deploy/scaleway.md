# Deploying the brnrd backend

How `brnrd.dev` is actually built, shipped, and rolled out, and what the
platform underneath does and does not do for us. The environment variables
themselves are inventoried once, in
[`runtime-contract.md`](runtime-contract.md) — this page is the wiring, not a
second copy of the table.

Written 2026-07-31, the day the Upsun deployment was deleted. Everything under
"Facts about the platform" was read out of Scaleway's own documentation or
driven against the running service on that date; where a claim came from a
probe rather than a document, the probe is named.

## The shape

One stateless container, one external database, one CDN in front.

| Layer | What |
| --- | --- |
| Image | Root [`Dockerfile`](../Dockerfile) — multi-stage: `node:22` builds the SvelteKit SPA, `python:3.12-slim` installs `.[backend,postgres]` and copies the build to `/app/frontend` |
| Registry | Scaleway Container Registry, `rg.fr-par.scw.cloud/brnrd/brnrd`. GHCR (`ghcr.io/hugimuni-labs/brnrd`) holds the canonical copy; the Scaleway push is a mirror, because Scaleway warns against pulling from an external public registry |
| Runtime | Scaleway Serverless Container, region `fr-par` (France) |
| Database | External managed PostgreSQL. The container filesystem is not state |
| Edge | Cloudflare — TLS, caching, and the `brnrd.dev` name |

The daemon on a user's machine dials *out* to this; nothing dials in except
webhooks (Telegram, GitHub, Stripe).

## The release path

`.github/workflows/publish-container.yml`, on every push to `main`:

1. derive an **immutable** tag — `sha-<first 7 of the commit>`
2. build and push to GHCR with `BRNRD_BUILD_COMMIT` as a build arg
3. mirror the same image to the Scaleway registry
4. **roll it out** — `scripts/scw_rollout.py`, which settles → `PATCH
   registry_image` → settles again, and reports Scaleway's own sentence on
   any failure

Step 4 is not optional decoration, and step 3 alone is not a release. See the
next section for why.

Three repository secrets gate the tail, and each missing one degrades
*visibly*: `SCW_SECRET_KEY`, `SCW_REGISTRY_IMAGE`, `SCW_CONTAINER_ID`. Without
the third the workflow mirrors the image and emits a `::notice::` saying it
deployed nothing — never a silent success.

### Build identity

`scripts/stamp_build_info.py` is the **one** writer of `build_info.txt`, and
the image is now its only caller. It records the sha, the build time, and
whether that first line is a real git sha; `GET /v1/stats/version` reports it.

Verify a deploy by **`built_at`**, never by eyeballing `commit` — that habit
comes from the 2026-07-30 incident where the field could not answer the
question it was named for.

## Facts about the platform

The four that have each cost a run:

**A registry push does not update the running container.** Scaleway's FAQ, in
its own words: *"When you push a new image with the same tag (e.g. `:latest`)
to the Container Registry, Serverless Containers does not automatically use
the updated image. To use the updated image, you must Redeploy the container.
Scaling events (scaling up, down, or from zero) do not fetch the latest image
digest."* We tag immutably, which makes it stronger still — the container is
pinned to a tag no later build ever reuses, so nothing but the rollout call
in step 4 can move production.

**A deploy costs no downtime.** Scaleway performs a rolling update: traffic
shifts to the new version incrementally while the old one keeps serving, and
the old version is decommissioned only once the new one is fully serving. In
practice the visible effect is that a daemon's 25 s inbox long-poll takes one
dropped connection and reconnects.

**A failed deploy is a 24-hour fuse, not a no-op.** Scaleway supports no
versioning and no automatic rollback. After a failed deployment the previous
version keeps answering for up to 24 hours; then *both* the failed and the
working container are deleted and nothing serves until a successful deploy
lands. A red publish run must be fixed, not deferred.

**Updating a container that is mid-flight answers `409 transient_state`.**
Editing environment variables in the console *is* an update, and so is a
rollout — so a console edit and a merge at the same moment collide. Two
consequences worth internalising: don't merge while the container settings are
open, and `scripts/scw_rollout.py` deliberately settles before it patches and
retries a 409 with backoff. It also never posts `/deploy`: `UpdateContainer`
already redeploys, and chaining `DeployContainer` after it is Scaleway's own
documented cause of that same 409.

## Configuring the container

Everything in [`runtime-contract.md`](runtime-contract.md) is read straight
from the process environment. Non-secret values go in the container's
**Environment variables**; secrets go in **Secrets**. Both are edited in place
in the console, which is the recommended path — `scw container container
update` takes `environment-variables.{key}`, and whether a partial map merges
or replaces server-side is not something we have verified; getting it wrong
drops `BRNRD_DATABASE_URL`. If you want the CLI, read the current set with
`scw container container get <id>` first and pass all of it.

Saving either list triggers a rolling deploy of the same image.

### The GitHub App pair is jointly load-bearing

`BRNRD_GITHUB_APP_ID` (plain) and `BRNRD_GITHUB_APP_PRIVATE_KEY_B64` (secret,
`base64 -w0` of the PEM, one line) are the only inputs to
`platforms.github_app.app_jwt`, which raises on either half. Everything that
mints an **installation token** is downstream of them — including the
credential a connected daemon pushes git with.

This trips people because *receiving* GitHub webhooks needs neither: that path
needs only the webhook secret. So "the GitHub App is working" and "pushing is
dead" can both be true at once, and were, for eleven hours on 2026-07-30 after
a migration carried the secrets but not the non-secret `APP_ID`.

The private key cannot be re-downloaded from GitHub — it is shown once, at
generation. If the PEM is lost, generate a new key in the App's settings and
delete the old entry; the App keeps working throughout.

### Transport security is the app's, not the edge's

HSTS is emitted by `src/brnrd/security_headers.py` on any request that arrived
over TLS, not by a route-table setting. That is a deliberate correction: it
*was* three lines of the deleted `.upsun/config.yaml`, and the cutover left it
behind, so production served no `Strict-Transport-Security` header at all
until 2026-07-31. Cloudflare can set its own — an operator-set header is never
overwritten — but nothing has to be configured for the guarantee to hold.

## Running it on a PaaS instead

The Upsun deployment was deleted on 2026-07-31. It is recorded here rather
than in a config file because the *knowledge* outlives the artifact, and
because a translation layer nobody deploys through rots unchecked. If you are
putting this backend on a Heroku-shaped platform, this is the shape that
worked and the four things that bit:

- **One process, no mounts.** `uvicorn brnrd:create_app --factory --host
  0.0.0.0 --port $PORT`. All state is in PostgreSQL.
- **A build-only second toolchain.** The SPA needs Node at build time and
  never at runtime. On Upsun that was a `dependencies: nodejs: npm` block
  alongside a `python:3.12` runtime; a container does it with a build stage.
- **The platform's variables need an adapter.** Upsun exposes a database
  *relationship* as `POSTGRESQL_*` and a route table as a base64
  `PLATFORM_ROUTES`, neither of which this app reads. A repo-root
  `.environment` shell file translated them into `BRNRD_DATABASE_URL` and
  `BRNRD_PUBLIC_BASE_URL`. Write that adapter against the runtime contract's
  table; do not reintroduce platform names into the application.
- **`BRNRD_FRONTEND_DIR` is required, not optional, wherever the build hook
  `pip install`s the package.** Installed into site-packages, the
  source-relative guess in `resolve_frontend_dir` resolves next to the
  installed package and finds nothing, and every SPA deep link 404s.

Two smaller lessons from that deployment, both still true anywhere:

- **`npm ci` demands byte-exact lock agreement and several frontend deps ship
  optional per-platform binaries whose nested ranges resolve to whatever the
  registry has live that day.** A lock resolved on one date can fail `ci`'s
  sync check on another with an unchanged `package.json` (hit live 2026-07-06:
  `@emnapi/core` 1.11.1 vs 1.11.2). The container build pins with `npm ci` and
  accepts the strictness; a PaaS build hook that cannot be re-run
  deterministically wants `npm install`.
- **A bot has exactly one webhook.** `BRNRD_TELEGRAM_AUTO_WEBHOOK` defaults on
  and registers at boot, so any second deployment sharing the bot token steals
  the registration from the live one every time it starts. Staging and
  rollback-window deployments must set it to `0`.

## What is not settled here

- Whether `scw container container update` merges or replaces the environment
  map. Unverified; the console is recommended precisely because of it.
- The container's scaling settings. A connected daemon holds a 25 s long-poll
  open, so scale-to-zero never fires in practice and `min-scale 1` is the
  intended shape — but the running configuration is not read back by anything
  in this repository, so this page cannot assert what it currently is.
- Cloudflare's own configuration (cache rules, HSTS, WAF). It is real, it is
  in front of every request, and no file here describes it.
