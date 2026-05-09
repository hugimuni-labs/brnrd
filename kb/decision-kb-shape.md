# Decision: kb shape — graph topology, semantic memory, cross-tool maintenance

Status: accepted, 2026-05-08. Synthesis of the resulting pattern lives
in the [kb subject hub](subject-kb.md); this page is the point-in-time
record of *why* that pattern.

Supersedes: the implicit "every task writes a `kb/log.md` entry" rule in
[`prompts/run.md`](../src/brr/prompts/run.md) and the per-task log file
mechanism (`kb/log-task-<id>.md`, `RunContext.log_file`,
`WorktreeEnv.prepare`'s log-file plumbing). Reframes the brr-only
[`prompts/kb-maintenance.md`](../src/brr/prompts/kb-maintenance.md)
phase as a redundant safety pass on top of agent-driven maintenance.

Sibling decisions in the same "drop the noisy abstraction" pattern:
[`decision-remove-triage.md`](decision-remove-triage.md) (the LLM
triage stage came off first) and
[`decision-drop-streams.md`](decision-drop-streams.md) (the workstream
manifest came off next).

Triggered by two ergonomics reviews on 2026-04-28 (since slashed in
phase 3b — synthesis lives here and in
[`decision-drop-streams.md`](decision-drop-streams.md)). Both raised
"per-task log lifecycle is policy-ambiguous" as a P1 finding. Aligns
the kb design with [`llm-wiki.md`](llm-wiki.md), the framing this
project explicitly takes inspiration from.

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
- **`src/brr/prompts/agents-template.md` as a separate file.** The template is duplication and a drift risk — brr's own AGENTS.md should *be* the template adopters receive. The file is deleted; brr's `AGENTS.md` (relocated to `src/brr/AGENTS.md`, see "What we add") fills both roles. Pure deletion: brr has no users, no migration concern.

## What we add

- **Subject pages as the missing semantic layer.** When a major repo subject (envs, gates, daemon loop, conversations, kb itself, runners) accumulates enough material to be worth a hub, a `kb/subject-<name>.md` page is created. It absorbs the synthesis ("what we currently know about X") and links to the relevant decisions, plans, research, reviews. *Subject pages are not pre-seeded by ontology.* They accrete naturally when the next substantial work touches a subject and there is enough material to be worth synthesising. See "Subject genesis" below for the formation rule.
- **Lifecycle markers on plan/design/decision pages.** Top-of-page line: `Status: <active | superseded by <link> on <YYYY-MM-DD> | abandoned on <YYYY-MM-DD> | accepted on <YYYY-MM-DD>>`. Existing decision pages already use a similar convention; this generalises and applies to plans and designs too.
- **Cross-link discipline.** Every committed kb page (except `index.md`, `log.md`, and subject-hub pages themselves) should link from at least one subject hub or peer page, and should link out to at least one neighbour. Orphans surface in the daemon-side preflight (see below).
- **A deterministic preflight inside the daemon's kb-maintenance phase.** Pure file-system logic, no LLM in the preflight. It scans `kb/` for: (a) pages listed in `index.md` but missing from disk, (b) `.md` files on disk not listed in `index.md`, (c) pages with no inbound graph edges from the index or any subject hub, (d) `plan-*` / `design-*` pages older than ~60 days with no lifecycle marker. The findings get injected into the (now-thin) maintenance LLM prompt for fix-up.
- **AGENTS.md as a single source.** brr's own playbook *is* the template adopters receive. `AGENTS.md` lives at `src/brr/AGENTS.md` (bundled with the package), with a symlink at brr's repo root for tool conventions. Universal sections (Workflow, Knowledge base, Artifacts, Operating rules, Self-review, Work re-review, Guardrails, Stewardship) copy verbatim during `brr init`; project-specific sections (Project, Build and run, Code guidelines, Constraints) are rewritten by the setup agent against the adopter's repo. Section identity is by name, as `prompts/setup.md` already enumerates. No second template file; whatever kb conventions brr adopts for itself, adopters automatically inherit.

## Subject genesis

The structural rules above describe what to do with a subject page once it exists. The corresponding rule for *when* one comes into being:

> When work in some area touches knowledge that does not yet have a subject page, *and* the agent's current work plus the existing related material is enough to make a useful hub today, create the subject page as part of the current work. Otherwise, file the material under the existing artifact types (`research-*`, `plan-*`, `design-*`, `decision-*`) and let those serve as the in-flight material for a future subject page.

The artifact types already in `kb/` (`research-*`, `plan-*`, `design-*`, `decision-*`) function as *seedlings*. A `plan-*.md` covering an area without a subject page is exactly the kind of material a subject page later absorbs. Naming a separate "seedling" artifact type would add a graduation lifecycle (seedling → subject) without buying anything those artifact types don't already provide.

What to avoid:

- **Pre-seeding subject pages from a top-down ontology.** Filling `subject-envs.md`, `subject-gates.md`, `subject-runners.md` etc. with three sentences and a TODO list is the pattern of "empty hub"; worse than no page.
- **Sitting on real semantic material in a `plan-*` or `research-*` page when it has clearly outgrown that frame.** If a plan page has shipped (lifecycle marker says superseded) and its semantic content is the actual canonical knowledge for an area, the next agent touching the area should promote it: rename or re-file as `subject-<name>.md`, add the supersedence note pointing at the successor, and link adjacent material.

The honest test for "should I create a subject page now?" is: *Could a future agent or human, opening this page cold, learn the canonical shape of this area from it today?* Three sentences is rarely enough; a two-paragraph synthesis plus links to the relevant decisions and plans usually is.

## Cross-tool architecture

The crucial insight: brr is one consumer of the kb, not the only one.
Cursor sessions, Claude Code CLI direct invocations, Codex CLI direct
invocations, and any future agent tool work in the same repo and read
the same `AGENTS.md`. If kb maintenance lives only inside brr's
post-task hook, every other tool produces unmaintained kb growth.

Architecture:

- **AGENTS.md is the schema.** Maintenance rules — the four memory layers, graph topology, subject convention, lifecycle markers, link discipline, what counts as a substantive log entry — all live in `src/brr/AGENTS.md` (bundled with the package; symlinked at brr's repo root for tool conventions). It is brr's own playbook *and* the template seeded into adopters' repos by `brr init`. There is no separate `agents-template.md`. Any agent reading AGENTS.md — brr's, an adopter's, an ad-hoc Cursor session in either — learns the same rules.
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

## Portability

The pattern this decision encodes is not brr-specific. The four memory
layers, the graph topology, the subject-page convention, the
lifecycle-marker and link-discipline rules, and the maintenance
contract describe a way of organising LLM-maintained knowledge that
any repo-shaped project can adopt — with or without brr.

What is portable today, with no work:

- **The schema text.** The AGENTS.md "Knowledge base" section (Phase 2)
  and this framework doc. Pure markdown.
- **The bundled reference doc** (`src/brr/docs/kb-shape.md`, added in
  Phase 2). Tool-agnostic explainer; adopters' agents can read it as
  background and non-brr users can copy it.
- **The seed scaffolding** (`kb/index.md`, `kb/log.md` templates).
  Content-only.

What stays brr-specific:

- The daemon-side redundancy phase (`_maybe_kb_maintenance` and the
  hook plumbing that feeds it). Exists because brr has that hook.
- `.brr/conversations/` as the raw layer. brr's runtime state.
- AGENTS.md template assembly during `brr init`. Specific to brr's
  bootstrap path.

The deterministic preflight (Phase 4) is the natural extraction
candidate. It will be implemented as its own module
(`src/brr/kb_preflight.py`) with a brr-independent API:
`preflight(kb_dir: Path) -> list[Finding]`. No imports from the rest
of brr; pure-stdlib like every other module. The bundled
`src/brr/docs/kb-shape.md` will be written in tool-agnostic terms (the
pattern, not brr's implementation of it). The AGENTS.md kb section
will be the same.

The trigger for actual extraction is a second consumer with non-trivial
logic. Three concrete shapes the second consumer is likely to take
(recorded so the implementation stays factored to support them, not
committed for now):

- **Pre-commit hook.** A small wrapper script (`python -m brr.kb_preflight kb/`) wired into `.pre-commit-config.yaml`, runs the deterministic preflight on every commit that touches `kb/`. Adopters who don't run brr's daemon still get orphan / staleness checks. Maybe ten lines of glue plus the existing `kb_preflight` module. This is the cleanest first extraction trigger because pre-commit users explicitly opt in and the surface is well-understood.
- **Cursor hook recipe.** Same preflight, dispatched from a Cursor agent-end hook, surfacing findings via Cursor's notification surface. Slightly more glue (Cursor hook config) but no new module.
- **Standalone usage.** An adopter copies `src/brr/AGENTS.md`, `src/brr/docs/kb-shape.md`, and `src/brr/kb_preflight.py` into their repo (or installs a future `kbgrove`-shaped package), without installing brr's daemon. The pattern works.

Until one of these becomes a real ask, the cost of speculative packaging
exceeds the benefit. The decision is: **factor the implementation so
extraction is a 30-minute move, but do not extract until a second
consumer materialises**.

## Execution plan

Steps below are intended to land in this order. Each phase is small
enough to be one focused commit (or a tight series); none of them is
load-bearing alone.

### Phase 1 — anchor (this page)

This decision page itself. Costs nothing, anchors everything that
follows. Updates `kb/index.md` and `kb/log.md` accordingly.

### Phase 2 — AGENTS.md restructure + chore removal + bot UX + Docker init-i

Largest phase; lands the new contract end-to-end. Sub-steps below; closely related and want to land together (or as a tight series of commits) since the playbook and the runtime contract together encode the new behaviour. **No backwards compatibility** — delete obsoleted code, prompts, and tests rather than preserving them. brr has no users; carrying old shape costs more than it saves.

**AGENTS.md as a single source:**

- Move repo-root `AGENTS.md` to `src/brr/AGENTS.md`. Update `pyproject.toml` to bundle as package data.
- Add a symlink at repo root: `AGENTS.md → src/brr/AGENTS.md`. brr is already Unix-only (signal.SIGALRM dependency); the symlink works on the supported platforms. Tool convention preserved: cursor / claude code / codex find `AGENTS.md` at root and follow the symlink.
- Delete `src/brr/prompts/agents-template.md` and any tests / fixtures that referenced its existence.
- Rewrite `src/brr/AGENTS.md`'s universal sections (Workflow, Knowledge base, Artifacts, Operating rules, Self-review, Work re-review, Guardrails, Stewardship) per this decision: drop "review = no commit", drop mandatory log entry, add the four-layer model, add the graph topology rule, add the lifecycle-marker convention, add the link-discipline rule, add the subject-genesis rule. brr-specific sections (Project, Build and run, Code guidelines, Constraints) keep their content; adopters' setup agent rewrites those for their repo.
- Update `src/brr/prompts/setup.md` to reference the bundled AGENTS.md ("use brr's own AGENTS.md as the model") and keep the explicit enumeration of universal-vs-project-specific sections.
- Update `src/brr/runner.py:build_init_prompt` to read `src/brr/AGENTS.md` instead of the (now deleted) `prompts/agents-template.md`.

**Chore removal (the immediate symptom from the 2026-05-07 test):**

- `src/brr/prompts/run.md` — remove the "write the completion log entry to kb/log.md" instruction; rewrite the second paragraph so stdout is unambiguously the user-visible chat reply, and kb writes are optional and only when material.
- `src/brr/runner.py:_build_task_context_bundle` — drop the `log_file` parameter and the corresponding bundle line. Sharpen the Delivery contract: stdout is what the user sees in their chat; don't substitute file paths for the answer.
- `src/brr/envs/__init__.py:WorktreeEnv.prepare` — stop setting `log_file=f"kb/log-{task.id}.md"`. **Remove the `RunContext.log_file` field entirely** — no future hook, slash it (we can re-add a clean field later if a real need emerges).
- `src/brr/daemon.py` — drop the `log_file=env_ctx.log_file` plumbing in `build_daemon_prompt`.
- Tests — delete cases asserting per-task log file paths and the `log_file` field; rewrite cases asserting old "review = no commit" behaviour; delete cases asserting the existence of `agents-template.md`.

**Bot UX:**

- `src/brr/gates/telegram.py:render_update` — fix message duplication: cache the last-rendered text in `telegram_progress.json`, short-circuit when text is unchanged; treat Telegram's "message is not modified" 400 as success rather than a fall-through trigger.
- `src/brr/run_progress.py:render_text` — make the compact rendering terser (drop `branch`, `env`, `attempt`, `last`, `response: <path>` rows). Keep the verbose form for `brr status` / `brr inspect`.

**Docker init-i:**

- `src/brr/adopt.py:_interactive_configure` — add a Docker question when `docker` is on PATH: bring-your-own image or auto-build from `docker/Dockerfile` and tag locally. On declined, write `environment=worktree` explicitly so the user's choice is recorded (rather than leaving `environment=auto` and hoping detection works).

Test coverage across `tests/test_runner.py`, `tests/test_envs.py`, `tests/test_daemon.py`, `tests/test_telegram_render_update.py`, `tests/test_run_progress.py`, `tests/test_adopt.py`. Tests that no longer make sense — gone, not preserved.

### Phase 3 — kb cleanup (one-time hand work)

The bar throughout this phase: *would a future agent or human, reading this page cold, learn something useful that isn't recorded elsewhere?* If no, slash. Supersedence with lifecycle markers is for material whose history-of-change is itself knowledge; deletion is for noise.

- Reorganise [`kb/index.md`](index.md) by subject hubs (Envs, Gates, Daemon & runners, Conversations & kb, Fleet & overlays, Agent ergonomics). Pure reshuffle of existing entries at this step; no content rewrite.
- Add lifecycle markers to plan / design pages that have shipped or been superseded: [`plan-branch-modes.md`](plan-branch-modes.md) and [`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md) are largely realised by [`decision-remove-triage.md`](decision-remove-triage.md) and the env work; [`design-env-interface.md`](design-env-interface.md) needs an "implementation status" header refresh.
- **Per-task log files (`kb/log-task-*.md`).** Where the content is a synthesis worth preserving, fold one curated entry into `kb/log.md`. Otherwise delete; they were transient operational scratch from when logging was mandatory and worktrees needed merge-conflict-avoidance.
- **Slash pages with no future value.** Pages already explicitly absorbed into successors (the personal-workflow-variants idea page, marked "absorbed" into the fleet deck), reviews whose findings are fully addressed and add no synthesis (the PR #1 review, the 2026-04-14 concurrency follow-up review, the 2026-04-28 ergonomics reviews), and standalone notes that no longer relate to live work — delete rather than mark superseded. Use a brief `kb/log.md` entry to record what was deleted and why.
- Add reciprocal links between obviously connected pages — e.g. [`decision-drop-streams.md`](decision-drop-streams.md) ↔ [`decision-remove-triage.md`](decision-remove-triage.md) ↔ this decision, all instances of the same "drop the noisy abstraction" pattern; [`repo-dive-in-map.md`](repo-dive-in-map.md) gets refreshed for the new index shape (subject hubs) and the absent log mandate.

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
- **Conventions for adopters' kb seeds beyond the AGENTS.md schema.** The bundled `src/brr/AGENTS.md` carries the four-layer model and the link-discipline rule; `prompts/kb-index.md` and `prompts/kb-log.md` are minimal scaffolding. We're not pre-seeding `subject-*.md` for adopters — their first non-trivial task creates their first subject page, the same way brr's own kb evolves. Re-evaluate after we see this framework run on real tasks for a while.

## Notes for future agents

- `kb/log.md` is a *curated* narrative now. If your task didn't produce a meaningful learning, decision, or shipped change, don't add an entry. Forced log entries are the bug we removed.
- If you find yourself wanting a "checklist" page, a "todos for next session" page, or a per-task log file, stop. Those are operational scratch — they belong in `.brr/` (gitignored) or in your task's response, not in `kb/`.
- If you create a new kb page, link it from at least one neighbour and from `kb/index.md` (under the right subject hub). Pages added without inbound links will surface as orphans in the daemon-side preflight.
- Subject pages are not filing categories. They are living synthesis pages. If a subject page consists of three sentences and a list of links, it is worse than no page; either fill it with real synthesis or delete it.
- **Lifecycle markers preserve history *when the historical record matters*.** Mark and link in cases where the why-this-changed is itself knowledge. **Delete** when the page was operational scratch, an idea fully absorbed by a successor with no provenance worth preserving, or a review whose findings are addressed and no longer actionable. Slash with confidence; the kb is healthier without dead weight.
- **Don't preserve stale shape for its own sake.** Tests testing removed behaviour should be removed. Pages no longer adding value should be deleted. Code paths existing for backwards compat we don't actually need should be slashed. brr's small size and clarity is the value; leftover scaffolding erodes it.

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
