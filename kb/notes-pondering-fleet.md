# Notes: Fleet, Managed Mode & Steering — open pondering

**Status: paused (older strands), active capture for managed mode.**
Companion to [`subject-fleet-overlays.md`](subject-fleet-overlays.md)
and [`deck-brr-fleet-steering.md`](deck-brr-fleet-steering.md). The
overlay and `brnrd` strands remain paused behind the active env axis;
the new dominant pondering strand is **managed mode** (hosted gates +
cloud execution backends), driven by two concrete forces:

- Real user need today: running brr jobs while the laptop is down,
  which the local-daemon-only shape does not cover.
- Adoption / sustainability: shipping the first paid tier at the same
  time as the public release, so early adopters see the OSS / paid
  split as the deal they signed up for rather than as something that
  appeared after they invested.

The page is still capture-only — nothing here is committed. When any
of the strands crystallises into something actionable, promote it
into a design or plan page and link from
[`kb/index.md`](index.md).

Reading order: §1 (managed-mode synthesis), §2 (cloud execution
platform research) carry the new thinking. §3-§5 are the older
overlay / registry / brnrd / supervisor strands, lightly updated to
the current state. §6 is the re-promotion guide.

---

## 1. Managed mode — the two dimensions

> **PROMOTED on 2026-05-22 — see
> [`subject-managed-mode.md`](subject-managed-mode.md) (hub),
> [`design-brr-run-protocol.md`](design-brr-run-protocol.md)
> (locked protocol; renamed from `design-managed-gates.md` when
> spawn-compute joined its scope), and the plan and decision
> pages
> ([`plan-managed-gates-launch.md`](plan-managed-gates-launch.md),
> [`plan-failover-compute.md`](plan-failover-compute.md),
> [`plan-env-fly-machines.md`](plan-env-fly-machines.md),
> [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md),
> [`decision-pricing-shape.md`](decision-pricing-shape.md)).
> Body below retained as provenance — the agreed shape lives in the
> promoted pages.**
>
> **2026-05-22 reframe.** The original two-dimension synthesis
> below treated "where does the daemon live" as an orthogonal
> concern and positioned an always-on-host as the *preferred BYO
> answer to laptop-down dispatch* (see also §4 + §1.3 below). On
> reflection that strayed from the work-continuity pitch — making
> the user operate a third thing for what is mostly a paper
> benefit (30% utilisation at 100% cost). The current shape
> instead frames brr.run itself as the always-on dispatcher:
> laptop online → forward to laptop; laptop offline AND failover
> enabled → brr.run spawns a per-task ephemeral sandbox in the
> user's cloud (BYO) or its own (managed compute), execute the
> task, push the branch home, tear the sandbox down. The
> always-on-host model survives as a niche path for cloud-first
> users only. Surfaces A / B / C (managed gates, BYO failover
> compute, managed compute) all ride the same dispatcher; see
> [`subject-managed-mode.md`](subject-managed-mode.md) for the
> current synthesis.

`brnrd` is not the right framing for "managed brr" — it's an operator
agent (a Cursor-Agents-window-shaped product) that *uses* brrs.
`brnrd` is one product axis; managed-brr is a different one.

Managed brr separates cleanly along **two dimensions**, each of which
can ship independently and both of which preserve the OSS
self-hosted path 1:1:

| Dimension | What it manages | Driver |
|-----------|----------------|--------|
| **A. Gates / IO** | Telegram, Slack, GitHub, … bots and tokens | Removes the per-user-token setup friction; lets a single hosted bot serve many users |
| **B. Cloud execution** | Where the runner actually runs when the user's laptop is down or busy | Lets brr keep working when the operator is offline; opens BYO-cloud and fully-managed compute tiers |

These two dimensions are independent. A user can take managed gates
without managed execution (their daemon stays local, just talks to
brr.run for transport); they can take BYO cloud execution without
managed gates (their tokens stay theirs, but tasks fan out to their
cloud); or both. Bundling them at the product level is a marketing
decision, not an architectural one.

### 1.1. Why this surfaces now

Older notes treated "scale brr" as overlays + `brnrd` + envs.
Overlays answer steering. `brnrd` answers fleet-as-an-object. Envs
answer task execution. Managed mode is orthogonal to all three: it
answers *where the moving pieces of brr itself run when they don't
run on the user's box*. Several pressures push it from "future" to
"plan now":

- **Laptop-down jobs.** Today a brr job needs the operator's box
  online. The natural recovery is "send the work somewhere else when
  the box is down". That capability is the smallest possible managed
  offering — a hosted runner — and it directly serves how the
  operator uses brr personally.
- **Sustainability at release.** OSS projects that introduce paid
  tiers *after* their adopter base is locked in lose adopter goodwill
  even when the OSS path stays intact. Adopters who joined because
  there was no commercial story feel co-opted. Shipping a clearly
  labelled paid tier at launch — with the OSS path unambiguously
  first-class — avoids the bait-and-switch reading.
