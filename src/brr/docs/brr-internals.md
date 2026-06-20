# brr Internals

Orientation for an agent running under brr. This document ships with
the `brr` tool itself — it is not project-specific.

If you (the agent) are running and something about the environment is
confusing (unfamiliar folders, unexpected metadata in your prompt,
runtime paths you need to re-check), consult the generated run context
file before guessing.

## You might be running under brr

You can tell you are running under a brr-driven invocation by the
following signals in your prompt:

- An `Event:` and/or `Run ID:` line in the metadata block.
- A `### Delivery contract` block telling you how stdout and the run
  outbox map to user-visible deliveries, plus the specific
  `.brr/responses/<event-id>.md` path used for captured stdout.
- A `Shared runtime dir:` pointing at the main checkout's `.brr/`.
- A generated `.brr/runs/<run-id>/context.md` file named in the
  Run Context Bundle.

When you see these, you are not in a normal interactive session. You
are one step of a pipeline. Behave accordingly:

- Do your best-effort work within the task scope.
- Do not invent extra work to be "helpful".
- Do not explore or edit `.brr/` beyond what the task explicitly
  requires. It is runtime scratch space.
- Your exit / final message will be captured and forwarded to a human
  over a gate (Telegram, Slack, Git PR comment), so keep it focused.

## `.brr/` layout

All runtime state lives under `.brr/` at the repo root. It is
gitignored; do not commit its contents.

| Folder       | Purpose                                                            |
| ------------ | ------------------------------------------------------------------ |
| `inbox/`     | Incoming events from gates, one markdown file per event            |
| `runs/`      | Per-run directories: `run.md` manifest, `context.md`, `prompt.md`, grouped history |
| `responses/` | Agent final responses destined for gate replies; per-event `<id>.partials/` hold queued interim replies |
| `outbox/`    | Per-event drop zone (`<id>/`) where the resident writes interim/interleaved replies mid-thought |
| `presence/`  | Who's awake right now — one JSON file per active thought/session, pruned on read |
| `dominion/`  | The resident's durable working memory (worktree on the `brr-home` branch); captured at sleep |
| `schedule/`  | Firing-state (`state.json`) for self-scheduled thoughts; specs live in the dominion's `schedule.md` |
| `conversations/` | Per-gate-thread append-only logs of events, runs, artifacts, lifecycle updates |
| `traces/`    | Prompt + stdout + meta per runner invocation (cleaned on success)  |
| `reviews/`   | Reserved for explicit review artifacts; default runs do not write here |
| `worktrees/` | Isolated git worktrees for concurrent runs                         |
| `gates/`     | Per-gate auth/state JSON                                           |
| `prompts/`   | Legacy per-repo prompt overrides                                   |
| `docs/`      | User overrides of bundled docs (see below)                         |
| `config`     | Key=value runtime config                                           |

## Agent recovery surface

Agents should orient from the Run Context Bundle in the prompt. When
they need to re-check runtime details, they should read the generated
`.brr/runs/<run-id>/context.md` file named in the bundle. That file
replaces the old command cheat sheet for run/event recovery.

The agent does not run daemon lifecycle commands. `brr up` and
`brr down` are managed by the human operator.

## Developer reload

For brr self-development, use an editable install and start the
foreground daemon with:

```
brr up --dev-reload
```

This is an opt-in developer mode, not the default daemon lifecycle. It
watches brr's installed package files (`.py`, bundled markdown,
`Dockerfile`, and source-layout `pyproject.toml` when visible). When a
change is detected, the daemon re-execs the same Python command at a
safe boundary: before starting the next pending run, or after the
current run has produced its response, finalized, and attempted push.

The same mode can be enabled with `dev_reload=true` in `.brr/config`.
Normal `brr up` stays a stable foreground process; use an external
supervisor if you want restart policy outside local development.

## Override model

