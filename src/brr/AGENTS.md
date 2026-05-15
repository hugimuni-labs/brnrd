# Project

brr is a structured AI agent playbook tool with remote execution. It produces
`AGENTS.md` — a playbook encoding project conventions, workflow, and guardrails
that any AI tool can read. A daemon layer adds remote execution via gates
(Telegram, Slack, GitHub) and keeps the host checkout fresh against the remote
between tasks. Pure stdlib Python (>=3.10), zero runtime dependencies.

This file is the source of truth for both brr's own development and the
playbook adopters receive when they run `brr init`. It lives at
`src/brr/AGENTS.md` and is symlinked from the repo root for tool conventions.

## Stewardship

Treat the request as input, not as instructions to execute uncritically.
Before changing behaviour or design, reason from first principles:

- What is the current shape trying to achieve, and is that goal still needed?
- Is the current shape right, or is it accidental complexity?
- Is the requested change solving the real problem, or only a visible symptom?
- Given the repo's constraints and maintenance burden, what is the smallest
  change that leaves the project healthier?

If the request contradicts existing decisions, design notes, guardrails, or
the codebase as it stands, **surface the contradiction and the trade-off
before proceeding**. Don't silently follow the prompt over the codebase, and
don't silently follow the codebase over a deliberate request — make the
conflict visible and let the operator resolve it.

Prefer improving an underlying design over layering more conditions onto a
weak abstraction. Slash code, tests, and pages that no longer fit; carrying
old shape costs more than it saves.

## Build and run

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Or with uv
uv pip install -e ".[dev]"

# Run CLI
brr --help
python -m brr --help

