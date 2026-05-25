# Design: config layout — three scopes, two files, one account store

**Status: proposed, not yet accepted on 2026-05-25.** Defines the
three-scope config model that replaces today's single gitignored
`.brr/config`. The model has two on-disk files (project-scope
`brr.toml` committed to the repo; local-scope `.brr/config`
gitignored) plus account-scope state on brnrd reached through the
existing protocol, with a merge precedence and a per-key scope
annotation in the schema. Pre-requisite for brnrd-side spawn
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
| `sync.fetch_before_task` | `project` | The pre-task sync behaviour is project-level (per AGENTS.md → "When the brr daemon runs you"). |
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
| `ai_credentials.*` | `account` | The vault; never stored locally. |
| `autotopup.*` | `account` | Billing prefs, server-side. |

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

### Private docker image — open question

If the project's `docker.image` points at a private registry,
brnrd can't pull it. Two paths:

1. **Generic credential vault** (preferred long-term). Extend
   the existing AI-credential vault from "AI-credential vault"
   to "credential vault" with a `kind` field
   (`ai-anthropic` / `ai-openai` / `docker-registry` / etc.).
   `brr brnrd creds add docker-registry --registry ghcr.io
   --username … --token …` writes; brnrd uses the stored
   creds for `docker login` before pull. Same encryption-at-rest,
   same audit log.
2. **Fail loudly at spawn time** (launch shape). brnrd's pull
   fails; the spawn returns a clear "private image; either
   make it public, self-host brnrd, or wait for the credential
   vault extension." Simpler at launch; addresses ~95% of users
   (public images are the default).

Path 2 at launch; revisit path 1 if registry-cred requests
appear in user feedback. Tracked here as an open question.

## Account-scope endpoints

New endpoint family on brnrd parallel to the existing
ai-credentials / failover-policy endpoints. Spec belongs in
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

- **Should `brr.toml` live at repo root or under `.brr/`?**
  Sketched as repo-root because the convention is strong
  (`pyproject.toml`, `Cargo.toml`, `wrangler.toml` — all
  repo-root). Putting it under `.brr/project.toml` would require
  partially un-gitignoring `.brr/`, which is awkward. Repo-root
  unless a strong reason emerges.
- **Generic credential vault timing.** Defer until users ask;
  most projects use public Docker images, and the failover path
  fails loudly enough that "this isn't supported yet" is a
  reasonable answer at launch. If even one user asks for
  private-registry pulls, the vault extension is a few hours
  of work (the underlying encryption + audit machinery is
  already in place for AI creds).
- **Should `brr config set --scope project` auto-`git add
  brr.toml`?** Probably not — would surprise users. Print a
  hint instead ("modified brr.toml; git add it to share with
  teammates").
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
