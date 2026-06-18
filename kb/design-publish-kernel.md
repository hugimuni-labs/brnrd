# Design: publish kernel

Status: accepted on 2026-05-21

This hangs off the runs/branching hub
([`subject-runs-branching.md`](subject-runs-branching.md)) and
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
  branches / ff onto auto-land / preserve run branch), called
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
publish path is the agent's own run branch, via the runner's normal
git operations inside the worktree.

## Resolver

`branching.resolve_publish_plan` returns a `PublishPlan`:

```python
@dataclass(frozen=True)
class PublishPlan:
    seed_ref: str
    target_branch: str | None
    source: str
    host_context_branch: str | None
    expected_remote_oid: str | None = None
```

Field semantics:

- `seed_ref` — required, used to sprout `brr/<run-id>`.
- `target_branch` — the branch the daemon expects the agent
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
one of four outcomes and records it on the run. **Finalize never
updates a non-run ref and never calls `gitops.fast_forward_branch`.**

| HEAD state | `publish_status` | `publish_branch` | worktree |
| ---------- | ---------------- | ---------------- | -------- |
| detached | `detached` | unset | kept for inspection |
| run branch, no commits beyond seed | `nothing` | unset | torn down, run branch deleted |
| run branch, has commits | `ready` | `brr/<run-id>` | torn down (or kept if uncommitted files) |
| different branch, has commits | `ready` | the agent's branch | torn down; throwaway run branch deleted |

`conflict` is owned by the publish step (see below) — the env layer
no longer produces it.

## Publish

`daemon.publish(repo_root, run)` is the single entry point. It reads
`publish_branch`, `target_branch`, and `expected_remote_oid` directly
off `run.meta` (no plan threading from the worker tail).

Decision (five mutually exclusive arms):

| Arm | When | Push command |
| --- | ---- | ------------ |
| noop | no `publish_branch` set, or no commits to push, or no remote configured | none |
| plain | `publish_branch` has upstream, source name == target name | `git push <remote> <branch>` |
| upstream | new local branch, source name == target name | `git push -u <remote> <branch>` |
| refspec | agent kept `brr/<run-id>` but event named a different `target_branch` | `git push <remote> brr/<run-id>:<target>` |
| lease | `publish_branch == target_branch` and `expected_remote_oid` set and local is not an ancestor of `<remote>/<branch>` | `git push --force-with-lease=refs/heads/<branch>:<oid> <remote> <branch>:refs/heads/<branch>` |

A failed push flips `publish_status` to `conflict` and emits the
`conflict` packet so gates render the delivery failure.

### Riders on the publish outcome

One daemon step hangs off a *successful* push, keyed only on
`publish_status`, never re-deriving git state:

- **Forge view link.** `_forge_view_url` builds the branch URL for the
  `push_done` card.

Diffense PR publication used to ride here; it moved on 2026-06-10 to the
agent-owned forge delivery path. The resident now projects the checked
pack and sends `gate: forge`; the GitHub gate opens or refreshes the PR.
The push still supplies the safety condition: if the push failed, there is
no remote head for the resident to publish.

### Possible: auto-fork on conflict (not built)

Today a `conflict` leaves the work on the **local** run branch in the
daemon's checkout and emits the `conflict` packet. That's fine for an
operator with shell access to the host, but a *remote* user (Telegram /
Slack) can't see a host-local branch — they get "conflict" with no
salvage path. A bounded improvement: on conflict, fall back to a plain
push of the already-unique `brr/<run-id>` branch (no lease, its own
namespace, so it can't collide), and deliver *that* branch's link
alongside the conflict packet. The user can then open a PR from it,
cherry-pick, or just delete it and re-run. Recommended as a small
follow-up — it closes the one case where the publish kernel hands a
remote user nothing actionable. Deliberately **not** an auto-second-PR:
conflicts fall back to the user's manual resolution (decided 2026-06-01).

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
   not finalize-then-publish. Runs for the same `target_branch`
   serialise on push; runs for different branches don't contend at
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
- Force-push of branches other than `target_branch` — the
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
