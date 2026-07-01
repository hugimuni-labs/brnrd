# Plan: home-scopes execution (bind/add, one command, KB checkout)

Status: active (2026-07-01) — execution plan for
[`design-home-scopes-and-knowledge.md`](design-home-scopes-and-knowledge.md)
(rounds 1–3, all folded). This page is the *how*; the design is the *why*.

## Framing: no backwards compatibility

The maintainer's steer (evt, 2026-07-01): *"we need not to honour backwards
compatibility yet."* This is the load-bearing simplifier. The design's round-1
[migration sequence](design-home-scopes-and-knowledge.md#migration-sequence)
was written to keep every existing `accounts/default` install and every green
test working while the shape changed — an alias layer (`home` mapped onto the
current account context), a "keep tests green first" step, and a slow softening
of `brr init`. **We drop all of that.** Pre-release, one dogfood install (the
maintainer's), no external users to protect → cut straight to the target shape
and migrate the one install by hand.

That turns a 6-step compat-preserving sequence into a small set of **direct
edits a mid-tier runner can execute one phase at a time**. Each phase below is
independently shippable, names the exact files/functions, and ends with a
concrete verify step. Order matters only where "depends on" is stated.

## Target shape (the "simpler model") in one screen

- **One command: `brnrd`.** No `brr` binary, no `brr` alias. Seven-verb taxonomy
  unchanged, hung under `brnrd`.
- **Two onboarding verbs, one loop:**
  - `brnrd bind <repo> <gate>` — gate delivers events **directly** to the local
    daemon. No service. This is the project-local lane.
  - `brnrd add <repo>` (after `brnrd connect <url>`) — the brnrd **service**
    routes events. This is the account lane.
  - Both emit the same event envelope into the same single-flight loop; the
    daemon stays transport-agnostic.
- **`home` replaces `account` as the storage primitive.** `bind` → a **project
  home**; `add` → an **account home**. No universal `accounts/default` fallback.
- **Knowledge ladder:** inject (perception, primary) → **checkout** (a versioned
  KB repo the runner reads *and commits to*, gitignored beside the worktree) →
  `brnrd kb …` query for the tail. No copy-into-repo, no read-only mount.
- **Lean GH gate:** ingest all comments; filtering optional; self-events dropped
  by `brnrd-bot` authorship (depends on the bot identity).

## Phases

### Phase 1 — `home` selection: kill the `accounts/default` fallback

**Goal.** No binding → project home derived from the repo; explicit account
binding → account home. Never a silent shared `accounts/default`.

**Files.** `src/brr/account.py` (`resolve_context`, `_default_account_root`,
`DEFAULT_ACCOUNT_ID`, `AccountContext`), callers in `daemon.py`, `run.py`,
`run_context.py`; `tests/` that assert the old default.

**Steps.**
1. Rename the concept in code to `home`. Minimum viable: rename `account.py` →
   `home.py`, `AccountContext` → `HomeContext`, `resolve_context` keeps its name
   but gains a `kind: "project" | "account"` field on the returned context.
   (Grep `resolve_context`, `AccountContext`, `dominion_repo`, `account_id` for
   the call sites — they are the blast radius.)
2. Change default selection in `resolve_context`: when no account id is
   configured and no `brnrd connect` binding exists, compute a **project home**
   root = `$XDG_STATE_HOME/brnrd/projects/<repo-slug>-<path-hash>/home`. Reuse
   `repo_label()` for the slug and hash the absolute repo path for the suffix
   (open fork in the design: two local repos named `api` must not collide —
   path-hash resolves it). Only when an account id / connect binding is present
   do you take the `accounts/<id>/home` root.
3. Delete `DEFAULT_ACCOUNT_ID` as a *fallback*. It may remain as the literal
   account-id when the user explicitly runs the account lane, but it must no
   longer be the implicit home for an unbound repo.
4. Update the `BRNRD_ACCOUNT_DOMINION` / `BRR_ACCOUNT_DOMINION` env overrides →
   a single `BRNRD_HOME` override; drop the `BRR_` alias (no back-compat).

**Verify.** `pytest -k "account or home or resolve"`. Add/adjust a test: a
resolve from a fresh temp git repo with no config lands under
`projects/<slug>-<hash>/`, **not** `accounts/default/`. Two temp repos with the
same basename resolve to different homes.

### Phase 2 — verbs: `brnrd bind` / `brnrd add`, retire the `brr` binary

**Goal.** Single CLI surface `brnrd`; `bind` (local) and `add` (account) as the
two onboarding verbs. Depends on Phase 1 (`add` needs account-home selection).

**Files.** `src/brr/cli.py` (parser table lines ~19–212; `cmd_bind`,
`cmd_brnrd_connect`, add `cmd_add`), `pyproject.toml`
(`[project.scripts]`), `src/brr/__main__.py`.

**Steps.**
1. `pyproject.toml` → `[project.scripts]`: keep only `brnrd = "brr.cli:main"`.
   Remove the `brr` entry point. (Package dir rename `src/brr/` → `src/brnrd/`
   is a *separate, riskier* import-rename chunk — see Phase 6; do **not** fold
   it here. The command name and the package dir are independent.)
2. Reshape the current `bind` verb: today it's "bind repo to a gate channel or
   watch" (`cmd_bind`). Make it the documented project-local onboarding verb:
   `brnrd bind <repo> <gate>` writes the repo-local gate binding and selects a
   **project home**. Confirm it does not require `connect`.
3. Add `brnrd add <repo>`: registers a repo under the connected account home
   (requires a prior `brnrd connect`). Reuse the existing account-registry write
   path (`_write_registry` in `account.py`) instead of a new store.
4. Keep `brnrd connect <url>` (already `cmd_brnrd_connect`); promote it out of
   the `brnrd brnrd …` sub-namespace if that nesting still exists, so the verb
   is `brnrd connect`, not `brnrd brnrd connect`.

**Verify.** `brnrd --help` lists `bind`, `add`, `connect`, `up` and no `brr`.
`pip install -e .` then `which brr` is empty; `which brnrd` resolves.
`brnrd bind . telegram` in a scratch repo produces a project-home binding with
no account created.

### Phase 3 — knowledge source chain + KB checkout

**Goal.** Generalize prompt knowledge loading from "repo `kb/`" to a chain, and
make the reachable/writable rung a **checkout** the runner commits to.

**Files.** `src/brr/prompts.py` (knowledge/kb injection), `src/brr/kb_health.py`
/ `src/brr/kb_preflight.py` (source selection), the wake-assembly path in
`run_context.py`.

**Steps.**
1. Injection stays primary and unchanged in *mechanism* — just widen the
   *source*: home knowledge (`<home>/knowledge/`) + repo `kb/` when present +
   repo docs references. Order: home knowledge → repo KB → repo docs. Emit the
   same woven block the wake already carries.
2. Add the **checkout** rung: when the runner needs the writable long tail,
   check out the home's knowledge repo at a **gitignored** path beside the
   worktree (e.g. `<worktree>/.brnrd-kb/`, added to the worktree's
   `.git/info/exclude` so it never enters project history). The runner reads and
   **commits** there; commits push to the KB repo remote when configured, same
   receipt model as the dominion.
3. Retire any read-only-mount code path if one was stubbed — the checkout
   subsumes it (design round 3). Add `brnrd kb <query>` as the query rung
   (thin wrapper over the knowledge grep already used at preflight).

**Verify.** Wake assembly on a repo with no `kb/` still injects home knowledge
(no crash on missing repo KB). A test repo with both sources injects home-first.
`brnrd kb "<term>"` returns matching pages. The `.brnrd-kb/` checkout never
shows in `git status` of the project repo.

### Phase 4 — lean GH gate + `brnrd-bot` self-filter

**Goal.** Gate ingests all comments; filtering optional; self-authored events
dropped. **Depends on** the `brnrd-bot` identity
([`design-brnrd-github-bot-user.md`](design-brnrd-github-bot-user.md)) — do not
ship the "read all comments" widening until the bot identity exists, or the
gate self-loops.

**Files.** `src/brr/gates/` (GitHub gate), gate protocol per
`src/brr/gates/README.md`.

**Steps.**
1. Remove any built-in author/keyword allow-list that the gate *requires* to
   function; make filtering an optional narrowing config, default off (ingest
   all).
2. Drop inbound events whose author is `brnrd-bot`. This is the loop-breaker and
   the reason it depends on posting under a distinct identity, not on the user's
   behalf.
3. Migrate outbound: comments/PRs post as `brnrd-bot`, not on the maintainer's
   behalf. (Infra: the bot user/app must be claimed first — maintainer task,
   see migration below.)

**Verify.** A GitHub comment by the maintainer produces one event; the
resident's reply (posted as `brnrd-bot`) produces **zero** re-entrant events.

### Phase 5 — agent-facing prose retirement (`brr` → `brnrd`)

**Goal.** `brnrd` everywhere the resident/runner is addressed. **Its own wake**
(prompts are the bulk of the surface); low code risk, high line count.

**Files.** `src/brr/prompts/run.md`, `daemon-substrate.md`, `identity-core.md`,
`src/brr/AGENTS.md` (+ root symlink), README, `kb/` prose that addresses the
agent as "brr".

**Steps.** Careful pass (not blind sed — "brr" appears in paths like `.brr/`,
`brr/<run-id>` branch names, and code identifiers that must **not** change).
Change only agent-facing *prose* ("brr's daemon" → "brnrd's daemon"). Leave the
on-disk `.brr/` runtime dir alone (Phase 7).

**Verify.** `grep -rn '\bbrr\b' src/brr/prompts/` returns only intended
path/identifier hits; every prose mention reads `brnrd`.

### Phase 6 — package dir rename `src/brr/` → `src/brnrd/` (optional, risky)

**Goal.** Align the package with the brand. Import-breaking; do it as its own
well-tested chunk or defer. Not required for the two-lane behavior to work.

**Verify.** Full `pytest` green after the rename + import fixups.

### Phase 7 — `.brr/` → `.brnrd/` state-dir rename (deferred)

The **one deliberate remnant.** A local-state migration, not agent-facing.
Schedule its own wake whenever cheap; no user is protected by keeping it.

## Migration / switch instructions (maintainer, no back-compat)

Your live dogfood install currently runs on `accounts/default`. To switch it to
the new shape once Phases 1–3 land:

1. **Stop the daemon.** `brnrd down` (or `brnrd daemon down`).
2. **Decide the lane for this repo.**
   - You dogfood one repo with a Telegram bot and local-first → **bind lane**.
     Your existing `accounts/default` home becomes redundant.
3. **Reuse your existing dominion instead of losing it.** Point the new project
   home at your current dominion so history carries over. Either:
   - set `BRNRD_HOME` to your existing home root before `brnrd up`, or
   - move `$XDG_STATE_HOME/brnrd/accounts/default/` →
     `$XDG_STATE_HOME/brnrd/projects/<repo-slug>-<hash>/home/` (the resolver
     will print the expected project-home path on first run — copy it there).
   The dominion is a git repo; a plain directory move preserves its history.
4. **Rebind the gate.** `brnrd bind . telegram` (re-uses your existing
   Telegram token config in `.brr/`). Confirm no account is created.
5. **Restart.** `brnrd up`. Watch the first wake's home-scope line resolve to a
   *project* home, not `accounts/default`.
6. **Account lane, if/when you want multi-repo:** `brnrd connect <service-url>`
   then `brnrd add <repo>` per repo. This is additive — it does not disturb the
   bind-lane repo.
7. **Infra (parallel, for Phase 4):** claim GitHub `brnrd-bot` + the app so the
   GH gate can post under it. Until then, keep GH filtering on.

Uninstall the old `brr` command after Phase 2: `pip install -e .` replaces the
entry points; if a stale `brr` shim lingers on PATH from an earlier editable
install, `pip uninstall brr` then reinstall as `brnrd`.

## Validation — both lanes work smoothly

Run both end-to-end after Phases 1–3. The point is to prove the daemon is
**transport-agnostic**: same loop, same envelope, two sources.

**Bind lane (project-local, no service):**
1. Fresh scratch git repo. `brnrd bind . telegram`, `brnrd up`.
2. Home resolves under `projects/<slug>-<hash>/`, **not** `accounts/`.
3. Send a Telegram message → one wake, one reply, no `/repo` prompt (the bot is
   the route).
4. Resident dominion + any KB commit land under the project home; the project
   repo `git status` stays clean (no `.brnrd-kb/` leak).

**Add lane (account router, service):**
1. `brnrd connect <url>`; `brnrd add repoA`; `brnrd add repoB`;
   `brnrd setup telegram` (home/account gate); `brnrd up`.
2. Home resolves under `accounts/<id>/`.
3. A Telegram message with no active repo **asks which repo** (no silent default
   fallback). Selecting `repoA` routes the wake to repoA's home.
4. A GitHub event on `repoB` routes to repoB without the Telegram dispatcher.

**Cross-check (the transport-agnostic invariant):** capture the event envelope
in both lanes (a bind-delivered Telegram event and an add-routed one) and
confirm they enter the same single-flight loop with the same schema — only the
source metadata differs. If the two lanes need different daemon code paths past
the gate boundary, the abstraction leaked; fix it before calling this done.

## Dependencies & sequencing at a glance

- Phase 1 → Phase 2 (add needs account-home selection).
- Phase 4 depends on `brnrd-bot` identity (infra + design page).
- Phases 5, 6, 7 are independent wakes; none block the two-lane behavior.
- Ship order for fastest working-both-ways: **1 → 2 → 3**, validate, then 4/5/6
  as separate wakes. 7 whenever.
</content>
</invoke>
