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
configured gate threads, and loops over `.brr/inbox/` looking for
pending events. `brr down` sends `SIGTERM` to the recorded PID. The
signal handlers for `SIGTERM` and `SIGINT` only flip the loop flag,
so a signal received during `_run_worker` asks the daemon to drain the
current task before exiting rather than trying to cancel the runner.

The daemon owns orchestration, not meaning:

- Gates are transport adapters. Telegram, Slack, Git, and any custom
  gate communicate through the file protocol under `.brr/inbox/` and
  `.brr/responses/`. Gate-specific routing and live progress rendering
  stay in the gate modules.
- Conversation history is append-only routing context, not a workstream
  identity. The decision to drop workstreams is recorded in
  [`decision-drop-streams.md`](decision-drop-streams.md).
- Tasks are built mechanically from events. The decision to remove
  LLM-driven triage and frontmatter-as-stdout is recorded in
  [`decision-remove-triage.md`](decision-remove-triage.md).
- Environments isolate execution. The daemon resolves branch intent and
  environment policy, asks the selected backend to prepare and finalize,
  then lets the agent make runtime branch choices inside the run. The
  live env design is [`design-env-interface.md`](design-env-interface.md).

The serial-v1 guarantee still matters. The old concurrent-worktree plan
imagined a pool and merge coordinator, but the shipped system keeps one
daemon worker active at a time and uses per-task branches/worktrees for
isolation. That preserves simple recovery semantics: one active event,
one active task, one response, one push path.

## Worker lifecycle

For each pending event, the daemon:

1. marks the event `processing`;
2. resolves the branch plan, then creates and persists a `Task`;
3. prepares the selected env backend (`host`, `worktree`, or `docker`);
4. builds the daemon prompt with the Task Context Bundle;
5. invokes the configured runner headlessly;
6. captures the runner's final stdout as the response file;
7. retries if no response was produced;
8. runs kb preflight plus the optional redundancy pass after successful
   work;
9. finalizes the environment, fast-forwarding or preserving branches;
10. marks the event terminal and pushes the branch that actually changed.

The durable user response is plain stdout captured by
[`runner.invoke_runner`](../src/brr/runner.py), not a file the agent
writes manually. This contract is documented in
[`execution-map.md`](../src/brr/docs/execution-map.md) and enforced by
the daemon prompt assembled in [`prompts.py`](../src/brr/prompts.py).

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
`dev_reload=true`) so the foreground daemon re-execs between tasks when
brr's own package files change. The reload path remains terminal-owned
and quiescent-only, not a remote command, and it stays explicit rather
than becoming unconditional `brr up` behaviour.

## Status and troubleshooting

Remote gates are the primary progress surface. Local status helpers are
for troubleshooting: answer whether the daemon is running, what task is
active, and where to inspect traces, responses, worktrees, or preserved
Docker containers after a failure. New product UX should not accrete in
`status.py`; the repo dive-in map records this as a runtime invariant.

## Deferred directions

- **External supervision.** The fleet notes sketch systemd, launchd,
  Docker, tmux, and future `brnrd` supervision. That is the right layer
  for "keep this running forever" and cross-repo process management, but
  it is not needed for the local brr self-development reload loop.
- **True cancellation.** The daemon has no cancellation in v1. Signals
  request drain-and-exit; they do not interrupt a running AI CLI.
- **Concurrent worker pool.** Still deferred. The current code and tests
  assume serial task execution, and the restart/reload design relies on
  that simplicity by only re-execing between tasks.

## Read next

Read these in order when changing daemon behavior:

1. [`repo-dive-in-map.md`](repo-dive-in-map.md) for the source reading
   route.
2. [`src/brr/daemon.py`](../src/brr/daemon.py) for the actual loop.
3. [`src/brr/docs/execution-map.md`](../src/brr/docs/execution-map.md)
   for the user-facing pipeline contract.
4. [`design-env-interface.md`](design-env-interface.md) for environment
   backend responsibilities.
5. [`subject-tasks-branching.md`](subject-tasks-branching.md) and
   [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
   for task construction, branch intent resolution, and the accepted fix
   for ambient host-checkout and hidden landing-config coupling.
6. [`decision-drop-streams.md`](decision-drop-streams.md) and
   [`decision-remove-triage.md`](decision-remove-triage.md) for the
   recent simplifications that keep daemon context lean.
7. [`design-daemon-dev-reload.md`](design-daemon-dev-reload.md) for the
   current development reload proposal.
