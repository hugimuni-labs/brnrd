# Subject: daemon and process lifecycle

Hub page for brr's daemon: the foreground process started by
`brr up`, the file protocol it drains, how it hands work to runners,
and how process lifecycle should evolve without turning local
troubleshooting into product UX.

This page is the daemon-loop subject hub described by
[AGENTS.md](../AGENTS.md) and
[`subject-kb.md`](subject-kb.md): it synthesises the current shape so a
future agent does not have to reconstruct daemon behavior from
[`daemon.py`](../src/brr/daemon.py), bundled docs, and old plans.
The bottom-up source route is still
[`repo-dive-in-map.md`](repo-dive-in-map.md).

## Current shape

The daemon is intentionally small and foreground-owned. `brr up` runs
one Python process in the repo, writes `.brr/daemon.pid`, starts any
configured gate threads, and dispatches pending events from
`.brr/inbox/` into a bounded worker pool. `brr down` sends `SIGTERM`
to the recorded PID. The signal handlers for `SIGTERM` and `SIGINT`
only flip the loop flag, so a signal received while workers are
running asks the daemon to stop accepting new events and drain the
pool before exiting rather than trying to cancel the in-flight
runners.

The daemon owns orchestration, not meaning:

- Gates are transport adapters. Telegram, Slack, future forge gates,
  and any custom script gate communicate through the file protocol
  under `.brr/inbox/` and `.brr/responses/`. Gate-specific routing and
  live progress rendering stay in the gate modules.
- Conversation history is append-only routing context, not a workstream
  identity. The decision to drop workstreams is recorded in
  [`decision-drop-streams.md`](decision-drop-streams.md).
- Tasks are built mechanically from events. The decision to remove
  LLM-driven triage and frontmatter-as-stdout is recorded in
  [`decision-remove-triage.md`](decision-remove-triage.md).
- Environments isolate execution. The daemon resolves branch intent and
  environment policy, asks the selected backend to prepare and finalize,
  then lets the agent make runtime branch choices inside the run. The
  env synthesis hub is [`subject-envs.md`](subject-envs.md); the protocol
  spec lives in [`design-env-interface.md`](design-env-interface.md).

## Concurrency model

The daemon runs tasks concurrently in a bounded worker pool (default
`max_workers=4`, configurable via `.brr/config`). Each worker thread
takes one pending event from the inbox, runs the full `_run_worker`
pipeline end-to-end (including push and post-task housekeeping), and
returns. Workers don't share mutable state with each other — the
shipped design is contention-free by partitioning, not by locking.

The partitioning rules that make concurrency safe without per-shared-
file locks:

- **Worktree / branch identity is per task.** Each task gets a fresh
  `brr/<task-id>` branch sprouted from the resolved seed ref into
  `.brr/worktrees/<task-id>/`. Task ids are globally unique
  (`evt-<nanotime>-<random>`), so two concurrent task starts never
  collide on branch name or worktree directory.
- **Conversation log is one file per event pipeline.**
  `.brr/conversations/<key>/<event-id>.jsonl` holds every record one
  worker invocation emits (event arrival, task lifecycle, update
  packets, artifact records). The file has exactly one writer for its
  lifetime. Readers glob the directory and merge by `ts` for
  projection. The conversations bundled doc
  ([`src/brr/docs/conversations.md`](../src/brr/docs/conversations.md))
  describes the user-visible side; the design page named below
  carries the per-event-file partitioning rationale.
- **Gate progress card state is one file per task.**
  `.brr/gates/<gate>/progress/<task-id>.json` carries the rendered
  card state for one task; the render path reads and writes only its
  own file.
- **Per-task artefacts** (`.brr/tasks/<task-id>.md`, response file
  at `.brr/responses/<event-id>.md`, trace dirs) are already keyed by
  id and don't overlap.

What still needs explicit synchronisation, because it touches a
genuinely shared git ref:

- **Auto-land fast-forward** (`gitops.fast_forward_branch` in the
  worktree finalize path) takes a per-branch lock keyed on the
  resolved target name, so two tasks landing on the same auto-land
  target serialise correctly. Tasks targeting different branches
  proceed in parallel.
- **Branch push** (`_push_if_needed`) takes the same kind of
  per-branch lock keyed on the branch being pushed, for the same
  reason.

Cancellation is still not in v1: signals request drain-and-exit, they
don't interrupt a running AI CLI.

## Worker lifecycle

For each pending event, the daemon:

1. marks the event `processing`;
2. fetches the default remote and best-effort fast-forwards the local
   default branch (and any structured branch named on the event) via
   [`sync.refresh_before_task`](../src/brr/sync.py) — the seed-ref
   invariant is described in
   [`design-git-layer-rework.md`](design-git-layer-rework.md);
3. resolves the branch plan, then creates and persists a `Task`;
4. prepares the selected env backend (`host`, `worktree`, or `docker`);
5. builds the daemon prompt with the Task Context Bundle;
6. invokes the configured runner headlessly;
7. captures the runner's final stdout as the response file;
8. retries if no response was produced;
9. marks the inbox event `done`, making the response file deliverable
   by the originating gate;
10. runs kb preflight plus the optional redundancy pass after successful
   work;
11. finalizes the environment, fast-forwarding or preserving branches;
12. pushes the branch that actually changed.

