# Cursor orientation ergonomics — 2026-05-16

Status: shipped on 2026-05-16 — recommendations 1, 2, and 5
absorbed into [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md)
and shipped as slices 1 and 2. Items 3, 4, and 6 are tracked as open
follow-ups on the plan. Kept as the cold-read narrative of what
prompted the work.

Point-in-time ergonomics review of an external unmanaged session
(Cursor on a developer laptop, no brr daemon mediating). The task: do
an ergonomics review of how fast an agent can orient itself in this
repo with the tools it has and the playbook it's given. The data is
the session itself — what I had to fetch, what was redundant, what
was missing, what was actively confusing.

Pairs with
[research-runner-context-ergonomics-2026-05-09.md](research-runner-context-ergonomics-2026-05-09.md),
which covered a *daemon-launched* run from a different angle, and
with [`research-runner-orientation-ergonomics-2026-05-16.md`](research-runner-orientation-ergonomics-2026-05-16.md),
the same-day daemon-runner view that converged independently on the
same direction.

## Setup

- Tool: Cursor, Claude Opus 4.7.
- Repo state: clean working tree on `main` at `490508b`. Git
  status snapshot in the system prompt was stale and showed three
  files as modified that were in fact committed — minor noise, called
  out only because it forced a verification round trip.
- Already in context at turn 1 (zero tool calls):
  - `AGENTS.md` (all 410 lines, injected as a workspace rule).
  - Git status snapshot (stale, see above).
  - Today's date, OS, shell, workspace path.
  - One open file: `terminals/6.txt` — the most recently active terminal.
  - The "available skills" list, "open files", "agent transcripts"
    pointers, mcp server list, mode selection guidance.
- Task in the user turn: do another ergonomics review; pay
  particular attention to redundancy and to AGENTS.md being
  mode-blind.

## Orientation cost — what I actually had to fetch

After AGENTS.md was already in context, I read in a single parallel
batch:

| File | Lines | Used for | Honest verdict |
|------|-------|----------|----------------|
| `README.md` | 172 | Confirm user-facing surface; check overlap with AGENTS.md | ~30% useful; the rest was install/quick-start prose I don't need |
| `kb/index.md` | 165 | See what knowledge exists; pick the right hubs | Useful — this is the only orientation read that fully earns its size |
| `kb/log.md` | 1697 | See what happened recently | Massively over-read (see below) |
| `kb/repo-dive-in-map.md` | 1357 | Get a current-shape mental model | Useful as reference, way too big to read for orientation |
| `terminals/6.txt` | 127 | See if recent terminal activity carries context | Zero signal for this task — it's a branch-publishing session unrelated to ergonomics |

Total bytes pulled into context for orientation, **before doing any
work on the actual task**: roughly 3,800 lines + the 410 of AGENTS.md
already injected = ~4,200 lines, of which I'd estimate I used 25-30%.

The two readily-cuttable contributors:

- `kb/log.md` at 1697 lines is read whole because `Read` defaults to
  whole-file. AGENTS.md tells me "the last 5-10 entries" but doesn't
  prescribe how to fetch only those. There are 66 entries in the log;
  the last 10 are ~250 lines.
- `kb/repo-dive-in-map.md` at 1357 lines is essentially "everything
  you might want to know about reading the codebase, rolled into one
  page." kb-preflight already flags it as `oversized-page`. Most of
  it (ring-by-ring read order, full module cross-reference map, all
  entity definitions, "tests as a second reading path", design
  history list) is reference material an agent dips into when going
  deep into one area, not when orienting.

## AGENTS.md is mode-blind, and it shows

The current AGENTS.md is shaped almost entirely around the
brr-daemon-runs-a-task case. Reading it as a Cursor agent, several
sections describe machinery I do not have and will not invoke:

- **Daemon freshness** — describes `sync.refresh_before_task` and the
  seed-ref invariant. I'm not the daemon. I read it, mentally
  bookmark it as "for when I'm reading daemon code", and move on. In
  a smaller context budget I would have spent tokens trying to
  reconcile "Task Context Bundle" with "your task is just text in
  the user turn."
