# Design: daemon branch intent resolution

Status: amended on 2026-05-12

Revision history at the top so cold readers see the current shape
first:

- **2026-05-12 amendment.** Conversation-derived branch facts are no
  longer auto-land authority. The resolver only sets
  `auto_land_branch` from structured event metadata; otherwise it
  preserves the task branch. Agents read recent conversation records
  from the prompt and switch branches themselves when continuity is
  actually meant. This supersedes resolution-order step 2 below.
  Rationale: a single recent `preserved_branch` in a sparse window
  was being treated as unambiguous authority and silently routed
  unrelated tasks onto stale sibling branches — a worktree collision
  on 2026-05-12 made this concrete. The fix is "empower the agent
  with context, do not pre-decide on its behalf"; the daemon's job is
  the mechanical safety contract, not branch intent inference. The
  fallback-mode surface is also trimmed to `preserve` (default) and
  `current` (explicit dev/compat); `inbox` and `default` had no
  shipped workflow.
- **2026-05-11 implementation note.** The core resolver and daemon
  gitops path shipped. `branching.BranchPlan` resolved structured
  event branch fields, conversation branch facts, and fallback modes
  (`preserve` default, `inbox`, `default`, `current`). Worktrees
  sprout from `seed_ref`; finalization fast-forwards named targets by
  ref or preserves `brr/<task-id>` when no target exists;
  `_push_if_needed` pushes the branch that actually changed and sets
  upstream for brr-owned new branches.
- **2026-05-10 revision.** Earlier `landing_branch=` recommendation
  superseded. A single configured landing branch removed dependence
  on the host checkout, but replaced that bug with hidden branch
  authority in config. The corrected design kept the useful split
  between execution branch and durable branch while making branch
  authority derive from the task, the thread, or structured source
  metadata before falling back to policy.

Richer source-specific metadata (PR/issue/task refs) remains an
expansion point, not a different design.

Follow-up research that drove the 2026-05-12 amendment lives in
[`research-branch-plan-simplification-2026-05-12.md`](research-branch-plan-simplification-2026-05-12.md).

This hangs off the tasks/branching hub,
[`subject-tasks-branching.md`](subject-tasks-branching.md), and refines
the agent-owned branch contract introduced by
[`decision-remove-triage.md`](decision-remove-triage.md).

## Problem

After the kb-shape work, design and research tasks naturally create kb
commits. That is good: the kb is semantic project memory, not chat
scratch. The problem is where those commits land.

Before this design shipped, the daemon captured
`base_branch = gitops.current_branch(repo_root)`
when a task starts. `worktree.create` sprouts `brr/<task-id>` from
`HEAD`, and `WorktreeEnv.finalize` fast-forwards the task branch into
the branch currently checked out in the host repo. That means a remote
Telegram/Slack design conversation can land durable kb commits onto
whatever branch the operator happened to have checked out when the
daemon was running. If that branch is an unrelated feature branch,
detached, dirty, behind upstream, or simply not the operator's intended
inbox branch, brr made the wrong thing easy.

The first proposed fix was an explicit `landing_branch=` config key.
That was still the wrong shape. It made the target stable, but it also
made branch choice a hidden, repo-local ambient fact. A user can forget
that `.brr/config` points at `feature/payment-refactor`, ask a remote
agent to "update the README with the new setup steps", and get a
silent commit on the wrong line of work. The remote-first model needs
branch authority to come from the task and its source context, not from
a forgotten daemon setting.

The hard part is timing: brr must choose a ref to create the worktree
before the worker agent runs, but we do not want to add back an LLM
triage call just to predict branch intent.

## Goals

- Keep kb-producing design/research tasks legitimate. Do not force
  durable findings back into ephemeral chat replies.
- Do not reintroduce an LLM triage stage or task-type classifier.
- Avoid mutating an unrelated user checkout just because it is the
  process cwd.
- Avoid a hidden universal landing branch that can silently mutate an
  unrelated feature branch.
- Preserve the simple fast-forward-only auto-land path when branch
  authority is clear.
- Preserve the agent-owned runtime branch escape hatch: if the agent
  switches branches, brr records and preserves that choice instead of
  second-guessing it.

## Branch Model