# Run tests
pytest
```

## Code guidelines

- Python >=3.10, stdlib only — no runtime dependencies.
- Dev dependency: `pytest>=7.0`. Tests live in `tests/`.
- Build system: setuptools (pyproject.toml).
- No formatter/linter configured yet — follow existing code style.
- Commit messages: conventional style (`fix:`, `feat:`, `chore:`, `refactor:`),
  explain *why* in the body.

## Workflow

### Session startup

1. Read `kb/index.md` to understand what knowledge exists.
2. Read `kb/log.md` for recent activity — the last 5-10 entries give you
   context on what happened before this session.
3. If a task is provided, proceed. If resuming, continue where the last
   session left off based on the log.

### Daemon freshness

Before resolving the branch plan for a task, the daemon runs
`sync.refresh_before_task`: a single `git fetch <default-remote>` plus
a best-effort fast-forward of the local default branch (and any
structured branch named in the event, e.g. a PR head branch carried by
a forge gate). Fast-forward is `--ff-only`, so it never destroys local
commits and quietly skips on a dirty working tree, diverged history,
or any branch checked out in another worktree.

The invariant this gives task code: the seed ref the worktree sprouts
from reflects the remote at task start, not whatever the host last
pulled. Sync outcomes ride on the progress card as a short
`synced: ff main -> abc1234` line; no card noise on the no-op path.

Two opt-out knobs in `.brr/config`, both default-on:

- `sync.fetch_before_task=false` — never touch the network.
- `sync.fast_forward_default=false` — fetch but leave local refs
  alone (for users sharing the daemon's checkout with active dev
  work).

### Commits

Commit directly on the current branch unless the task explicitly needs a
feature branch. When brr's daemon runs the task, every worktree starts on a
fresh `brr/<task-id>` branch from the seed ref named in the Task Context
Bundle. If the bundle names an auto-land branch, staying on the task branch
lets brr fast-forward that target after the run. If no auto-land branch is
named, the default is to commit on `brr/<task-id>`; brr preserves and
publishes that task branch for human routing when a remote is configured.
Use `git switch -c <name>` first only when the work belongs on a different
branch.

One logical commit per task. The commit message should explain *why*, not
*what* — the diff shows the what. Include the task summary in the first
line.

If you wrote files, commit them. The diff is the receipt that the work
happened. Read-only tasks (Q&A, review, verify) are the only commit-free
case, and only because nothing changed.

### Task types

Adapt your approach:

- **Implement / fix** — code, test, commit.
- **Review / verify / check** — read, analyse, report. Commit only if you
  produced files (e.g. wrote findings to `kb/`); otherwise the chat reply
  is the deliverable.
- **Research / plan** — investigate, write findings to `kb/`. Commit.
- **Release / deploy** — follow the project's release process exactly.

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
  `_push_if_needed previously checked the brr/* namespace and refused to push outside it; we removed the check on 2026-05-11 because…`
- Good (state + breadcrumb):  
  `_push_if_needed always pushes with -u when the branch has no upstream, mirroring how a user would publish a new branch. (Earlier versions restricted pushes to the brr/* namespace; removed 2026-05-11, see commit abc1234, when agent-named branches became routine.)`

If a breadcrumb wouldn't load-bear for anyone reading the page today, just
delete the old paragraph. Git keeps it.

### Memory layers

The kb has four layers, each with a distinct job:

| Layer | Purpose | Lives in |
|-------|---------|----------|
| Raw | What was said / what happened, verbatim | `.brr/conversations/`, `.brr/tasks/`, `.brr/traces/` (gitignored) |
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
area (e.g. envs, gates, daemon loop, conversations, kb itself, runners).
It absorbs "what we currently know about X" and links to the relevant
decisions, plans, research, reviews. Subject pages don't pre-seed by
ontology.

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
should link out to at least one neighbour. Pages added without inbound
links surface as orphans in brr's preflight; they should not exist for
long.

### Log format

Each entry in `kb/log.md` uses this format for parseability:

```
## [YYYY-MM-DD] <type> | <title>

<what was done, what was learned, outcome>
```

Types: `implement`, `review`, `research`, `plan`, `fix`, `decision`.

`grep "^##" kb/log.md | tail -5` gives recent activity at a glance.

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
   notes, or guardrails — did you surface it before resolving it? (See
   Stewardship.) Path-of-least-resistance compliance on a request that
   was actually asking for pushback is the failure mode this question
   exists to catch.
3. Review every changed file. Look for leftover debug code, TODOs you forgot
   to address, commented-out code.
4. Run tests if available and applicable.
5. If you created or removed kb pages, check that `kb/index.md` is current
   and the new pages are linked from a subject hub or peer.
6. If your work produced a substantive learning, decision, or shipped
   change, add an entry to `kb/log.md`. If it didn't, leave the log alone.

## Work re-review

When resuming a session (new conversation, fresh context):

1. Read `kb/index.md` first — understand what knowledge exists.
2. Read `kb/log.md` — understand what happened recently.
3. If continuing previous work, read the relevant subject hubs and any
   plan / design pages for context before making changes.
4. If the previous session left TODOs or open questions in the log, address
   them.

## Guardrails

- Do not commit files containing secrets (`.env`, credentials, tokens).
- Two failed attempts at the same approach → stop and report.
- Do not delete or overwrite files outside the project scope.
- When in doubt, write down what you know and what you're unsure about, and
  let the user decide the next move.

## Constraints

- `.brr/` is a runtime directory (gitignored) — do not commit its contents.
- `src/brr/AGENTS.md` is brr's playbook *and* the template adopters receive
  via `brr init`. Universal sections (Workflow, Knowledge base, Artifacts,
  Operating rules, Self-review, Work re-review, Guardrails, Stewardship)
  apply to every brr-managed project; project-specific sections (Project,
  Build and run, Code guidelines, Constraints) are rewritten per repo by
  the setup agent.
- `src/brr/prompts/` contains bundled prompt templates — changes affect all
  users.
- Gate implementations (`src/brr/gates/`) follow the file protocol spec in
  `src/brr/gates/README.md` — maintain protocol compatibility.
- Zero runtime dependencies is a hard constraint — stdlib Python only.