brr ships prompts, docs, and default runner profiles with the package.
Lightweight runtime choices belong in `.brr/config`, especially `runner`,
`runner_cmd`, and environment policy. Project-owned runner profiles live
in `.brr/runners.md`; the legacy `.brr/prompts/runners.md` path is still
accepted as a compatibility override, but runner profiles are execution
medium data rather than prompt templates. Deep prompt or orchestration
customization is done by using a local checkout, editable install, or fork
of brr.

Use `environment` for the user-facing execution policy:

- `environment=auto` — prefer configured Docker isolation, then fall
  back to worktree behavior.
- `environment=docker` — require Docker and `docker.image`.
- `environment=worktree` — run in a separate git worktree.
- `environment=host` — run directly in the main checkout (no isolation).

The legacy `env` and `default_env` config keys are still accepted, but
new config should use `environment`.

Branching is no longer carried on the event file. Before env prep the
daemon resolves a publish plan: seed ref, optional
`target_branch` (when the event named one), source string,
host checkout branch as context, and an optional `expected_remote_oid`
captured from the remote-tracking ref at run start for force-with-
lease pushes. Worktree and Docker runs always start on a fresh
`brr/<run-id>` branch sprouted from the seed ref. New run IDs start with
`run-`. After the run,
finalize records `publish_branch` + `publish_status` on the manifest and
`daemon.publish` ships that branch — via a refspec push when the
agent kept the run branch but the event named a different expected
publish target, a leased force-push when the agent rewrote the
expected branch, or an ordinary push otherwise.
`branch.fallback` (or the legacy spelling `branch_fallback`) controls
the no-authority fallback. The only supported mode is `preserve`
(the default). Legacy values (`inbox`, `default`, `current`) warn once
on daemon start and downgrade to `preserve`.

Legacy per-repo override folders may still be read by the library, but
there is no public command to seed them:

| Bundled at                     | Per-repo override         |
| ------------------------------ | ------------------------- |
| `src/brr/prompts/<name>.md`    | `.brr/prompts/<name>.md`  |
| `src/brr/docs/<name>.md`       | `.brr/docs/<name>.md`     |

The runner checks prompt overrides first, then falls back to the
bundled copy. Docs helpers do the same for doc overrides when used
internally.

Project-specific knowledge belongs in `kb/` (the knowledge base),
never in `.brr/`. The split is:

- `kb/` — permanent, project-specific, committed to the repo. Owned by
  agents working in this repo.
- `.brr/` — tool runtime. Ephemeral by default; traces and any task
  failures/leftovers stay for inspection.
- `src/brr/docs/` (bundled) + `.brr/docs/` (override) — tool
  documentation, same across all repos unless a user overrides.

## KB maintenance: deterministic preflight, injected on wake

brr runs a deterministic kb consistency scan
(`brr.kb_preflight.scan(repo_root)`) over `kb/` as part of prompt
assembly (`prompts._build_kb_health_block`). When the scan finds
anything, the findings — plus a one-line graph-stats summary
(`brr.kb_health`) — ride into the resident's wake prompt as a
`kb health (deterministic preflight)` block, and the resident folds
fixes into its own thought. A clean scan injects nothing.

There is **no separate post-task kb-maintenance agent**. (Earlier
versions spawned a second LLM pass after every kb-touching task, with
its own `prompts/kb-maintenance.md`; removed 2026-06-08 — the resident
curates the shared kb as part of its single thought, with the
deterministic scan as the standing safety net. See
`kb/design-agent-dominion.md` and `kb/subject-daemon.md`.)

The preflight is cheap and structural — it only flags things a
deterministic scanner can be confident about:

- `missing-from-index` — a kb page exists on disk but isn't linked
  from `kb/index.md`.
- `stale-index-entry` — `kb/index.md` links to a path that doesn't
  exist on disk.
- `broken-link` — any kb page (other than `log.md`) links relatively
  to a path that doesn't exist.

Lifecycle-marker drift, contradictions with the log, and other
judgement calls aren't the scanner's job — they need synthesis the
resident does directly as it works.

### Why a deterministic safety net

Deterministic checks are cheap enough to run on every wake, so they
catch drift left by *previous* work too (say, a slashed page another
page still links to), surfaced where the resident is already working
rather than in a separate pass that has to be spawned and that
historically dropped its edits silently.

