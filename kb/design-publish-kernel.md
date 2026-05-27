# Design: publish kernel

Status: accepted on 2026-05-21

This hangs off the tasks/branching hub
([`subject-tasks-branching.md`](subject-tasks-branching.md)) and
supersedes [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md).
It collapses the daemon's post-run "land + push" pipeline around a
single kernel.

## Why this exists

The previous shape carried three concerns through three layers:

- **Pre-run plan.** `branching.BranchPlan` named a seed ref and an
  `auto_land_branch`, plus a dual-role `expected_old_oid` that meant
  "anchor a local concurrency window" for the local-land path *and*
  "anchor a force-with-lease push" for the rebase-push path.
- **Post-run local ref bookkeeping.** `WorktreeEnv._land_or_preserve`
  ran a 5-way decision tree (detached / no commits / agent switched
  branches / ff onto auto-land / preserve task branch), called
  `gitops.fast_forward_branch` to advance the auto-land target locally,
  and reached for `gitops.advance_branch_with_anchor` on the narrow
  PR-rebase-onto-target case.
- **Push.** `_push_if_needed` + `_push_lease_anchor` +
  `_needs_force_with_lease` + `_push_command` then re-derived whether
  the push needed a lease, with rules that quietly assumed the
  finalize step had already updated the local ref.

