# Design: daemon branch intent resolution

Status: accepted on 2026-05-12; amended on 2026-05-18

This hangs off the tasks/branching hub,
[`subject-tasks-branching.md`](subject-tasks-branching.md), and refines
the agent-owned branch contract introduced by
[`decision-remove-triage.md`](decision-remove-triage.md).

## Why this exists

Remote task sources (Telegram, Slack, future forge gates) can submit
work without the operator standing at the host checkout. brr therefore cannot
overload the daemon's `gitops.current_branch(repo_root)` as both "where
to sprout the task" and "where to land the work". Doing so couples
remote durable work — kb pages, design notes, code commits — to
whatever the operator happened to have checked out at the moment the
event arrived, including detached HEADs, dirty branches, or unrelated
features.

The fix is to separate three concepts the old shape conflated, and to
keep branch authority **structured**: it comes from event metadata or
explicit policy, not from inference over prose or conversation
history. Free-text branch instructions inside the event body are the
worker agent's job to parse at runtime; the daemon stays mechanical.

## Goals

- Keep kb-producing design/research tasks legitimate. Durable findings
  belong in commits, not in ephemeral chat replies.
- Do not reintroduce an LLM triage stage or task-type classifier.
- Do not mutate an unrelated host checkout just because it is the
  daemon's cwd.
- Do not introduce a hidden universal landing branch that silently
  routes unrelated tasks together.
- Preserve the fast-forward-only auto-land path when authority is
  clear.
- Preserve the agent-owned runtime escape hatch: if the agent switches
  branches inside the worktree, brr records and preserves that choice.

## Branch model

Three concepts replace the overloaded "base branch":

| Concept | Decided when | Meaning |
| ------- | ------------ | ------- |
| `seed_ref` | before env prep | The commit/ref used to create `brr/<task-id>`. |
| `auto_land_branch` | before env prep, optional | The branch brr may fast-forward if the agent stays on `brr/<task-id>`. |
| `final_branch` | after the agent exits | The branch HEAD points at in the worktree. The agent's runtime choice. |

The daemon builds a `BranchPlan` mechanically:

```python
@dataclass(frozen=True)
class BranchPlan:
    seed_ref: str
    auto_land_branch: str | None
    source: str
    host_context_branch: str | None
    expected_old_oid: str | None
    notes: list[str]
```

`seed_ref` is required because git needs a starting point.
`auto_land_branch` is optional — "no clear landing authority" is a
valid state where brr preserves `brr/<task-id>` for human routing.

## Resolution order

Deterministic, no LLM call, runs before env prep:

1. **Explicit structured instruction wins.** Event metadata fields
   (`branch_target=`, `target_branch=`, `base_branch=`, then the
   legacy `branch=`) are authoritative because they're already
   structured. Prose instructions inside the event body ("do this on
   feature/payment-refactor") are the worker agent's responsibility to
   act on through normal git operations inside the worktree.
2. **Source integrations materialize branch facts as event metadata.**
   A gate that knows a PR head branch, task-file branch, or other
   structured source ref should put it in one of the resolver fields
   above. GitHub PR events and PR comments do this with
   `branch_target`; the resolver itself still only reads event fields.
3. **Current branch is context only.** `gitops.current_branch(repo_root)`
   is recorded as `host_context_branch`, shown in the run context, and
   handed to the agent. It is never an automatic landing target. The
   explicit `current` fallback mode is the only way to opt into "this
   daemon run is bound to the host current branch", for self-development
   workflows.
4. **Policy decides fallback.** When no structured authority exists:

   | Mode | Behavior |
   | ---- | -------- |
   | `preserve` | Seed from the repo default branch when known, otherwise current `HEAD`; set no auto-land target. Commits on `brr/<task-id>` are preserved for human routing. Safest remote default. |
   | `current` | Seed from and auto-land to the host current branch. Opt-in compatibility/development behavior. |

The key invariant is that config controls the no-authority fallback
mode, *not* the branch target for every task.

## No extra agent call

The resolver is deterministic. It does not classify the task, parse
free-text intent, or ask a model where to land work. It only gathers
branch authority that already exists in structured event fields and
fallback policy.

The worker prompt then includes the branch plan:

- the task branch the agent starts on;
- the seed ref and, if present, the auto-land target;
- the resolver source string (e.g. `event:target_branch`,
  `fallback:preserve`) for trace/observability;
- the host current branch as context only;
- the rule that explicit instructions in the task body or in recent
  conversation history can still override the plan by `git switch`
  before editing.

brr learns the runtime branch choice from `final_branch`, never from a
second LLM call.

## Finalization

1. Record `auto_land_branch`'s old OID, when one exists, before
   preparing the worktree.
2. Create `brr/<task-id>` from `seed_ref`.
3. After the runner exits, read `final_branch`.
4. If `final_branch` is detached: preserve the branch and mark the
   task for human salvage.
5. If `final_branch != brr/<task-id>`: the agent made a runtime branch
   choice. Don't merge it elsewhere. Record `preserved_branch`, update
   conversation branch context, push it if a publish remote is
   configured. If the branch is the explicit auto-land target and its
   history was rewritten relative to the remote-tracking ref, the push
   uses `--force-with-lease` with the recorded old remote OID; this is
   the PR-rebase path, not a broad force-push permission.
6. If `final_branch == brr/<task-id>` and `auto_land_branch` exists:
   fast-forward `auto_land_branch` to task HEAD only when the recorded
   old OID still matches and the update is a fast-forward.
7. If `final_branch == brr/<task-id>` and no auto-land target exists:
   mark the task done but preserve the task branch. The response
   surfaces the branch so the operator can merge, PR, or continue the
   same thread.

When the host checkout is currently on the target branch, brr uses
`git merge --ff-only <task-branch>` so the working tree updates. When
the target is not checked out anywhere, brr advances the ref with
`git update-ref <ref> <task-head> <old-oid>`. If another worktree has
the target branch checked out, or the expected OID changed, brr marks
the task `conflict` and preserves the task branch.

## Push behavior

The push helper accepts the changed branch/ref explicitly:

- push `auto_land_branch` when finalization advanced it and it has an
  upstream;
- push the agent's chosen branch (whether `brr/<task-id>` or a
  renamed branch) with `git push -u` when it has no upstream and a
  default remote exists — matching how a user would publish a new
  branch;