The durable user response is plain stdout captured by
[`runner.invoke_runner`](../src/brr/runner.py), not a file the agent
writes manually. This contract is documented in
[`execution-map.md`](../src/brr/docs/execution-map.md) and enforced by
the daemon prompt assembled in [`prompts.py`](../src/brr/prompts.py).
Response delivery is intentionally released before kb maintenance,
environment finalization, and push: those stages are post-response
housekeeping and should not delay the operator seeing the result. The
progress card can continue to show maintenance, finalization, and push
after the final reply is already in the originating chat thread.

## Forge-aware response card

After a successful push the daemon derives a clickable branch URL
from the configured `origin` remote and embeds it in the `push_done`
packet under `view_url`. The response card renders the URL on its
own `view: <url>` line below the `delivered` header so remote
operators get a link they can actually click — local worktree paths
in chat replies don't resolve on the user's machine. The inference
lives in [`forges.py`](../src/brr/forges.py) and covers GitHub,
GitLab (including `gitlab.<corp>` self-hosts), Bitbucket Cloud, and
Gitea/Forgejo (including `codeberg.org`) out of the box. For
internal hosts the host-pattern table doesn't recognise, two
`.brr/config` keys override detection:

- `forge.kind = github | gitlab | bitbucket | gitea` — force the
  template that should apply to this host.
- `forge.url_base = gitlab.internal.example.com` — replace the web
  host in the resulting URL when the SSH remote and the web UI live
  at different domains.

The module is intentionally observational: any failure (missing
remote, unparseable URL, unknown forge) returns `None` and the card
emits without the link rather than guessing. Action-shaped behaviour
like opening a PR / MR belongs to a post-task hook, deferred so its
contract can be designed honestly rather than wedged into the
default prompt.

## Process control

Process control is deliberately local:

- `brr up` starts the foreground daemon.
- `brr down` asks that daemon to stop.
- The operator terminal, shell, tmux, launchd, systemd, or a future
  supervisor decides whether to start it again.

That boundary avoids letting chat messages or agent code kill the
process that is currently responsible for delivering their response.
Agents should not run daemon lifecycle commands from inside daemon
tasks; the generated run context and bundled internals doc both frame
`brr up` / `brr down` as human-operator concerns.

For brr self-development, the restart pain is real but narrower than a
product restart feature. The shipped path is captured in
[`design-daemon-dev-reload.md`](design-daemon-dev-reload.md): use an
editable install, then run `brr up --dev-reload` (or set
`dev_reload=true`) so the foreground daemon re-execs when brr's own
package files change. Under the concurrent worker pool the reload
remains quiescent-only — when a worker notices changed package files
on task completion, the daemon stops accepting new events and re-execs
once the pool drains to zero in-flight tasks. The reload path stays
terminal-owned and explicit, not a remote command.

## Status and troubleshooting

Remote gates are the primary progress surface. Troubleshooting follows
the generated run context, persisted task and conversation files,
traces, response artifacts, and preserved worktree/container metadata
rather than a separate local status module. New lifecycle UX should
extend update packets, `RunProgressView`, and gate renderers.

Earlier versions kept private `status.py` helpers after removing the
public `brr status` / `brr inspect` commands; those helpers were
removed on 2026-05-14 once the only importers were tests and stale docs.

## Deferred directions

- **External supervision.** The fleet notes sketch systemd, launchd,
  Docker, tmux, and future `brnrd` supervision. That is the right layer
  for "keep this running forever" and cross-repo process management, but
  it is not needed for the local brr self-development reload loop.
- **True cancellation.** The daemon has no cancellation in v1. Signals
  request drain-and-exit; they do not interrupt a running AI CLI.

(Earlier versions of this page recorded a concurrent worker pool as
deferred and the original merge-coordinator design as abandoned;
both were reversed on 2026-05-16 when concurrency shipped on top of
the contention-free per-event/per-task partitioning above, so the
coordinator never came back. See
[`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md) for
the lineage of the partial pre-2026-05-16 shape and
[`design-concurrent-execution.md`](design-concurrent-execution.md)
for the accepted design.)

## Read next

Read these in order when changing daemon behavior:

1. [`repo-dive-in-map.md`](repo-dive-in-map.md) for the source reading
   route.
2. [`src/brr/daemon.py`](../src/brr/daemon.py) for the actual loop.
3. [`src/brr/docs/execution-map.md`](../src/brr/docs/execution-map.md)
   for the user-facing pipeline contract.
4. [`subject-envs.md`](subject-envs.md) for environment backend
   responsibilities; [`design-env-interface.md`](design-env-interface.md)
   for the underlying protocol spec.
5. [`subject-tasks-branching.md`](subject-tasks-branching.md) and
   [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
   for task construction, branch intent resolution, and the accepted fix
   for ambient host-checkout and hidden landing-config coupling.
6. [`design-git-layer-rework.md`](design-git-layer-rework.md) for the
   pre-task fetch+ff invariant, the boundary between pure git refs
   (daemon) and forge concepts (per-provider gates), and the staged
   Phase 1 / 2 / 3 plan.
7. [`decision-drop-streams.md`](decision-drop-streams.md) and
   [`decision-remove-triage.md`](decision-remove-triage.md) for the
   recent simplifications that keep daemon context lean.
8. [`design-daemon-dev-reload.md`](design-daemon-dev-reload.md) for the
   current development reload proposal.