- **Commits** — opens with the universal rule ("commit on the
  current branch unless..."), then immediately drops into
  "When brr's daemon runs the task, every worktree starts on a fresh
  `brr/<task-id>` branch from the seed ref named in the Task Context
  Bundle..." Two paragraphs of daemon-only mechanics that an ad-hoc
  agent has to mentally filter out. The universal advice
  ("one logical commit per task, explain why") is what actually
  applies to me; it's buried in the daemon framing.
- **Session startup** — "If a task is provided, proceed. If
  resuming, continue where the last session left off based on the
  log." Reads cleanly under a brr task spec; reads ambiguously in
  Cursor where "task" is just "the user message I'm replying to."
- **Work re-review** — duplicates Session startup with slightly
  different framing. Both tell me to read `kb/index.md` then
  `kb/log.md`. Pick one.

The Constraints section already notes that universal sections are
"Workflow, Knowledge base, Artifacts, Operating rules, Self-review,
Work re-review, Guardrails, Stewardship" — but the Workflow section
itself mixes universal (Session startup, Task types) and
daemon-only (Daemon freshness, half of Commits) without telling the
reader that distinction. The doc *knows* the universality split
exists at the section level (Constraints names it) but doesn't carry
it through inside Workflow.

## Redundancy across what I had to read

The four orientation reads (README, index, log, dive-in-map) re-state
several facts. A non-exhaustive list, just from this session:

| Fact | README | AGENTS.md | index.md | dive-in-map | Subject hubs / design pages |
|------|--------|-----------|----------|-------------|------------------------------|
| "brr is a playbook + remote execution" | ✓ | ✓ | (implied) | ✓ "one-sentence model" | subject-* preambles |
| Environment policy = `host|worktree|docker|auto` | ✓ | — | (links) | ✓ "Current ownership snapshot" + Ring 3 | subject-envs, envs.md (bundled), design-env-interface |
| No LLM triage; mechanical task construction | — | (oblique) | (links) | ✓ "Current ownership snapshot" + invariant | decision-remove-triage, subject-runs-branching, subject-daemon |
| Stewardship "surface contradictions" | — | ✓ (section) | — | ✓ "Current ownership snapshot" | — |
| Daemon-side freshness invariant | — | ✓ (section) | (links) | ✓ header + Ring 4 + invariant | subject-daemon, design-git-layer-rework |
| KB four-layer model | — | ✓ (table) | (links) | (invariant + dive list) | subject-kb (full synthesis) |

This is not "everything is duplicated"; the cross-linking is
deliberate and useful for graph navigation. But for **orientation
specifically**, the same fact is asserted in 3-5 places at different
levels of detail. The agent reads each one in full because none of
them are short enough to ignore. The result is a tax of several
hundred lines of restated context every session.

The pattern that's *missing* is a "you have 30 seconds; here's what
you need" primer. Today's closest equivalent is the dive-in-map's
"Current ownership snapshot" — which is excellent, but it lives 60
lines into a 1357-line page that the agent has to commit to reading
to find it.

## External unmanaged environment friction

Specific things that bit me, or could bite a less-prepared agent,
running outside brr:

1. **No Task Context Bundle.** AGENTS.md references "the Task
   Context Bundle" in the Commits section as if every agent has
   one. I do not. I had to know to ignore that sentence.
2. **No "Recent in this conversation" injection.** Daemon prompts
   carry recent records into the prompt; Cursor doesn't. I had to
   read `kb/log.md` cold instead.
3. **`terminals/<id>.txt` is shown without explanation.** Cursor
   surfaces the latest terminal as "open file". The terminal in
   question was a git-push housekeeping session that has nothing to
   do with the user's question. An agent treating "open file"
   as a context signal will burn a read on it and then have to
   decide it's irrelevant. The system prompt notes it's a snapshot
   and how to read other terminals; it doesn't say "this is
   ambient, not necessarily relevant."
4. **Git status snapshot was stale.** Three files showed as
   modified that were in fact committed. I verified before
   proceeding, which cost a round trip. The system prompt does
   say "this status is a snapshot in time, and will not update
   during the conversation" — good — but doesn't tell agents to
   re-verify before reasoning about uncommitted work.
5. **No declared "you're in mode X."** The agent has to infer from
   the absence of brr-specific signals (no event id, no `.brr/`
   inbox content alongside the prompt, no Task Context Bundle)
   that it is not running through brr. AGENTS.md could declare the
   modes once and tell each mode which sections apply.

## What I'd actually change

Listed in rough order of leverage. None of these are
multi-day projects; most are AGENTS.md / index restructuring plus a
small kb addition.

### 1. Make AGENTS.md mode-aware up front

Open AGENTS.md with a short "How to read this file" block. Three
modes brr produces this playbook for; for each, name which sections
apply:

| Mode | Trigger | Sections that apply |
|------|---------|---------------------|
| brr daemon | `brr/<task-id>` worktree + Task Context Bundle in prompt | All |
| Direct CLI (`brr run`, `brr init`) | Console invocation; no daemon | Universal + Build/run + Code guidelines; ignore Daemon freshness; "current branch" framing in Commits, not `brr/<task-id>` |
| Ad-hoc agent (Cursor, Codex CLI, Claude Code, etc.) | No event, no bundle, no task id | Universal + Build/run + Code guidelines; ignore Daemon freshness, the Task Context Bundle reference in Commits, and any "task id" framing |

Then, inside the body, mark daemon-only paragraphs explicitly:

- Move the entirety of "Daemon freshness" under a `### Daemon
  freshness (daemon mode only)` heading, or split it into
  `subject-daemon.md` and reduce it to a one-line pointer here.
- Rewrite the Commits section to lead with the universal rule
  ("commit on the current branch, one logical commit, explain why,
  if you wrote files commit them"), then a small
  `> When run by the brr daemon: …` aside. The aside should be one
  paragraph, not three.
- Collapse "Session startup" and "Work re-review" into one section
  ("Orientation"). They duplicate each other and the duplication
  is the source of half the redundancy in this analysis.

### 2. Trim `kb/log.md` reading cost

Three options, in increasing intrusiveness:

- **Cheap (prompt-only).** Change the AGENTS.md guidance from
  "Read kb/log.md for recent activity — the last 5-10 entries"
  to a concrete recipe the agent can execute in one tool call.
  E.g. `Read kb/log.md offset=-300` or `grep -n '^## \[' kb/log.md`
  to find the recent headings and then targeted reads of just
  the relevant entries. Today the agent reads the whole file
  because no tool-level hint says otherwise.
- **Medium.** Add a `kb/log-recent.md` mirror containing only the
  last ~10 entries, regenerated when entries are appended. Agents
  read that for orientation; full `kb/log.md` is for "go back and
  find when X changed."
- **Heavier.** Split the log by quarter or by month into archive
  files. Same goal: the orientation read is always small.

The cheap option is enough for now. It also helps daemon-launched
sessions — `prompts.py` has its own log-budgeting logic, but
prompts that an external agent reads (AGENTS.md) don't.

### 3. Slim the dive-in-map or split it

`kb/repo-dive-in-map.md` is too valuable to delete and too big to
read for orientation. It's already self-flagged as `oversized-page`
by kb-preflight, and the operator has flagged this in
`plan-kb-state-first-maintenance.md` (or its successor). Two
splits worth considering:

- A new `kb/orientation.md` carrying just "Current ownership
  snapshot" + "One-sentence model" + "Spiral reading route → Ring
  0/1" + the practical navigator notes. Maybe 200 lines, ideal as
  the *single* orientation read paired with `kb/index.md`. The
  dive-in-map shrinks to the reference content (full ring
  expansion, entity definitions, cross-reference map, design
  history, maintenance rule).
- Or split the dive-in-map by ring: `repo-dive-ring0.md` …
  `repo-dive-ring5.md` plus an entrypoint. Heavier; only worth it
  if the operator wants the spiral structure to map 1:1 onto
  files.

The first split (orientation-vs-reference) is the higher-leverage
move and matches how the page is actually used in practice — early
sections to orient, later sections to look things up.

### 4. Make redundant facts authoritative in one place each

Pick the canonical home for each repeated fact and shrink the other
mentions to one-line pointers:

- Environment policy table: canonical in `subject-envs.md`.
  README mentions briefly, dive-in-map cross-references, AGENTS.md
  doesn't need to repeat. Today multiple pages re-state the full
  resolution rule.
- "No LLM triage" invariant: canonical in `decision-remove-triage.md`
  and the dive-in-map runtime invariant. Other pages link.
- KB four-layer model: canonical in AGENTS.md (already), with
  `subject-kb.md` linking. Don't restate the table in subject-kb;
  link.
- "What brr is" preamble: canonical in README. Subject pages and
  the dive-in-map can drop their re-summaries to a one-line pointer
  ("This page assumes you know what brr is — see README.md.").

This is normal kb-maintenance work; calling it out so a future pass
has an explicit list to slim.

### 5. Add explicit external-mode hints

A short `kb/agent-modes.md` (or a section in `subject-kb.md` for now)
that names what an ad-hoc agent should ignore: there's no
`.brr/conversations/`, no Task Context Bundle, no
`branch_plan`, no `kb_preflight` invocation on this run. The
agent should still follow the universal sections, still commit
when files change, still update `kb/log.md` if the session
produced a substantive learning. Linking to it from
`kb/index.md` and from AGENTS.md's mode block keeps the rules
discoverable without burying them in the brr-specific
machinery.

### 6. Small system-prompt-side wishlist (Cursor)

These are not brr changes — record them as observed friction so
future sessions don't keep re-discovering them.

- The git-status snapshot should be timestamped or marked stale-on-
  read, not just "snapshot in time".
- "Open files" surfaces `terminals/*.txt` as if it were a regular
  file. A one-line tag like "ambient editor state, may not be
  related to your task" would save a read.
- A "what runtime is hosting me right now" line in the system
  prompt would short-circuit a lot of the AGENTS.md mode inference.
  Today the agent has to deduce it.

## Out of scope (recorded so future passes don't re-derive)

- Replacing AGENTS.md with per-mode files. Tested mentally and
  rejected: the playbook's value is that *one* file is the source
  of truth, copied into adopter repos by `brr init`. Splitting
  produces drift. Mode-awareness inside the file is the better
  shape.
- Auto-generated subject hubs or a `brr docs orient` CLI. Both
  appeared in earlier kb iterations and were explicitly rejected
  in `decision-kb-shape.md` / `subject-kb.md`. Don't reopen
  without new evidence.
- Pre-injecting orientation pages into Cursor's workspace rules
  the way AGENTS.md is injected. Cursor's rule budget is finite;
  the right answer is to make the agent's one extra read cheap and
  obvious, not to pre-load more.

## Headline

The biggest single ergonomic win is making AGENTS.md mode-aware (item
1) and trimming the orientation read budget (items 2 and 3). Those
three together would cut a Cursor-mode session's orientation cost
from ~4,200 lines to ~1,000-1,500 lines, with no loss of useful
context, and would stop ad-hoc agents from mentally filtering
daemon-only machinery on every read.
