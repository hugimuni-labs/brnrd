# Research: agent orientation ergonomics, 2026-05-16

Point-in-time review of how quickly a daemon-launched runner can recover
where it is, what mode it is in, and which surfaces it has to read before
doing useful work. This follows the earlier
[`daemon runner context ergonomics`](research-runner-context-ergonomics-2026-05-09.md)
review, but focuses on redundancy elimination, mode layering, and the
proposal to generalize `AGENTS.md` while adding environment-specific
overrides.

Pairs with
[`research-cursor-orientation-ergonomics-2026-05-16.md`](research-cursor-orientation-ergonomics-2026-05-16.md),
a same-day external-session view of the same problem. The two converged
on the same direction independently, which is what made the synthesis
in [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md)
actionable.

## What this run had to read

The task meaning was clear quickly. The first orientation batch
(`AGENTS.md`, `kb/index.md`, recent `kb/log.md`, and the generated run
context file) was enough to know:

- this was a daemon task, not a local ad-hoc session;
- delivery was stdout captured by brr, with remote-chat path hygiene;
- the task was research/proposal-shaped, so a kb artifact and commit were
  appropriate if the findings were reusable;
- the live environment was Docker, on a preserved `brr/<task-id>` branch
  with no auto-land target.

Understanding the design issue took more reads: the earlier ergonomics
research, `subject-kb.md`, `README.md`, `src/brr/prompts/run.md`,
`src/brr/prompts.py`, `src/brr/run_context.py`, the kb-maintenance prompt,
and the old branch-modes plan section that already proposed
tool-agnostic `AGENTS.md` plus orchestrator overrides.

That is acceptable for a design review, but it exposes the current tax:
the runner needs multiple nearby surfaces because the mode model is
implicit rather than named in one place. `AGENTS.md` gives the universal
rules, the run prompt gives daemon-only delivery and reconsideration
rules, the Task Context Bundle gives branch/delivery metadata, and the
run context file gives environment/source/runtime-file details. The split
is conceptually sound; the handoff between layers is not yet crisp.

## Redundancy map

| Surface | What paid for itself | Redundancy or friction | Recommendation |
| --- | --- | --- | --- |
| `README.md` | Product story, install, quick start, CLI, extension points. | `AGENTS.md` repeats the product summary, zero-dependency fact, install/test commands, and daemon concepts. Some overlap helps self-work, but README-level marketing and usage prose is not agent-critical. | Keep README as user/product documentation. Keep `AGENTS.md` to the short project identity plus operational facts agents need for edits: build/test commands, runtime constraints, package/template hazards. |
| `AGENTS.md` | High-value repo contract: Stewardship, commit discipline, kb shape, guardrails, self-review. | The universal `Workflow` section mixes always-on rules with brr-daemon facts such as daemon freshness and task-branch preservation. External tools can read it, but they must infer which parts apply. | Add a short "How to read this playbook" / "Execution modes" map near the top, then split Workflow into always-on rules and brr-daemon-specific rules. |
| `src/brr/prompts/run.md` | Correctly supplies daemon/run-stage overrides: stdout delivery, optional kb writes, ambiguity stop condition, revisit-signal handling. | It says to read `kb/index.md`, while `AGENTS.md` says to read `kb/log.md`; the daemon prompt already injects recent log activity, so a diligent runner may read the same recent context twice. | In run mode, explicitly say the injected "Recent Activity" block satisfies the `kb/log.md` startup requirement unless more history is needed. |
| Task Context Bundle | The hot-path packet: event/task id, branch plan, response contract, recent conversation, original event body. | It omits the stage/source/environment facts that made the run context file necessary here. It also repeats delivery and original-body material that the run context file repeats again. | Make the bundle the complete hot path: include `Stage: daemon task`, `Source`, `Environment`, and maybe `Execution backend state: see run context if needed`. Treat the context file as recovery detail, not mandatory reading. |
| Generated run context file | Useful cold-path recovery surface: exact host paths, runtime files, env state, container/image details. | It duplicates Task, Delivery, Recent conversation, and Original Event Body from the prompt. It lists raw `.brr/` paths, which is useful but tempts over-reading. | Keep it read-only and explicit: "open when the Task Context Bundle lacks detail or runtime recovery is needed." Do not require it on every daemon task once the bundle carries source/env/stage. |
| `kb-maintenance.md` | Good specialized overlay: limits scope to kb/AGENTS, names current-state failure modes, tells the runner not to continue the original task. | It necessarily repeats AGENTS kb rules. That duplication is useful because maintenance is a different stage with a narrower blast radius. | Keep this duplication; make it fit the same stage-overlay pattern rather than trying to remove it. |

## Critical evaluation of the proposal

The proposal is directionally right, but "environment-specific overrides"
is the wrong primary axis.

`AGENTS.md` should stay the universal repository contract that Cursor,
plain Codex, Claude Code, and brr runners can all read. Making it
"external-tool friendly" by watering it down would lose the strongest
part of the current shape: every tool gets the same Stewardship, kb,
commit, artifact, and guardrail rules.

The missing piece is a named layering model:

1. **Repository contract** — `AGENTS.md`: project constraints, build/test
   commands, stewardship, kb schema, commit rules, guardrails.
2. **Stage overlay** — bundled prompts such as `run.md`, `setup.md`, and
   `kb-maintenance.md`: what role the runner is playing right now and
   which base rules are narrowed or overridden.
3. **Runtime state packet** — Task Context Bundle and optional run
   context file: event id, source, environment backend, branch plan,
   delivery path, recent conversation, runtime recovery paths.
4. **Subject knowledge** — `kb/index.md` plus the relevant subject,
   decision, plan, or research pages.

The important distinction is **stage**, not environment. Docker,
worktree, and host change paths, isolation, and available tooling; they
do not decide whether the runner is doing a user task, setup/adoption, or
post-task kb maintenance. The stage decides scope and responsibility.

There is already a prior design note pointing the same way:
`plan-branch-modes.md` says `AGENTS.md` should stay tool-agnostic while
brr injects mode-specific prompt overrides. This review does not reverse
that decision. It says the repo has partially implemented it, but the
mode/stage boundary should be surfaced more directly to reduce startup
reads and ambiguity.

## Proposed improvements

### 1. Add an execution-mode map to `AGENTS.md`

Near the top, after Project and before Stewardship, add a compact section
like:

```markdown
## How to read this playbook

These rules are the repository contract for any AI tool. If an
orchestrator prompt supplies a narrower stage contract, follow that
contract for the specific points it addresses and keep AGENTS.md for the
base repo rules.

- Local/ad-hoc tool session: use Project, Build and run, Code
  guidelines, Workflow, and Knowledge base. Do not inspect `.brr/`
  unless the task asks.
- brr daemon task: obey the Task Context Bundle for delivery, branch,
  runtime, and `.brr/` access. It overrides generic workflow wording for
  those points.
- brr kb-maintenance stage: touch only `kb/` and `AGENTS.md`, and do not
  continue the user task.
- brr init/setup stage: copy universal sections, rewrite project-specific
  sections from the target repo.
```

This keeps `AGENTS.md` external-tool friendly without making external
tools second-class. The file remains useful on its own, while brr's
runtime prompt can declare when it is narrowing the rules.

### 2. Split AGENTS Workflow into always-on and brr-daemon subsections

The current Workflow section starts with universal startup rules, then
dives into daemon freshness, commits, and task types. That shape makes a
local Cursor/Codex session read daemon details before it knows whether
they matter.

A cleaner shape:

- **Always-on startup:** read `kb/index.md`; read recent `kb/log.md`
  unless the orchestrator already injected recent activity; inspect
  relevant subject pages before changing design.
- **When brr daemon launched you:** seed-ref freshness, branch plan,
  stdout delivery, `.brr/` boundary, preserved task branch, branch rename
  nudge.
- **Task outcome modes:** implement/fix, review/verify, research/plan,
  release/deploy.

This is a restructure, not a behavior change.

### 3. Make the Task Context Bundle the hot path

The bundle should include the fields that determine "where am I?":

```markdown
### Mode
- Stage: daemon task
- Source: telegram
- Environment: docker
- Delivery: stdout captured by brr
- Runtime recovery: run context file at ...
```

With that present, the run prompt can say: open the run context file only
when you need details not already in the bundle, such as exact runtime
paths, container/image metadata, or recovery file locations. That removes
one routine file read from ordinary tasks.

### 4. Let injected recent activity satisfy the log-read requirement

The daemon already injects a capped `Recent Activity (from kb/log.md)`
block before the Task Context Bundle. In daemon mode, that should count
as the startup log read unless the task clearly needs more history. This
keeps `kb/log.md` useful for local sessions without making daemon runners
re-read the same material.

### 5. Keep useful duplication; delete role confusion

Not all duplication is bad:

- The kb-maintenance prompt should repeat kb rules because maintenance is
  a narrow, high-risk stage.
- The daemon delivery contract should repeat stdout instructions because
  one mistake drops the user-visible response.
- The setup prompt should repeat which AGENTS sections are universal
  because that is the adoption algorithm.

The duplication to cut is product/user documentation in AGENTS and
runtime state repeated across prompt and context file. The goal is not
"one fact appears once"; it is "a runner can identify the authoritative
layer for this fact without searching."

## Suggested implementation order

1. Prompt-only low-risk slice:
   - add `Stage`, `Source`, and `Environment` to the Task Context Bundle;
   - update `run.md` to say injected Recent Activity satisfies the log
     startup requirement in daemon mode;
   - clarify that the generated run context file is optional recovery
     detail once the bundle is sufficient.
2. Template-structure slice:
   - add "How to read this playbook" to `AGENTS.md`;
   - split Workflow into always-on and brr-daemon subsections;
   - trim README-like product/install prose from AGENTS where it does
     not affect agent behavior.
3. Regression slice:
   - add a snapshot-style test for a realistic full daemon prompt plus
     run context, so future edits can see whether orientation data is
     duplicated, missing, or stale.

The first slice gives the biggest tool-call reduction. The second slice
is the user-facing design choice and should be reviewed before changing
the template every `brr init` adopter receives.
