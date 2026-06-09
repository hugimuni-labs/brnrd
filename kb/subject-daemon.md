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

The daemon is intentionally small and foreground-owned — a thin
**reflex** layer that leaves judgement to the agent it wakes. `brr up`
runs one Python process in the repo, writes `.brr/daemon.pid`, starts
any configured gate threads, and runs **single-flight**: it scans
`.brr/inbox/` and spawns one *thought* (one `_run_worker` invocation)
when idle and work is pending. `brr down` sends `SIGTERM` to the
recorded PID. The signal handlers for `SIGTERM` and `SIGINT` only flip
the loop flag, so a signal received mid-thought asks the daemon to stop
spawning and let the in-flight thought drain before exiting rather than
interrupting the running runner.

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

## Execution model — single-flight

The daemon runs **one thought at a time**. When idle and work is
pending it spawns a single worker, which runs the full `_run_worker`
pipeline end-to-end (env prepare, runner invocation + retries, response
capture, kb maintenance, finalize, push) and returns; only then does the
daemon consider the next pending event. The worker runs off the main
thread (a one-slot executor) so the loop stays responsive to dev-reload,
gate-thread liveness, and shutdown signals while a long thought runs.

This reshapes the former parallel worker pool: local parallelism is
discarded, and concurrency within one resident becomes cooperative
rather than parallel across workers. (The threaded-pool thesis —
`max_workers`, default 4 — was reversed 2026-06-08; the knob is now
ignored. See [`design-agent-dominion.md`](design-agent-dominion.md) §4;
the superseded
[`design-concurrent-execution.md`](design-concurrent-execution.md)
holds the prior reasoning.)

The per-task isolation primitives the parallel design relied on
**survive** — they still earn their keep for crash recovery, ad-hoc
sessions, and the managed multi-daemon case:

- **Worktree / branch identity is per task.** Each task gets a fresh
  `brr/<task-id>` branch sprouted from the resolved seed ref into
  `.brr/worktrees/<task-id>/`. Task ids are globally unique
  (`evt-<nanotime>-<random>`), so task starts never collide on branch
  name or worktree directory.
- **Conversation log is one file per event pipeline.**
  `.brr/conversations/<key>/<event-id>.jsonl` holds every record one
  worker invocation emits; one writer per file, readers glob and merge
  by `ts`. The bundled doc
  ([`src/brr/docs/conversations.md`](../src/brr/docs/conversations.md))
  describes the user-visible side.
- **Gate progress card state is one file per task**
  (`.brr/gates/<gate>/progress/<task-id>.json`); the render path reads
  and writes only its own file.
- **Per-task artefacts** (`.brr/tasks/<task-id>.md`, the response file
  at `.brr/responses/<event-id>.md`, trace dirs) are keyed by id.
- **Publish** (`daemon.publish`) takes a per-branch lock keyed on the
  branch being pushed. Within one single-flight daemon this is now
  uncontended (one publish at a time), but it still guards a daemon
  publish racing an ad-hoc session that pushes the same branch, and
  stays cheap. Finalize no longer participates — the env layer never
  updates a non-task ref since the 2026-05-21 publish-kernel collapse
  (see [`design-publish-kernel.md`](design-publish-kernel.md)).

