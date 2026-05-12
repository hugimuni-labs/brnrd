# Plan: state-first kb maintenance and regular grooming

Status: active

The current kb-shape decision made the knowledge base much healthier:
it removed mandatory per-task logs, added subject hubs, made `AGENTS.md`
the shared schema, and put deterministic preflight in the daemon. The
remaining problem is subtler: the kb still tends to preserve the full
path of change inside the current reading surface. A cold agent can
open a page to learn "what is true now" and instead spend context on
revision blocks, superseded implementation details, and old wording
that only matters if someone is auditing history.

The target is not "forget history". The target is:

- current kb pages describe the state of the project now;
- a short breadcrumb explains when and why the state changed;
- deep history lives in git history, `kb/log.md`, and older commits;
- maintenance is a regular first-class task, not only a post-task
  safety lint that depends on the current agent remembering to groom.

This plan refines, but does not yet replace, the accepted
[`decision-kb-shape.md`](decision-kb-shape.md). The conflict is
intentional and called out below.

## Current shape

Today there are three maintenance paths:

1. **Agent discipline.** Every runner reads [`AGENTS.md`](../AGENTS.md)
   and should update the kb when work changes durable project knowledge.
2. **Daemon preflight.** `kb_preflight.scan` checks structural facts:
   pages missing from the index, stale index entries, and broken
   relative links outside `kb/log.md`.
3. **Post-task LLM redundancy pass.** `_maybe_kb_maintenance` runs
   after a successful daemon task when `kb/` changed or preflight found
   something.

The first two are still right. The third is where the contract is weak:
the pass runs after the user's response has already been captured, its
prompt says not to commit, and finalization only preserves dirty
worktrees rather than turning maintenance edits into a durable commit.
So the pass can notice problems, but any edits it makes are not a clean,
visible unit of work. That matches the operator symptom: kb cleanup
"doesn't happen really."

The kb itself also shows the state/history tension:

- [`subject-kb.md`](subject-kb.md) already says pages with fully
  absorbed findings should be slashed and git history is the receipt.
- [`decision-kb-shape.md`](decision-kb-shape.md) also says
  supersedence is recorded and that "the history of why beliefs evolved
  is itself knowledge."
