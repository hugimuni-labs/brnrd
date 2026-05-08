# Decision: kb shape — graph topology, semantic memory, cross-tool maintenance

Status: accepted, 2026-05-08.

Supersedes: the implicit "every task writes a `kb/log.md` entry" rule in
[`prompts/run.md`](../src/brr/prompts/run.md) and the per-task log file
mechanism (`kb/log-task-<id>.md`, `RunContext.log_file`,
`WorktreeEnv.prepare`'s log-file plumbing). Reframes the brr-only
[`prompts/kb-maintenance.md`](../src/brr/prompts/kb-maintenance.md)
phase as a redundant safety pass on top of agent-driven maintenance.

Acknowledges
[the 2026-04-28 runner review](agent-ergonomics-evaluation/task-context-bundle-runner-review-2026-04-28.md)
and
[its v2 follow-up](agent-ergonomics-evaluation/task-context-bundle-v2-followup-review-2026-04-28.md) —
both raised "per-task log lifecycle is policy-ambiguous" as a P1 finding.
Aligns the kb design with [`llm-wiki.md`](llm-wiki.md), the framing
this project explicitly takes inspiration from.

## Context

A live test run on 2026-05-07 surfaced the symptom: a Telegram task
"check whether this docker environment looks operational" produced a
substantive review filed at `kb/log-task-1778167445-bz4e.md`, but the
actual chat reply was a one-line acknowledgment ("The background disk
usage task completed — no new information to add"). Nothing was
committed; nothing was pushed. From the user's perspective, brr
returned no answer.

The agent had two competing instructions:

- The Task Context Bundle: *"your final reply is the response — print
  it as your stdout"*.
- AGENTS.md: *"Review/verify/check — read, analyse, report. No commit."*
  combined with *"Write the completion log entry to kb/log.md unless
  task metadata says otherwise"*.

It dutifully wrote the substantive content to a kb log file (per
AGENTS.md) and printed a vacuous acknowledgment as stdout (because
*something* had to be the final reply). The kb log file was not the
deliverable; it was a chore that hijacked the deliverable.

Reading the actual `kb/` against [`llm-wiki.md`](llm-wiki.md) sharpened
the diagnosis. brr's kb is strong on three of the four memory layers
the wiki pattern implies — episodic, decisional, procedural — and
nearly empty on the fourth (semantic). It is *artifact-heavy and
synthesis-light*: a stack of memos under a catalog, with very few
edges between memos. By a quick check, only 6 of 22 kb pages contain
any markdown link to another kb file. The "wiki" is barely a wiki.

This decision records the framework that resolves both — the immediate
chore-removal need *and* the deeper missing semantic layer — and
states what we're choosing not to do.

## The four memory layers

| Layer                | Purpose                                  | Lives in                                                       |
|----------------------|------------------------------------------|----------------------------------------------------------------|
| Raw                  | What was said / what happened, verbatim  | `.brr/conversations/`, `.brr/tasks/`, `.brr/traces/` (gitignored) |
| Episodic-thin        | Curated chronological narrative          | `kb/log.md` (one entry per substantive piece of work)          |
| Semantic + decisional | What we know / why we chose it          | `kb/subject-*.md`, `kb/decision-*.md`, `kb/research-*.md`, `kb/plan-*.md`, `kb/design-*.md` |
| Schema               | How the wiki is structured + how to maintain it | `AGENTS.md`, `src/brr/docs/`, `src/brr/prompts/`        |

The split tracks [`llm-wiki.md`](llm-wiki.md)'s three-layer model
(raw / wiki / schema) with episodic broken out, because chronological
narrative serves a different purpose than synthesised semantic pages
and conflating them is what produced today's per-task log noise.

## Graph topology, not catalog-of-memos

The kb is a graph:

- **Entry point.** `kb/index.md` is the root of navigation.
- **Nodes.** Every committed `.md` file under `kb/`.
- **Edges.** Markdown relative links between nodes. A node with no
  inbound edges is an orphan; a node with no outbound edges is a sink
  (acceptable for terminal artifacts like one-shot research, suspect
  for everything else).
- **Splits and merges are normal operations.** A subject page that
  grows past comfortable reading size splits into a hub plus daughter
  pages. Two related small pages merge when their material is one
  thing.
- **Supersedence is recorded, not deleted.** When a plan ships or a
  decision is reversed, the page stays and gains a `Status: superseded
  by <link> on <date>` marker at the top. The history of why beliefs
  evolved is itself knowledge.
- **Health is edge density and freshness, not page count.** The right
  measure of a healthy kb is whether the cross-references reflect the
  current state of the world.

## What we keep

- `kb/log.md` as a curated narrative — kept committed and human-readable, but no longer mandatory per task. One entry per substantive piece of work; the bar is "would a future agent or human benefit from reading this?".
- `kb/index.md` as graph entry point — but reorganised by subject hubs (see below) instead of artifact type.
- `kb/decision-*.md` for moment-in-time anchoring decisions.
- `kb/research-*.md`, `kb/plan-*.md`, `kb/design-*.md` for in-flight thinking. Plans and designs gain a top-of-page lifecycle marker.
- The existing daemon-side post-task maintenance phase (`_maybe_kb_maintenance`), but reframed as a redundancy check rather than the primary maintenance loop.

## What we drop

- **Mandatory `kb/log.md` per task.** `prompts/run.md` no longer instructs the agent to write a log entry.
- **Per-task log files (`kb/log-task-<id>.md`).** The worktree merge-conflict-avoidance hack falls away once logging isn't mandatory. `RunContext.log_file`, `WorktreeEnv.prepare`'s `log_file=` plumbing, and the bundle's "write your log entry to …" line are all removed.
- **Index organised by artifact type.** "Architecture / Decisions / Design decks / Active design / Ideas / Research / Agent ergonomics evaluations" gets replaced by subject hubs. The artifacts are the same; the navigation is by subject.
- **The "Review/verify/check — no commit" rule.** Replaced by a simpler one: *if you wrote files, commit them; the diff is the receipt*. The current rule is the wrong abstraction — it incentivised the agent to write material content into a place we'd then throw away.
- **`prompts/kb-maintenance.md` as the primary kb maintenance contract.** The primary contract moves to AGENTS.md (see "Cross-tool architecture" below). The daemon-side prompt becomes a thin "redundancy lint" that defers to AGENTS.md.

## What we add

- **Subject pages as the missing semantic layer.** When a major repo subject (envs, gates, daemon loop, conversations, kb itself, runners) accumulates enough material to be worth a hub, a `kb/subject-<name>.md` page is created. It absorbs the synthesis ("what we currently know about X") and links to the relevant decisions, plans, research, reviews. *Subject pages are not pre-seeded by ontology.* They accrete naturally when the next substantial work touches a subject and there is enough material to be worth synthesising.
- **Lifecycle markers on plan/design/decision pages.** Top-of-page line: `Status: <active | superseded by <link> on <YYYY-MM-DD> | abandoned on <YYYY-MM-DD> | accepted on <YYYY-MM-DD>>`. Existing decision pages already use a similar convention; this generalises and applies to plans and designs too.
- **Cross-link discipline.** Every committed kb page (except `index.md`, `log.md`, and subject-hub pages themselves) should link from at least one subject hub or peer page, and should link out to at least one neighbour. Orphans surface in the daemon-side preflight (see below).
- **A deterministic preflight inside the daemon's kb-maintenance phase.** Pure file-system logic, no LLM in the preflight. It scans `kb/` for: (a) pages listed in `index.md` but missing from disk, (b) `.md` files on disk not listed in `index.md`, (c) pages with no inbound graph edges from the index or any subject hub, (d) `plan-*` / `design-*` pages older than ~60 days with no lifecycle marker. The findings get injected into the (now-thin) maintenance LLM prompt for fix-up.

## Cross-tool architecture

The crucial insight: brr is one consumer of the kb, not the only one.
Cursor sessions, Claude Code CLI direct invocations, Codex CLI direct
invocations, and any future agent tool work in the same repo and read
the same `AGENTS.md`. If kb maintenance lives only inside brr's
post-task hook, every other tool produces unmaintained kb growth.

Architecture:

- **AGENTS.md is the schema.** Maintenance rules — the four memory layers, graph topology, subject convention, lifecycle markers, link discipline, what counts as a substantive log entry — all live in AGENTS.md and `src/brr/prompts/agents-template.md` (the seed for adopters). Any agent reading AGENTS.md learns the same rules.
- **brr's daemon hook is a redundancy pass.** `_maybe_kb_maintenance` still fires when `kb/` was touched, but its prompt no longer carries the maintenance logic — it just says "follow the kb maintenance guidance in AGENTS.md as a final lint on the work the previous task did." The deterministic preflight described above feeds it concrete findings.
- **Tool-specific hooks ride on the same schema.** A future Cursor hook (using Cursor's hook surface) can run the same redundancy pass on agent-end events. A pre-commit hook in the repo could do similar. They all reuse the AGENTS.md contract; they're transport, not policy.
- **Tools without a hook surface (Claude Code direct, Codex direct, ad-hoc shell sessions) rely on AGENTS.md alone.** Their agents are expected to maintain the kb during the session, before the user closes the terminal. AGENTS.md is in their working context; this is the same path adopters' agents already take for any other AGENTS.md rule.

This is consistent with the existing project principle (recorded
implicitly across [`decision-drop-streams.md`](decision-drop-streams.md)
and [`decision-remove-triage.md`](decision-remove-triage.md)): the
user-facing CLI surface stays minimal; agent-facing information flows
through prompt injection and the durable schema, not through new
commands. No `brr kb-check`, no `brr kb lint` — those would mix the
user-facing and agent-facing interfaces and create maintenance debt
without clear value.

## Execution plan

Steps below are intended to land in this order. Each phase is small
enough to be one focused commit (or a tight series); none of them is
load-bearing alone.

### Phase 1 — anchor (this page)

This decision page itself. Costs nothing, anchors everything that
follows. Updates `kb/index.md` and `kb/log.md` accordingly.

### Phase 2 — chore removal + bot UX fixes

Single logical change set, because the prompts/template changes
together encode the new contract:

- `src/brr/prompts/run.md` — remove the "write the completion log entry to kb/log.md" instruction; rewrite the second paragraph so stdout is unambiguously the user-visible chat reply, and kb writes are optional and only when material.
- `src/brr/prompts/agents-template.md` — rework Workflow / Commits / Task types / Knowledge base sections per this decision: drop "review = no commit", drop mandatory log entry, add the four-layer model, add the graph topology rule, add the lifecycle-marker convention, add the link-discipline rule.
- `src/brr/runner.py:_build_task_context_bundle` — drop the `log_file` parameter and the corresponding bundle line. Sharpen the Delivery contract: stdout is what the user sees in their chat; don't substitute file paths for the answer.
- `src/brr/envs/__init__.py:WorktreeEnv.prepare` — stop setting `log_file=f"kb/log-{task.id}.md"`. The `RunContext.log_file` field can stay for now as a future hook but goes unused.
- `src/brr/daemon.py` — drop the `log_file=env_ctx.log_file` plumbing in `build_daemon_prompt`.
- `src/brr/gates/telegram.py:render_update` — fix message duplication: cache the last-rendered text in `telegram_progress.json`, short-circuit when text is unchanged; treat Telegram's "message is not modified" 400 as success rather than a fall-through trigger.
- `src/brr/run_progress.py:render_text` — make the compact rendering terser (drop `branch`, `env`, `attempt`, `last`, `response: <path>` rows). Keep the verbose form for `brr status` / `brr inspect`.
- `src/brr/adopt.py:_interactive_configure` — add a Docker question when `docker` is on PATH: bring-your-own image or auto-build from `docker/Dockerfile` and tag locally. On declined, write `environment=worktree` explicitly so the user's choice is recorded.
- Tests across `tests/test_runner.py`, `tests/test_envs.py`, `tests/test_daemon.py`, `tests/test_telegram_render_update.py`, `tests/test_adopt.py`.

### Phase 3 — kb cleanup (one-time hand work)

- Reorganise [`kb/index.md`](index.md) by subject hubs (Envs, Gates, Daemon & runners, Conversations & kb, Fleet & overlays, Agent ergonomics). Pure reshuffle of existing entries; no content rewrite.
- Add lifecycle markers to plan / design pages that have shipped or been superseded: [`plan-branch-modes.md`](plan-branch-modes.md) and [`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md) are largely realised by [`decision-remove-triage.md`](decision-remove-triage.md) and the env work; [`design-env-interface.md`](design-env-interface.md) needs an "implementation status" header refresh.
- Fold any salvageable content from existing `kb/log-task-*.md` files into `kb/log.md` (curated, one entry each); delete the per-task log files.
- Add reciprocal links between obviously connected pages — e.g. [`decision-drop-streams.md`](decision-drop-streams.md) ↔ [`decision-remove-triage.md`](decision-remove-triage.md), both as instances of the same "drop the noisy abstraction" pattern; [`repo-dive-in-map.md`](repo-dive-in-map.md) gets refreshed for the new index shape.

### Phase 4 — daemon maintenance phase becomes safety net

- Rewrite `src/brr/prompts/kb-maintenance.md` as a thin redundancy pass: "you are a final lint after the previous task. Follow the Knowledge base section in AGENTS.md. Below are concrete findings the deterministic preflight produced." Keep the prompt short.
- Add a deterministic preflight inside `src/brr/daemon.py:_maybe_kb_maintenance`: scan for orphan pages, missing index entries, stale lifecycle markers; format the findings; inject into the maintenance prompt. No LLM in the preflight.
- Tests in `tests/test_daemon.py` for the preflight branches.

### Phase 5 — subjects accrete (open-ended, post-decision)

When the next substantial work touches Envs / Gates / Daemon / Conversations / kb-itself, the agent doing that work creates the corresponding subject page and links neighbouring artifacts to it. Don't pre-seed in a separate task — the first material write on a subject earns the page. The decision-page itself counts as the first material write on the kb-as-subject; a `kb/subject-kb.md` may follow naturally if the next kb-touching task wants a hub to point at.

## What this decision deliberately defers

- **A vector / embedding / graph-database semantic layer.** Out of scope for now. The textual subject-page layer is compatible with future per-page embedding indexing; that's a separate project.
- **Cursor / Claude Code / Codex tool-specific hooks.** Useful future work but only valuable once the AGENTS.md schema is stable. Document the pattern in this decision; ship hook recipes later.
- **A `brr kb` CLI sub-namespace.** Explicitly rejected — keep the user-facing surface small and let the schema do the work.
- **Auto-promotion of plan/design pages to subject pages on ship.** Manual marker for now (Phase 3, Phase 5). Automation is premature until we see how this convention behaves in practice.
- **Migration of pre-existing `kb/log-task-*.md` files for adopters.** brr has no users yet; backwards compatibility is not a constraint. brr's own per-task logs are dealt with manually in Phase 3.
- **Conventions for adopters' kb seeds.** The seeds in `src/brr/prompts/` should describe the four-layer model and the link-discipline rule but otherwise stay minimal; an adopter project's first non-trivial task creates its first subject page. We'll re-evaluate after we see brr's own kb evolve under this framework for a few real tasks.

## Notes for future agents

- `kb/log.md` is a *curated* narrative now. If your task didn't produce a meaningful learning, decision, or shipped change, don't add an entry. Forced log entries are the bug we removed.
- If you find yourself wanting a "checklist" page, a "todos for next session" page, or a per-task log file, stop. Those are operational scratch — they belong in `.brr/` (gitignored) or in your task's response, not in `kb/`.
- If you create a new kb page, link it from at least one neighbour and from `kb/index.md` (under the right subject hub). Pages added without inbound links will surface as orphans in the daemon-side preflight.
- Subject pages are not filing categories. They are living synthesis pages. If a subject page consists of three sentences and a list of links, it is worse than no page; either fill it with real synthesis or delete it.
- Lifecycle markers (`Status: superseded by ...`) preserve history. Don't delete plan/design/decision pages when they're outdated — mark them and link to the successor.

## Lineage

- 2026-05-07 chat: live Telegram test surfaced the chore-conflict symptom.
- The conversation transcript walked through (a) why the agent's response was disconnected from the user's question, (b) why nothing was committed or pushed, (c) why per-task log files are policy-ambiguous (echoing the 2026-04-28 reviews' P1 finding), and (d) the cross-tool reality that AGENTS.md — not brr's daemon hook — must own the maintenance contract.
- Earlier groundwork:
  - [`llm-wiki.md`](llm-wiki.md) — the pattern this is grounded in.
  - [`decision-drop-streams.md`](decision-drop-streams.md) — same "drop the noisy abstraction" pattern at runtime layer.
  - [`decision-remove-triage.md`](decision-remove-triage.md) — same pattern at orchestration layer.
- Related but unchanged by this decision:
  - [`design-env-interface.md`](design-env-interface.md) — env durability contract is independent.
  - [`repo-dive-in-map.md`](repo-dive-in-map.md) — will need a refresh in Phase 3 to reflect the new index shape and the absent log mandate.