- **First-mile friction.** The slowest part of brr setup today is
  token provisioning (Telegram bot, Slack app, GitHub PAT, runner
  CLI subscriptions). A managed-gates tier removes that friction in
  one verb, which is a credible paid wedge that does not gate
  capability.

### 1.2. Dimension A — managed gates / IO

The current gate model is per-user: each adopter creates a Telegram
bot via @BotFather, gets a token, runs `brr setup telegram`. Same
shape for Slack apps and GitHub apps / PATs. Setup is the longest
single friction in adoption.

Managed-gates shape:

- brr.run operates **one bot per channel kind**: `@brr_bot` on
  Telegram, a single brr Slack app, a single brr GitHub App.
- Users `/invite @brr_bot` to a channel or install the GitHub App on
  a repo. No tokens to manage.
- The hosted bot writes events to an inbox-as-service on brr.run,
  scoped to the user.
- The user's local daemon long-polls (or websocket-connects) brr.run
  for events and posts responses back the same way.
- The daemon still runs the runner on the user's hardware. Repo
  contents never leave the user's box.

Architectural fit with the current code:

- The existing protocol contract is "anything that writes to
  `.brr/inbox/` is a gate". A `cloud` gate that long-polls a remote
  inbox is just one more gate adapter — the daemon side is unchanged.
  See [`src/brr/gates/README.md`](../src/brr/gates/README.md) for
  the file protocol.
- The "remote gate stub idea" in older notes is exactly this — see
  §3.3 (brnrd's outline) for the original framing; managed mode is
  that idea operationalised as a user-facing product instead of a
  brnrd internal.
- New surface needed on brr's side: one CLI verb (`brr connect`,
  `brr connect brr.run`, or similar) that authenticates the local
  daemon to the hosted inbox and starts the cloud-gate thread. No
  per-bot setup verbs.

What this does *not* change:

- Token-based, fully self-hosted gates stay first-class. The OSS
  path remains `brr setup telegram` with the user's own bot. The
  managed tier is one alternative gate, not a replacement.
- No code execution happens on brr.run in this tier. It is pure
  transport — the security model is "we route messages; we never see
  repos".

### 1.3. Dimension B — cloud execution

Where Dimension A solves "tokens are annoying", Dimension B solves
"my laptop is offline". The model:

- The user runs a brr daemon somewhere (laptop, home server,
  occasional VM). When it's online, tasks run locally.
- When it's offline (or busy, or the user explicitly routes a task
  off-box), the task runs on **a cloud sandbox** the user has
  authorised brr to launch on their behalf.
- The sandbox holds the repo for the task duration, runs the runner,
  pushes the branch back to the user's remote, returns the response
  file, and self-destructs.

Two product tiers nest cleanly here:

- **BYO cloud key.** User adds a Fly token / Modal token / Daytona
  key / SSH host to `.brr/config`. brr launches sandboxes on the
  user's account. brr.run charges nothing per sandbox; it charges
  for the orchestration convenience (the scheduling, the gate
  routing, the receipt of "your task is done" notifications). User
  pays platform directly. **This is the near-term target** because
  it preserves the self-hosted ideology and it's what the user wants
  for the laptop-down case right now.
- **Fully managed compute.** brr.run runs sandboxes on its own infra
  (most likely Fly Machines or similar). User pays brr.run a
  per-task / per-second rate with margin. This is the higher-margin
  tier and the higher-trust ask (we touch their code), so it ships
  second.

Both tiers fit through the *same env-protocol shape* — see §2.1.

The OSS path: the same env-protocol adapter that talks to Fly /
Modal / Daytona / SSH for managed-mode is the same adapter an OSS
user runs locally with their own keys. Who operates what differs;
the code does not:

- **OSS / BYO compute**: user operates the daemon (on a box of their
  choice — see §4), holds the cloud token, the daemon hits the
  platform API directly. brr.run is out of the per-task path; its
  only role for BYO users is the gates side (Dimension A, optional).
- **Fully managed compute**: brr.run operates the compute on its own
  infra. User pays per-task with margin. Adds a brr.run-side
  scheduler that is its own v-next thing.

**Who dispatches when the laptop is down?** Not brr.run, in the BYO
case — the answer is "the daemon doesn't run on the laptop in the
first place". It runs on an always-on host the user controls, which
long-polls brr.run for gates events and dispatches tasks the usual
way. See §4 for the deployment-targets list. brr.run holding the
user's cloud token and spawning sandboxes on their behalf is a
v-next convenience layer, not the primary BYO answer — the
always-on-host model is simpler operationally and keeps brr.run's
scope tight.

### 1.4. Monetisation timing — ship the paid tier at launch

Three constraints push this:

- **Goodwill.** Adopters who joined because there was no commercial
  story will feel rug-pulled when one appears, even when nothing
  about their use changes. Shipping the paid tier visibly at launch
  removes the bait-and-switch reading.
- **Funding the maintenance.** Solo OSS maintenance is a known
  burnout pattern. A small recurring revenue stream from the
  managed-gates tier funds review / support / docs hours without
  needing a foundation or VC angle.
