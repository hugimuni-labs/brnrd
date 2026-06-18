# Design: config layout — three scopes, two files, one account store

**Status: accepted 2026-05-26** (locked in PR #40 MR review,
locking pass IV — added "Per-branch overrides — embraced,
not avoided" section answering "which branch's `brr.toml`
wins?"; clarified that brnrd has no "active branch" concept
at all, and the daemon's "last-spawned branch per project"
is the natural default base when an event doesn't name a
branch; **account binding promoted to machine scope** at
`~/.local/state/brr/account/` so `brnrd connect` from a
second project skips the account-pair step on already-paired
machines). Fluid past the schema specifics — implementation
will surface keys we haven't enumerated; the three-scope
model + precedence rule are the contract.
Defines the three-scope config model that replaces today's
single gitignored `.brr/config`. The model has two on-disk files
(project-scope `brr.toml` committed to the repo; local-scope
`.brr/config` gitignored) plus account-scope state on brnrd
reached through the existing protocol, with a merge precedence
and a per-key scope annotation in the schema. Pre-requisite for brnrd-side spawn
bootstraps reading project preferences (Docker image, runner
choice, etc.) from the cloned repo and for the "what knobs exist?"
discoverability gap that
[`decision-cli-shape.md`](decision-cli-shape.md) → "`brr config
list`" addresses. Companion to
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) (the
account-scope endpoints) and
[`decision-cli-shape.md`](decision-cli-shape.md) (the `brr config`
sub-verbs that operate over this layout).

## Why this exists

Three pressures point at the same gap:

1. **brnrd-side spawn bootstraps need project preferences.** When
   a managed-compute spawn fires for a user, the brnrd backend
   clones the repo and runs the same env class the daemon would
   (per [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
   "Failover dispatch"). The spawn needs to know which Docker
   image to use, which runner (claude / codex / gemini) to launch,
   any project-level env overrides. Today those settings live in
   `.brr/config` which is **gitignored** — brnrd cannot read them
   from the clone because they aren't in the repo.
2. **Teammates can't share project-level prefs.** Today's flat
   `.brr/config` is per-developer per-machine. A team adopting brr
   has to redo setup on every machine; no canonical "this is how
   our project runs brr" surface exists.
3. **The discoverability gap.** Users have no clear overview of
   what config keys exist or which apply where. The README +
   AGENTS.md mention some keys; others live in code. The CLI shape
   adds `brr config list`, but for that to be useful, the
   underlying model needs three things: a schema with per-key docs,
   per-key scope (so "where it's set" means something), and a
   place to write generated templates.

## Decision

**Three scopes**, each with a clear home and lifecycle:

| Scope | Lives in | Committed? | Who sees it | Sync? |
|-------|----------|------------|-------------|-------|
| **`project`** | `brr.toml` at repo root | Yes | Anyone with the repo: teammates, CI, brnrd-side spawns | No active sync — repo is the source of truth; brnrd reads from the cloned repo |
| **`local`** | `.brr/config` (today's file) | No (gitignored) | This machine only | None — overrides `brr.toml` for this checkout |
| **`account`** | brnrd-side store, `GET/PUT /v1/accounts/settings` | N/A (server-side) | All daemons + spawns under this account | Daemon syncs at startup and on user write; brnrd pushes updates to daemons via inbox notifications |

**Precedence at read time** (highest wins):

```
local (.brr/config)  >  project (brr.toml)  >  account (brnrd)  >  defaults
```

Local has highest precedence because per-machine overrides
fundamentally need to win — if your laptop binds the daemon to a
specific port that's free locally, no project- or account-level
preference should override it.

## Per-branch overrides — embraced, not avoided

The project-scope file `brr.toml` is git-tracked, so it
naturally varies per branch. **This is a feature, not a bug.**
Switching branches changes the policy your daemon and any
spawned sandbox apply on that branch's tasks. Useful for:

- A feature branch that overrides `runner.timeout_seconds =
  1200` for a heavy refactor without polluting `main`'s
  config.
- An experiment branch that flips `env.default` from `host`
  to `docker` for a one-week reproducibility test.
- A long-lived release branch that pins `docker.image` to an
  older tag for stability while `main` rolls forward.

Concretely: when the daemon dispatches a task that lands on
branch `X`, the `brr.toml` it reads is **the one in
branch `X`**, not the one in whatever branch happened to be
checked out when the daemon started. When brnrd fails over
to a managed-compute spawn, the spawn clones the repo at
the event's `branch_target` and reads THAT branch's
`brr.toml` — same per-branch shape applies on the cloud side.

**brnrd does not track an "active branch" of its own.** It
has no need to — brnrd's responsibilities (routing events,
dispatching, failover policy) are all per-project, not
per-branch. Events name a branch via `branch_target`; brnrd
forwards that name unchanged. Account-scope settings
(subscription, brnrd URL, account binding) are
branch-independent by construction (they live on brnrd's
side, not in the repo).

### Picking the working branch when an event doesn't name one

Some events name a target branch (GitHub PR events specify
the PR's head branch; the daemon honours that). Many don't
(a Telegram message, a generic GitHub issue comment, a
manual `brr run`). For those, the daemon picks the working
branch using this rule, in order:

1. **`event.branch_target`** if provided.
2. **`daemon.last_spawned_branch[project_id]`** — the most
   recent branch the daemon successfully ran a task on, for
   this project, on this machine. Captures the "work
   continuity" intent: if you've been iterating on
   `feature/foo`, the next event arrives on `feature/foo`
   too, not on `main` out of the blue.
3. **The repo's default branch** (whatever `git symbolic-ref
   refs/remotes/origin/HEAD` returns; typically `main` or
   `master`).

The last-spawned-branch state lives in the daemon's
process memory + a small persisted hint file at
`.brr/state/last_spawned_branch` per project (gitignored,
machine-local). It's NOT account-scope — different machines
working the same project can be on different branches,
and that's correct. Resets to None when the project is
forgotten from the registry.

This rule fits the per-branch-`brr.toml` shape: continuing
work on the same branch means the daemon keeps reading the
same `brr.toml`, so behaviour is stable across consecutive
tasks in a session.

**Schema declares per-key scope.** Each known config key has a
schema entry with `name`, `type`, `default`, `scope`, `doc`,
optional `valid_values`. The schema is the single source of truth
for:

- Which scope a key belongs to (drives `brr config set` writes).
- Docs (drives `brr config doc <key>` and the inline-comment
  template generator).
- Validation (drives `brr config validate`).
- The `brr.toml` template emitted by `brr config template`.

`brr config set <key> <value>` resolves the schema entry, writes
to the right file (or PUTs to brnrd), validates the value
matches the type. If the key isn't in the schema, falls back to
`.brr/config` writes with a warning.

## File format: TOML

Both on-disk files become TOML. Reasons:

- **Convention recognition.** Project-level TOML in repo root is
  the convention developers expect (`pyproject.toml`,
  `Cargo.toml`, `wrangler.toml`). `brr.toml` reads as "oh, that's
  brr's project config" without any docs.
- **Nesting + comments + types.** The current `key=value` format
  only supports flat strings with `True/False/int/str` coercion;
  TOML gives us nested sections (`[runner]`, `[docker]`,
  `[failover]`), real types, and comments that parsers preserve.
- **Stdlib support.** Python 3.11+ ships `tomllib` (read-only).
  Brr already targets Python 3.10+ (per AGENTS.md) but the
  runtime-dependencies decision allows small deps that pay
  for themselves; `tomli` (the 3.10 backport) is one line of
  install and is the most widely-deployed TOML reader in Python.
  Writing goes through `tomli-w` or hand-rolled emit (the template
  generator hand-rolls for comment preservation; ad-hoc writes
  via `brr config set` use `tomli-w`).

Backward-compat: a one-release shim reads old flat-`.brr/config`
files (no users yet to actually migrate, but the shim catches
in-flight developer setups). The shim runs at daemon start; if it
finds a flat file, it converts it to TOML in place and logs a
one-line "migrated".

### Example `brr.toml` (project scope, committed)

```toml
# brr project config — committed to the repo, shared with teammates,
# read by brnrd-side managed-compute spawns. Run `brr config doc <key>`
# for per-key documentation.

