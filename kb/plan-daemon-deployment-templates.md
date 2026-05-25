# Plan: daemon deployment templates

Small content / template work that cashes out the *cloud-first
daemon hosting* story from
[`subject-managed-mode.md`](subject-managed-mode.md) → Daemon
hosting: one `brr/daemon` Docker image variant + a `deploy/` folder
of platform-specific templates + a "deploying brr" docs page.

## Status

**Demoted to launch-nice-to-have on 2026-05-22.** Earlier framing
positioned the always-on-host model as the *preferred* answer to
"my laptop is offline" — that answer is now
[`plan-failover-compute.md`](plan-failover-compute.md) (brr.run
spawns per-task sandboxes in the user's or its own cloud account,
without the user operating a separate always-on box). These
templates remain useful for the niche where the user genuinely
wants a cloud-first daemon home (security policy, no laptop at all,
"I want my daemon to live next to my prod"), but they are no longer
load-bearing for the work-continuity pitch. Ship when convenient;
do not block the launch on them.

Lightly coupled to
[`plan-env-fly-machines.md`](plan-env-fly-machines.md) on the
Dockerfile-split work — both plans need the daemon-only image to
land first.

## Goals

- Lower the "where do I run brr" friction *for the cloud-first
  audience* — users who don't want a laptop daemon at all, not
  users whose laptop is offline 30% of the time.
- A daemon image small enough that read-only PaaS templates
  (Heroku / Upsun / Render / Railway / App Platform) deploy in
  under a minute.
- Templates that work today, not "would work if we wrote them."

## Done definition

- `brr/daemon` Docker image published, distinct from `brr/runner`.
  Daemon image excludes claude / codex / gemini CLIs (cloud-hosted
  daemons fan out to per-task envs) — keeps the image under
  ~150 MB.
- `deploy/` folder in brr core repo with these sub-templates:
  - `deploy/fly/` — `fly.toml` + minimal `Dockerfile` referencing
    `brr/daemon:latest`. `flyctl launch` produces a working
    deployment.
  - `deploy/render/` — `render.yaml` Blueprint. One-click deploy
    button in the README.
  - `deploy/heroku/` — `app.json` + `Procfile`. Heroku-button
    compatible.
  - `deploy/upsun/` — `.upsun/config.yaml` template covering the
    writable-mount config for `.brr/` and repo clones (read-only
    `/app` doesn't accommodate `git worktree` directly). Shares
    the read-only-container shape with the brr.run *backend*
    Upsun deployment (per
    [`design-brr-run-protocol.md`](design-brr-run-protocol.md) →
    "Upsun deployment notes"); the daemon template and the
    backend template will share patterns (build-vs-deploy split,
    routes-yaml, writable mounts, secrets) and should be authored
    together.
  - `deploy/railway/` — Railway template config.
  - `deploy/vps/` — `docker-compose.yml` + systemd unit template
    for "I have an Ubuntu box" users.
  - `deploy/docker-compose/` — bare `docker-compose.yml` for "I
    have docker somewhere" users (NAS, Synology, RPi, etc.).
- `src/brr/docs/deploying.md` page covering target selection
  (which template to pick), credential delivery (how to wire
  `FLY_API_TOKEN` / runner keys / GitHub PAT into each platform's
  secret store), and the read-only-PaaS caveats (no `docker` env
  on these — must fan out to cloud-runner envs).
- Each template includes a 1-2 paragraph README explaining
  trade-offs and the runtime envs supported on that target.

## Steps

1. **Dockerfile split.** Refactor
   [`src/brr/Dockerfile`](../src/brr/Dockerfile) into a
   multi-stage build producing two named targets:
   - `daemon` — Python + brr package + supporting CLIs (git, gh,
     curl, jq). No claude / codex / gemini.
   - `runner` — Python + brr package + claude / codex / gemini +
     dev tools.

   Build matrix publishes both as `brr/daemon:latest` and
   `brr/runner:latest`.
2. **Fly template.** Smallest possible — `fly.toml` + a
   `Dockerfile` that's two lines (`FROM brr/daemon` + the config
   mount). Tested on a free-tier Fly app.
3. **Render Blueprint.** `render.yaml` with a single web service
   referencing `brr/daemon:latest`; documented secret-var setup.
4. **Heroku button.** `app.json` declaring the buildpack-less
   container deploy + the required env vars.
5. **Upsun template.** `.upsun/config.yaml` with the
   writable-mount declaration for `.brr/` and `/data/repos/` —
   the daemon clones repos into `/data/repos/` instead of the
   read-only `/app`. Workers section for the long-running daemon
   process.
6. **Railway template.** Railway's GitHub-coupled template format
   pointing at the same image.
7. **VPS template.** `docker-compose.yml` + a `brr-daemon.service`
   systemd unit template for non-container users.
8. **Bare docker-compose.** Minimal compose for "I just have
   docker" users.
9. **Docs page.** `src/brr/docs/deploying.md` with the target
   selection matrix, credential wiring patterns, and read-only
   PaaS caveats.

## Estimate

~200 LOC total across all templates (each is small); ~150 LOC
Dockerfile refactor; ~200 LOC docs page. Mostly content / config
work, very little Python.

## Out of scope

- Cloud-runner env adapters (those are separate plans:
  [`plan-env-fly-machines.md`](plan-env-fly-machines.md), and
  future plans per
  [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)).
- `brr install-service` for macOS + Linux daemon supervision —
  separate plan (`plan-install-service.md`, not yet drafted).
- Kubernetes / Helm chart — defer until a real user asks; the
  bare docker-compose template is the "just give me a container"
  alternative.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) → Daemon
   hosting for the strategic context and the demotion rationale.
2. [`plan-failover-compute.md`](plan-failover-compute.md) for the
   work that replaced this plan as the *load-bearing* answer to
   laptop-down dispatch.
3. [`design-brr-run-protocol.md`](design-brr-run-protocol.md) →
   "Upsun deployment notes" for the brr.run *backend* Upsun
   template — shares the read-only-container shape, should be
   authored together with this plan's `deploy/upsun/` template.
4. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   for where `deploy/` lives in the monorepo (shared across the
   daemon and the brr.run backend templates).
5. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §4 for
   the original deployment-targets table that drove the first
   draft of this plan, plus the 2026-05-22 reframe breadcrumb in
   §1 that explains the demotion.

## Lineage

- 2026-05-22 — drafted as part of the managed-mode KB shape
  rollout.
- 2026-05-22 — demoted to launch-nice-to-have when the work-
  continuity reframe made
  [`plan-failover-compute.md`](plan-failover-compute.md) the
  load-bearing answer to laptop-down dispatch. Scope and goals
  retained but recontextualised to the cloud-first audience.
- 2026-05-25 — Upsun template entry cross-linked to the brr.run
  backend's Upsun deployment work (shared read-only-container
  shape; should be authored together). Added monorepo-structure
  decision to "Read next" so the `deploy/` location is
  unambiguous. Third reframe breadcrumb in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
