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
recorded PID. The signal handlers for `SIGTERM` and `SIGINT` flip the
loop flag so the daemon stops spawning; if a thought is in flight when
the signal lands, shutdown kills its runner (`runner.kill_active`) to
reclaim the slot promptly rather than waiting out the wall-clock budget,
then finalizes and exits.

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

Events that arrive while a thought is running are no longer invisible
until the next spawn. The worker writes an initial pending-events view to
the run outbox and refreshes `inbox.json` there on every heartbeat after
draining any agent-written replies. The resident checks that file at
natural plan / todo boundaries and may fold in quick replies with the
multi-response `event: <id>` path. Idle dispatch is still FIFO: true
agent-selected next-event ordering or long-running batch claims need a
separate claim protocol beyond today's `pending` / `processing` / `done`
states.

### Loop cadence & gate responsiveness

The reflex loop is **event-driven with a poll backstop**, not pure
sleep-polling. Each idle iteration blocks on a process-local
`threading.Event` (`protocol.inbox_wake()`) for at most `_SCAN_INTERVAL`
(3s). `protocol.create_event` sets that signal whenever it writes a
`pending` event *in the daemon process* — a gate thread enqueuing a
message, a self-scheduled thought firing — so the loop reacts at once
instead of waiting out the tick. The loop clears the signal at the top of
each iteration *before* reading the inbox, so a set that lands mid-pass
keeps the flag raised and the next pass picks it up; no event is missed
and a busy iteration can't spin. The 3s tick still bounds latency for the
paths that can't raise the in-process signal: cross-process writers (the
`brr run` CLI writing an event file from another process) and time-based
work (due `schedule.md` entries). Outbound-only (`done`) events don't set
the signal — they're delivered by gate threads, not the spawn loop.

Each gate also reuses one `requests.Session` per gate module
(`_SESSION`), so keep-alive reuses the TCP/TLS connection across
long-poll cycles instead of dialing the platform fresh every poll. Each
gate runs its network calls from a single loop thread, so the per-gate
session needs no locking. The managed brnrd backend plugs its own async
`httpx` client and never touches the OSS sync transport. (Both landed
2026-06-14 for the Co-maintainer "daemon responsiveness" slice, #115 →
[`design-co-maintainer.md`](design-co-maintainer.md) §9; this is idle
latency, not added concurrency — single-flight is unchanged.)

### Self-scheduled thoughts (the resident's own clock)

The resident isn't only summoned — it wakes itself. Each reflex tick,
**before** listing pending events, the daemon reads the dominion's
`schedule.md` specs against a runtime firing-state and the clock
(`_fire_due_schedules` → [`schedule.py`](../src/brr/schedule.py)) and
fires any due entry as an ordinary `schedule`-source inbox event. Two
trigger forms, deliberately not cron syntax: `at:` (one-shot, absolute)
and `every:` (interval). A fired event queues behind a running thought
like any other — no new concurrency. Specs are owned + durable (dominion,
committed); firing-state is daemon-owned + ephemeral
(`.brr/schedule/state.json`), so the reflex never writes the agent's
`schedule.md` and firing never races the dominion commit lock. A gateless
schedule thought is retired by the daemon when it completes
(`_retire_internal_event`) — its effect is the work it did, not a chat
reply. See
[`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md).

The per-run isolation primitives the parallel design relied on
**survive** — they still earn their keep for crash recovery, ad-hoc
sessions, and the managed multi-daemon case:

- **Worktree / branch identity is per run.** Each run gets a fresh
  `brr/<run-id>` branch sprouted from the resolved seed ref into
  `.brr/worktrees/<run-id>/`. Run ids are globally unique
  (`run-<date>-<time>-<random>`), so run starts never collide on branch
  name or worktree directory.
- **Conversation log is one file per event pipeline.**
  `.brr/conversations/<key>/<event-id>.jsonl` holds every record one
  worker invocation emits; one writer per file, readers glob and merge
  by `ts`. The bundled doc
  ([`src/brr/docs/conversations.md`](../src/brr/docs/conversations.md))
  describes the user-visible side.
- **Gate progress card state is one file per run**
  (`.brr/gates/<gate>/progress/<run-id>.json`); the render path reads
  and writes only its own file.
- **Per-run artefacts** (`.brr/runs/<run-id>/run.md`, the response file
  at `.brr/responses/<event-id>.md`, trace dirs) are keyed by id.
- **Publish** (`daemon.publish`) takes a per-branch lock keyed on the
  branch being pushed. Within one single-flight daemon this is now
  uncontended (one publish at a time), but it still guards a daemon
  publish racing an ad-hoc session that pushes the same branch, and
  stays cheap. Finalize no longer participates — the env layer never
  updates a non-run ref since the 2026-05-21 publish-kernel collapse
  (see [`design-publish-kernel.md`](design-publish-kernel.md)). PR
  finalization no longer rides this step; the resident projects its
  diffense pack and sends `gate: forge`, which the GitHub delivery loop
  opens or refreshes.

**No command layer; liveness is a heartbeat-enforced, agent-extensible
budget.** The daemon never parses `/cancel` or any command — every event
either wakes the agent or waits for the living agent to handle it
(cancel/redirect semantics are the agent's job, reconsidered at plan
boundaries; the mid-flight inbox channel is the multi-response protocol,
specified in [`design-multi-response.md`](design-multi-response.md) —
interim, multiple, and interleaved replies ship via the agent's outbox).
What the daemon *does* guarantee is that the single-flight slot is
reclaimed: the heartbeat tick enforces a wall-clock budget
(`runner.timeout_seconds`, default 3600s) and kills an overrunning runner
via `runner.kill_active`; the runner's own `communicate` timeout, set to
a generous hard cap, is the final backstop if the heartbeat path itself
wedges. The budget is **agent-extensible** — a thought that knows it will
run long writes a `.keepalive` control file in its outbox (an ISO time or
a `+30m`-style duration) and the heartbeat holds the slot until then,
capped at the hard ceiling. `brr down` / SIGTERM also kill the in-flight
runner, so shutdown reclaims the slot promptly instead of waiting out a
long budget. A finer *silence-based* idle-kill ("no check-in in N
minutes") stays deferred: the budget is a flat timer, and an absent
check-in still can't separate a wedged process from a healthy-but-silent
one (a long build, deep reasoning) — see
[`design-multi-response.md`](design-multi-response.md) → liveness.
(Liveness shipped as a cooperative budget 2026-06-09; see
[`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md)
§2.)

