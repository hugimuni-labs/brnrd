# Agent orientation layering

Status: active — slices 1 and 2 shipped on 2026-05-16; slice 3
(snapshot regression test) and follow-up redundancy cleanups still
open.

Synthesis of two same-day ergonomics reviews that converged
independently on the same diagnosis and direction:

- [`research-runner-orientation-ergonomics-2026-05-16.md`](research-runner-orientation-ergonomics-2026-05-16.md) —
  daemon-launched runner view, Docker env, brr/<task-id> branch.
- [`research-cursor-orientation-ergonomics-2026-05-16.md`](research-cursor-orientation-ergonomics-2026-05-16.md) —
  external Cursor session view, no daemon in the loop.

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

## Slice 3 — regression coverage, open

Suggested by both reviews, not yet shipped.

A snapshot-style test for a realistic full daemon prompt plus run
context, so future edits can see at a glance whether orientation
data is duplicated, missing, or stale. The current
`TestDaemonModeGuardrails` pins the right anchors but doesn't catch
new duplication between the bundle and the context file. Owner
flexibility: the test can live in `tests/test_prompts.py` or in a
new `tests/test_daemon_orientation.py`; what matters is that it
reads the bundle and the run-context file from a representative
fixture and asserts that key facts are unique to one or the other.

Skip the snapshot if it would freeze ergonomically-good prose into
brittle byte-equality — the orientation guardrail tests in
`TestDaemonModeGuardrails` already do the load-bearing work.

## Open follow-ups (not yet sliced)

- **Canonical-home cleanup.** Pick one authoritative home per
  repeated fact (environment policy → `subject-envs.md`; "no
  triage" → `decision-remove-triage.md`; KB four-layer model →
  AGENTS.md) and shrink restatements elsewhere to pointers. Both
  reviews flagged this; it's an in-passing chore as the kb is
  touched, not a single dedicated slice.
- **Dive-in-map orientation prominence.** The current
  [`repo-dive-in-map.md`](repo-dive-in-map.md) buries the
  "Current ownership snapshot" — the section most readers actually
  need first — 60 lines into a 1357-line page that's already
  flagged `oversized-page` by preflight. Either elevate that
  block in the page (cheap), split orientation off into its own
  page (medium), or split by ring (heavy). The cheap polish landed
  alongside the kb work; medium / heavy splits remain open if
  ad-hoc agents are still over-reading the map.
- **Cursor-side wishlist** (recorded so future agents don't
  re-discover): timestamp the git-status snapshot, tag
  `terminals/*.txt` as ambient editor state, and declare the
  runtime mode in the system prompt so the agent doesn't have to
  infer it. Not brr's to ship; logged in the cursor research page.

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