**No command layer, and liveness is a substrate backstop.** The daemon
never parses `/cancel` or any command — every event either wakes the
agent or waits for the living agent to handle it (cancel/redirect
semantics are the agent's job, reconsidered at plan boundaries; the
mid-flight inbox channel is the multi-response protocol, specified in
[`design-multi-response.md`](design-multi-response.md) — interim,
multiple, and interleaved replies now ship via the agent's outbox). What
the daemon *does* guarantee is that the single-flight
slot is reclaimed even if a runner subprocess wedges: the runner's
wall-clock timeout (`runner.timeout_seconds`, default 3600s) kills it. A
finer idle timeout ("no agent check-in in N minutes") stays deferred even
now that multi-response shipped: the agent *can* check in mid-run (its
outbox drain is a positive liveness signal), but those check-ins are
opportunistic, so their *absence* still doesn't separate a wedged process
from a healthy-but-silent one (a long build, deep reasoning). A safe
idle-kill needs a check-in the substrate can count on as periodic; until
that contract exists the generous wall-clock ceiling is the only hard
kill. (See [`design-multi-response.md`](design-multi-response.md) →
liveness.)

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
7. captures the runner's final stdout as the terminal response file, and
   drains the agent's outbox on each heartbeat and once after the runner
   returns — promoting any interim or interleaved replies to the
   per-event partials queue (the multi-response protocol,
   [`design-multi-response.md`](design-multi-response.md));
8. retries if no terminal response was produced;
9. marks the inbox event `done`; the originating gate streams any queued
   interim partials, then the terminal reply, then cleans up;
10. finalizes the environment, classifying the worktree's final state
    into a `publish_status` and recording the branch to publish;
11. publishes that branch via `daemon.publish` under a per-branch lock.

The durable user response is plain stdout captured by
[`runner.invoke_runner`](../src/brr/runner.py), not a file the agent
writes manually — though the agent may *additionally* stream interim
replies through its outbox (step 7). This contract is documented in
[`execution-map.md`](../src/brr/docs/execution-map.md) and enforced by
the daemon prompt assembled in [`prompts.py`](../src/brr/prompts.py).
Response delivery is intentionally released before environment
finalization and push: those stages are post-response housekeeping and
should not delay the operator seeing the result. The progress card can
continue to show finalization and push after the final reply is already
in the originating chat thread. (Deterministic kb-health now rides the
*wake* prompt rather than a post-task pass; see
[`subject-kb.md`](subject-kb.md).)

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

Process control is deliberately local and now has two operator-facing
layers:

- `brr up` and `brr daemon up --foreground` start the existing
  foreground daemon for the current repo; `brr down` and
  `brr daemon down` ask that daemon to drain and stop.
- On Linux, `brr daemon install` writes one user-scoped systemd unit at
  `~/.config/systemd/user/brr.service`, with no `WorkingDirectory`, and
  `brr daemon up | down | status | logs | uninstall` operate that
  service through `systemctl --user` / `journalctl --user`.
- On macOS, `brr daemon install` writes one LaunchAgent at
  `~/Library/LaunchAgents/dev.brnrd.brr.plist`, with no
  `WorkingDirectory`, and the same daemon verbs operate that service
  through `launchctl` while logs land in `~/Library/Logs/brr/`.

That boundary avoids letting chat messages or agent code kill the
process that is currently responsible for delivering their response.
Agents should not run daemon lifecycle commands from inside daemon
tasks; the generated run context and bundled internals doc both frame
daemon lifecycle verbs as human-operator concerns. The Linux systemd
and macOS LaunchAgent service-lifecycle slices shipped on 2026-05-26
from [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md); the
broader machine-scoped multi-project runtime remains tracked there.

For brr self-development, the restart pain is real but narrower than a
product restart feature. The shipped path is captured in
[`design-daemon-dev-reload.md`](design-daemon-dev-reload.md): use an
editable install, then run `brr up --dev-reload` (or set
`dev_reload=true`) so the foreground daemon re-execs when brr's own
package files change. The reload stays quiescent-only — when the
watcher notices changed package files, the daemon stops spawning and
re-execs once the in-flight thought (if any) drains. The reload path
stays terminal-owned and explicit, not a remote command.

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

- **Machine-scoped daemon runtime.** The Linux unit is machine-scoped,
  and the macOS LaunchAgent follows the same no-`WorkingDirectory`
  service shape, but the runtime still needs the project registry /
  poller reshape tracked in
  [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md) to serve
  multiple repos from one process.
- **Windows native service install.** Deferred until there is real user
  demand and the daemon model can support Windows honestly.
- **Agent-driven cancellation + finer liveness.** The daemon honours no
  cancel command by design; the living agent handles cancel/redirect at
  plan boundaries, and the wall-clock `runner.timeout_seconds` is the
  only hard backstop until the multi-response check-in channel makes a
  shorter idle timeout honest (see
  [`design-agent-dominion.md`](design-agent-dominion.md) §4).

(Lineage: a concurrent worker pool was once deferred and the original
merge-coordinator design abandoned; both were reversed 2026-05-16 when
concurrency shipped on the per-event/per-task partitioning above, so the
coordinator never came back. Then concurrency itself was reversed to
single-flight 2026-06-08 with the resident-agent reshape — the
partitioning primitives survived the round trip and now serve crash
recovery / ad-hoc sessions instead. See
[`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md) for the
pre-2026-05-16 shape and
[`design-concurrent-execution.md`](design-concurrent-execution.md)
(superseded) for the parallel design's reasoning.)

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
   [`design-publish-kernel.md`](design-publish-kernel.md) for task
   construction, branch intent resolution, and the accepted publish
   kernel that replaced the predecessor land-then-push pipeline.
6. [`design-git-layer-rework.md`](design-git-layer-rework.md) for the
   pre-task fetch+ff invariant, the boundary between pure git refs
   (daemon) and forge concepts (per-provider gates), and the staged
   Phase 1 / 2 / 3 plan.
7. [`decision-drop-streams.md`](decision-drop-streams.md) and
   [`decision-remove-triage.md`](decision-remove-triage.md) for the
   recent simplifications that keep daemon context lean.
8. [`design-daemon-dev-reload.md`](design-daemon-dev-reload.md) for the
   current development reload proposal.