[runner]
# Default runner CLI for tasks in this project.
default = "claude"

[docker]
# Container image used by the docker env. Must be pullable from
# wherever the daemon (or brnrd) is running — public images are
# zero-config; private images need a registry credential on the
# spawn side (see brnrd's vault).
image = "myorg/brr-runner:py3.12"

[env]
# Preferred env for this project. Daemons that have the named
# env installed pick it up automatically.
default = "docker"

[kb]
# Maintenance schedule (off | per-task | weekly).
maintenance_schedule = "per-task"
```

### Example `.brr/config` (local scope, gitignored)

```toml
# brr local overrides — per-machine, gitignored. Settings here win
# over brr.toml for this checkout only.

[daemon]
# Local bind for the foreground supervisor.
host = "127.0.0.1"
port = 7474

[git]
# Identity for daemon-authored commits.
author_email = "arseni@example.com"
author_name = "Arseni"

[claude]
# Env var that holds the Anthropic API key (read at runner-spawn
# time, never logged).
api_key_env = "ANTHROPIC_API_KEY"
```

## What goes where: scope assignments

The schema declares scope per key. Headline assignments:

| Key | Scope | Why |
|-----|-------|-----|
| `runner.default` | `project` | Project picked a default runner; teammates + spawns need it. |
| `docker.image` | `project` | The container image the project's tasks run in. |
| `env.default` | `project` | Project's preferred env (`docker` vs `worktree` vs `host` vs a cloud env). |
| `kb.maintenance_schedule` | `project` | Methodology choice for the repo. |
| `sync.fetch_before_run` | `project` | The pre-run sync behaviour is project-level (per AGENTS.md → "When the brr daemon runs you"). |
| `sync.fast_forward_default` | `project` | Same. |
| `daemon.host` | `local` | Per-machine binding. |
| `daemon.port` | `local` | Per-machine binding. |
| `claude.api_key_env` | `local` | Per-machine env-var name. |
| `git.author_email` | `local` | Per-developer identity. |
| `git.author_name` | `local` | Per-developer identity. |
| `daemon_install.name` | `local` | Per-machine systemd / launchd unit name (per [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md)). |
| `runner.preferred` | `account` | User-wide preference: "across all my projects, use codex unless overridden". Pushed to all daemons under the account. |
| `failover.policy` | `account` | Cross-daemon account state; lives on brnrd. |
| `failover.threshold_usd` | `account` | Same. |
| `credentials.*` | `account` | The vault (AI runner + docker-registry + cloud-platform for subscribers); never stored locally. `kind=cloud-platform` writes / reads gate on `subscription.tier == "subscribed"` — see [`design-brnrd-protocol.md`](design-brnrd-protocol.md) § "Credential vault endpoints". |
| `autotopup.*` | `account` | Billing prefs, server-side. |
| `subscription.tier` | `account` (read-only) | Current tier: `subscribed` / `subscribed_past_due` / `free`. Written by brnrd on Stripe webhook events; read by the daemon + brnrd-side dispatcher to apply tier-based caps (project count, event ceiling, audit retention). Clients can read via `brr config get subscription.tier` but cannot write — use `brr brnrd subscribe` / `brr brnrd subscription cancel` instead. |
| `subscription.plan` | `account` (read-only) | `monthly` / `annual` / `none`. Same write rules as `subscription.tier`. |
| `subscription.project_cap` | `account` (read-only, derived) | Effective project cap: `3` (Free), `25` (Subscribed not unlocked), `unlimited` (Subscribed unlocked). Derived from `subscription.tier` + `subscription.project_cap_unlocked`; daemon + dashboard read this for cap display + 409 handling. |
| `subscription.project_cap_unlocked` | `account` (read-only, derived) | Boolean: `true` once `cumulative_purchased_usd_lifetime >= 10`; once true, **permanent** (survives subscription cancel + re-subscribe). Drives the `subscription.project_cap` value when tier is `subscribed`. |
| `cumulative_purchased_usd_lifetime` | `account` (read-only, derived) | Monotonic counter of total Stripe top-ups in USD across the account's lifetime; never decremented on refund. Used by the dashboard to show "$X.XX to go to unlock unlimited projects" when subscribed and not yet unlocked. |

Concrete merge: if `brr.toml` says `runner.default = "claude"`,
the account-scope `runner.preferred` is `"codex"`, and the local
`.brr/config` says `runner.default = "gemini"`, the effective
runner is `"gemini"` (local wins). With no local override, project
beats account, so the runner is `"claude"`. With neither, account
wins, so `"codex"`.

## Brnrd-side bootstrap reads `brr.toml`

The daemon-equivalent bootstrap in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Failover dispatch" step 6 already clones the repo at spawn
time. After clone, it adds:

```
6.1. Read brr.toml from the cloned repo if present
6.2. Fetch account-scope settings from brnrd's own store
6.3. Build the effective config: account < project; local is
     ignored (it's not in the repo, by design — local-scope
     keys are by definition per-machine and don't apply to
     ephemeral sandboxes)