- when the chosen branch is the explicit auto-land target and a rebase
  or other deliberate rewrite made it non-fast-forward relative to the
  remote-tracking ref, use `--force-with-lease` against the captured
  pre-run remote OID;
- otherwise skip push and surface the local branch name in progress
  and the final response.

Delivery follows the branch that actually changed, not the daemon
process's `HEAD`.

## Operator modes

This design supports several honest workflows:

- **Remote-safe default.** `branch_fallback=preserve`. Unattributed
  remote work cannot mutate a random feature branch.
- **Threaded work.** A PR event/comment, git task file, or any other
  source that emits structured branch metadata carries branch
  continuity. Follow-ups land on or continue the same branch without
  re-asking.
- **Explicit branch work.** The user or source metadata names the
  branch in structured metadata; prose instructions are honoured by
  the worker agent at runtime.
- **Self-development branch.** The operator binds to the host current
  branch explicitly via `current` fallback or interactive source.

## Rejected alternatives

| Alternative | Why not |
| ----------- | ------- |
| Fixed `landing_branch=` config | Removes host-checkout dependence but creates hidden branch authority. A stale config can silently route unrelated tasks into a feature branch. |
| Keep using the current branch | Status quo; remote durable work remains coupled to host checkout state. |
| Add a pre-run LLM branch selector | Reintroduces the triage-shaped latency, cost, and parse-failure class removed by [`decision-remove-triage.md`](decision-remove-triage.md). |
| Parse free-text branch names in the daemon | Brittle and incomplete. Structured metadata is daemon-readable; free text belongs to the worker agent that already reads the task. |
| Mine conversation records for an unambiguous recent durable branch | A sparse-window recent `preserved_branch` gets treated as durable authority and silently routes unrelated tasks onto stale sibling branches. The agent reads conversation history from the prompt and can switch branches itself when continuity is meant. |
| Always land on a hard-coded `brr/inbox` branch | Reasonable as a fallback mode, wrong as universal branch authority. |
| Make design/research tasks chat-only | Loses the durable kb value AGENTS.md is trying to create; the kb commit is not the bug. |

## Implementation notes

- `branching.py` owns deterministic branch resolution.
- `worktree.create(base_ref=...)` starts the task branch from the
  resolved seed ref, not necessarily the host checkout.
- `RunContext` carries `BranchPlan`.
- Conversation records and progress packets carry branch facts
  (`landed_branch`, `preserved_branch`, `changed_branch`) so later
  thread agents can see branch continuity in prompt context; the daemon
  resolver does not mine conversation history.
- `gitops.fast_forward_branch` advances a named local branch by merge
  when it is the daemon checkout, or by `update-ref` when it is not
  checked out anywhere.
- `_push_if_needed` accepts the changed branch/ref explicitly and
  always uses `git push -u` for new branches.
- `brr docs envs`, `execution-map`, `active-task`, the daemon prompt,
  and progress rendering describe the branch plan: seed, optional
  auto-land target, final branch, and resolver source.

Richer source-specific metadata (PR/issue/task refs) remains an
expansion point, not a different design.

## Lineage

Amended 2026-05-12 to remove conversation-derived branch authority
from the resolver and keep only `preserve` / `current` fallback modes
after a sparse-window false positive; amended 2026-05-18 to add leased
publishing for explicit target branches after PR-rebase pushes exposed
the ordinary-push gap. See
[`research-branch-plan-simplification-2026-05-12.md`](research-branch-plan-simplification-2026-05-12.md)
for the resolver critique; the 2026-05-18 change is in commit
`4c6959f`.
