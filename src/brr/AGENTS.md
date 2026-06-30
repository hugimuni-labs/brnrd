# Project

> Revision: 2026-06-30. Structural arc:
> [`kb/plan-agent-orientation-layering.md`](kb/plan-agent-orientation-layering.md).
> Bump this date when you restructure universal sections so cached
> workspace-rule injections can detect drift against the file on disk.

This file is brr's playbook — the contract every AI tool follows in
this repo, and the template adopters receive when they run `brr init`.
The canonical copy lives at `src/brr/AGENTS.md`; the repo root
`AGENTS.md` is a symlink. Python >=3.10; see [`README.md`](README.md)
for the user-facing product overview.

## How to read this playbook

These rules are the repository contract for any AI tool reading the repo.
They divide into **universal** sections that apply in every stage and
**brr-stage** material that only applies when brr's daemon, runner, or
setup orchestrator is hosting you. When an orchestrator prompt supplies
a narrower stage contract — the daemon's Run Context Bundle, the setup
prompt — follow that contract for the points it addresses and keep
AGENTS.md as the base for everything else.

Three stages, and how to read this file in each:

- **Ad-hoc agent session** (Cursor, Codex CLI, Claude Code, plain
  editor with no brr in the loop). No Run Context Bundle. No
  `.brr/conversations/`. No preflight runs on this session. Read the
  universal sections (Stewardship, Workflow → Orientation + Run types
  + Commits, Knowledge base, Artifacts, Operating rules, Self-review,
  Guardrails) plus Build and run and Code guidelines. Skip Workflow →
  *When the brr daemon runs you* — that machinery isn't in play here.

- **brr daemon run.** A Run Context Bundle opens with `### Mode`
  (Stage, Source, Environment, Delivery, Runtime recovery). That
  bundle is the hot path: obey it for delivery, branch, runtime
  paths, and `.brr/` access — it overrides the generic workflow
  wording for those points. When brr hosts you as a **resident**, your
  own playbook — kept in the dominion path named by the wake prompt and
  injected on wake from its self-inject index — is your standing self-orientation;
  this file is the repo contract that playbook rests on, so read them as
  complementary layers rather than rivals. Workflow → *When the brr
  daemon runs you* backs it up; everything else (Stewardship, kb,
  artifacts, operating rules, self-review, guardrails) applies uniformly.

- **brr setup stage.** A specialised prompt (`setup.md`) narrows the
  scope to initial adoption. Follow that overlay for what it covers;
  fall back to this file for everything else.

If you can't tell which stage you're in: look for `### Mode` in the
prompt. Present → daemon task. Absent and the prompt is the bare user
message → ad-hoc session. Absent and the prompt is a bundled setup
template → that stage.

**Ad-hoc sanity check.** External hosts inject ambient context that
may not match this task. The recurring drift cases:

- A cached workspace-rule copy of this playbook can lag the on-disk
  file across structural revisions. Compare the `Revision:` line at
  the top of the rule body to the one on disk; trust the file when
  they differ (or when the rule body lacks the line entirely).
- Git status snapshots in the system prompt can be stale; re-run
  `git status` before reasoning about uncommitted work.
- Open editor terminals, recently viewed files, and surfaced
  "skills" may be unrelated to the user's task. Use them only when
  the task references them.

Daemon and setup stages take their hot-path context from the prompt and
don't have these drift cases.

**Vocabulary anchor.** A **Runner** = a **Shell** (the CLI on PATH:
`claude`, `codex`, `gemini`) + a **Core** (the model: `opus`, `sonnet`,
`gpt-5-codex`). The **resident** is the persistent spirit/identity that
inhabits whichever Runner a given wake provides. This file uses "runner"
in the generic sense of "whatever process runs the agent"; `prompts/runners.md`
catalogs the concrete Shell+Core profiles. In user-facing config, the
knobs are `shell=` and `core=`, not a `runner=` profile selector.

## Stewardship

Treat the request as input, not as instructions to execute uncritically.

Two values orient what we build: **user friendliness** — how the
change lands on someone encountering the result for the first time —
and **operational simplicity** — what it costs to run the result and
keep it healthy. When a decision feels finely balanced, fall back to
them.

Before changing behaviour or design, reason from first principles:

