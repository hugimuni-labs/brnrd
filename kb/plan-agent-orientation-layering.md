# Agent orientation layering

Status: active — slices 1 and 2 shipped on 2026-05-16; the first
AGENTS.md follow-up cleanup shipped on 2026-05-16 in `ddee9bd`; slice 3 was
**rejected on 2026-05-16** as low ROI (see
[`research-cursor-orientation-ergonomics-followup-2026-05-16.md`](research-cursor-orientation-ergonomics-followup-2026-05-16.md)
Finding 9). The runner-side recent-conversation filtering follow-up shipped
on 2026-05-17; remaining open follow-ups are lower-priority prompt
deduplication and external-host wishlist items.

Synthesis of two same-day ergonomics reviews that converged
independently on the same diagnosis and direction, plus a same-day
follow-up review taken after slices 1+2 shipped, plus a 2026-05-17
daemon-runner review after the first AGENTS.md cleanup landed:

- [`research-runner-orientation-ergonomics-2026-05-16.md`](research-runner-orientation-ergonomics-2026-05-16.md) —
  daemon-launched runner view, Docker env, brr/<task-id> branch.
- [`research-cursor-orientation-ergonomics-2026-05-16.md`](research-cursor-orientation-ergonomics-2026-05-16.md) —
  external Cursor session view, no daemon in the loop.
- [`research-cursor-orientation-ergonomics-followup-2026-05-16.md`](research-cursor-orientation-ergonomics-followup-2026-05-16.md) —
  second-pass Cursor view after slices 1+2 shipped: workspace-rule
  cache staleness, README ↔ AGENTS.md duplication, slice-3 ROI.
- [`research-runner-orientation-ergonomics-2026-05-17.md`](research-runner-orientation-ergonomics-2026-05-17.md) —
  daemon-runner follow-up after AGENTS.md trim / drift guard:
  confirms the Mode block and cold run-context contract are working,
  identifies mechanical `Recent in this conversation` records as
  the next prompt-noise target, and records the 2026-05-17 filtering
  implementation.

Plan supersedes the relevant parts of the older
[`plan-branch-modes.md`](plan-branch-modes.md) note about "AGENTS.md
stays generic, mode-specific overrides via prompt injection" by
making the *stage* axis explicit in the playbook itself instead of
leaving it implicit across prompts and code.

## The layering model

Four layers, each with a distinct job. A runner can identify which
layer owns any given fact without searching.

| Layer | What lives there | Owns |
|-------|------------------|------|
| **Repository contract** | [`src/brr/AGENTS.md`](../src/brr/AGENTS.md) | Project identity, build/test commands, Stewardship, kb schema, commit rules, guardrails, self-review. Universal across every stage and every tool that reads the repo. |
| **Stage overlay** | bundled prompts: [`run.md`](../src/brr/prompts/run.md), [`setup.md`](../src/brr/prompts/setup.md), [`kb-maintenance.md`](../src/brr/prompts/kb-maintenance.md) | What role the runner is playing right now and which base rules narrow or override. Stage = daemon task / kb-maintenance / init-setup. |
| **Runtime state packet** | Task Context Bundle (built by [`prompts._build_task_context_bundle`](../src/brr/prompts.py)) + optional generated run context file ([`run_context.py`](../src/brr/run_context.py)) | Per-task state: stage, source, environment, branch plan, delivery path, recent conversation, runtime recovery paths. Bundle is hot path; context file is recovery detail. |
| **Subject knowledge** | [`kb/index.md`](index.md), [`subject-*.md`](.) hubs, decisions, plans, designs, research | Project knowledge graph: current shape of each area, why decisions were made, what is shipped vs in flight vs paused. |

The important distinction is **stage**, not environment. Docker /
worktree / host change paths, isolation, and available tooling; they
don't change whether the runner is doing a user task, post-task
kb-maintenance, or initial adoption. Stage decides scope and
responsibility; environment decides the runtime substrate.

## Slice 1 — prompt-only, shipped 2026-05-16

Lowest-risk wins; landed first because they reduce daemon-task
tool-call cost without touching the adopter-facing template.

- `_build_task_context_bundle` opens with a `### Mode` block:
  `Stage: brr daemon task`, plus optional `Source`, `Environment`,
  `Delivery`, and `Runtime recovery` lines.
- `daemon.py` threads `task.source` and `task.env` into the bundle
  builder on both the first-attempt and the retry-attempt prompt
  paths.
- [`prompts/run.md`](../src/brr/prompts/run.md) is rewritten to:
  point at the bundle's Mode block as the authoritative "where am
  I?" surface; declare that the prompt-injected
  `Recent Activity (from kb/log.md)` extract together with the
  bundle's `Recent in this conversation` block satisfies the
  AGENTS.md kb/log.md startup step; treat the generated run context
  file as recovery detail rather than routine reading.
- [`run_context.py`](../src/brr/run_context.py) header rewritten to
  match: the bundle is the hot path, this file is for when the
  bundle didn't include what's needed.
- `tests/test_prompts.py` gains a `TestDaemonModeGuardrails` class
  that pins the new run.md anchors so silent prompt drift can't
  quietly undo them, plus three cases over the Mode block (full /
  minimal / recovery-line shape).

## Slice 2 — AGENTS.md restructure, shipped 2026-05-16

Stage-aware template. Universal-vs-daemon split is now explicit
inside the playbook instead of relying on the reader to infer it.

