# Design: home scopes and knowledge placement

Status: active (2026-07-01 design round; rounds 2 and 3 folded in below —
round 3 governs where it sharpens round 2). Execution: see
[`plan-home-scopes-execution.md`](plan-home-scopes-execution.md) — the
phase-by-phase, no-backwards-compat implementation plan, with maintainer
migration steps and a both-lanes validation checklist.

## 2026-07-01 round 3 — full brr retirement, issues-as-channel, KB-as-checkout, lean GH gate (evt-mo3g)

The maintainer read round 2 ("I like the shape, go ahead") and added four
sharpenings. Each folds in here; where round 3 differs from rounds 1–2, round 3
governs.

### Retire `brr` completely — including the agent-facing surface

Round 2 elected option (a) (one command, `brnrd`). The maintainer's nuance is
that the plan still left a residue: *"brr is gonna be left as an agent-facing
compatibility layer … safer to get rid of it completely, especially since the
bind/add unification partly makes it obsolete."*

He is right, and the residue is real. Even after (a) drops the `brr` command and
alias, `brr` still lives in the **agent-facing prose** — the resident/runner
prompts (`run.md`, `daemon-substrate.md`, `identity-core.md`, `AGENTS.md`) speak
"brr's daemon", "brr hosts you", "the brr runner". That prose *is* an
agent-facing compatibility layer: the product renamed, but the way the daemon
addresses its own resident did not. Round 3 closes it — `brnrd` everywhere the
resident and runner are addressed, not only in the user-facing CLI.