6.4. Construct the RunContext using the effective config
```

Concretely, this lets the user `brr config set docker.image
myorg/codex:py3.12 --scope project`, commit `brr.toml`, and have
brnrd-side spawns immediately use the new image — no protocol
field to push, no daemon round-trip. The repo is the message.

### Private docker image — resolved via the generic credential vault

If the project's `docker.image` points at a private registry,
brnrd needs registry credentials to pull. **Resolved
2026-05-25 (pass-4 follow-up, third wave): supported at launch
via the generalised credential vault** in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md). User
flow:

```
$ brr brnrd creds add docker-registry --registry ghcr.io \
    --username myorg --token <ghcr-pat>
```

At spawn time, brnrd extracts the image's registry host
(`ghcr.io` from `ghcr.io/myorg/foo`), looks up a matching
`docker-registry` credential for the account, and runs
`docker login <host>` before `docker pull`. Public images skip
this step entirely. Same encryption-at-rest, same audit log,
same revoke flow as AI-runner credentials — the vault hosts
both kinds in one store.

The user-visible contract: declare a private image in `brr.toml`
and add the registry credential once. The same `brr.toml`
declaration works for the local daemon (which uses the
machine's existing docker config — `docker login` runs
out-of-band) and for brnrd-side spawns (which use the vault).
The credential is never passed to the spawn sandbox itself;
it lives on the build/host worker for the duration of the
pull and is then cleared from memory (the resulting image is
what the sandbox sees, not the cred).

## Account scope — machine-scoped binding + cached settings

Account scope lives on brnrd's side (one row per account in
the settings table), but the daemon caches the most recent
read at **machine scope** on the laptop / cloud host:

```
~/.local/state/brr/account/
  ├── binding.toml      # brnrd URL, account_id, auth token
  ├── subscription.toml # tier, period_end, project_cap state
  └── settings.toml     # cached account-scope settings