- [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
  is a live example of the uneasy compromise: a current amendment sits
  on top, but the body still carries superseded resolver steps and old
  push behavior.
- [`kb/log.md`](log.md) is injected into prompts by entry count rather
  than by budget. The last 10 entries can still dominate the prompt
  when recent work was verbose.

## Policy refinement

Adopt this split:

| Page kind | Current-state rule | Historical breadcrumb |
| --- | --- | --- |
| Subject hub | Canonical state of the area today. Avoid revision logs and old implementation tours. | One compact "Lineage" or "Changed by" paragraph linking to current decisions, log entries, or `git log -- <path>`. |
| Decision | Current accepted decision and the reasoning still needed to understand it. If reversed, mark superseded and point at the successor. | Keep the key alternatives and why the chosen path won. Do not keep every later implementation delta inline. |
| Plan/design | Active plans describe intended work. Shipped plans become receipts only when the plan's reasoning is still useful. | If the useful knowledge has moved to a subject hub, mark the plan shipped/superseded and compress or delete it. |
| Research | Point-in-time findings. Keep when they answer a reusable question; delete when absorbed and no longer useful. | Link to the subject or decision that absorbed it. |
| Log | Chronological narrative, not a design database. | Keep entries short enough to be prompt context; deep detail belongs in the committed page or git diff. |

The main wording change for `AGENTS.md` should be:

> The kb is a current-state synthesis layer. Preserve enough lineage to
> explain why the current state is shaped this way, but rely on git
> history for deep history. Do not rewrite a page into "old behavior"
> prose unless the old behavior is still needed to understand a live
> constraint.

This narrows the existing lifecycle-marker rule rather than removing
it. Lifecycle markers still matter; they should point away from stale
material, not license a page to become an append-only changelog.

## Conflicting paths

1. **Supersedence vs deletion.** The accepted kb-shape decision says
   supersedence should be recorded, while the desired state-first rule
   says many details should move to git history. Resolution: keep
   supersedence markers for decisions and plans whose *reasoning*
   remains useful; delete or compress operational scratch, fully
   absorbed reviews, and implementation deltas that only repeat the
   diff.

2. **Proactive work vs brr's small daemon.** Letting brr initiate
   maintenance tasks conflicts with the current "daemon drains user
   events" mental model and can spend runner time unexpectedly.
   Resolution: make proactive maintenance explicit configuration,
   idle-only, and visible as normal `source=brr-maintenance` tasks on
   normal task branches. Do not let hidden background edits mutate
   `main`.

3. **Post-task LLM edits vs commit discipline.** The current
   kb-maintenance prompt says not to commit, but brr has no auto-commit
   step for those edits. Resolution: demote post-task maintenance to a
   deterministic scan plus scheduling trigger, or make it a first-class
   maintenance task with the normal "write files, commit them" contract.
   Do not keep the current in-between state.

4. **Cursor gap.** A daemon scheduler only helps while `brr up` is
   running. Cursor and direct CLI sessions can still leave stale kb.
   Resolution: keep `AGENTS.md` as the primary schema, add an optional
   repo hook or command later, and use scheduled daemon tasks as a
   safety net rather than the only maintenance path.

5. **Semantic grooming vs deterministic checks.** A scanner can prove
   broken links; it cannot prove that a page is "too historical".
   Resolution: add deterministic budget/advisory signals, then hand
   those to a first-class maintenance task for judgement.

6. **Git history reliance vs shallow or rewritten repos.** Some repos
   may not have deep local history. Resolution: breadcrumbs must be
   human-usable without perfect history: name the date, successor page,
   and reason in one paragraph. Git gives the full trail when present.

7. **Prompt context vs useful continuity.** Cutting log injection too
   hard can make agents lose recent decisions; leaving it entry-count
   based lets verbose entries wash out the task. Resolution: keep recent
   activity, but enforce a byte/token budget and make log entries more
   summary-like.

## Implementation plan

### Phase 1 - schema and prompt budget

- Rewrite the `AGENTS.md` kb section around "current-state synthesis
  plus breadcrumbs". Keep the four memory layers and graph topology.
- Tighten `kb/log.md` guidance: entries should summarize the durable
  learning/change, not narrate every file touched. Use links for detail.
- Change `prompts._read_recent_log` from "last 10 entries" to a bounded
  budget: read newest entries until a byte limit is reached, then stop.
  Keep the entry-count cap as a secondary guard.
- Update `src/brr/prompts/run.md` to remind agents that deep history is
  in git and that kb pages should stay focused on the current shape.
- Update `src/brr/prompts/setup.md` / kb seeds only if the adopter
  guidance needs to mention the state-first rule.

Tests: prompt tests for log budget behavior; setup/init prompt tests if
seed text changes.

### Phase 2 - one focused kb grooming pass

Run a manual cleanup over brr's own kb before adding more automation.
Use this classification per page:

- **Canonical current page**: rewrite to current state plus compact
  lineage. Candidates: `subject-kb.md`,
  `subject-tasks-branching.md`, `subject-daemon.md`,
  `repo-dive-in-map.md`.
- **Decision receipt**: preserve the stable decision and useful
  alternatives, but compress later implementation notes. Candidates:
  `decision-kb-shape.md`, `design-daemon-landing-branch.md`,
  `decision-remove-triage.md`, `decision-drop-streams.md`.
- **Archive or slash**: if a plan/design/research page has been fully
  absorbed and no longer answers a reusable question, delete it with a
  log breadcrumb. If it remains useful, mark it clearly as shipped,
  superseded, blocked, or paused.

Pilot the cleanup on `design-daemon-landing-branch.md`: keep the
current branch contract, a short lineage paragraph, the rejected
alternatives still relevant today, and a pointer to
`git log -- kb/design-daemon-landing-branch.md` / the 2026-05-12 log
entries for the detailed evolution. The current body should not make a
cold reader wade through the removed conversation-mining design to
understand today's resolver.

Tests: run `kb_preflight.scan(Path("."))`; no pytest needed unless docs
or prompt behavior changed in the same commit.

### Phase 3 - deterministic health signals

Extend deterministic scanning without pretending it can judge semantics:

- keep existing structural findings;
- add advisory findings for oversized active pages, oversized injected
  recent-log context, missing top-of-page status on plan/design/deck
  pages, and "revision-history-heavy" pages;
- include severity (`error` for broken structure, `advisory` for
  grooming prompts) so gates/status can distinguish hard drift from
  cleanup suggestions.

This can stay in `kb_preflight.py` if the `Finding` shape grows
severity/kind, or split into `kb_health.py` if the advisory checks make
the structural preflight muddy. Prefer the split if the wording starts
to look like policy rather than file-system facts.

Tests: new scanner tests for severity ordering, log-budget advisory,
large-page advisory, and status-marker detection.

### Phase 4 - maintenance as a first-class task

Replace the "LLM edit after another task" shape with normal task
execution:

- Add a bundled prompt such as `src/brr/prompts/repo-maintenance.md`.
  Its job is repo grooming: kb current-state cleanup first, docs/schema
  drift second, no product-code changes unless the maintenance tooling
  itself is being fixed.
- Add daemon-owned runtime state under `.brr/maintenance.json`:
  last run time, last scanned commit, last task id, last result, and
  whether a maintenance event is already pending/running.
- Add config, defaulting conservatively:
  - `maintenance.enabled=false` initially;
  - `maintenance.interval_days=7`;
  - `maintenance.source=brr-maintenance`;
  - `maintenance.autoland=` empty by default, so normal branch
    preservation applies unless the operator opts in.
- At a quiescent boundary, if enabled and due, run the deterministic
  health scan. If there are findings or the repo has advanced since the
  last maintenance run, enqueue one internal event with
  `source=brr-maintenance` and the maintenance prompt body.
- Process that event through the same `_run_worker` path as user work.
  It gets a task branch, response file, trace handling, finalization,
  push behavior, and commit discipline like every other task.
- Suppress gate delivery by default. The task and response remain
  inspectable locally; add notification later only if operators want
  it.

This is deliberately broader than the rejected `brr kb` CLI
subnamespace. It is not a user-facing pile of kb verbs; it is a normal
daemon-initiated maintenance task using the same task substrate.

Tests: daemon scheduler unit tests for due/not-due, pending user events
winning over maintenance, duplicate prevention, disabled default, and
normal branch preservation.

### Phase 5 - retire or narrow post-task LLM maintenance

Once first-class maintenance tasks exist:

- keep deterministic preflight after successful tasks;
- stop running an editing LLM pass inline after the user's response;
- if preflight finds hard structural issues, record the findings on the
  task and make the scheduler due soon;
- optionally make `kb_maintenance=always` mean "schedule a maintenance
  task after every successful task" rather than "run hidden edits in the
  just-finished worktree".

This removes the current false contract where an LLM can edit files
after the response but before finalization without creating a durable
commit.

## Open decisions

- **Default for proactive maintenance.** Recommended: default off for
  adopters, explicit opt-in during `brr init -i` or in `.brr/config`.
  brr's own repo can opt in early as the proving ground.
- **Autoland target.** Recommended: no autoland by default. Preserve
  maintenance branches unless `maintenance.autoland=<branch>` is set.
- **How much history stays in decisions.** Recommended: keep rationale
  and rejected alternatives that constrain future work; move detailed
  implementation evolution to git/log breadcrumbs.
- **Whether to add a manual command.** Recommended: defer. A scheduler
  plus `brr run "<maintenance prompt>"` covers the first need without a
  new CLI namespace.
- **Cursor integration.** Recommended: do not build first. After the
  state-first schema lands, add a documented optional hook/recipe that
  runs the same deterministic health scan outside the daemon.

## Acceptance criteria

- A cold agent can read `kb/index.md`, the relevant subject hub, and the
  last recent-log context without spending most of its prompt on old
  implementation history.
- Pages that describe live areas open with the current shape, not a
  revision narrative.
- Superseded or deleted material leaves a breadcrumb: date, successor
  page or log entry, and enough reason to know where to look in git.
- Any automated maintenance that writes files is a normal task with a
  commit on a normal branch.
- The daemon never silently mutates `main` as part of proactive
  maintenance.