### Configuring it

In `.brr/config`:

- `kb_maintenance=auto` (default) — inject preflight findings on wake
  whenever the scan isn't clean.
- `kb_maintenance=never` — never inject; do kb hygiene by hand.

## Multi-response: situational outputs

Stdout is the plain current-thread fallback, but delivery is not defined by
stdout alone. The daemon needs a recognized operational signal that the run
did not disappear; the resident uses explicit portals when it intends to
communicate. The mechanism is a file drop zone, mirroring the diffense
precedent (agent writes a host-visible artifact, then addresses the delivery
path explicitly):

- **Drop zone** — `.brr/outbox/<event-id>/`. The resident writes a
  complete markdown reply per file (staging as `*.tmp` and renaming for
  an atomic write). The path rides the Run Context Bundle's delivery
  contract. The daemon also reserves `inbox.json` in this directory as a
  live view of other pending events; it is control state for the agent to
  read at plan boundaries, not a deliverable message.
- **Drain** — on every heartbeat tick and once right after the runner
  returns, the daemon (`daemon._drain_outbox`) scans the drop zone
  oldest-first, promotes each file to a per-event partials queue
  (`protocol.write_partial` → `.brr/responses/<id>.partials/<seq>.md`),
  emits an `interim_response` packet, indexes the artifact on the
  conversation log, and removes the consumed file. The same heartbeat
  refreshes `inbox.json`. A promoting drain is a positive liveness
  check-in.
- **Streaming delivery** — `runtime.deliver_stream` walks **active**
  events (`processing` *or* `done`): it delivers queued partials in
  order, deleting each after a successful send (so delivery is
  resumable), and only on `done` delivers the terminal `<id>.md` and
  cleans up the event, terminal file, and partials dir. Telegram runs
  this delivery scan in a small outbound loop separate from its
  `getUpdates` long poll, so interim and folded replies are not delayed
  by inbound polling.
- **Silent-run fallback** — when an addressed event reaches the end of the
  runner/env path without any satisfying signal, the daemon writes an
  explicit terminal failure note and marks the inbox event `done` so the
  gate closes the thread. The run record still stays `error`, preserving
  the operational truth.
- **Interleaving** — an outbox file whose frontmatter names another
  pending event (`event: <id>`) is routed to *that* event's queue and
  that event is marked `done`, so its thread gets the reply and it never
  wakes as its own thought. The conversation record is written to the
  target event's thread, not the current event's thread. The bundle lists
  the wake-time pending events, and `inbox.json` keeps that view fresh while
  the thought runs. Unknown targets are dropped, not misrouted.
- **Gate-addressed sends** — an outbox file whose frontmatter names
  `gate: <name>` is an agent-initiated send with no waiting event. The
  daemon creates an already-`done` event for that gate and the gate's
  normal delivery loop sends it once. `gate: forge` is an alias for the
  GitHub delivery path; it opens or refreshes a PR from `head`, `base`,
  `title`, and the file body.

This is additive and backward compatible: a thought that prints one final
stdout and writes nothing to its outbox behaves as before, while failed or
silent addressed runs now produce an honest closeout instead of disappearing.
A finer *silence-based* idle-kill is *not* built on this — interim
check-ins are opportunistic, so their absence doesn't reliably mean
wedged. The liveness budget itself (`runner.timeout_seconds`) is now
heartbeat-enforced and agent-extensible: a long-running thought writes a
`.keepalive` control dotfile in its outbox (an ISO time or `+30m`-style
duration) to push the deadline out, capped at a hard ceiling, and
shutdown kills the in-flight runner to reclaim the slot. The full
protocol contract lives in `kb/design-multi-response.md`; the liveness
contract in `kb/review-daemon-coherence-2026-06.md` §2.