- **Product clarity.** "OSS thing that is also a SaaS" is a 2026
  pattern users recognise (Supabase, Plausible, PostHog). Shipping
  the SaaS surface at launch slots brr into that recognisable
  pattern. Shipping it later forces an audience reset.

What "ship at launch" means concretely, minimum:

- **Free tier.** Local daemon. Self-managed bots. Self-managed
  execution. Everything brr currently does.
- **Paid tier v0 — managed gates only.** Hosted `@brr_bot` on
  Telegram (the easiest one to operate; one bot scales to thousands
  of users). User installs `brr connect brr.run`, the daemon
  long-polls. Charged at maybe $10-30/mo flat — priced as
  convenience, not as compute.
- **Defer to v1.** BYO cloud execution. Fully managed execution.
  Slack / GitHub gate hosting. Per-call pricing models.

The launch-day paid tier doesn't have to be impressive. It has to
exist, be honest about what it costs and what it gives you, and
have a one-line description ("we run the bot so you don't have to
deal with tokens"). That's the goodwill cover.

### 1.5. Where this fits with the existing env protocol

The accepted env protocol — `prepare → invoke → finalize` from
[`design-env-interface.md`](design-env-interface.md) — already
generalises beyond local execution. The `ssh` env that page sketches
is conceptually a cloud runner with SSH as its transport:

- `prepare` provisions a remote scratch dir and copies the repo to
  it.
- `invoke` runs the runner over the transport (`ssh remote 'cd …
  && runner …'`).
- `finalize` retrieves the branch (git bundle + scp + git fetch) and
  the response file (scp), then tears down the scratch dir.

Every cloud platform in §2 below is the same shape with a different
transport:

| Env | Transport for create / upload / exec / download / destroy |
|-----|---------------------------------------------------------|
| `ssh` (designed) | OpenSSH client; rsync; ssh exec; scp |
| `fly-machine` | Fly Machines REST API; image registry; SSH or Fly exec; SSH or volume mount + download |
| `modal` | Modal Python SDK; image build; `sandbox.exec`; `sandbox.open` / `snapshotFilesystem` |
| `daytona` | Daytona REST API or SDK; image / snapshot; `sandbox.process.executeCommand`; Daytona FS API |
| `e2b` | E2B Python SDK; template (Dockerfile-derived); `sandbox.commands.run`; sandbox file API |
| `codespaces` | `gh codespace create`; devcontainer-bound; `gh codespace ssh`; `gh codespace cp` |

So the architectural question is **not** "do we need a new
abstraction?" — the env protocol already covers it. The questions
are about each adapter individually: how much code, how much
operating cost, what's the credential delivery story per platform,
what's the per-task cold-start time, what's the price floor for a
typical brr task (a few minutes of one shared CPU).

### 1.6. What still needs research

Concrete unknowns to resolve before crystallising any of this into a
design page:

- **Per-platform "what brr has to add" delta.** §2 starts the audit
  with the candidates we know matter most.
- **Credential delivery beyond env vars.** Brr's local docker env
  forwards `ANTHROPIC_API_KEY` etc. through `-e` and bind-mounts
  `~/.claude/`, `~/.codex/`, `~/.gemini/` (and `~/.config/gh`,
  `~/.ssh`) — see `_DOCKER_DEFAULT_CRED_PATHS` and
  `_DOCKER_DEFAULT_PASSTHROUGH_ENV` in
  [`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py). Remote
  sandboxes have no bind-mount equivalent — credential dirs have to
  be uploaded somehow (encrypted platform secrets, file upload over
  the platform SDK, or a one-off `git bundle`-style upload). Each
  adapter needs to pick the right vehicle.
- **Repo delivery.** Three patterns, each with trade-offs:
  - `git clone https://${TOKEN}@github.com/<owner>/<repo>` in the
    sandbox `prepare`. Cleanest; assumes the sandbox can reach the
    remote and the operator has provisioned a per-task token.
  - `git bundle create` locally then upload + `git fetch`. Works
    over any transport; expensive for large repos.
  - Platform-native volume / snapshot reuse (Daytona snapshots, Fly
    volumes pinned to a host). Lowest per-task cost; highest
    coupling to the platform.
- **Result delivery.** Branch back: push to the remote from inside
  the sandbox (simplest, assumes the remote is reachable and the
  token has push). Response file back: SDK file-download (Modal,
  E2B, Daytona), `gh codespace cp`, scp, or "stream it back over
  stdout from `invoke`" (the runner's stdout already carries the
  response — `runner.invoke_runner` captures it before any
  finalize step touches files).
- **Cold start budget.** Brr tasks tend to be 1-15 minutes. A
  60-second cold start is acceptable; a 5-minute cold start is not.
  Fly Machines claim ~300ms boot from a warm image; Daytona claims
  ~90ms from snapshot; E2B and Modal land in the seconds range. SSH
  to an always-on box is zero cold start but the highest standing
  cost. The right default for managed-tier-v1 falls out of this.
- **Per-task cost floor at brr usage shape.** For a 1 shared vCPU /
  1 GB RAM box running for 5 minutes (a typical small brr task),
  what's the realistic platform cost? Order-of-magnitude estimates
  before any commitment, not final pricing.
- **Network egress.** Each runner CLI calls home (Anthropic / OpenAI
  / Google) and the sandbox calls the git remote. Most platforms
  default to open egress; some let users restrict it (Daytona's
  network allow-list, Modal's `outbound_cidr_allowlist`). Brr
  probably wants permissive default with an opt-in tightening.
- **AGPL exposure.** Daytona is AGPL-3.0; if brr.run uses Daytona
  as an upstream sandbox backend, AGPL covers only modifications to
  Daytona itself (which we wouldn't need to make — we'd be an API
  client). Worth confirming with a lawyer-or-equivalent before
  committing.

---

## 2. Cloud execution candidates — what brr has to add per platform

> **PROMOTED on 2026-05-22 — see
> [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
> for the durable reference (cross-adapter patterns + per-platform
> briefs), and [`plan-env-fly-machines.md`](plan-env-fly-machines.md)
> for the first concrete adapter plan. Body below retained as
> provenance — the structured form lives in the promoted pages.**

This rewrites the older §10 plugin-candidate list around a single
question: *for each platform, what is the minimum brr has to add to
support it through the existing env protocol?* The audit is
necessarily incomplete until each plugin is actually drafted, but
the per-platform shape is concrete enough today to pick which
adapters to write first.

The list excludes anything that violates brr's positioning: SaaS
agents-as-a-service (Devin, Cognition) that own the loop, anything
that requires brr to hand them the repo plus the prompt and trust
their orchestration. Brr keeps the loop.

### 2.1. The minimum protocol delta — what every cloud-execution adapter needs

Every adapter implements the existing `EnvBackend` Protocol from
[`design-env-interface.md`](design-env-interface.md). The per-phase
work is:

| Phase | What the adapter has to do |
|-------|---------------------------|
| `prepare` | Create a sandbox / VM / workspace on the platform; choose or build the image; upload the repo (clone or bundle); upload credentials (env vars + auth dirs); record the handle in `ctx.env_state` |
| `invoke` | Exec the runner CLI inside the sandbox; stream stdout/stderr back to the host trace; honour the task timeout |
| `finalize` | Push the branch back (from inside the sandbox) or bundle-and-fetch (from the host); pull the response file to `response_path_host`; destroy the sandbox on clean done; preserve it on `status ∈ {error, conflict}` per the salvage rule |

The runner CLI install is **not** brr's problem per task — it ships
inside the image. The bundled image at
[`src/brr/Dockerfile`](../src/brr/Dockerfile) already builds a
practical runner image with claude / codex / gemini installed and
the dev tools brr expects. The same image is the right starting
point for every cloud-execution adapter; per-platform customisation
is choosing where to host it and how to point the platform at it.

The credential delivery layer is the single load-bearing complexity
across all adapters. Local docker uses bind-mounts; remote sandboxes
cannot. The realistic options, ranked from least operationally
demanding to most:

1. **Env vars only.** Forward `ANTHROPIC_API_KEY` /
   `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY` /
   `GITHUB_TOKEN` through the platform's env / secret system.
   Sufficient when the runner CLI supports keyed auth and the
   operator pays via direct API key.
2. **Env vars + platform secret store for credential dirs.** Tar
   `~/.claude/` / `~/.codex/` / `~/.gemini/` into the platform's
   encrypted secret store; expand at sandbox start. Needed when the
   runner CLI uses subscription auth (Claude Pro, Codex Plus, Gemini
   OAuth) — those credentials live in directories the CLI reads from
   `$HOME` and don't reduce to env vars.
3. **One-shot upload at task start.** Use the platform SDK's file
   API (Daytona, E2B, Modal) or `scp` (Fly Machines via SSH,
   Codespaces via `gh codespace cp`) to drop the credential dirs in
   the sandbox before `invoke`. Slower per task; simpler to reason
   about than option 2.

Brr's existing
`_DOCKER_DEFAULT_CRED_PATHS = (".claude", ".claude.json", ".codex",
".gemini", ".gitconfig", ".config/gh", ".ssh")` is the canonical
list; remote adapters should consume the same list rather than
inventing per-platform variants.

### 2.2. Fly Machines

- **Why it's first.** Fastest cold start of the credible options
  (~300ms boot from a warm image); the smallest VM is a few cents
  per hour, so a 5-minute task is under a cent; pure REST API with
  no SDK lock-in; per-second billing of running compute means brr
  pays for what it uses; `auto_destroy: true` matches brr's
  ephemeral-by-construction contract directly.
- **Adapter shape.** `prepare` calls `POST /v1/apps/{app}/machines`
  with `config.image = <our runner image>`, `auto_destroy: true`,
  and an env block containing the credential keys. Repo gets in via
  `git clone https://${TOKEN}@github.com/...` in the image's entry
  command (or as a `cmd` override). `invoke` is `POST .../exec` or
  SSH into the machine via WireGuard. `finalize` pushes the branch
  from inside the machine; the response file is captured from
  `invoke`'s stdout the same way every other env does it.
- **What brr needs to add.** A `FlyMachineEnv` adapter (~300-400
  LOC informed estimate; lots of the surface is already in
  `DockerEnv`'s shape); the bundled runner image needs to be
  published to a Fly-reachable registry (Docker Hub or
  `registry.fly.io`). Credential delivery via env-var path covers
  API-key users; subscription-auth users need the
  tarball-via-secret path.
- **Open question.** Volumes are pinned to physical hosts. For
  per-task ephemeral machines this is fine — no volume — but if a
  managed-mode user wants persistent caches (pip / npm), the volume
  pinning forces region-locked tasks. Not a blocker, just shapes
  the v1 default.

### 2.3. Modal Sandboxes

- **Why interesting.** SDK-first API (Python and JS) with the
  cleanest "create a sandbox from this image with this command and
  these env vars" surface; per-second billing; mature filesystem
  snapshot support (`snapshotFilesystem`) which would let brr cache
  repo state across tasks if that ever becomes worth the complexity;
  experimental "Docker-in-sandbox" mode if brr ever wants to host
  user-supplied dev container images inside the sandbox.
- **Adapter shape.** `prepare` uses `modal.Sandbox.create(app,
  image, command=..., env=..., timeout=...)`. `invoke` uses
  `sandbox.exec(...)`. `finalize` uses `sandbox.terminate()` plus
  push-from-sandbox for the branch. Image is `image =
  Image.from_registry(<our image>)` or built ad-hoc.
- **What brr needs to add.** A `ModalEnv` adapter (~400-500 LOC).
  Brings the Modal SDK as a runtime dep when this env is in use —
  acceptable as a plugin (optional dep) but not as a built-in.
  Credential delivery via Modal secrets is the natural path; the
  SDK has first-class `Secret.from_dict(...)` for this.
- **Open question.** Modal's pricing model rewards always-on
  apps with autoscaling sandboxes. Brr's "one task, then destroy"
  pattern is fine but is not Modal's primary use case; cold start
  is closer to seconds than to Fly's hundreds-of-ms. Probably the
  right backend for users who already use Modal, not the default.

### 2.4. Daytona (self-hosted and SaaS)

- **Why interesting.** Explicitly purpose-built for "run AI-agent
  code in isolated sandboxes"; ~90ms sandbox creation from snapshot;
  has both a SaaS (app.daytona.io) and a Docker-Compose-deployed
  self-hosted stack, so it fits brr's BYO-cloud-key tier *and*
  brr's self-hosted ideology without forcing a platform commitment;
  full REST + SDK + CLI; per-sandbox network allow-lists out of the
  box.
- **Adapter shape.** Same pattern as Fly / Modal: API call to
  create a sandbox from an image or snapshot, exec the runner via
  `sandbox.process.executeCommand(...)`, push the branch back,
  pull the response file via the FS API, destroy.
- **What brr needs to add.** A `DaytonaEnv` adapter that can target
  either the SaaS endpoint or a self-hosted Daytona instance via a
  configurable base URL. Probably ships with the Python SDK as a
  runtime dep. The runner image either lives in a registry Daytona
  can pull from, or is snapshotted ahead of time via Daytona's
  snapshot API for the faster cold start.
- **Open question — AGPL.** Daytona's AGPL-3.0 licence affects
  *modifications to Daytona itself* offered over a network. Brr as
  an API consumer doesn't trigger AGPL (we're not modifying
  Daytona's source). If managed brr ever extends Daytona itself
  (e.g., custom runner types upstream), that work would be AGPL-
  bound. Worth a one-line legal check before committing — not a
  blocker.

### 2.5. E2B Sandboxes

- **Fit.** Closely matches brr's pattern: Python SDK,
  `Sandbox.create()`, custom templates built from a Debian-based
  `e2b.Dockerfile`, file API for upload / download, command exec.
  The product is explicitly framed around "AI-generated code
  execution" so the security and isolation defaults are sensible.
- **Adapter shape.** Build a brr-specific E2B template once (the
  runner image as an `e2b.Dockerfile`), then `Sandbox.create(template_id)`
  per task. Repo upload via the file API or via `git clone` in the
  startup script. `sandbox.commands.run(...)` for invoke.
  Destroy on close.
- **What brr needs to add.** An `E2BEnv` adapter (~300 LOC).
  Template build is a one-off operator step, not per-task. SDK is a
  runtime dep when this env is in use.
- **Open question.** E2B's main muscle is short-lived
  code-interpreter sandboxes (max ~24h default). Brr's per-task
  shape is well within that window. Less clear how it handles
  persistent caches; probably bring-a-clean-template-every-time is
  the right default for v1.

### 2.6. GitHub Codespaces

- **Fit.** Devcontainer-native, so users with a
  `.devcontainer/devcontainer.json` already in their repo get the
  cloud runner with no extra image. `gh codespace create -r
  owner/repo -b branch` boots a codespace, `gh codespace ssh -c
  <name>` execs commands, `gh codespace cp` moves files. Free tier
  is generous for personal use (120 core-hours/mo); paid tier is
  billed to the GitHub account / org.
- **Adapter shape.** `prepare` runs `gh codespace create ...` with
  the right devcontainer path. `invoke` runs `gh codespace ssh -c
  <name> -- <runner-cmd>`. `finalize` pushes the branch from inside
  the codespace, `gh codespace cp <name>:<path> <host-path>` for
  the response file, `gh codespace delete <name>`. Cleanest CLI
  story of the set.
- **What brr needs to add.** A `CodespacesEnv` adapter — mostly
  subprocess shelling to `gh codespace …`, no SDK dep. Easiest
  adapter to write; arguably belongs as a near-term plugin since
  the audience overlap is high (every brr user with GitHub is a
  candidate).
- **Open question.** Codespaces are inherently GitHub-coupled. For
  brr's "cross-SCM" positioning, this is fine as one option among
  several — not a default. Cold start is slower than Fly / Daytona
  (typically tens of seconds, sometimes minutes for fresh
  codespaces).

### 2.7. Hetzner-style vanilla VMs (cloud-init or SSH bootstrap)

- **Why include it.** Some users will want managed-mode cost ceiling
  bound by *their cheapest cloud option*, not by a managed-runtime
  platform's per-second pricing. A vanilla VM on a budget host
  (Hetzner Cloud, low-end OVH, even a Raspberry Pi reachable over
  ssh) is the floor.
- **Adapter shape.** Identical to the designed `ssh` env in
  [`design-env-interface.md`](design-env-interface.md): provision
  via cloud-init at first use (or "bring your already-provisioned
  box"), rsync repo to scratch, ssh exec, git bundle back, scp
  response, ssh destroy / rsync clean.
- **What brr needs to add.** Mostly already designed — implement
  the `ssh` env. The "cloud-init bootstrap" variant is a thin
  prepare wrapper around the existing `ssh` shape that calls the
  platform's "create server" API (Hetzner Cloud API,
  vultr / digitalocean / etc.) once.
- **Open question.** "Pay-per-task ephemeral VM" on these platforms
  is poorly priced (you pay for the hour even when the task takes
  three minutes). Better used in long-lived-box mode: one always-on
  cheap VM that brr ssh's into. That's the operator's BYO-laptop
  replacement, not really a managed-mode offering.

### 2.8. What we are explicitly *not* building

- **Devin / Cognition / Lovable.** SaaS agents that own the loop.
  Brr's loop is brr's loop.
- **CI-as-runner (GitHub Actions, GitLab CI, CircleCI, Buildkite).**
  CI runners are great for triggered jobs; they're poorly shaped for
  brr's "the agent takes 7 minutes and then maybe asks a clarifying
  question" pattern. The `gh-aw` comparison in
  [`research-brr-vs-gh-aw.md`](research-brr-vs-gh-aw.md) covers this
  in depth.
- **Per-cloud platform built-ins.** Fly / Modal / Daytona / E2B /
  Codespaces all ship as **plugins**, not as built-ins. The
  rule-of-thumb from older §10 still applies: anything that needs an
  account, a CLI install, or an SDK install belongs in a plugin.
  Brr core ships `host`, `worktree`, `docker` and the protocol; the
  rest are opt-in.
- **PaaS platforms with read-only application containers (Heroku,
  Upsun, Render, Railway, App Platform).** Designed for always-on
  web apps with writes limited to declared mount paths; no per-task
  ephemeral sandbox API, no bring-your-own-OCI-image, and the
  read-only `/app` blocks `git worktree`-style operations brr's envs
  do. Wrong shape for the per-task sandbox role.

  These same platforms ARE viable as **daemon-hosting** targets —
  the brr daemon is exactly the always-on-web-app shape they were
  designed for, with a writable mount for `.brr/` and repo clones.
  See §4 for the deployment-templates promise; the read-only PaaS
  templates would be one row in that list. Per-task fan-out from a
  daemon hosted there uses the cloud-runner envs above, not the
  local `docker` env (which doesn't work without docker-in-docker).

---

## 3. brnrd — separate product, further postponed than managed mode

The framing has tightened since the older notes: **brnrd is not "the
managed-mode product"**. brnrd is closer to "Cursor's Agents window
for your fleet of brrs" — an operator-as-agent layer that orchestrates
brrs and other agentic surfaces, has its own brain, its own UI, its
own memory. Managed mode is *infrastructure brnrd would use*, not
brnrd itself.

That separation makes both directions easier to reason about:

- **Managed mode** is a property of brr — same code, hosted gates
  and / or hosted execution as opt-in tiers. Ships earlier because
  the OSS shape and the managed shape are 1:1.
- **brnrd** is a separate product with its own brain, kb, UI,
  multi-channel surface. Ships much later, after brr itself has
  proven traction and managed mode has stabilised. brnrd consumes
  brr (and probably the brr.run managed APIs), it does not extend
  brr's own runtime.

What brr should ship now to leave room for brnrd later:

- The same three small things from the older brnrd notes —
  machine-readable repo health, a self-maintaining registry, the
  "remote gate stub" idea — are still cheap groundwork. They were
  framed for brnrd; they happen to be useful for managed mode too
  (the cloud-gate is exactly the remote-gate stub).

What brr should *not* ship for brnrd:

- Cross-repo task coordination, fleet-level scheduling, shared
  memory across repos. All brnrd-side concerns. Brr stays per-repo
  at runtime.

The earlier "fully-managed brnrd is the commercial play" framing
from older §7 is misleading and worth retiring: the commercial play
that ships first is **managed-brr**, not managed-brnrd. brnrd is a
v-next product, on its own clock, possibly its own monetisation.

---

## 4. Cross-platform daemon supervision

The older table (systemd / launchd / Task Scheduler / Docker / tmux)
still stands as the survey. Updates since:

- **Linux is non-negotiable.** It's the operator's daily driver.
  systemd-user is the obvious target; brr would ship a template and
  a `brr install-service` verb that generates it.
- **macOS-first beyond Linux.** The audience for brr (AI-tool
  creator crowd, see
  [`research-positioning-and-runtime-deps-2026-05-21.md`](research-positioning-and-runtime-deps-2026-05-21.md))
  skews Mac. launchd plist generation is the right macOS path.
- **Windows is on the list but later.** Real Windows support needs
  more than a Task Scheduler / NSSM template — the signal-handling
  in [`src/brr/daemon.py`](../src/brr/daemon.py) and the bash gate
  example are the more interesting blockers. Set expectations in
  the README ("Linux and macOS first; Windows planned but not yet
  supported") to avoid the WSL hand-wave reading.
- **Docker as the universal escape hatch.** `docker compose up -d
  brr` survives restarts, works on every platform that runs Docker,
  and uses the runner image brr already ships. Pair it with
  per-platform native paths, not as a replacement.
- **Two-layer daemon hosting (the managed-mode angle).** *2026-05-22
  reframe: the "always-on host as the preferred BYO answer to
  laptop-down" claim below was demoted. The current preferred
  answer is brr.run-as-failover-dispatcher (Surface B in
  [`subject-managed-mode.md`](subject-managed-mode.md)) — the
  always-on host survives as a niche path for cloud-first users
  only. Body below retained as provenance.* In Dimension B the
  daemon's restart-survival problem partially
  disappears: if the daemon itself runs on a small always-on host
  the user controls, the laptop-down case is solved by the box-up
  case. This was originally framed as the preferred answer for
  BYO compute dispatch (§1.3) — simpler than brr.run spawning
  sandboxes on the user's account, and OSS-pure end-to-end. The
  model is two-layer:
  - *Always-on daemon host* = where the brr process lives.
    Long-polls brr.run for gates events; dispatches tasks via
    whatever env is configured.
  - *Per-task sandbox host* = where individual tasks fan out when
    bigger compute / GPU / stronger isolation is needed. Optional;
    uses the cloud-runner envs from §2.

  Deployment targets worth shipping templates for, ranked by setup
  ease:

  | Target | Setup | Trade-offs |
  |--------|-------|-----------|
  | Free-tier always-on cloud apps (Fly app, Render free worker, Railway) | `flyctl launch` from template / one-click | Cheapest; the "deploy brr in 30 seconds" path. Free-tier resource caps. |
  | Read-only PaaS templates (Heroku, Upsun, Render Blueprint, Railway, App Platform — see §2.8) | One-click deploy button | Broadest reach into developer audiences already on these platforms. Daemon runs fine on read-only `/app` if `.brr/` and repo clones live on a writable mount; per-task work must fan out to cloud-runner envs (no `docker` env without docker-in-docker). |
  | Cheap always-on VPS (Hetzner CX11 €3.79/mo, Oracle Free Tier ARM, low-end OVH / DO / Vultr) | `docker compose up -d brr` + systemd unit | Most flexible (full `docker` env support); cheapest at scale. |
  | Laptop / home server | `brr install-service` for macOS + Linux | Existing default; the install-service verb removes the "go add it to your startup scripts" friction. |

  The deployment-templates folder is a small package of
  `deploy/{fly,render,heroku,upsun,vps,docker-compose}/` examples
  that all reference the same `brr/daemon` Docker image. That image
  is a thin split of the existing bundled Dockerfile — the daemon
  variant drops the runner CLIs (claude / codex / gemini) since
  cloud-hosted daemons typically fan tasks out to per-task envs
  anyway, keeping the image small and dependency-light.

Earlier note "stays out of v1" for `brr install-service` is no
longer obviously right — for macOS + Linux it's cheap enough to be
part of the launch shape, and the smooth-startup UX shifts brr from
"indie hack" to "tool I'd recommend".

---

## 5. Self-maintaining registry

Unchanged from older §5:

- `~/.local/state/brr/repos.json` — XDG state dir.
- `brr init` appends `{path, created_at}`.
- `brr down` or future `brr forget` removes its entry.
- An external operator (`brnrd`, or a fleet-aware managed-mode
  service) reads this file; prunes entries whose path no longer
  exists.

Still trivial; still useful; still not urgent. The managed-mode
angle adds one consumer: a hosted brr.run dashboard that pulls the
registry over the daemon's authenticated API to render "your repos"
without scanning anything.

---

## 6. Overlay shape — capture-only

The overlay strands from older §1, §2, §3, §4 collapsed into
[`plan-overlays.md`](plan-overlays.md), which is blocked behind the
overlay-shape research gate (single-file vs multi-file). What
remains here as capture:

- **Where overlays belong.** `~/.config/brr/` (XDG-respecting,
  override via `BRR_CONFIG_HOME`) is fine. Self-hosted-friendly
  because the user's machine *is* the source of truth.
  Git-cloning that dir for remote-edit is the obvious nice-to-have.
- **Machine vs user scope.** The git-clone trick collapses both —
  one repo, N machines, each cloned into `~/.config/brr/`. Use git
  branches per machine if divergence is needed; default = main
  everywhere.
- **Setup ergonomics.** `brr init` already runs without an overlay.
  An eventual `brr overlay init` (with optional `--git=<url>`) is
  the *only* extra knob needed for the spinal-brain user. Without
  it, brr behaves exactly as today.

Promote into a design page if `plan-overlays.md` unblocks.

---

## 7. Re-promotion guide

Priorities have shifted since the older guide. The current order,
highest priority first:

- **Managed mode (Dimension A — managed gates).** Promote into a
  small page family: `subject-managed-mode.md` (hub covering
  Surfaces A / B / C + daemon hosting), `design-brr-run-protocol.md`
  (the wire format spanning gates + failover dispatch + cloud-
  token security; renamed from `design-managed-gates.md` once
  spawn-compute joined its scope), and
  `plan-managed-gates-launch.md` with two slices — GH App adapter
  first (largest BYO-setup pain relief), TG bot adapter as
  fast-follow (same backend, additional webhook endpoint + event
  parser). Backend skeleton is a FastAPI app + postgres; sized in
  the chat thread that produced this guide.
- **Managed mode (Dimension B — BYO cloud execution).** Promote
  `research-cloud-runner-patterns.md` (lifting §2 of this page into
  a durable reference: credential / repo / result-delivery patterns
  plus per-platform deltas) and the first adapter
  `plan-env-fly-machines.md`. **No new design page needed** —
  [`design-env-interface.md`](design-env-interface.md) already
  covers the protocol, cloud adapters are variations of the `ssh`
  env shape. Codespaces adapter as fast-follow (cheapest second
  adapter, biggest audience overlap).
- **Daemon hosting deployment templates (§4).** Promote into
  `plan-daemon-deployment-templates.md`. Mostly content / template
  work — Dockerfile split (daemon vs runner) + the
  `deploy/{fly,render,heroku,upsun,vps,docker-compose}/` examples
  folder + a "deploying brr" docs page. The "deploy brr in 30
  seconds" promise is what cashes out the BYO compute story without
  brr.run having to hold cloud credentials.
- **`brr install-service` for macOS + Linux.** Promote into
  `plan-install-service.md`. Cheap; part of the launch shape per §4.
- **Self-maintaining registry (§5).** Trivial; promote into a
  small plan page when convenient. Useful for managed-mode
  inspection as well as for brnrd.
- **Overlays (§6 → `plan-overlays.md`).** Still blocked behind the
  research gate; nothing new here.
- **brnrd (§3).** Separate product, further postponed. Don't start
  until brr is publicly launched, managed mode has hands-on
  feedback, and overlays have shipped.
- **Cross-platform supervisor (§4) — Windows.** Defer until a
  Windows user complains *and* the daemon model can support it
  honestly.
- **Decentralised merge (older §8).** Shipped; absorbed in
  [`design-env-interface.md`](design-env-interface.md) and
  [`subject-envs.md`](subject-envs.md). No re-promotion needed.

Until any of the active items above promotes, **no code changes
here**. The point of this page is still to keep the side-channel
from getting lost while the active strands ship.

The old §7 (brnrd-as-service productisation note), §8 (decentralised
merge concrete examples), §9 (older re-promotion guide), and §10
(plugin candidates for `brr.envs`) sections were promoted out as
part of the 2026-05-22 reshape: §10 lifted into
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
(with the Daytona dogfood candidate joined by Fly Machines / Modal /
E2B / Codespaces / vanilla VMs); §7-§9 absorbed into the new §7
re-promotion guide above and the
[`subject-managed-mode.md`](subject-managed-mode.md) hub's "Boundary"
section.
