# Subject: the knowledge base itself

Hub page for everything we know about brr's kb pattern: where it
lives, what it's for, how it stays coherent, and what each surrounding
artifact contributes.

This is a *subject hub* in the sense
[AGENTS.md → "Knowledge base shape"](../AGENTS.md) describes —
synthesis you can read in one sitting to understand the kb without
reconstructing it from a dozen scattered pages. Schema lives in
AGENTS.md; the *why* of any given choice lives in
[`decision-kb-shape.md`](decision-kb-shape.md); this page is *what
brr's kb actually is today and how it works*.

## What the kb is for

The kb makes the project's structured knowledge survive between
agent sessions. Each session — Cursor, Codex CLI, Claude Code,
remote brr — starts cold; without persistent context, every task
re-derives the same things ("what's the runner contract?", "why is
worktree the default?", "what was tried and didn't work?"). A live
kb that's read at task start and updated at task end turns those
re-derivations into one-time costs.

Concretely the kb covers four kinds of memory:

| Memory       | Purpose                                  | Where it lives                              |
| ------------ | ---------------------------------------- | ------------------------------------------- |
| Raw          | What literally happened — every event,   | `.brr/conversations/<key>/<event-id>.jsonl` |
|              | every prompt, every stdout — immutable.  | (merge by ``ts``), `.brr/tasks/<id>/`,      |
|              |                                          | `.brr/traces/`                              |
| Episodic     | Curated narrative of what was done and   | [`kb/log.md`](log.md)                       |
|              | what was learned, one entry per session. |                                             |
| Semantic /   | What we know and why, evolving over      | `kb/subject-*.md`, `kb/decision-*.md`,      |
| decisional   | time — subject hubs and decision pages.  | `kb/plan-*.md`, `kb/design-*.md`            |
| Schema       | The universal rules every agent follows. | [`AGENTS.md`](../AGENTS.md) (canonical at   |
|              |                                          | `src/brr/AGENTS.md`, symlinked at the root) |

The first two are append-only. The last two are **rewritten** to
reflect the current state — subject hubs and decision pages describe
how things are now, with concise lineage breadcrumbs when the *fact*
of a change still matters. Deep history lives in `git log` and
`kb/log.md`, not inline. The split keeps per-task chatter out of the
synthesis layer and the synthesis out of the schema.

## The graph topology

The kb is a **graph** with [`kb/index.md`](index.md) as the entry
point, every other page as a node, and Markdown links as edges. Two
invariants make it navigable:

1. **Index reachability** — every page in `kb/` (except `index.md`
   and `log.md`) is linked from the index under a subject heading
   with a one-line summary. The index is grouped by *subject area*,
   not by *artifact type*: a reader asking "what do we know about
   environments?" lands on the Environments section, not on a
   `Plans` / `Decisions` / `Designs` directory listing.
2. **Lifecycle markers** — plan / design / deck / notes pages carry a
   status (`active` / `shipped` / `shipped, with revisions` /
   `blocked` / `paused` / `roadmap`) at the top so a cold reader
   never mistakes stale planning for a current spec. Decision pages
   carry `Status: accepted` and `Supersedes:` headers; the supersedence
   chain *is* the history of how the design evolved.

`kb/log.md` is *curated narrative* — one entry per substantial
session, in chronological order, with a `## [YYYY-MM-DD] <type> |
<title>` header and a few paragraphs of what was done, what was
learned, what's outstanding. It's append-only: older entries are
preserved unchanged even when they reference pages that have since
been slashed (the entry is true at the time of writing; git history
holds the slashed page if anyone needs the original content).

## When to create a new subject hub

Subject hubs exist when there's enough material to synthesise.
Don't pre-create empty pages. Don't create them top-down from a list.
The rule from AGENTS.md is two conditions that both have to hold:

1. **A new piece of work plus existing material would form a useful
   hub today**, and
2. **There is no good place for the synthesis except a new page**.

This page is a worked example. The trigger was the kb-shape arc
(phases 1-5 in [`decision-kb-shape.md`](decision-kb-shape.md))
landing four substantial pieces of work on the kb itself: the
schema rewrite in `AGENTS.md` (phase 2), the per-task log file
removal (phase 2), the cleanup pass that slashed nine stale pages
and reshaped the index (phase 3b), and the preflight + redundancy
pass that makes consistency cheap (phase 4). With that much material
across `decision-kb-shape.md`, `AGENTS.md`, `kb_preflight.py`, and
`llm-wiki.md`, "what do we know about the kb?" had no single answer
page — so this hub earns the spot.

The right shape of a subject hub:

- A **what / why / where** opening so a reader knows whether the
  page is for them.
- **Synthesis**, not citation lists. Extract the pattern; link the
  artifacts as the receipts that pattern is grounded in real work.
- **Status**: which parts are stable, which are in flight, which
  were tried and rejected.
- **Pointer paragraph at the bottom** with the half-dozen
  must-reads, in priority order.

## Cross-tool maintenance

The kb has to work outside brr too. Cursor sessions, Codex CLI
runs, Claude Code direct invocations, ad-hoc shell scripts that hit
a runner — they all read the same `AGENTS.md`. That is deliberate:
the kb-maintenance contract is the universal schema, not a
brr-specific behaviour.

The brr daemon's role is the **safety net**:

- A **deterministic preflight** ([`src/brr/kb_preflight.py`](../src/brr/kb_preflight.py))
  scans the kb on every wake: orphans missing from the index,
  index entries pointing to deleted files, broken cross-links inside
  kb pages. It runs every time, costs nothing, and produces
  structured findings.
- When the preflight finds something, those findings are **injected
  into the resident's own wake prompt** (via
  [`prompts._build_kb_health_block`](../src/brr/prompts.py)), so the
  resident folds the fix into its current thought against the AGENTS.md
  → "Knowledge base" rules. A clean preflight is silent — no tax on
  every wake. (Earlier this was a separate post-task LLM pass —
  `daemon._maybe_kb_maintenance` + a `prompts/kb-maintenance.md`
  overlay; retired 2026-06-08 with the resident reshape, since a
  resident that curates the shared kb as part of its single thought
  doesn't need a second spawn to do it. See
  [`design-agent-dominion.md`](design-agent-dominion.md).)