## Worker lifecycle

For each pending event, the daemon:

1. marks the event `processing`;
2. fetches the default remote and best-effort fast-forwards the local
   default branch (and any structured branch named on the event) via
   [`sync.refresh_before_run`](../src/brr/sync.py) — the seed-ref
   invariant is described in
   [`design-git-layer-rework.md`](design-git-layer-rework.md);
3. resolves the branch plan, then creates and persists a `Run`;
4. records the thought in the presence registry (`presence.py`,
   `.brr/presence/`) so concurrent thoughts see who's on which stream;
5. prepares the selected env backend (`host`, `worktree`, or `docker`);
6. builds the daemon prompt with the Run Context Bundle (including the
   dominion digest, any dominion pitfalls whose triggers the run text
   hits — the env-shaping loop's failure-memory affordance,
   [`pitfalls.py`](../src/brr/pitfalls.py) — the wake-time pending-events
   snapshot plus live `inbox.json` path, and who else is present), plus
   brr's **driver's manual** (`daemon-substrate.md`)
   — the daemon-only machinery (single-flight, capture-at-sleep net,
   self-scheduled wakes) the host-agnostic playbook deliberately leaves out
   (see [`plan-playbook-generalization.md`](plan-playbook-generalization.md));
7. invokes the configured runner headlessly;
8. treats stdout as the default terminal reply while also accepting a
   current-thread outbox reply as a valid closeout; on each heartbeat it
   drains the agent's outbox, then refreshes the live inbox view (plus one
   final drain after the runner returns) — promoting interim or
   interleaved replies to per-event partial queues, delivering
   gate-addressed sends such as `gate: forge` PR publication, and routing
   cross-event conversation records to the target thread (the
   multi-response protocol,
   [`design-multi-response.md`](design-multi-response.md)). The same
   heartbeat tick also drains the agent's `.card` control dotfile into
   a `card_composed` packet when its content has changed, so the resident
   can narrate what its live progress card says (see
   [`design-co-maintainer.md`](design-co-maintainer.md) §8);
9. captures the resident's dominion edits via a serialized commit
   (`dominion.commit`; runs on success *and* failure — a failed thought
   may still have recorded pain), best-effort pushing the `brr-home`
   branch so the memory travels;
10. retries only when the addressed thread has no output yet; if the
    runner/env path ultimately fails or stays silent, writes an explicit
    terminal failure note for addressed events while keeping the run record
    `error`;
11. marks the inbox event `done` once it has something deliverable; the
    originating gate streams any queued interim partials, then the terminal
    reply if present, then cleans up;