The metadata triple `preserved_branch` / `landed_branch` /
`changed_branch` mirrored the same split out to every renderer
(`run_progress.py`, `run_context.py`, `prompts.py`, `conversations.py`,
`gates/github/`, the daemon's `done` packet). Six readers had to
agree on which field meant "the branch to talk about" for each
outcome.

## Kernel

The agent leaves work on a branch. That branch is the unit of
publication. The daemon publishes it. Pull-side freshness lives in
`sync.py`; publishing is a single step.

This collapses the local ref bookkeeping for non-agent branches
entirely — the only local branch the daemon ever writes from the
publish path is the agent's own task branch, via the runner's normal
git operations inside the worktree.

## Resolver

`branching.resolve_publish_plan` returns a `PublishPlan`:

```python
@dataclass(frozen=True)
class PublishPlan:
    seed_ref: str
    expected_publish_branch: str | None
    source: str
    host_context_branch: str | None
    expected_remote_oid: str | None = None
```

Field semantics:

- `seed_ref` — required, used to sprout `brr/<task-id>`.
- `expected_publish_branch` — the branch the daemon expects the agent
  to publish under, when the event named one. The agent can still
  switch branches inside the worktree; the daemon publishes whichever
  branch HEAD ends up on.
- `expected_remote_oid` — unambiguously the **remote** lease anchor
  for `--force-with-lease`. Never a local-ref concurrency anchor.
- `source` — observability string (e.g. `event:target_branch`,
  `fallback:preserve`).
- `host_context_branch` — prompt context only.

Resolution order is unchanged from the prior design:

1. Structured event branch field
   (`branch_target` / `target_branch` / `base_branch` / legacy
   `branch`). When the event names a target, the plan seeds from
   `<remote>/<target>` if present, so the worker sprouts from the
   forge-visible state even when the daemon's local copy diverged.
2. Fallback: seed from the repo default branch (or host HEAD); no
   expected publish target. The only supported fallback mode is
   `preserve`. Legacy `current` / `inbox` / `default` values warn once
   and downgrade.

## Finalize

`WorktreeEnv.finalize` classifies the worktree's final git state into
one of four outcomes and records it on the task. **Finalize never
updates a non-task ref and never calls `gitops.fast_forward_branch`.**

| HEAD state | `publish_status` | `publish_branch` | worktree |
| ---------- | ---------------- | ---------------- | -------- |
| detached | `detached` | unset | kept for inspection |
| task branch, no commits beyond seed | `nothing` | unset | torn down, task branch deleted |
| task branch, has commits | `ready` | `brr/<task-id>` | torn down (or kept if uncommitted files) |
| different branch, has commits | `ready` | the agent's branch | torn down; throwaway task branch deleted |

`conflict` is owned by the publish step (see below) — the env layer
no longer produces it.

## Publish

`daemon.publish(repo_root, task)` is the single entry point. It reads
`publish_branch`, `expected_publish_branch`, and `expected_remote_oid`
directly off `task.meta` (no plan threading from the worker tail).

Decision (five mutually exclusive arms):

| Arm | When | Push command |
| --- | ---- | ------------ |
| noop | no `publish_branch` set, or no commits to push, or no remote configured | none |
| plain | `publish_branch` has upstream, source name == target name | `git push <remote> <branch>` |
| upstream | new local branch, source name == target name | `git push -u <remote> <branch>` |
| refspec | agent kept `brr/<task-id>` but event named a different `expected_publish_branch` | `git push <remote> brr/<task-id>:<expected>` |
| lease | `publish_branch == expected_publish_branch` and `expected_remote_oid` set and local is not an ancestor of `<remote>/<branch>` | `git push --force-with-lease=refs/heads/<branch>:<oid> <remote> <branch>:refs/heads/<branch>` |

A failed push flips `publish_status` to `conflict` and emits the
`conflict` packet so gates render the delivery failure.

## Metadata

The triple `preserved_branch` / `landed_branch` / `changed_branch`
collapses to one pair:

- `publish_branch` — name of the branch to publish (and the branch
  renderers should talk about).
- `publish_status` — one of `ready` | `nothing` | `detached` |
  `conflict`.

All six readers (`run_progress.py`, `run_context.py`, `prompts.py`,
`conversations.py`, `gates/github/`, `daemon.py`) consume only those
keys.

## Why drop local-land

The local-land step (`gitops.fast_forward_branch` from finalize) only
ever updated the *host* checkout's local ref. The remote was
authoritative anyway — every gate routes through it. Three concrete
benefits of dropping the local ref update:

1. **Cross-task freshness is preserved by `sync.py`.** Before each
   task the daemon fetches origin and the resolver seeds from
   `<remote>/<target>` when present, so a follow-up task sees the
   previous task's publish even if the operator's local default branch
   never moved.
2. **Operator's local divergence stops blocking tasks.** Under
   local-land a divergent local copy of the target branch caused the
   pre-task ff to refuse and finalize to record a `conflict`. Under
   the publish kernel the worker seeds from `<remote>/<target>` and
   publishes back; the operator's local copy is irrelevant.
3. **One concurrency story.** Per-branch locks now guard *publish*,
   not finalize-then-publish. Tasks for the same `expected_publish_branch`
   serialise on push; tasks for different branches don't contend at
   all.

## Removed operator mode

The previous `branch.fallback=current` mode was a self-development
knob that bound a task to the host checkout *and* asked the daemon to
fast-forward that checkout after the run. Both halves only made sense
inside the local-land path. After the kernel collapse the operator's
self-dev flow is just "switch the host checkout, run brr inside the
worktree" — the worktree env already isolates work onto its own
branch, and publishing is the agent's branch as-is.

## Out of scope

- `sync.py` — pull-side freshness is its own contract and isn't
  touched. The targeted-vs-sweep distinction
  (`sync.fast_forward_all` etc.) is still how per-project branching
  strategy is expressed.
- Force-push of branches other than `expected_publish_branch` — the
  narrow lease scope is intentional. Other branches stay ordinary
  pushes; an out-of-band rewrite there still gets a clean rejection.
- Conversation-derived branch authority — the 2026-05-12 amendment to
  the prior design stays the policy. Free-text branch names belong to
  the worker agent at runtime.

## Lineage

Supersedes [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
on 2026-05-21. The prior design's two amendments
(2026-05-12 conversation-authority removal, 2026-05-18 leased PR
rebase) are preserved here: the leased push is the publish kernel's
lease arm, and the resolver still ignores conversation history.
