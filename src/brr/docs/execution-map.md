# Execution Map

How an event flows through brnrd, and where each artifact lives.

This document ships with the `brnrd` tool. Users can override it
per-repo by dropping a file at `.brr/docs/execution-map.md`.

## Pipeline

```
event (inbox) -> run manifest -> context file -> run env -> response -> response release -> kb preflight -> finalize
```

### 1. Event arrives

A gate (Telegram, Slack, GitHub, future forge gates) or a script writes a
markdown file to `.brr/inbox/`. The file has frontmatter (`id`,
`source`, `status`) and a body with the user's message. The resident also
emits events to its **own** future: each reflex tick the daemon fires any
due entry from the dominion's `schedule.md` as a `schedule`-source inbox
event (`schedule.py`; `at:` one-shot / `every:` interval), so a
self-scheduled wake enters this same flow — see
`kb/design-self-scheduled-thoughts.md`.

### 2. Run manifest created

The daemon constructs a `Run` directly from the event with
`Run.from_event` — no LLM-driven triage step. `Run`/`run_id` is the
current storage name; the prompt and docs now frame the product unit as
a daemon run. Environment policy is resolved deterministically from the
event source and `.brr/config`.
The manifest is saved to `.brr/runs/<run-id>/run.md` and tracks: event
ID, env, status, source, and manifest metadata (response path, branch
name, worktree path, run context path, trace directories). Manifests
store the concrete backend as `env`; user-facing config should prefer
`environment`.

Branch behavior is no longer carried on the manifest. The daemon resolves a
branch plan before env prep: seed ref, optional auto-land target, and
authority. Worktree and Docker runs start on a fresh `brr/<run-id>`
branch sprouted from the seed ref. New run IDs start with `run-`, so the
default branch is `brr/run-...`. If the plan has no auto-land target,
commits on that run branch are preserved for human routing and
published when a remote is configured. The agent can still switch to a
named branch at runtime; brnrd preserves the branch it ends on.

### 3. Execution

The daemon hands the run off to one of the env backends — `host`,
`worktree`, or `docker` today. Each backend prepares the working
directory, invokes the runner, and finalizes the result. See
[`envs.md`](envs.md) for the full breakdown: when to pick each, the
docker credential wiring, the durability contract, and the salvage
rule.

The runner receives `run.md` + recent `kb/log.md` context + daemon
metadata (run ID, event ID, execution root, seed ref, optional
auto-land target, current branch, response path, interim-response outbox,
other pending events, live `portal-state.json` / `inbox.json` paths,
shared runtime dir, generated run context file).
The bundle's delivery contract is explicit: stdout is the plain
current-thread fallback, and the run should otherwise leave an operational
receipt while using explicit portals for anything meant to communicate. kb
writes are optional — agents log only when there's something worth logging
(see AGENTS.md → Knowledge base).

Prompt assembly first injects `identity-core.md`, the product-owned
resident contract, then the resident's dominion digest (per its
`self-inject` index). It also injects task-scoped surfaces such as matched
pitfalls, recent `kb/log.md` activity, and, when the deterministic kb
preflight isn't clean, a `kb health` block of findings for the resident to
fold into its work (see [`internals.md`](internals.md) → KB
maintenance). The daemon path additionally injects `daemon-substrate.md` —
brnrd's driver's manual for the daemon-only machinery (single-flight, the
capture-at-sleep net, self-scheduled wakes) that the host-agnostic dominion
playbook leaves out; `brnrd run` skips it. `brnrd agent inject` prints this
assembled wake-context (identity core + dominion digest + matched pitfalls +
recent log) so a non-brr wrapper can reuse the same orientation semantic.

### 4. Response

When the resident chooses a plain current-thread reply, brnrd captures stdout
and writes it to `.brr/responses/<event-id>.md`. Runners are invoked
headless (`claude --print`, `codex exec`, `gemini -p --yolo`); progress
goes to stderr, so no per-runner output flag is needed for the common
stdout-capture case.

Responses are plain text — there is no frontmatter contract. If the
agent cannot complete the task (missing context, ambiguous request,
unreachable service), it should say so plainly in the response and
stop. The operator sees the reply in the gate thread and follows up
with another event.

Once the run has a recognized operational signal for the addressed thread,
the daemon marks the inbox event `done` before environment finalization or
branch push. Gates deliver `done` events and clean up the inbox and response
files after a successful send, while the progress card can continue to show
post-response housekeeping.

The agent may *also* stream replies mid-thought (the multi-response
protocol; see [`internals.md`](internals.md) → Multi-response).
It drops markdown files in its per-event outbox (`.brr/outbox/<event-id>/`).
Tier-2 runner boundaries synchronously request and await promotion; heartbeat
polling plus a post-return recovery check preserve Tier-0/1 correctness. Each
message is promoted to a per-event partials queue
(`.brr/responses/<event-id>.partials/`). Gates stream queued partials —
for `processing` or `done` events — ahead of the terminal reply. An
outbox file whose frontmatter names another pending event
(`event: <id>`) is delivered to *that* event's thread and marks it
handled, so a quick request can be folded in without its own spawn; the
conversation artifact is recorded on the target event's thread.
An outbox file with `gate: <name>` is an out-of-bound send. The shipped
`gate: forge` is the explicit PR handoff: it uses the GitHub gate to
open or refresh a PR from the file's `head`, `base`, and `title`
frontmatter plus the body. Diffense can generate that title/body from a
checked review pack, but the forge send itself is not diffense-owned.
Richer branch-keyed PR desired state remains future portal work.
The same outbox directory also carries daemon-owned `portal-state.json`
and `inbox.json` control files refreshed on each heartbeat. The state
portal is the broad live view (pending events, card/delivery posture,
budget/keepalive, change token); `inbox.json` is the focused pending-event
list. The running agent checks them at plan boundaries and once more
before terminal closeout to decide whether to fold waiting work in or
leave it queued.

