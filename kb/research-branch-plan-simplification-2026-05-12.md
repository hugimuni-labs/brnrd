# Research: branch plan simplification, 2026-05-12

This research follows the accepted
[`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
implementation and the tasks/branching hub,
[`subject-runs-branching.md`](subject-runs-branching.md). The prompt
from the operator was to re-check whether the branch plan became more
complicated than the job requires, with the goal of simplifying code
while preserving runner-agent economics and the user-facing remote-run
feel.

> Follow-up: the 2026-05-21 publish-kernel collapse
> ([`design-publish-kernel.md`](design-publish-kernel.md)) implemented
> the further simplification this page recommended — `BranchPlan`
> became `PublishPlan`, the local-land step and the metadata triple
> went away, and the `current` fallback was removed alongside.

## Current mechanics

The daemon resolves a `branching.BranchPlan` before environment prep.
The plan carries:

- `seed_ref`, required so worktree/docker runs can create
  `brr/<task-id>` from a stable ref;
- `auto_land_branch`, optional, naming the branch brr may
  fast-forward if the agent stays on the task branch;
- `authority`, `host_context_branch`, `expected_old_oid`, and notes,
  mostly for prompt/status/debug context and fast-forward safety.

Resolution order today is structured event branch fields, then
unambiguous branch facts mined from recent conversation records, then
`branch.fallback` policy (`preserve`, `inbox`, `default`, `current`).
Finalization is intentionally conservative: if the agent stays on the
original task branch and an auto-land target exists, brr fast-forwards
that target with an expected-old-OID guard; otherwise it preserves the
branch. If the agent switches branches, brr preserves that branch as
the agent's runtime decision.

That finalization shape is still doing useful work. It is the part that
protects the host checkout, keeps Docker/worktree runs isolated, avoids
non-fast-forward surprises, and gives the user a predictable status
card.

## What feels heavier than the problem

The branch plan is now carrying two different ideas:

1. **Mechanical git defaults** needed before the runner starts.
2. **Branch intent memory** inferred from append-only conversation
   history.

The first is necessary. The second is where the complexity starts to
look expensive. A broad Telegram or Slack conversation can have a
single recent branch fact that is "unambiguous" only because the recent
window is small, not because the next request truly belongs there.
Using that fact as an auto-land target turns branch continuity into
hidden state. It is safer as runner context unless the source has
explicit structured branch metadata.

The code also still pays for compatibility names from the pre-plan
shape:

- env backends receive both `base_branch` and `branch_plan`;
- `RunContext` stores both `base_branch` and `branch_plan`;
- task/conversation/status/progress paths carry `base_branch` beside
  `seed_ref` and `auto_land_branch`;
- tests must stub the older argument even when the plan is the real
  contract.

This is understandable migration residue, but it makes the model feel
larger than it is. The core rule is only: start on a task branch from a
seed, optionally fast-forward a known target if the agent did not
switch branches.

## Recommended simplification

Keep `BranchPlan`, but shrink its job to **landing defaults**, not
general branch intent detection:

1. Resolve `auto_land_branch` only from explicit structured branch
   input: event metadata now, future PR/issue/task metadata later, and
   any deliberately structured thread metadata a gate may add.
2. Demote inferred conversation branches (`landed_branch`,
   `preserved_branch`, `changed_branch`) from auto-land authority to
   prompt/run-context hints. The worker still sees the prior branch and
   can switch to it after reading the request. That preserves the
   agent-owned runtime model without making stale chat history mutate
   durable refs.
3. Keep `branch.fallback=preserve` as the remote-safe default. Treat
   `current` as an explicit development/compatibility mode. Defer or
   remove `inbox` and `default` until there is a concrete workflow using
   them; they are policy surface, not core machinery.
4. Remove the legacy `base_branch` API/metadata once callers are moved
   to `branch_plan` directly. Progress renderers can display
   `auto_land_branch or seed_ref`; prompts can say "auto-land: none"
   without carrying a second field name.
5. Drop unused plan ornamentation (`display_base`, probably `notes`)
   unless a real status surface depends on it. `authority` can become a
   small `source` string for observability, not something every layer
   needs to understand.

The result keeps the runner economics: one deterministic pre-run
resolver, no LLM triage call, no free-text branch parser, no extra
round trip. It also keeps the UX feel: the status card can still show
`task branch <- seed/target`, the prompt still tells the agent what
will happen if it commits on the default branch, and users still get a
preserved branch when no safe landing target exists.

## Suggested implementation order

1. Change `branching.resolve_branch_plan` so conversation-derived
   durable branches are returned as context hints, not auto-land
   targets. A small companion value such as `recent_branch_context` can
   feed prompts and run context without entering finalization policy.
2. Update `prompts.py` and `run_context.py` to render that hint as
   advisory branch context.
3. Remove `base_branch` from `EnvBackend.prepare`, `RunContext`,
   conversation task records, task metadata compatibility writes, and
   tests.
4. Revisit fallback modes after that cut. If no shipped workflow or
   test needs `inbox`/`default`, delete them and keep only `preserve`
   plus explicit `current`.

The main trade-off is follow-up convenience. Auto-landing to a previous
conversation branch is pleasant when the thread truly is a branch-scoped
work session. The simpler model makes that explicit: the gate/source
can provide structured branch metadata, or the worker can switch after
reading the request. What it avoids is treating incidental recent chat
state as durable branch authority.
