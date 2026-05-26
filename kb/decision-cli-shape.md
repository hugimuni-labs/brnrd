# Decision: brr CLI shape

**Status: proposed, not yet accepted on 2026-05-25; reshaped
2026-05-25 (pass-4 follow-up, third wave) — subscription
sub-verb family added for the new subscription billing leg.
Reshaped again 2026-05-26 (third-wave follow-up) — renamed
from `brr brnrd plus [...]` to noun-first `brr brnrd
subscription [...]` (with `brr brnrd subscribe` as a
shortcut for `subscription start`).** Names the top-level
command shape for brr after the managed-mode reshape (always-
online managed gates, brnrd-managed compute, multi-project
routing, platform subscription + credit wallet, three-scope
config). The current CLI grew incrementally; this page resets
it before any user surface adopts it. Companion to
[`subject-managed-mode.md`](subject-managed-mode.md) (the
surfaces these verbs configure),
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) (the
REST endpoints `brr brnrd` wraps),
[`design-billing.md`](design-billing.md) (the subscription
endpoints `brr brnrd subscription` wraps and the wallet
endpoints `brr brnrd topup | balance | autotopup` wrap),
[`design-config-layout.md`](design-config-layout.md) (the
three-scope config model `brr config` operates over),
[`plan-laptop-daemoning.md`](plan-laptop-daemoning.md) (the
`brr daemon install | uninstall` cross-platform unit-writing
work), and [`plan-kb-subcommand.md`](plan-kb-subcommand.md) (the
`brr kb` surface).

## Decision

**Seven top-level verbs** organised by noun, with subcommands
under each noun.

```
brr init                       # agentic repo setup (per managed-mode discussion)
brr run [<task-id>]            # one-off task run (debugging / manual dispatch)
brr daemon ...                 # daemon lifecycle (new noun; collapses today's up/down)
  brr daemon up
  brr daemon down
  brr daemon status            # is it running, where, since when, last activity
  brr daemon install [--name <project>]    # write+register native service unit
                                           # (systemd user / launchd LaunchAgent)
  brr daemon uninstall [--name <project>]  # stop + deregister + remove unit
  brr daemon logs [--follow]   # tail logs (journalctl --user / launchd log path)

brr gate ...                   # gate management (new noun; collapses today's auth/bind/setup)
  brr gate <name> setup        # one-shot: auth + bind + verify (today's `brr setup`)
  brr gate <name> auth         # token-only step
  brr gate <name> bind         # bind-only step
  brr gate <name> remove       # tear down a configured gate
  brr gate list                # list configured gates + their bindings

brr brnrd ...                  # hosted-service management (new noun)
  brr brnrd connect [<url>]    # three-layer smart bootstrap (account-pair →
                               # project-create → gate-pair); default
                               # url=https://brnrd.dev
  brr brnrd disconnect         # unpair this daemon from the brnrd account
  brr brnrd status             # paired? online? last sync, daemons under this account
  brr brnrd pair <gate> --project <id>   # bind a managed-gate channel (TG chat,
                                         # GH App install, ...) to a project;
                                         # returns a pairing code or install URL
  brr brnrd creds add|list|remove   # credentials in the vault (AI runner +
                                    # docker-registry; --kind filters list)
  brr brnrd policy get|set          # failover policy: mode (ask / auto-approve-always /
                                    # auto-approve-under-usd / auto-approve-under-per-day /
                                    # auto-approve-below-monthly-limit / never) + caps
  brr brnrd projects list           # projects bound on the brnrd side
  brr brnrd projects bind <gate>    # bind/rebind an existing gate channel to a project
  brr brnrd audit [--since <date>]  # paginated audit log
  brr brnrd subscription status     # current state: free | subscribed | subscribed_past_due,
                                    # period_end, cancel-at-period-end, last invoices
  brr brnrd subscription start      # opens Stripe Checkout for the subscription
                                    # (monthly $5 OR annual $50); prints checkout URL
                                    # — defaults to monthly; --annual selects yearly
  brr brnrd subscribe               # shortcut for `brr brnrd subscription start`
  brr brnrd subscription cancel     # cancel-at-period-end via the Stripe API; user
                                    # retains subscriber access until period boundary
  brr brnrd subscription resume     # clear cancel-at-period-end on a sub that
                                    # hasn't lapsed yet
  brr brnrd subscription portal     # open the Stripe Customer Portal in browser for
                                    # card update / invoice download / plan switch
  brr brnrd topup [<amount>]        # opens Stripe Checkout for a credit top-up
                                    # (compute overage on top of any tier)
  brr brnrd balance                 # current credit balance (purchased + subscriber_monthly +
                                    # free_signup_bonus + promotional split) + subscription tier
  brr brnrd autotopup on|off|configure   # opt-in auto-top-up on low balance

brr config ...                 # configuration introspection (new noun)
  brr config list              # ALL parameters across scopes: project (brr.toml),
                               # local (.brr/config), account (brnrd-side)
  brr config get <key> [--source]            # merged value; --source adds origin
  brr config set <key> <value> [--scope]     # writes to schema-declared scope
                                             # unless --scope overrides
  brr config doc <key>         # show docs / type / valid range
  brr config template [--scope project] > brr.toml   # emit a fully-commented
                                                     # template for that scope
  brr config validate          # walk all sources, validate against schema;
                               # non-zero exit on errors (pre-commit friendly)

brr kb ...                     # knowledge-base health + introspection (new noun)
  brr kb status                # one-screen health summary (counts, proposed-pending,
                               # log activity, warnings)
  brr kb pages [filters]       # --proposed | --accepted | --superseded |
                               # --abandoned | --untouched-since 30d | --orphaned
  brr kb proposed              # shortcut for `brr kb pages --proposed`
  brr kb log [--since <date>]  # tail of kb/log.md, filterable
  brr kb check                 # graph + status-marker + drift validation;
                               # non-zero exit on errors
  brr kb doc <page>            # per-page summary (status, lineage, links, age)
```