The resident may also **compose what its live progress card says** by
writing a `.card` control dotfile in the same outbox directory. The
daemon drains it on each heartbeat (and once more after the runner
returns), emits a `card_composed` packet only when the content has
changed, and the renderer surfaces the text as a `note: …` tail line
under the live phase. Rewrite the file to update; empty or delete it
to withdraw. The daemon stays the renderer (header, sync line,
phase log, terminal state); brnrd stays a transient relay. See
`kb/design-co-maintainer.md` §8 and `kb/design-managed-delivery.md`.

## Run progress UX

The daemon emits typed lifecycle packets through `brr.updates` for
every run: `run_created`, `env_prepared`, `container_started`,
`attempt_started`, `attempt_failed`, `retrying`, `run_started`,
`artifact_created`, `interim_response`, `card_composed`, `finalizing`,
`container_preserved`, `push_started`, `push_done`, plus the terminal
`done` / `failed` / `conflict`. `card_composed` is the resident's
narration of its own progress (see the outbox `.card` seam above):
it lands on `RunProgressView.agent_card_text` and the renderer surfaces
it as a `note:` tail line.

Gates may opt in to a `render_update(brr_dir, packet)` hook to surface
progress to a human:

- The Telegram gate sends one progress message per run in the
  originating chat or topic on `run_created`, then edits the same
  message via `editMessageText` for later packets. Per-run state lives
  under `.brr/gates/telegram/progress/<run-id>.json` so concurrent
  workers never share a file.
- The Slack gate posts one threaded reply per run on `run_created`,
  then updates it with `chat.update`. Per-run state lives under
  `.brr/gates/slack/progress/<run-id>.json` on the same one-writer
  guarantee.
- Non-chat gates (script gates, future forge gates posting on issues
  or PRs) typically skip live progress and let the durable artifact —
  a commit, a comment, a delivered file — speak for the run.

Live progress is remote-first. There is no public local status or
inspect command; new lifecycle UX should flow through update packets,
`RunProgressView`, and gate renderers instead of reintroducing a
separate status module.

## Concurrency model

The daemon runs **single-flight**: one *thought* at a time, by design. A
resident agent's continuity lives in durable memory (the dominion), not in
throughput-parallel workers, so the local loop spawns one worker when idle
and lets new events wait. Per-run worktree/branch isolation and the
partitioned per-event/per-run state still hold — they let overlapping
thoughts (ad-hoc sessions, a second daemon) coexist without sharing a
mutable surface, coordinated by presence rather than a lock. See
`kb/subject-daemon.md` and `kb/design-agent-dominion.md` §4. Whether the
daemon should grow back toward owned concurrency is an open question — see
`kb/review-daemon-coherence-2026-06.md` §4.

When a worktree-backed run finishes, the daemon inspects the
worktree's git state. If the agent left commits on the original
`brr/<run-id>` branch and the branch plan has an auto-land target,
that target is fast-forwarded. If there is no target, or if the agent
created/checked out a different branch, the resulting branch is
preserved as-is. Conflicts preserve the run branch. The worktree is
removed only on a clean success with no uncommitted/untracked
leftovers; failures, conflicts, and dirty leftovers keep the worktree
for inspection. Docker runs use the same worktree-backed branch
behavior with the same outcome-aware cleanup applied to the container
itself, and run the runner command inside the configured container
image.

The full env story — built-ins, configuration knobs, the docker
credential wiring, the durability contract, and the salvage rule —
lives in [`envs.md`](envs.md).

## Traces and forensics

Every runner invocation writes a trace directory under
`.brr/traces/<kind>/<label>-<timestamp>/` containing the prompt,
stdout, stderr, meta JSON, and any artifacts the runner produced.
Traces are always *written* — there is no operator switch — but
they're forensic-only: the daemon removes them when the run
finishes cleanly. Failures (`error`) and unmerged outcomes
(`conflict`) keep their traces so you can correlate the run-context
file with what the agent actually saw and said.

`.brr/` is gitignored, so traces stay local to whoever ran the
daemon. The durable record of a successful run is the git commit,
the response file at `.brr/responses/<event-id>.md`, and any kb
updates the agent committed — the trace would only repeat that
information.