Why bind/add makes this safe (the maintainer's own point): `brr` earned its keep
as the name for "the repo-based local daemon." The [bind/add event-source
model](#event-source-is-the-primary-axis-bind-vs-add) dissolves exactly that
concept — the local-first path is now `brnrd bind <repo> <gate>`, served by the
same transport-agnostic loop as `brnrd add`. There is no longer a distinct
"brr thing" for the name to point at. The concept that justified the short verb
is gone, so the verb goes with it.

The **one** deliberate remnant stays scoped and named: the on-disk `.brr/`
runtime dir, whose rename is a state migration deferrable on its own schedule
(round 2's reconciliation, unchanged). That is a directory name in local state,
not an agent-facing surface — keeping it does not re-create a "brr compatibility
layer" in how the daemon talks to the resident. So: retire the command, the
alias, and the agent-facing prose now; let `.brr/` → `.brnrd/` land whenever a
state-migration wake is cheap. This is a prose-and-prompt migration pass, and
should be scheduled as its own wake (the prompts are the bulk of the surface).

### Issues are a first-class co-maintainer channel (perception *and* action)

Round 2 named cross-repo KB and repo docs as knowledge layers. The maintainer
adds the one a human co-maintainer actually lives in: **forge issues** (and PR
threads). *"A perceived resident co-maintainer has to take initiative and use the
same-as-human information channels."*

This is not just another knowledge store — it reframes the resident's stance.
Issues are **both**:

- **Perception**: open issues, their discussion, labels, and cross-references are
  live project state the resident should read as orientation, the same way a
  human maintainer skims the tracker before deciding what to do. This belongs in
  the knowledge-source chain alongside home KB and repo docs.
- **Action with initiative**: a co-maintainer does not only *answer* an issue it
  was mentioned on — it *opens* issues for problems it noticed, comments to move
  threads forward, triages, and links related work. This is the ownership stance
  from the identity core, expressed on the forge.

Concretely this extends the gate model below: the `gh` CLI (the action channel)
already lets the resident read and write issues; what round 3 adds is the
**intent** — issues are a standing surface the resident is expected to perceive
and act on proactively, not a request queue it waits on. A future "issue radar"
portal (open issues assigned to / mentioning the resident, plus staleness) is the
natural standing-portal form of this; logged under portal candidates below.

### KB access is a *checkout*, not a copy or a read-only mount

Round 2's access ladder was inject → mount (read-only, containers) → query. The
maintainer proposes a cleaner unifying shape for the reachable-tree rung: *"maybe
not copy-to-repo or mount-to-container but **checkout** into a repo? … The KB
should be tracked and versioned, and the runner should be able to commit there —
otherwise it is not a live KB, it is a library no one maintains. It is just maybe
not part of each project repo."*

This is a genuine improvement and round 3 adopts it. The flaw in "mount for
reachability" is that a mount is **read-only reference** — the runner can read the
KB but cannot tend it, so it rots into exactly the unmaintained library the whole
design is trying to avoid. A **checkout** is read-write and versioned: the home
knowledge is its own git repo (the brnrd home already *is* git-backed), checked
out where the runner can reach it, and **the runner commits its edits back**.
That is what makes it a *live* KB — maintenance is just commits, the same receipt
model the dominion already uses.

Reconciliation with round 2's "never copy into the tree" — it still holds, and
checkout is how it holds:

- The KB is a **separate versioned repo**, not files copied into the project
  repo. "Not part of each project repo" is the maintainer's own framing and is
  exactly right: one KB repo (or the home's knowledge tree as a repo), checked
  out beside or under the worktree at a **gitignored** path, never committed into
  the project's own history.
- **Perception (injection) stays the primary path** — the runner still *sees* the
  relevant KB woven into the wake without spending a turn. The checkout is the
  reachable-and-writable substrate for the long tail and for edits, replacing the
  read-only mount rung, not the injection rung.
- Commits to the KB checkout push to the KB repo's remote (when configured),
  exactly as dominion commits do. That is what "tracked and versioned, runner can
  commit" buys: the KB accrues history and survives across wakes and hosts.

So the revised ladder: **inject** (perception, primary) → **checkout** (a
versioned KB repo the runner reads *and commits to*, gitignored beside the
worktree, host or container alike) → **query** (`brnrd kb …` for the tail). The
read-only mount is retired in favour of the checkout, which subsumes it and adds
write.

### Keep the GitHub gate lean, and fix the self-reaction loop at the identity layer

The maintainer sharpened the GH-gate section with four asks: **read all
comments**, keep **filtering optional**, **don't react to self-comments**, and
fix that today's comments are **posted on behalf of the user (him)** when a gate
is bound to a repo the old way.

The unifying resolution — and it ties directly to the "thin, uniform gate"
principle round 2 already set: the gate should ingest **all** comments (no
built-in author/keyword allow-list required to function), with filtering as an
**optional** narrowing, not a precondition. That keeps the gate a pure,
uniform event transport.

But "read everything" collides head-on with the self-reaction loop the maintainer
flags: if the resident's own comments re-enter as events, an unfiltered gate
loops. His two sub-points are the fork, and (a) is the clean answer:

- **(a) Post as `brnrd-bot`, not on the user's behalf.** This is precisely what
  [`design-brnrd-github-bot-user.md`](design-brnrd-github-bot-user.md) already
  specs — a distinct `brnrd-bot` machine identity. With it, self-filtering is
  *trivial and reliable*: drop events authored by `brnrd-bot`. The loop is broken
  by identity, not by heuristics. Round 3 makes this a **dependency**, not just a
  UX nicety: the lean "read all comments" gate *requires* a distinct bot identity
  to be safe. Today's "comments posted as the user" is the thing to retire —
  it conflates human and resident authorship and makes the loop unfilterable.
- **(b) Heuristic self-filtering without a distinct identity is unreliable** — the
  maintainer's own worry, and correct. When resident and human share one author,
  no author check can separate them; you fall back to fragile content matching.
  So (b) is not a real alternative to (a); it is what you are stuck with *until*
  (a) ships. Ship (a).

Net: gate reads all comments; filtering optional; self-authored events dropped by
`brnrd-bot` authorship; migrate off "post on behalf of the user." The GH gate
still does not grow — the fix lives in the **identity** the gate posts under, not
in gate logic. This also makes the "issues as a co-maintainer channel" point
above safe: a resident that proactively opens and comments on issues *must* post
under its own identity, or every initiative it takes becomes a self-event.

## 2026-07-01 round 2 — event-source axis, one command, docs split (evt-cayp)

The maintainer read the round-1 recommendation and sharpened it. Four moves,
each folded in here; the round-1 sections below stay valid as user stories but
this section governs where they differ.

### Event source is the primary axis (bind vs add)

Round 1 organised everything around *home scope* (project vs account). The
maintainer's framing is sharper: the primary axis is **where events come from**,
and home scope is a *consequence* of it. Two verbs, one daemon loop:

- **`brnrd bind <repo> <gate>`** — the gate delivers events **directly** to the
  local daemon. Local-first; works with **no brnrd service connected**. This is
  today's repo-local Telegram/GitHub path.
- **`brnrd add <repo>`** (after `brnrd connect <url>`) — the brnrd **service**
  routes events to the daemon. Managed, multi-repo under one account, requires
  `connect`.

The load-bearing insight the maintainer names: *both* produce the same event
envelope into the same single-flight loop, so the daemon stays
**transport-agnostic** — it does not care whether an event arrived direct from a
gate or routed from the service. That is the "generic arch handling both cases
smoothly" he sees, and it holds. Adopt `bind` / `add` as the user-facing verbs;
"project home" vs "account home" then falls out of which verb was used, rather
than being a separate concept the user must learn.

This does not create two engines (round 1's constraint still holds). It renames
the distinction from *scope of storage* to *source of events*, which is the
thing the user actually chooses at setup time.

### One command: `brnrd` (elects rename option (a))

The maintainer: *"we are deprecating the brr command … we only gonna leave the
brnrd command."* This **flips the evt-qhk6 (2026-06-29) resolution**, which had
resolved the [rename sub-fork](decision-brnrd-rename.md) to option **(b)** (keep
`brr` as a short local verb). This is option **(a)** — one name everywhere — and
it is the maintainer's call to make, so take it as made. `decision-cli-shape.md`
and `decision-brnrd-rename.md` both need a superseding note: the seven-verb
taxonomy survives intact, it just hangs under a single `brnrd` binary with no
`brr` sibling and no `brr` alias.

One reconciliation to protect the cheap-migration property that option (b)
existed for: **separate the command name from the on-disk runtime dir.** Retire
the `brr` *command* now (single CLI surface = `brnrd`); let the `.brr/` runtime
directory rename to `.brnrd/` on its own schedule (or never). The command
surface and the state-dir name are independent — collapsing to one command does
not force a flag-day on every install's `.brr/` state, which was the only real
cost (b) was buying down.

### Knowledge: public docs site + private home KB

Round 1 softened *repo* `kb/`. Round 2 goes further on brr's **own** KB: split
it by audience.

- **Public docs → a deployed documentation website** (Hermes-agents style),
  sourced from the doc-worthy pages: how to install, the CLI surface, the
  bind/add model, the resident model, gate setup. These are the pages an adopter
  needs.
- **Private home KB → brnrd home**: pricing, billing internals, economics,
  revenue plans, unshipped design forks. Not *hidden* — just irrelevant to an
  adopter and not something to ship on the marketing docs site.

This **replaces the planned KB gardening pass** — the audience split *is* the
gardening. (Concrete first cut of what goes public vs private is a follow-up
pass over `kb/index.md`, not this design.)

**Pushback on the access mechanism.** The maintainer proposed
"mount/link/**copy** the whole brnrd home to the repo directory" so the runner
(shell spawned in the repo) can reach the KB. Copy-into-repo is the one part to
reject: it goes stale the moment home changes, and it risks the home being
committed or leaking absolute paths — round 1's own open fork already flagged
"an absolute home path should not be committed into portable project files."
Three clean channels instead, in priority order:

1. **Injection (perception).** The daemon weaves the *relevant* home knowledge
   into the wake, exactly as dominion/`self-inject` land today. This is the
   strong path — the agent sees it without spending a turn to go read it
   (perception=injection, per `portal-reshape-synthesis`).
2. **Mount for reachability (containers only).** When the runner shell runs in a
   container whose cwd is the repo worktree, bind-mount the home read-path at a
   known **gitignored** location. Host runs already reach the absolute home path
   (the dominion is injected by absolute path today), so no copy is needed there.
3. **Query on demand.** A `brnrd kb …` surface answers lookups the injection
   didn't carry, so the agent pulls the long tail instead of grepping a mounted
   tree.

Inject what's relevant, mount to make reachable, query for the tail — never copy
into the tree.

### GitHub gate: transport vs action channel (the connecting shape)

The maintainer flagged the GH paragraph as "mostly unrelated" but asked if it
connects. It does. A gate has **two** roles that Telegram conflates and GitHub
forces apart:

- **Event transport** — inbound events + outbound replies, carried by the
  `connect`-configured token / app.
- **Action channel** — how the agent actually *acts* on the forge: the installed
  `gh` CLI it uses to push, comment, and open PRs.

That is the perception/action split landing at the gate boundary: gate inbound
is **perception transport**; agent emission is **action through whatever richer
tool exists**. Generalised: keep every gate **thin and uniform** — pure event
I/O, identical whether reached by `bind` or `add` — and let action richness live
in the agent's *tools*, not in the gate. The GH gate does not need to grow; the
`gh` CLI already is the action channel, and the gate should stay a transport.

---
## Round 1 (2026-07-01 design round)

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
