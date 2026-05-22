# Plan: daemon deployment templates

Small content / template work that cashes out the daemon-hosting
story from [`subject-managed-mode.md`](subject-managed-mode.md) →
Daemon hosting: one `brr/daemon` Docker image variant + a
`deploy/` folder of platform-specific templates + a "deploying brr"
docs page.

## Status

**Not started.** Lightly coupled to
[`plan-env-fly-machines.md`](plan-env-fly-machines.md) on the
Dockerfile-split work — both plans need the daemon-only image to
land first.

## Goals

- Lower the "where do I run brr" friction from "read the daemon
  docs and figure it out" to "click one button" for the most
  common platforms.
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
    `/app` doesn't accommodate `git worktree` directly).
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
   hosting for the strategic context.
2. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §4 for
   the deployment-targets table that drove this plan.

## Lineage

- 2026-05-22 — drafted as part of the managed-mode KB shape
  rollout.
