# Design: home scopes and knowledge placement

Status: active (2026-07-01 design round)

This design reopens one part of the
[account-centered daemon decision](decision-account-centered-daemon.md): the
account router is still the right shape for managed / multi-repo use, but it
should not silently become the default mental model for the simple
"one repo, one local daemon, one Telegram bot" OSS install. It also reopens the
[kb shape](decision-kb-shape.md) default now that the resident's durable memory
lives in an account-scoped store instead of inside the project repo.

## Why this exists

brr started as a repo-local tool:

- `brr init` wrote `AGENTS.md`, `kb/`, and `.brr/` into the repo;
- `brr setup telegram` configured a bot token in that repo's `.brr/`;
- `brr up` meant "run the daemon for this repo."

That shape made the single-repo self-deploy case native. A user could have five
repos and five Telegram bots, with no routing layer and no cloud account.

Recent work moved the durable resident home and run-control state into an
account-centered model:

- `account.resolve_context()` auto-creates a local account repo under the
  `brnrd` XDG state namespace, with `account/repos.json`, dispatch queues,
  run-state docs, repo-tagged dominion directories, plans, runner policy, and a
  ledger.
- `_start_account_gates()` starts configured gates for the account store and for
  every registered repo.
- `brr setup telegram` still configures the repo-local Telegram gate, so the
  old self-deploy route still works mechanically.
- `adopt._run_setup()` still requires `AGENTS.md`, `kb/index.md`, and
  `kb/log.md`; the README still says `brr init` creates a repo-committed KB.

The result is a working but unchosen hybrid. Single-repo Telegram survives as a
compatibility path, while the storage and wording imply every user has a
single "account" home. If a self-deploy user starts five repo-local daemons
without configuring account identity, the default `accounts/default` home can
make unrelated repos feel like one shared account by accident.

## Recommended direction

Use one substrate, but expose two native onboarding lanes:

| Lane | User story | Gate shape | Storage scope | Routing |
| --- | --- | --- | --- | --- |
| **Project-local** | "I want a bot for this repo and a local CLI agent. No account/router." | repo-local gate config, usually one Telegram bot token per repo | a **project home** derived from this repo | no repo selection; every message belongs to this repo |
| **Account router** | "I want one identity / bot / service spanning several repos." | home/account gate config, usually one Telegram bot for the account | an **account home** tied to a user/forge/service identity | chat/topic has an active repo; forge events carry repo identity |

This should not become two engines. Both lanes use the same file protocol,
run model, worktree execution, outbox, run-state docs, resident dominion,
plans, runner policy, and local-first git store. The difference is only which
**home** the daemon selects and where the channel binding lives.

## Name the store: brnrd home

"Account dominion repo" now carries too much old scaffolding:

- **account** is wrong for project-local users who explicitly do not want an
  account concept;
- **dominion** should remain the resident-owned memory directory inside the
  store, not the name for every durable object;
- **repo** is a useful implementation fact but a poor user-facing noun.

Use **brnrd home** for the local-first storage container. It is a git-backed
directory by default, with an optional remote. It can be a project home or an
account home:

```text
$XDG_STATE_HOME/brnrd/
  projects/<repo-slug-or-path-hash>/home/
    home.toml
    repos/<repo-slug>/dominion/
    run-state/<repo-slug>/
    plans/<repo-slug>/
    knowledge/...

  accounts/<account-id>/home/
    home.toml
    account/repos.json
    dispatch/inbox/
    dispatch/responses/
    repos/<repo-slug>/dominion/
    run-state/<repo-slug>/
    plans/<repo-slug>/
    runner-policy/...
    ledger/...
    knowledge/...
```

The exact paths can evolve, but the product distinction matters: "home" is the
storage primitive; "account" is one way to select a home.

## Project-local lane

The project-local lane should be the default for a fresh OSS install that runs
inside a repo and has not connected to brnrd or configured a multi-repo account.

Desired UX:

```bash
brr init
brr setup telegram
brr up
```

Properties:

- `brr setup telegram` keeps writing repo-local gate state.
- `brr up` selects a project home derived from the repo label plus a path hash
  when no account/home binding exists. Five repos get five homes, not one
  accidental `default` account.
- The current checkout is the only managed repo unless the user deliberately
  adds more.
- Telegram messages from that bot never ask for `/repo`; the bot is already the
  route.
- Repo-local `.brr/inbox` and `.brr/responses` remain valid for scripts and
  simple gates. The project home carries durable resident/run/control state,
  not every transient file.

This restores the old "repo daemon" affordance without reviving a separate
daemon architecture.

## Account-router lane

The account-router lane is still the right model for brnrd service users and
local users who want one bot across repos.

Desired UX:

```bash
brnrd connect          # or brr account connect, final CLI name pending
brnrd repo add .
brnrd setup telegram   # configures the home/account gate
brnrd up
```

Properties:

- Telegram binds to the home/account first, then to an active repo per chat or
  topic.
- `/repos`, `/repo <label>`, and dashboard route selection are first-class. A
  free-form message with no active repo should ask for a repo instead of
  quietly relying on a stale default.
