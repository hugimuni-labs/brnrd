# Decision: brr CLI shape

**Status: proposed, not yet accepted on 2026-05-25.** Names the
top-level command shape for brr after the managed-mode reshape
(always-online managed gates, brnrd-managed compute, multi-
project routing, credit wallet). The current CLI grew
incrementally; this page resets it before any user surface
adopts it. Companion to
[`subject-managed-mode.md`](subject-managed-mode.md) (the
surfaces these verbs configure),
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) (the
REST endpoints `brr brnrd` wraps), and
[`design-billing.md`](design-billing.md) (the wallet endpoints
`brr brnrd topup | balance | autotopup` wrap).

## Decision

**Six top-level verbs** organised by noun, with subcommands
under each noun.

```
brr init                       # agentic repo setup (per managed-mode discussion)
brr run [<task-id>]            # one-off task run (debugging / manual dispatch)
brr daemon ...                 # daemon lifecycle (new noun; collapses today's up/down)
  brr daemon up
  brr daemon down
  brr daemon status            # is it running, where, since when, last activity

brr gate ...                   # gate management (new noun; collapses today's auth/bind/setup)
  brr gate <name> setup        # one-shot: auth + bind + verify (today's `brr setup`)
  brr gate <name> auth         # token-only step
  brr gate <name> bind         # bind-only step
  brr gate <name> remove       # tear down a configured gate
  brr gate list                # list configured gates + their bindings

brr brnrd ...                  # hosted-service management (new noun)
  brr brnrd connect [<url>]    # pair this daemon to a brnrd instance (account-level);
                               # default url=https://brnrd.dev
  brr brnrd disconnect         # unpair this daemon from the brnrd account
  brr brnrd status             # paired? online? last sync, daemons under this account
  brr brnrd pair <gate> --project <id>   # bind a managed-gate channel (TG chat,
                                         # GH App install, ...) to a project;
                                         # returns a pairing code or install URL
  brr brnrd creds add|list|remove   # AI credentials in the vault
  brr brnrd policy get|set          # failover policy: mode (ask/auto/never) + caps
  brr brnrd projects list           # projects bound on the brnrd side
  brr brnrd projects bind <gate>    # bind/rebind an existing gate channel to a project
  brr brnrd audit [--since <date>]  # paginated audit log
  brr brnrd topup [<amount>]        # opens Stripe Checkout for a credit top-up
  brr brnrd balance                 # current credit balance (paid + free split)
  brr brnrd autotopup on|off|configure   # opt-in auto-top-up on low balance

brr config ...                 # configuration introspection (new noun)
  brr config list              # ALL parameters: local .brr/config + remote brnrd-side
  brr config get <key>
  brr config set <key> <value>
  brr config doc <key>         # show docs for one parameter
```

Six top-level verbs (`init`, `run`, `daemon`, `gate`, `brnrd`,
`config`) cover everything; the rest is subcommand structure
under those nouns.

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
| — | `brr config <subcommand>` | New noun head for parameter introspection. Did not exist; addresses the long-standing "what config keys exist?" gap. |

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

## Why `brr config list` (across local + remote)

The original CLI never exposed a "what config can I set?"
listing. The new shape adds one:

- `brr config list` enumerates every config parameter:
  - Local: keys in `.brr/config`, defaults, current value,
    where it's set (file path).
  - Remote (when `brr brnrd connect` has happened): keys on the
    brnrd-side configuration (failover policy, autotopup,
    project bindings, etc.), via the brnrd REST API.
- `brr config get <key>` reads the merged value.
- `brr config set <key> <value>` writes to the right place:
  local → file; remote → REST call.
- `brr config doc <key>` shows docs / type / valid range.

Removes the discoverability problem ("what can I configure?")
without forcing users to read the source. The doc-string per key
becomes part of the API contract.

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
- **`brr daemon logs`** as a sibling of `brr daemon status`?
  Probably yes; uses the same noun head naturally. Defer to a
  daemon-observability plan when one exists.
- **JSON output mode** (`--json` flag on every verb)? Useful for
  scripting / dashboarding. Defer to v-next unless a clear ask
  emerges.

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
   the REST endpoints `brr brnrd <subcommand>` wraps.
3. [`design-billing.md`](design-billing.md) for the wallet
   endpoints `brr brnrd topup | balance | autotopup` wrap.

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