After the runner returns, the daemon also **captures the resident's
dominion** in the account dominion repo (repo-scoped resident memory under
`repos/<repo>/dominion/`, with a legacy `.brr/dominion/` fallback during
migration) with a serialized commit — on success and failure alike — so
working-memory edits survive to the next wake without the agent committing by
hand. The commit step is serialized across processes by a file lock so a
concurrent ad-hoc session never races the shared git index. A remote push
happens only when the account dominion repo already has a remote; brnrd does not
create a forge repo by default.

If the runner exits cleanly but produces no satisfying signal, the daemon
retries up to `response_retries` times before failing the run. Hard failures
(non-zero exit, timeout — controlled by `runner.timeout_seconds`, default
3600s) are not retried. In both cases, an addressed event that would
otherwise go silent receives an explicit terminal failure note; the run
record remains `error`, while the inbox event is marked `done` so the gate
can deliver and clean up.

### 5. Finalization

For worktree-backed runs, the daemon inspects the worktree's git state. If
the agent left commits on the original `brr/<run-id>` branch and the
branch plan has an auto-land target, that target is fast-forwarded.
With no target, or when the agent moved to another branch, the branch
is preserved as-is. If the target cannot fast-forward, the run becomes
`conflict` and the run branch is preserved. The worktree is removed
on a clean success with nothing uncommitted left behind; failures,
conflicts, and uncommitted/untracked leftovers keep the worktree for
inspection.

On the give-up path (timeout, runner error, quota exhaustion) the daemon
first runs a salvage net (`_capture_worktree`): it commits any in-flight
edits on the work branch and arms `publish_branch` so the publish step
ships the branch to the remote — finalize otherwise resolves a publish
outcome only for a `done` run, so without this a killed run's work (even
already-committed commits) would sit local-only in the preserved worktree.
Best-effort, gated by `salvage.enabled` (default on), and silent when the
branch carries no commits beyond the seed.

When `brnrd up --dev-reload` or `dev_reload=true` is active, this is also
the safe boundary where the daemon may re-exec itself if brnrd package
files changed. Reload never interrupts a running worker.

## Artifact locations

| Artifact      | Path                                        | Persists across runs                |
| ------------- | ------------------------------------------- | ----------------------------------- |
| Events        | `.brr/inbox/<event-id>.md`                  | Yes (until cleanup)                 |
| Run manifests | `.brr/runs/<run-id>/run.md`             | Yes                                 |
| Responses     | `.brr/responses/<event-id>.md`              | Yes                                 |
| Interim queue | `.brr/responses/<event-id>.partials/`       | Until streamed + cleaned up         |
| Agent outbox  | `.brr/outbox/<event-id>/`                   | Drained mid-run; live `portal-state.json` + `inbox.json`; removed at finalize |
| Presence      | `.brr/presence/<id>.json`                   | While a thought/session is active; pruned on read |
| Account dominion | `~/.local/state/brnrd/accounts/<account>/dominion/` by default | Local-first git repo; committed at sleep; remote durability is opt-in |
| Resident memory | `repos/<repo>/dominion/` inside the account dominion repo | Owned working memory (`self-inject`, playbook, pitfalls, schedule); legacy fallback `.brr/dominion/` |
| Schedule state | `.brr/schedule/state.json`                 | Machine-persistent (firing-state); specs live in resident `schedule.md` |
| Run context   | `.brr/runs/<run-id>/context.md`             | Yes                                 |
| Wake prompt   | `.brr/runs/<run-id>/prompt.md`              | Yes — persists through success so "what did this wake see?" is always answerable |
| Traces        | `.brr/traces/<kind>/<label>-<timestamp>/`   | Kept on `error` / `conflict`, removed on clean `done` |
| Reviews       | `.brr/reviews/`                             | Reserved for explicit review artifacts; not part of the default lifecycle |
| Worktrees     | `.brr/worktrees/<run-id>/`                  | Removed on clean success; kept on failure / conflict / uncommitted leftovers |
| Gate state    | `.brr/gates/<gate>.json`                    | Yes                                 |
| Config        | `.brr/config`                               | Yes                                 |

There are no per-run kb log files. Durable project knowledge goes in
`kb/` only when the task produced material worth preserving; `kb/log.md`
is the curated chronological narrative, not a mandatory completion
receipt.

## Cross-linking

The run manifest (`.brr/runs/<run-id>/run.md`) is the central runtime
record.
Its frontmatter contains:

- `event_id` → links to `.brr/inbox/` and `.brr/responses/`
- `branch_name` → the git branch used
- `seed_ref` / `target_branch` → the resolved publish plan
- `publish_branch` / `publish_status` → recorded by finalize for the
  publish step (status is `ready` | `nothing` | `detached` |
  `conflict`)
- `worktree_path` → the worktree directory (if applicable)
- `context_path` → generated run context file
- `response_path` → the response file
- `trace_dirs` → comma-separated trace directories under `.brr/`