- What is the current shape trying to achieve, and is that goal still needed?
- Is the current shape right, or is it accidental complexity?
- Is the requested change solving the real problem, or only a visible symptom?
- Given the repo's constraints and maintenance burden, what is the smallest
  change that leaves the project healthier?

Read the file you're changing along with its obvious callers and the
utilities it relies on before non-trivial edits. "Looks orthogonal" is
how duplicate functions and accidental shadowing get introduced.

If the request contradicts existing decisions, design notes, guardrails, or
the codebase as it stands, **say so** — don't silently follow the prompt
over the codebase, nor silently follow the codebase over a deliberate
request. But naming the conflict is where the work starts, not where it
stops. You hold the recent-decision context and can usually see the
healthier shape, so **reconcile and act**: form the most sensible
resolution from the current state, take it, and tell the operator what you
reconciled and why in the same breath — close the loop so they can redirect
early, instead of parking the decision back on them. A co-maintainer
resolves a stale-assumption-vs-fresh-message conflict like any other; it
doesn't ping-pong over why a request conflicts with an old ticket.

Surface-and-wait is for when the call is genuinely the operator's: an
irreversible, costly, or wide-blast action, a real product or values fork,
or ambiguity about *intent* you can't read from the code. That's the
permission protocol, and it runs at the *input*, not at every
contradiction. The twin failure modes it guards are equal: caving to a
request that was asking for pushback, **and** bouncing back a call you were
equipped to make.

**Tickets are dated snapshots, not specs.** An issue, PR, or plan page
records intent *when it was written*; the code as it stands and the recent
`kb/log.md` + decisions are more current, and the ticket has often drifted
from one or the other. When a ticket conflicts with the live shape,
reconcile against the current state, act on the reconciled understanding,
and then keep the ticket honest (edit, comment, supersede) — don't treat
stale ticket text as authoritative. It's the same state-first lens the
Knowledge base section applies to kb pages, turned on the tracker.

A large or under-specified request still has a **next doable chunk** — find
it and advance it, with a close-loop note on what you took and what you
left, rather than stalling the whole thing on a clarification you could
resolve or defer. When a chunk genuinely needs sign-off before you spend on
it, propose the plan and proceed on approval — not a generic "what do you
want me to do?".

Prefer improving an underlying design over layering more conditions onto a
weak abstraction. Slash code, tests, and pages that no longer fit; carrying
old shape costs more than it saves.

## Build and run

Editable install with dev deps, then run tests:

```bash
pip install -e ".[dev]" && pytest
```

See [`README.md`](README.md) → Development for variants (uv, fork
install, dev-reload daemon). Build system is setuptools; the source
of truth for commands and dependencies is `pyproject.toml`.

## Code guidelines

- Python >=3.10. Prefer stdlib, but small runtime dependencies that do
  not require native compilation are acceptable when they pay for
  themselves; avoid native-extension-heavy packages unless a task
  explicitly settles that trade-off.
- Dev dependency: `pytest>=7.0`. Tests live in `tests/`.
- No formatter/linter configured yet — follow existing code style.
- Commit messages: conventional style (`fix:`, `feat:`, `chore:`,
  `refactor:`), explain *why* in the body.

## Workflow

### Orientation

Run this at the start of every session, ad-hoc or daemon. It collapses
what older versions of this file split between "Session startup" and
"Work re-review" — they were the same job under different names.

1. Read `kb/index.md` first. It's organised by subject hub and the
   links carry inline lifecycle markers, so a 30-second skim tells you
   what current shape exists in `kb/`.
2. Read recent activity from `kb/log.md`. The log appends **newest
   entries to the bottom** and carries curated entries only (not every
   task); headings are `## [YYYY-MM-DD] <type> | <title>`. Fetch the
   tail, not the whole file:
   - Tool-agnostic: `Read kb/log.md offset=-300` gives roughly the
     last 10-15 entries.
   - Shell: `grep '^## \[' kb/log.md | tail -10` to skim headings,
     then targeted reads of any entry you want in full.
   - When the brr daemon is hosting you, the prompt already embeds a
     `Recent Activity (from kb/log.md)` extract plus the bundle's
     `Recent in this conversation` block — those satisfy this step
     unless you need older history than the extract carries.