- GitHub events remain naturally repo-addressed; they do not need the Telegram
  dispatcher.
- The default repo still exists as a fallback for local scripts and explicit
  operator choice, but it should not be the invisible answer to an ambiguous
  remote chat.

This is the shape already described by
[brnrd channel routing](design-brnrd-channel-routing.md), but it should be
framed as the account-router lane, not as the universal install model.

## Knowledge placement

Do not move every KB fact out of the repo by reflex. Split by audience:

| Layer | Default home | Why |
| --- | --- | --- |
| `AGENTS.md` | repo | Repo-specific conventions must travel with the code and be visible to any agent host. |
| Human docs | repo (`README.md`, `docs/`, maybe `kb/`) | Stable collaborator-facing knowledge belongs with the source. |
| Resident working memory | brnrd home (`repos/<slug>/dominion/`) | Owned, private, noisy, and already local-first. |
| Run/control state | brnrd home | Account/project runtime state should not pollute the source tree. |
| Cross-repo knowledge | brnrd home (`knowledge/_account/` or equivalent) | It cannot honestly belong to one repo. |
| Repo-specific agent wiki | configurable by adoption path | Some repos want a committed `kb/`; others have enough docs or want local-only memory. |

The current mandatory `kb/` default should soften:

- **Playbook-only / portable wiki path**: `brr init --with-repo-kb` or an
  equivalent setup choice creates `kb/index.md` and `kb/log.md` in the repo.
  This preserves the original "AGENTS.md + kb works with any AI tool" value.
- **Full-tool default path**: `brr init` can rely on the brnrd home for resident
  knowledge and leave the repo clean unless a `kb/` already exists.
- **Existing repo KB** remains supported and should be injected when present.
  brr's own repo can keep its committed KB while we dogfood the split.

The bridge is promotion, not duplication: resident notes and home knowledge get
promoted into repo docs / repo KB only when they become stable, shared project
knowledge. This keeps the source repo from becoming an agent scratchpad while
preserving the portability path for users who want it.

## What to cut or rename

If this direction is accepted, cut these pre-release leftovers rather than
building around them:

- The universal default `accounts/default/dominion` for every repo-local daemon.
  Default home selection should be project-local unless the user has connected
  or configured an account home.
- User-facing "account dominion repo" wording. Use "brnrd home"; reserve
  "dominion" for resident-owned memory inside it.
- Mandatory repo `kb/` creation in setup. Keep it as a portable wiki option, not
  as the unavoidable full-tool default.
- Silent Telegram default-repo routing in the account-router lane. Ask for an
  active repo when the chat/topic has none.
- Treating repo-local gates and account/home gates as one blurry thing. The
  location of the gate config is the routing contract.

## Standing portal candidates

The wake context should eventually surface these as live state rather than make
the resident reconstruct them from prose and code:

- **Home scope portal**: project vs account, home id, home path/remote status,
  selected repo, registered repos, and whether this wake is using legacy
  account/default state.
- **Channel route portal**: current gate/thread, active repo for that chat/topic,
  available repo choices, and whether the default repo was used explicitly or
  by fallback.
- **Knowledge source portal**: repo KB present/absent, home knowledge present,
  injected pages/summaries, byte budget, graph health, and last-updated facts.
  This would turn today's large, expensive KB orientation into a bounded live
  surface.
- **Migration warning portal**: legacy `.brr/dominion`, shared default account
  home, or multiple daemons sharing one home unexpectedly.

These are better as standing portals because they are live routing and
orientation facts. A paragraph in a prompt cannot stay true once a user adds a
repo, binds a Telegram topic, or moves a home remote.

## Migration sequence

1. Introduce a `home` abstraction in code and docs as an alias over the current
   account context. Keep tests green by mapping the current account store onto a
   home store first.
2. Change default home selection: no account binding means project home; account
   binding means account home. Include a migration note for existing
   `accounts/default` installs.
3. Split setup wording and commands around gate location: repo-local Telegram
   for project lane; home/account Telegram for account-router lane.
4. Add route-state projection for Telegram: chat/topic active repo, repo list,
   and "ask when none" behavior.
5. Generalize prompt knowledge loading from "repo `kb/`" to a knowledge-source
   chain: home knowledge, repo KB when present, repo docs references, then
   deterministic health findings for whichever source is active.
6. Only after the source chain exists, soften `brr init` so repo `kb/` is
   optional for new adopters.

## Open forks

- CLI naming: whether account-router commands live under `brnrd ...`,
  `brr account ...`, or `brr home ...`. The design only needs the distinction
  between repo-local setup and home/account setup.
- Repo identity for project homes: prefer forge remote slug when present; fall
  back to a path hash so two local repos named `api` do not collide.
- How much home knowledge should be visible to ad-hoc non-brr agent sessions.
  A repo-committed `AGENTS.md` can point at `brr agent inject`, but an absolute
  home path should not be committed into portable project files.
- Whether brr's own repo should eventually move most of its KB to home
  knowledge, or keep the committed KB as a deliberate dogfood artifact for the
  portable wiki path while improving its maintenance.