12. finalizes the environment, classifying the worktree's final state
    into a `publish_status` and recording the branch to publish;
13. publishes that branch via `daemon.publish` under a per-branch lock
    (push only; PR open/refresh is agent-owned forge delivery), and
    deregisters the thought from the presence registry.

The durable user response is a delivery artifact, not synonymous with
stdout. Plain stdout captured by
[`runner.invoke_runner`](../src/brr/runner.py) remains the common terminal
reply, but the agent may satisfy the current thread through its outbox, fold
in another pending thread with `event: <id>`, or send a `gate:` message; brr
synthesizes an explicit failure note when an addressed event would otherwise
go silent. This contract is documented in
[`execution-map.md`](../src/brr/docs/execution-map.md) and enforced by the
daemon prompt assembled in [`prompts.py`](../src/brr/prompts.py).
Response delivery is intentionally released before environment
finalization and push: those stages are post-response housekeeping and
should not delay the operator seeing the result. The progress card can
continue to show finalization and push after the final reply is already
in the originating chat thread. (Deterministic kb-health now rides the
*wake* prompt rather than a post-run pass; see
[`subject-kb.md`](subject-kb.md).)

### Society-of-Mind concurrency (dominion + presence)

The daemon is single-flight, but the repo is *already* multi-thought:
ad-hoc sessions (Cursor, Codex, a hand-run agent) work alongside the
daemon and can touch the one shared dominion (`.brr/dominion/`,
`brr-home`) at the same moment. brr tolerates that rather than caging it
(the model is laid out in
[`design-agent-dominion.md`](design-agent-dominion.md) §4):

- **Serialized capture, free edits.** `dominion.commit` captures the
  resident's working-memory edits at sleep. Only the index-touching commit
  serializes — across processes, via an advisory `fcntl.flock` on
  `.brr/dominion.commit.lock` — so two thoughts never corrupt the shared
  git index, while their *file edits* run without coordination. A clean
  dominion is a silent no-op; the step is best-effort and never fails a
  run.
- **Presence registry.** `presence.py` keeps a lock-free, prune-on-read
  registry under `.brr/presence/` (one JSON file per participant). The
  daemon registers a thought at step 4, heartbeats it on the runner
  heartbeat, and deregisters at step 13; the wake bundle surfaces other
  live participants so a thought knows when it shares memory.
- **Reconciliation is judgement.** Contradictions left in shared memory
  are resolved by a later thought noticing and reconciling them (the
  playbook's inward salience loop), not by a lock or a deterministic
  detector. Eventual consistency — each thought sees memory as of its last
  read — is the accepted cost.

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
like opening a PR / MR belongs to a post-run hook, deferred so its
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
runs; the generated run context and bundled internals doc both frame
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
the generated run context, persisted run and conversation files,
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
- **Agent-driven cancellation + silence-based idle-kill.** The daemon
  honours no cancel command by design; the living agent handles
  cancel/redirect at plan boundaries. The liveness budget is now
  heartbeat-enforced and agent-extensible, and shutdown kills the
  in-flight runner (shipped 2026-06-09). What stays deferred is a
  *silence-based* idle-kill: a flat budget can't separate a wedged
  process from a healthy-but-silent one, so a shorter idle timeout still
  waits on a check-in the substrate can count on (see
  [`design-multi-response.md`](design-multi-response.md) → liveness and
  [`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md)
  §2).

(Lineage: a concurrent worker pool was once deferred and the original
merge-coordinator design abandoned; both were reversed 2026-05-16 when
concurrency shipped on the per-event/per-run partitioning above, so the
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
5. [`subject-runs-branching.md`](subject-runs-branching.md) and
   [`design-publish-kernel.md`](design-publish-kernel.md) for run
   construction, branch intent resolution, and the accepted publish
   kernel that replaced the predecessor land-then-push pipeline.
6. [`design-git-layer-rework.md`](design-git-layer-rework.md) for the
   pre-run fetch+ff invariant, the boundary between pure git refs
   (daemon) and forge concepts (per-provider gates), and the staged
   Phase 1 / 2 / 3 plan.
7. [`decision-drop-streams.md`](decision-drop-streams.md) and
   [`decision-remove-triage.md`](decision-remove-triage.md) for the
   recent simplifications that keep daemon context lean.
8. [`design-daemon-dev-reload.md`](design-daemon-dev-reload.md) for the
   current development reload proposal.
9. [`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md)
   for the in-flight coherence pass: the cooperative liveness contract,
   generic gate-addressed delivery, and the daemon-vs-agent ownership
   crossroads.