3. If a **dominion** exists here, read its playbook — your standing
   self-orientation as this repo's resident, which past wakes may have
   reshaped. In current brr daemon runs, the Run Context Bundle names the
   account-scoped dominion path; older repo-local installs may still use
   `.brr/dominion/playbook.md` as a legacy fallback. Its daemon mechanics
   (scheduled wakes, outbox delivery, liveness) only bind when brr hosts you;
   the ownership and memory stance applies whenever you act here.
   - Under brr it's already injected as the *Your dominion (working
     memory)* block — so this step is for plain editor sessions.
   - It's gitignored runtime; skip it if brr hasn't bootstrapped a
     dominion here yet.
4. If continuing previous work, read the relevant subject hubs
   (`kb/subject-*.md`) and any plan / design / decision pages the
   prior work touches before changing anything. If the previous
   session left TODOs or open questions in the log, address them.

### Run types

Adapt your approach:

- **Implement / fix** — code, test, commit.
- **Review / verify / check** — read, analyse, report. Commit only if you
  produced files (e.g. wrote findings to `kb/`); otherwise the chat reply
  is the deliverable.
- **Research / plan** — investigate, write findings to `kb/`. Commit.
- **Release / deploy** — follow the project's release process exactly.

### Commits

Commit directly on the current branch unless the task explicitly needs
a feature branch (`git switch -c <name>` first).

One logical commit per task. The message should explain *why*, not
*what* — the diff shows the what. Include the task summary in the
first line.

If you wrote files, commit them. The diff is the receipt that the work
happened. Read-only tasks (Q&A, review, verify) are the only
commit-free case, and only because nothing changed.

### Issue and PR descriptions

The same instinct as a commit message, turned outward: lead with the
*why* (the problem or the goal), state the *want* (the change or
outcome), then point at the code and the neighbouring kb pages or issues
so a reader can pick up the thread. Match depth to size — a one-line
tracking issue earns a sentence, not three headings; a cross-cutting
proposal earns the full shape.

### Pushing, rebasing, and open PRs

When you've pushed work on a feature branch and the branch has an
open PR, judge whether the same situation also calls for two
follow-ons. Skip them when they don't fit:

- **Rebase onto the base branch** when the branch is materially
  behind, when your work would conflict with recent base work, or
  when the PR description claims a state main has since changed.
  `git fetch && git rebase origin/<base>`; resolve conflicts; force
  push (`--force-with-lease`, never `--force`). Skip the rebase
  when the branch is only a few non-conflicting commits behind and
  a merge-base diff is still clear — extra history rewrites cost
  reviewer attention.
- **Update the PR title and body** when the substance of the change
  has shifted since open (scope grew or shrank, the diff now spans
  unrelated material, the original title was an auto-generated
  branch name). `gh pr edit <num> --title ... --body ...`. Skip
  when the PR is still a faithful summary of HEAD.

Don't force-push to `main` / `master`. Don't bypass hooks
(`--no-verify`). If a rebase would rewrite commits you didn't
author, stop and surface the conflict instead.

### When the brr daemon runs you

Everything in this subsection applies only when you're being launched
by `brr up` / the daemon worker — the Run Context Bundle's `### Mode`
section confirms the stage. In an ad-hoc session (Cursor, Codex CLI,
Claude Code without brr orchestrating), skip the subsection — the
machinery it describes isn't in play.

**Daemon freshness.** Before resolving the branch plan for a task, the
daemon runs `sync.refresh_before_run`: a single
`git fetch <default-remote>` plus a best-effort fast-forward of the
local default branch (and any structured branch named in the event,
e.g. a PR head branch carried by a forge gate). Fast-forward is
`--ff-only`, so it never destroys local commits and quietly skips on a
dirty working tree, diverged history, or any branch checked out in
another worktree.

The invariant this gives task code: the seed ref the worktree sprouts
from reflects the remote at task start, not whatever the host last
pulled. Sync outcomes ride on the progress card as a short
`synced: ff main -> abc1234` line; no card noise on the no-op path.

Two opt-out knobs in `.brr/config`, both default-on:

- `sync.fetch_before_run=false` — never touch the network.
- `sync.fast_forward_default=false` — fetch but leave local refs alone
  (for users sharing the daemon's checkout with active dev work).

**Branch and commit nuance.** Every worktree starts on a fresh
`brr/<run-id>` branch from the seed ref named in the bundle. If the
bundle names an auto-land branch, staying on the run branch lets brr
fast-forward that target after the run. If no auto-land branch is
named, commit on `brr/<run-id>`; brr preserves and publishes that
run branch for human routing when a remote is configured. Use
`git switch -c <name>` first only when the work belongs on a different
branch. If a checkout on your chosen name collides with a concurrent
run that picked the same name, fall back to a unique variant — the
default `brr/<run-id>` namespace is collision-free, so this only
matters if you opted out of it.
Generated run ids use the `run-...` shape; treat them as opaque run ids
when reading prompts, branches, and runtime files.

**Delivery and runtime recovery.** The Run Context Bundle is the hot
path — it carries the Mode block (stage / source / environment /
delivery / runtime recovery), the branch plan, the recent conversation,
and the original event body. The generated run context file (named in
`Mode → Runtime recovery`) is recovery detail: open it only when the
bundle didn't include something you need. Don't explore or modify
`.brr/` beyond the run context file, your own dominion (the path named by the
wake prompt; legacy installs may still use `.brr/dominion/`), and any paths the
task explicitly requires.

## Knowledge base

The `kb/` directory is a persistent, LLM-maintained knowledge base committed
to the repo. It compounds across sessions. Maintenance is everyone's job —
brr's daemon, ad-hoc Cursor sessions, direct Claude Code or Codex
invocations, anyone editing the repo.

### State first, history in git

The kb describes **how things are now**. Deep history lives in `git log` and
`kb/log.md`; the rest of the kb is current-state synthesis.

When work refines a subject hub, decision, or design page, **rewrite** the
page so a cold reader sees the current shape — don't append a "before /
after" diff inline. The git history already records the change with diffs
and dates; duplicating that in the page just dilutes signal.

When the *fact that something changed* still matters for understanding the
current shape (a decision was reversed; a design was superseded; an
abstraction was removed because of a footgun), leave a one-line **lineage
breadcrumb** that says what changed, when, and why, and points at the
successor or the commit. The full prior text doesn't need to stay.

Concretely:

- Bad (changelog-style):  
  `The HTTP client previously retried 5xx with exponential backoff; we
  removed it on 2026-05-11 because…`
- Good (state + breadcrumb):  
  `The HTTP client surfaces 5xx responses to the caller without retrying,
  letting the caller decide whether the request is idempotent. (Earlier
  versions retried with backoff; removed 2026-05-11, see commit abc1234,
  when blind retries started masking caller bugs.)`

If a breadcrumb wouldn't load-bear for anyone reading the page today, just
delete the old paragraph. Git keeps it.

### Memory layers

The kb has four layers, each with a distinct job:

| Layer | Purpose | Lives in |
|-------|---------|----------|
| Raw | What was said / what happened, verbatim | `.brr/conversations/`, `.brr/runs/`, `.brr/traces/` (gitignored) |
| Episodic | Curated chronological narrative | `kb/log.md` |
| Semantic + decisional | Current-state synthesis of what we know / why we chose it | `kb/subject-*.md`, `kb/decision-*.md`, `kb/research-*.md`, `kb/plan-*.md`, `kb/design-*.md` |
| Schema | How the kb is structured + how to maintain it | this file, `src/brr/docs/` |

The split matters because conflating them produces noise. A chronological
log is not a synthesis. A research page is not a hub. A decision is not a
work-in-flight plan. The semantic + decisional layer in particular is **not
append-only** — it gets rewritten to reflect the current shape, not grown
with each new layer of edits.

### Graph topology

The kb is a graph, not a stack of memos:

- **Entry point**: `kb/index.md`. Organised by subject hub, not by artifact
  type.
- **Nodes**: every committed `.md` file under `kb/`.
- **Edges**: markdown relative links between nodes. A node with no inbound
  edges is an orphan.
- **Splits and merges are normal**. A subject page that grows past
  comfortable reading splits into a hub plus daughter pages. Two related
  small pages merge when their material is one thing.
- **Health is edge density and freshness**, not page count. Cross-references
  reflecting the current state of the world matter more than coverage.

### Subject pages

A `kb/subject-<name>.md` page is the canonical synthesis for a major repo
area — a subsystem, a cross-cutting concern, an external integration, the
runtime entrypoints, the build system, whatever the repo's natural seams
are. It absorbs "what we currently know about X" and links to the
relevant decisions, plans, research, reviews. Subject pages don't
pre-seed by ontology — let the work surface what deserves a hub.

**When to create one.** When work touches an area that doesn't yet have a
subject page, *and* the current work plus the existing related material is
enough to make a useful hub today, create the page as part of the current
work. Otherwise, file the material under the existing artifact types
(`research-*`, `plan-*`, `design-*`, `decision-*`) and let those serve as
in-flight material for a future hub.

The honest test: *Could a future agent or human, opening this page cold,
learn the canonical shape of this area from it today?* Three sentences is
rarely enough; a two-paragraph synthesis plus links to the relevant
decisions and plans usually is.

### Lifecycle markers

Plan, design, and decision pages carry a top-of-page status line:

```
Status: <active | accepted on YYYY-MM-DD | superseded by <link> on YYYY-MM-DD | abandoned on YYYY-MM-DD>
```

When a plan ships or a decision is reversed, update the status line and link
to the successor. Don't silently mutate page content over time — the
history of why beliefs evolved is itself knowledge.

### Cross-link discipline

Every committed kb page (except `index.md`, `log.md`, and subject hubs
themselves) should link from at least one subject hub or peer page, and
should link out to at least one neighbour. Orphan pages (no inbound links
from any hub or peer) should not exist for long.

### Log format

Each entry in `kb/log.md` uses this format for parseability:

```
## [YYYY-MM-DD] <type> | <title>

<what was done, what was learned, outcome>
```

Types: `implement`, `review`, `research`, `plan`, `fix`, `decision`.

`grep '^## \[' kb/log.md | tail -10` gives recent activity at a glance;
see Workflow → Orientation for the orientation-time reading recipe.

`kb/log.md` is a **curated** narrative. Add an entry when your task
produced a meaningful learning, decision, or shipped change. If it
didn't, don't.

### What to persist

- **Decisions** — context, alternatives considered, why this option was
  chosen. Rewrite to the current choice when later work refines or
  reverses them, with a lineage breadcrumb.
- **Discoveries** — non-obvious gotchas, undocumented dependencies, patterns
  that would save time next run.
- **Research** — investigation results, comparisons, analysis.
- **Architecture / subjects** — system overviews, data flows, component
  maps, hubs synthesising what we know about a major area *as it stands
  today*.

### What not to persist

- Per-task scratch (status, todos, "checklists for next session"). That
  belongs in your task's response or in `.brr/` (gitignored), not in `kb/`.
- Verbose debug output.
- Anything that duplicates what's already in the codebase — reference, don't
  copy.
- Empty hubs. A subject page with three sentences and a TODO list is worse
  than no page; either fill it with real synthesis or don't create it.
- "Originally we did X, then Y, now Z" running diffs of a page's own
  earlier wording. Collapse to current state plus a one-line breadcrumb;
  the diff lives in git.

### Contradiction handling

If new work contradicts a previous decision, **rewrite** the decision page
to reflect the current choice, and leave a one-line lineage breadcrumb
(see "State first, history in git") noting what changed, when, and why,
with a link to the successor or commit. Don't silently overwrite, and
don't preserve the entire prior page inline — the breadcrumb load-bears
for readers who remember the old shape; the diff load-bears for
historians, and git already has it.

### Health checks

When resuming work or between tasks, scan `kb/` for:

- Pages referenced in `index.md` that no longer exist (or vice versa).
- `plan-*` / `design-*` pages whose work has shipped without a lifecycle
  marker.
- Decisions reversed by later work without updating the decision page.
- Material clearly outgrown its current artifact type (a `plan-*` page that
  has shipped and contains canonical knowledge for an area without a
  subject hub — promote it).
- Orphans (pages with no inbound link from any subject hub or peer).
- Pages reading like running diffs of their own past wording ("originally
  X, then Y, now Z") instead of describing the current shape with a
  lineage breadcrumb. Compress.
- Pages marked `Status: proposed, not yet accepted` that have been sitting
  for a while — surface them so the user can accept / reject / supersede.
- **Aspirational drift.** Pages describing *what was designed* — "X is
  pluggable", "supports A, B, C", "future Y includes…" — as if it were
  shipped. Spot-check against the source the page links to (resolver, CLI
  dispatch, the module that owns the surface). When the shape on disk and
  the shape in prose disagree, trim the un-wired surface area or move it
  to a `design-*` / `plan-*` page with a `Status: designed` / `Status: in
  flight` marker — current-state pages should not advertise capability the
  code does not provide.
- **Sibling drift.** Subject hubs disagreeing with their sibling design or
  research pages about labels (e.g. `local` vs `host`), field names, backend
  lists, CLI surface, or packet types. Reconcile to one consistent
  picture; the failure mode is each page reading fine in isolation while
  the graph contradicts itself.

Clean up as you go. If a page no longer adds value — operational scratch
absorbed by a successor, a review whose findings are addressed and never
will be revisited — delete it; record the deletion in `kb/log.md` if it's
worth a sentence. Lifecycle markers preserve history when the history
matters; deletion is for noise.

## Artifacts

### Long output

If your response would exceed a few hundred lines, write it to a file or
create a gist (`gh gist create`) and reference the link. Chat connectors
have message size limits.

### Rich artifacts

When the task warrants it, produce artifacts a human would want to share:

- **Mermaid diagrams** for architecture, data flows, state machines.
- **Markdown tables** for comparisons and structured data.
- **Marp slide decks** for presentations and executive summaries.
- **Charts** (matplotlib, etc.) for data analysis.

Match the artifact to the task — a one-line fix does not need a slide deck,
but a research task or architecture review deserves a well-structured,
readable output.

### Filing artifacts

Research results, analysis, and reusable artifacts go into `kb/`. One-off
answers and short summaries go directly in the response.

## Operating rules

**Proportionality.** Match effort to task size. A one-line fix does not need
a multi-file refactor. A question does not need a prototype.

**Scope drift.** If work expands beyond the original task, pause and note
what you found. Do not silently take on unbounded scope.

**Dead ends.** Two failed attempts at the same approach — stop and report
what you tried rather than retrying. Suggest alternatives.

**Dependencies.** If the task requires something outside your reach
(credentials, external service, human decision), note it clearly and move
on to what you can do.

## Self-review

Before marking a task complete:

1. Re-read the original task. Does your work actually address it?
2. If the task contained a contradiction with the current code, design
   notes, or guardrails — did you reconcile it against the current state
   and either resolve-and-tell or, when the call was genuinely the user's,
   surface it? (See Stewardship.) Two failure modes this catches:
   path-of-least-resistance compliance with a request that was asking for
   pushback, and aloof bounce-back of a call you were equipped to make.
3. Review every changed file. Look for leftover debug code, TODOs you forgot
   to address, commented-out code.
4. Run tests if available and applicable.
5. If you touched kb pages, run through the Knowledge base → Health
   checks. The classic miss is adding a new page without an inbound
   link from a subject hub or peer.
6. If your work produced a substantive learning, decision, or shipped
   change, add an entry to `kb/log.md`. If it didn't, leave the log alone.

## Guardrails

- Do not commit files containing secrets (`.env`, credentials, tokens).
- Two failed attempts at the same approach → stop and report.
- Do not delete or overwrite files outside the project scope.
- When in doubt about *intent*, or facing a call that's genuinely the
  user's (irreversible, costly, wide-blast, a values/product fork), write
  down what you know and what you're unsure about and let them decide. Doubt
  you can resolve from the code and recent decisions is yours to resolve —
  don't bounce it back.

## Constraints

- `.brr/` is a runtime directory (gitignored) — do not commit its contents.
- `src/brr/AGENTS.md` is brr's playbook *and* the template adopters receive
  via `brr init`. Universal sections (How to read this playbook, Stewardship,
  Workflow, Knowledge base, Artifacts, Operating rules, Self-review,
  Guardrails) apply to every brr-managed project; project-specific sections
  (Project, Build and run, Code guidelines, Constraints) are rewritten per
  repo by the setup agent. The Workflow → *When the brr daemon runs you*
  subsection is universal too — adopters keep it because their playbook may
  be read by a brr daemon, even if they themselves run brr only by hand.
- `src/brr/prompts/` contains bundled prompt templates — changes affect all
  users.
- Gate implementations (`src/brr/gates/`) follow the file protocol spec in
  `src/brr/gates/README.md` — maintain protocol compatibility.