Split the overloaded "base branch" idea into three separate concepts:

| Concept | Decided when | Meaning |
| ------- | ------------ | ------- |
| `seed_ref` | before env prep | The commit/ref used to create `brr/<task-id>`. |
| `auto_land_branch` | before env prep, optional | The branch brr may fast-forward if the agent stays on `brr/<task-id>`. |
| `final_branch` | after the agent exits | The branch HEAD points at in the worktree. This is the agent's runtime branch choice. |

The daemon prepares a `BranchPlan` mechanically:

```python
@dataclass(frozen=True)
class BranchPlan:
    seed_ref: str
    auto_land_branch: str | None
    authority: str
    host_context_branch: str | None
    expected_old_oid: str | None
    notes: list[str]
```

`seed_ref` is required because git needs a starting point. `auto_land_branch`
is optional because "no clear landing authority" is a valid state. In
that state the task still runs, but if the agent stays on the task
branch, brr preserves that branch rather than pretending it knows where
to land it.

## Resolution Order

Resolve the plan without an LLM call, using only structured event
fields, conversation records, source metadata, git refs, and explicit
policy.

1. **Explicit structured instruction wins.** A gate or CLI can put a
   branch target in event metadata (`branch=`, `target_branch=`,
   `branch_target=`). That is authoritative because it is already
   structured. For prose inside the event body ("do this on
   feature/payment-refactor"), the worker agent sees the instruction
   and can switch branches at runtime. The daemon should not grow a
   brittle regex parser just to claim it understood free text before
   the run.
2. **Issue/PR/task metadata wins.** Source-specific structured metadata
   should map directly into the plan: PR head branch, issue-linked
   branch, git-gate source ref, task-file frontmatter branch, etc. This
   lets remote-first sources carry branch authority without asking the
   daemon to infer it from prose.
3. **Current branch is context only.** `gitops.current_branch(repo_root)`
   is still useful context and is recorded as `host_context_branch`,
   shown in the run context, and handed to the agent. It is not an
   automatic landing target. The `current` fallback mode opts into
   "this daemon run is intentionally bound to the host current branch"
   for self-development workflows.
4. **Policy decides fallback behavior.** Policy is allowed to decide
   what happens when no structured branch authority exists; it must
   not be a hidden universal feature-branch target. The shipped
   fallback modes are:

   | Mode | Behavior |
   | ---- | -------- |
   | `preserve` | Seed from the repo default branch if known, otherwise current `HEAD`; set no auto-land target. Completed commits on `brr/<task-id>` are preserved for human routing. Safest remote default. |
   | `current` | Seed from and auto-land to the host current branch. Opt-in compatibility/development behavior. |

> **Superseded step:** an earlier draft had a "existing session/thread
> branch wins" step between (1) and (2) that mined conversation records
> for an unambiguous recent durable branch. The 2026-05-12 amendment
> removed it — the agent reads conversation history from the prompt
> itself and can switch branches at runtime when continuity is meant.
> Pre-decoding a sparse-window branch fact as durable authority was a
> stealth triage step the daemon should not own.

The key correction is that config controls the no-authority fallback
mode, not the branch target for every task.

## No Extra Agent Call

The resolver is deterministic and runs before env prep. It does not
classify the task, parse free-text intent, or ask a model where to
land work. It only gathers branch authority that already exists in
structured state.

The normal worker prompt then includes the branch plan:

- the task branch it starts on;
- the seed ref and, if present, the auto-land target;
- the resolver source (e.g. `event:branch_target`, `fallback:preserve`)
  for trace/observability;
- the host current branch as context only;
- the rule that explicit instructions in the task body — or relevant
  conversation history in the recent activity section — can still
  override the plan by switching branches before editing.

That keeps the old "agent owns branching at runtime" contract. If the
agent decides the work belongs somewhere else after reading the actual
request, it uses git inside the worktree. brr learns that decision from
`final_branch`, not from a second structured LLM output.

## Finalization

Finalization should use the branch plan plus the actual git state:

1. Record `auto_land_branch`'s old OID, when one exists, before
   preparing the worktree.
2. Create `brr/<task-id>` from `seed_ref`.
3. After the runner exits successfully, read `final_branch`.
4. If `final_branch` is detached, preserve the worktree/branch and
   mark the task for human salvage.
5. If `final_branch != brr/<task-id>`, the agent made a runtime branch
   choice. Do not merge it elsewhere. Record `preserved_branch`, update
   conversation branch context, and let branch-aware push policy decide
   whether to push it.
6. If `final_branch == brr/<task-id>` and `auto_land_branch` exists,
   fast-forward `auto_land_branch` to task HEAD only if the recorded
   old OID still matches and the update is a fast-forward.
7. If `final_branch == brr/<task-id>` and no auto-land branch exists,
   mark the task done but preserve the task branch. The response should
   name the branch so the operator can merge, PR, or continue the same
   thread.

When the host checkout is currently on the target branch, brr can use
`git merge --ff-only <task-branch>` so the working tree updates. When
the target is not checked out anywhere, brr should advance the ref
with `git update-ref <ref> <task-head> <old-oid>`. If another worktree
has the target branch checked out, or the expected OID changed, mark
the task `conflict` and preserve the task branch.

## Push Behavior

The old `_push_if_needed` checked `@{u}..HEAD`, so it only worked for
the host checkout branch. Branch intent resolution required, and now
uses, a branch-aware push helper:

- push `auto_land_branch` when finalization advanced it and it has an
  upstream;
- push `preserved_branch` when policy allows publishing preserved
  branches, preferably setting upstream only for brr-owned namespaces
  such as `brr/*`;
- otherwise skip push and surface the local branch name in progress and
  the final response.

Delivery should follow the branch that actually changed, not whatever
branch the daemon process has checked out.

## Operator Modes

This design leaves several honest workflows:

- **Remote-safe default.** `branch_fallback=preserve` or `inbox`.
  Unattributed remote work cannot mutate a random feature branch.
- **Threaded work.** A Slack thread, Telegram topic, git task file, PR,
  or issue carries branch continuity. Follow-ups land on or continue
  the same branch without re-asking.
- **Explicit branch work.** The user or source metadata names the
  branch. Structured metadata is honoured before the run; prose
  instructions are honoured by the worker agent through normal git
  operations inside the worktree.
- **Self-development branch.** The operator can make current-branch
  binding explicit via a local/interactive source or a development mode
  on `brr up`. That is an intentional mode, not a stale config value
  quietly affecting unrelated remote tasks.

## Rejected Alternatives

| Alternative | Why not |
| ----------- | ------- |
| Fixed `landing_branch=` config | Removes host-checkout dependence but creates hidden branch authority. A stale config can silently route unrelated tasks into a feature branch. |
| Keep using the current branch | Status quo; remote durable work remains coupled to host checkout state. |
| Add a pre-run LLM branch selector | Reintroduces the triage-shaped latency, cost, and parse-failure class removed by [`decision-remove-triage.md`](decision-remove-triage.md). |
| Parse free-text branch names in the daemon | Brittle and incomplete. Structured metadata is daemon-readable; free text belongs to the worker agent that already reads the task. |
| Make design/research tasks chat-only | Loses the durable kb value AGENTS.md is trying to create; the kb commit is not the bug. |
| Always land on a hard-coded `brr/inbox` branch | Reasonable as a fallback mode, wrong as universal branch authority because PR/thread/explicit branch work should route to its own branch. |

## Implementation Notes

- `branching.py` owns deterministic branch resolution.
- `worktree.create(base_ref=...)` starts the task branch from the
  resolved seed ref, not necessarily the host checkout.
- `RunContext` carries `BranchPlan`; `base_branch` remains compatibility
  wording for the auto-land branch while older renderers exist.
- Conversation records and terminal update packets carry branch facts
  such as `landed_branch`, `preserved_branch`, and `changed_branch` so
  later thread tasks can resolve branch continuity.
- `gitops.fast_forward_branch` safely advances a named local branch by
  merge when it is the daemon checkout or by `update-ref` when it is not
  checked out elsewhere.
- `_push_if_needed` accepts the changed branch/ref explicitly rather
  than assuming the host checkout's `HEAD`.
- `brr docs envs`, `execution-map`, `active-task`, the daemon prompt,
  and progress rendering describe the branch plan: seed, optional
  auto-land target, final branch, and authority source.