Seven top-level verbs (`init`, `run`, `daemon`, `gate`, `brnrd`,
`config`, `kb`) cover everything; the rest is subcommand structure
under those nouns. Every sub-verb supports `--json` for machine
consumption (default output is human-readable).

The seventh verb (`kb`) bends the earlier "six minimal verbs"
promise by one. Justification: kb is half the project's identity
(the methodology layer); putting it under `config` would mean
typing `brr config kb …` every time, friction the agent audience
hits often. Top-level noun is the right home — see
[`plan-kb-subcommand.md`](plan-kb-subcommand.md) for the full
rationale.

## Differences from today's CLI

| Today | New | Why |
|-------|-----|-----|
| `brr init` | `brr init` | Same verb; becomes agentic per [`subject-managed-mode.md`](subject-managed-mode.md) (planned). |
| `brr run` | `brr run` | Same. |
| `brr up` | `brr daemon up` | Noun-first organises daemon lifecycle under one head; makes room for `brr daemon status`, future `brr daemon logs`. Slightly more typing in exchange for consistency. |
| `brr down` | `brr daemon down` | Same. |
| `brr auth <gate>` | `brr gate <name> auth` | Noun-first; collapses three verbs (auth / bind / setup) into one noun head. |
| `brr bind <gate>` | `brr gate <name> bind` | Same. |
| `brr setup <gate>` | `brr gate <name> setup` | Same. |
| — | `brr gate list` | New: enumerate configured gates. |
| — | `brr gate <name> remove` | New: tear down a gate cleanly. |
| — | `brr brnrd <subcommand>` | New noun head for the managed-service surface. Did not exist; this is the load-bearing new addition for managed mode. |
| — | `brr config <subcommand>` | New noun head for parameter introspection. Did not exist; addresses the long-standing "what config keys exist?" gap. Backed by [`design-config-layout.md`](design-config-layout.md). |
| — | `brr kb <subcommand>` | New noun head for kb health + introspection. Addresses [issue #41](https://github.com/Gurio/brr/issues/41) (kb maintenance for non-brr agents) and the long-standing gap that the kb is half the project's value prop but has no first-class read surface. Backed by [`plan-kb-subcommand.md`](plan-kb-subcommand.md). |
| — | `brr daemon install \| uninstall` | New sub-verbs: write/register a per-user systemd unit (Linux) or LaunchAgent (macOS) so the daemon survives reboot without `tmux` rituals. Backed by [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md); tracked at [issue #29](https://github.com/Gurio/brr/issues/29). |
| — | `brr brnrd subscription <subcommand>` + `brr brnrd subscribe` shortcut | New: subscription management (`status \| start \| cancel \| resume \| portal`). Backs the subscription billing leg in [`design-billing.md`](design-billing.md) and the pricing reshape in [`decision-pricing-shape.md`](decision-pricing-shape.md). Did not exist; added when subscription billing replaced the credits-only model. Initially sketched as `brr brnrd plus [upgrade \| downgrade \| ...]`; renamed to drop the "Plus" branding and adopt noun-first taxonomy. |

`brr daemon up` is a one-key-more typing tradeoff for organising
lifecycle under a noun; the noun pays for itself once
`brr daemon status` and (future) `brr daemon logs` exist as
sibling subcommands. Same applies to `brr gate`.

## Why `brr brnrd` (vs alternatives)

Picked `brr brnrd` as the namespace for hosted-service
management. Alternatives considered:

| Verb | Pro | Con |
|------|-----|-----|
| **`brr brnrd` (chosen)** | Namespace = product name; self-documenting; future-proof; no naming-by-committee; future named services (if any) get their own noun naturally | Product-name lock-in — if brnrd is ever renamed, the verb churns (mitigated via aliases) |
| `brr remote` | Generic; no lock-in | Reads as "remote daemon," not "managed service"; doesn't signal what's at the other end |
| `brr service` | Generic; system-y | Collides with "systemd service" mental model; unclear what service |
| `brr cloud` | Short | Promises more than we deliver (gates aren't "cloud"; this verb is broader than just compute) |
| `brr config-remote` | Descriptive | Two-word ugly; mixes verb + adjective; doesn't extend to other actions (balance? topup?) |

The lock-in risk is the only real cost, and it's bounded —
introducing an alias is one PR if brnrd ever gets renamed. The
upside (a self-documenting verb that immediately tells the user
what they're configuring) is worth it.

## Why `brr config list` (across project + local + account)

The original CLI never exposed a "what config can I set?"
listing. The new shape adds one, driven by the three-scope
config model from
[`design-config-layout.md`](design-config-layout.md):

- `brr config list` enumerates every config parameter:
  - **Project scope** (`brr.toml`, committed to the repo;
    teammates + brnrd-side spawns see it): docker image, runner
    default, env default, kb maintenance schedule, etc.
  - **Local scope** (`.brr/config`, gitignored; this machine
    only): daemon host/port, per-developer git identity, local
    API-key env-var names, daemon-install name.
  - **Account scope** (brnrd-side, fetched via
    `GET /v1/accounts/settings`; visible to all the user's
    daemons + spawns): user-wide runner preference, failover
    policy, autotopup config.
  Each row shows the merged value, the source it came from,
  the scope, and the doc snippet.
- `brr config get <key>` reads the merged value (precedence:
  local > project > account > default). `--source` shows
  origin.
- `brr config set <key> <value>` looks up the schema, writes to
  the right place automatically. `--scope` overrides if the
  schema scope is wrong for the situation.
- `brr config doc <key>` shows docs / type / valid range.
- `brr config template [--scope project] > brr.toml` writes a
  fully-commented template with every key in that scope,
  defaults, valid-value hints, inline docstrings. The "where I'd
  start a brr.toml" surface — answer to the "what knobs exist?"
  question for people who prefer reading an example file.
- `brr config validate` walks all three sources, validates every
  value against the schema. Pre-commit hook friendly (non-zero
  exit on errors).

Removes the discoverability problem ("what can I configure?")
without forcing users to read the source. The schema becomes the
single source of truth — `brr config doc`, `brr config list`,
the template generator, and `brr config validate` all read from
it.

## `brr brnrd connect` — three-layer smart bootstrap

`brr brnrd connect` is **not** just account-pairing. It's the
one-command per-repo bootstrap for managed mode, walking three
layers (account-pair → project-create → gate-pair), each step
skippable if already done, each step prompting before acting.
Each layer is also a separate verb (`brr brnrd pair <gate>`,
`brr brnrd projects bind`, etc.) — the walkthrough is just the
same code paths sequenced behind one entry point.

### Detection rules (mechanical, no LLM)

- **GitHub App** offered when `git remote get-url origin`
  matches a GH URL (`github.com:org/repo` or
  `https://github.com/org/repo`).
- **Telegram** offered if `.brr/config` has existing TG
  settings (migration path: re-bind the existing chat to the
  managed bot); otherwise prompted in low-emphasis form
  (`[y/N]`, default skip).
- **GitLab / Slack / Discord** join the prompt list when those
  gates ship; same detection-then-prompt pattern.
- Anything not matched → not prompted. No nagging.

### First-time, repo-aware (the common path)

```
$ brr brnrd connect           # run from inside a brr-init'd repo

> No brnrd account paired on this machine yet.
> Opening browser to https://brnrd.dev/pair?code=ABC123
> (waiting for you to sign in / sign up + approve)
> ✓ Paired as account: arseni@hugimuni.fr (machine: <hostname>)

> This repo isn't yet a brnrd project.
> Detected git remote: github.com/Gurio/brr
> Create project "brr" on brnrd? [Y/n] Y
> ✓ Project "brr" created.

> Set up managed gates for this project?
>   • GitHub App (detected github.com/Gurio/brr) — install? [Y/n] Y
>     Opening: https://github.com/apps/brnrd/installations/new?state=...
>     (waiting for installation webhook…)
>     ✓ Installed on Gurio/brr; auto-bound to project "brr".
>   • Telegram — pair a chat with @brr_bot? [y/N] N
>     (skip; run `brr brnrd pair telegram --project brr` later)

> ✓ Done. Try commenting `@brr <task>` on a PR or issue.
```

### Subsequent repo from the same machine

```
$ brr brnrd connect           # different repo, same machine
> Already paired as: arseni@hugimuni.fr (since 2026-05-15)
> Detected git remote: github.com/Gurio/other-repo
> Create project "other-repo" on brnrd? [Y/n] Y
> ✓ Project created.
> GitHub App already installed for Gurio org — auto-bind? [Y/n] Y
> ✓ Bound to project "other-repo".
> Telegram — pair a chat with @brr_bot? [y/N] …
```

### Self-hosted brnrd

```
$ brr brnrd connect https://my-brnrd.example.com
# everything else identical — same three-layer walkthrough
```

### Flags for scripted / non-interactive use

```
brr brnrd connect [<url>]               # bare = interactive walkthrough
  --account-only                        # pair account, skip project + gates
  --project <name>                      # override default (repo basename)
  --no-auto-pair                        # skip the gate-pair phase entirely
  --pair github,telegram                # pre-select gates non-interactively
  --yes / -y                            # answer Y to all prompts (still detection-gated)
```

### Why this layering

- Single command does the right thing in the most common case
  (you're inside a brr-init'd repo with a GH remote, you want
  it on brnrd; one command + a few `[Y/n]` taps).
- Each layer is independently scripted via its own verb, so
  power users / CI scripts keep fine-grained control.
- Detection is mechanical — fast, predictable, debuggable; no
  LLM call gates the setup flow.
- Self-hosting stays first-class: the URL is a positional arg
  with a default, the walkthrough is identical against any
  brnrd instance.
- The agentic-init philosophy from `brr init` (interactive
  walkthrough with smart defaults) gets a smaller sibling here,
  scoped to the managed-mode surface.

### Self-hosting policy

No flag-gating, no environment-variable indirection — just the
positional URL arg. Self-hosting is a first-class path; the
trust pitch ("we don't have your code; here, run your own
brnrd") needs the path to be real. The friction of running
your own brnrd is deployment itself (cloud account,
Stripe-equivalent for self-hosted, etc.); the CLI shouldn't
add hoops on top of that.

## Subcommand-discovery aids

- `brr <noun>` (no subcommand) prints subcommand help, not an
  error.
- `brr <noun> -h` prints expanded help with examples.
- `brr -h` prints the top-level six verbs only, not every
  subcommand. Discoverability is shallow at the top, deep
  per-noun. Avoids the wall-of-text problem.

## Non-verbs intentionally not added

- `brr accounts` — was in an early draft of
  [`plan-failover-compute.md`](plan-failover-compute.md);
  retired in favour of `brr brnrd creds | policy | balance`.
  "Accounts" was the wrong noun (the user has one brnrd account
  per daemon; account-scoped operations live under `brr brnrd`).
- `brr login` / `brr logout` — collapsed into
  `brr brnrd connect | disconnect`. The pairing flow is the
  login.
- `brr update` / `brr upgrade` — handled by the package manager
  (pip / uv / uvx). Not brr's responsibility.
- `brr help <topic>` — covered by `brr <noun> -h` and the docs
  bundle. No separate help tree.
- `brr <gate>-<verb>` (e.g. `brr telegram-setup`) — defeated by
  the gate-noun collapse; subcommand structure is cleaner.

## Migration

No users to migrate. Today's CLI is a forward-only break:
`brr up` → `brr daemon up`, `brr setup telegram` → `brr gate
telegram setup`, etc. Bundled docs (`src/brr/docs/`) and the
README get rewritten against the new shape in the same PR that
lands the implementation.

## Open questions

- **Alias support** for the verb-collapsed forms? E.g.
  `brr up` → alias of `brr daemon up`. Probably not at launch
  (no users to keep happy; aliases add surface to document and
  test). Revisit if the noun-first form turns out to be
  annoying in real use.
- **Shell completions** (bash / zsh / fish) at launch? Probably
  yes; the noun-first shape benefits a lot from completions
  ("brr gate <TAB>" → list of gate names you have configured).
  Small slice; goes in the CLI implementation plan.

## Implementation slice (when this gets built)

Not part of this decision page, but for context: implementation
is a single non-trivial slice that rewrites
`src/brr/cli/__init__.py` to use a noun-subcommand structure
(argparse subparsers are sufficient; no Typer / Click rewrite
needed). Bundled docs in `src/brr/docs/` get a full pass.
Estimate: ~2-3 days for the CLI itself + docs; integration with
`brr brnrd` subcommands waits for the brnrd backend stub.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   user flows these verbs configure.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for
   the REST endpoints `brr brnrd <subcommand>` wraps (and the
   `/v1/accounts/settings` endpoints the `brr config` account
   scope uses).
3. [`design-billing.md`](design-billing.md) for the wallet
   endpoints `brr brnrd topup | balance | autotopup` wrap.
4. [`design-config-layout.md`](design-config-layout.md) for the
   three-scope config model and per-key schema that `brr
   config` operates over.
5. [`plan-kb-subcommand.md`](plan-kb-subcommand.md) for the
   `brr kb` sub-verb design and the agent/user split it serves.
6. [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md) for
   the `brr daemon install | uninstall` cross-platform
   unit-writing work.

## Lineage

- 2026-05-25 — drafted as part of the managed-mode reshape
  pass 4, after the user flagged that the existing CLI grew
  incrementally and didn't have a place for the managed-service
  surface, and that `brr accounts` (the placeholder verb in
  earlier drafts) wasn't the right shape. User confirmed
  `brr brnrd` over `brr remote` / `brr service` / etc. in the
  same exchange. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (fourth 2026-05-25 reframe breadcrumb).
- 2026-05-25 (pass 4 follow-up) — `brr brnrd connect` shape
  formalised as a **three-layer smart bootstrap** (account-pair
  → project-create → gate-pair, each skippable if already done,
  each prompting before acting), after the user pushed back on
  pure account-only connect ("we should autosetup gates when
  `brr brnrd connect`"). Mechanical detection rules added (GH
  via `git remote get-url`, TG via existing `.brr/config`
  settings). Non-interactive flags (`--account-only`,
  `--no-auto-pair`, `--pair`, `--yes`, `--project`) defined for
  scripted use. Walkthrough does not invent verbs — each layer
  is the same code path as the standalone `brr brnrd pair` /
  `brr brnrd projects bind`, just sequenced.
- 2026-05-25 (pass 4 follow-up — second wave) — three additions
  in one pass after the user raised "we need daemons natively
  installable for mac and linux," "we need a kb command for
  non-brr agents (related to #41)," and "we need a way to see
  all config knobs across local/remote, sync project prefs to
  brnrd":
  1. **Seventh top-level verb `brr kb`** added (status / pages
     / proposed / log / check / doc, with `--json` mode on
     each). Backed by [`plan-kb-subcommand.md`](plan-kb-subcommand.md).
  2. **`brr daemon install | uninstall | logs`** sub-verbs
     added. Backed by
     [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md);
     tracked at [issue #29](https://github.com/Gurio/brr/issues/29).
  3. **`brr config template | validate`** sub-verbs added, and
     the `brr config list` description rewritten around the
     three-scope model (project / local / account). Backed by
     [`design-config-layout.md`](design-config-layout.md).
  Closed the open question on `--json` (now default-on across
  the verb tree, not deferred) and on `brr daemon logs` (now
  part of the install/uninstall slice since the same plan owns
  the OS-service integration).
- 2026-05-25 (pass 4 follow-up — third wave) — added a
  subscription sub-verb family under `brr brnrd` to wrap the
  new subscription endpoints in
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) and
  the subscription billing leg in
  [`design-billing.md`](design-billing.md). Driven by the
  pricing reframe in
  [`decision-pricing-shape.md`](decision-pricing-shape.md) —
  the credits-only model proved self-defeating; subscription
  for the platform + metered credits for compute is the new
  shape, and the CLI needs verbs to manage the subscription
  side. Initial sketch named the family `brr brnrd plus
  [status | upgrade | downgrade | resume | portal]`. `brr
  brnrd creds add` description updated to clarify it now
  accepts `docker-registry` as a credential kind alongside
  the existing AI-runner kinds, per the credential vault
  generalisation (private docker images supported at launch
  via the same encrypted vault as AI creds). No new top-level
  verb — the seventh-verb count (`init`, `run`, `daemon`,
  `gate`, `brnrd`, `config`, `kb`) is unchanged; the new
  family is a sub-verb under the existing `brnrd` noun head.
- 2026-05-26 (third-wave follow-up) — **subscription sub-verb
  family renamed**. `brr brnrd plus [status | upgrade |
  downgrade | resume | portal]` → noun-first `brr brnrd
  subscription [status | start | cancel | resume | portal]`
  + `brr brnrd subscribe` shortcut for the `subscription
  start` case (the most common first-time interaction). Verb
  changes within the family: `upgrade` → `start` (it's not
  really an "upgrade" — there's just one tier), `downgrade`
  → `cancel` (cancel-at-period-end is what it actually does;
  "downgrade" implied a multi-tier ladder that doesn't
  exist). Help-text changes: subscription price corrected
  from $9/$90 → $5/$50, and the balance breakdown sub-bucket
  renamed from `plus_monthly` → `subscriber_monthly`.
  Driven by the user's "I don't like Plus as a name or
  verb" feedback alongside the price + project-cap
  refinements documented in
  [`decision-pricing-shape.md`](decision-pricing-shape.md).