External tools (Cursor / Codex / Claude Code) don't have brr's
preflight, so they fall back to the AGENTS.md schema alone.
Eventually that schema may need a small companion — an optional
pre-commit hook that runs the same preflight from outside brr. Not
yet built; tracked as future work in
[`decision-kb-shape.md`](decision-kb-shape.md) → "What this decision
deliberately defers."

## Slashing, lifecycle, and the cost of preserving things

A healthy kb is a *small* kb of dense pages, not a big kb of pages
nobody reads. Some defaults:

- A page whose findings have been fully absorbed into a successor
  (decision, code, or another page) gets slashed, not marked
  superseded. Git history preserves the original; a redirect page in
  `kb/` adds noise without adding navigation.
- A plan that *shipped* gets a lifecycle marker at the top
  describing what landed, what was reversed, and pointing at the
  current code or successor decision. The plan stays as the
  reasoning record; readers know not to treat it as a spec.
- A plan that's *blocked* / *paused* gets a top-of-page marker
  naming what would unblock it. If the unblocking conditions never
  resolve, slash later.
- The log is never edited retroactively — a slashed page stays
  mentioned in the log entries that recorded its creation. The
  entry was true at the time.

The phase 3b cleanup applied this to brr's own kb: 22 → 13 subject
pages, every survivor with a clear status, no orphans, no broken
references. The before/after is in the log entry for that phase.

The state-first principle is now part of the schema (see
[`AGENTS.md`](../AGENTS.md) → "State first, history in git"): pages
describe the current shape, lineage breadcrumbs replace inline
running diffs of past wording, and git is the deep-history layer.
The execution plan is in
[`plan-kb-state-first-maintenance.md`](plan-kb-state-first-maintenance.md).

## What was deliberately rejected

A few attractive ideas that have been considered and not pursued:

- **A `brr kb` CLI subnamespace** (`brr kb-check`, `brr kb-lint`,
  `brr kb-add subject:envs`). Rejected: agent-facing surface goes
  through prompt injection and scanner output, not user CLI verbs.
  Keep the user-facing CLI minimal.
- **Auto-generating subject pages from a fixed list of subjects.**
  Rejected: a subject page with three sentences is worse than no
  subject page. Subjects accrete from real work. The decision page
  itself counts as the first material write on the
  kb-as-subject — and *this* page is the natural follow-up, not a
  pre-seeded scaffold.
- **A vector / embedding / graph-database semantic layer.** Out of
  scope. The textual layer here is compatible with future per-page
  embedding indexing; that's a separate project.
- **Wikilinks (`[[page]]`) and other Obsidian-isms.** Rejected:
  GitHub renders relative Markdown links; agents read raw
  Markdown. No Obsidian transport.
- **Per-task log files (`kb/log-task-<id>.md`).** Tried, removed.
  The story is in [`decision-kb-shape.md`](decision-kb-shape.md) —
  the per-task files split agent attention between two kb writes
  (the per-task file *and* `kb/log.md`) and the result was that
  neither was reliable. Stdout is the chat reply now; commits are
  mandatory for any file write; the log entry happens at the
  curated-narrative level when a session is worth recording.

## Read these next

In priority order:

1. [`AGENTS.md`](../AGENTS.md) → "Knowledge base shape" — the
   universal rules every agent follows.
2. [`decision-kb-shape.md`](decision-kb-shape.md) — why those rules,
   what triggered the rethink, what was deferred.
3. [`plan-kb-state-first-maintenance.md`](plan-kb-state-first-maintenance.md)
   — active refinement for keeping pages focused on the current shape
   while using git as the deep-history layer.
4. [`llm-wiki.md`](llm-wiki.md) — the framing the kb pattern took
   inspiration from. Skim for context, not as a spec.
5. [`src/brr/kb_preflight.py`](../src/brr/kb_preflight.py) and its
   tests — the deterministic side of the maintenance contract (its
   findings ride the resident's wake prompt via
   [`prompts._build_kb_health_block`](../src/brr/prompts.py)); the
   shortest path to "what is structurally enforceable about the kb."
6. The "drop the noisy abstraction" decision trio:
   [`decision-remove-triage.md`](decision-remove-triage.md) →
   [`decision-drop-streams.md`](decision-drop-streams.md) → this
   subject's triggering decision. Same pattern three times: when an
   abstraction's cost stops paying for itself, remove it.
