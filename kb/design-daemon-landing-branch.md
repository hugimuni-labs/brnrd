# Design: daemon landing branch policy

Status: active

Design note for removing the daemon's dependency on the operator's
ambient host checkout branch while preserving the useful property that
design, research, and implementation tasks can commit durable repo
changes. This hangs off the tasks/branching hub,
[`subject-tasks-branching.md`](subject-tasks-branching.md), and refines
the agent-owned branch contract introduced by
[`decision-remove-triage.md`](decision-remove-triage.md).

## Problem

After the kb-shape work, design and research tasks naturally create kb
commits. That is good: the kb is semantic project memory, not chat
scratch. The problem is where those commits land.

Today the daemon captures `base_branch = gitops.current_branch(repo_root)`
when a task starts. `worktree.create` sprouts `brr/<task-id>` from
`HEAD`, and `WorktreeEnv.finalize` fast-forwards the task branch into
the branch currently checked out in the host repo. That means a remote
Telegram/Slack design conversation can land durable kb commits onto
whatever branch the operator happened to have checked out when the
daemon was running. If that branch is an unrelated feature branch,
detached, dirty, behind upstream, or simply not the operator's intended
inbox branch, brr made the wrong thing easy.

The design task is only the symptom. The underlying issue is that brr
does not distinguish the daemon's execution checkout from the branch
that should receive daemon-produced commits.

## Goals

- Keep kb-producing design/research tasks legitimate. Do not force
  durable findings back into ephemeral chat replies.
- Do not reintroduce an LLM triage stage or task-type classifier.
- Make the landing target explicit and stable across daemon runs.
- Avoid mutating an unrelated user checkout just because it is the
  process cwd.
- Preserve the simple fast-forward-only history and branch-preservation
  escape hatch.
- Keep brr self-development possible, including the developer reload
  design that expects landed source changes to appear in the checkout
  when the operator deliberately runs that way.

## Recommendation

Add an explicit daemon landing branch policy:

```ini
landing_branch=main
```

For backwards compatibility, an unset `landing_branch` can initially
mean "the branch checked out when `brr up` starts", with a warning in
daemon startup output. Interactive `brr init -i` should ask which
branch remote tasks should land on and write the answer. That changes
the normal path from ambient per-task state to a recorded operator
choice without breaking existing repos immediately.

Task preparation should sprout from the landing ref, not raw `HEAD`:

```python
worktree.create(repo_root, task.id, base_ref=f"refs/heads/{landing_branch}")
```

The run context should name both:

- **Execution root/current branch**: where the agent is running
  (`.brr/worktrees/<task-id>`, `brr/<task-id>`).
- **Landing branch**: where brr will attempt a fast-forward if the
  agent stays on the task branch.

Finalization should fast-forward the landing branch, not the host
checkout's current branch:

1. Record the landing branch's old OID before preparing the worktree.
2. On finalize, get the task branch HEAD.
3. Verify the old OID is an ancestor of task HEAD.
4. If the host checkout is currently on the landing branch, use
   `git merge --ff-only <task-branch>` so the working tree updates too.
5. If the host checkout is on another branch and no worktree has the
   landing branch checked out, advance `refs/heads/<landing_branch>`
   with `git update-ref <ref> <task-head> <old-oid>`.
6. If the landing branch is checked out somewhere else, or the expected
   old OID no longer matches, mark the task `conflict` and preserve the
   task branch.

The push path must follow the same explicit target. `_push_if_needed`
currently checks `@{u}..HEAD`, which only works for the checked-out
branch. Once brr can fast-forward a non-checked-out landing branch, the
push helper needs a branch-aware mode that compares and pushes
`landing_branch` against its upstream.

## Operator modes

This design leaves two honest operator workflows:

- **Remote-safe inbox branch.** Set `landing_branch=brr/inbox` or
  another dedicated branch. The daemon can advance that branch without
  touching the user's active feature checkout. The operator merges or
  opens a PR when ready.
- **Self-development branch.** Set `landing_branch=<current dev
  branch>` and run the daemon from a clean checkout on that branch.
  Finalization updates the working tree, so an editable install plus
  daemon developer reload can pick up source changes after the task
  completes.

Both modes are valid. The important change is that the operator chooses
one explicitly rather than inheriting it from whatever branch happened
to be active.

## Rejected alternatives

| Alternative | Why not |
| ----------- | ------- |
| Make design/research tasks chat-only | Loses the durable kb value AGENTS.md is trying to create; the kb commit is not the bug. |
| Ask agents to switch to a named branch for design tasks | Reintroduces a task classifier by prompt convention, and agents will apply it inconsistently. |
| Keep using the current branch | Status quo; remote durable work remains coupled to host checkout state. |
| Always land on a hard-coded `brr/daemon` branch | Safe for host checkouts, but surprising for implementation tasks and incompatible with self-development reload unless the operator manually merges every change. |
| Bring back LLM triage to choose the branch | The triage-removal decision was about removing exactly this brittle, predictive classification step. |

## Implementation notes

- `worktree.create` needs a `base_ref` parameter and tests proving the
  task branch starts from the configured landing branch, not the
  current checkout.
- `RunContext` should carry `landing_branch` and the expected base OID.
  `base_branch` can either be renamed or kept as a compatibility alias
  in prompts/status output until the wording is cleaned up.
- `WorktreeEnv._land_or_preserve` should call a `gitops` helper that
  fast-forwards a named local branch safely and reports why it refused.
- `daemon._run_worker` should read the policy once per task from config
  and put it in task metadata, conversation records, progress payloads,
  and the generated run context.
- `brr docs envs`, `execution-map`, `active-task`, and the daemon prompt
  should describe "landing branch" instead of implying the base is
  whatever the daemon checkout currently has selected.