```

Path follows XDG-base-dir convention (respects
`$XDG_STATE_HOME`); on macOS it lives at
`~/Library/Application Support/brr/account/` instead (per
platform convention).

The binding file is **machine-scoped, not per-project**.
That's the load-bearing UX win of the locking-pass-IV
daemon shape: when a user runs `brnrd connect` (or
`brr brnrd connect`) from a second project's directory on
the same machine, the binding is already there and connect
goes straight to project-create + gate-pair. The first
project pays the auth cost; every subsequent project on
the same machine is one tap.

See
[`plan-laptop-daemoning.md`](plan-laptop-daemoning.md) §
"Account binding lives at machine scope" for the user-flow
shape, and
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) §
"The protocol shape, at a glance" for where this file fits
in the daemon's startup.

### Account-scope endpoints (brnrd-side)

New endpoint family on brnrd parallel to the existing
credential vault / failover-policy / subscription endpoints.
Spec belongs in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md);
summary here for completeness:

| Method | Path | Description | Persists |
|--------|------|-------------|----------|
| `GET` | `/v1/accounts/settings` | Read all account-scope settings for this account. | Read-only |
| `PUT` | `/v1/accounts/settings/{key}` | Write one account-scope setting (e.g. `runner.preferred=codex`). | settings row |
| `DELETE` | `/v1/accounts/settings/{key}` | Reset to default. | settings row removed |

The daemon fetches account settings at startup and on a periodic
refresh (every 5 min while connected); brnrd-side spawns fetch at
bootstrap. Push-style invalidation (brnrd notifies daemons of
settings changes via the inbox long-poll) is a v-next refinement.
The local mirror at `~/.local/state/brr/account/settings.toml`
is the read source so the daemon doesn't have to hit brnrd on
every per-task lookup; staleness is bounded by the 5-min
refresh.

## CLI surface

All driven by [`decision-cli-shape.md`](decision-cli-shape.md)'s
`brr config` sub-verbs. Concrete behaviours each verb gets from
this design:

| Verb | Behaviour |
|------|-----------|
| `brr config list` | Print all schema-known keys with: current value, source file (or "default"), scope, doc snippet. `--json` mode for machine consumption. `--scope <project\|local\|account>` to filter. |
| `brr config get <key>` | Print the merged value. `--source` flag adds where it came from. |
| `brr config set <key> <value>` | Look up schema, write to the right file or PUT to brnrd. Validate against schema. Error clearly if the key is unknown (suggests `brr config doc` to discover). |
| `brr config doc <key>` | Print the schema entry: type, default, valid values if enum, scope, full doc string. |
| `brr config template [--scope project] > brr.toml` | Emit a fully-commented template with every key in that scope, defaults, valid-value hints, inline docstrings. The "where I'd start a brr.toml" surface. |
| `brr config validate` | Walk both files + account-scope state, validate every value against the schema. Exit non-zero on errors. Wirable to pre-commit. |

`brr config template` and `brr config validate` are added in this
design as additions to the CLI shape (the decision page calls them
out in the same pass).

## What this loses (and accepts)

| Concern | Status |
|---------|--------|
| Migration cost for existing users | None (no users today; one-release shim catches in-flight developer setups). |
| TOML adds a dep on `tomli` for Py 3.10 | Acceptable per [`decision-runtime-dependencies.md`](decision-runtime-dependencies.md). Goes away when Python 3.11+ is the minimum. |
| Project-scope file is committed and may include opinions teammates disagree with | This is the desired behaviour — teammates *should* agree on the project's runner / image / kb schedule. Disagreements escalate to a PR discussion, which is the right shape. |
| Local-scope file diverges from project over time | Acceptable. Local is intentionally per-machine; `brr config list` shows the merged view so users can see what's overriding what. |
| Account-scope adds a network read on daemon start | Cached; refresh every 5 min; the daemon works fine offline against the last-known account state. |

## Open questions

- **Should `brr config set --scope project` auto-`git add
  brr.toml`?** Probably not — would surprise users. Print a
  hint instead ("modified brr.toml; git add it to share with
  teammates"). Resolve when the CLI implementation slice
  lands.
- **Schema versioning.** When the schema changes (key removed,
  type changed), `brr config validate` should give clear
  guidance. Versioning the schema and shipping migration logic
  is straightforward but out of scope until the schema
  stabilises.
- **Sync push from brnrd → daemons.** Polling at 5 min is fine
  at launch; push-style invalidation (brnrd announces "settings
  changed" via the inbox long-poll) is a v-next refinement.

## Estimate

Single non-trivial slice. Approximate breakdown:

- Schema definition + per-key entries (~50 keys at launch):
  ~200 LOC.
- TOML read/write (`src/brr/config/loader.py`, `writer.py`):
  ~200 LOC.
- Merge precedence + source-tracking:
  ~100 LOC.
- Account-scope client (HTTP) + cache:
  ~150 LOC.
- CLI sub-verbs (`brr config list | get | set | doc | template
  | validate`): ~250 LOC.
- Migration shim for old flat-`.brr/config`:
  ~50 LOC.
- Tests: ~400 LOC.

Total: ~1300 LOC. ~1 week of focused work, sequencable in
parallel with the brnrd backend stub. The brnrd-side
`/v1/accounts/settings` endpoints can land later; the daemon-side
TOML + scope model is independently shippable (account scope
degrades to "empty" when brnrd isn't connected).

## Read next

1. [`decision-cli-shape.md`](decision-cli-shape.md) for the
   `brr config <subcommand>` verb shape this design backs.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for
   the `/v1/accounts/settings` endpoints and the
   daemon-equivalent bootstrap that reads `brr.toml` at spawn
   time.
3. [`subject-managed-mode.md`](subject-managed-mode.md) →
   "Surface B" for the managed-compute path that depends on
   brnrd reading project-scope config from the clone.
4. [`plan-kb-subcommand.md`](plan-kb-subcommand.md) for the
   sibling change in the same pass that adds `brr kb` as a
   top-level verb (this design adds `brr config template /
   validate` sub-verbs in the same CLI reshape).
5. [`decision-runtime-dependencies.md`](decision-runtime-dependencies.md)
   for the policy that allows the `tomli` (Py 3.10 backport)
   and `tomli-w` dependencies this design adds.

## Lineage

- 2026-05-25 — drafted as part of the pass-4 follow-up second
  wave (the user raised: "could be cool to sync the local
  settings file with the remote runs … some config properties
  are daemon-deployment specific, but it makes sense to sync
  them … a nice way of seeing all the possible config
  properties visible"). Replaces the implicit single-file
  `.brr/config` model with an explicit three-scope split. The
  earlier draft of `subject-managed-mode.md` and
  `design-brnrd-protocol.md` assumed brnrd would receive
  project preferences via a push protocol; this design
  inverts: the repo carries them (in `brr.toml`), brnrd
  reads from the clone. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (pass-4 follow-up — second wave).
- 2026-05-25 (pass 4 follow-up — third wave) — two updates:
  1. **"Private docker image — open question" resolved** as
     "Private docker image — resolved via the generic credential
     vault." The credential vault generalisation in
     [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
     means `brr brnrd creds add docker-registry --registry
     ghcr.io --username --token` is a launch surface; the
     spawn bootstrap does `docker login` before `docker pull`
     for private images. Same encryption / audit / revoke as
     AI credentials. Driven by the user's "I would actually
     want the images and also the credential dir mounting
     (stored encrypted as we discussed)" feedback.
  2. **`subscription.tier` and `subscription.plan` added as
     account-scope read-only keys.** Mirrored by brnrd from
     the Stripe subscription state on every relevant webhook;
     the daemon + brnrd-side dispatcher read these to apply
     tier-based caps (project count, event ceiling, audit
     retention). Clients read via `brr config get
     subscription.tier`; writes happen via the dedicated
     `brr brnrd subscribe` / `brr brnrd subscription cancel`
     verbs, not via `brr config set`. Driven by the pricing
     reframe in
     [`decision-pricing-shape.md`](decision-pricing-shape.md)
     (third wave) that introduced the platform subscription
     tier.
  3. **`ai_credentials.*` schema entry renamed to
     `credentials.*`** to match the generalised credential
     vault (the schema entry now covers both AI-runner and
     docker-registry credentials).
- 2026-05-26 (third-wave follow-up) — subscription tier
  string-value names finalised (`subscribed` /
  `subscribed_past_due` / `free`, replacing the third-wave
  draft's `plus` / `plus_past_due` / `free`); plan codes
  finalised as `monthly` / `annual` (replacing `plus_monthly`
  / `plus_annual`). CLI verbs writing the subscription state
  are now `brr brnrd subscribe` (start) and `brr brnrd
  subscription cancel`, replacing the draft's `brr brnrd
  plus upgrade/downgrade`. Driven by the user's naming
  feedback in [`decision-pricing-shape.md`](decision-pricing-shape.md).
- 2026-05-26 (locking pass — credential vault scope clarification).
  **`credentials.*` schema entry extended** to cover a third
  `kind` value: `cloud-platform` (BYO compute, subscriber-only
  at launch with Fly Machines; Modal / Daytona / etc. as
  managed support ships per
  [`decision-pricing-shape.md`](decision-pricing-shape.md)).
  Vault writes + reads on `kind=cloud-platform` gate on
  `subscription.tier == "subscribed"` (403 otherwise) — the
  gate lives in the brnrd-side credential endpoint, not in
  config; this page only notes that the account-scope
  `credentials.*` entry has a subscriber-gated sub-shape.
  No on-disk change to `brr.toml` / `.brr/config` schemas;
  cloud-platform credentials never live locally, same as AI
  + docker-registry credentials. Driven by the BYO-for-
  subscribers framing in
  [`decision-pricing-shape.md`](decision-pricing-shape.md) and
  the credential-vault extension in
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md).
- 2026-05-26 (locking pass II — project cap unlock keys).
  **Three new account-scope read-only keys added** for the
  subscriber project cap unlock policy:
  `subscription.project_cap` (derived: `3` / `25` /
  `unlimited`); `subscription.project_cap_unlocked` (boolean,
  permanent once true); `cumulative_purchased_usd_lifetime`
  (monotonic counter, never decremented on refund). All
  three are derived / mirrored from the brnrd-side ledger
  state per
  [`design-billing.md`](design-billing.md) § "Cumulative
  purchase tracking and the subscriber project cap unlock";
  daemon + dashboard read them to show cap status + the
  "$X.XX to go to unlock unlimited" nudge. Driven by the
  user's "capped at smth high like 25, unlimited as soon as
  they spent smth small but reasonable on credits."
- 2026-05-26 (locking pass IV — per-branch overrides +
  machine-scoped account binding + last-spawned-branch
  default). Three additions:
  1. **New "Per-branch overrides — embraced, not avoided"
     section** answering the user's "which branch's `brr.toml`
     wins?" question. `brr.toml` is git-tracked → per-branch
     by construction; that's a feature. Brnrd has no
     "active branch" concept at all (its responsibilities
     are per-project, not per-branch). When brnrd fails over
     to a managed-compute spawn, the spawn clones the repo
     at the event's `branch_target` and reads THAT branch's
     `brr.toml` — same per-branch shape on the cloud side.
     Use cases enumerated (feature-branch `runner.timeout`,
     experiment-branch `env.default`, release-branch
     `docker.image` pinning).
  2. **New "Picking the working branch when an event
     doesn't name one" subsection** codifies the daemon's
     three-step rule: `event.branch_target` → `daemon.
     last_spawned_branch[project_id]` → repo default. The
     last-spawned-branch state lives in `.brr/state/
     last_spawned_branch` per project (machine-local,
     gitignored), captures the "work continuity" intent so
     consecutive tasks land on the same branch and read the
     same `brr.toml`.
  3. **New "Account scope — machine-scoped binding +
     cached settings" section** codifies the file layout
     at `~/.local/state/brr/account/` (binding.toml,
     subscription.toml, settings.toml). Binding is
     machine-scoped — the load-bearing UX win of the
     locking-pass-IV daemon shape: `brnrd connect` from a
     second project on the same machine skips the
     account-pair step. The local settings.toml is the read
     source for per-task lookups; staleness bounded by the
     5-min brnrd refresh. Driven by the user's "the local
     branch that brr daemon last spawned task at is used as
     a base? ... the work continuity idea hints it should
     be based on the local runs" + "we should pickup at
     least the account binding, subscription status, brnrd
     url" (from the daemon-shape reshape).