- New "How to read this playbook" section after Project names the
  three stages (ad-hoc agent / brr daemon task / kb-maintenance or
  setup) and tells each one which sections apply. Detection hint:
  presence of `### Mode` in the prompt.
- Workflow rebuilt as Orientation (universal) + Task types + Commits
  (universal) + "When the brr daemon runs you" (daemon-only
  subsection absorbing Daemon freshness, the `brr/<task-id>` commit
  nuance, and the delivery/recovery rules).
- "Work re-review" deleted — it duplicated Session startup. Both
  collapsed into Workflow → Orientation.
- Orientation gives a concrete tail-fetch recipe for `kb/log.md`
  (`Read kb/log.md offset=-300`, or the `grep '^## \[' | tail -10`
  shell form) so agents stop reading 1700 lines of log to satisfy a
  "last 5-10 entries" budget. Daemon-mode runners are told the
  injected extract already covers this step.
- Constraints section updated for the new universal section list
  and to note that the daemon subsection is universal-for-adopters
  too — adopters' playbooks may be read by their own brr daemon
  even if they only run brr by hand.
- `decision-kb-shape.md` gets a small lineage breadcrumb on its
  list of universal sections, pointing at this plan for the new
  shape.

## Slice 3 — regression coverage, rejected 2026-05-16

The pre-ship guess was that a snapshot test for a realistic full
daemon prompt + run context would catch duplication / drift between
the bundle and the run-context file. The follow-up review
([`research-cursor-orientation-ergonomics-followup-2026-05-16.md`](research-cursor-orientation-ergonomics-followup-2026-05-16.md)
Finding 9) re-examined the trade-off and recommends not shipping
it: `tests/test_prompts.py::TestDaemonModeGuardrails` already pins
the load-bearing anchors (Mode block, "injected-extract satisfies
the step" claim, run-context-as-recovery framing), and a snapshot
would freeze ergonomically-good prose into byte equality, taxing
every prompt copy-edit on the cheap iteration loop. Cost outweighs
the catch.

If new duplication classes appear later that the guardrail tests
miss, prefer extending those tests with targeted assertions over a
broad snapshot.

## Shipped follow-up cleanup

- **AGENTS.md canonical-home first target.** Commit `ddee9bd`
  (2026-05-16) trimmed the Project block and Build-and-run section so
  README.md and
  `pyproject.toml` remain the canonical home for product overview and
  detailed install variants. Broader canonical-home cleanup stays
  opportunistic: act when a concrete repeated fact slows a task, not
  as a blanket rewrite.
- **Workspace-rule staleness mitigation and cold-start sanity.**
  Commit `ddee9bd` (2026-05-16) added the top-of-file `Revision:`
  marker plus the ad-hoc sanity-check block for stale workspace rules,
  stale git status snapshots, and ambient terminals / surfaced skills.
- **Recent-conversation filtering.** The 2026-05-17 follow-up keeps
  ordinary daemon prompts focused on semantic conversation memory:
  user events, task branch rows, final done / failed / conflict
  outcomes, and successful or failed push summaries. Heartbeats,
  in-flight progress packets, response artifact paths, and other
  lifecycle plumbing stay in the raw conversation log and the live
  progress projection; daemon-debugging tasks can still inspect the
  raw `.brr/conversations/` files through the run-context recovery
  path when the task explicitly requires it.

## Open follow-ups (not yet sliced)

- **Daemon delivery de-duplication.** Lower priority. Delivery and
  remote path hygiene are load-bearing, so some repetition is useful;
  the next possible trim is making the Task Context Bundle the single
  full daemon delivery contract and shortening the generic run
  preamble only when a bundle is present.
- **Dive-in-map orientation prominence.** The cheap two-halves
  declaration shipped with slice 2 is paying for itself —
  [Finding 8 of the follow-up review](research-cursor-orientation-ergonomics-followup-2026-05-16.md)
  reports an external session stopping after the orientation block
  rather than wading through the reference half. Medium / heavy
  splits stay deferred indefinitely; revisit only if a future
  review surfaces an agent over-reading the map.
- **Cursor-side wishlist** (recorded so future agents don't
  re-discover): timestamp the git-status snapshot, tag
  `terminals/*.txt` as ambient editor state, declare the runtime
  mode in the system prompt, **invalidate the workspace-rule cache
  on file content change** (new from the follow-up review), and
  filter surfaced skills by task domain. Not brr's to ship; logged
  in the cursor research pages.

## What was rejected

- **Watering down AGENTS.md to be "external-tool friendly".** Loses
  the strongest part of the current shape — every tool gets the
  same Stewardship, kb, commit, artifact, and guardrail rules. The
  fix is layering, not removal.
- **Splitting AGENTS.md into per-mode files.** Tested mentally and
  rejected: the playbook's value is that *one* file is the source
  of truth, copied into adopter repos by `brr init`. Splitting
  produces drift. Mode-awareness inside the file is the right
  shape.
- **Environment-as-primary axis.** Docker / worktree / host don't
  change responsibility; stage does. Recorded in the runner review.
- **Auto-generated subject hubs, a `brr docs orient` CLI,
  pre-injecting more orientation pages into Cursor's workspace
  rules.** All appeared in earlier kb iterations and were rejected
  in [`decision-kb-shape.md`](decision-kb-shape.md) /
  [`subject-kb.md`](subject-kb.md). Don't reopen without new
  evidence.
