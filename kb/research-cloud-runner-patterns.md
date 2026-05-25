# Research: cloud-runner patterns and per-platform deltas

Cross-adapter analysis for cloud-execution adapters in brr. The
same adapter code is consumable from two callers (see "Caller
axis" below):

- **Laptop daemon caller** — user installs a `brr-env-*` plugin
  and uses their own cloud account. Independent of managed mode;
  this is user-driven plugin work shipped on each plugin's own
  clock.
- **brnrd server-side caller** — brnrd uses one (or a few)
  adapter(s) on its **own** cloud account to power the managed-
  compute failover surface from
  [`subject-managed-mode.md`](subject-managed-mode.md) (Surface
  B). Fly Machines is the first adapter used this way.

At launch, brnrd ships exactly one server-side adapter (Fly
Machines on a brnrd-owned Fly app). Other adapters are
available to laptop-daemon users but **not** wired up server-side
on brnrd — BYO server-side compute (a user's cloud token stored
on brnrd, used by brnrd to spawn in their account) is
deferred from launch per
[`decision-pricing-shape.md`](decision-pricing-shape.md); the
wire shape is preserved in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"BYO compute — designed, deferred."

Promoted from
[`notes-pondering-fleet.md`](notes-pondering-fleet.md) §2 to be
a durable reference that per-platform adapter plans cite.

## TL;DR

1. Every cloud-runner adapter implements the existing
   [`design-env-interface.md`](design-env-interface.md) Protocol
   (`prepare → invoke → finalize`). No new protocol needed; cloud
   runners are variations of the designed `ssh` env with the
   transport swapped for a per-platform SDK or REST API.
2. Each adapter is structurally callable from **either** the
   laptop daemon (user's own cloud account) **or** brnrd
   server-side (brnrd's own cloud account for managed compute,
   OR — when BYO server-side spawn lands post-launch — the user's
   account using a stored token). At launch, only Fly Machines is
   wired up server-side; other adapters are laptop-daemon-only.
3. The cross-adapter complexity lives in three patterns:
   credential delivery, repo delivery, and result delivery. Each
   has 2-3 ranked options; each platform picks per its constraints.
4. Per-platform cold start ranges from ~90ms (Daytona from
   snapshot) to ~minutes (cold Codespaces). Per-task cost floor
   for a 5-minute small task ranges from a fraction of a cent
   (Fly Machines per-second) to free-tier (Codespaces personal
   account).
5. First adapter to ship is **Fly Machines** — fastest cold start,
   REST API, cheapest per-task, AND the chosen brnrd server-side
   managed-compute backend. **Codespaces** is the cheap
   fast-follow on the laptop-daemon-only side (`gh` CLI,
   devcontainer-native, huge audience overlap).
6. Read-only PaaS platforms (Heroku, Upsun, Render, Railway, App
   Platform) are NOT cloud-runner candidates — wrong runtime
   shape. They are *daemon-hosting* candidates and *brnrd
   backend-hosting* candidates; see
   [`subject-managed-mode.md`](subject-managed-mode.md) → Daemon
   hosting and
   [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
   "Upsun deployment notes."

## Caller axis — same adapter, callable from two places

The adapter Protocol is symmetric across callers. What that
means in practice:

- **Laptop daemon caller** (today's `EnvBackend` shape). The
  user runs the daemon; the daemon picks the adapter from
  `.brr/config`; the adapter reads the platform token from the
  user's env. This is the existing designed shape from
  [`design-env-interface.md`](design-env-interface.md). Available
  for every adapter listed below as soon as that adapter's
  plugin ships.
- **brnrd server-side caller** (managed-compute Surface B).
  brnrd runs the dispatcher; when a user's daemon is offline
  and failover is approved per policy, the dispatcher
  instantiates the adapter against brnrd's own cloud account
  (managed compute) and runs the same `prepare → invoke →
  finalize` sequence. At launch, **only Fly Machines is wired up
  server-side**; the brnrd backend imports the
  `brr-env-fly-machines` plugin and calls it with its own
  pool-control token. Specified in
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
  "Failover dispatch."

A *third* caller — brnrd server-side using a **user's stored
cloud token** to spawn in the user's cloud account ("BYO
server-side compute") — is supported by the Protocol but **not
shipped at launch** per
[`decision-pricing-shape.md`](decision-pricing-shape.md). The
wire shape is preserved in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"BYO compute — designed, deferred" for clean add-back when usage
justifies. When this third caller comes back, the deltas table
below covers it under "brnrd server-side caller."

The adapter code is identical between callers. What differs:

| Concern | Laptop daemon caller | brnrd server-side caller |
|---------|---------------------|----------------------------|
| **Token source** | `os.environ[adapter.api_token_env]` | brnrd's own pool-control token (managed compute, launch); decrypted from the user's stored cloud-credential vault at spawn time, cleared after (BYO server-side, deferred) |
| **Repo delivery** | Per Pattern B below — usually `git clone` with a token from the user's env | Per Pattern B below — but the git token comes from a per-spawn GH App installation token (for GitHub remotes) OR a per-account deploy key (for other remotes) |
| **AI-credential delivery** | Per Pattern A below — env vars / mounted dirs from the user's home | Decrypted from brnrd's AI-credential vault at spawn time per `design-brnrd-protocol.md` → "AI-credential vault"; injected as env var (api-key shape) OR tar-expanded into `$HOME/<provider>` (dir-tarball shape) |
| **Response delivery** | Writes to `.brr/responses/` on the daemon host | Sandbox carries a one-shot `task-key` (Bearer token scoped to one `event_id`, 1h TTL) and POSTs to `/v1/daemons/responses` directly |
| **Failure salvage** | Daemon's `salvage` rule from [`subject-envs.md`](subject-envs.md): preserve on `error` / `conflict`, destroy on `done` | Server-side default destroy-on-anything; on failure, orphan response written to `.brr/failover-orphans/<event-id>.md` and pushed via git so user sees the trace |
| **Cost ceiling** | Not the adapter's concern (user pays their own bill in real time) | Enforced before spawn by the dispatcher per `failover-policy.monthly_spawn_cap` and `monthly_cost_cap_usd` |

Implementation guarantee: any adapter written for the laptop
caller can be invoked from the server caller by wrapping it in a
small caller-context object that injects the token source and the
response sink. Adapters do not need two implementations.

This is the load-bearing reason adapters ship as plugin packages:
the same plugin package the laptop daemon `pip install`s as
`brr-env-fly-machines` is the same package the brnrd backend
imports for its managed-compute spawn path. One implementation,
two deployment targets.

## Part 1 · The minimum protocol delta

Every adapter implements `EnvBackend` per
[`design-env-interface.md`](design-env-interface.md). The per-phase
work, in adapter terms:

| Phase | What the adapter does |
|-------|-----------------------|
| `prepare` | Create a sandbox / VM / workspace on the platform; choose or build the image; upload the repo (clone or bundle); upload credentials (env vars + auth dirs); record the handle in `ctx.env_state`. |
| `invoke` | Exec the runner CLI inside the sandbox; stream stdout / stderr back to the host trace; honour the task timeout. |
| `finalize` | Push the branch back (from inside the sandbox) or bundle-and-fetch (from the host); pull the response file to `response_path_host`; destroy the sandbox on clean `status=done`; preserve on `status ∈ {error, conflict}` per the salvage rule from [`subject-envs.md`](subject-envs.md). |

The runner CLI install is **not** a per-task concern — it ships
inside the image. The bundled image at
[`src/brr/Dockerfile`](../src/brr/Dockerfile) already builds a
practical runner image with claude / codex / gemini and the dev
tools brr expects; the same image is the starting point for every
cloud-execution adapter. Per-platform customisation is just choosing
where to host the image and how to point the platform at it.

## Part 2 · The three cross-cutting patterns

### Pattern A — Credential delivery

Local docker uses bind-mounts of host credential dirs
(`_DOCKER_DEFAULT_CRED_PATHS` in
[`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py)):

```python
_DOCKER_DEFAULT_CRED_PATHS = (
    ".claude", ".claude.json", ".codex", ".gemini",
    ".gitconfig", ".config/gh", ".ssh",
)
```

Remote sandboxes cannot bind-mount the host's home directory. The
credentials need to get into the sandbox by other means. Three
ranked vehicles, least-to-most operationally demanding:

1. **Env vars only.** Forward `ANTHROPIC_API_KEY` /
   `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY` /
   `GITHUB_TOKEN` through the platform's env / secret system.
   Sufficient when the runner CLI supports keyed auth and the
   operator pays via direct API key.
2. **Env vars + platform secret store for credential dirs.** Tar
   `~/.claude/` / `~/.codex/` / `~/.gemini/` into the platform's
   encrypted secret store; expand at sandbox start. Needed when
   the runner CLI uses subscription auth (Claude Pro, Codex Plus,
   Gemini OAuth) — those credentials live in directories the CLI
   reads from `$HOME` and don't reduce to env vars.
3. **One-shot upload at task start.** Use the platform SDK's file
   API (Daytona, E2B, Modal) or `scp` / `gh codespace cp` to drop
   the credential dirs in the sandbox before `invoke`. Slower per
   task; simpler to reason about than option 2.

All adapters should consume `_DOCKER_DEFAULT_CRED_PATHS` rather
than inventing per-platform variants.

**Server-side caller specifics.** The brnrd server-side caller
sources its credentials from the AI-credential vault per
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"AI-credential vault endpoints." The vault accepts two payload
shapes (api-key + dir-tarball) on a single endpoint; the
dispatcher decrypts them in process memory at spawn time and
injects via the appropriate vehicle from the list above:

- api-key shape → vehicle 1 (env var injection at machine create).
- dir-tarball shape → vehicle 2 if the platform supports an
  encrypted secret store for tar payloads (Fly Machines has this
  via `flyctl secrets set --staged` + a small startup decode), OR
  vehicle 3 (one-shot upload before the runner CLI starts) as
  fallback.

Subscription-auth (Pro / Plus / OAuth) users are first-class on
the server-side path via the dir-tarball shape — no API-key
provisioning required, same UX as the local docker env's
mounted-dir flow.

### Pattern B — Repo delivery

Three patterns, each with trade-offs:

1. **`git clone https://${TOKEN}@github.com/<owner>/<repo>` in the
   sandbox `prepare`.** Cleanest; assumes the sandbox can reach
   the remote and the operator has provisioned a per-task token.
   Best default.
2. **`git bundle create` locally then upload + `git fetch`.**
   Works over any transport; expensive for large repos; useful
   when the remote isn't reachable from the sandbox (corporate
   VPN, private mirror).
3. **Platform-native volume / snapshot reuse.** Daytona snapshots,
   Fly volumes pinned to a host. Lowest per-task cost; highest
   coupling to the platform; only sensible once an adapter is
   shipping at non-trivial volume.

### Pattern C — Result delivery

Branch back, response file back. Three patterns:

1. **Push from the sandbox** to the remote (simplest, assumes the
   remote is reachable and the token has push). Best default.
2. **Bundle + fetch from the host** (the designed `ssh` env's
   pattern). Useful when the sandbox can't push directly.
3. **Stream over stdout of `invoke`.** The runner's stdout
   already carries the response — `runner.invoke_runner` captures
   it before any finalize step touches files. For the response
   file specifically, this is the cheapest path.

### Cold start budgets

Brr tasks tend to be 1-15 minutes. A 60-second cold start is
acceptable; a 5-minute cold start is not. Per platform:

| Platform | Cold start (warm image / snapshot) | Cold start (fresh image build) |
|----------|------------------------------------|---------------------------------|
| Fly Machines | ~300ms | ~tens of seconds |
| Daytona (from snapshot) | ~90ms | ~tens of seconds |
| Modal | ~seconds | ~minute+ |
| E2B (from template) | ~seconds | n/a (templates pre-built) |
| Codespaces | ~tens of seconds (warm) | ~minutes (fresh) |
| Vanilla VM (cloud-init) | ~tens of seconds to minutes | n/a |
| SSH to always-on box | 0 (highest standing cost) | 0 |

### Network policy

Each runner CLI calls home (Anthropic / OpenAI / Google) and the
sandbox calls the git remote. Most platforms default to open
egress; some let users restrict it (Daytona network allow-list,
Modal `outbound_cidr_allowlist`). Brr adapters should default
permissive with an opt-in tightening config key, not the other
way around.

## Part 3 · Per-platform briefs

### Fly Machines

- **Why first.** Fastest cold start of the credible options
  (~300ms from warm image); smallest VM is a few cents per hour, so
  a 5-min task is under a cent; pure REST API with no SDK lock-in;
  per-second billing of running compute; `auto_destroy: true`
  matches brr's ephemeral-by-construction contract directly.
- **Adapter shape.** `prepare` calls `POST /v1/apps/{app}/machines`
  with `config.image = <our runner image>`, `auto_destroy: true`,
  and an env block carrying the credential keys. Repo via
  `git clone https://${TOKEN}@github.com/...` in the image's entry
  command (Pattern B option 1). `invoke` is SSH via WireGuard or
  `POST .../exec`. `finalize` pushes the branch from inside the
  machine; response file captured from `invoke` stdout.
- **What brr needs to add.** A `FlyMachineEnv` adapter (~300-400
  LOC informed estimate); the bundled runner image needs to be
  published to a Fly-reachable registry (Docker Hub or
  `registry.fly.io`). Credential delivery via env-var path covers
  API-key users; subscription-auth users need the
  tarball-via-secret path. Adapter plan:
  [`plan-env-fly-machines.md`](plan-env-fly-machines.md).
- **Open question.** Volumes pinned to physical hosts — fine for
  per-task ephemeral machines (no volume), but if a managed-mode
  user wants persistent caches (pip / npm), the volume pinning
  forces region-locked tasks.

### Modal Sandboxes

- **Why interesting.** SDK-first API (Python and JS) with the
  cleanest "create a sandbox from this image with this command and
  these env vars" surface; per-second billing; mature filesystem
  snapshot support (`snapshotFilesystem`) which would let brr cache
  repo state across tasks if that ever becomes worth the
  complexity; experimental "Docker-in-sandbox" mode if brr ever
  wants to host user-supplied dev-container images inside the
  sandbox.
- **Adapter shape.** `prepare` uses `modal.Sandbox.create(app,
  image, command=..., env=..., timeout=...)`. `invoke` uses
  `sandbox.exec(...)`. `finalize` uses `sandbox.terminate()` plus
  push-from-sandbox for the branch.
- **What brr needs to add.** A `ModalEnv` adapter (~400-500 LOC).
  Brings the Modal SDK as a runtime dep when this env is in use —
  acceptable as a plugin (optional dep) but not as a built-in.
  Credential delivery via Modal secrets is the natural path; SDK
  has first-class `Secret.from_dict(...)`.
- **Open question.** Cold start is closer to seconds than to Fly's
  hundreds-of-ms. Probably the right backend for users who already
  use Modal, not the default.

### Daytona (self-hosted and SaaS)

- **Why interesting.** Explicitly purpose-built for "run AI-agent
  code in isolated sandboxes"; ~90ms sandbox creation from
  snapshot; has both a SaaS (app.daytona.io) and a
  Docker-Compose-deployed self-hosted stack, so it fits brr's BYO
  tier *and* brr's self-hosted ideology without forcing a platform
  commitment; full REST + SDK + CLI; per-sandbox network
  allow-lists out of the box.
- **Adapter shape.** Same pattern as Fly / Modal: API call to
  create a sandbox from an image or snapshot, exec the runner via
  `sandbox.process.executeCommand(...)`, push the branch back,
  pull the response file via the FS API, destroy.
- **What brr needs to add.** A `DaytonaEnv` adapter that can
  target either the SaaS endpoint or a self-hosted Daytona
  instance via a configurable base URL. Probably ships with the
  Python SDK as a runtime dep. The runner image either lives in a
  registry Daytona can pull from, or is snapshotted ahead of time
  for the faster cold start.
- **Open question — AGPL.** Daytona is AGPL-3.0; brr as an API
  consumer doesn't trigger AGPL (we're not modifying Daytona's
  source). If managed brr ever extends Daytona itself (e.g.,
  custom runner types upstream), that work would be AGPL-bound.
  Worth a one-line legal check before committing — not a blocker.

### E2B Sandboxes

- **Fit.** Closely matches brr's pattern: Python SDK,
  `Sandbox.create()`, custom templates built from a Debian-based
  `e2b.Dockerfile`, file API for upload / download, command exec.
  The product is explicitly framed around "AI-generated code
  execution" so the security and isolation defaults are sensible.
- **Adapter shape.** Build a brr-specific E2B template once (the
  runner image as an `e2b.Dockerfile`), then
  `Sandbox.create(template_id)` per task. Repo upload via the
  file API or `git clone` in the startup script.
  `sandbox.commands.run(...)` for invoke. Destroy on close.
- **What brr needs to add.** An `E2BEnv` adapter (~300 LOC).
  Template build is a one-off operator step, not per-task. SDK is
  a runtime dep when this env is in use.
- **Open question.** E2B's main muscle is short-lived
  code-interpreter sandboxes (max ~24h default). Brr's per-task
  shape is well within that window. Less clear how it handles
  persistent caches; probably bring-a-clean-template-every-time
  is the right default for v1.

### GitHub Codespaces

- **Fit.** Devcontainer-native, so users with a
  `.devcontainer/devcontainer.json` already in their repo get the
  cloud runner with no extra image. `gh codespace create -r
  owner/repo -b branch` boots a codespace, `gh codespace ssh -c
  <name>` execs commands, `gh codespace cp` moves files. Free tier
  is generous for personal use (120 core-hours/mo); paid tier is
  billed to the GitHub account / org.
- **Adapter shape.** `prepare` runs `gh codespace create ...` with
  the right devcontainer path. `invoke` runs `gh codespace ssh -c
  <name> -- <runner-cmd>`. `finalize` pushes the branch from
  inside the codespace, `gh codespace cp <name>:<path>
  <host-path>` for the response file, `gh codespace delete
  <name>`. Cleanest CLI story of the set.
- **What brr needs to add.** A `CodespacesEnv` adapter — mostly
  subprocess shelling to `gh codespace …`, no SDK dep. Easiest
  adapter to write; arguably the second-most-important after Fly
  because the audience overlap is enormous (every brr user with
  GitHub is a candidate).
- **Open question.** Codespaces are inherently GitHub-coupled.
  For brr's cross-SCM positioning, this is fine as one option
  among several — not a default. Cold start is slower than Fly /
  Daytona (typically tens of seconds, sometimes minutes for
  fresh codespaces).

### Hetzner-style vanilla VMs (cloud-init or SSH bootstrap)

- **Why include it.** Some users will want the managed-mode cost
  ceiling bound by *their cheapest cloud option*, not by a
  managed-runtime platform's per-second pricing. A vanilla VM on
  a budget host (Hetzner Cloud, low-end OVH, even a Raspberry Pi
  reachable over ssh) is the floor.
- **Adapter shape.** Identical to the designed `ssh` env in
  [`design-env-interface.md`](design-env-interface.md): provision
  via cloud-init at first use (or "bring your already-provisioned
  box"), rsync repo to scratch, ssh exec, git bundle back, scp
  response, ssh destroy / rsync clean.
- **What brr needs to add.** Mostly already designed — implement
  the `ssh` env. The "cloud-init bootstrap" variant is a thin
  prepare wrapper around the existing `ssh` shape that calls the
  platform's "create server" API (Hetzner Cloud, vultr /
  digitalocean / etc.) once.
- **Open question.** "Pay-per-task ephemeral VM" on these
  platforms is poorly priced — better used in long-lived-box
  mode (one always-on cheap VM that brr ssh's into). That's the
  operator's BYO-laptop replacement, not really a managed-mode
  fan-out option.

## Part 4 · What we are explicitly NOT building

- **Devin / Cognition / Lovable.** SaaS agents that own the loop.
  Brr's loop is brr's loop.
- **CI-as-runner (GitHub Actions, GitLab CI, CircleCI,
  Buildkite).** CI runners are great for triggered jobs; they're
  poorly shaped for brr's "the agent takes 7 minutes and then
  maybe asks a clarifying question" pattern. The `gh-aw`
  comparison in
  [`research-brr-vs-gh-aw.md`](research-brr-vs-gh-aw.md) covers
  this in depth.
- **Per-cloud platform built-ins.** Fly / Modal / Daytona / E2B /
  Codespaces all ship as **plugins**, not as built-ins. The
  rule-of-thumb still applies: anything that needs an account, a
  CLI install, or an SDK install belongs in a plugin. Brr core
  ships `host`, `worktree`, `docker` and the protocol; the rest
  are opt-in.
- **PaaS platforms with read-only application containers
  (Heroku, Upsun, Render, Railway, App Platform).** Designed for
  always-on web apps with writes limited to declared mount paths;
  no per-task ephemeral sandbox API, no bring-your-own-OCI-image,
  and the read-only `/app` blocks `git worktree`-style operations
  brr's envs do. Wrong shape for the per-task sandbox role.

  These same platforms ARE viable as **daemon-hosting** targets —
  the brr daemon is exactly the always-on-web-app shape they were
  designed for. See
  [`subject-managed-mode.md`](subject-managed-mode.md) → Daemon
  hosting for the deployment-templates path.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic frame this research informs.
2. [`plan-env-fly-machines.md`](plan-env-fly-machines.md) for the
   first concrete adapter plan that cites this page.
3. [`design-env-interface.md`](design-env-interface.md) for the
   env Protocol every adapter implements.

## Lineage

- 2026-05-22 — promoted from
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §2 as
  part of the managed-mode KB shape rollout. Per-platform briefs
  grounded in 2026 vendor docs surveyed during the pondering
  reshape.
- 2026-05-22 — added Caller-axis section formalising that
  adapters are consumed by two callers (laptop daemon, brnrd
  server-side) with small per-caller deltas.
- 2026-05-25 — Caller-axis section refreshed: BYO server-side
  compute (user's stored cloud token used by brnrd) deferred
  from launch per
  [`decision-pricing-shape.md`](decision-pricing-shape.md); only
  the brnrd-owned managed-compute caller wires up at launch
  (Fly Machines only). Pattern A grew a "server-side caller
  specifics" subsection covering the AI-credential vault's two
  payload shapes (api-key + dir-tarball) and how the dispatcher
  injects them per platform. Subscription-auth (Pro / Plus /
  OAuth) explicitly first-class on the server-side path via the
  dir-tarball shape. Third reframe breadcrumb in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
