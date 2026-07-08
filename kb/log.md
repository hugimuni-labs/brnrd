# Activity Log

Newest entries at the bottom. Keep repo-root `.gitattributes` with
`kb/log.md merge=union` so parallel merges usually combine appended entries;
append new sections only, not concurrent rewrites of the same lines. Format:

## [YYYY-MM-DD] <type> | <title>

<description>

---

## [2026-04-07] implement | Initial setup

Set up AGENTS.md and knowledge base structure.

## [2026-04-07] plan | Concurrent worktree-based task execution

Designed a multi-phase plan for replacing the serial daemon loop with concurrent
task execution using git worktrees. Key components: `worktree.py` (lifecycle),
`pool.py` (worker pool + merge coordinator), daemon v2 loop. Each task gets an
isolated worktree on a `brr/<event-id>` branch, merged back sequentially.
Full plan in `kb/plan-concurrent-worktrees.md`.

## [2026-04-07] research | Branch & review strategy for agent commits

Explored how to make agents use branches/PRs instead of committing to main.
Proposed a tiered approach: (1) default: branch-and-wait (universal, just git),
(2) enhanced: branch-and-PR when `gh`/`glab` detected, (3) opt-in: direct commit
via `review=false` config. This aligns with the worktree plan — branches already
exist, just need a review gate before merging. Key decisions still open: how
AGENTS.md should express this (generic vs injected), and notification mechanism
when no PR tooling is available.

## [2026-04-08] plan | Reconciling worktree vs. existing-branch modes

Designed a unified approach for two execution cases: (A) orchestrator-managed
worktree isolation for new tasks, and (B) project-managed existing branches
(e.g., Linear ticket branches). Core insight: the agent instruction is the same
("commit on current branch") — the difference is orchestrator setup. Key
decisions: `branch` event field selects mode (auto/current/<name>), state files
are injected into prompts rather than copied into worktrees, kb/log.md
responsibility shifts to orchestrator in worktree mode to avoid merge conflicts.
Mode-specific prompt templates override base AGENTS.md instructions where needed.
Full plan in `kb/plan-branch-modes.md`.

## [2026-04-08] plan | v2 revision of branch modes & concurrent worktrees

Major revision of both plans based on user feedback. Key changes:
(1) Branch is now a task property (not event property) — runner creates a Task
from an event and the task spec carries the branch strategy, offloading the
decision to the agent. (2) Per-task log files (`kb/log-<task-id>.md`) replace
the orchestrator-written-log approach — agents write normally, orchestrator
squashes post-merge, zero conflict risk. (3) `needs_context` is a first-class
task outcome — agent can successfully exit asking for more info. (4) Execution
environment abstraction (local/worktree/docker) with `env` field on task.
(5) AGENTS.md stays generic, mode-specific overrides via prompt injection.
(6) Branch creation is a supported flow (not an error).
Updated `kb/plan-branch-modes.md` (v2) and `kb/plan-concurrent-worktrees.md` (v2).

## [2026-04-08] implement | Task abstraction, context injection, triage prompt

Implemented three connected features from the v2 plans:

1. **Task dataclass** (`src/brr/task.py`): Event → Task conversion with branch
   strategy, env type, status (including `needs_context`), persistence to
   `.brr/tasks/`, and branch name resolution. The triage agent decides the
   branch property when converting events into tasks.

2. **Conversation context injection** (`runner.py`): Recent kb/log.md entries
   are now injected into every agent prompt, giving session-to-session continuity
   without manual context sharing. Capped at 10 most recent entries to prevent
   prompt bloat. The log is agent-maintained per AGENTS.md conventions.

3. **Triage prompt** (`prompts/triage.md`): Template for the agent that converts
   raw events into Task specs (deciding branch strategy and execution environment).

4. **Daemon integration** (`daemon.py`): Worker now creates and persists Tasks,
   tracks status throughout execution, and detects `needs_context` responses.

Closed open question #4 (task persistence) in both plan documents.
All 56 tests pass (26 new).

## [2026-04-10] fix | Use non-interactive Codex runner

Updated the built-in `codex` runner profile to use `codex exec --full-auto`
instead of the interactive `codex --full-auto` path, which was failing under
the daemon with `stdout is not a terminal`. Added a regression test covering
the generated Codex command.

## [2026-04-10] fix | Make Codex daemon writes reliable

Troubleshot a second Codex daemon failure where runs exited successfully but
never created `.brr/responses/<event>.md`. Root cause: Codex's default sandbox
was blocked on this Linux host (`bwrap ... Operation not permitted`), and brr
also relied on the agent manually writing the response file. Updated daemon
invocations to pass Codex `--output-last-message <response-path>` and to append
`--dangerously-bypass-approvals-and-sandbox` when `auto_approve=true`, plus
clarified the daemon prompt and added regression coverage.

## [2026-04-10] review | PR #1 task abstraction review

Reviewed PR #1 deeply against the code path actually exercised by the daemon.
Found a larger gap where the new triage prompt is present but not wired into
execution, so branch/env are still not agent-decided in practice. Also fixed
two concrete issues in the working tree: daemon event files now preserve the
real task outcome (`needs_context` / `error` instead of always `done`), and
`Task.from_event()` now honors explicit event `branch` / `env` overrides.
Added daemon and task regression tests and recorded the review in
`kb/review-pr-1.md`. Verified with `PYTHONPATH=src pytest` because the current
virtualenv imports `brr` from `.venv/site-packages` rather than `src/`.

## [2026-04-14] fix | Wire daemon triage into task execution

Fixed the remaining PR #1 review gap by making the daemon run a real triage
step before execution instead of creating tasks directly from raw events.
Triage output is now parsed into a persisted `Task`, malformed triage output
fails closed with task/event status `error`, and branch/env/body decisions now
actually affect execution. Also reduced duplicated prompt assembly in
`runner.py`, clarified the triage prompt's branch/env relationship, and added
regression coverage for valid and invalid triage output. Verified with
`PYTHONPATH=src pytest tests/test_task.py tests/test_runner.py tests/test_daemon.py`.

## [2026-04-14] review | Concurrency follow-up review

Re-reviewed the code after the triage wiring change, focusing on whether the
planned merge coordinator and concurrent worktree execution now exist in code.
Conclusion: task branch/env/needs-context scaffolding is implemented and
coherent, but the actual concurrency path is still not present — no
`worktree.py`, `pool.py`, merge-back flow, or daemon pool dispatch yet, and
`daemon.py` remains serial v1. Recorded the review in
`kb/review-concurrency-followup-2026-04-14.md`, clarified what "concurrent
execution" means in the plan, and recommended deferring cancellation until
after the worktree/pool path exists.

## [2026-04-14] fix | Make worktree tasks execute on real branches

Implemented the first runtime slice from the concurrency follow-up review.
`daemon.py` now creates a real git worktree when a triaged task requires one,
runs the agent in that isolated checkout, and finalizes the branch explicitly
after success. Auto/task branches are merged back to the current branch via a
new `gitops.merge_branch()` helper, while named branches are preserved and only
their temporary worktree is removed. Added `src/brr/worktree.py` for worktree
lifecycle management plus daemon/git regression tests. Verified with
`PYTHONPATH=src pytest`.

## [2026-04-14] fix | Trace runner invocations and validate outputs consistently

Added a runner-level invocation contract in `runner.py` with explicit required
artifacts, validation status, and retry reasons, then persisted each invocation
under `.brr/traces/<kind>/...` with the prompt, stdout/stderr, metadata, and
copies of produced required files. Updated daemon execution and triage to use
that contract so response retries derive from missing validated artifacts rather
than ad hoc file checks, and updated `brr init` to validate AGENTS/kb outputs
through the same interface. Added regression coverage for trace persistence,
missing-output validation, daemon retries, and init/integration call sites.
Verified with `PYTHONPATH=src pytest`.

## [2026-04-20] plan | Fleet & steering design (overlays, brnrd, envs)

Took the personal-workflow-variants idea to a full three-axis design and
delivered two Marp decks as consulting-style artefacts:
`kb/deck-brr-current.md` (bird's-eye of the system today: file protocol,
pipeline, CLI surface, where state lives, current override model) and
`kb/deck-brr-fleet-steering.md` (the future design).

Locked decisions: single-slot overlay profile, pull-on-next-run, overlay scope
= prompts + config defaults (not docs), `~/.config/brr/` ownership, worktree
demoted to one env among several with no concurrent pool in v1, fleet UX ships
as `brnrd` (registry + broadcaster first; supervisor daemon later).

Recommended roadmap: Phase 1 overlays (~200 LOC), Phase 2 `brnrd` registry +
`brnrd all`, Phase 3 `Env` protocol refactor, Phase 4 first non-worktree env
(docker) + optional `brnrd up` supervisor. Phase 1 alone unblocks the
one-edit-N-repos-converge demo that sells the whole thesis. Marked
`idea-personal-workflow-variants.md` as absorbed into the new decks.
No code changes; read-only design pass.

## [2026-04-20] plan | Fleet & steering v2 — git-backed overlay + env parallelism correction

Revised `kb/deck-brr-fleet-steering.md` after user review. Two changes:
(1) Blessed overlay transport is now a git clone of a user-owned repo, making
overlays remote-editable (push from phone/web IDE; fleet converges via
`overlay_sync=auto|always|never` and `brnrd overlay sync`). Added a dedicated
slide and updated the gitlab demo to showcase push-based rollout. (2) Corrected
the parallelism framing in A3: concurrency is *not* a property of the env type.
All non-local envs are ephemeral by construction, so every env shares the same
durability contract (commit + push + response in `finalize`), and real
parallelism is gated by the merge coordinator above the Env layer — not by
whether the env is worktree, docker, or kube. Added a "durability contract"
slide and a "where parallelism lives" slide with a mermaid diagram. Roadmap
gained Phase 5 "merge coordinator → true concurrency" decoupled from env work.
Updated decisions-locked table (overlay transport, env model, parallelism
source rows added) and the idea-page resolution section. Still read-only.

## [2026-04-20] plan | Refocus on envs; capture pondering for overlays/brnrd

Pivoted the active work from the strategic three-axis design back onto the
in-flight worktree PR. New `kb/design-env-interface.md` is the actionable
spec to finish that PR: extract an `Env` Protocol with `prepare/invoke/finalize`,
codify the durability contract (only git refs + the response file survive an
ephemeral env), ship four built-ins (`local`, `worktree`, `docker`, `ssh`),
and replace the deferred "merge coordinator" with a decentralised model —
every task produces a branch, `auto`/`task` strategies attempt
`git merge --ff-only` with a `conflict` fallback, named branches stay for
human/PR review. Third-party envs ship via the `brr.envs` entry-point group.
Concurrency stays serial in v1; the v2 unlock is a host-HEAD mutex, not a
component. Daemon code shrinks because env-specific branches collapse into
the protocol calls.

Captured the parallel pondering in `kb/notes-pondering-fleet.md` so it does
not block the env work: likely-drop of `brr eject`, lean toward a
single-file overlay (`~/.config/brr/overlay.md` appended to every prompt
rather than a multi-file lookup chain), self-maintaining registry under
`~/.local/state/brr/repos.json` for discovery without home-scanning, brnrd
as an agentic operator (separate project, hosted, uses the existing brr
file-protocol as its interface), cross-platform supervisor sketch
(systemd / launchd / docker), and a use-case table that grounds the
no-coordinator framing. Updated `kb/index.md` with both new pages.
No code changes; planning pass to drive the next implementation slice.

## [2026-04-23] research | brr vs GitHub Agentic Workflows (gh-aw)

Deep comparison against `github/gh-aw` (cloned locally at `.local/gh-aw`,
CLI v0.68.x, technical-preview-since-2026-02-13) to assess opposition,
market fit, and whether gh-aw could be adopted for brr's stated use case
("remotely controlled repo-first agentic CLI runner"). Read gh-aw's canonical
schema (`.github/aw/github-agentic-workflows.md`, ~2400 lines), the docs
site content (`introduction/*`, `patterns/chat-ops`, `patterns/multi-repo-ops`),
`create.md`/`install.md`, and web-researched reception (GitHub Changelog,
gh-aw internal audit discussions, adjacent-project landscape).

Conclusion: **not a substitute, not a direct competitor, plausible complement
on GitHub-hosted repos**. gh-aw is GitHub-native — substrate is the GHA runner,
transport is GitHub events, security posture is defense-in-depth for
multi-principal untrusted input. brr is self-hosted, gate-pluggable
(Telegram/Slack/git/anything-writing-a-file), on-box execution, cross-SCM,
single-principal. They share the "markdown playbook → coding agent → commit+push"
spine but disagree on every structural axis. Wrote full write-up to
`kb/research-brr-vs-gh-aw.md` with side-by-side architecture map, eight axis-
by-axis comparisons, market-fit segmentation, use-case winner table, list
of ideas brr should steal (safe-outputs pattern, rate-limit/stop-after,
XPIA nudge) vs. not (compile step, large frontmatter DSL, GitHub-shaped
worldview), and a concrete recommendation. No code changes; research only.

## [2026-04-24] plan | Env design refinements; extract overlays plan

Markdown-only pass following a review of `kb/design-env-interface.md`.

Refined the env design: split `RunContext.response_path` into
`response_path_env` (runner-visible) vs `response_path_host` (daemon-
checked), with a per-env equality table; added `devcontainer` as the
fifth built-in (validate → up → exec → down mirroring docker);
rewrote the registry section to cover both dispatch modes — Python
entry points and drop-in script envs under `.brr/envs/<name>/` or
`~/.config/brr/envs/<name>/`, sharing a JSON-on-stdio protocol; added
a "Why worktree stays a flat env in v1" subsection arguing against a
working-copy × isolation taxonomy for now; upgraded the salvage rule so
worktrees, docker containers, ssh scratch dirs, and devcontainers are
preserved on `status ∈ {error, conflict}` or `debug=True` (matching the
existing `conflict` case); replaced the "Custom Env packaging tooling"
one-liner with a forward-looking `brr env init` sketch (`--kind=script`
seeds four executables + README; `--kind=python` seeds a minimal
pyproject with an entry point); expanded the tests section with
worktree-salvage, devcontainer-stub, script-env dispatch, and registry
precedence cases. Done-definition and docs list updated to reflect five
built-ins and the dual plugin model.

Extracted the overlays roadmap into a new `kb/plan-overlays.md`. Plan
is blocked behind the env PR and a research gate: before implementation
starts, we must pick between a single-file overlay (`~/.config/brr/
overlay.md` appended to every prompt) and the multi-file lookup chain
from the deck, with the decision landing as
`kb/research-overlay-shape.md`. Plan covers XDG paths +
`BRR_CONFIG_HOME`, `brr overlay init|sync|show`, `overlay_sync=auto|
always|never`, a staged `brr eject` retirement (ship overlays →
deprecation notice → remove one release later → replace with
`src/brr/docs/customising.md`), tests, and explicit non-goals
(`brnrd`, multi-profile composition, auto-migration).

Updated `kb/notes-pondering-fleet.md`: marked §1 (`brr eject`) and §2
(single-file overlay) as promoted — both now live in
`kb/plan-overlays.md` — with provenance preserved; refreshed §9
re-promotion guide; added §10 "Plugin candidates for `brr.envs`"
naming Daytona as the first dogfood plugin (rationale: validates the
plugin mechanism end-to-end, keeps core zero-dep and self-hosted, ships
as `brr-env-daytona` outside the main repo; sketches the env shape
reusing the `ssh` pattern), plus one-liners for E2B, Modal, Gitpod,
Codespaces, Fly Machines, Runpod, each testing a slightly different
plugin-surface slice. Added the same Daytona pointer in the design
doc's registry section. Updated `kb/index.md` under Active design with
`plan-overlays.md` (blocked / research gate noted). No code changes.

## [2026-04-25] fix | Salvage agent ergonomics worktree changes

Reviewed the retained worktree for `task-1777067182-xej1`. The task branch
`brr/task-1777067182-xej1` existed, but it pointed at the same commit as the
base branch and all useful work was still uncommitted inside the retained
worktree, which explains why normal finalization had nothing to merge.

Salvaged the useful changes into the main checkout: shared runtime state now
resolves through `git rev-parse --git-common-dir` so config, docs overrides,
prompt overrides, status, inspect, and brr-managed worktree paths keep using
the shared `.brr/` even when commands run from a worktree. Added base-branch
metadata to daemon prompts and task inspection so task branches created while
`brr up` runs on a non-main branch are clearly described as branching from the
current checkout branch, not `main`; triage guidance now says the same.

## [2026-04-25] fix | Improve active-task inspection and orientation

Analyzed the debug run for `task-1777109858-zopo`. The run produced three
runner invocations: triage, daemon-run, and KB maintenance. Only the daemon-run
was asked to create the ergonomics review artifact; triage emitted the task
spec and KB maintenance only reconciled `kb/index.md` / `kb/log.md`. The real
discoverability gap was that task metadata linked the triage and daemon traces
but not the KB-maintenance trace, and `brr inspect` did not expose the event
file or latest prompt path.

Implemented the clear ergonomic fixes: `brr inspect` now shows the event file
and latest linked prompt path and supports `--event-body` / `--prompt` for
inline inspection; KB-maintenance trace dirs are appended to task metadata for
future runs; `run.md` now points daemon tasks at `brr status`, `brr inspect`,
and `brr docs active-task`; added the new bundled `active-task` doc as a short
task-orientation guide. Larger decisions remain open around a structured run
manifest and making Telegram feel more like a straight conversation.

## [2026-04-27] implement | Workstream ergonomics — first slice

Implemented the workstream-ergonomics plan end-to-end. The runtime now
resolves every incoming event to a stream before triage (explicit
`stream_id` → related task → gate-thread fingerprint → fallback) and
maintains `.brr/streams/<stream-id>/` with a `stream.md` manifest plus
append-only `events.ndjson` / `tasks.ndjson` / `artifacts.ndjson` logs.

Daemon prompts now ship a structured **Task Context Bundle** (workstream,
task metadata, delivery contract, original event body when small) so
agents can orient without needing extra CLI calls. Triage prompts gain
the same workstream block plus an opt-in stage-feedback note when the
event explicitly requests per-stage artifacts.

Added a gate-agnostic update packet model in `src/brr/updates.py` —
`stream_created`, `event_received`, `task_created`, `triage_done`,
`run_started`, `artifact_created`, `needs_context`, `done`, `failed`,
`conflict` — appended to each stream's event log, printed to the
daemon console, and dispatched to any gate that exposes a
`render_update(brr_dir, packet)` hook. Agents can now suggest a
`reply_route` in the response frontmatter; the daemon enforces the
stream's allowed list with `input_gate` as the default and tiebreaker.

CLI surface: added `brr streams` and `brr stream show <id>`,
extended `brr status` with an active-streams summary, and enriched
`brr inspect` with stream/title/intent/per-task artifact links.

Tests added: stream resolution, append-only records, manifest
roundtrip, prompt enrichment (with and without stream), reply-route
acceptance/rejection, daemon stream wiring (records, prompt threading,
follow-up reuse), and CLI/status output. Documented the model in
`src/brr/docs/streams.md` and the bundled `active-task.md` guide;
updated `brr-internals.md` and `prompts/run.md` to surface the new
commands and Task Context Bundle expectations.

## [2026-05-01] fix | Remove runner auto-approve toggle

Removed the `auto_approve` config path because brr's daemon runner needs
non-interactive, approval-bypassing CLIs by design; sandboxing belongs to the
execution environment rather than a per-runner prompt gate. Built-in Claude and
Codex profiles now carry the required bypass flags directly, setup no longer
asks about or writes `auto_approve`, and command-building tests cover the
always-on behavior. While verifying the change, tightened adopt tests to mock
the actual runner detection path so the suite cannot escape to a real local
runner.

## [2026-05-01] implement | Add first Docker env slice

Implemented the built-in `docker` execution environment behind the existing
`EnvBackend` protocol. Docker now requires the CLI on PATH plus a configured
`docker.image`, wraps the normal runner command in `docker run`, bind-mounts the
repo at the same absolute path, tracks per-attempt container names for retries,
and removes containers only after clean non-debug runs.

For branch work, Docker now reuses the worktree setup/finalize path before
entering the container so it does not switch or dirty the main checkout. Updated
the triage prompt, bundled docs, active env design note, and focused tests to
document and verify the required image config, worktree-backed branch behavior,
container cleanup, and unconfigured-daemon rejection path. While running the
full suite, fixed a pre-existing gate-ordering flake by making event listing
honor the documented oldest-first behavior via file modification time instead
of random event IDs, and switched newly generated event IDs to a nanosecond
timestamp prefix so same-second events still sort by creation order.

## [2026-05-01] plan | Clean-slate environment testing playbook

Created `kb/agent-ergonomics-evaluation/clean-slate-environment-testing-playbook.md`
as a lightweight manual testing guide for brr environment ergonomics. The
playbook keeps the scope to fresh fixture repos, the currently implemented
`local` and `worktree` backends, unsupported-env failure probes for `docker`,
`devcontainer`, and `ssh`, repeatable prompts, an observation checklist, a
1-5 scoring rubric, and copy-paste run/finding templates. Updated `kb/index.md`
so the playbook is discoverable with the other agent ergonomics materials.

## [2026-05-02] research | Repo dive-in map

Added `kb/repo-dive-in-map.md` as a durable bottom-up guide for reading the
repository file by file. The page uses branch-neutral relative links so it reads
well in GitHub and GitHub mobile without pinning source references to `main`,
and it captures the core runtime path, spiral reading order, main entities,
module cross-references, runtime invariants, test-first reading path, and
maintenance triggers for future updates. Updated `kb/index.md` so the guide is
discoverable under Architecture.

## [2026-05-03] implement | Strengthen agent stewardship guidance

Updated `AGENTS.md` with an explicit stewardship section that asks future agents
to act like passionate maintainers of brr's viability and prosperity. The new
guidance requires deeper reflection before each behavior or design change:
understand what the existing system is trying to achieve, decide whether that
goal remains necessary, question accidental complexity, and prefer the smallest
change that leaves the project healthier.

## [2026-05-03] research | Refresh repo dive-in map

Updated `kb/repo-dive-in-map.md` after validating it against the commits that
landed since the guide was introduced. The refresh captures the `local` →
`host` backend rename, `environment` as the user-facing policy key, legacy
`env`/`default_env` compatibility, Docker-preferred auto resolution when
`docker.image` is configured, and the newer framing that branch strategy is
task-internal staging/delivery state rather than the user's isolation control.

## [2026-05-03] implement | Add one-step gate setup

Added `brr setup <gate>` as the normal gate configuration command while keeping
`auth` and `bind` available for split setup. Each built-in gate now exposes a
`setup(brr_dir)` flow: Telegram saves a token and optionally restricts chat/topic,
Slack saves a token and channel, and Git configures its watch source. Updated the
README, gate protocol docs, repo map, and current-state deck so the public
surface leads with setup rather than forcing every gate into the same auth/bind
lifecycle.

## [2026-05-05] refactor | Generalise runner response capture via stdout

Replaced per-runner specials in `_build_cmd` with a uniform "stdout is the
response" contract. All three supported CLIs (claude, codex, gemini) print the
final agent message to stdout in headless mode, so brr now captures stdout and
writes it to the task response file itself instead of asking Codex for
`--output-last-message` and asking the agent to also write the file as a
backup.

Side-fixes folded in:

- Bundled Gemini profile updated from bare `gemini` to `gemini -p --yolo`. The
  `-p` flag is required for non-interactive mode (Gemini CLI Jan 2026), and
  `--yolo` is the equivalent of Claude's `--dangerously-skip-permissions` and
  Codex's `--dangerously-bypass-approvals-and-sandbox`. brr's daemon cannot
  function without auto-approval since runners would otherwise hang on tool
  confirmation prompts.
- Removed the dead `auto_approve` config path (deleted on 2026-05-01 but the
  Codex-only branch was still appending `--dangerously-bypass-approvals-and-sandbox`
  twice when set, which obsoletes the earlier same-day `Avoid duplicate Codex
  approval bypass flag` fix).
- Removed the `{response_path}` placeholder in `runner_cmd`. Custom runners
  print to stdout like the built-ins; brr writes the file.
- Daemon no longer registers the response file as a `required_artifacts` entry
  on every run. `RunnerResult.validation_ok` now combines exit code, missing
  artifacts (still used by adopt for AGENTS.md / kb files), and a new
  `has_response` check that is only meaningful when the invocation supplies a
  `response_path`. Retry trigger flips from "response file missing" to "stdout
  was empty".
- Daemon prompt "Delivery contract" simplified to a single contract sentence:
  the final reply is the response, brr captures stdout and stores it.
- Bundled `execution-map.md` updated to describe the stdout-capture flow.

Refactor removes ~80 lines of conditional logic across `runner.py`,
`envs/__init__.py`, and `daemon.py`. All 198 tests pass.

## [2026-05-05] refactor | Drop streams; conversations are routing+history

Removed the `.brr/streams/` runtime layer, the workstream manifest with
its frozen `title`/`intent`/`status`/`gate_context`/`reply_route` fields,
and every CLI/library surface that exposed them. Replaced with a thin
per-gate-thread append-only conversation log under
`.brr/conversations/<safe-key>.ndjson` carrying `kind=event|task|artifact|update`
records. Trigger was the 2026-05-05 incident where a 10-day-old prompt-
injection demo from Telegram was still being injected into triage prompts
and progress cards as the "frozen intent" of that chat's stream — proof
that auto-derived stream identity was a context-poisoning leak, not a
useful abstraction.

Code-level changes:

- New `src/brr/conversations.py`; deleted `src/brr/stream.py`.
- `Task.stream_id` → `Task.conversation_key`; the key is the gate-thread
  fingerprint (`telegram:<chat>:<topic>`, `slack:<channel>:<thread_ts>`,
  `git:<file>`).
- `UpdatePacket.stream_id` → `UpdatePacket.conversation_key`. Dropped the
  `stream_created` packet type. Persistence is one ndjson per conversation
  with `kind=update` rows.
- `run_progress.project_task` reads from a conversation log filtered by
  `task_id`. Added `project_conversation_latest`. Dropped title/intent
  from the view.
- Daemon (`_run_worker`, `_push_if_needed`) routes everything via the
  conversation key. Triage and daemon prompts now carry a
  `Recent in this conversation` block fed by `read_recent` instead of a
  frozen `## Workstream` block. Same for the per-task context file.
- Telegram and Slack `render_update` now look up delivery info from the
  task's `meta` (which already carries `telegram_chat_id`,
  `slack_channel`, etc.), not from a stream manifest. Progress card
  state is keyed by `task_id` alone.
- `status.py` shrunk: dropped `list_streams`/`show_stream`. `get_status`
  finds the active task by walking conversation keys directly.
- Removed `src/brr/docs/streams.md`; added `src/brr/docs/conversations.md`.
  Updated `src/brr/docs/active-task.md` and `src/brr/docs/brr-internals.md`
  for the new `.brr/conversations/` directory.
- Tests: deleted `test_stream.py`, `test_status_streams.py`,
  `test_daemon_streams.py`. Added `test_conversations.py` and
  `test_daemon_conversations.py`. Rewrote `test_run_progress.py`,
  `test_telegram_render_update.py`, `test_slack_render_update.py`,
  `test_status_troubleshooting.py`, `test_runner.py`, and
  `test_daemon_progress_packets.py` to read/write the conversation log.

The CLI didn't actually expose `brr streams` / `brr stream show`
publicly — those commands existed only as library functions referenced
from earlier kb pages. `brr status` and `brr inspect <task-id>` remain
as dev-phase troubleshooting helpers; the primary user surface is the
gate (Telegram), where the chat itself is the conversation history.

Captured the reasoning, the lineage from the 2026-04-27 implementation
entry and the 2026-04-28 follow-up reviews, and the deferred work
(deliberate "lines of work" → `kb/` pages, no migration code, per-task
log lifecycle still tracked separately) in
[`kb/decision-drop-streams.md`](decision-drop-streams.md). Updated
`kb/index.md` and `kb/repo-dive-in-map.md` to match. All 196 tests pass.

## [2026-05-05] review | Check Docker execution environment

Verified the live daemon task running with `Environment: docker`. The
orchestration path now reaches the container because `.brr/config` contains
`default_env=docker` and `docker.image=brr-runner:dev`, and the task context
shows `env_prepared` for the Docker run. The container itself is not yet
operational for normal brr project work: it lacks `python`, `python3`, `brr`,
`pip`, `pytest`, `uv`, `docker`, and `rg`; Git is installed but refuses the
mounted checkout as unsafe because the container runs as root while the repo is
owned by uid/gid 1000. Reported that Docker launch is working, but the runner
image needs Python/project tooling and a Git safe-directory/user fix before it
can reliably execute brr daemon tasks.

## [2026-05-06] implement | Make docker env beginner-friendly (slices 1–2)

Sliced "make docker actually usable for new users" into three PRs and shipped
the first two. Strategy and slicing rationale:

- **Slice 1 — credential wiring** (`6df83c3` "fix docker", plus a follow-up
  patch for the safe.directory issue surfaced by the verification task above).
  `DockerEnv.invoke` now auto-forwards `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `GEMINI_API_KEY`, `GOOGLE_API_KEY` via `-e`; auto-mounts `~/.claude/`,
  `~/.claude.json`, `~/.codex/`, `~/.gemini/` into `/root/<basename>` when
  present; and unconditionally injects `safe.directory='*'` via git's
  `GIT_CONFIG_*` env vars so git works against the bind-mounted repo
  regardless of host vs container UID. New config keys: `docker.env=` to
  extend the passthrough allowlist, `docker.mount_credentials=` (default
  `true`) to opt out of cred mounts. Tests in `tests/test_envs.py` cover all
  paths including opt-out, missing host dirs, dedupe with extra keys, and the
  safe.directory wiring. End-to-end smoke-tested by reproducing the dubious-
  ownership error without the env vars and confirming `git status` works
  with them.

- **Slice 2 — docs.** New `src/brr/docs/envs.md` is the canonical bundled
  doc covering host/worktree/docker, the durability contract, the docker
  credential story, image expectations, the minimum-viable Dockerfile, the
  layering pattern for project tooling, and a troubleshooting block.
  `execution-map.md` and `brr-internals.md` retired their inlined docker
  bits and now cross-link `envs.md`. README's "Environments" paragraph got
  a short docker-credential blurb pointing at the bundled doc. The design
  doc (`kb/design-env-interface.md`) gained an "Implementation status"
  callout in the docker section so readers know what shipped vs what's
  still designed.

- **Slice 3 — wildcard image + auto-resolve** (deferred). Drafted
  `docker/Dockerfile` (untracked) bundling claude/codex/gemini on
  `node:22-slim` with git, ripgrep, ca-certificates, and a baked-in
  `safe.directory='*'` as defense in depth. Local build tagged
  `brr-runner:dev` (~919MB) and used for the dogfooding above. Publishing
  to GHCR + an auto-resolve of `docker.image=` waits on the `hugimuni`
  org existing so we don't burn a name we'll have to migrate. The
  Dockerfile-as-modified is ready to commit alongside the GHA workflow
  whenever that lands.

Decisions worth remembering:

- Single wildcard image (all three runners) over per-runner images. Smaller
  matrix to maintain, simpler `docker.image=` default once it's published,
  marginal size cost.
- Container runs as **root**; `--user` remap is a v2 problem. The cred-
  mount target (`/root/<basename>`) is coherent with this. Trade-off is
  files written inside the container land root-owned on the host if the
  CLI does delete-and-recreate (rare in practice; flagged in the
  troubleshooting block of `envs.md`).
- `safe.directory='*'` via `GIT_CONFIG_*` env vars rather than baked into
  the image. Means user-rolled images don't need to remember the line —
  brr does the right thing automatically. Defense-in-depth: the slice 3
  Dockerfile bakes it in too.

Flagged but not addressed: the gate-side progress packets are repetitive
("running" appears 3x for one task in the live test). Separate concern,
lives in `src/brr/run_progress.py` and the gate `render_update` paths.

## [2026-05-06] plan | Reconsider frontmatter protocol shape

Reviewed a live Telegram triage failure where the runner succeeded but its
stdout did not begin with the required triage frontmatter, causing the daemon
to reject the task before execution. The core finding: frontmatter is still a
reasonable human-readable envelope for durable `.brr/` markdown artifacts, but
it is too brittle as an AI output contract and the custom YAML-like parser is
now carrying more operational risk than value. Preferred direction is to stop
requiring parseable frontmatter from runner stdout: make triage deterministic
or parse only a tiny structured side-channel, and keep final responses as plain
captured text unless explicit lifecycle metadata is needed.

## [2026-05-06] implement | Remove the triage stage

Followed up on the frontmatter plan by removing the LLM-driven triage stage
entirely. Tasks are now built mechanically from events with `Task.from_event`;
the env policy resolves deterministically from `.brr/config` (`auto` →
docker if configured, else worktree). `prompts/triage.md`, `_triage_task`,
`Task.from_triage_output`, the `branch` field on `Task`, the `needs_context`
lifecycle hook, and the `triage_done` / `needs_context` packets are gone.

Worktree creation now always sprouts a fresh `brr/<task-id>` branch from
HEAD; the agent decides at runtime whether to commit there (brr does an
`ff_only` merge back) or `git switch -c <name>` to preserve a separate
branch. `WorktreeEnv.finalize` reads the worktree's git state to make that
choice — no more frozen `task.branch` driving the merge. Responses are now
plain text; if the agent can't complete the task, it explains why and the
operator follows up in-thread.

Updated bundled docs (`brr-internals.md`, `execution-map.md`, `envs.md`,
`active-task.md`, `conversations.md`), gate progress packet whitelists
(Telegram, Slack), and reworked the daemon/env tests. Decision recorded in
`kb/decision-remove-triage.md`. Full pytest run is green (176 passing).

## [2026-05-08] plan | kb shape — graph topology, semantic memory, cross-tool maintenance

Recorded the framework decision in `kb/decision-kb-shape.md` after a live
Telegram test on 2026-05-07 surfaced the chore-conflict symptom (the agent
filed a substantive review at `kb/log-task-1778167445-bz4e.md` while
returning a vacuous one-line acknowledgment as the chat reply, with nothing
committed and nothing pushed). The decision aligns brr's kb with the
[`llm-wiki.md`](llm-wiki.md) pattern by making the four memory layers
(raw / episodic-thin / semantic+decisional / schema) explicit, framing the
kb as a graph (entry point, edges, supersedence over deletion, splits and
merges as normal operations), naming subject pages as the missing semantic
layer (accreting naturally, not pre-seeded), and crucially moving the
maintenance contract out of brr-specific code: AGENTS.md becomes the schema
that all agents read (cursor sessions, claude code / codex CLI direct, brr
remote runs all share it), and brr's `_maybe_kb_maintenance` is reframed as
a redundancy pass on top of agent-driven maintenance.

The page lays out a five-phase execution plan: (1) anchor, this decision;
(2) chore removal + bot UX fixes (drop mandatory log entries and per-task
log files, fix Telegram message duplication, terser progress card, Docker
question in `brr init -i`); (3) one-time kb cleanup (reorganise index by
subject, lifecycle markers on shipped plans, fold per-task log files into
log.md); (4) daemon kb-maintenance phase becomes a thin redundancy lint
with a deterministic preflight; (5) subject pages accrete from real work
rather than top-down seeding.

Explicitly defers vector/graph semantic indexing (future project), tool-
specific hooks for cursor/claude code/codex (deferred until the AGENTS.md
schema is stable), a `brr kb` CLI subnamespace (rejected — keep user-facing
surface minimal, agent-facing info via prompt injection), and adopters'
kb-seed conventions (revisit after brr's own kb evolves under the new
framework for a few real tasks). No code changes; framework anchoring only.

## [2026-05-09] implement | Phase 2: AGENTS.md restructure, delivery contract, bot UX, Docker init

Landed Phase 2 of the kb-shape decision end-to-end.

**AGENTS.md is now a single source.** Moved repo-root `AGENTS.md` to
`src/brr/AGENTS.md`, symlinked at the root. Bundled in package_data so
adopters' `brr init` reads brr's own playbook directly as the model
(rewrote `setup.md` and `runner.build_init_prompt` to load it). Deleted
the stale `src/brr/prompts/agents-template.md`. Rewrote AGENTS.md's
universal sections to install the four-layer memory model, graph
topology, lifecycle markers, link discipline, and subject genesis rule.
Reworded the Stewardship section to be project-agnostic and functional —
"surface contradictions and trade-offs before proceeding" replaces the
older aspirational framing.

**Delivery contract sharpened.** Removed the per-task `log_file` plumbing
end-to-end: dropped `RunContext.log_file`, `WorktreeEnv.prepare`'s
log-file assignment, `runner.build_daemon_prompt`'s `log_file` parameter,
and `daemon.py`'s plumbing. Rewrote `prompts/run.md` and the Task
Context Bundle's Delivery contract to make stdout the unambiguous chat
reply, kb writes optional (only when material), and "if you wrote
files, commit them" the universal rule (replacing the old "review = no
commit" carve-out that left work unpushable).

**Telegram message duplication fix.** Cached the last-rendered card text
in `gates/telegram.py:render_update` and short-circuit on unchanged
text. Added a typed `_TelegramNotModified` exception so `_api_call` can
treat Telegram's 400 "message is not modified" as a success no-op
instead of letting it fall through to `sendMessage` (the duplication
bug). Same dedupe pattern in `gates/slack.py` for parity. Made
`run_progress.render_text` compact mode terser — header + phase only,
with attempt/error surfacing only when actionable; verbose mode keeps
the full operator-facing detail.

**Docker question in `brr init -i`.** Moved `docker/Dockerfile` to
`src/brr/Dockerfile`, bundled in package_data. Added
`adopt._configure_environment` that detects docker on PATH, asks
yes/no, prompts for image (default `brr-runner:local`), and offers to
auto-build from the bundled Dockerfile in a temp context. When user
declines or docker is missing, writes `environment=worktree`
explicitly so the choice is recorded.

Tests: 176 → 188. Updated all stub envs to drop `log_file=...`, the
runner-bundle test to assert no `kb/log-` mention, the compact
progress test to assert the dropped fields are gone, plus new tests
for dedupe, "not modified" handling, and the Docker question paths.

Outstanding: Phase 3 (kb cleanup — reorganise index by subject hubs,
add lifecycle markers, fold `kb/log-task-*.md` into `kb/log.md`,
delete pages with no future value), Phase 4 (daemon maintenance
becomes deterministic preflight + thin LLM redundancy pass), Phase 5
(subjects accrete from real work). All anchored in `decision-kb-shape.md`.

## [2026-05-09] refactor | Phase 3a: split prompt assembly out of runner.py

`runner.py` had quietly grown into the agent-facing surface — kb/log.md
context injection, AGENTS.md bundling, the Task Context Bundle, the
delivery-contract paragraphs — even though its docstring still claimed
"this module is plumbing." A small wording change to the contract and a
one-line subprocess fix were sharing the same module attention.

Moved all prompt assembly into a new `src/brr/prompts.py` (307 lines):
`read_prompt` (was `runner._read_prompt`), `_read_recent_log`,
`_build_context_block`, `_join_prompt_parts`,
`build_init_prompt` / `build_run_prompt` / `build_daemon_prompt` /
`build_kb_maintenance_prompt`, `_build_task_context_bundle`,
`_format_recent_conversation`, plus the `_PROMPTS_DIR`, `_AGENTS_PATH`,
log-entry constants. `runner.py` is back to ~490 lines of subprocess
plumbing. `_load_profiles` now calls `prompts.read_prompt` for
`runners.md`, the only remaining file-IO crossover. `daemon.py` and
`adopt.py` import `prompts` directly; tests follow
(`monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", …)`), and
the prompt-assembly tests live in a new `tests/test_prompts.py`.
Pure refactor — public behaviour and prompt text are byte-identical;
all 188 tests still green.

## [2026-05-09] refactor | Phase 3b: kb cleanup pass (slash + lifecycle + index reshape)

One-time hand work prescribed by `decision-kb-shape.md` § Phase 3.

**Slashed nine pages with no future value.** The pages explicitly named
in the kb-shape decision plus their orbit:

- `idea-personal-workflow-variants.md` — already self-marked
  "absorbed" into `deck-brr-fleet-steering.md` Axis 1; provenance is
  git history, not a redirect page in `kb/`.
- `review-pr-1.md` and `review-concurrency-followup-2026-04-14.md` —
  point-in-time PR reviews whose findings have either landed in the
  codebase or been reversed by `decision-remove-triage.md`. No
  surviving recommendations.
- `deck-brr-current.md` — bird's-eye of brr-from-a-few-months-ago,
  built around triage and `brr eject` as the override flow. Both
  removed/reshaped since. A future "brr today" deck can be re-derived
  from current state when there's a reason to give one.
- `agent-ergonomics-evaluation/task-context-bundle-runner-review-2026-04-28.md`
  and its `v2-followup` — the two reviews that triggered the streams
  removal and the kb-shape decision. Their synthesis is captured in
  `decision-drop-streams.md` and `decision-kb-shape.md`; the original
  reviews referenced workstreams, the per-task log override, and
  stream/task duplication concerns that no longer exist.
- `agent-ergonomics-evaluation/clean-slate-environment-testing-playbook.md` —
  a 2026-05-01 manual procedure that referenced `local`/`worktree`/
  `docker` policy concepts and stream/active-task surfaces that have
  since been rationalised. The procedural shape can be re-derived
  cheaply when the next ergonomics pass is needed.
- `kb/log-task-1777333195-8ed7.md` and `log-task-1777378942-vr1a.md` —
  the per-task companions of the two reviews. Three lines of summary
  each, fully redundant with the now-canonical synthesis in the
  decision pages.

The `agent-ergonomics-evaluation/` directory is now empty and removed.

**Lifecycle markers on what survives.**

- `plan-concurrent-worktrees.md` → *shipped* (one-task-per-worktree
  slice; the merge-coordinator path was abandoned in favour of
  decentralised `git merge --ff-only` from the agent's branch).
- `plan-branch-modes.md` → *shipped, with revisions* (triage and
  `needs_context` reversed by `decision-remove-triage.md`).
- `plan-overlays.md` → *blocked* (env work + a research gate; the
  page already said so, this just makes the marker top-of-page).
- `design-env-interface.md` → *in flight (3/5 envs shipped,
  durability contract partial)*. Names what's outstanding (`ssh`,
  `devcontainer`, plugin point, full durability enforcement).
- `deck-brr-fleet-steering.md` → *roadmap* (env axis active,
  overlays/brnrd paused). Added a header comment listing the
  decisions that have overtaken specifics — triage removal,
  workstream removal, per-task log removal — so a reader doesn't
  treat it as a current spec.
- `notes-pondering-fleet.md` → *paused*. Several items already
  promoted to `plan-overlays.md`; the rest stays as capture-only.
- Four decision pages (`decision-bundled-docs`, `decision-drop-streams`,
  `decision-kb-shape`, `decision-remove-triage`) keep their existing
  `Status: accepted` headers — those *are* lifecycle markers in the
  decision-page format.

**Reciprocal links between connected pages.** The three "drop the
noisy abstraction" decisions (triage → streams → kb log files) now
each cite the other two as siblings. `repo-dive-in-map.md`'s related-
reading list and `research-brr-vs-gh-aw.md`'s sources lost their
references to `deck-brr-current.md`.

**`kb/index.md` reorganised by subject area** (Architecture &
orientation, Environments, Tasks & branching, Conversations &
responses, Documentation strategy, Fleet & overlays, Knowledge base
itself, Research) instead of by artifact type (Decisions, Decks,
Plans, Ideas). Every link gets a one-line "what it is" summary; pages
with a meaningful lifecycle (in-flight, shipped, blocked, paused,
roadmap) carry that marker inline. The index header explains the
graph topology and the lifecycle-marker convention so a cold reader
sees the rules of the road in one screen.

**`repo-dive-in-map.md`** got a refreshed "Last validated against …"
header pointing at this kb-shape arc, plus a reading-route entry that
distinguishes prompt assembly (`prompts.py`) from subprocess plumbing
(`runner.py`).

Net: 23 → 13 subject pages plus index + log. No code changes; pure
content/structure work. Index → files cross-reference is exact (every
file present in `kb/` is linked from the index; every link in the
index resolves). 188 tests still green (no source changes that would
affect them).

Outstanding: Phase 4 (daemon maintenance becomes deterministic
preflight + thin LLM redundancy pass), Phase 5 (subjects accrete from
real work).

## [2026-05-09] implement | Phase 4: kb-maintenance becomes preflight + redundancy pass

The brr-only kb-maintenance step used to be the *primary* contract:
"every kb-touched task triggers an LLM pass that re-reads the rules
and fixes drift." The kb-shape decision moved that contract into
AGENTS.md so every tool (Cursor, Codex, Claude Code, brr) shares it.
The brr daemon's hook had to follow — stop pretending to be the
primary, become a safety net.

**`src/brr/kb_preflight.py`** — new deterministic scanner. Every run,
it walks `kb/` and reports structured findings:

- `missing-from-index` — page exists on disk, no link from `kb/index.md`.
- `stale-index-entry` — `kb/index.md` links to a path that doesn't exist.
- `broken-link` — any kb page (other than `log.md`, which is
  append-only narrative) links relatively to a missing path.

`format_findings()` renders the findings as a Markdown block ready
for prompt injection. Lifecycle-marker drift, contradictions with the
log, and other synthesis-heavy checks are deliberately *not* in the
preflight — they're judgement calls the LLM redundancy pass handles.

**`daemon._maybe_kb_maintenance` rewritten.** Preflight always runs
when policy is `auto` or `always`. When the kb is unchanged *and*
the preflight is clean, the LLM pass is skipped — kb maintenance
becomes a true skip-fast safety net rather than a tax on every run.
When findings exist or kb has been touched, the maintenance prompt
is built with the findings injected and the LLM pass runs.

**`prompts/kb-maintenance.md` rewritten** to be a thin redundancy
pass: short preamble, point at AGENTS.md → "Knowledge base shape" for
the rules, address the injected findings or do a brief redundancy
spot-check otherwise. Was 19 lines of duplicated rules; now 19 lines
of pointer + scope + skip-fast contract.

**The preflight earned its keep on first run.** Catching `kb/repo-dive-in-map.md`'s
stale `agents-template.md` link — left over from the Phase 2
template deletion — was the first finding in this commit's preflight.
Fixed inline.

**Docs.** `docs/brr-internals.md` and `docs/execution-map.md`
rewritten for the preflight + redundancy shape. The `auto` /
`always` / `never` config keys keep their meaning; the trigger logic
is now described accurately.

Tests: 188 → 203. New `tests/test_kb_preflight.py` (12 tests cover
empty / consistent / each finding type / format helpers / stable
ordering). Three new daemon tests: preflight findings on unchanged
kb still trigger the pass; preflight clean + kb unchanged still
skips; kb changed + preflight clean runs with the bare prompt.

Outstanding: Phase 5 — when the next substantial work touches Envs /
Gates / Daemon / Conversations / kb-itself, that work earns the
subject hub page. (This commit is the substantial kb-itself work, so
a kb subject hub follows next.)

## [2026-05-09] implement | Phase 5: first subject hub — `subject-kb.md`

The kb-shape arc (phases 1-4) produced enough material on the kb
pattern that "what do we know about the kb?" had no single answer
page. The schema lives in `AGENTS.md`; the *why* lives in
`decision-kb-shape.md`; the framing reference is `llm-wiki.md`; the
deterministic backbone is `kb_preflight.py`; the slashing/lifecycle
norms are scattered across log entries. Synthesising those into one
hub page now satisfies both halves of the subject-genesis rule (real
density + no good place for the synthesis except a new page).

Wrote `kb/subject-kb.md`. Structure: what the kb is for, the four
memory layers as a single table, the graph topology with index
reachability and lifecycle markers, when to create a subject hub
(with this page as the worked example), cross-tool maintenance,
slashing economics, what was rejected (the `brr kb` CLI subnamespace,
auto-generated subject pages, vector indexing, wikilinks, per-task
log files), and a priority-ordered "read these next" pointer list.

Linked from `kb/index.md` under "Knowledge base itself" as the hub
entry; `decision-kb-shape.md` now points at the synthesis hub from
its status line so the decision page (point-in-time *why*) and the
subject hub (evolving *what we know*) sit in their proper roles.

Preflight clean; 203 tests still green (no source changes). The
five-phase kb-shape execution plan is now fully landed.

## [2026-05-09] implement | repo-dive-in-map refresh for the full kb-shape arc

Follow-up. Earlier per-phase commits had patched the dive-in-map only
narrowly (phase 3b touched the validation header and one
reading-route note; phase 4 patched a single broken
`agents-template.md` link the preflight caught). A thorough sweep
was overdue.

What changed in `repo-dive-in-map.md`:

- **Validation header** rewritten as a phase-by-phase summary of the
  whole kb-shape arc (2 → 5).
- **Stewardship paragraphs** rewritten — the description still
  carried the old "improve the underlying design instead of layering
  conditions" wording, while the actual AGENTS.md text is now about
  surfacing contradictions between the request and the codebase.
- **Ring 3 read list** now includes `prompts.py`, `kb_preflight.py`,
  and the `test_prompts.py` / `test_kb_preflight.py` files.
- **Worker pass steps** updated: step 7 names
  `prompts.build_daemon_prompt` and the Task Context Bundle; step 10
  describes the `kb_preflight.scan` + conditional kb-maintenance
  pass instead of the old "optionally run KB maintenance."
- **`RunContext` important fields** lost the `log_file` entry
  (phase 2b had removed the field; the dive-in-map still listed
  it). An explanatory note replaces it so cold readers coming from
  older versions aren't confused.
- **"Runner and prompts" cross-reference section** rewritten to
  split runner / prompts / kb_preflight responsibilities, list each
  module's callers separately, and group the prompt files alongside.
- **Worktree execution bullet** lost the stale "writes per-task log
  instructions through `RunContext.log_file`" line.
- **New runtime invariant**: "KB consistency is preflight +
  redundancy, not a primary gate" — names the contract for adding
  new structural kb invariants (extend `kb_preflight.scan` over
  expanding the LLM prompt; AGENTS.md is the universal schema).
- **Design history** restructured by category: subject hub, decisions
  trio, other decisions, designs/notes, decks, bundled docs. Each
  entry carries its current lifecycle marker so a cold reader knows
  what's stable, in flight, paused, blocked, or shipped.
- **Practical navigator notes** now include "if a file talks about
  kb consistency or orphan pages → `kb_preflight.py` +
  `_maybe_kb_maintenance` in `daemon.py`; the maintenance contract
  itself lives in AGENTS.md."
- **Tests reading path** includes `test_prompts.py` and
  `test_kb_preflight.py` in their dependency-correct positions.
- **Maintenance rule for this guide** lists kb consistency contract,
  module-boundary changes, and subject-hub additions/retirements as
  triggers for refreshing the dive-in-map.

Preflight clean; 203 tests still green (no source changes).

## [2026-05-09] fix | Reduce daemon prompt duplication and record runner ergonomics review

Reviewed a live daemon-launched Codex task to measure how much context
the agent had to recover before doing useful work. The kb index, recent
log, run context file, and repo dive-in map were enough to orient without
reading raw `.brr/` runtime logs, but the generated prompt repeated the
current Telegram event as recent conversation, original event body, and a
trailing `Task:` block.

Fixed the prompt shape: daemon prompts now filter records for the
in-flight event/task out of `Recent in this conversation`, and
`build_daemon_prompt` suppresses a duplicate `Task:` block when it is
identical to the original event body. Updated tests for the cleaner
contract.

Also fixed stale bundled docs that still described removed per-task kb
log files and direct response-file writes (`active-task.md`,
`execution-map.md`, `brr-internals.md`). Recorded the broader review in
[`research-runner-context-ergonomics-2026-05-09.md`](research-runner-context-ergonomics-2026-05-09.md):
context recovery mostly works, extra MCP was not needed, but the live
Docker environment started without `rg`, Python, and pytest, so brr
self-development needs either worktree/host execution or a
project-layered Docker image before tests are ergonomic.

While verifying the new kb page, a manual `kb_preflight.scan(Path("."))`
call exposed a false-positive path bug: relative repo roots made every
indexed page look missing because link targets were resolved absolute
but `kb_dir` stayed relative. `scan` now resolves `repo_root` up front
and has a regression test for relative roots.

## [2026-05-09] design | Developer reload for the daemon

Designed a brr self-development reload path without adding a broad
user-facing restart feature. The recommended shape is an editable
install (`pip install -e ".[dev]"`) plus an opt-in `brr up --dev-reload`
mode that watches brr's package files and re-execs the foreground daemon
only at safe boundaries: idle scans or after a task has delivered its
response, finalized, run kb maintenance, and pushed. Rejected a public
`brr restart`, chat-triggered restart, `.brr/` control marker, in-process
`importlib.reload()`, and immediate supervisor work as the wrong layer
for this development-only pain.

The work also earned the daemon subject hub,
[`subject-daemon.md`](subject-daemon.md), which synthesises the current
foreground process model, gate/file-protocol boundary, serial worker
lifecycle, local process-control contract, and deferred supervisor /
cancellation / concurrency directions. Detailed reload design lives in
[`design-daemon-dev-reload.md`](design-daemon-dev-reload.md).

## [2026-05-09] design | Explicit landing branch for daemon-produced commits

Recorded the branch-policy issue exposed by kb-writing design tasks:
the current daemon uses the host checkout's current `HEAD` as both the
worktree seed and the auto-land target, so legitimate remote kb commits
can land on whatever branch the operator happened to have checked out.
The kb commit is not the bug; the ambient landing target is.

Added [`subject-tasks-branching.md`](subject-tasks-branching.md) as the
tasks/branching hub and
[`design-daemon-landing-branch.md`](design-daemon-landing-branch.md) as
the active design. The recommended direction is an explicit
`landing_branch=` policy: task branches sprout from that ref,
finalization fast-forwards that target rather than blindly merging into
the current checkout, and push logic follows the landing branch's
upstream. This keeps agent-owned runtime branching and durable kb
commits while removing accidental dependence on host checkout state.

## [2026-05-10] design | Branch intent resolver replaces fixed landing-branch config

Revised [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
after operator feedback that a fixed `landing_branch=` config merely
moves the wrong-branch risk from the host checkout into hidden config.
The corrected design separates `seed_ref`, optional
`auto_land_branch`, and the agent-observed `final_branch`. A
deterministic resolver chooses the pre-run branch plan from structured
event metadata, existing thread branch context, issue/PR/task metadata,
host current branch as context, and fallback policy; it does not add a
pre-run LLM selector or parse free-text branch instructions in the
daemon. If no safe landing authority exists, brr should preserve the
task branch rather than silently routing durable work into a stale
feature branch. Updated the tasks/branching and daemon hubs plus the
index to point at this corrected shape.

## [2026-05-10] implement | Developer reload for editable brr daemon runs

Implemented the daemon reload design as explicit developer behaviour,
not an unconditional `brr up` default. `brr up --dev-reload` and
`dev_reload=true` now enable a package-file watcher over brr's installed
package tree (`.py`, bundled markdown, `Dockerfile`, and visible
source-layout `pyproject.toml`). When the watcher sees a change at a
quiescent boundary, the daemon re-execs the same Python command with
`BRR_REEXEC=1`; the PID-file guard permits only that same-PID re-entry.

The option/default decision was recorded in
[`design-daemon-dev-reload.md`](design-daemon-dev-reload.md): reload is
process lifecycle policy, so normal packaged installs and externally
supervised daemons keep the small `brr up` / `brr down` model. The
explicit mode gives editable brr development the intended workflow
without making local packaging shape a hidden restart policy.

## [2026-05-11] fix | Codex stdin hang and silent runner timeouts

Diagnosed a recurring `docker failed (exit 124): Reading additional
input from stdin...` failure: codex prints an stdin banner whenever its
stdin is an open pipe, the brr daemon's `subprocess.Popen` left stdin
open and inherited from the parent, and the runner timeout was hard-
coded to 600s with three retries — so a long codex run was killed
mid-flight, the misleading banner became the visible error, and the
daemon then burned two more equally doomed retries.

Fixed in three layers: (a) `runner.invoke_runner` and `DockerEnv.invoke`
now pass `stdin=subprocess.DEVNULL` (and the docker container is
launched with `-i` over a closed pipe), so codex sees real EOF and
skips the banner; (b) the timeout is now a configurable
`runner.runner_timeout(cfg)` defaulting to 3600s via
`runner.timeout_seconds`; (c) `RunnerResult.retry_reason()` returns
`None` for hard failures (timeouts, non-zero exits) so the daemon stops
retrying anything that isn't a clean "missing response file" exit.
`_run_worker` now collects per-attempt failure detail
(`exit_code`/`error`/`timed_out`) and reorders packet emission so
`finalizing` precedes the terminal `failed` packet — the projection
keeps the real runner error instead of "finalizing (failed)".

## [2026-05-11] implement | Strike-through phase log status card with heartbeat

Rewrote the gate-rendered status card around a vertical phase log:
sticky `runner · env · branch ← base` header, then closed phases as
struck-through `~~preparing · 1s~~` lines with the live phase showing
its rolling elapsed (e.g. `running · 4m 02s`). Multiple retry attempts
each get their own line (`running (attempt 1)` / `running (attempt 2)`).
The terminal entry reports total wall-clock from event arrival
(`delivered · 4m 24s · pushed 2 commits`) and failed/conflict cases
keep the runner's own error message on the line below the struck log.

`render_text` takes a `RenderStyle` so each gate plugs in its own
strike-through markup: Telegram uses HTML `<s>…</s>` (parse_mode=HTML)
with user content HTML-escaped before render, Slack uses mrkdwn `~text~`,
the CLI default is plain text where the log reads positionally.

A new `heartbeat` packet fires every 30s while a runner is alive
(`daemon._invoke_with_heartbeat` runs the env-backend invoke on a
worker thread and ticks on the main thread). The packet itself is a
no-op for the projection — it only re-triggers the gate render so the
elapsed counter visibly bumps during silent runs.

## [2026-05-11] implement | Branch intent resolver and branch-aware push

Shipped the core branch-intent design. The daemon now resolves a
`BranchPlan` before env prep instead of treating the host checkout as
implicit branch authority. The resolver uses structured event branch
fields, unambiguous conversation branch facts, then `branch.fallback`
(`preserve` by default; also `inbox`, `default`, `current`). Worktree
and Docker tasks sprout `brr/<task-id>` from `seed_ref`; with no
auto-land target, committed task branches are preserved and can be
published when a remote is configured instead of silently folding into
whatever branch the daemon host happened to have checked out.

Finalization now fast-forwards named targets through
`gitops.fast_forward_branch`, using `git merge --ff-only` only when the
target is the daemon checkout and `git update-ref` when the target is
not checked out elsewhere. This avoids the worktree checkout collision
that made local inspection awkward. `_push_if_needed` now receives the
actual changed branch and pushes that branch, setting upstream for new
brr-owned preserved branches.

Updated agent-facing prompt/context text, bundled docs, the
tasks/branching hub, and the branch-intent design status. Richer
source-specific PR/issue metadata remains a future expansion point; the
free-text task body still belongs to the worker agent, not a daemon
regex parser.

## [2026-05-11] implement | Baseline dev tools in the bundled Docker image

The bundled `src/brr/Dockerfile` now treats Docker as a practical
runner image for common daemon-launched development, not only a tiny
AI-CLI wrapper. It still avoids repo-specific dependencies, but it now
installs the tools agents routinely need to inspect, test, fetch, and
commit work: Python (`python`, `python3`, `pip`, venv support), SSH
client, `git`, `rg`, `curl`/`wget`, `jq`, `rsync`, zip tools, and a
native build toolchain (`build-essential`, `pkg-config`). The image sets
`PIP_BREAK_SYSTEM_PACKAGES=1` because the container is ephemeral and the
playbook's direct `pip install -e ".[dev]"` workflow should work without
forcing every agent through a venv first.

Updated the bundled env docs, README pointer text, and env/design kb
notes to distinguish baseline tools from project-specific dependencies.
Added `tests/test_dockerfile.py` so the packaged Dockerfile cannot
silently regress to an image without Python, SSH, git, or the common
inspection/build tools. Full-suite verification also exposed and fixed
three stale test harness issues around dev-reload reexec sentinels and
Docker timeout monkeypatch ordering.

## [2026-05-12] research | Branch plan simplification review

Reviewed the accepted branch-intent implementation against the operator's
concern that it may still be too stateful for what is mostly a default
branch setup for the runner. The core worktree finalization contract is
still pulling its weight: seed a task branch, optionally fast-forward a
known target, otherwise preserve the branch, and preserve any branch the
agent switches to at runtime.

The simplification target is the pre-run resolver surface. BranchPlan now
mixes mechanical git defaults with inferred conversation branch memory,
and the code still carries old `base_branch` compatibility beside the
new `seed_ref` / `auto_land_branch` vocabulary. Captured the recommended
direction in
[`research-branch-plan-simplification-2026-05-12.md`](research-branch-plan-simplification-2026-05-12.md):
keep structured event/source branch metadata as auto-land authority,
demote inferred conversation branches to prompt context, retain
`preserve` as the default, treat `current` as explicit dev/compat mode,
and delete the legacy `base_branch` API path before adding more policy
surface.

## [2026-05-12] implement | Brutally simplify the branch plan

Acted on the research above, with the operator's added direction that
the daemon should not pre-decode any conversation branch facts at all —
the agent already sees recent records in the prompt and can `git
switch` itself if continuity is meant. Today's "merge conflict on
brr/task-…-rtc8" was the proof of the over-engineering: a single recent
`preserved_branch` row in the Telegram conversation was treated as
unambiguous auto-land authority, so the resolver targeted a sibling
task's preserved branch and the worktree-collision guard in
`gitops.fast_forward_branch` correctly refused to update it.

Cuts shipped in this commit:

- `branching.resolve_branch_plan` no longer reads conversation
  history. Structured event fields (`branch_target`, `target_branch`,
  `base_branch`, legacy `branch`) are the only auto-land authority;
  otherwise `preserve` (default) or `current` (opt-in dev/compat).
  Fallback modes `inbox` and `default` were removed — no shipped
  workflow used them.
- `BranchPlan` trimmed: `display_base`, `notes` removed; `authority`
  renamed to `source` (trace/observability only).
- `base_branch` deleted as a parameter and `RunContext` field
  end-to-end. `EnvBackend.prepare` now takes a required `branch_plan`;
  prompts, run-context renderer, status renderer, conversation rows,
  daemon update packets, gates, and tests follow. `run_progress.View`
  renames its old `base_branch` display field to `display_base` so the
  intent is clear.
- `WorktreeEnv._land_or_preserve` is now outcome-aware. Clean success
  with no uncommitted/untracked files tears the worktree down (the
  branch ref + traces are the durable artefact). A conflict
  (auto-land collision), a detached HEAD, or any
  untracked/unstaged files left in the worktree keeps it alive for
  inspection regardless of `--debug`. `--debug` shrinks to a
  "force-keep even on clean success" override; it still preserves
  trace dirs (versus only artifact records) and Docker containers.
- `_push_if_needed` now publishes any new branch with `git push -u`
  regardless of namespace. There's no good reason for the daemon's
  push to differ from a user's manual push; agent-switched runtime
  branches reach the remote with upstream set the way a human would
  expect.

## [2026-05-12] refactor | docker container runs as host UID

The Docker env left root-owned files in the host's `.git/objects/`
after every run because the bundled image followed the standard
"container runs as root, creds mount to `/root/`" pattern. Concrete
symptom: `git commit` failed for the daemon's host user with
`error: insufficient permission for adding an object to repository
database .git/objects` after a few container-launched tasks.

Fix shipped end-to-end:

- The bundled image now bakes a world-writable `/brr-home` (mode 1777)
  and sets `ENV HOME=/brr-home`. Any UID can use it as HOME, even one
  with no `/etc/passwd` entry.
- The daemon passes `-u "$(id -u):$(id -g)"` and
  `-e HOME=/brr-home` on every `docker run`, so the container process
  is the host user from the kernel's perspective. Bind-mounted writes
  are host-owned; nothing leaks back as root.
- `_docker_credential_mount_args` remaps cred targets from
  `/root/<basename>` to `/brr-home/<basename>`, and now also mounts
  `~/.gitconfig` when present so `git commit` uses the host user's
  real author identity instead of the codex CLI's
  `brr agent <brr-agent@example.invalid>` fallback.
- `tests/test_envs.py` was updated for the new mount and env-arg
  shape; a new `tests/test_dockerfile.py` assertion locks in the
  `/brr-home` + `ENV HOME=/brr-home` contract for the bundled image.
- `src/brr/docs/envs.md` and `kb/repo-dive-in-map.md` rewrote the
  "container runs as root" section to reflect the new contract;
  troubleshooting tips were updated.

Verified by rebuilding the runner image and running a smoke container
as the host UID — codex 0.130 starts, host creds are visible, and
files written into the bind-mounted repo come back owned by the host
user.

## [2026-05-12] refactor | remove --debug, traces always on

`--debug` had three jobs (force-keep worktrees, force-keep containers,
write trace dirs). The earlier outcome-aware cleanup work already
handles forensic salvage on its own — worktrees and containers stay
on `error`, `conflict`, or when files were left uncommitted, and tear
down on a clean success. That leaves `--debug` with only one real
job: writing traces. But traces are tiny, gitignored, and the captured
prompt/stdout/stderr snapshot is the actual forensic value, so
defaulting them off was the wrong call.

What changed:

- The ``--debug`` CLI flag is gone. So is the ``debug=true``
  config key.
- The ``debug`` parameter is gone from ``daemon.start()``,
  ``daemon._run_worker``, and every env backend's
  ``prepare/invoke/finalize``. The runner-level ``trace=`` keyword
  still exists for non-daemon callers (``adopt``, ``run_executor``)
  but the daemon always passes ``trace=True``.
- ``DockerEnv.finalize`` is now outcome-aware in the same shape as
  worktree cleanup: clean ``done`` removes the container, anything
  else preserves it and records the container ID in
  ``task.meta["docker_containers"]``.
- ``envs.md``, ``execution-map.md``, and ``brr-internals.md``
  replaced their "debug mode" sections with the outcome-aware
  contract. ``kb/design-env-interface.md`` and
  ``kb/repo-dive-in-map.md`` updated to match.
- ``tests/test_daemon.py::test_debug_mode_from_config`` deleted;
  every fake-env mock signature trimmed; the ``test_cli`` daemon-start
  signature trimmed.

Result: one fewer knob, traces always captured, the salvage rule
runs automatically. 256 tests green.

## [2026-05-12] refactor | clean up traces on successful task

Closing the symmetry gap from the previous commit: worktrees and
containers tear down on a clean ``done`` while traces stayed around
forever. That left ``.brr/traces/`` as the only ``.brr/`` subtree
without an outcome-aware contract — over weeks of successful daemon
runs it would have grown unbounded for no forensic value (every
durable artifact a successful run produces is already captured in
the git commit + response file + kb updates).

Changes:

- ``daemon._cleanup_traces_on_success(brr_dir, tasks_dir, task)`` is
  called after the success-path ``env_backend.finalize``. When
  ``task.status == "done"``, every directory recorded in
  ``task.meta["trace_dirs"]`` is removed and the key is dropped from
  meta so the on-disk task file doesn't leave dangling pointers.
- The failure-path finalize already sets ``status="error"`` before
  finalize runs, so the same helper is a no-op there. ``conflict``
  outcomes (auto-land collision) also keep traces because the status
  is no longer ``done``.
- Tests: ``test_cleanup_traces_on_success_removes_dirs_and_meta``
  asserts the happy-path behavior; ``..._keeps_on_failure`` exercises
  both ``error`` and ``conflict``.
- Docs (``envs.md``, ``execution-map.md``, ``brr-internals.md``)
  updated to describe traces as "forensic-only, cleaned on success".

The full outcome-aware contract now reads identically across all
three scratch artifacts: clean ``done`` removes, failures and dirty
leftovers preserve.
- Task IDs are now `task-YYMMDD-HHMM-<4 random>`. The old raw-unix
  timestamps sorted fine but read as noise.

Amended `design-daemon-landing-branch.md` with a 2026-05-12 supersedure
note covering the resolver cut and the trimmed fallback surface. The
amendment explicitly supersedes resolution-order step 2 ("existing
session/thread branch wins"). `subject-tasks-branching.md` and the
dive-in map were synced. Full suite green (256 passing).

## [2026-05-12] plan | State-first kb maintenance and regular grooming

Evaluated the operator concern that the kb is still growing like an
inline history log instead of a current-state synthesis layer. Captured
the proposed refinement in
[`plan-kb-state-first-maintenance.md`](plan-kb-state-first-maintenance.md):
subject hubs should describe what is true now, decisions should keep
only the rationale that still constrains future work, and deep
implementation history should be recovered from git with short
breadcrumbs left in the kb.

The plan also surfaces a concrete flaw in the current daemon cleanup
path: the post-task kb-maintenance LLM pass runs after the user response
has been captured and is told not to commit, so any cleanup edits are
not reliably delivered as a durable branch/commit. Recommended direction:
keep deterministic preflight, but move semantic grooming into explicit
first-class maintenance tasks scheduled at idle boundaries and processed
through the same branch, response, commit, and push path as user work.

## [2026-05-13] implement | KB/Telegram/PR follow-ups

Shipped the bundle accepted off the previous plan, plus the
state-first refinement on top of `decision-kb-shape.md`:

- Telegram cards drop the `← seed_ref` fallback. The branch arrow
  now only appears when an explicit `auto_land_branch` is set, so
  the card no longer claims `main` is the landing target when the
  agent will pick its own branch.
- The runner image installs GitHub CLI (`gh`) from upstream and the
  daemon mounts `~/.config/gh` into `/brr-home/.config/gh`. The
  no-auto-land branch prompt gains a conditional nudge to
  `git push -u && gh pr create --fill` so an agent that finishes a
  task on a fresh branch can open a PR for review on its own.
- `AGENTS.md` grew a "State first, history in git" section. Pages
  are rewritten to current shape; running-diff blocks (`previously
  X, now Y`) collapse into one lineage breadcrumb. `prompts._read_recent_log`
  swapped a fixed-N entry cap for a byte budget so a single verbose
  log entry can't push older breadcrumbs out of the prompt.
- Manual grooming pass: rewrote `design-daemon-landing-branch.md`,
  `subject-tasks-branching.md`, the repo-dive-in-map header, and
  refreshed `subject-kb.md` / `decision-kb-shape.md` to align with
  the new principle. Trimmed the `design-env-interface.md`
  durability/teardown wording so it matches the shipped
  outcome-aware salvage rule.
- `kb_preflight` carries severity (`error` / `warning` / `info`)
  and four advisories: `oversized-page`, `missing-status-marker`,
  `revision-history-heavy`, `recent-log-budget-exceeded`. New
  `kb_health.compute_graph_stats` feeds graph topology stats
  (pages-by-kind, largest pages, peer-orphan candidates, log shape)
  into the maintenance prompt alongside the findings.
- Inline kb maintenance now commits leftover kb edits onto the
  task's branch as `brr maintenance <brr-maintenance@brr.local>`
  and emits a `kb_maintenance_done` packet so the response card
  shows "maintenance: N kb commits" (or "clean"). Closes the silent
  drop where cleanup edits never reached the operator.

Rejected: scheduled / proactive maintenance jobs. Inline maintenance
plus the schema rewrite cover the same ground without spawning stale
branches or unclear push semantics. The `AGENTS.md` rewrite is the
key lever for Cursor and other non-daemon sessions because they read
the same schema brr-managed runs do.

Tests: full suite is green (288 passing, up from 262). Manual
preflight pass on `kb/` left two known advisories — `oversized-page`
on `repo-dive-in-map.md` (intentional reading guide, listed for
later splitting) and a stale-status fix on `decision-bundled-docs.md`
that was applied during the pass.

## [2026-05-13] implement | Hub-coverage advisory + env subject hub

Review-driven follow-up to the state-first kb work. Two new
deterministic advisories in `kb_preflight` and a structural fix to
the kb itself:

- `hub-coverage` (info): an `index.md` section with at least two
  design / plan / decision / deck pages and no `subject-*.md` is a
  soft nudge to write a synthesis hub. Skipped when the section
  already lists a subject page or contains only research / notes
  material. Reports the clean section title (decoration stripped).
- `proposal-scaffolding` (info): a page whose `Status:` line is
  `accepted` or `shipped` but still carries two or more
  Goals / Non-goals / Alternatives / Why this PR / Proposed
  approach / Open questions headers gets a nudge to compress to
  current state plus, if warranted, a short Rejected alternatives
  appendix.
- Promoted `design-env-interface.md` to a slim spec + new
  `subject-envs.md` hub. The hub carries the current state (which
  envs ship, durability contract, salvage rule, decentralised
  merging) so the design page can stop reporting "in flight" status
  and stand as the accepted protocol spec. Dropped the design's
  proposal-shape sections (Goals, Done definition, Docs/Tests to
  add) into a tight Scope paragraph and a Test shape block; added a
  Lineage breadcrumb covering the 2026-05-06 acceptance, the
  2026-05-11 branch-intent rewrite, and today's split.
- Retargeted high-value inbound links (`subject-tasks-branching.md`,
  `subject-daemon.md`, `repo-dive-in-map.md`,
  [`kb/index.md`](index.md) Environments section) at the new hub.

After the change the preflight is down to one intentional
`oversized-page` warning on `repo-dive-in-map.md`, plus one expected
`hub-coverage` advisory on the paused "Fleet & overlays" section
(legitimate gap; the strand is dormant) and one expected
`proposal-scaffolding` advisory on `design-daemon-dev-reload.md`
that the next grooming pass will pick up. Full test suite is green
(295 passing, up from 288).

## [2026-05-14] implement | Forge-aware response card + tighter kb maintenance loop

End-to-end follow-up driven by the live "remove `status.py`" test
run: the response card was leaking local worktree paths to remote
operators, the post-task kb maintenance pass was reviewing the wrong
files, and the previous PR-creation nudge baked in GitHub-specific
tooling.

Forge URL inference (Layer 1). New `brr.forges` module parses an
`origin` remote URL and produces a clickable branch URL for the
four big forge families: GitHub, GitLab (incl. `gitlab.<corp>`
self-hosts), Bitbucket Cloud, and Gitea / Forgejo (incl.
`codeberg.org`). For internal hosts the pattern table doesn't
recognise, two `.brr/config` keys override detection
(`forge.kind`, `forge.url_base`). The daemon embeds the URL in
`push_done` after a successful push; `RunProgressView` stores it on
the projection; the compact card renders a `view: <url>` line under
`delivered`. Detection failures stay silent — no guessed URLs.

Maintenance prompt rewritten to lead with the task. The old
prompt's conditional "redundancy spot-check only when findings are
absent" had the polarity backwards: a noisy `oversized-page`
finding would crowd out the always-on review of the task's actual
edits, which is why the `status.py` run only compressed
`repo-dive-in-map.md` and missed the historical-narrative drift in
`decision-drop-streams.md`. New shape:

- "Always do this" section names the primary job — read the diff
  for every page in the new `Task-touched kb pages` block and check
  for historical-narrative leakage. Findings and graph stats are
  named explicitly as *additional* concrete targets.
- The daemon pins `task_pre_head` (the seed-ref OID resolved
  against the run root) right after env prep and threads it into
  `_maybe_kb_maintenance`, which runs `git diff --name-only
  <pre_head>..HEAD -- kb AGENTS.md src/brr/AGENTS.md` and renders
  the result as a Markdown block above the findings and stats.
- `kb_health.compute_graph_stats` gained an opt-in `task_touched`
  parameter so the rendered stats block surfaces
  "task touched N kb / AGENTS.md pages this run" alongside the
  structural stats. Older call sites that don't pass the list get a
  zero count and the line is omitted.

Delivery-contract prompt fixes. Two bullets added to
`prompts.build_daemon_prompt` so agents stop generating chat replies
remote users can't act on:

- An explicit "users read this remotely; refer to files by basename
  only" rule, citing `.brr/worktrees/...` as the bad pattern. The
  rule also tells the agent that brr already publishes a forge URL
  in the response card so they don't need to fabricate a link.
- A meaningful-branch-name nudge replacing the previous
  GitHub-specific `gh pr create --fill` snippet on the
  no-auto-land-target path. When the work has a clear theme the
  agent renames `brr/task-…` to `brr/<short-slug>` before
  committing; read-only / discussion runs keep the placeholder.
  Brr's finalize logic already follows the final branch name, so
  this is a prompt-only change with zero daemon code.

Deferred: post-task hooks for PR / MR creation. The forge URL on
the card closes the immediate "clickable link in chat" UX gap; the
hook protocol (JSON contract, timeouts, security framing) deserves
its own design pass.

Tests: 353 passing (up from 295). New coverage:

- `tests/test_forges.py` — 41 cases covering remote URL parsing,
  forge detection, and URL emission for the four families plus the
  override paths.
- `tests/test_daemon.py` — four cases for `_forge_view_url` and
  five for `_kb_pages_touched_since` / `_format_touched_block`.
- `tests/test_run_progress.py` — three cases covering `view_url`
  storage and the `view:` render line.
- `tests/test_kb_health.py` — four cases for the `task_touched`
  parameter and its rendered line.
- `tests/test_prompts.py` — one case for the no-local-paths bullet;
  the existing PR-nudge case rewritten for the branch-rename
  bullet.

## [2026-05-14] implement | Remove the unused status module

Deleted `src/brr/status.py` after checking that it had no runtime or CLI
callers; the only importers were direct tests for the private helper
module. Removed those obsolete tests, kept the CLI coverage that asserts
`status` and `inspect` are not public commands, and updated bundled docs
plus kb pages so run progress is described as remote-first through
`updates.py`, `run_progress.py`, and gate renderers.

## [2026-05-14] fix | Fleet hub and dev-reload synthesis

Added `subject-fleet-overlays.md` so the paused overlays / brnrd material
has a current-state hub, narrowed the overlays blocker to its research
gate, and compressed the shipped dev-reload design out of proposal
scaffolding into a current reference.

## [2026-05-15] implement | Daemon freshness + delete tasks-folder gate

Phase 1 of the git layer rework (see `design-git-layer-rework.md`).
The daemon now runs `sync.refresh_before_task` before resolving each
task's branch plan: a single `git fetch <default-remote>` plus a
best-effort `--ff-only` advance of the local default branch and any
structured branch named on the event (`branch_target`,
`target_branch`, `base_branch`, legacy `branch`). Outcomes ride the
progress card on a new `synced` packet that stays quiet on the no-op
path. Two opt-out config knobs in `.brr/config`:
`sync.fetch_before_task` and `sync.fast_forward_default`, both
default-on; the second is the safety valve for users sharing the
daemon's checkout with active local dev work.

The seed-ref invariant after this change: the daemon's local view of
each named target branch is at least as fresh as the remote was at
task start, or the result records why it isn't (dirty tree,
divergence, fetch failure, opt-out). Worker code can rely on the
seed ref reflecting that view rather than the operator's last manual
`git pull`. The fast-forward is `--ff-only`, so it never destroys
local commits, and the call never raises — any unexpected exception
is captured into `SyncResult.error` so a flaky network can't block a
task.

Deleted `src/brr/gates/git_gate.py` and removed it from
`_BUILTIN_GATES`, the CLI gate map, the bundled gate README, and
several kb / bundled-doc references. The tasks-folder watcher was a
niche workflow with an awkward primitive (always-empty tracked
directory; concurrent execution model unclear; overlapping with
`.brr/tasks/`). Anyone wanting a folder watcher can write one with
the bash protocol example in `gates/README.md`. Stale
`.brr/gates/git_gate.json` on existing installs becomes inert: the
daemon's `import_gate` catches `ImportError` and skips.

Tests: 370 passing (up from 345). New coverage in
`tests/test_sync.py` (23 cases for fetch+ff scenarios, config
opt-outs, error capture, render summary) and three new cases in
`tests/test_daemon.py` for the sync hook ordering, soft-failure
propagation, and the `_branches_to_refresh` helper. The
`test_git_setup_saves_watch_configuration` case in
`tests/test_gate_setup.py` was deleted alongside the gate.

Phase 2 (real GitHub gate) and Phase 3 (prompt-level mitigation for
runner thoughtfulness on revisit-loaded tasks) are queued on the
same plan and design page; the design page will amend in place as
each lands.

## [2026-05-15] implement | GitHub gate (Phase 2 of git-layer rework)

Phase 2 of the git layer rework. New built-in gate
[`gates/github.py`](../src/brr/gates/github.py) — stdlib `urllib`
against `https://api.github.com`, mirroring the slack/telegram
shape. Two configurable triggers, both opt-in:

- `label-on-issue`: polls `/issues?labels={label}` and emits one
  inbox event per newly-labelled issue. PRs returned by the same
  endpoint are deliberately filtered out.
- `mention-in-comment`: polls `/issues/comments` and emits one event
  per comment containing the configured mention string. PR-comment
  events fetch the PR head ref and pin it as `branch_target`, so
  Phase 1's pre-task fetch+ff hook refreshes that branch before the
  worker runs. The bot's own login is filtered to avoid
  self-trigger loops.

Auth: `resolve_token` chain is stored > `gh auth token` > env
(`GITHUB_TOKEN` / `GH_TOKEN`). gh CLI / env tokens are never
persisted; pasted tokens land under the gitignored `.brr/`. Repo
autodetect parses both HTTPS and SSH origin URLs, ignores non-
github.com remotes. Replies post as comments on the originating
issue / PR via `POST /issues/{number}/comments`. Rate limits and
4xx / 5xx errors handled distinctly: `Retry-After` wins, then
`X-RateLimit-Reset`, then a long backoff for non-transient 4xx, a
short one for 5xx.

`is_configured` requires repo + at least one trigger + a resolvable
token, so adding `github` to `_BUILTIN_GATES` does not surprise-
enable anything for users who haven't run `brr setup github`.

Tests: 403 passing (up from 370). New `tests/test_github_gate.py`
(33 cases) covers the token chain, autodetect from HTTPS and SSH
origins, both triggers, the PR-skip on label trigger, the
self-comment filter, the no-mention skip, cursor advancement,
response posting, and the error-handling matrix. All API calls
mocked at the `_api_get` / `_api_post` boundary.

Phase 3 (prompt-level mitigation for runner thoughtfulness) is the
remaining piece on the same plan and design page.

## [2026-05-15] implement | Runner thoughtfulness (Phase 3)

Final phase of the git layer rework. Three small surface tweaks that
attack the path-of-least-resistance failure mode the
`brr/git-gate-defaults` runner exhibited, without adding a pre-task
plan stage:

- A new *"When the task asks you to reconsider"* section in
  [`prompts/run.md`](../src/brr/prompts/run.md) names the trigger
  phrases verbatim (`revisit`, `not great`, `wdyt`, `is this the
  right shape`, etc.) and tells the runner what to do: re-read the
  relevant code and design pages, surface contradictions per
  Stewardship before resolving them, and prefer a chat-only reply
  over a half-fitting commit when the right next step isn't clear
  yet.
- "Chat-only reply" is named explicitly as a complete and successful
  task outcome for those signals, so the diff-as-receipt rule
  doesn't override the right call on a revisit task.
- A new self-review bullet in [`AGENTS.md`](../src/brr/AGENTS.md)
  promotes the Stewardship principle from prose into a concrete
  checklist item: *"If the task contained a contradiction with the
  current code, design notes, or guardrails — did you surface it
  before resolving it? (See Stewardship.)"*

Why no plan stage: judgment failure, not a procedural one. A
separate stage would split design from execution into different
runs, hurt follow-through, and consume tokens on the 95% case
(implement / fix / Q&A) where it adds no value. Post-task
kb-maintenance is justified because it's mechanical; a pre-task
plan stage would not be.

Tests: 406 passing (up from 403). New `TestRevisitSignalGuardrails`
in `tests/test_prompts.py` reads the bundled `run.md` and
`AGENTS.md` directly to pin the trigger-phrase list, the
chat-only-reply outcome, and the new self-review bullet. Silent
prompt drift that drops them fails loudly.

This closes the three-phase git layer rework
(`kb/design-git-layer-rework.md` is now `Status: shipped`).

## [2026-05-16] implement | Groom test suite around intent and shared scaffolding

Filed [`kb/research-test-suite-grooming-2026-05-16.md`](research-test-suite-grooming-2026-05-16.md)
mapping the bloat in `tests/` (mainly inline scaffolding duplicated
across daemon-test files plus one file fully covered by another),
then executed the high-leverage cuts in three commits:

- Dropped `tests/test_integration.py` — three of its four tests were
  weaker copies of `tests/test_adopt.py` and the fourth was a
  tautology of its own mock.
- Extracted `tests/_helpers.py` with `init_git_repo`, `commit_files`,
  `write_repo_scaffold`, `make_event`, `StubWorktreeEnv`, and a
  `succeed_invoke` factory. Migrated `test_daemon.py`,
  `test_daemon_progress_packets.py`, `test_daemon_conversations.py`,
  `test_branching.py`, `test_envs.py`, `test_gitops.py`, and
  `test_sync.py` to import from it. `test_daemon.py` shrank from
  1303 to 1172 LOC, mostly by collapsing five inline `StubEnv`
  copies inside the kb-maintenance tests onto the shared
  `StubWorktreeEnv(invoke_fn=succeed_invoke("ok\\n"))` factory.
- Replaced four real-git `test_forge_view_url_*` tests in
  `test_daemon.py` with three stub-based tests that cover only the
  wrapper's actual responsibilities (read remote via gitops, read
  forge overrides from config, swallow exceptions). URL templating
  is already exhaustively parametrised in `test_forges.py`.
- Parametrised the False/`"false"` twin in `test_envs.py`; tightened
  the redundant "no triage" assertion in
  `test_run_worker_constructs_task_without_triage` to a single
  exact-equality check.

Suite shape went 29 files / 7970 LOC / 406 tests → 28 + helpers /
7786 LOC / 401 tests, still passing in ~3 s. Decision: keep the
four-file daemon-test split (worker, progress packets,
conversations, heartbeat) — combined they'd be ~1820 LOC and the
concerns are genuinely distinct.

## [2026-05-16] implement | Agent orientation layering — slices 1 and 2

Two same-day ergonomics reviews converged on the same diagnosis: the
playbook + prompts treat *stage* (ad-hoc / daemon task /
kb-maintenance / setup) implicitly, so external Cursor sessions
filter daemon-only material on every read and daemon-launched
runners open the run context file even when the bundle already
covered them. Filed both reviews and acted on the high-leverage
slices in one arc:

- [`kb/research-cursor-orientation-ergonomics-2026-05-16.md`](research-cursor-orientation-ergonomics-2026-05-16.md) —
  external Cursor view, ~4,200 lines of orientation context for a
  session that used ~25-30%.
- [`kb/research-runner-orientation-ergonomics-2026-05-16.md`](research-runner-orientation-ergonomics-2026-05-16.md) —
  daemon-launched-runner view from inside Docker, naming the
  stage-vs-environment axis as the missing layering and the Task
  Context Bundle as the right place to hang stage/source/env.
- [`kb/plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md) —
  synthesis page locking in the four-layer model (repository
  contract / stage overlay / runtime state packet / subject
  knowledge) and tracking slice status.

Slice 1 (`feat(prompts):`) — opens the Task Context Bundle with a
`### Mode` block (Stage / Source / Environment / Delivery / Runtime
recovery). `daemon.py` threads `task.source` and `task.env` into
both the first-attempt and retry-attempt prompts. `prompts/run.md`
is rewritten to point at the Mode block as the authoritative "where
am I?" surface, declare that the injected
`Recent Activity (from kb/log.md)` extract satisfies AGENTS.md's
kb/log.md startup step, and treat the generated run context file
as recovery detail. `run_context.py` header matches that framing.
Tests: a `TestDaemonModeGuardrails` class pins the new run.md
anchors, plus three cases over the Mode block.

Slice 2 (`refactor(AGENTS.md):`) — stage-aware restructure. New
"How to read this playbook" section after Project names the three
stages and tells each one which sections apply, with `### Mode`
as the detection hint. Workflow rebuilt as Orientation
(universal) + Task types + Commits (universal) + "When the brr
daemon runs you" (daemon-only subsection absorbing Daemon
freshness, the `brr/<task-id>` commit nuance, and the
delivery/recovery rules). "Work re-review" deleted — it duplicated
Session startup. Orientation carries a concrete tail-fetch recipe
for `kb/log.md`. Constraints updated for the new universal section
list. `decision-kb-shape.md` gets a lineage breadcrumb pointing at
the plan page for the section rename.

Cheap dive-in-map polish folded into the same kb commit: opens
with a "How to read this page" two-halves declaration
(orientation vs reference) so cold readers can stop after the
snapshot block if they only need to act. Full orientation-vs-
reference split deferred to a future slice; tracked on the plan.

Slice 3 (snapshot regression test) and the canonical-home-per-fact
cleanup remain open follow-ups on the plan; both reviews flagged
them as in-passing chores. Full suite green at 404 tests (was 401
before slice 1's three new cases).

## [2026-05-16] research | Cursor orientation ergonomics — follow-up

Second-pass external Cursor view taken after slices 1 and 2 of the
agent-orientation layering plan landed earlier the same day. Filed
[`kb/research-cursor-orientation-ergonomics-followup-2026-05-16.md`](research-cursor-orientation-ergonomics-followup-2026-05-16.md)
and routed the recommendations onto
[`kb/plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md).
No code or AGENTS.md edits applied yet — operator selects which
findings to act on.

Headline findings:

- **Workspace-rule cache delivers a stale `AGENTS.md`.** Cursor
  injected the *pre-slice-2* playbook as the workspace rule (no
  "How to read this playbook", `Session startup` + `Work re-review`
  still split, log-tail recipe missing), while the on-disk file
  carries the new shape. The slice that was meant to short-circuit
  daemon-only filtering is silently invisible until the agent
  notices the drift. Cheap brr-side mitigation: a top-of-file
  `Revision:` marker plus a one-line "trust the on-disk file" rule
  in the ad-hoc-agent stage block.
- **README ↔ AGENTS.md elevator-pitch + Build-and-run duplication
  is real and trimmable.** The user's prompt named this; confirmed.
  `# Project` (11 lines) and `## Build and run` (16 lines) restate
  material that lives canonically in `README.md` and
  `pyproject.toml`. ~25 lines saved per session × every adopter if
  trimmed to a one-liner pointer. First concrete target for the
  plan's open canonical-home-cleanup follow-up.
- **Slice 3 (snapshot regression test) is rejected** as low ROI:
  `TestDaemonModeGuardrails` already pins the load-bearing anchors,
  and a snapshot would tax every prompt copy-edit on the cheap
  iteration loop. Plan updated.
- **Dive-in-map two-halves declaration earns its keep.** The cheap
  polish from slice 2 worked — this session stopped after the
  orientation block. Deeper splits stay deferred.
- **Cursor-side wishlist** gained one new entry: invalidate the
  workspace-rule cache on file-content change. Not brr's to ship.

Smaller findings recorded for batched cleanup: a Code-guidelines
bullet that drifted in from Stewardship territory (with a typo);
Self-review #5 overlapping Knowledge base → Health checks; an
optional cold-start sanity-check block (workspace rule may be
stale, git status may be stale, terminals/skills are ambient).

## [2026-05-16] implement | AGENTS.md trim + workspace-rule drift guard

Acted on the follow-up review's recommended cuts in one commit on
`main`:

- **Workspace-rule drift guard.** Top-of-file `Revision: 2026-05-16`
  marker, with a one-line maintenance rule asking future structural
  edits to bump the date. Paired with a new "Ad-hoc sanity check"
  block in Workflow → "How to read this playbook" naming the
  recurring host frictions (workspace-rule cache lag, stale git
  status, ambient terminals/skills) and how to reconcile them.
  Daemon and kb-maintenance stages are scoped out of the block;
  they take their hot-path context from the prompt.
- **Project-block trim.** Cut the elevator-pitch duplication with
  `README.md`. The Project block now leads with what the file *is*
  (brr's playbook + adopter template), where the canonical copy
  lives, and the stdlib-only constraint that affects code edits;
  product overview points at `README.md`.
- **Build-and-run trim.** Replaced the eight-stanza shell block
  with `pip install -e ".[dev]" && pytest`, a pointer to
  `README.md` → Development for variants, and the line that
  `pyproject.toml` is the source of truth.
- **Code-guidelines / Stewardship homing.** Moved the
  "read-before-editing" rule from Code guidelines into Stewardship
  (it's a discipline, not a code-style rule) and fixed the typo
  (`unless it the task is real straightforward` →
  `unless the task is straightforward` — actually trimmed the
  caveat entirely; "non-trivial edits" carries the proportionality).
- **Self-review #5 compression.** Pointed at Knowledge base →
  Health checks rather than re-stating one entry from the list.

Net AGENTS.md change: +43/-29 (net +14 lines), 476 → 490 lines. The
sanity-check block more than offsets the trims, but the file is
materially less duplicative with `README.md` and the new block is
load-bearing. Full test suite green at 404 passing; `kb_preflight`
clean apart from the known `oversized-page` advisory on
`kb/repo-dive-in-map.md` that Finding 8 of the same review argues
for keeping deferred. No bumps to test anchors — `Stewardship` and
`did you surface it before resolving it` (Self-review #2 / the
contradiction-surfacing checklist item) both still load as
asserted.

Plan housekeeping: `plan-agent-orientation-layering.md` was already
updated by the research commit (slice 3 rejected, follow-ups
re-routed).

## [2026-05-16] implement | Gate responses thread under their source event

First slice of the concurrent-execution roll-up: make every gate's
response visibly reply to the message that triggered it, so concurrent
tasks in the same channel/chat can be told apart at the source. The
work stays gate-local — no daemon-loop changes, no shared-state
locking — and lands value today regardless of whether the threading
work follows.

- **Telegram** ([`gates/telegram.py`](../src/brr/gates/telegram.py)):
  capture `telegram_message_id` at event creation; thread both the
  status card's *initial* `sendMessage` and the final response's
  `_send_with_overflow` via `reply_to_message_id` plus
  `allow_sending_without_reply: true` (resilient when the source is
  deleted mid-run). `editMessageText` deliberately doesn't carry the
  reply pointer — Telegram has no way to change a message's reply
  target after the fact, so only the first send matters.
- **Slack** ([`gates/slack.py`](../src/brr/gates/slack.py)): capture
  `thread_ts` (the parent ts when the source message is itself an
  in-thread reply) at event creation; thread the final
  `chat.postMessage` on `slack_thread_ts or slack_ts`. The status card
  was already threaded — this fixes the existing inconsistency where
  the card lived in-thread while the response posted at channel
  level, splitting the conversation in half.
- **GitHub** ([`gates/github.py`](../src/brr/gates/github.py)):
  mention-trigger replies now prepend `> Replying to [@author's
  comment](url)` (or a no-handle variant for deleted users). Issue and
  PR comment endpoints have no first-class reply primitive, so a
  blockquote pointer is the closest visible anchor (matches what the
  GitHub UI's "Quote reply" button generates). Label-trigger replies
  are unchanged — the issue itself is the source.

Out of scope, surfaced in the same conversation but deliberately not
done here: (1) the daemon's worker loop is still single-threaded, and
[`subject-daemon.md`](subject-daemon.md) + the abandoned
merge-coordinator path on
[`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md) record
that as a deliberate decision; reversing it should land as its own
PR with the kb decision rewrite up front rather than slip in as a
side-effect; (2) the proposed "tell the runner to pick a new branch
name if checkout fails" prompt nudge was dropped — the default
worktree flow (`brr/<task-id>` is unique per task id) is immune to
that collision, so it would have been guidance for an imagined
problem.

Tests: 414 passing (was 404). +10 new: 4 telegram (event capture
records `message_id`; delivery threads; legacy no-message-id events
still deliver; `_send_message` API params), 3 telegram render-update
(first send threads, edits don't carry the pointer, legacy tasks
still post), 3 slack (parent `thread_ts` capture from
`conversations.history`, delivery threads, legacy events still
post), 2 github (mention-trigger response quotes the source comment;
falls back without `@handle` when author missing). The existing
`test_replies_are_sent_to_originating_chat` stub for telegram was
widened to accept the new keyword.

## [2026-05-16] implement | Concurrent task execution via contention-free state

Second slice of the concurrent-execution roll-up: the daemon worker
loop is now a bounded `ThreadPoolExecutor` (default `max_workers=2`,
config-overridable). Reaching that needed a redesign of every shared
mutable surface the old serial loop was hiding, not just an
"add-locking" pass — the CRDT-vibes steer the operator put on the
direction earlier in the conversation. Partitioning by task / event
removed every cross-worker file write the daemon used to make,
leaving only two genuinely-shared resources (the auto-land target ref
and the push branch ref), each guarded by its own per-branch lock.

KB first: [`subject-daemon.md`](subject-daemon.md) gained a
**Concurrency model** section laying out the partitioning rule and the
two per-resource locks; the old "Concurrent worker pool. Still
deferred." bullet was rewritten as a 2026-05-16 lineage breadcrumb
pointing at the new design.
[`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md) was
marked **superseded** with a breadcrumb noting the
merge-coordinator path it described was abandoned and *never came
back* — the partitioned model replaces the coordinator entirely. New
canonical design page: [`design-concurrent-execution.md`](design-concurrent-execution.md)
spells out the contract, the resource→writer table, the
conversation-layer change, the gate-progress change, the packet flow,
the threaded loop, the per-branch locks, and the dev_reload
quiescence semantics under concurrency. `kb/index.md` linked.

Implementation, in shipping order:

- **Conversation layer** ([`conversations.py`](../src/brr/conversations.py)):
  `.brr/conversations/<key>.ndjson` → `.brr/conversations/<key>/<event-id>.jsonl`.
  Every record one worker emits lands in that one event's file; the
  file has exactly one writer for its lifetime. `read_records` globs
  the directory and merges by `ts`; new `read_event_records` opens
  just the one file when a caller already knows the event id.
  Timestamps bumped to microsecond precision so multi-file merge
  ordering survives sub-second concurrent appends. Single-line
  `O_APPEND` writes in binary mode are defence in depth — the
  per-event-file partitioning already guarantees one writer per file,
  but the kernel atomicity guarantee makes the orphan-fallback path
  safe too. `safe_filename`/`key_from_filename` renamed to the
  directory-name variants `safe_dir_name`/`key_from_dir_name`.
- **Packet flow** ([`updates.py`](../src/brr/updates.py),
  [`daemon.py`](../src/brr/daemon.py)): `UpdatePacket` gained an
  explicit `event_id` field so `conversations.append_update` knows
  which per-event jsonl to write. `daemon._run_worker` now builds a
  `_WorkerEmit(brr_dir, conv_key, event_id)` closure-like dataclass
  and calls `emit("packet_type", **payload)` everywhere — the helpers
  `_emit_new_containers`, `_emit_preserved_containers`,
  `_record_response_artifact`, `_maybe_kb_maintenance`, and
  `_push_if_needed` all take an `emit` (or carry `event_id`)
  argument. The repetition of `updates.emit(brr_dir,
  updates.UpdatePacket(type=..., conversation_key=conv_key,
  payload={...}))` is gone, ~120 lines of churn removed.
- **Gate progress state** ([`gates/telegram.py`](../src/brr/gates/telegram.py),
  [`gates/slack.py`](../src/brr/gates/slack.py)):
  `.brr/gates/telegram_progress.json` (single shared dict) →
  `.brr/gates/telegram/progress/<task-id>.json` (one file per task);
  same for slack. New helpers `_load_progress_for_task` /
  `_save_progress_for_task` collapse the old load → mutate → save
  triplet into a per-task load/save pair. Two concurrent renders
  for two tasks touch two distinct files; no locks.
- **Threaded loop** ([`daemon.start`](../src/brr/daemon.py)):
  the serial body became a dispatch loop on a `ThreadPoolExecutor`
  capped at `max_workers`. New `_run_worker_and_finalize` wraps the
  existing `_run_worker` plus the post-task `set_status`, push,
  and dev_reload-flag bookkeeping that used to live in the main
  loop body, so each worker thread owns its full pipeline. The
  main loop reaps completed futures, throttles dispatch to
  capacity, polls the dev_reload watcher (main thread only — the
  watcher's snapshot state isn't thread-safe), and waits for
  `reload_requested and not in_flight` before re-execing. Per-branch
  locks via a tiny `_branch_lock(name)` helper backed by a
  `defaultdict(Lock)` guard the `WorktreeEnv` finalize's
  fast-forward and `_push_if_needed`'s git push.
- **Config knob**: `max_workers` reads from `.brr/config`
  (`_DEFAULT_MAX_WORKERS = 2`). Setting `max_workers=1` reproduces
  the previous serial-v1 behaviour exactly for adopters that don't
  want concurrency.
- **AGENTS.md** worktree note: added one sentence under the
  branch-and-commit nuance paragraph saying that if a runner that
  opts out of `brr/<task-id>` collides with another concurrent task
  on the same chosen name, fall back to a unique variant. Kept
  minimal per the operator's "fine to skip, very minor" steer.
- **Bundled docs**: `src/brr/docs/conversations.md` updated to
  describe the directory-of-jsonls layout and the one-writer-per-
  event invariant; `src/brr/docs/brr-internals.md` updated for the
  per-task gate progress file paths.

Out-of-scope rejections recorded on `design-concurrent-execution.md`
so future revisits don't re-litigate them: a merge coordinator, a
lock on the old aggregated ndjson, an asyncio rewrite, per-task
subprocess workers.

Tests: 424 passing (was 414). Conversation layout migrated; the
`safe_filename`/`key_from_filename` tests became
`safe_dir_name`/`key_from_dir_name`/`conversation_path`/
`event_log_path` tests. Existing daemon tests updated where they
depended on `_push_if_needed` running on the main thread (it now
runs in the worker, so termination via `StopIteration` moved to
`protocol.list_pending`); the `_maybe_kb_maintenance` signature
change rippled into two tests. +5 new: 4 in
`test_daemon_concurrency.py` (two events dispatch in parallel with
`max_workers=2`; `max_workers=1` enforces serial dispatch; a worker
crash doesn't kill the daemon; default `max_workers` lands on
`_DEFAULT_MAX_WORKERS` when config omits it), 1 in
`test_conversations.py` (two concurrent writers for different event
ids in the same conversation don't lose records and merge cleanly
on read). Run-progress had a sub-second rounding bug surfaced by
microsecond timestamps — `_to_iso` was throwing away microseconds
when round-tripping `now` through string format, so the live
elapsed counter was off by up to 1s; fixed by preserving
microseconds in `_to_iso`.

No migration code. The operator confirmed no users persist across
this change, so old `.brr/conversations/*.ndjson` and
`.brr/gates/{telegram,slack}_progress.json` files become inert
(no readers). They can be deleted by hand or left as historical
artefacts.

## [2026-05-17] implement | GitHub gate: any-trigger + setup UX fix

Fixed two issues in `src/brr/gates/github.py`:

**Setup UX**: The `bind()` prompts said "empty to disable" but pressing
Enter actually accepted the default. Added `_prompt_trigger()` helper;
prompts now say "off to disable", Enter accepts the bracketed value, and
`off`/`none`/`disable` removes the trigger. No behaviour change — just
accurate copy and a cleaner abstraction.

**Any trigger**: Added a third trigger `any` (boolean). When enabled,
`_poll_any_activity()` polls all issues, PRs (`github_kind=pr`,
`branch_target` from PR head), and comments (`github_kind=issue-comment`
or `pr-comment`) without label/mention filtering. Bot's own comments are
still filtered. Overrides label and mention in `_loop_once`. Setup adds an
"any" prompt first with a token-cost warning; answering `on` skips the
label/mention prompts. Off by default.

Tests: 432 passing (was 424). +10 new in `test_github_gate.py`: 4 for
`bind()` UX (Enter accepts default, off disables, typed value overrides,
any skips subsequent prompts), 5 for `_poll_any_activity` (issue event,
PR event with `branch_target`, comment events with bot-self-filter, any
overrides label/mention routing).

## [2026-05-17] fix | Docker env: SSH mount + GitHub token injection

Addressed two failures observed in an e2e GitHub gate run (PR #14) where the
agent inside Docker could not use `gh` CLI (unauthenticated) and could not
push via SSH (no key).

**Root cause 1 — `gh` unauthenticated**: On Linux, `gh auth login` stores
tokens in the system keyring (libsecret), not in `~/.config/gh/hosts.yml`.
The existing `.config/gh` directory mount therefore carried the config but
not the secret. Fix: add `GITHUB_TOKEN`/`GH_TOKEN` to
`_DOCKER_DEFAULT_PASSTHROUGH_ENV` (forwarded from daemon env when set), and
for tasks with `source == "github"` inject the gate's stored token directly
as `GITHUB_TOKEN=...` in the container args by reading
`.brr/gates/github.json`. A new `_resolve_github_gate_token` helper in
`envs/__init__.py` handles the state file read with a silent fallback.

**Root cause 2 — SSH push failure**: `.ssh` was missing from
`_DOCKER_DEFAULT_CRED_PATHS`. Added it; the mount is skipped when the
directory doesn't exist on the host (matching the behaviour of all other
credential paths) and is omitted entirely when `docker.mount_credentials=false`.

Tests: 451 passing (was 449). +4 new in `test_envs.py`: SSH mount present
when directory exists; GitHub token injected as key=value when task source is
`github`; no injection for non-github tasks; key=value form absent (bare
passthrough used instead) when `GITHUB_TOKEN` is already in daemon env.

## [2026-05-18] fix | Branch plan: event branches seed from remote ref

PR #14's second run produced a tangled branch — the daemon's pre-task ff
was refused on a non-fast-forward (`origin/brr/runner-ergonomics-review`
diverged from the local branch), then the worker seeded from the **local**
branch and rebased onto the local `origin/...` tracking ref instead of
`origin/main`. The result was three commits that re-implemented main
commits under different SHAs, plus the three genuine new ones.

Root cause in `branching._plan_for_target`: when an event names a target
branch, the seed was `target` (the local branch), with the local oid as
the ff anchor. If the local branch had diverged from the remote, the
worker started from a stale point — and the daemon's sync hook is
ff-only, so it does nothing about divergence; it just records it.

Fix: `_plan_for_target` gained a `prefer_remote` parameter (default
False). `resolve_branch_plan` passes `prefer_remote=True` for the
event-branch path only. When set, the plan looks up
`<remote>/<target>` via `gitops.rev_parse`; if present, that becomes
the `seed_ref` and `expected_old_oid`. The `fallback:current` path
keeps the host-branch behaviour — that mode is the self-development
knob where the host is the source of truth.

Salvage: rebuilt `brr/runner-ergonomics-review` by cherry-picking the
5 genuine commits onto `origin/main` and force-pushing with
`--force-with-lease`. The three duplicate-of-main commits were dropped.

Known gap recorded here so the next run doesn't re-derive it: an agent
that rebases or otherwise rewrites a published task branch produces a
non-fast-forward push, and brr's auto-push uses plain `git push` (no
force). Today's mitigation is the new remote-seed: agents won't *need*
to rebase a PR branch to catch up, because the worker already starts
from the forge-visible state. If a future task type genuinely needs to
rewrite published history (squash, fixup), it will require an explicit
force-with-lease publishing story; not in scope for this fix.

Tests: 454 passing (was 451). +3 in `test_branching.py`: event branch
seeds from `origin/<branch>` when the local copy has diverged from the
remote ref; falls back to the local branch when no remote ref exists;
`fallback:current` ignores the remote (self-development mode).

## [2026-05-18] fix | Release responses before kb maintenance

Recent daemon logs showed the runner response artifact was written, but
the originating gate could not deliver it until after kb maintenance,
environment finalization, and branch push. Root cause: gates only deliver
events with `status: done`, while `_run_worker_and_finalize` set that
status after `_run_worker` returned, and `_run_worker` included the
post-response housekeeping path.

Fix: successful `_run_worker` now marks the inbox event `done`
immediately after the response file is validated and the task status is
persisted, before `_maybe_kb_maintenance`. The worker tail no longer
rewrites an already-deliverable event and tolerates the gate having
cleaned up the inbox file while maintenance/finalize/push continue.
GitHub's branch footer now waits for `changed_branch` rather than using
the prepared `branch_name`, avoiding a race where early delivery could
link a branch before finalization had identified what should be
published.

Docs updated: `subject-daemon.md`, `repo-dive-in-map.md`,
`execution-map.md`, and `gates/README.md` now describe `done` as
"response is deliverable" rather than "all daemon housekeeping is
finished."

Tests: 457 passing (was 454). +3 tests: response release happens before
kb maintenance; worker finalization tolerates gate cleanup after early
delivery; GitHub branch footers ignore `branch_name` before finalization.

## [2026-05-18] fix | KB consistency: compress dive-in map and reconcile env labels

Compressed `repo-dive-in-map.md` from an oversized module-by-module
reference into a compact current-state reading guide, keeping source
and tests as the authoritative detail. The pass also removed a stale
status-module reference and reconciled the env kb wording with source:
`host`, `worktree`, and `docker` are the shipped backends; `ssh`,
`devcontainer`, and plugin/script env registries remain accepted design
surface, not wired runtime behavior.
## [2026-05-18] fix | Sync: ff every tracking branch, agent prompt nudge

Closes the freshness gap that `prefer_remote` didn't cover. The earlier
fix only kicks in when the event itself names a branch (GitHub gate with
`branch_target`). For free-text gates like Telegram — "rebase
brr/feature-b onto main" — the daemon couldn't know which branch the
agent was about to consume, so the local copy of `feature-b` could be
stale relative to the remote when the agent did `git switch feature-b`
inside the worktree.

Two parts:

**Daemon: broaden the pre-task ff to every tracking branch.**
`sync.refresh_before_task` now sweeps every local branch with a matching
`<remote>/<branch>` after the explicit-target ff pass. Targets keep
their failure-recording behaviour (the caller asked, the caller gets
told); sweep-discovered branches that can't ff are **silent no-ops**
(not in `result.skipped`) so abandoned branches don't pollute the
progress card. Gated by `sync.fast_forward_all` (default True). New
`gitops.list_local_branches` lists local heads via
`git for-each-ref refs/heads/`. `_try_fast_forward` gained a
`silent_on_skip` parameter; a new `_sweep_candidates` builds the
discovery list.

**Agent prompt nudge in `src/brr/prompts/run.md`.** A new section
"Working on a branch the task names" tells the agent to seed work from
`origin/<branch>` rather than the local branch when the task asks them
to operate on something other than their task branch. The daemon's
sweep makes this almost always equivalent to using the local name, but
the nudge keeps agents robust against the cases the sweep can't fix
(force-push divergence, network-disabled daemon, branch the daemon
hasn't seen yet).

Tests: 458 passing (was 454). +4 in `test_sync.py`: sweep advances
non-target tracking branches; sweep failures stay silent; explicit-
target failures still recorded; `sync.fast_forward_all=false` reverts
to the pre-sweep contract.

## [2026-05-18] fix | Runner and daemon can publish rebased GitHub branches

Closed the gap left by the PR #17 rebase attempt: the runner could
produce the rebased branch locally, but `git push` inside Docker still
used the repo's SSH remote and failed with `Permission denied
(publickey)`. Docker GitHub tasks now resolve a gate token from stored
state, env, or `gh auth token`, pass it as `GITHUB_TOKEN`, and configure
git to rewrite GitHub SSH remotes to HTTPS with a token-backed
credential helper. Runner-initiated pushes now have a credentialed path
without requiring an SSH agent in the container.

The host-side daemon publish path also learned the explicit PR-rebase
case. When the changed branch is the resolved auto-land target and the
branch is not a fast-forward of its remote-tracking ref, `_push_if_needed`
uses `--force-with-lease` anchored to the remote OID captured before the
run. This is deliberately narrower than a general force-push: other
branches keep ordinary push semantics.

Progress rendering now treats failed `push_done` packets as `push
failed` instead of saying `pushed N commits`, so a delivered response
card no longer hides the publish failure.

## [2026-05-19] fix | KB branch resolver prose matches shipped resolver

Post-task kb maintenance checked the rebased-branch publish notes
against `branching.py`, `daemon.py`, and `envs/__init__.py`. The branch
hub and daemon branch design now describe the shipped resolver order:
structured event branch fields (`branch_target`, `target_branch`,
`base_branch`, legacy `branch`) are the only daemon auto-land authority;
fallback policy is only `preserve` or explicit `current`; conversation
branch facts stay prompt context. The design's history was compressed
to one lineage breadcrumb, and the index status now includes the
2026-05-18 leased-publish amendment.

## [2026-05-21] refactor | Collapse the publish pipeline around one kernel

Collapsed the daemon's land+push pipeline into one publish step. The
agent leaves work on a branch; the daemon publishes that branch.
`branching.BranchPlan` became `PublishPlan` (`auto_land_branch` →
`expected_publish_branch`, `expected_old_oid` → `expected_remote_oid`).
`WorktreeEnv._land_or_preserve` was replaced with a 4-state outcome
classifier (`ready` | `nothing` | `detached`); finalize no longer
touches non-task refs. `daemon._push_if_needed` +
`_push_lease_anchor` + `_needs_force_with_lease` + `_push_command`
collapsed into `daemon.publish()` plus a small `_push_command` builder
with a 5-arm decision table (noop / plain / upstream / refspec /
lease). The metadata triple `preserved_branch` / `landed_branch` /
`changed_branch` collapsed to `publish_branch` +
`publish_status`; six readers (`run_progress.py`, `run_context.py`,
`prompts.py`, `conversations.py`, `gates/github.py`, `daemon.py`) now
consume only those keys. `gitops.advance_branch_with_anchor` deleted
(only caller was the old land path). `branch.fallback=current` removed
with a one-shot warning for legacy configs. New
[`design-publish-kernel.md`](design-publish-kernel.md) supersedes
[`design-daemon-landing-branch.md`](design-daemon-landing-branch.md).
Cross-task freshness is unchanged — `sync.refresh_before_task` plus
the resolver's `prefer_remote` seeding from `<remote>/<target>` cover
the case the predecessor design used local-land for.
## [2026-05-21] research | Positioning and runtime dependencies re-evaluation

Reframed the zero-dependency constraint as one symptom of a broader
positioning question. New page at
`kb/research-positioning-and-runtime-deps-2026-05-21.md`, cross-linked
from `kb/index.md` (Research section) and as a peer of
`kb/research-brr-vs-gh-aw.md`. Per-candidate analysis: `dulwich` is a
net negative (no `git worktree` support, would split brr's git code
path); `requests` is a clean modest win across the three gates
(~80-100 LOC); per-forge SDKs (PyGithub / python-telegram-bot /
slack_sdk) are a bigger lever (300-500 LOC saved) but defer to a
separate decision. Part 2 of the page reads brr's positioning against
the AI-tool creator crowd: the README tagline buries the hook, the
killer Telegram-as-remote-control demo is invisible from the landing
page, and `pip install brr` signals dated Python next to `uvx`. Ranked
adoption moves put a 60-90s demo video first and the deps change at #6.
No code or README touched in this pass; natural follow-ups are a
`decision-runtime-dependencies.md` and a `plan-readme-rework.md`.

## [2026-05-22] implement | Adopt requests for built-in gates

Accepted the runtime-dependency slice of the positioning research.
`pyproject.toml` now declares `requests>=2.31,<3`; Telegram, Slack, and
GitHub gates use `requests` instead of hand-written `urllib` glue for
JSON HTTP calls and error bodies. README and `src/brr/AGENTS.md` no
longer present zero runtime dependencies as a value or hard constraint;
the new rule is stdlib-preferred with small runtime deps allowed when
they pay for themselves and do not require native compilation. Added
[`decision-runtime-dependencies.md`](decision-runtime-dependencies.md)
and updated current-state kb pages that still described the gates as
stdlib/urllib-only.

## [2026-05-22] fix | KB consistency: env and dependency prose

Reviewed the requests-gates kb updates against the shipped source.
Confirmed `requests` in `pyproject.toml` and the built-in gates, then
trimmed sibling drift in env/fleet pages: brr ships `host`, `worktree`,
and `docker`; `ssh`, `devcontainer`, plugin entry points, and script envs
remain design surface; `WorktreeEnv.finalize` records
`publish_status`/`publish_branch` and `daemon.publish` ships the branch.
The remaining deterministic preflight item is the Research-section
hub-coverage info nudge; no hub was added because the section is a mixed
artifact bucket rather than one coherent subject area.
## [2026-05-22] research | Managed mode reshape of pondering-fleet

Reshaped `kb/notes-pondering-fleet.md` around managed mode as the
new dominant pondering strand. Split managed-brr into two
independent dimensions: managed gates / IO (hosted bots, removes
per-user token setup) and cloud execution (BYO cloud key + later
fully-managed compute, removes the laptop-down blocker). Both
preserve a 1:1 OSS self-hosted path through the existing env
protocol — every cloud-runner candidate is the same `ssh`-shaped
adapter with a different transport. Added a per-platform "what brr
has to add" audit grounded in 2026 platform docs: Fly Machines
(~300ms cold start, REST API, `auto_destroy`), Modal Sandboxes (SDK
+ image build, per-second), Daytona (~90ms from snapshot, SaaS or
self-hosted, AGPL-3.0 as API client is fine), E2B (Debian-only
templates, code-interpreter shape), Codespaces (`gh codespace`
CLI, devcontainer-native), vanilla VMs (SSH bootstrap). Surfaced
the credential-delivery gap explicitly: local docker bind-mounts
`_DOCKER_DEFAULT_CRED_PATHS` (~/.claude / ~/.codex / ~/.gemini /
~/.config/gh / ~/.ssh), which remote sandboxes cannot — three
ranked vehicles (env vars only, platform secret store, one-shot
upload). Recontextualised `brnrd` as a separate further-postponed
operator-agent product distinct from managed-brr (managed-brr ships
first; brnrd consumes brr and brnrd later). Argued for shipping
the first paid tier at launch as adopter-goodwill cover and
solo-OSS maintenance funding. Dropped the stale "promoted" pointer
subsections (older §1, §2) and collapsed the shipped decentralised-
merge §8 to a one-line breadcrumb. Updated re-promotion guide
puts managed gates and the first two cloud-runner adapters (Fly
Machines, Codespaces) at the top, `brr install-service` for
mac+linux as part of the launch shape, brnrd much later. Index
section header and `notes-pondering-fleet.md` description updated
in `kb/index.md` and `kb/subject-fleet-overlays.md`; no code,
README, or design-page changes in this pass.

## [2026-05-22] research | Pondering-fleet follow-up — BYO dispatch, daemon hosting, read-only PaaS

Three small follow-up edits to `kb/notes-pondering-fleet.md`
clarifying the BYO compute dispatch question and the daemon-hosting
story that resolves it:

1. §1.3 (Dimension B) gained a "Who dispatches when the laptop is
   down?" paragraph: the answer is "daemon-on-an-always-on-host",
   not brnrd-spawns-sandboxes. The OSS / BYO / fully-managed
   distinction is sharpened — BYO has brnrd out of the per-task
   path; only fully-managed adds a brnrd-side scheduler.
2. §2.8 (what we're not building) gained the read-only PaaS bullet
   (Heroku / Upsun / Render / Railway / App Platform): wrong shape
   for per-task sandboxes (no per-task API, no BYO OCI image,
   read-only `/app` blocks `git worktree`) but valid as
   daemon-hosting targets — cross-references §4.
3. §4 (cross-platform daemon supervision) expanded around the
   two-layer daemon hosting model (always-on daemon host +
   optional per-task sandbox fan-out) with a four-row deployment
   targets table (free-tier always-on cloud apps, read-only PaaS
   templates, cheap always-on VPS, laptop / home server) ranked by
   setup ease, plus the `deploy/{fly,render,heroku,upsun,vps,
   docker-compose}/` templates folder pointing at a `brr/daemon`
   image variant that drops the runner CLIs to stay small.
4. §7 re-promotion guide updated to the agreed KB shape:
   `subject-managed-mode.md` + `design-managed-gates.md` +
   `plan-managed-gates-launch.md` (GH adapter first, TG fast-follow)
   for Dimension A; `research-cloud-runner-patterns.md` +
   `plan-env-fly-machines.md` for Dimension B; new
   `plan-daemon-deployment-templates.md` for the deployment story;
   explicitly no `design-cloud-runner-protocol.md` since
   `design-env-interface.md` already covers it.

Still capture-only — no design / plan pages drafted yet. The agreed
KB shape is the next promotion target.

## [2026-05-22] plan | Managed-mode KB shape promoted out of pondering

Promoted the managed-mode strand from
`kb/notes-pondering-fleet.md` §1 / §2 into a six-page family that
fresh-context pickup can navigate without rereading the pondering
doc. Optimised for least implementation / maintenance effort,
adoption-index leverage, and ease of BYO setup. New pages:

- `kb/subject-managed-mode.md` — hub. Covers the two-dimension
  split (Dimension A managed gates, Dimension B BYO cloud
  execution) plus the orthogonal daemon-hosting concern.
  References down to design, research, and three plan pages.
- `kb/design-managed-gates.md` — *proposed*. Locks the cloud-gate
  adapter shape on the daemon side and the brnrd inbox-as-service
  REST API on the server side. Specifies the normalised event
  shape (uniform across TG and GH), the long-poll + response loop,
  the pairing flows for both platforms, multi-daemon routing
  policies, failure modes, and operational concerns. Wire format
  is the boundary that lets daemon-side and brnrd-side ship in
  parallel once accepted.
- `kb/research-cloud-runner-patterns.md` — durable reference
  lifted from pondering §2. Cross-adapter patterns (credential
  delivery, repo delivery, result delivery, cold-start budgets,
  network policy) and per-platform briefs for Fly Machines, Modal,
  Daytona, E2B, Codespaces, vanilla VMs, plus the explicit
  not-building list including the read-only PaaS category.
- `kb/plan-managed-gates-launch.md` — two slices: GH App adapter
  first (largest BYO-setup pain relief), TG bot adapter
  fast-follow on the same backend. Backend skeleton in a separate
  `brr-run` repo, OSS reference implementation.
- `kb/plan-env-fly-machines.md` — first BYO cloud-runner adapter,
  shipping as `brr-env-fly-machines` plugin package (not a
  built-in). ~300-400 LOC plugin + image-publish work shared with
  the deployment-templates plan.
- `kb/plan-daemon-deployment-templates.md` — Dockerfile split
  (`brr/daemon` vs `brr/runner`) + the
  `deploy/{fly,render,heroku,upsun,railway,vps,docker-compose}/`
  template folder + a "deploying brr" docs page. Cashes out the
  daemon-hosting story without brnrd holding cloud credentials.

No new design page for cloud-runner protocol —
`design-env-interface.md` already covers it; cloud adapters are
variations of the designed `ssh` env. No `plan-env-codespaces.md`
yet — defer until Fly adapter is shipping or shipped, to de-risk
the second adapter from real first-adapter experience.

KB wiring updates:

- `kb/index.md` Fleet & overlays section header changed to
  "managed mode active; overlays / brnrd paused"; six new entries
  added.
- `kb/subject-fleet-overlays.md` Current State and Reading Map
  expanded to include `subject-managed-mode.md` as a peer hub and
  acknowledge managed mode as the active cross-cutting strand.
- `kb/notes-pondering-fleet.md` §1 and §2 marked PROMOTED with
  breadcrumbs pointing at the new pages; bodies retained as
  provenance.

No code changes; all designs are status:proposed (gates) or pending
acceptance (plans). The brnrd backend prototype is the blocker
for the gates launch plan, sized at ~3 days for the
end-to-end inbox-as-service smoke test.

## [2026-05-22] plan | Managed-mode reshape — work continuity via brnrd

Reframed managed mode around **work continuity, not laptop
continuity** after spotting that the previously-preferred
"always-on host" answer to laptop-down dispatch was a shape
mismatch with the pitch: the pitch sells "your laptop, accessible
from anywhere" — i.e. the user is buying *work continuity*, with
their laptop as default home. The always-on host forces a third
operational surface for a 30%-utilisation case at 100% cost, and
nudges brr toward an infra-deployment story when its wedge is
"my laptop has superpowers."

The replacement answer uses what's already always-on: **brnrd
itself**. The dispatcher gains a failover path — when a user's
daemon is offline AND failover is enabled, brnrd spawns a
per-task ephemeral sandbox (in the user's cloud via BYO token,
or in brnrd's account via paid managed compute), runs the
task, pushes the branch home, posts the response via the gate,
tears down. Three paid surfaces emerge cleanly: managed gates
(free); BYO failover compute (free; user pays own cloud bill);
managed compute (paid usage-based; brnrd's cloud account). All
three ride the same dispatcher and the same cloud-runner
adapters — same code, different callers.

Pricing settled on a three-tier shape mapped to marginal cost:

- **Free dispatcher** — gates + BYO failover. brnrd is a
  public-good for the OSS user; rate caps bound the loss-leader
  exposure. Honest because per-user dispatch cost is
  approximately zero.
- **Usage-based managed compute** — pure pass-through with
  margin (30-50%). Unit economics forced-positive by
  construction; never under water.
- **Team / SLA tier later** — sticky revenue with org-level
  features. Lands once individual usage proves out.

The shape resolves the tension between non-VC-backed + OSS-self-
hostable: pricing aligned with marginal cost means the hosted-vs-
self-host pitch reads as "we run the ops so you don't" rather
than "we charge for the privilege."

KB changes from the reshape:

- `kb/subject-managed-mode.md` — rewritten around work-continuity
  and the three-surface frame. Daemon hosting demoted to a niche
  path for cloud-first users.
- `kb/design-managed-gates.md` → renamed to
  `kb/design-brnrd-protocol.md` and grown with the
  spawn-compute / failover-dispatch endpoint family, cloud-
  credential storage endpoints, and the cloud-token security
  model.
- New: `kb/decision-pricing-shape.md` (status: proposed) —
  three-tier pricing decision with alternatives considered.
- New: `kb/plan-failover-compute.md` — Surfaces B + C
  implementation, four slices (credential storage; dispatcher +
  first server-side caller; managed-compute pool; docs).
- `kb/plan-daemon-deployment-templates.md` — demoted to
  launch-nice-to-have; recontextualised for cloud-first audience.
- `kb/plan-managed-gates-launch.md` — repointed at the renamed
  design page; cross-linked to `plan-failover-compute.md` as
  sister plan sharing the backend skeleton.
- `kb/research-cloud-runner-patterns.md` — added "Caller axis"
  section formalising that each adapter is consumed by laptop
  daemon AND brnrd server-side, with same code and small
  per-caller deltas (token source, repo delivery, response
  delivery, failure salvage, cost ceiling).
- `kb/notes-pondering-fleet.md` — added reframe breadcrumbs to
  §1 and §4 noting the demotion of the always-on-host answer;
  retained body as provenance; updated §7 re-promotion guide to
  reference the renamed design page.
- `kb/index.md` and `kb/subject-fleet-overlays.md` — Fleet &
  overlays section reflects the new page family and the rename.

No code changes; designs are status:proposed pending acceptance
before backend implementation can start. The brnrd backend
prototype remains the immediate blocker. brnrd unaffected — the
work-continuity frame makes the boundary even clearer: managed
mode keeps individual task work flowing; brnrd thinks at the
fleet / planning level.

## [2026-05-25] plan | Managed-mode reshape pass 2 — drop BYO from launch, retire brnrd as a name, add dashboard + monorepo decisions

Second reshape pass on the managed-mode KB family, driven by a
deeper look at what brnrd actually has to do at launch vs what's
defensible to ship. Surfaces shifted:

- **BYO compute (Surface B) dropped from launch.** The wire
  protocol still supports it (preserved as a "designed,
  deferred" sketch in `design-brnrd-protocol.md`), but the
  per-platform credential storage UI, per-platform onboarding
  docs, dispatcher branching, and partial-support-matrix
  maintenance burden didn't justify shipping it day one for
  the ~5% of launch users who'd care. Add-back is small when
  usage justifies. Daemon-side cloud-runner adapters (laptop
  fans out to user's cloud via a `brr-env-*` plugin) remain
  independent of managed mode entirely.
- **One product, one name.** brnrd as a separate fleet-operator
  brand was retired in this pass; the hosted product collapses
  into a single name (the dashboard becomes "the &lt;product&gt;
  dashboard"). At this pass we picked `brr.run` as the kept
  name. **Superseded by the 2026-05-25 pass-3 entry below**,
  which flipped the kept name to `brnrd` (hosted at
  `brnrd.dev`) on cost + brand-asset grounds. The collapse
  itself stands; only which name survives changed.
  The previously-proposed `subject-brnrd.md` / `plan-brnrd-mvp.md`
  family folds into the managed-mode hub + a new
  `plan-brnrd-dashboard-mvp.md`.
- **Multi-project routing protocol added.** One managed bot
  per platform serves all of a user's projects via chat-binding
  + per-message prefix override (TG/Slack/Discord) or
  repo-binding (GH). Spec in protocol design; UX integration in
  `plan-managed-gates-launch.md` Slice 2.
- **Permission-prompt API added.** Cost-transparency before each
  failover spawn: est cost, est runtime, current-month usage,
  two action buttons (Approve / Queue), optional "Never ask
  under $X" on first prompt. Mode defaults to `ask`. Spec in
  protocol design; integration in `plan-managed-gates-launch.md`
  Slice 3.
- **AI-credential vault supports both shapes on one endpoint.**
  API-key (`--key sk-ant-...`) and credential-directory tarball
  (`--dir ~/.claude`) — both flow into the same encrypted store.
  Subscription-auth users (Claude Pro, Codex Plus, Gemini OAuth)
  are first-class on the server-side failover path via the
  dir-tarball shape, matching the local docker env's mounted-dir
  UX.
- **Free-tier failover spawn cap revised down: 100/month**
  (was 200). Framed as a fallback feature, not a free
  continuous-execution SaaS. Math at ~$0.28/user/month
  worst-case cloud cost is sustainable with a small percentage
  of paying users on top.
- **Data minimization principle promoted to load-bearing.**
  brnrd is a thin dispatcher + a credential vault; user
  content (prompts, code, responses, conversation history, repo
  state) lives on the daemon side and is never mirrored to
  brnrd. Event bodies dropped after dispatch; response bodies
  pass through without storage; AI credentials encrypted at
  rest with per-account envelope keys; audit log metadata-only.
  Trust signal on the pricing page: "we don't have your code."
- **Monorepo structure decided.** `src/brr/` (daemon today) +
  `src/brnrd/` (backend) + `src/brnrd_web/` (dashboard) +
  `src/brr_env_*/` (vendored plugins, split out when they
  mature) in one repo. Shared kb, shared CI, separate pip
  install surfaces via optional dependencies.
- **Gates vs connectors split named.** Gates are per-project /
  inbound (existing shape); connectors are per-account /
  outbound / proactive (for the future agentic-secretary
  layer). No connectors ship at launch; the decision page
  exists so the future agentic-mode upgrade doesn't retrofit
  the gate API.
- **Upsun confirmed as prototype hosting environment.**
  Read-only-app-container constraints handled via the
  build-vs-deploy split, declared writable mounts, postgres
  add-on, Upsun-secret-store for pool tokens. The daemon
  template's Upsun shape shares patterns with the backend
  template.

KB changes from this reshape:

- `kb/design-brnrd-protocol.md` — reshaped: BYO platform-
  tokens dropped from launch (preserved as "designed,
  deferred" section); AI-credential vault added (api-key +
  dir-tarball shapes on one endpoint); multi-project routing
  protocol added (project_id resolution per platform,
  chat-binding + prefix override grammar); permission-prompt
  API added (`/v1/internal/prompts` + gate-callback webhooks);
  data minimization principle promoted to a load-bearing
  section governing every endpoint; Upsun deployment notes
  added.
- `kb/plan-failover-compute.md` — rewritten: BYO scope dropped
  entirely; refocused on AI-credential vault + brnrd-owned
  Fly pool + permission-gate API + Upsun backend deployment.
  Four slices (vault + policy; dispatcher + prompts; pool +
  sandbox image; audit + docs).
- `kb/subject-managed-mode.md` — reshaped: two surfaces (free
  dispatcher; paid managed compute) with BYO as deferred;
  brnrd absorbed as "brnrd as fleet manager" angle of the
  same product; multi-project routing + permission gating +
  dashboard sections; data-minimization callout; "where the
  code lives" pointer at the monorepo decision.
- `kb/decision-pricing-shape.md` — updated: dropped launch BYO
  tier (collapsed to two-tier free dispatcher inc. 100 managed-
  compute spawns/month + usage-based over cap); revised free-
  tier spawn cap 200 → 100; data-minimization trust signal
  promoted; "we charge for ops, not for AI usage" framing
  added; self-hosted brnrd framed as parallel path.
- New: `kb/decision-connectors-layering.md` (status: proposed) —
  gates vs connectors split; agentic-mode upgrade path frame.
- New: `kb/decision-monorepo-structure.md` (status: proposed) —
  monorepo layout + plugin-split-out criterion + alternatives.
- New: `kb/plan-brnrd-dashboard-mvp.md` — seven views,
  HTMX-first, four slices (bootstrap + login; config surfaces;
  observability surfaces; polish).
- `kb/plan-managed-gates-launch.md` — added multi-project
  routing UX (chat / repo binding, `/connect`, `/project`,
  `@<name>` command grammar) and permission-prompt API +
  gate-side integration as Slice 3. Backend repo replaced with
  `src/brnrd/` per the monorepo decision.
- `kb/research-cloud-runner-patterns.md` — refreshed: caller-
  axis section now reflects that only Fly Machines wires up
  server-side at launch (BYO server-side deferred); Pattern A
  grew a "server-side caller specifics" subsection covering the
  AI-credential vault's two payload shapes and per-platform
  injection.
- `kb/plan-daemon-deployment-templates.md` — Upsun entry
  cross-linked to the brnrd backend Upsun deployment
  (shared read-only-container shape; should be authored
  together).
- `kb/notes-pondering-fleet.md` — appended second 2026-05-25
  reframe breadcrumb to §1 capturing all of the shifts above
  and pointing at the new + reshaped pages.
- `kb/index.md` — Fleet & overlays section updated for the new
  pages (`decision-connectors-layering.md`,
  `decision-monorepo-structure.md`,
  `plan-brnrd-dashboard-mvp.md`) and reshaped descriptions
  for the existing managed-mode pages.

No code changes; designs remain status:proposed pending
acceptance. Next blocker is the brnrd backend prototype
(unchanged from the previous pass), now scoped against the
reshaped protocol + the monorepo layout. Implementation can
start once the design + pricing pages are accepted.

## [2026-05-25] plan | Managed-mode reshape pass 3 — brnrd kept as the name; cross-gate conversation context via metadata graph

Third reshape pass on the managed-mode KB family, two changes:

- **brnrd kept as the canonical hosted-product name; domain
  `brnrd.dev`.** Pass 2 had picked `brr.run` as the kept name
  after collapsing the two-name proposal. Domain pricing
  surfaced post-pass-2 (`brr.run` runs ~$120/yr as a premium
  domain; `brnrd.dev` ~$15/yr), plus the brand-asset value of
  the `brr → brnrd → ⟍brr` reflection-palindrome animation,
  plus the sibling-naming fit with "brr" itself — net flip:
  `brnrd` is the kept name. The pass-2 collapse logic
  (one product, one name) stands; only which name survives
  changed.
- **Cross-gate conversation context via metadata-only graph +
  on-demand fetch.** Pass 2 left conversation history as
  "lives on the daemon; gone when daemon is offline," which
  loses cross-gate continuity (a conversation that spans TG
  and a GH PR would fragment on failover). Pass 3 closes the
  gap without brnrd holding conversation contents:
  - **Metadata graph on brnrd**: `event_metadata(event_id,
    gate, source_channel, project_id, conversation_id,
    branch_name, received_at)` — no body, no preview, no
    participant names; ~200 bytes per row; 30-day TTL on the
    live graph; aggregated count-only summaries past that.
  - **Conversation_id sources**: a `Brnrd-Conversation-Id`
    git commit trailer the daemon writes on every commit
    (source of truth — brnrd can re-derive the linkage by
    walking git log on any branch), plus the conversation_id
    field on the daemon's response POST (keeps the metadata
    index current as a cache).
  - **Three-source spawn-context assembly**: originating event
    payload (already in dispatch memory) + gate-side history
    fetch from the platform's own API + git remote replay.
    Cross-gate continuity adds a fourth: query the metadata
    graph for other events in the same conversation_id and
    fetch their platform-side context on demand.
  - **One named concession — Telegram per-chat ring buffer**
    (50 messages × 72 hours, encrypted at rest, dropped on
    `/disconnect`, every read in the audit log). TG's Bot API
    has no retroactive `getChatHistory`; the ring buffer is
    the minimum viable held data to make failover and
    dashboard rendering work on TG without forcing users to
    push history into their own infra. Slack / Discord don't
    need a ring buffer — their APIs expose history natively.
  - **Dashboard rendering split**: when daemon online, the
    dashboard proxies live (no brnrd-held copy); when offline,
    contents are rendered from gate-side history + git log +
    ring buffer (TG only), marked clearly in the UI as "live
    from &lt;platform&gt;; daemon offline."

KB changes from this pass:

- **Renames** (preserving content; rename history captured in
  each file's lineage and the design's preamble):
  - `kb/design-brr-run-protocol.md` →
    `kb/design-brnrd-protocol.md`
  - `kb/plan-brr-run-dashboard-mvp.md` →
    `kb/plan-brnrd-dashboard-mvp.md`
  - All `brr.run` → `brnrd` text replacements across the
    kb (the API surface, endpoint paths, and protocol
    contract are unchanged; only the product name changed).
  - All `brr_run` / `brr_run_web` → `brnrd` / `brnrd_web`
    path replacements (sub-package layout in
    `decision-monorepo-structure.md`).
- `kb/design-brnrd-protocol.md` — added
  **"Conversation context for failover and dashboard"** as a
  top-level section between Failover dispatch and Multi-daemon
  routing: schema for the event_metadata graph,
  conversation_id sources (git trailer + response POST) and
  inference rules, three-source spawn-context assembly,
  per-gate history-fetch mechanics, Telegram ring buffer spec,
  dashboard rendering split, `GET /v1/internal/context/
  {event_id}` endpoint. Also: **"What we DO hold"** subsection
  promoted inside Data minimization, with every persistent
  surface listed (scope + TTL + reason). Audit-log section now
  records context-fetch reads.
- New: `kb/plan-conversation-id-propagation.md` — small
  daemon-side enabler plan (~80 LOC): `git commit --trailer
  "Brnrd-Conversation-Id: <ulid>"` everywhere brr commits, plus
  `conversation_id` field on the response POST. Three slices
  (trailer stamping; response field; docs). Gates the metadata
  graph from being meaningful in practice.
- `kb/subject-managed-mode.md` — added a
  **"Conversation context"** section after Data minimization
  summarising the three-source approach + ring buffer; updated
  the "brnrd as the product" section to flip the
  pass-2 narrative ("brnrd retired" → "brnrd kept"), citing
  cost + brand-asset + sibling-naming reasoning.
- `kb/decision-pricing-shape.md` — Trust signals section
  expanded with the new **"What we DO hold, named and
  bounded"** signal (full table reference) so the trust
  promise stays honest about the named concessions
  (conversation graph, TG ring buffer). Audit-log mention
  picks up context-fetch reads.
- `kb/notes-pondering-fleet.md` — appended the **third
  2026-05-25 reframe breadcrumb** to §1 capturing both the
  brnrd-kept name flip and the cross-gate context machinery,
  pointing at the new + updated pages. Updated the
  pass-2 "brnrd retired as a name" sub-bullet to mark itself
  superseded by the breadcrumb below.
- `kb/subject-fleet-overlays.md` — reframed the brnrd
  treatment: "fleet-operator axis collapsed into the
  managed-mode hub on 2026-05-25 (one platform, one name —
  `brnrd`, hosted at `brnrd.dev`)" — was "retired as a
  separate name on 2026-05-25."
- `kb/decision-monorepo-structure.md` — sub-package paths
  renamed in-place (`src/brr_run/` → `src/brnrd/`,
  `src/brr_run_web/` → `src/brnrd_web/`); lineage entry
  appended explaining the rename.
- `kb/plan-managed-gates-launch.md` — lineage entry appended
  for the pass-3 references update.
- `kb/research-positioning-and-runtime-deps-2026-05-21.md` —
  "no public surface" section updated from `brr.run` to
  `brnrd.dev`; "Name" subsection rewritten with the
  sibling-product naming rationale (brnrd as a brand asset,
  domain cost rationale).
- `kb/index.md` — Fleet & overlays section updated for the
  pass-3 reshape: hub description flipped, design page
  description gained the conversation-context bullet, new
  `plan-conversation-id-propagation.md` listed.

No code changes; designs remain status:proposed pending
acceptance. Next blocker is still the brnrd backend prototype,
now scoped against the conversation-context machinery as well.
The daemon-side conversation_id propagation is the natural
first code slice — small, no schema migration needed, harmless
metadata for OSS users — and it gates everything cross-gate.

## 2026-05-25 — pass 4: billing, envs unification, plugin packaging, CLI shape

Triggered by user feedback after pass 3 surfaced five concrete
productisation gaps. Each shifted a piece of the managed-mode
architecture; the launch shape itself didn't move, but the
mechanics under it got considerably more concrete.

### Trigger

User flagged five things:

1. **Billing**: legal entity ready in France (HugiMuni SAS +
   Qonto bank); proposed a credits-purchase model (like the
   OpenAI API); asked whether this was too much for MVP or
   atypical enough to scare users away.
2. **Plugin packaging**: didn't like `pip install
   brr-env-fly-machines` as a separate command — felt
   over-engineered; suggested it should be a plugin /
   component of `brr[backend]`.
3. **Envs vs cloud-runs architectural contradiction**: noted
   that cloud runs (as previously framed) contradicted the
   env shape; asked for "a unification of sorts."
4. **CLI verb naming**: disliked `brr accounts`; offered
   `brr config` / `brr service` as alternatives but
   acknowledged they weren't ideal either.
5. **Cross-platform daemoning**: reaffirmed systemd-first
   tracked at issue #29; invited counter-proposals that fit
   the new architecture better.

Plus a strategic question on the `brr connect-to-brnrd`
command's URL arg: how easy should self-hosting be, given the
risk of "leaking" users to their own deployment?

### Net direction

- **Credits-wallet billing adopted.** Yes to credits — they're
  industry-standard for usage-metered services (OpenAI,
  Anthropic, AWS), match the "we charge for ops, not for AI
  usage" pricing framing, and align with the data-minimization
  pitch (no card-on-file by default = no recurring identity-
  mapping). MVP effort is ~1 week (Stripe Checkout for top-ups,
  one ledger table, debit-at-finalize hook). Drafted
  `design-billing.md` covering the full mechanics; updated
  `decision-pricing-shape.md` to reflect the wallet model;
  added "no card-on-file by default" as the fourth trust
  signal.
- **Plugin packaging collapsed to extras.** Dropped
  `brr-env-fly-machines` as a separate pypi name. First-party
  cloud envs live at `src/brr/envs/<name>/` and ship gated by
  `brr[<name>]` pip extras. Third-party envs still use the
  `brr.envs` entry-point mechanism — that path stays unchanged
  per [`design-env-interface.md`](design-env-interface.md), so
  external plugin authors aren't disadvantaged. Wins:
  single-version surface, no plugin/core version-skew bugs,
  simpler discovery. Reshaped
  `decision-monorepo-structure.md`; updated `plan-env-fly-
  machines.md` to reflect the new env location +
  `brr[fly]` extra; added a "first-party (extras) vs
  third-party (entry points)" subsection to
  `design-env-interface.md`.
- **Cloud envs unification — "cloud runs ARE envs."** Dropped
  the separate "cloud-runner adapter" framing.
  `research-cloud-runner-patterns.md` renamed to
  `research-cloud-envs.md` and reframed: cloud envs implement
  the existing `EnvBackend` Protocol; the brnrd backend
  invokes the same env class the daemon would use, after a
  daemon-equivalent bootstrap (clone repo with per-spawn GH
  App token, materialise AI creds, construct a `RunContext`).
  Added a "brnrd server-side caller" subsection to
  `design-env-interface.md` documenting the bootstrap +
  invocation flow. `design-brnrd-protocol.md` updated to
  drop the cloud-runner-adapter framing in its spawn step +
  BYO-deferred section. One env implementation, two
  deployment targets, no second protocol.
- **CLI shape decision drafted.** New page
  `decision-cli-shape.md` outlines a 6-verb noun-first
  taxonomy: `init`, `run`, `daemon` (replaces top-level `up` /
  `down`), `gate` (replaces top-level `auth` / `bind` /
  `setup`), `brnrd` (new namespace for hosted-service
  management — `connect`, `creds`, `policy`, `topup`,
  `balance`, `projects`, ...), `config` (new namespace for
  parameter introspection — `list`, `get`, `set`, `doc`).
  Compared `brr brnrd` vs `brr remote` / `brr service` /
  `brr cloud` / `brr config-remote`; locked in `brr brnrd`
  for self-documentation. Renamed all `brr accounts <verb>`
  references in `plan-failover-compute.md` +
  `plan-managed-gates-launch.md` to the new `brr brnrd`
  verbs (`brr brnrd creds add|list|remove`,
  `brr brnrd policy set|get`, `brr brnrd audit`).
- **Cross-platform daemoning.** Added a one-line cross-
  reference to [issue #29](https://github.com/Gurio/brr/issues/29)
  in `plan-daemon-deployment-templates.md`. Managed mode
  reduces the urgency (failover compute covers gaps); the
  systemd-first track at #29 proceeds independently. No new
  architectural commitment in the kb; deferred to the issue.
- **Self-hosting friction.** Decision: don't add any. `brr
  brnrd connect <url>` accepts any URL, defaulting to
  `https://brnrd.dev`. Reasoning: the friction of running
  your own brnrd is deployment itself; the CLI shouldn't
  add hoops. Power users self-host anyway and are likely OSS
  contributors; making the path real (not symbolic) is what
  backs the "we don't have your code" trust pitch. Captured
  in `decision-cli-shape.md` § Self-hosting.

### Pages changed in this pass

- `kb/design-billing.md` — **new**. Wallet model, top-up flow
  (Stripe Checkout, no card on file by default), debit
  mechanics (at spawn-finalize, USD → credits conversion),
  zero-balance UX (enqueue + gate notify, optional auto-topup),
  pro-rata refund policy, free-tier monthly credit grant,
  audit log entries per wallet operation, Stripe + HugiMuni
  SAS + Qonto + Stripe Tax integration shape, alternatives
  considered (subscription, card-on-file PAYG, invoicing,
  no-billing). Status: proposed.
- `kb/decision-pricing-shape.md` — wallet model adopted on the
  pricing tier table; "no card-on-file by default" added as
  the fourth trust signal; lineage entry appended for the
  pass-4 reshape pointing at `design-billing.md`.
- `kb/decision-cli-shape.md` — **new**. Six-verb noun-first
  taxonomy; alternatives table for `brr brnrd` vs `brr remote`
  / `brr service` / `brr cloud` / `brr config-remote`;
  intentionally-not-added verbs; migration note (no users
  to migrate); open questions on aliases, completions,
  daemon-logs sibling, JSON output mode. Status: proposed.
- `kb/decision-monorepo-structure.md` — reshaped to
  single-package + optional-extras model;
  `src/brr/envs/<name>/` location for first-party cloud envs;
  `brr.envs` entry-point mechanism preserved for third-party
  envs; Alt 2 (multi-pypi-in-monorepo) added to alternatives;
  lineage entry appended for the pass-4 reshape.
- `kb/research-cloud-envs.md` — **renamed** from
  `research-cloud-runner-patterns.md`. TL;DR + "Caller axis"
  reframed: cloud runs ARE envs (no separate concept);
  caller-axis table updated for the new env-class invocation
  + daemon-equivalent bootstrap pattern; reference to
  `design-billing.md` for the cost ceiling enforcement.
- `kb/design-env-interface.md` — "Python envs — first-party
  (extras) and third-party (entry points)" subsection
  rewritten to distinguish the two paths;
  **new "brnrd server-side caller" subsection** detailing the
  daemon-equivalent bootstrap before env invocation.
- `kb/plan-env-fly-machines.md` — reshaped: env now lives at
  `src/brr/envs/fly_machines/` inside the brr package, gated
  by the `brr[fly]` extra; same env class invoked from both
  daemon and brnrd server-side; lineage entry appended.
- `kb/plan-failover-compute.md` — all `brr accounts <verb>`
  references renamed to `brr brnrd <verb>` per
  `decision-cli-shape.md`; "Implementation location" row for
  CLI verbs renamed (`src/brr/cli/accounts.py` →
  `src/brr/cli/brnrd.py`); cloud-runner-adapter framing
  dropped in the BYO-deferred mention; user flow now starts
  with `brr brnrd connect` + `brr brnrd topup 20`.
- `kb/plan-managed-gates-launch.md` — `brr accounts` →
  `brr brnrd` rename applied throughout.
- `kb/plan-daemon-deployment-templates.md` — added a
  cross-reference paragraph to
  [issue #29](https://github.com/Gurio/brr/issues/29) for
  cross-platform daemoning; framed as a parallel-strand
  decoupled by managed mode's failover coverage.
- `kb/subject-managed-mode.md` — Surface B description now
  references the envs unification (same env class invoked from
  daemon + brnrd server-side); CLI examples updated to use
  `brr brnrd <subcommand>` verbs; new "Billing — credit wallet,
  no card on file by default" subsection under Surface B with
  pointer to `design-billing.md`; "Where the code lives"
  section updated to reference the extras + first-party-env
  approach; "Out of scope" Stripe-integration bullet rewritten
  to say "card-on-file subscriptions deferred to v-next"
  (since one-shot Stripe Checkout now ships at launch); Read
  next list expanded for the new pages.
- `kb/design-brnrd-protocol.md` — spawn-finalize endpoint
  description now mentions the wallet debit trigger pointing
  at `design-billing.md`; "Out of scope" updated to defer
  wallet/Stripe mechanics to `design-billing.md`; failover-
  spawn step 6 description rewritten to use the env-class
  invocation language ("daemon-equivalent bootstrap +
  `envs.get_env('fly_machines')`") instead of "cloud-runner
  adapter"; BYO-deferred section drops the cloud-runner-
  adapter framing; spawn-finalize step 8 updated to walk
  through the USD-to-credits debit; lineage entry appended
  for the pass-4 reshape.
- `kb/notes-pondering-fleet.md` — appended the **fourth
  2026-05-25 reframe breadcrumb** to §1 capturing all five
  shifts (billing, plugin packaging, envs unification, CLI
  shape, daemoning), pointing at the new + updated pages.
- `kb/index.md` — pricing description updated for the wallet
  model + free-credit grant framing; new `design-billing.md`
  entry; new `decision-cli-shape.md` entry; monorepo
  description rewritten for the single-package + extras
  approach; cloud envs research description updated for the
  rename + reframe; Fly Machines plan description rewritten
  to reflect the new env location.

No code changes this pass either; designs and plans remain
status:proposed. Three new pages (`design-billing.md`,
`decision-cli-shape.md`), one rename + reshape
(`research-cloud-runner-patterns.md` →
`research-cloud-envs.md`), eight existing pages updated.

Next blocker is still the brnrd backend prototype. The
daemon-side conversation_id propagation
(`plan-conversation-id-propagation.md`) and the CLI reshape
(`decision-cli-shape.md`) are both small enough that they
could be the first two code slices in parallel with
`src/brnrd/` scaffolding.

## 2026-05-25 — pass 4 follow-up: connect-flow shape + Stripe EU specifics

Two narrow updates after the pass-4 commit landed and the user
read through:

### Trigger

User asked two clarifying questions:

1. Should `brr brnrd connect` auto-setup gates, given gate
   pairing is still a "separate thing" conceptually?
   ("we should autosetup gates when `brr brnrd connect` i guess,
    or what is the shape you're proposing")
2. Stripe handles European users natively, right? Wanted
   confirmation + specifics, never used Stripe before.

Both answered + the answers formalised into the docs so the
next reader doesn't have to dig into chat history.

### Net direction

- **`brr brnrd connect` is a three-layer smart bootstrap** —
  not just account-pair. Layer 1: account-pair (one-time per
  machine). Layer 2: project-create (per repo, default name
  from repo basename). Layer 3: gate-pair via mechanical
  detectors (GH detector fires when `git remote get-url
  origin` matches a GH URL; TG detector fires if legacy
  `.brr/config` has TG settings; each detector also
  invocable as a standalone `brr brnrd pair <gate>`).
  Idempotent — each layer skipped if already satisfied.
  Non-interactive flags (`--account-only`, `--no-auto-pair`,
  `--pair`, `--yes`, `--project`) for scripts. Walkthrough
  invents no new verbs; just sequences existing ones.
- **Stripe EU support is turnkey** — but with five things to
  enable explicitly that most independent vendors miss: SCA
  (handled by Checkout automatically, no code), Stripe Tax
  add-on (0.5%/txn, mandatory for compliant VAT calculation),
  OSS scheme registration via DGFiP (not optional for
  cross-EU digital services), EU-local payment methods (SEPA,
  iDEAL, Bancontact, EPS, Giropay, P24, Apple/Google Pay —
  toggleable in Dashboard, big conversion wins), and the
  TVA intracommunautaire on every B2B invoice (Stripe
  inserts when configured).
- **Headline managed-compute margin lands at 27-47% net of
  Stripe + Stripe Tax** (down from the 30-50% gross target),
  with the worked-example breakdown spelled out for a French
  card user (3.25% overhead) and a German SEPA user (1.3%
  overhead).

### Pages changed in this follow-up

- `kb/decision-cli-shape.md` — "Self-hosting and
  `brr brnrd connect <url>`" section replaced with
  **"`brr brnrd connect` — three-layer smart bootstrap"**,
  detailing layer-by-layer behaviour + detection rules +
  flags. Self-hosting policy moved into a final subsection
  ("Self-hosting policy"). Lineage entry appended.
- `kb/design-brnrd-protocol.md` — "Pairing flow" section
  reorganised: new
  **"`brr brnrd connect` — three-layer smart bootstrap"**
  top-level subsection describing the protocol-side endpoints
  for each layer (Layer 1: `POST /v1/accounts/pair` +
  `GET /v1/accounts/pair/{pair_code}`; Layer 2:
  `POST /v1/accounts/projects`; Layer 3:
  `POST /v1/accounts/projects/{project_id}/gates/{kind}` for
  auto-bind when an App is already installed). New endpoint
  table for the connect-flow endpoints. Telegram + GitHub
  subsections retitled as "(Layer 3 detector — explicit
  pair)" and "(Layer 3 detector — install + auto-bind, or
  explicit pair)" — clarifying they're the same code paths
  the walkthrough invokes. Lineage entry appended.
- `kb/design-billing.md` — Stripe integration section expanded
  into four subsections (legal + payouts, payment methods
  enabled at launch, SCA, VAT compliance, tax invoicing) with
  an explicit fee table + worked examples. SEPA / iDEAL /
  Bancontact / EPS / Giropay / P24 / Apple/Google Pay listed
  as day-one toggles. OSS scheme registration via DGFiP called
  out as not-optional. TVA intracommunautaire on B2B invoices
  documented. Margin implication of Stripe overhead
  (~27-47% net) spelled out. Lineage entry appended.

Three pages updated; one short follow-up commit on top of the
pass-4 commit. No new pages.

## 2026-05-25 — pass 4 follow-up, second wave: kb command + cross-platform daemoning + three-scope config

Three substantive additions to the managed-mode launch shape,
all triggered by a single user message reviewing the pass-4
result.

### Trigger

User raised three concerns in one pass:

1. **"We need them for mac and linux, ideally natively
   installable."** Daemons should survive reboot without
   `tmux` rituals; the existing systemd-only track at #29
   needs explicit macOS coverage and a concrete CLI shape.
2. **"Better KB management for non-brr operated agents …
   maybe we could come up with a command for kb."** Tied
   directly to [#41](https://github.com/Gurio/brr/issues/41).
   The kb is half the value prop but has no first-class read
   surface from the CLI; non-brr agents (Cursor, Codex CLI,
   Claude Code) have to walk pages by hand to know state.
3. **"Sync the local settings file with the remote runs …
   nice way of seeing all the possible config properties
   visible."** `.brr/config` is gitignored, so brnrd-side
   spawns can't read project preferences (Docker image,
   runner choice). Teammates can't share project-level
   settings either. And the "what knobs exist?" gap is still
   unaddressed.

### Net direction

- **Seventh top-level CLI verb `brr kb`** with six sub-verbs
  (`status` / `pages [filters]` / `proposed` / `log` / `check`
  / `doc`), all with `--json` mode. Same surface for users
  (who get "what needs my review?") and non-brr agents (who
  get structured kb health for orientation and post-edit
  validation). AGENTS.md → "Health checks" gets a forward
  pointer; the manual scan stays in place until the verb
  ships.
- **`brr daemon install | uninstall | logs`** sub-verbs,
  cross-platform (Linux systemd user unit, macOS launchd
  LaunchAgent). Per-user, no sudo (except optional one-time
  `loginctl enable-linger`). Falls back to today's foreground
  supervisor when not installed. Windows deferred.
- **Three-scope config model**: `project` (`brr.toml`
  committed at repo root), `local` (`.brr/config` gitignored),
  `account` (brnrd-side, via new `/v1/accounts/settings`
  endpoint family). TOML format both files. Merge precedence
  `local > project > account > default`. Per-key schema
  declares scope. `brr config template | validate` rounds out
  the existing list/get/set/doc verbs.
- **brnrd-side spawn bootstrap reads `brr.toml`** from the
  cloned repo as part of the daemon-equivalent bootstrap step
  in failover dispatch. Project preferences (Docker image,
  runner choice, env default) flow from the repo to brnrd-side
  spawns automatically — no protocol push needed; the repo IS
  the message. Private docker images flagged as a launch-blocker
  for the spawn path (clear gate-side error message); generic
  credential-vault extension (registry creds alongside AI
  creds) tracked as an open question, deferred to v-next.
- **BYO cloud env vs managed compute clarified** in
  `subject-managed-mode.md` as orthogonal coexisting paths:
  daemon-side BYO env (your cloud account, fires every task)
  vs brnrd-side managed compute (brnrd's cloud account, fires
  only when daemon offline and policy allows). Same env class
  serves both callers per the envs unification.

### Pages added / modified

New pages:

- `kb/plan-laptop-daemoning.md` — cross-platform daemoning plan
  (Linux systemd user units + macOS launchd LaunchAgents; `brr
  daemon install | uninstall | logs` mechanics; per-project
  unit naming via `--name`; out-of-scope: Windows, system-wide
  install, non-systemd Linux distros). Cross-refs #29.
- `kb/plan-kb-subcommand.md` — `brr kb` subcommand plan
  (six sub-verbs, what each verb checks, AGENTS.md integration,
  implementation sketch in `src/brr/kb/`). Cross-refs #41.
- `kb/design-config-layout.md` — three-scope config model
  (project / local / account), TOML format, per-key schema,
  scope assignments table, brnrd-side spawn bootstrap reading
  `brr.toml`, private-docker-image open question.

Modified:

- `kb/decision-cli-shape.md` — Six-verb shape promoted to
  seven (added `brr kb`); `brr daemon` gets `install` /
  `uninstall` / `logs` sub-verbs; `brr config` gets
  `template` / `validate` sub-verbs; `brr config list`
  description rewritten around the three-scope model; `--json`
  promoted from "open question" to "default-on across the verb
  tree"; "Differences" table updated; "Open questions" updated;
  "Read next" expanded; lineage entry appended.
- `kb/design-brnrd-protocol.md` — new "Account-scope settings
  endpoints" subsection (`GET / PUT / DELETE /v1/accounts/
  settings[/{key}]`); failover-dispatch step 6 (spawn path)
  rewritten to spell out the daemon-equivalent bootstrap
  reading `brr.toml` from the cloned repo + layering with
  account-scope settings; private docker image flagged as a
  launch-blocker with a clear error path; lineage entry
  appended; "Read next" expanded.
- `kb/subject-managed-mode.md` — new "BYO cloud env vs
  managed compute" subsection with a comparison table
  spelling out caller / cloud account / when it fires /
  payment model; "Daemon hosting" table updated to reference
  `brr daemon install` instead of the placeholder
  `brr install-service`; "Where the code lives" expanded with
  `src/brr/daemon_install/`, `src/brr/kb/`, `src/brr/config/`,
  `brr.toml`; "Boundary → In scope" updated to list the new
  verbs and the three-scope config model; "Read next"
  expanded with three new entries (laptop daemoning, kb
  subcommand, config layout).
- `src/brr/AGENTS.md` — Revision bumped to 2026-05-25.
  Knowledge base → Health checks gets a final paragraph
  pointing forward to `brr kb status` and `brr kb check` once
  the verb ships (per #41); the manual scan stays as the
  current contract since the verb isn't shipped yet. Added a
  bullet to the scan list for "pages marked `proposed, not
  yet accepted` that have been sitting for a while."
- `kb/index.md` — CLI shape description rewritten for seven
  verbs + new sub-verbs; new entries for
  `plan-laptop-daemoning.md`, `design-config-layout.md`,
  `plan-kb-subcommand.md`.
- `kb/notes-pondering-fleet.md` — appended "second wave"
  paragraph to the pass-4 follow-up breadcrumb in §1.

Three new pages, six updates. No code changes this pass; all
designs and plans remain `Status: proposed`. The implementation
order suggested by the page set: `brr.toml` + three-scope
config (because it unlocks brnrd-side preference reading);
then `brr kb` (because it's the lowest-coupling slice with the
highest agent-experience leverage); then `brr daemon install`
(can ship anytime, no upstream coupling).

## [2026-05-25] kb | Managed-mode pass 4 follow-up — third wave

Pricing reframe + credential vault generalisation, driven by
two pieces of user feedback at the same time:

> "I would actually want the [private] images and also the
>  credential dir mounting (stored encrypted as we discussed).
>  maybe I don't get it right, but supporting only public
>  images makes sense I guess."

> "the current pricing won't make this project successful if
>  you know what I mean. We should avoid rent-seeking, but we
>  need to reframe it slightly, to both make more coherent, and
>  more sustainable, yet still ideally friendly."

### What changed

- **Pricing reframe**. The "free dispatcher + paid managed
  compute (credits)" shape rejected as self-defeating: active
  users wouldn't hit the compute cap; casual users wouldn't hit
  anything; nobody would pay; project would starve. Adopted
  **subscription for the platform + metered credits for
  compute**:
  - **Free** (1 project, 100 events/month, 5 spawn-credits,
    basic dashboard, 7-day audit, community support).
  - **Brnrd Plus — $9/month** (up to 10 projects, 10K
    events/month, 500 spawn-credits included, full dashboard,
    90-day audit, email support).
  - **Compute overage** on either tier: existing credit wallet,
    $0.01/credit, one-shot Stripe Checkout top-ups, no
    card-on-file except opt-in auto-topup.
  - **Self-hosted brnrd** stays always-free with full feature
    parity.
  Plus's gating feature is **multi-project routing** — anyone
  running brr for more than one repo is the realistic "this is
  real for me" line. Plus also unlocks the full dashboard (cost
  charts, cross-project view, permission-prompt customisation,
  project bindings UI) and 90-day audit retention. Event
  overages on either tier are soft-throttle + notify, not
  metered (dispatcher is cheap to operate; metered event
  charges feel punitive).
- **Plus subscription billing leg** added alongside the
  existing credit wallet. Stripe recurring subscription
  (monthly + annual variants), Stripe Customer Portal for
  self-service card / invoice / cancel, prorated upgrade
  mid-cycle, cancel-at-period-end downgrade, dunning grace,
  monthly Plus credit grant (500 credits, expires
  end-of-month). EU compliance machinery from pass-4 first
  wave (Stripe France, HugiMuni SAS, Qonto, Stripe Tax, OSS
  scheme, SCA via Checkout) applies to the subscription
  product identically. New `/v1/accounts/subscription` endpoint
  family on the brnrd protocol; subscription state mirrored to
  account-scope settings (`subscription.tier`, `subscription.
  plan`) for in-band reads by the daemon + dispatcher.
- **Credential vault generalised**. The `/v1/accounts/
  ai-credentials` endpoint family renamed to `/v1/accounts/
  credentials` with a `kind` discriminator covering both
  AI-runner credentials (Anthropic / OpenAI / Google / GitHub
  — preserving the `dir-tarball` shape for Claude Pro / Codex
  Plus / Gemini OAuth) AND docker-registry credentials
  (ghcr.io / docker.io / etc.). Same encryption-at-rest, same
  per-credential audit-log shape, same revoke semantics.
  Failover dispatch step 6 now performs `docker login` before
  `docker pull` when the project's `brr.toml` declares a
  private image — **resolves the "private image launch-blocker"
  open question** that the second wave deferred. Public images
  bypass the lookup (fast path). Credential material lives only
  in the build worker's `~/.docker/config.json` for the spawn's
  duration; the sandbox itself never sees registry creds.
- **`brr brnrd plus` sub-verb family** added to the CLI
  (`status | upgrade | downgrade | resume | portal`) wrapping
  the new subscription endpoints. `brr brnrd creds add`
  extended to accept `docker-registry` as a kind alongside the
  existing AI-runner kinds. Seven-verb top-level taxonomy
  unchanged; everything sits under existing nouns.

### Pages modified

- `kb/decision-pricing-shape.md` — **full rewrite** around the
  two-tier sub + metered model. New tier comparison table,
  "What gates Plus" subsection, "What we charge for / don't"
  reframed for the sub leg, event-cap-overage policy (soft
  throttle, not metered), "Why this shape" updated, "What
  changed and why" section explains the credits-only-rejection
  and the reframe motivation, "Plus subscription mechanics"
  subsection, sustainability math table at three subscriber
  scales, "Alternatives considered" reorganised, open
  questions updated, trust signals updated, lineage entry
  appended.
- `kb/design-billing.md` — title + intro reshaped to "Plus
  subscription + credit wallet" (two billing legs). New
  "Plus subscription" section (Stripe product setup, monthly
  / annual prices, prorated upgrade, cancel-at-period-end
  downgrade, dunning grace, Plus credit grant). "Monthly
  credit grants" section reshaped for per-tier grants (5 Free
  / 500 Plus). New `/v1/accounts/subscription` endpoint family.
  Stripe webhook contract extended to handle subscription
  events. Audit log extended with subscription-lifecycle
  entries. Ledger gains `sub_bucket` to distinguish paid /
  plus_monthly / free_monthly draws. Refund policy split into
  wallet vs subscription leg. "Why two billing legs" replaces
  "Why credits" alternatives table. "What we do NOT do"
  clarified. Stripe Customer Portal enabled for self-service.
  Lineage entry appended.
- `kb/design-brnrd-protocol.md` — credential vault endpoints
  generalised (kind discriminator, registry-userpass shape,
  host field, `--kind` filter on list). Failover dispatch step
  6 rewritten to perform `docker login` before `docker pull`
  for private images (clear gate-side error if no registry
  cred is configured). AI-credential security model renamed
  to "Credential security model" and extended to cover
  registry credentials. New "Subscription endpoints"
  subsection. Project-creation endpoint enforces tier-based
  project cap. Failure-modes table gains "user revokes
  docker-registry credential mid-flight." "What we DO hold"
  table gains subscription state row + clarifies the
  credentials row spans AI + docker-registry. BYO compute
  "designed, deferred" rewritten to use the same vault with
  a new `kind` (not a parallel endpoint family). Scope section
  + intro updated to reflect the third wave. Lineage entry
  appended.
- `kb/decision-cli-shape.md` — `brr brnrd plus` sub-verb
  family added (`status | upgrade | downgrade | resume |
  portal`). `brr brnrd creds add` description updated to
  clarify both AI-runner kinds and `docker-registry` are
  supported. `brr brnrd balance` description updated for the
  three sub-buckets (paid + plus_monthly + free_monthly).
  Differences table gets a new row for `brr brnrd plus`.
  Lineage entry appended. Status note updated.
- `kb/design-config-layout.md` — "Private docker image — open
  question" section rewritten as "Private docker image —
  resolved via the generic credential vault" with concrete
  user flow. Scope-assignments table gets `credentials.*`
  (renamed from `ai_credentials.*`) and adds `subscription.
  tier` + `subscription.plan` as account-scope read-only
  keys. Open-questions section drops the now-resolved
  credential-vault-timing question. Lineage entry appended.
  Status note updated.
- `kb/subject-managed-mode.md` — Surfaces A/B table reshaped
  to show Free / Plus / overage instead of the old
  free-dispatcher / paid-compute split. New paragraph on
  self-hosted brnrd staying always-free. Credential vault
  subsection rewritten as "one store, two domains" covering
  both AI-runner and docker-registry creds. Shape-from-the-
  user's-perspective example updated for `brr brnrd plus
  upgrade` + `creds add docker-registry`. Billing subsection
  reshaped around two legs (Plus sub + wallet). "Surface B"
  intro mentions the `docker login` bootstrap step for
  private images. Data-minimization point on AI credentials
  widened to cover both kinds. Boundary → In scope reframed
  for Plus tier + generalised vault + Plus billing leg. Read
  next entries updated for the new shape.
- `kb/index.md` — managed-mode hub blurb updated for the
  pricing reframe + vault generalisation + Plus tier.
  Pricing-shape entry rewritten around the new two-tier
  shape. Billing entry rewritten around the two-leg model.
  Brnrd-protocol entry mentions subscription endpoints +
  `docker login` step. CLI-shape entry mentions `plus`
  sub-verb + `docker-registry` cred kind. Failover-compute
  plan entry updated for the generalised vault and `plus`
  verb. Dashboard MVP entry mentions the unified credentials
  view.
- `kb/notes-pondering-fleet.md` — third-wave paragraph
  appended to the pass-4 follow-up breadcrumb in §1.

No new pages this pass; six pages modified, plus index +
log + pondering breadcrumb. All designs / decisions remain
`Status: proposed`. Implementation order suggested by the
page set: credential vault generalisation (smallest
extension; unlocks private images at launch), then Plus
subscription endpoints + Stripe product setup (largest piece;
unblocks the revenue model), then `brr brnrd plus` CLI verbs
(thin wrapper over the endpoints).

## 2026-05-26 — pass-4 follow-up, third-wave refinement (naming + pricing)

User pushback on the just-proposed third-wave shape:

> "I don't like the plus as a name for the subscription and
>  neither as a subcommand verb tbh. I think the shape you
>  proposed makes the most sense, I would say that we could
>  offer the subscription even at 5 a month, and give the
>  fallback compute credits to make up for it. I am not sure
>  we want to limit to 1 project. maybe it is a good idea,
>  but maybe just a properly tweaked free tier limits will
>  do the jobs only for a real hobbyist user."

### What changed

- **Subscription tier left deliberately unnamed.** "Plus"
  rejected as too SaaS-upsell-tier branding-coded; "Pro" /
  "Premium" / "Member" / "Gear" all considered and deferred.
  UI + docs say "Subscribed" / "Subscriber" / "Subscription
  tier." A brand name can be retro-fitted post-launch with
  market data; un-naming a launched tier is painful.
- **Subscription price set to $5/month** ($50/year annual,
  ~17% off) — was $9/month in the third-wave draft. Sub-$5
  psychological threshold biases toward conversion volume
  ("I'll subscribe at $5 to support a tool I use casually")
  vs sub-$10 ("is this really worth $9?"). At equal
  subscriber counts the alternatives are revenue-similar;
  the bet is $5 + 300 credits converts materially more users
  than $9 + 500 credits.
- **Included compute set to 300 credits/month** ($3 of compute)
  — was 500 credits in the third-wave draft. Leaves $2/month
  true platform-fee headroom over the included compute (still
  comfortably above marginal cost; comfortably below "we're
  reselling compute at a markup" perception).
- **Free tier project cap raised from 1 → 3.** Considered the
  community reception of 1 vs 2 vs 3 vs unlimited Free:
  1-project Free reads as "trial mode, not Free" (HN / dev-
  twitter audience bounce); 2-3 captures the "side project
  + day-job + scratchpad" hobbyist cleanly; the "generous-but-
  bounded" pattern Plausible / Supabase / PostHog / Cal.com
  all use earned their adoption from that posture, not from
  tighter caps. Subscription cap unchanged at 10 projects
  (still 3.3× headroom over Free, plus the rest of the
  bundle).
- **CLI verb family renamed.** `brr brnrd plus [status |
  upgrade | downgrade | resume | portal]` → noun-first
  `brr brnrd subscription [status | start | cancel | resume |
  portal]` + `brr brnrd subscribe` as a shortcut for
  `subscription start` (the most common first-time
  interaction). Verb-within-family changes: `upgrade` →
  `start` (it's not really an "upgrade" — there's just one
  paid tier), `downgrade` → `cancel` (cancel-at-period-end
  is what actually happens; "downgrade" implied a multi-tier
  ladder that doesn't exist).
- **Subscription state value names finalised.** Tier value
  `"plus"` → `"subscribed"`; past-due `"plus_past_due"` →
  `"subscribed_past_due"`; plan codes `"plus_monthly"` /
  `"plus_annual"` → `"monthly"` / `"annual"`; wallet sub-
  bucket `plus_monthly` → `subscriber_monthly`. Stripe
  product label `"Brnrd Plus"` → `"Brnrd Subscription"`.

### Pages modified

- `kb/decision-pricing-shape.md` — tier comparison table
  rewritten ($5 + 300 credits + 3 projects on Free); "What
  the subscription unlocks" subsection renamed + reframed
  around bigger project headroom rather than multi-project as
  a binary gate; "Sustainability math" table re-run at $5 +
  300-credit assumptions (net-positive threshold around 80
  subscribers); "Subscription mechanics" section price /
  credit numbers updated; "Alt 4" + new "Alt 6 — Hard
  1-project cap" rejected-alternative entry added with the
  community-reception rationale; "Reseller of AI compute"
  renumbered to "Alt 7"; open questions reordered around the
  Free-project-cap and subscription-tier-brand-name questions;
  status note + lineage entry appended for 2026-05-26.
- `kb/design-billing.md` — title + intro reshaped to drop
  the "Plus" branding; "Plus subscription" section renamed
  to "Subscription" with all price / credit-grant numbers
  updated ($5/mo, 300 credits, 10 projects on Subscribed,
  3-project cap-down on cancel); audit-log operation
  `subscription_upgraded / downgraded` renamed to
  `subscription_plan_switched`; ledger sub-bucket
  `plus_monthly` renamed to `subscriber_monthly` across
  monthly-grants and API surface; subscription state values
  updated (`subscribed` / `subscribed_past_due`); CLI verb
  references updated to the new noun-first family +
  `subscribe` shortcut; lineage entry appended.
- `kb/design-brnrd-protocol.md` — Subscription endpoints
  table updated for the new state values + plan codes;
  project-creation endpoint enforces 3 / 10 (was 1 / 10);
  scope + intro paragraph + lineage entry updated.
- `kb/decision-cli-shape.md` — `brr brnrd plus` family
  rewritten as `brr brnrd subscription [status | start |
  cancel | resume | portal]` + `brr brnrd subscribe`
  shortcut; status output + price text + sub-bucket name
  in `brr brnrd balance` description updated; differences
  table updated; lineage entry appended.
- `kb/design-config-layout.md` — `subscription.tier` /
  `subscription.plan` value enums updated; CLI write-path
  references switched from `brr brnrd plus upgrade/downgrade`
  to `brr brnrd subscribe` / `brr brnrd subscription cancel`;
  lineage entry appended.
- `kb/subject-managed-mode.md` — Surface A/B table reshaped
  for the new tier shape (Free up to 3 projects, $5/mo
  Subscribed up to 10, 300-credit grant); user-perspective
  example uses `brr brnrd subscribe`; billing subsection
  renamed + numbers updated; Read-next + Boundary entries
  refreshed for the dropped "Plus" branding.
- `kb/plan-failover-compute.md` — status update paragraph
  updated; goals + done-definition CLI surface uses new verb
  family; per-tier defaults updated (300 Subscribed);
  "Subscription tier under-priced / over-priced" risk re-
  anchored around the $5 + 300-credit shape; lineage entry
  appended.
- `kb/plan-managed-gates-launch.md` — launch-announcement
  framing + Read-next entries updated for the new tier shape
  and the dropped "Plus" branding.
- `kb/index.md` — managed-mode hub blurb + pricing-shape +
  billing + protocol + CLI-shape + failover-plan entries all
  updated for the new pricing, naming, and CLI verb.

No new pages this pass; nine pages refined, plus index +
log + pondering breadcrumb. Implementation order from the
third wave still holds; this pass refines the externally-
visible surfaces (price, name, project cap) without touching
the implementation surface (vault + endpoints + Stripe
product + dispatcher are the same shape; only labels +
numbers + a few enum value names changed).

## 2026-05-26 — locking pass: licensing + competitive-defense posture

Fifth small wave on the managed-mode / pricing surface — the
"OK lock these decisions in" pass. User asked:

> "yeah lets add a few notes to lock it. 5 for early adopters
>  (six seven :D for the afterparty) sounds great. the license
>  also is a right thing. don't have money on the trademark
>  yet, but we need to have it as a prio post launch."

### What changed

- **New page: `kb/decision-licensing-and-defense.md`.** Locks
  the three competitive-defense moves into canonical form:
  - **License split**: `src/brr/` stays MIT (daemon —
    maximises community goodwill, fork freely); `src/brnrd/`
    + `src/brnrd_web/` ship **AGPLv3** (backend / dashboard —
    closes the "Big Cloud rebrands our OSS as managed
    service" attack while keeping self-hosters fully
    unaffected). Per-package `LICENSE` files; top-level
    `LICENSE-OVERVIEW.md` documents the split; AGPL chosen
    over BUSL / ELv2 / SSPL specifically because it preserves
    OSI-approved status + community trust + protects against
    the specific realistic attacker.
  - **Early-adopter pricing**: **first 200 subscribers at $5
    / month grandfathered forever**, then **$7 / month for
    public-cohort joiners** (with $50 / $70 annual
    variants). Stripe-native grandfathering: two `Price` IDs
    on one Product, existing subs never migrate. Atomic
    counter on the brnrd backend gates the supporter
    boundary; live counter on the pricing page shows
    "Y / 200 spots remaining" during the cohort window.
  - **Trademark on `brr` + `brnrd`**: deferred at launch for
    budget reasons (€800-1500 via EUIPO through HugiMuni SAS
    / French IP lawyer; classes 9 + 42). Becomes priority
    work when **first of** launch+12-months OR €10K
    cumulative revenue OR first observed competitor fires.
    No defensive look-alike domain pre-buys; trademark +
    UDRP covers the realistic attack pattern at lower cost.
  - Explicit anti-patterns named: don't go BUSL / ELv2 /
    SSPL; don't gate any feature behind hosted-only (breaks
    always-free-self-host); don't race to bottom on price;
    don't require a CLA at launch.
  - Adjacent moats already in other pages (verified bot
    accounts on `brnrd.dev`, integration stickiness,
    data-minimization trust signal, security posture, brand
    + community) cross-referenced without duplicating.
- **`kb/decision-pricing-shape.md`** — tier table updated to
  show **two `Price` variants** ($5 supporter / $7 public)
  with the cohort boundary noted; new "Early-adopter price
  step" section locks the Stripe mechanics + cohort-counter
  contract + dashboard surface. "Subscription mechanics"
  section reframed around the supporter→public step.
  "Sustainability math" table re-run with blended pricing
  (200 supporters × $5 + remainder × $7) — shows the step
  adds ~$600/mo at 500 subs and ~$1,600/mo at 1,000 subs vs
  an all-supporter-price universe. Open-questions entry on
  annual discount level updated. Lineage entry appended.
- **`kb/decision-monorepo-structure.md`** — new short
  "License boundary aligns with the package boundary"
  section locks the per-package `LICENSE` files (MIT for
  `src/brr/LICENSE`, AGPLv3 for `src/brnrd/LICENSE` +
  `src/brnrd_web/LICENSE`) and notes that the monorepo
  restructuring PR should land them together. Read-next
  expanded with the licensing-and-defense decision.
  Lineage entry appended.
- **`kb/index.md`** — pricing-shape entry updated for the
  $5/$7 supporter→public step; new
  `decision-licensing-and-defense.md` entry added in the
  Fleet & overlays / managed-mode section; monorepo-
  structure entry mentions the license-boundary alignment.

### Pages modified

- `kb/decision-licensing-and-defense.md` — **new file**.
- `kb/decision-pricing-shape.md` — tier table + Status
  intro + new "Early-adopter price step" section +
  subscription-mechanics rephrase + sustainability-math
  blended numbers + open-question on annual discount +
  lineage entry.
- `kb/decision-monorepo-structure.md` — new "License
  boundary aligns with the package boundary" section +
  read-next expansion + lineage entry.
- `kb/index.md` — pricing-shape blurb + new licensing-
  and-defense blurb + monorepo-structure license-boundary
  callout.
- `kb/log.md` — this entry.
- `kb/notes-pondering-fleet.md` — locking-pass breadcrumb
  appended to §1 (separate edit below).

One new page; three pages refined; index + log + pondering
breadcrumb updated. All status markers stay `proposed`.
Implementation impact is small at launch: a top-level
`LICENSE-OVERVIEW.md` + per-package `LICENSE` files (~30
min of work, lands with the monorepo restructuring PR); two
Stripe `Price` IDs instead of one + an atomic supporter
counter on the backend (~half-day during Stripe product
setup); trademark registration is post-launch (€800-1500
when triggered). The defensive posture is overwhelmingly
**already-built** — the license / pricing-step / trademark
moves are just locking already-implicit architectural
choices into explicit, defensible form before launch reveals
them to the world.

## [2026-05-26] decision | locking pass: BYO-for-subscribers + credit-bucket / per-source expiry policy

Closing pass on two intertwined topics: the BYO-everything-
for-subscribers policy across cloud envs (and pre-applied to
future agentic-secretary connectors), and the explicit credit-
bucket ledger schema with per-source expiry policy that
backs the "$5/mo for the platform + $3 of bundled compute on
the house" framing without leaking into "we owe users their
unused grants forever."

### Decisions locked

- **BYO compute is a subscriber-only sub-option of Surface B,
  parallel-shipped with managed support for each cloud.** At
  launch, only Fly Machines ships managed → only BYO Fly ships
  at launch. Subsequent clouds (Modal / Daytona / Codespaces /
  …) get BYO when they get managed, one-for-one. Free stays
  managed-only on purpose: BYO is structurally a cost-saving
  feature, subscribing is the cost-saving move; Free's role is
  "try it without setup friction." The subscriber gate sits on
  the credential-vault write + read paths for `kind=cloud-
  platform`.
- **Credit buckets formalised** with per-source expiry policy
  (the "temporal grouped resources" abstraction, solved with
  the standard bucketed-ledger shape used by OpenAI /
  Anthropic / AWS / GCP / Stripe Customer Balance):
  - `free_monthly` and `subscriber_monthly`: use-it-or-lose-it
    at cycle boundary (mobile-plan intuition). Free's monthly
    grant is **activity-gated** — only refreshes if the
    account had any prior-month activity, bounds dormant-
    account compute cost at zero.
  - `purchased`: **never expires** (account-dormancy bounded,
    not credit-expiry bounded — 24mo pause / 36mo prompt /
    deletion only on explicit user request or GDPR). "I paid
    you, my credits are mine forever" is the EU-friendly
    strongest-consumer-protection posture and exceeds OpenAI /
    Anthropic's 1-year default.
  - `promotional`: future-proofing for signup bonuses /
    referrals / support-issued goodwill, per-grant
    `expires_at`. Not used at launch but schema-ready.
  - **Debit priority**: grants first (soonest-expiring within
    grants), purchased last (FIFO). Preserves the user's
    purchased balance as long as possible.
  - **Dashboard never says "credits expired"** — says "your
    monthly allowance refreshes on &lt;date&gt;." Same
    mechanic, opposite emotional valence.
- **Sub-bucket name rename**: `paid` → `purchased`
  everywhere (audit ops, debit-spawn `sub_bucket`, refund op).
  `purchased` describes the semantic precisely ("user
  explicitly purchased these via Stripe top-up Checkout")
  and pairs cleanly with `granted` as the category boundary.
- **Reimbursement framing rejected** in favour of "$5
  platform fee + $3 of bundled compute on the house" — the
  platform fee is the platform fee, the credits aren't a
  refund. Subscribers who BYO let the grant lapse unused (or
  spend it on a different managed env they didn't bring).
- **"Don't lock subscribers into brnrd's cloud" promoted to
  load-bearing anti-pattern** in
  [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md);
  BYO-everything-for-subscribers added as a fifth adjacent
  defense move (a competing fork can't out-open us without
  giving up more revenue than their model bears).
- **Per-paying-customer** as the canonical terminology
  (replacing per-account when the meaning is "the gate sits
  on subscription state").

### Pages updated

- **`kb/decision-pricing-shape.md`** — new "Compute: managed
  vs BYO" section codifying the two-flow shape, the
  subscriber-gate rationale, and the cloud-env-by-cloud-env
  shipping rule. Subscription-feature table grew a row for
  BYO opt-in. New "Credit buckets and expiry policy"
  subsection summarises the four buckets + debit priority +
  activity-gated Free grants + the dashboard-language pass.
  "Compute included" framing rewritten as "300 credits of
  bundled compute on the house" + grant-not-reimbursement
  nuance. Existing "BYO compute — designed, deferred"
  section reframed as "BYO compute — subscriber feature,
  parallel-shipped with managed." Open-questions extended
  with Free-grant-size-at-scale + account-dormancy-timing.
  Lineage entry appended.
- **`kb/design-billing.md`** — full new "Credit buckets and
  expiry policy" section (replaces and subsumes the prior
  "Monthly credit grants" section) with the bucket table,
  per-bucket expiry mechanics, activity-gated Free grants,
  account-dormancy state machine, and the
  dashboard-language pass. Audit log entries renamed: `paid`
  → `purchased`, new `grant_promotional` /
  `expire_promotional` / `account_marked_dormant` /
  `account_reactivated` / `spawn_byo` ops. Refund policy
  cleaned up: pro-rata within 30 days for `purchased`;
  grants never cash-refundable; beyond 30d purchased credits
  stay valid forever but aren't cash-refundable. New
  "BYO-compute spawns — wallet bypass" section codifies the
  zero-debit BYO path. Scope expanded; lineage entry
  appended.
- **`kb/design-brnrd-protocol.md`** — credential vault grew
  a third domain `cloud-platform` with a `provider`
  discriminator (Fly at launch). Vault writes + reads
  subscriber-gated on `kind=cloud-platform`. CLI surface in
  the vault section grew `brr brnrd creds add cloud-platform
  --provider fly --token …`. New "BYO compute — subscriber
  feature, parallel-shipped with managed" section replaces
  the prior "BYO compute — designed, deferred" section; the
  dispatch path documents the same env class with two
  callers (managed token vs decrypted user token). Scope
  "What we explicitly do NOT do" updated. Lineage entry
  appended.
- **`kb/plan-failover-compute.md`** — ship-order updated to
  parallel-ship BYO Fly alongside managed Fly at launch.
  Credential-vault done-definition extended with the fourth
  payload shape + subscriber gate. "Out of scope" entry on
  BYO platform tokens rewritten to make clear non-Fly BYO
  follows non-Fly managed support (one-for-one rule).
  Dispatcher's pre-spawn balance check now walks the bucket
  priority order. Lineage entry appended.
- **`kb/decision-licensing-and-defense.md`** — "Don't lock
  subscribers into brnrd's cloud" added to the anti-pattern
  surface; BYO-everything-for-subscribers added as a fifth
  entry under "Adjacent moves" with the "competing fork
  can't out-open us without giving up revenue" rationale.
  Lineage entry appended.
- **`kb/decision-connectors-layering.md`** — new
  "BYO-for-subscribers applies to connectors" section
  pre-applies the cloud-compute BYO posture to the
  agentic-secretary connectors layer (same vault, new `kind`
  value `connector-oauth`, same subscriber gate). One
  pattern across compute + connectors + future surfaces.
  Lineage entry appended.
- **`kb/subject-managed-mode.md`** — Surface table reshaped:
  the prior "B. Managed compute" + "C. BYO compute
  (deferred)" rows collapsed into a single Surface B with a
  managed-default-vs-BYO-opt-in sub-structure. Subscriber-
  only BYO rationale captured. "Surface C" section
  re-titled "BYO compute (subscriber sub-option of Surface
  B)" with the pre-2026-05-26 deferral rationale preserved
  for context. In-scope / out-of-scope lists updated.
- **`kb/design-config-layout.md`** — `credentials.*` schema
  entry extended to cover the third `kind` value
  `cloud-platform` with the subscriber-gate note. No
  on-disk schema change (cloud-platform creds never live
  locally). Lineage entry appended.
- **`kb/index.md`** — pricing-shape blurb extended with
  buckets + BYO; managed-mode hub blurb updated for
  Surface B's BYO sub-option; brnrd-protocol blurb covers
  the three credential vault domains + BYO dispatch branch;
  billing blurb covers the bucketed ledger + dormancy
  policy + BYO wallet bypass; failover-compute blurb
  reflects BYO Fly at launch; licensing blurb gains the new
  anti-pattern + adjacent move; connectors-layering blurb
  notes BYO pre-applies; config-layout blurb mentions the
  subscriber-gated `cloud-platform` extension.
- **`kb/log.md`** — this entry.
- **`kb/notes-pondering-fleet.md`** — locking-pass breadcrumb
  appended to §1.

### Why this lock-in matters

The pricing-shape page already had "BYO deferred forever" as
its working assumption; in practice this turned out to be
inconsistent with the "open and honest" trust posture we
need to sustain the community-trust moat at $5/$7. The
locking pass reconciles by tying BYO availability one-for-
one to managed support per cloud — at launch the cost is
small (one credential `kind` value + one dispatcher branch)
because Fly's managed path is anyway on the critical path;
post-launch the rule is simple ("if we ship it managed,
subscribers can BYO it"); the posture doubles as a moat
amplifier (a competing fork would have to be more open on
credentials, meaning less revenue per customer, in a model
that already runs at $5/$7).

The credit-bucket / per-source-expiry pass codifies what
was implicit before: monthly grants are "this month's
allowance" (mobile-plan intuition), purchased credits are
property (EU-friendly + trust signal vs OpenAI / Anthropic's
1-year expiry), promotional credits are activation tools.
Activity-gating Free grants bounds the dormant-account
compute tail at zero — important when Free scales to 10K /
100K accounts. The rename `paid` → `purchased` is small but
semantically precise.

All status markers stay `proposed`. Implementation cost is
small over already-planned work: credential vault grows one
`kind` value (~30 LOC); dispatcher grows one branch (~20
LOC); the bucket model is a renaming + activity-gate + a
small dormancy-state machine (~150 LOC for the dormancy
state machine, otherwise mostly already-designed).

## [2026-05-26] plan | brnrd pricing locking pass II — Free signup bonus, subscriber project cap unlock, honest-nudge UX, deferred-revenue accounting

Second locking pass on the brnrd pricing + dashboard surfaces,
in response to the user's "start a bit stingier and relax as
we go" + "lets allow subscribers to have unlimited as soon as
they spent smth small but reasonable on credits" + "a
dashboard to show the allowance consumption and a nudge to go
subscribe — that's not too mean, right?" + "throttling is a
good idea, like it" framing. All decisions tighten the
pricing model's economic shape AND its UX honesty.

### What's locked

1. **Free monthly grant reshaped into a one-time signup
   bonus.** 10 credits granted on Free account creation,
   expires 30 days from creation OR on full consumption,
   whichever first. Replaces the prior "5/month activity-
   gated recurring" shape. Bounded by signup count (not
   active-user retention): at 100K signups total, cost caps
   at $10K of compute (one-time, not per year). The
   activity-gating logic is removed entirely. Tightening
   reads as betrayal, loosening reads as winning — start
   stingier than required, relax later if data warrants.
2. **Subscriber project cap reshaped from flat to tiered.**
   25 projects by default; **unlimited after $10 of
   cumulative top-ups** (monotonic counter
   `cumulative_purchased_usd_lifetime`, never decremented on
   refund). `project_cap_unlocked` is a permanent flag once
   set — survives subscription cancel + re-subscribe. 25
   covers almost every solo developer; the unlock rewards
   sustained-usage power users with no rent-seeking layer.
3. **Multi-account abuse mitigation via binding uniqueness,
   not fingerprinting.** Database-level UNIQUE constraints
   on `(platform, chat_id)` for chat bindings and on
   `repo_full_name` for repo bindings. Enforced anyway for
   routing correctness; framing it as abuse-mitigation
   gives ~95% of the value at zero incremental cost.
   Conflict response returns an obfuscated
   `bound_to_account` (no PII leak). Explicitly no
   fingerprinting / IP velocity / "suspicious account"
   flagging at launch.
4. **Dashboard nudges + transparency policy codified.**
   Eighth dashboard view added: "Allowance + usage" with
   bucket-breakdown credits bar, events bar, projects bar
   (with unlock-progress delta), throttle banner when
   active. Banner-nudge triggers + copy table covers Free
   80% / 100% events, bonus-consumed, bonus-expiring,
   subscriber 80% credits, 25-project cap, 80% event cap.
   Anti-patterns explicitly named: no modals, no
   cancellation friction, no countdown timers, no silent
   throttling, no nudge spam. Gate-side one-line subscribe
   footer ONLY on throttle / cap / out-of-credit events.
   "Throttling is always surfaced" is the load-bearing
   honest pattern.
5. **Deferred-revenue accounting framing locked in.**
   Purchased credits + subscription fees are deferred
   revenue under French GAAP / IFRS; Stripe Revenue
   Recognition automates the daily proration on
   subscriptions + per-debit recognition on purchased
   credits; grants are NOT deferred revenue (they're
   operational COGS). HugiMuni SAS chart-of-accounts
   sketch included for the launch-stage accountant. Bank-
   account separation (operating vs reserve) is called out
   as treasury hygiene at ≥€10K MRR, NOT a legal
   requirement at launch. No legal segregation needed for
   SaaS prepaid balances in France.

### Files updated

- **`kb/decision-pricing-shape.md`** — tier table refreshed
  (Free signup bonus, Subscribed 25/unlimited cap); two
  new sections "Free compute grant — one-time signup
  bonus, not recurring" + "Subscriber project cap — 25
  default, unlimited after $10 of cumulative top-ups";
  bucket table renamed `free_monthly` → `free_signup_bonus`;
  new "Multi-account abuse mitigation: binding uniqueness,
  not fingerprinting" section; new "Dashboard nudges +
  transparency" section with trigger/copy table +
  anti-patterns list + gate-side footer spec; open
  questions updated for the cap-unlock threshold + signup-
  bonus size; throttling explicitly noted as always
  surfaced. Lineage entry appended.
- **`kb/design-billing.md`** — bucket table renamed
  `free_monthly` → `free_signup_bonus` with new mechanics
  (one-time on Free signup, 30-day expiry, no activity
  gating); audit ops renamed (`grant_free_signup_bonus` /
  `expire_free_signup_bonus` with `reason` field);
  balance-UI examples updated; debit priority updated. New
  "Cumulative purchase tracking and the subscriber project
  cap unlock" section codifies the two new monotonic
  counters + the derived `project_cap_unlocked` flag +
  effective-cap function + new `project_cap_unlocked`
  audit op. New "Deferred-revenue accounting" section
  (purchased + subscription = deferred revenue with
  Stripe Revenue Recognition automation; grants = COGS;
  HugiMuni SAS chart-of-accounts; bank separation as
  treasury hygiene not legal requirement). Lineage entry
  appended.
- **`kb/design-brnrd-protocol.md`** — project-creation
  endpoint updated to enforce the new effective project
  cap (3 / 25 / unlimited) with `subscription_hint` field
  on the 409 response. New "Binding uniqueness —
  correctness + abuse-mitigation" section below the
  bindings endpoints (global UNIQUE on `(platform,
  chat_id)` and `repo_full_name`; 409 with obfuscated
  `bound_to_account`; no fingerprinting at launch). "What
  we DO hold" table grew a row for the cumulative-purchase
  counters + their mirror keys in account-scope settings.
  Lineage entry appended.
- **`kb/design-config-layout.md`** — three new account-
  scope read-only derived keys added:
  `subscription.project_cap` (3 / 25 / unlimited),
  `subscription.project_cap_unlocked` (boolean, permanent
  once true), `cumulative_purchased_usd_lifetime`
  (monotonic counter). All derived from the brnrd-side
  ledger state. Lineage entry appended.
- **`kb/plan-failover-compute.md`** — Free compute math
  reframed around the 10-credit one-time signup bonus
  (30-day expiry) replacing the prior 5/month activity-
  gated recurring grant. Done-definition + Goals updated.
  Project-cap shape updated to 25 / unlimited. Multi-
  account abuse framing added to the Free-tier-abuse
  risk note. Lineage entry appended.
- **`kb/plan-brnrd-dashboard-mvp.md`** — eight views
  instead of seven; new View 8 "Allowance + usage" with
  full spec; new "Allowance gauges + honest-nudge UX"
  section between Done-definition and Slices, with the
  inline-gauge placements + banner-nudge trigger / copy /
  CTA table + anti-patterns list + gate-side footer
  spec; Slice 3 extended to deliver the allowance view +
  inline gauge component + banner-nudge component, LOC
  estimates raised; projects-view grew tier-aware
  project-cap gauge. Lineage entry appended.
- **`kb/subject-managed-mode.md`** — Surface A description
  updated to "25 projects (unlimited after $10 of
  cumulative top-ups)" and "basic dashboard with
  allowance gauges"; Surface B description updated to
  "10 spawn-credit one-time signup bonus (30-day
  expiry)"; Dashboard section says "eight views" with
  the allowance view as item 8; "Dashboard MVP" scope
  entry updated; debit-priority blurb updated to use the
  new bucket names.
- **`kb/index.md`** — pricing-shape blurb refreshed with
  Free signup bonus + 25/unlimited cap + binding
  uniqueness + dashboard nudges + locking-pass-II
  breadcrumb; billing blurb refreshed with the
  `free_signup_bonus` bucket + cumulative purchase
  tracking + deferred-revenue framing; dashboard MVP
  blurb refreshed for eight views + honest-nudge UX.
- **`kb/log.md`** — this entry.
- **`kb/notes-pondering-fleet.md`** — locking-pass-II
  breadcrumb appended to §1.

### Why this lock-in matters

The pricing shape locks the economics in the direction the
business needs to go (stingier on Free, more rewarding on
sustained-paying subscribers) while the dashboard nudges
lock the UX in the direction the user trust needs to go
(honest, always-signposted, no dark patterns). Together
they answer the user's pivotal question — "is this too
mean?" — with no: throttling that's announced is fair;
caps that the user sees coming are fair; subscribe-prompts
that respect dismissal are fair. The deferred-revenue
framing tells the implementer + accountant how the
purchased-credits-never-expire promise is held safely on
the books at launch, and at what scale treasury hygiene
should evolve into operating-vs-reserve account
separation.

Implementation cost is small over already-planned work:
bucket rename + activity-gate removal + 30-day expiry is
~50 LOC of mechanical changes; cumulative-purchase
counter + cap-unlock flag is a few columns + ~30 LOC of
threshold check; binding uniqueness is two DB UNIQUE
constraints + ~20 LOC of conflict response handling;
allowance dashboard view + gauge + banner components are
~800 LOC of templates + routes + tests. Total ~1K LOC
spread across the slice-3 dashboard work + the
already-planned billing + protocol slices.

## [2026-05-26] plan | brnrd pricing locking pass III — MR-review grooming, open questions closed, soft-throttle reframed

Third locking pass, driven by the user's MR-review walkthrough of
the pass-II changes. Three goals: close as many open questions
as we can with launch-default-plus-config-knob locks, prune
duplication that snuck in during pass II, and resolve / bubble
up contradictions.

### What's locked

1. **Free signup bonus size = 10 credits** + env knob
   `BRNRD_FREE_SIGNUP_BONUS_CREDITS`. Demoted from "open
   question" to "tunable post-launch."
2. **Free project cap = 3 projects** + env knob. "Could it be
   2" considered and rejected (too close to "trial mode"
   framing for community perception). Open question dropped.
3. **Project-cap unlock threshold = $10 cumulative top-ups** +
   env knob `BRNRD_PROJECT_CAP_UNLOCK_USD`. Demoted from
   open question.
4. **Supporter cohort = first 200 subscribers** at $5/mo /
   $50/yr (locked, grandfathered forever); public cohort at
   $7/mo / $70/yr afterward. Annual discount = ~17%, locked.
   The "could annual go to 25%" open question dropped — one
   pricing knob at a time, supporter step is already the
   launch's annual-conversion lever.
5. **Subscriber monthly grant = 300 credits** ($3 of compute)
   + env knob `BRNRD_SUBSCRIBER_MONTHLY_CREDITS`. Locked at
   launch; tuning is post-launch based on median/p95
   subscriber consumption instrumentation. Open question
   replaced by an instrumentation note.
6. **Permission-prompt defaults**: Free = `ask`; Subscribed =
   **`auto-approve-below-monthly-limit`** (NEW MODE — sixth
   in the failover-policy mode list). Auto-approves any
   spawn whose estimated cost fits inside the user's
   remaining monthly grant + purchased balance; falls back
   to `ask` once the envelope is exhausted, until cycle
   reset or top-up. Subscribers stop seeing routine
   in-budget prompts; the prompt appears at the natural
   upsell moment when the envelope is exhausted.
7. **Account dormancy timing = 24mo pause / 36mo prompt** +
   env knobs. Any future change is a 60-day-notice billing-
   policy update. Open question dropped.

Only **one open question remains**: subscription-tier brand
name (post-launch only; current lean = keep as "Subscribed").

### Reframed: event-cap overage = soft-throttle, not hard wall

The pass-II shape had Free events at 100/month *queue*
indefinitely until the next month boundary, with the nudge
sitting as a paywall on the queued events.

The user clarified during MR review: **the nudge is the
resolution to a throttled-flow situation, not a paywall**.
Events should still flow past 100/month, just slowly (~1/hour
post-cap). The gate-side footer is "here's why this is slow,
here's how to lift it" — events still arrive, the user still
gets their reply, the subscribe link is one of several
resolutions (subscribe / wait for cycle / self-host). Same
shape as the existing Subscribed soft-throttle (~1 event/sec
post-10K/month).

Free users can keep using brnrd indefinitely at the slow rate
without subscribing. The "speed limit, not a wall" framing
better matches the open / honest posture the project is
building on.

### Pruned: duplication grooming pass

- **Nudge trigger / copy / anti-patterns table** lived in both
  `decision-pricing-shape.md` and `plan-brnrd-dashboard-mvp.md`.
  Canonical home is now pricing-shape; dashboard-mvp's
  "Allowance gauges + honest-nudge UX" section trimmed to
  describe only the *build* side (gauge component, banner
  component, dismissal persistence, gate-footer wiring) and
  delegates the *policy* side back to pricing-shape. Banner
  copy + gate footer strings now live in a single
  `src/brnrd_web/nudges.py` module that both the dashboard
  AND the gate adapter read from, eliminating drift at the
  code level too.
- **Surface table tier captions** in `subject-managed-mode.md`
  duplicated the pricing-shape tier table. Reduced to a
  surfaces-only table ("What it is" + "Adoption pain it
  removes" columns); pricing details (caps, included compute,
  bonus, etc.) only exist in pricing-shape now.
- **Dashboard view list** in `subject-managed-mode.md`
  duplicated the plan-brnrd-dashboard-mvp.md view spec.
  Reduced to a one-line summary + delegate to the plan page.

### Stale claims pruned (6 sites)

Already committed separately as [`67b0bad`]: stale
"BYO-deferred from launch" mentions in `design-brnrd-protocol.md`
+ `index.md` + `plan-managed-gates-launch.md`; stale "manual
invoicing at launch" mentions in `subject-managed-mode.md` +
`plan-brnrd-dashboard-mvp.md` + `plan-failover-compute.md`.

### Stripe-integrated billing callout added

Top of `decision-pricing-shape.md` now carries an explicit
"this page assumes Stripe-integrated billing from day one
per design-billing.md" callout, preventing future drift
between the policy page and the implementation page.

### Files updated

- **`kb/decision-pricing-shape.md`** — Stripe callout near
  the top; event-cap-overage section reframed (soft throttle
  ~1/hour, footer = resolution not paywall, gate footer
  copy rewritten for three throttle/out-of-credit/blocked
  cases); open questions section replaced by "Launch-tunable
  knobs" (15-row config-keys table) + "Post-launch tuning
  checklist" (4 metrics to instrument) + a single remaining
  open question (tier brand name); trust signals
  consolidated; lineage entry appended.
- **`kb/plan-failover-compute.md`** — sixth approval mode
  `auto-approve-below-monthly-limit` added; per-tier
  defaults (Free=ask, Subscribed=auto-approve-below-monthly-limit)
  documented; CLI flag added to `brr brnrd policy set`;
  permission-prompt-fatigue risk-mitigation rewritten;
  lineage entry appended.
- **`kb/plan-brnrd-dashboard-mvp.md`** — "Allowance gauges +
  honest-nudge UX" section trimmed (canonical pointer to
  pricing-shape replaces the duplicated trigger table);
  gate-footer wiring section updated for soft-throttle
  ("events still flow"); `nudges.py` module called out as
  the single source of truth for banner / footer copy at
  the code level; lineage entry appended.
- **`kb/subject-managed-mode.md`** — Surface table reduced
  from 4 columns to 2 (surfaces only, no pricing
  duplication); dashboard view list reduced to a one-line
  summary + delegate to plan-brnrd-dashboard-mvp.
- **`kb/decision-cli-shape.md`** — `brr brnrd policy get|set`
  help text grew the new `auto-approve-below-monthly-limit`
  mode in the inline mode-list comment.
- **`kb/design-billing.md`** — new "Launch defaults +
  tunable knobs" section with the 10-row env-knob → ledger /
  Stripe-product mapping; stale "(10 vs 3 on Free)"
  project-cap mention in the opening bullet updated to the
  locked tiered shape; lineage entry appended.
- **`kb/index.md`** — pricing-shape blurb extended with the
  locking-pass-III summary (knob-locks, new permission
  mode, soft-throttle reframe).
- **`kb/log.md`** — this entry.
- **`kb/notes-pondering-fleet.md`** — locking-pass-III
  breadcrumb appended to §1.

### Why this lock-in matters

Locking pass III is the pass that takes the design from
"proposed shapes + 8 open questions" to "implementable launch
shape + 1 brand-name question." The combination of
config-knob locks (every launch-default-number has a
`BRNRD_*` env var) + post-launch instrumentation checklist
makes the launch numbers explicitly tunable without code
changes — which is the right shape for a product whose
unit-economics need 6-12 weeks of real usage to validate.

The soft-throttle reframe is the more important UX win: the
pass-II shape had Free events HARD queue past 100/month,
which is the actually-mean thing the project's positioning
argues against. The new shape is "speed limit, not wall" —
which fits the open / honest posture cleanly.

The duplication grooming is the smaller but meaningful win:
nudge copy now has one canonical home (pricing-shape) + one
canonical code module (`nudges.py`), eliminating the two
biggest drift surfaces the MR introduced.

Implementation cost over already-planned work is ~0 LOC — the
locking pass is all policy + organisational work, no new
code paths.

## [2026-05-26] plan | brnrd locking pass IV — accepts + daemon shape + overdraft + websites

Fourth locking pass, driven by the user's MR-review continuing
into substantive architectural choices. **Four big accepts +
six substantive reshapes + one new decision page**.

### Accepts (with "fluid" framing per the user's caveat)

**Earlier in the same pass (status flips only, no shape
change)**: `decision-connectors-layering.md`,
`decision-licensing-and-defense.md`,
`decision-monorepo-structure.md`, `decision-pricing-shape.md`.

**Plus six accepts on the plan / hub side**:
`kb/subject-managed-mode.md`, `kb/plan-managed-gates-launch.md`,
`kb/plan-brnrd-dashboard-mvp.md` (explicitly fluid — the user
plans to adjust a lot during implementation),
`kb/plan-failover-compute.md`, `kb/plan-env-fly-machines.md`,
`kb/plan-kb-subcommand.md`. All marked accepted with explicit
"implementation feedback may reshape — treat as a working
spine, not a contract" language.

### Substantive reshapes

**1. Daemon shape: per-project → machine-scoped multi-project
multiplexer.**

The biggest architectural shift of the pass. The pre-pass-IV
shape had one systemd / launchd unit per brr-init'd project
(`brr daemon install --name <project>`), each with a daemon
pinned to a single repo via `WorkingDirectory`. The new
shape is **one daemon per machine**, serving all brr-init'd
repos discovered via `~/.config/brr/projects.toml` (appended
by `brr init`; manipulable via new
`brr daemon list | adopt | forget` verbs). One supervised
unit per machine, no `--name`, no `WorkingDirectory` pinning.
Internal multiplexing: one asyncio inbox-poller task per
project, all sharing one `httpx.AsyncClient` (HTTP/2-pooled)
to brnrd.

**Account binding lives at machine scope** at
`~/.local/state/brr/account/` (`binding.toml`,
`subscription.toml`, `settings.toml`). When the user runs
`brnrd connect` from a second project on the same machine,
the binding is already there; only project-create +
gate-pair phases run. The load-bearing UX win.

Touches: `kb/design-brnrd-protocol.md` (new protocol-shape
diagram + new "Runtime profile: async, httpx, ASGI"
section), `kb/plan-laptop-daemoning.md` (reshaped from
per-project to machine-scoped; new project-registry +
account-binding sections; verbs added),
`kb/plan-daemon-deployment-templates.md` (cloud-host
templates aligned with the machine-scoped multi-project
shape), `kb/design-config-layout.md` (new "Account scope —
machine-scoped binding + cached settings" section).

User intent: "probably the local daemon should serve all
local projects and connect to the brnrd... if a user has
configured brnrd once for a project already, we should
pickup at least the account binding, subscription status,
brnrd url, etc."

**2. `brnrd` as a sibling top-level binary (same package).**

`brr` owns per-project operations (init, run, daemon, gate,
config, kb). `brnrd` owns per-account operations
(subscription, credits, vault, projects-across-the-account).
Same wheel, two `[project.scripts]` entries:
`brr = "brr.cli:main"`, `brnrd = "brnrd_cli.main:main"`. The
existing `brr brnrd <subcmd>` shape stays as a convenience
alias.

Touches: `kb/decision-cli-shape.md` (new "`brnrd` as a
sibling top-level binary" section).

User intent: "i am thinking to make brnrd a separate
command, installed in the same package, unless you would
oppose."

**3. Permission-prompt scope clarified: compute-only.**

The six-mode permission-prompt model (`ask` /
`auto-approve-*` / `never`) applies to **managed-compute
spawns only**. Future credit-eating features (realtime
voice, vector / semantic stores, visual graphs) that will
land later use a **one-time enablement consent** model
(`brnrd features enable <name>`) plus ongoing visibility
in the credit bucket breakdown — not per-call prompts.
Per-call prompts only make sense where the user has a
meaningful local-vs-cloud choice (which is compute);
cloud-only features have no local fallback to compare
against.

Touches: `kb/decision-cli-shape.md` (new "Permission
prompts apply to compute only" section).

User intent: "the bot-gating 'do you wanna run in the
cloud?' concept harder to implement or support I think...
we also shouldn't design to accommodate for all the future
ideas."

**4. Config — per-branch overrides embraced + implicit
"active branch" concept on the daemon.**

`brr.toml` is git-tracked → varies per branch. Embraced as
a feature: feature-branch `runner.timeout` overrides,
experiment-branch `env.default` flips, release-branch
`docker.image` pinning. Brnrd has no "active branch"
concept at all (its responsibilities are per-project, not
per-branch). When brnrd fails over to a managed-compute
spawn, the spawn clones the repo at the event's
`branch_target` and reads THAT branch's `brr.toml`.

The daemon picks the working branch when the event doesn't
name one via a three-step rule:
1. `event.branch_target` if provided.
2. `daemon.last_spawned_branch[project_id]` — captures the
   work-continuity intent.
3. Repo default branch.

Last-spawned-branch state lives in
`.brr/state/last_spawned_branch` per project, machine-local,
gitignored.

Touches: `kb/design-config-layout.md` (new "Per-branch
overrides — embraced, not avoided" section + "Picking the
working branch when an event doesn't name one" subsection).

User intent: "the local branch that brr daemon last spawned
task at is used as a base? I mean or main. the work
continuity idea hints it should be based on the local runs
i think."

**5. Billing — overdraft envelope.**

New per-account setting `max_overdraft_credits` (signed
integer; default 0; Subscribed can raise within
`BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS` = 500 credits = $5
default cap). Spawn-start gate reframed from
"balance ≥ estimated cost" to two conditions:
`current_balance >= 0` AND
`estimated_spawn_cost <= current_balance + max_overdraft_credits`.
The last spawn of the cycle can dip the balance negative
within the envelope; next spawn waits for a top-up to clear
back to ≥ 0. No interest, no penalty fees; top-up first
clears the negative then adds headroom. Three new audit
ops: `overdraft_settings_changed`, `overdraft_consumed`,
`overdraft_cleared`. Dashboard surfaces signed balance +
envelope-used gauge when negative; gate footer on the
spawn that crossed zero.

Touches: `kb/design-billing.md` ("Zero-balance UX (and the
overdraft envelope)" section rewritten; tunable-knobs table
extended; three new audit ops; new lineage entry).

User intent: "we'll need to allow people to go below
credits... default would be set to 0, and if you balance
is 0 and above - you can run a cloud runner, but it will
make your balance negative for that last runner's price."
The user's `>= 1` → `>= 0` correction folded in.

**6. Conversation-id-propagation — ID-vs-context
clarification, `conversation_key` adoption.**

Two clarifications, no contract change to brnrd:

1. The plan is about **identity propagation only**. The
   daemon already injects rich context (kb/log tail + Task
   Context Bundle + 8 recent conversation records); this
   plan adds none of that.
2. `conversation_id` = the existing `conversation_key`
   string (e.g. `telegram:-1001234567890:`), not a new
   ULID. The implementation audit showed there's no
   bridge between the two today; adopting the existing key
   closes the gap at zero migration cost. The trailer is
   self-documenting in git logs.

Token-budget discipline (per-source byte/token budgets +
assembler enforcing total + per-source minimums) flagged
as a discipline to carry forward into future context-rich
features, **not** as a separate plan page. The user's
"i wouldn't add a new plan for prompt budgeting, it is
just something we need to be mindful I guess."

Touches: `kb/plan-conversation-id-propagation.md` (new
"What this plan is + isn't (clarified pass IV)" section;
slice descriptions updated; risks section updated to
reflect the conversation_key-is-deterministic shape).

### New decision page

**`kb/decision-websites.md`** — two distinct web properties
at two distinct URLs: **brr.dev** (OSS landing, no auth,
no payments) + **brnrd.dev** (hosted product, signup,
pricing, dashboard, billing portal). Cross-linking is the
trust signal: each acknowledges the other as a real
alternative, which makes the "we charge for ops, not for
crippled OSS" trust pitch *visible* rather than something
the user has to take on faith. brr.dev MVP is a static
landing page; brnrd.dev hosts the eight-view dashboard +
marketing pages.

User intent: "do we make two websites or one... I am
leaning towards the two."

### Files touched (15)

- **Locked accepts (Status flips)**:
  `kb/decision-connectors-layering.md`,
  `kb/decision-licensing-and-defense.md`,
  `kb/decision-monorepo-structure.md`,
  `kb/decision-pricing-shape.md` (already committed earlier
  in the pass via `e152b6a`); `kb/subject-managed-mode.md`,
  `kb/plan-managed-gates-launch.md`,
  `kb/plan-brnrd-dashboard-mvp.md` (extra-fluid framing),
  `kb/plan-failover-compute.md`,
  `kb/plan-env-fly-machines.md`,
  `kb/plan-kb-subcommand.md` (also `e152b6a`).
- **Substantive reshapes**:
  `kb/decision-cli-shape.md` (brnrd sibling binary +
  permission-prompt scope clarification),
  `kb/design-brnrd-protocol.md` (protocol-shape diagram +
  runtime profile),
  `kb/plan-laptop-daemoning.md` (reshaped for
  machine-scoped daemon + project registry),
  `kb/plan-daemon-deployment-templates.md` (aligned with
  machine-scoped daemon),
  `kb/design-config-layout.md` (per-branch overrides +
  last-spawned-branch + machine-scoped account scope),
  `kb/design-billing.md` (overdraft envelope),
  `kb/plan-conversation-id-propagation.md` (ID-vs-context
  + conversation_key + token-budget mindfulness).
- **New**: `kb/decision-websites.md`.
- **Breadcrumbs**: `kb/index.md`, `kb/log.md` (this
  entry), `kb/notes-pondering-fleet.md`.

### Why this lock-in matters

Locking pass IV is the pass where the **architecture
solidifies** — the per-project-vs-machine-scoped daemon
question was the load-bearing-but-hadn't-been-stated
ambiguity in everything from the protocol diagram to the
laptop install story to the config layout. Naming it +
locking the machine-scoped shape lets all of those
artifacts stop hedging.

The `brnrd`-as-sibling-binary and permission-prompt-
scoping clarifications are smaller but real wins on UX
coherence — the future cloud-only-features story now has
a place to land (one-time enablement consent), and the
per-account CLI doesn't have to live behind a prefix.

The overdraft envelope is the smallest spec change but
arguably the friendliest UX touch — it means subscribers
don't get a rejection at credit 301 when working through
a 300-credit grant, just a "you dipped into your envelope"
nudge.

The two-websites decision settles a question that was
going to come up no matter what; locking it now lets us
sketch the brr.dev MVP independently of the brnrd.dev
implementation slices.

Implementation cost over already-planned work: ~250 LOC
for the project registry + `daemon list|adopt|forget`
verbs in the laptop daemoning slice (already absorbed by
the async-runtime migration which is now in the same
slice); ~50 LOC for the overdraft envelope in the
ledger + ~30 LOC for the dispatcher gate; ~0 LOC for the
brnrd sibling binary (it's a `[project.scripts]` line +
the existing `brr brnrd` subcommand surface, reused).
Everything else is policy + organisational.

## [2026-05-26] implement | Linux systemd daemon install slice

Implemented the Linux side of the laptop daemoning plan: `brr daemon
install | uninstall | up | down | status | logs` now exists, with
top-level `brr up` / `brr down` kept as compatibility aliases. The Linux
installer writes the machine-scoped systemd user unit at
`~/.config/systemd/user/brr.service` with no `WorkingDirectory`, creates
the machine registry placeholder at `~/.config/brr/projects.toml`, wires
`systemctl --user` lifecycle commands, tails logs through
`journalctl --user -u brr -f`, and handles the `loginctl enable-linger`
prompt flow with noninteractive flags for CI / scripts.

Tests cover exact unit rendering, install / uninstall command selection,
linger marker behaviour, and CLI dispatch without invoking real systemd.
Updated `subject-daemon.md`, `plan-laptop-daemoning.md`, `kb/index.md`,
and `README.md` so the current state is explicit: the Linux service
wrapper has shipped, while the macOS LaunchAgent and the
machine-scoped multi-project runtime remain separate follow-up slices.

## [2026-05-27] implement | GitHub gate design pass — package split, ETag polling, review summaries

Three changes on the OSS GitHub gate that ship together as one design
pass; OSS/managed boundary documented at the same time so the
managed slice in [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
has a structural seam to lean on instead of re-implementing.

1. **Package split.** `src/brr/gates/github.py` (1.2k LOC monolith) →
   `src/brr/gates/github/` package with focused submodules:
   `client` / `paths` / `cache` / `parse` / `state` / `wizard` /
   `polling` / `delivery` / `progress` / `loop` / `constants`. Public
   API (`is_configured`, `run_loop`, `render_update`, `setup`, `auth`,
   `bind`, `parse_origin_url`, `autodetect_repo`, `resolve_token`,
   `GitHubAPIError`) re-exported from `__init__.py`. Daemon dispatch
   via `importlib.import_module` is unchanged. The interactive
   submodule is named `wizard` rather than `setup` because Python
   would shadow the submodule with the re-exported `setup` function.
2. **Conditional polling.** Every high-volume GET (`/issues`,
   `/issues/comments`, `/pulls/comments`) sends `If-None-Match` with
   the last ETag GitHub returned. 304 responses don't count against
   the REST rate limit, so quiet repos stop spending budget on
   polling. ETag store lives in gate state under `cursor.etags`
   (keyed by `method + path`) and self-heals when stale — a wrong
   cached ETag just trades one 200 for a fresh one. Cuts steady-state
   rate-limit consumption by roughly an order of magnitude on quiet
   repos.
3. **PR review summary events.** When a line comment surfaces a new
   `pull_request_review_id`, fetch the parent review once and emit a
   `pr-review` event if its summary body @-mentions us. New
   `github_kind: pr-review` carries `github_review_id` and
   `github_review_state` (APPROVED / CHANGES_REQUESTED / COMMENTED).
   Replies post via `/issues/{n}/comments` with a quote pointer at
   the review's HTML URL (GitHub has no dedicated review-summary
   reply endpoint; the plan's wording mentioned
   `/pulls/{n}/reviews/{id}/comments` but that's a GET-only list).
   Reviews are deduped via `cursor.seen_review_ids`. Standalone
   summary-only reviews (no line comments) remain undiscoverable by
   polling and fall through to the managed brnrd webhook path.

Reasoning for keeping the OSS gate sync `requests` rather than
swapping to `httpx` or a GH library: the 2026-05-22 decision to take
`requests` over stdlib urllib stands; reversing it for fewer deps is
aesthetic, not real risk reduction. `httpx`'s payoff (sync + async one
client) only matters for brnrd. GH App auth (`pyjwt[crypto]`,
`cryptography`) would bring native compile chains — disqualifies the
OSS gate from the "zero red flags for casual `pip install brr` users"
goal. App-auth and webhooks stay brnrd-side.

Boundary captured in
[`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md):
what OSS owns (PAT auth, three-trigger polling, single-repo binding,
response posting, live progress card), what brnrd owns exclusively
(GH App JWT minting, webhook receipt + signature verification,
multi-project routing, permission-prompt UX, hosted bot identity),
and the three modules both sides share (`paths`, `cache`, `parse`).
Brnrd's first commit will import these from `brr.gates.github`; if it
can't, the modules get refactored at that point — closes the
"reusable-core illusion" failure mode.

Deferred follow-ups (not in this slice): reactions-as-signal for the
permission-prompt UX (brnrd-side first; webhook delivery handles
reactions for free), webhooks for the OSS gate (require public URL
infra exactly the managed path is designed to remove), GH App auth
in OSS (native crypto deps disqualify it). Filed in the design page's
"Deferred follow-ups" section.

497 → 501 tests after adding ETag tests (4) and review-summary tests
(4). PR #62 fix branch (`fix/github-pr-review-comment-mentions`) is
the prerequisite this branch stacks on.
## [2026-05-26] implement | macOS LaunchAgent daemon lifecycle slice

Added the macOS side of `brr daemon install | uninstall |
status | logs`: `src/brr/daemon_install/macos.py` renders the
machine-scoped `dev.brnrd.brr` LaunchAgent, writes it to
`~/Library/LaunchAgents/dev.brnrd.brr.plist`, creates the
machine registry file at `~/.config/brr/projects.toml`, manages
`launchctl bootstrap | bootout | kickstart`, and tails
`~/Library/Logs/brr/brr.out.log` plus `brr.err.log`. The plist
intentionally has no `WorkingDirectory`, matching
[`plan-laptop-daemoning.md`](plan-laptop-daemoning.md); the broader
multi-project daemon runtime remains a later slice, so this commit
keeps the service lifecycle faithful to the accepted unit shape
without pinning it back to one repo.

CLI wiring adds the noun-first `brr daemon ...` surface while keeping
the existing `brr up` / `brr down` aliases. README now points macOS
users at `brr daemon install` and notes the first-run network prompt.
Tests cover plist generation, no-`WorkingDirectory`, launchctl command
construction, log tailing, registry reads, and CLI dispatch; full suite
passed with 487 tests.

## [2026-05-27] fix | kb daemon lifecycle cleanup after macOS slice

Reconciled the kb after the macOS LaunchAgent implementation: the
daemon hub, laptop-daemoning plan, index, and fleet pondering notes now
state that Linux systemd and macOS LaunchAgent service lifecycle both
ship, while the registry-aware multi-project runtime and
`brr daemon list|adopt|forget` remain future work. Also compressed
`notes-pondering-fleet.md` from a running reframe chronicle into a
provenance map, compressed the daemon deployment template lineage to
current state, marked the `brr kb` plan as accepted-but-not-started,
and added a peer link to the two-websites decision from the
managed-mode hub.

## [2026-05-27] decision | Stewardship lodestar — user friendliness + operational simplicity

Added a "lodestar" paragraph to `AGENTS.md` → Stewardship naming the
two values that orient every trade-off: **user friendliness** (how the
change lands on someone encountering the result for the first time) and
**operational simplicity** (what it costs to run the result and keep it
healthy). Positioned them as the source the other instincts in the file
derive from — slash old shape, prefer better abstractions, don't paper
over weak ones — and as the fall-back when a decision feels finely
balanced. Codifies what was a recurring per-prompt reminder.

Kept the wording universal in shape rather than brr-specific, since
Stewardship ships to adopters via `brr init` and the two values
generalise to nearly any project; bumped the playbook `Revision:` date
to flag the structural change for cached workspace-rule injections.

Refined the same day (see next `decision` entry) — the "derive from
those two" framing turned out to overclaim across categories.

## [2026-05-27] decision | Refine Stewardship lodestar — drop "everything derives from these"

Followup to the lodestar addition (commit e00a469). Dropped the "Most
of the other instincts in this file derive from those two" framing and
scoped the sentence to "what we build" rather than "every trade-off".
The two foundational values stand cleanly without a primacy claim; the
rest of the Stewardship section is mostly *process discipline* (engage
with the request, surface contradictions, read callers, slash dead
shape) — a sibling category, not derivative. Naming a third lodestar
("honest assessment") would have conflated product values with process
virtues and diluted the punch. Final wording:

> Two values orient what we build: **user friendliness** — how the
> change lands on someone encountering the result for the first time —
> and **operational simplicity** — what it costs to run the result and
> keep it healthy. When a decision feels finely balanced, fall back to
> them.

Landed in commit 5ab92b3 alongside the adopter-lens cleanup below
(autocommit grouped both AGENTS.md edits under a single generic
message; this entry captures the rationale the commit message omits).

## [2026-05-27] fix | AGENTS.md adopter-lens cleanup — strip brr-internal leaks from universal sections

Re-read `AGENTS.md` and `prompts/setup.md` with adopter eyes (they
receive this verbatim via `brr init`), found five brr-specific leaks
in sections that are supposed to be universal:

1. **Lineage-breadcrumb example** used brr-internal `_push_if_needed` +
   `brr/*` namespace — replaced with a generic HTTP-retry example that
   illustrates the same shape.
2. **Subject-page area examples** listed brr's own subjects ("envs,
   gates, daemon loop, conversations, kb itself, runners") — replaced
   with category-level examples ("a subsystem, a cross-cutting concern,
   an external integration, the runtime entrypoints, the build system").
3. **Cross-link discipline** mentioned "brr's preflight" — generalised
   to just "orphan pages should not exist for long".
4. **`brr kb` blockquote** at the end of Knowledge base → Health checks
   advertised brr's own future tooling with a hard link to
   `Gurio/brr/issues/41` — deleted; the forward-tracking lives in
   [`plan-kb-subcommand.md`](plan-kb-subcommand.md) and adopters have
   no reason to see it.
5. **`prompts/setup.md` universal-section list** was stale: included
   "Work re-review" (folded into Workflow → Orientation per the
   2026-05-16 restructure) and omitted "How to read this playbook"
   (clearly universal, named in AGENTS.md → Constraints). Synced.

Items 1-4 landed in commit 5ab92b3 (autocommit alongside the lodestar
refinement); item 5 committed in the wrap.

Defensible-but-heavy items noted and left in place — both arguably
adopter-irrelevant, but both already justified by current policy
recorded in [`decision-kb-shape.md`](decision-kb-shape.md):

- "How to read this playbook" carries 40+ lines of stage-distinguishing
  prose mostly about brr's orchestrator stages; an adopter who doesn't
  run `brr up` reads heavy conditional framing for marginal benefit.
- "When the brr daemon runs you" (48-line subsection) is explicitly
  brr-internal but justified per current Constraints policy ("adopters
  keep it because their playbook may be read by a brr daemon").

Worth revisiting if adopter ergonomics ever become a priority concern.

Full test suite (493 tests) passes; the two tests that pin "Stewardship"
in the bundled prompts are unaffected.

## [2026-05-27] fix | docker runner ergonomics from three agent reviews

Three recurring complaints from recent runner ergonomics reviews
distilled into two real issues plus a design question:

1. **`pytest` "missing" on cold tasks.** Two reviews ran
   `pip install -e ".[dev]"` before testing. The Dockerfile *does*
   pin pytest (commit `cdb9ccd`), but the user's local
   `brr-runner:dev` was built 9 days before that commit landed —
   stale image, image-mtime never compared to Dockerfile-mtime.
   Documented behaviour; no code fix beyond reminding users to
   rebuild. The orientation pass would benefit from a
   pre-task image-mtime check, but that's a separate slice.

2. **Even after install, agents had to call `python -m pytest`**
   because pip's user-mode install (forced by the container
   running as host UID with no write access to system site-
   packages) lands scripts in `/brr-home/.local/bin`, which
   wasn't on `PATH`. Fix: `ENV PATH=/brr-home/.local/bin:$PATH`
   in the Dockerfile. One line, makes every freshly-installed
   console script (`pytest`, the project's own `brr` entry
   point, ruff, etc.) reachable by name. Test added
   (`test_bundled_runner_image_exposes_user_local_bin_on_path`).

3. **`gh auth status` exits non-zero inside the container even
   when `GITHUB_TOKEN` is injected.** Root cause: on Linux gh
   stores its OAuth token in the system keyring; `~/.config/gh/
   hosts.yml` only carries the account name. brr was bind-
   mounting `~/.config/gh` into the runner, but the keyring
   isn't reachable across the container boundary — so gh sees
   a stale account it can't authenticate. `gh pr`, `gh api`,
   etc. all worked via the injected `GITHUB_TOKEN`, but the
   broken stored account in `gh auth status` output kept
   confusing agents into thinking auth was broken. **Fix:**
   dropped `.config/gh` from `_DOCKER_DEFAULT_CRED_PATHS` and
   made the GitHub-token resolver run on **every** docker task
   (not just `source == "github"`), so any cross-source task
   that wants to touch GitHub has a working `gh` and HTTPS
   `git push`. With the mount gone, the resolver is the sole
   path, hence the universal scope. Verified end-to-end:
   `gh auth status` now exits 0 inside the runner, reports
   `Logged in to github.com account Gurio (GITHUB_TOKEN)` as
   the single active account.

Lessons:

- **Cosmetic ≠ ignorable** when agents are reading the output
  and reasoning about next steps. Wasted reasoning costs tokens
  and clouds context. The original "don't fix it" framing got
  pushed back on appropriately.
- **The bind-mount fallback was load-bearing in the wrong
  direction.** It was added as the "easy" path for gh auth but
  silently broke for the majority Linux setup (keyring backend).
  Explicit token injection is both cleaner and more reliable.
- **Scope creep is OK when the previous scope was a fiction.**
  The pre-fix code only resolved a token for `source ==
  "github"`, but the mount-based fallback already gave non-
  github tasks access to the same credentials in a broken form.
  Moving to universal injection isn't expanding scope; it's
  making the existing access actually work.

Companion question for the interactive-init story (issue #24):
the playbook should walk users through GitHub credential
setup explicitly — probe `gh auth token`, offer to install/auth
gh, walk PAT creation with sensible scope defaults, validate
the result — because relying on `gh auth token` to silently
just work has the keyring sharp edge documented above. Comment
posted on #24 with the proposed playbook steps.

Open question for the runner-image story (separate from this
fix): the bundled image carries Python-specific tooling
(`python3-pip`, `python3-venv`, baseline pytest pin) primarily
to support brr's own dogfooding. That's a smell for a
"generic" runner image meant to host Rust, Go, TS, etc.
projects. Two plausible shapes — strict-minimal base + per-
project layered images, or a tiered family
(`brr-runner` / `brr-runner-python` / …) — discussed in the
chat thread; not settled, no kb design page yet because the
question is still in the "do we even want to pay the
maintenance cost" stage.

Files touched: `src/brr/Dockerfile`, `src/brr/envs/__init__.py`,
`src/brr/docs/envs.md`, `tests/test_dockerfile.py`,
`tests/test_envs.py`. No new kb pages; this entry is the
synthesis.

## [2026-05-27] design | Agent ergonomics observability — back-channel design

Triggered by the user's "what should we *generally* do about
agent ergonomics issues" question following the docker-runner
fix above. The current shape (`runner.self_review` config that
injects `prompts/self-review.md` to make agents append a free-
text **Ergonomics review:** footer to their stdout response)
worked as a stop-gap but is structurally wrong for what brr is
becoming:

- Signal rides in user-visible chat output → pollution for
  users, useless for managed-mode (brnrd) where the operator,
  not the user, needs the data.
- No structured form, no storage, no aggregation. Three agents
  in a row reporting the same `gh auth status` confusion is a
  pattern the system never noticed.
- All-or-nothing toggle; no sampling.
- Agent-only signal; lots of friction the daemon could detect
  deterministically (image staleness, missing tools, auth
  resolvability) just isn't checked.

New page: [`kb/design-agent-ergonomics.md`](design-agent-ergonomics.md).
Proposes a back-channel observability surface with three
producer layers (deterministic **probe**, deterministic
**telemetry** piggybacking on `run_progress`, sampled
agent **reflection**), one canonical `Record` shape, and a
pluggable **ergo proxy** abstraction (`ErgoProxy` Protocol with
three concrete impls: `NullErgoProxy` / `LocalErgoProxy` writing
JSONL / `BrnrdErgoProxy` posting batched HTTPS). The name is a
nod to the 2006 anime; the role — proxying ergonomic observations
from producers to operators, opaque to both sides — fits cleanly
enough that the pun pays for itself. Tenancy decides which
proxy: self-hosted defaults to `NullErgoProxy` with opt-in to
`LocalErgoProxy` (+ a `brr ergonomics` CLI) or to brnrd's
improve pool; managed-mode routes to `BrnrdErgoProxy`
unconditionally, suppresses the reflection footer from user
output entirely, and surfaces project + fleet ergonomics views
in the dashboard (user-scoped vs operator-scoped).

Reflection capture nailed down: the existing `self-review.md`
free-text footer becomes a marker-delimited block
(`<!-- BRR_ERGONOMICS_START --> … <!-- BRR_ERGONOMICS_END -->`)
the daemon parses out and strips before the response reaches the
gate. HTML comments fail invisibly in markdown renderers, so the
failure modes are deliberately asymmetric: missed-parse skips the
record but leaves the response intact; leaked-marker shows the
user a stray invisible comment but never strips real response
content. Sampling stays daemon-side (the agent always gets the
nudge when injected); the daemon decides per-task whether to
inject based on `ergonomics.reflection_sample_rate` plus forced
overrides on retry / probe-error.

Why design this now (vs after brnrd ships): the brnrd protocol
needs to know about the ergonomics endpoint slot, the dashboard
MVP plan needs to know an ergonomics view is a follow-up slice,
and the wire format needs the same record shape for both
tenancies so the producer code is proxy-agnostic from day one.
Locking the shape ahead of implementation avoids retrofit cost.

Implementation footprint sketched (~600 LOC for daemon-side
ergo proxy + probes + CLI; ~600 LOC for telemetry + reflection
+ `BrnrdErgoProxy` + endpoint; ~600 LOC for the dashboard views).
Sliced so probe layer alone (the highest-leverage piece) is
shippable independently.

Index updated under "Architecture & orientation" with a
substantive blurb. Cross-links added from
[`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
("Out of scope for MVP" → ergonomics views deferred to a
follow-up slice) and [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
("Out of scope" → ergonomics ingestion endpoint joins as
`POST /v1/daemons/ergonomics` when the `BrnrdErgoProxy` slice
lands).

Open questions left explicit on the design page: default
`reflection_sample_rate` for self-hosted vs managed, the
minimal safe redaction surface for the brnrd pool, interaction
with `.brr/traces/`, subject-hub timing (premature today;
revisit once probe + ergo proxy slices land).

## [2026-05-28] design | diffense (PR review experience) kickoff

Opened the design for **diffense**, brr's kb-first PR review
experience, in [`design-diffense.md`](design-diffense.md)
(`Status: proposed, not yet accepted`). Motivated by a dogfooding
pain: reviewing brr-generated PRs in a generic forge diff view is
hostile to how brr works, because ~half of a typical PR's value lives
in `kb/` changes that read poorly as raw diff and well as rendered,
cross-linked Markdown. The reviewer's real job isn't reading — it's
fitting the diff into a mental model and cross-referencing scattered
context items; the tool should do that packing and present a navigable
surface, not another wall of text.

What converged (the proposed cornerstones): an **inspect-mode card
model** — reviews are a navigable graph of cards in three first-class
kinds (item / walkthrough / uncertainty), each with always-present
axes (identity, kind, descriptive lore, stat block, provenance) and
emit-iff-honest conditional axes (possibility lore, before/after,
lateral edges, usage demo, exercising-tests link). **Two-axis lore**
(what-it-is + what-it-enables, the game-menu "perceived gain"
register) and **load-bearing per-kind stat blocks** make a card
skimmable-then-divable. **Tests are grounding evidence** for honest
usage demos (real values, not invented); test-add and walkthrough
cards lean on them hardest. A **two-layer architecture** separates a
JSON **review pack** (generated by the runner at publish time, the
contract) from multiple **rendering targets** over one proposed
**Textual** substrate — same component model as TUI and `textual
serve` web, plus a forge PR-body projection as the v0 surface and a
future brnrd hosted view. **Six discipline clamps** (sharp / helpful /
honest / non-prescriptive / emit-iff-honest / substrate-honest) keep
it Occam's-razor sharp and guard against the wall-of-text relapse.

The sharpest late addition: **agent uncertainty as first-class,
top-of-reading-order output**. Tasks arrive half-defined; agents
assume, hit dilemmas, and spot upstream concerns mid-run. diffense
expects the agent to surface those as uncertainty cards (assumption /
concern / dilemma / out-of-scope-flag) read *first* — a pack that
always reads "everything is clean" can't be honest. Named `diffense`
(diff+sense / diff+defense) as a working name over cosplay-leaning
`pensieve` / `holocron`. Promoted straight to a design (not a
research page) because the cornerstones held across five refinement
passes; research dimension preserved inside as "Alternatives briefly
considered." Stays open: pack JSON schema (after a hand-authored
prototype against a real PR), the Textual substrate spike, aesthetic
locking, and the in-tree-vs-own-package project boundary. Companion
to [`plan-kb-subcommand.md`](plan-kb-subcommand.md),
[`design-publish-kernel.md`](design-publish-kernel.md), and
[`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md); the
human-side counterpart to the 2026-05-27 `pr-review` gate event work.

## [2026-05-29] design | diffense pass 6 — zoomable graph, feedback loop, web-first

Reshaped [`design-diffense.md`](design-diffense.md) after a deep review
pass (the user read two parallel drafts and brought ~12 discussion
points). Most collapsed into one structural upgrade plus three additions
and one reversal; folded all in. Status stays `proposed`.

**The structural unlock: cards become a *zoomable* graph, not just a
graph.** Two navigation axes now — lateral (peer edges, as before) and
**zoom** (each card descends gloss → summary levels → a ground-truth
leaf: the real diff / rendered kb page / code at a locator). This single
model absorbs four separate asks: the kb "tree of summarized info"
(zoom levels on `kb-page-edit`), walkthrough-as-a-group-of-cards
(walkthrough is now a *composite card* whose zoom reveals ordered member
cards), "cards-as-graph needs structure," and the Marathon
glance/dive/wander experience. Two properties make it load-bear: honesty
is structural (you can always zoom past a summary to ground truth) and
token cost is bounded (summaries are LLM-authored and small; leaves are
mechanical, not generated).

**Three concrete additions.** (1) **Code locators** — every card that
mentions a code item carries a resolvable locator (commit-pinned forge
permalink + local `path:line`); rich renderers open it inline as the
zoom leaf, minimal renderers link out. (2) **A pack validation/render
tool** — `brr review --check` schema-validates, clamp-lints, and
dry-renders the pack before publish (a compile step for the review
artifact), folded into the runner's self-review. (3) **The feedback
loop** — diffense composes the *already-shipped* `pr-review-comment`
gate: flag a card → diffense authors a forge comment anchored at the
card's locator → gate turns the mention into a task → agent iterates,
commits, re-emits the pack → surface re-renders. The live agent is the
ephemeral *ask* shortcut; durable change-requests ride the real loop.
Uncertainty cards gained **tension references** (point at the conflicting
parts — most often the input prompt: shallow task, false implication,
code contradicts the assumed model) and a new **`follow-up` subkind**
(near-future work that would maximize the change's value, held out of
scope), reconciled against the non-prescriptive clamp (foresight about
*next work* is allowed; prescribing interpretation of *this change* is
not).

**The reversal: web-first, not text-first or TUI-first.** The mobile
requirement (the user reviews from a phone a lot; no native app; use
existing tech) breaks the earlier "one Textual substrate serves TUI +
`textual serve` web" hope — terminal-in-browser is wrong on a phone. So
the web renderer is now **responsive HTML** (the brnrd-dashboard stack),
a *distinct* renderer from the Textual TUI, both over the shared pack;
pack-as-contract is what makes two renderers affordable. The PR body
demotes from "v0 we build first" to a **lossy fallback projection**. The
recommended build order is pack + `--check` → responsive web (served
locally by `brr review` or hosted by brnrd) → PR-body falls out → TUI
follows. Honest hard case stated plainly: good mobile review for a
self-hoster without brnrd needs a tunnel or the degraded PR-body path;
brnrd's hosted renderer is the clean mobile answer.

**Packs live** in `.brr/diffense/<pr>/` locally and travel with the PR
via the forge (HTML-comment marker block reusing the ergo proxy's
technique, git-note/ref fallback for size); brnrd stores server-side.
**Ergo proxy fold:** shared source (one agent run-time reflection),
split audience — diffense renders the change-relevant slice for the user
(uncertainty cards), ergo routes the capability-relevant slice to the
operator; they overlap exactly on task clarity; share the
reflection-elicitation prompt step + marker transport, but don't merge
(audience and subject differ).

Process note: the working-tree copy of `design-diffense.md` (and earlier
`index.md`) keeps getting mangled by an editor format-on-save extension
that wraps `[text](url)` links in backticks and strips list-continuation
indentation — not a git hook; committed copies are clean. Worth chasing
the editor extension down separately.

## [2026-05-29] design | diffense pass 7 — drop the near-term TUI, lock web-first

Refined [`design-diffense.md`](design-diffense.md) after a review of the
pass-6 shape. Three decisions and two thinking-questions, all folded in;
Status stays `proposed`.

**Drop the near-term Textual TUI.** Pass 6 carried two renderers (Textual
TUI + responsive web) over the shared pack, flagged as a "weakened but
affordable" split. Pass 7 drops the TUI from the first cut entirely,
which *resolves* the substrate tension rather than carrying it: build
**one** light, brnrd-independent responsive-web renderer; a CLI/TUI is a
clean follow-up over the same pack. The terminal aesthetic survives —
expressed in the web medium as ascii/terminal-*looking* cards, not an
actual terminal. Accepted tradeoff: a local web tool is slightly more
friction than a TUI for a terminal-native self-hoster, closed later by
the CLI follow-up.

**Build before brnrd, keep it light.** diffense ships first, for the
self-hosting story, and must not depend on brnrd or absorb its
complexity — a small minimal-dependency web app over the pack; brnrd
later renders the same pack without diffense knowing it exists. The
hosted brnrd renderer is the eventual clean mobile-without-a-tunnel path;
near term, phone review of a self-hoster is LAN/tunnel to the local
server or the PR-body fallback. Updated the architecture diagram,
renderers table, "Renderers", "Project boundary", and "Surfaces and what
to build first" to match; "what to build first" moved from an open
question to a settled decision (web-first).

**Concrete zoom interaction.** The web rendering of the zoom axis:
ascii-looking cards where **opening a nested card collapses its parent to
a full-width heading bar, nesting indefinitely** so the path from root
reads as a breadcrumb stack of heading bars. Two interaction parts left
explicitly open (resolved by the spike): lateral / inter-card (graph)
navigation, and what a code leaf looks like when opened at its locator
(inline highlight vs side-by-side diff vs jump-to-forge).

**Thinking-questions answered, captured as design intent.** On "making
review entertaining": framed in "Experience principles" as removing
*accidental* burden (scattered context, wall-of-text, no signal, no
agency) so the *irreducible core* — the judgment call, which carries the
stakes that make a thing engaging — is what's left; gated on two honesty
constraints (enjoyment is downstream of trust; the aesthetic is a
multiplier, not the substance). On naming: `diffuse` (a frequent typo)
rejected on meaning — it means *scatter/dilute*, the opposite of a tool
that concentrates context; and the typed verb is `brr review`, so
codename ergonomics don't load-bear. On "would've been easier as a PR
description": agreed text-first is the wrong *anchor*, with the
refinement that the PR-body projection ships anyway as a near-free
fallback and a forcing function (it makes us generate a real pack against
a real PR early), just not the ceiling.

Next concrete move unchanged: hand-author a pack for one real recent brr
PR to pressure-test the schema, then the web-renderer spike.

## [2026-05-29] implement | diffense prototype pack for PR #64 (schema pressure-test)

Hand-authored the first diffense review pack against a real PR to
pressure-test the [`design-diffense.md`](design-diffense.md) card schema
before it locks. Artifacts:
[`diffense-prototype-pr64-pack.json`](diffense-prototype-pr64-pack.json)
(the contract instance) +
[`diffense-prototype-pr64.md`](diffense-prototype-pr64.md) (cards
rendered as ascii, plus the findings).

Chose [PR #64](https://github.com/Gurio/brr/pull/64) (`fix: poll GitHub
PR review comments`, 2642+/1221−, 23 files) because it braids three
stories — the fix, a 1052-line-monolith→12-module-package refactor, and
a conditional-polling+review-events feature — plus a new kb design page.
That braid is the stress; it's also the exact `pr-review-comment` gate
code diffense's own feedback loop rides. Ten curated cards stand in for
23 files: 3 uncertainty subkinds (concern/out-of-scope/follow-up), a
walkthrough (the round-trip fix), and item cards across code-fn-new,
code-fn-edit, the new code-module-split kind, kb-page-new, and test-add.
Grounded every card in the real repo (read polling/cache/parse/delivery/
client/constants/__init__ + the test names + the new kb page).

Validated the pack with a throwaway `python3` script standing in for the
designed `brr review --check`: JSON well-formed, reading_order maps,
every locator's file exists and line is in range, card-id edges resolve.
All passed.

What the schema got right: curation held on a big PR (ten cards, not a
1:1 hunk dump); leaves-by-reference kept the pack ~430 lines of JSON for
a 3863-line diff; two-axis lore and uncertainty-first reading order
earned their place; the `kb-page-new` "inbound-links 0 → 6" stat is the
concrete kb-aware advantage.

Findings that fed back into the design (folded into "Open questions →
Pack JSON schema" + the item-kinds list): (1) **a `code-module-split` /
`code-move` kind is missing** — a 1052→12 split has no honest home among
per-function kinds; added it. (2) **`--check` resolving locators is
load-bearing** — the same check would have rejected the design's mock
`cache.get_with_etag` (the real ETag logic is `client._request
(etag_store=…)`; the mock invented a symbol). (3) **edges need
`{card|locator}` targets**, not free-text, so non-carded peers stay
resolvable. (4) **uncertainty cards need an `honest_nuance` slot** —
grounding forced the seen-cap concern (`sorted(seen)[-_SEEN_CAP:]`, 7
sites, cap 500) down from the design mock's overstated "could re-surface
any handled comment" to its true narrow bound (only an *edited* old
comment past the cap on a busy PR, since the `since` cursor is
belt-and-suspenders). (5) provenance's `conversation_msg` is the one
field a hand-authored pack can't exercise — next prototype should run on
a brr-*produced* PR. Net: the schema survived contact with a real,
messy, braided PR and is sharper for it; none of the findings block the
design.

## [2026-05-29] design | diffense pass 8 — open taxonomy, summary card, gloss-first ease-in

Folded a round of review on the PR #64 prototype render back into
[`design-diffense.md`](design-diffense.md) and reshaped the prototype
([`diffense-prototype-pr64.md`](diffense-prototype-pr64.md) +
[`pack.json`](diffense-prototype-pr64-pack.json)) to match. Five changes,
all from the user reviewing the rendered cards:

**Open card-kind taxonomy.** The kind set is now an *open core, not a
closed enum*: the agent may declare a `custom` kind inline (named, with a
one-line "why," degrading to a generic card) and is expected to **raise
the gap as a meta uncertainty card** — a new framing where uncertainty
points *inward* at the representation (`the change ⟂ the representation`),
not just at the code. Recurring custom kinds get promoted;
`code-module-split` is the first to make that round trip (provisional in
pass 7 → promoted to core here). The taxonomy self-improves from use, the
way the kb does.

**Summary card + reading-order ease-in.** A reviewer dropped straight
onto the sharpest, most specific concern (`sorted(seen)[-_SEEN_CAP:]`,
×7) is jarring even when the concern is right. Added a **summary card**
that opens every pack (the on-ramp), and reframed "uncertainty first" as
**orient → surface-concerns → explore**: the summary is tiny, names the
concern count + severity (so nothing's buried), and points at the
concerns. This also absorbed the "there should be a header / PR stats but
they're contextless" instinct — the summary is numbers in service of a
shape (arcs, surface area, risk pointer), never raw +/−.

**Gloss-first, esp. uncertainty cards.** Made explicit that *the gloss
leads* — every card's first human-readable line is the plain-language
gloss; id / locator / tension descend. The prototype's own first
uncertainty render had violated this (led with
`id` / `tension` / `where:polling.py:283` before stating the worry in
words); fixed it, and used the slip as the worked cautionary example in
the design. Uncertainty cards gained a `headline` field.

**`--check` reword.** "Resolution is a hard gate, not a nicety" confused
the user; reworded to plain terms — an unresolvable locator is a
*blocking* failure (non-zero exit, publish refused), because a card
pointing at a non-existent symbol is lying. Same teeth, clearer words.

**Productization noted, not chased.** Added a short design aside: strip
the kb-specific parts and the code-change-card format generalizes to any
PR review on any repo; the code cards alone carry most of the value, and
brr's kb-awareness is the unfair advantage, not a prerequisite. Deferred
explicitly so it doesn't pull focus from dogfooding.

**Code gap filed (not a design change).** Surfaced that brr currently
publishes a *branch*, not a PR, and doesn't thread the originating issue /
Telegram message onto it — so even brr-authored PRs (#36 from a ticket,
#17 from Telegram) may carry no origin context, which is exactly what the
prototype's `provenance.conversation_msg` finding needs. Filed as
[#68](https://github.com/Gurio/brr/issues/68) (sub-issue of release
readiness #23); adjacent to #61 (conversation_id propagation).

Reshaped pack re-validated with the same `brr review --check` stand-in
(11 cards now incl. the summary; reading_order maps, locators + edges
resolve). Status stays `proposed`; the remaining gate before it flips is
the web-renderer spike.

## [2026-05-29] implement | diffense renderer spike — read model validated, two open questions closed

Built the web-renderer spike the design named as its last gate, in
[`src/brr/diffense/`](../src/brr/diffense): a generic, dependency-free
renderer (`template.html` — HTML + CSS + vanilla JS) plus `render.py`, a
stdlib-only inliner that embeds a pack into the template to produce a
self-contained HTML page (the seed of `brr review`'s render step).
Generated `review-pr64.html` from the hand-authored
[PR #64 pack](diffense-prototype-pr64-pack.json) and verified it end to
end with headless Chrome — index view, a focused uncertainty card, the
walkthrough (with members), a code card, and a 390px phone width.

**Resolved the two interaction questions pass 7 left open.**

- *Inter-card / graph navigation:* lateral edges and zoom-drills (a
  walkthrough's members) both **push onto one breadcrumb heading-bar
  stack**; within-card zoom (gloss → L1 → leaf) is in-place disclosure,
  not a push. One stack for both axes beat a separate graph view — the
  simplest model that never loses your place.
- *Code rendering at a locator:* **jump-to-forge at v0** — the leaf opens
  the commit-pinned permalink ("open ↗") with `path:line` inline;
  inline-diff deferred (upgrading it never touches the pack).

**Terminal aesthetic carries to the web and reflows.** The look is CSS
(monospace, line-drawn borders, low-key palette, per-kind accent stripe),
not literal box-drawing, so cards reflow on a phone — stats stack
key-over-value, chips wrap, the demo block scrolls. Summary card renders
first, then concerns (headline-first), then the change, matching the
orient → surface-concerns → explore order.

**Two render-only bugs found and fixed** (in the renderer, not the model):
a breadcrumb label and edge chips forced horizontal overflow on narrow
viewports (flex items with default `min-width:auto` won't shrink) — fixed
with `min-width:0` + `overflow-wrap`. A measurement detour confirmed the
remaining clip was a `--headless=new --screenshot` artifact (it lays out
at ~485px but captures the narrower `--window-size`), not a CSS defect;
switched to driving Chrome over CDP (Node's global `WebSocket`) for
faithful device metrics + full-page captures.

Render-only by design: the flag-a-card action, the local `brr review`
server, and runner/publish wiring are not in it. With the read model
validated, [`design-diffense.md`](design-diffense.md) flips to
**accepted** (both gates — prototype pack + renderer — now met) and the
in-tree `src/brr/diffense/` boundary is settled (zero runtime deps).

## [2026-05-31] design | diffense pass 10: state/data/invariant axes, visual entry stats, data-trace, transport correction

Post-acceptance format refinement of [`design-diffense.md`](design-diffense.md),
demonstrated in the PR #64 prototype + live renderer. Five threads:

- **Invariant axis + data-shape delta.** Cards now carry an **invariant
  frame** — what the change holds constant (the reference a delta is read
  against); a *threatened* invariant is exactly what an uncertainty card's
  tension points at, which unifies two previously separate ideas. The
  stats split **state shape** (control/behaviour) from **data shape**
  (types/schema/event kinds), with a caveat that the cleaner cut than
  "code=state, tests=data" is **possible vs actual** (code is the grammar,
  tests are the sentences — which is why tests-as-grounding works).
- **Entry stats as rolled-up visual distributions.** The summary card's
  stats are now an *aggregation of the per-card axes* (change-kind mix,
  surface/contract delta, invariants, data-shape, cost axes before→after),
  rendered as **bars / meters / heat** to imprint pre-attentively rather
  than be read. Raw size is demoted — it is the one stat that is *not* a
  rollup, hence least useful.
- **Control-trace vs data-trace walkthroughs.** A walkthrough can **follow
  the datum** (shape at each hop), not just the execution path — the data
  flow is precisely what a diff can't show. Structured as ordered steppable
  stages so **animated data flow** is a renderer-only upgrade, not a
  re-author (promoted from a deferred "GIF" to a named direction; motion is
  pre-attentive in a way printed before/after is not).
- **kb-native axes.** kb changes were reading flat because they were
  flattened code cards. Their native shape is a **graph**: claim delta /
  graph-position (inbound-link delta, hub, orphan check) / lifecycle. The
  kb counterpart of a data-trace is **walking the link graph**.
- **Transport correction.** Removed a sibling-drift bug: the page had said
  brnrd *stores* the pack server-side, contradicting
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md)'s "data ownership
  stays at the metadata-graph level" stance. brnrd is now a **transient
  relay, never a pack store**; the pack stays the producer's
  (`.brr/diffense/<task>/`, task-keyed not PR-keyed), the user-published PR
  body is the durable forge artifact. Zachtronics (cost-axis distributions,
  visible data-in-motion) added as a stated inspiration alongside Souls/DMC.

Renderer (`template.html`) gained visual stat widgets (segmented bar /
meter / before→after delta / heat dots / tags), an invariant block, a
data-trace stage view with `↓` flow connectors (`[data-stage]` hooks for
future animation), and kb-axis rendering; verified via CDP screenshots
(desktop summary panel, data-trace, threatened invariant, kb card, 390px
mobile). 510 tests still green.

## [2026-05-27] implement | brnrd inbox-as-service spine (first slice)

Built the first executable slice of `src/brnrd/`, the prototype that
unblocks [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md).
Sequenced in [`plan-brnrd-inbox-prototype.md`](plan-brnrd-inbox-prototype.md).

- **Backend** (`src/brnrd/`, FastAPI + SQLAlchemy/SQLite, AGPLv3 per
  [`decision-monorepo-structure.md`](decision-monorepo-structure.md)):
  accounts + initial API key, sessions, projects (idempotent on name),
  a device-flow connect handshake (`POST /v1/accounts/pair` →
  account-approve → poll until paired, minting a project-scoped daemon
  token), and the daemon-facing loop — `register`, long-poll
  `inbox?since=&wait=`, `responses`, `deregister`. A `_dev/enqueue`
  ingress stands in for the real Telegram/GitHub webhooks so the
  queue/drain/respond loop is testable end-to-end.
- **Data minimization, made concrete.** `POST /v1/daemons/responses`
  records only metadata (status / length / latency-ms) and hands the
  body to a `Forwarder` seam (no-op in prod default, capturing list in
  tests, the platform post in production) — the response body is never
  a column. The inbound task body is dropped once answered. This is the
  "you own your data" stance from
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) enforced at the
  schema level.
- **Code reuse (the operator's ask).** Extracted
  `src/brr/gates/runtime.py` — gate state files, per-task progress-card
  state, the backoff loop, and the `list_done → deliver → cleanup`
  delivery skeleton — and migrated the Slack + Telegram gates onto it as
  thin delegators (their suites stayed green unchanged; the per-platform
  chat/thread resolution moved into each gate's `deliver` closure). The
  new `cloud` gate is then a thin wrapper on that runtime plus an HTTP
  seam; `brr brnrd connect` runs the device flow; `cloud` joined
  `_BUILTIN_GATES`. The webhook/PR-shaped GitHub gate stays out of the
  extraction (different protocol) — a noted later candidate, not forced.
- **Bootstrap.** `pyproject.toml` gained a `backend` extra (folded into
  `dev` so the suite exercises brnrd) + explicit src-layout package
  discovery; per-package `LICENSE` files (`src/brr/` MIT, `src/brnrd/`
  AGPLv3) and a root `LICENSE-OVERVIEW.md`.

Tests: 14 new (round trip, long-poll timeout + wake-on-enqueue, cursor
idempotency, project isolation, token-kind 401/403 scoping, pair
secret, metadata-only response, idempotent project create; cloud-gate
connect + drain/deliver + cursor-resume + orphan-skip). Full suite 524
green. Open follow-ups: real webhook ingress + signature verification,
project caps / subscription tiers, the dashboard, drop-queued-body-
after-ack, threading the runner's real response status.

## [2026-05-31] implement | brnrd slice 2: Telegram webhook ingress + thin approve dashboard

Second slice of [`plan-brnrd-inbox-prototype.md`](plan-brnrd-inbox-prototype.md),
turning the spine's producer half real and making `brr brnrd connect`
human-completable.

- **Telegram ingress.** `POST /v1/webhooks/telegram` — one managed bot
  multiplexed by `chat_id`, authed by the `setWebhook` secret-token
  header (constant-time compared, not a bearer). `/start <code>` binds
  a chat to a project (account issues the `TG-…` code via
  `POST /v1/accounts/pair/telegram`); a bound chat's message enqueues
  with an opaque `reply_to = {platform, chat_id, topic_id, message_id}`;
  an unbound chat is ignored. Bindings are global-unique on
  `(platform, chat_id)` so a chat can't be silently re-pointed across
  accounts.
- **Routing home without storing.** `inbox.make_default_forwarder`
  dispatches on `reply_to['platform']` and posts the runner's body back
  via the Telegram Bot API, threaded under the source message — the
  body is still never persisted. This replaces the no-op default
  forwarder; `_dev/enqueue` stays as a dev-only stand-in.
- **Thin dashboard** (`src/brnrd_web/`, its own AGPLv3 `LICENSE`,
  bundled by `brr[backend]`): web `/login` (session cookie) + the
  device-flow `/connect/{code}` approve page. It reuses the API's exact
  paths — `approve_core` (factored out of the approve endpoint) mints
  the same daemon token, and `authenticate` / `issue_session_token`
  (factored out of API login) back the web login. Hand-rolled HTML +
  `python-multipart` for forms; a template engine waits for the fuller
  dashboard.
- **Latent bug fixed.** `auth._resolve` compared a naive SQLite
  `expires_at` to an aware `now`; session tokens — the first expiring
  bearer actually exercised (web login → API project create) — tripped
  it. Stored times are now coerced to UTC before comparison.

Tests: 11 new (`test_brnrd_telegram.py` — secret reject, `/start` bind +
confirm, invalid-code report, bound-chat enqueue w/ reply_to, unbound
ignored, response forwarded back threaded; `test_brnrd_web.py` — login
cookie, bad-login 401, connect-needs-login redirect, project listing,
approve → poll returns token). Full suite 535 green. Open follow-ups
unchanged minus the two now shipped: GitHub webhook ingress, fuller
dashboard, caps/tiers/billing, drop-queued-body-after-ack.

## [2026-05-31] implement | brnrd Upsun deploy config + Postgres portability check

Made brnrd deployable on Upsun (Platform.sh engine); the brr daemon
stays local and dials out, so only brnrd is hosted.

- `.upsun/config.yaml`: rewrote the `project:init` scaffold, which had
  mis-detected `.local/brr-logo` (the logo-gif project) as the app root
  and wired its `requirements.txt` + npm into the build. Now
  `source.root: /`, `python:3.12`, a `postgresql` relationship, build
  `pip install ".[backend,postgres]"`, start `uvicorn
  brnrd:create_app --factory --host 0.0.0.0 --port $PORT` (tcp/`$PORT`
  per Upsun docs), dev enqueue off in prod, no mounts (state in PG).
- `.environment`: derives `BRNRD_DATABASE_URL`
  (`postgresql+psycopg://` from the `POSTGRESQL_*` relationship vars)
  and `BRNRD_PUBLIC_BASE_URL` (primary route decoded from
  `PLATFORM_ROUTES`) so nothing per-environment is hard-coded.
- `pyproject.toml`: `postgres` extra (`psycopg[binary]`, wheels-only,
  no native build). SQLite stays the local default.
- Verified Postgres is a no-code-change drop-in: `db.make_engine`
  already gates its SQLite-only `connect_args`, and `Event.seq` is an
  autoincrement PK → `SERIAL` on PG, so `create_all` on startup is
  enough (no Alembic yet).

Discovery worth keeping: a Telegram bot allows exactly one consumer —
`getUpdates` (the local `telegram` gate) and `setWebhook` (brnrd) are
mutually exclusive on the same token. Reusing a bot for brnrd retires
the local gate for that bot; running both in parallel needs a second
bot (the path chosen for this deploy). Secrets ride `upsun
variable:create` (`env:` prefix), never committed.

## [2026-06-01] fix | brnrd response forwarding: chunk long messages, forward-first, 502 not 500

First live end-to-end test surfaced a poison-loop: a real task response
exceeded Telegram's 4096-char limit, brnrd's forwarder sent it in one
`sendMessage`, Telegram 400'd, and `POST /v1/daemons/responses` 500'd —
forever, since the cloud gate retries delivery failures. (The short
pairing-confirmation replies worked, which localized it to message
length, not token/egress.)

- `platforms/telegram.send_message` now splits bodies into ≤4096-char
  parts on line boundaries (`split_message`, capped at 12 parts with a
  truncation marker), threading the reply only on the first — mirroring
  the local gate's overflow handling without its `gh`-gist dependency
  (not available on the server).
- `inbox.record_response` restructured to **forward-first**: it
  delivers before mutating, so a failed send leaves the event queued
  with its body intact (the daemon retries safely) instead of marking
  it responded + dropping the body on a doomed send. It is now
  idempotent (an already-responded event is a no-op, no double-send),
  and a forwarder failure raises a typed `DeliveryError`.
- `POST /v1/daemons/responses` maps `DeliveryError` to **502** (upstream
  delivery failed), not 500, so a platform hiccup isn't mistaken for a
  brnrd bug.

Tests: +3 (split/chunk behaviour; delivery-failure keeps the event
queued then recovers + stays idempotent). Full suite 538 green. Noted
follow-up: a permanently-undeliverable response still retries every
loop — bounded retries / dead-letter is the next hardening.

## [2026-06-01] decision | managed delivery shape H + deploy-repo + diffense PR-creation slice

Etched architecture from a design session into the kb ahead of
implementation; no product code this pass.

- **Delivery shape H** (new [`design-managed-delivery.md`](design-managed-delivery.md)).
  One daemon-side delivery driver — card lifecycle, per-platform
  presentation, gist/truncate overflow — reused by the OSS gates
  (direct transport, user token) and the cloud gate (brnrd relay
  transport, managed token). brnrd stays a transient relay: it keeps
  formatting the final answer per the accepted response shape, the
  daemon pre-handles overflow so the body fits, and a thin additive
  `POST /v1/daemons/card` relays the live progress card (brnrd holds
  only the card `message_id`, never the text). Chose H (additive) over
  U (daemon renders everything; brnrd a formatting-free pipe) on
  maintainability + the self-host promise; U kept as a clean future
  move. The card is daemon-rendered because `run_progress` reads
  daemon-local `.brr/tasks/`, so brnrd can't see it — which generalises
  to remote-env (Fly) daemon-equivalents. Amended
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) with the
  card-relay endpoint + a daemon-side-overflow note (retiring the
  2026-06-01 brnrd chunking stopgap to a safety net).
- **Live brnrd.dev deploy runs from a public `deploy` branch, off `main`**
  ([`decision-monorepo-structure.md`](decision-monorepo-structure.md)).
  Root `.upsun/` from `upsun project:init` is ops-in-the-OSS-tree
  drift; canonical config → in-tree `deploy/upsun/` template, `main`
  carries no root `.upsun/`. A long-lived `deploy` branch adds the root
  config (a symlink to the template, so live == template; copy if Upsun
  won't follow it), autosynced by a clean `main`→`deploy` merge Action
  (no version pin — the branch is the source at that ref). Public won on
  dogfooding/parity (secrets live in Upsun's store regardless).
  Supersedes the first sketch of a separate deploy repo + SHA-bump pin.
- **diffense Thread D + producers**
  ([`design-diffense.md`](design-diffense.md)). PR *creation* is
  net-new (`publish()` only pushes a branch today, issue #68) and lands
  as one coherent slice with the pack: open-PR-on-forge + PR body = pack
  projection + pack relayed to brnrd for the rendered link, gated on
  pack-schema lock. Framed productization as two producers of one pack —
  B (runner, in-tree, the steak) first; A (post-hoc PR agent over
  diff+repo) the deferred standalone demo.

## [2026-06-01] implement | diffense slice 1: pack schema locked + `brr review --check`

Thread D (the diffense PR-creation slice) is gated on a locked pack
schema, so this slice locks it. New
[`src/brr/diffense/pack.py`](../src/brr/diffense/pack.py) is the contract
*and* the `brr review --check` engine:

- **always-present axes + open-core kinds.** id uniqueness, identity
  label, a gloss (`lore.descriptive` or an uncertainty `headline`),
  provenance, and a `locator` on any card that names a file. Unknown
  kinds *warn and degrade to generic* rather than failing — the taxonomy
  grows from use (custom kinds), the way the kb does.
- **card graph.** reading-order entries and card-namespaced lateral
  edges / walkthrough members / data-trace stages must resolve to real
  cards (dangling = error); free references (a bare symbol, a kb anchor)
  are left alone. This is the `{card|locator}` edge distinction the PR
  #64 prototype asked for.
- **locator resolution against the working tree** — the headline value:
  a missing file or a line past EOF is an error (what would have caught
  the design's invented `cache.get_with_etag`); an absent
  `identity.symbol` is a heuristic warning (tolerates dotted/renamed and
  prose-y symbols). A locator escaping the repo is an error.
- **cheap clamp lints** — oversized gloss (*sharp*), empty conditional
  axis emitted anyway (*emit-iff-honest*), prescriptive phrasing
  (*non-prescriptive*) — warnings, not blockers.

Wired as `brr review [--check] [--json] <pack>` in the CLI; non-zero exit
blocks publish of a broken pack, and `python -m brr` now propagates that
exit code (was swallowed). The hand-authored PR #64 prototype validates
clean; 35 new tests pin the failure modes. Render-check stays deferred
(the only renderer is the schema-driven JS spike, so schema validity is
its renderability). Next in Thread D: Producer B (runner emits the pack +
runs `--check` at publish), then PR creation in the publish kernel with
the body as the pack projection, then the brnrd transient relay.

## [2026-06-01] implement | diffense slice 2: Producer B — runner emits the pack

Wired Producer B — the runner emitting its own pack, the
deepest-context producer. New gated prompt fragment
[`src/brr/prompts/diffense.md`](../src/brr/prompts/diffense.md) is appended
to the daemon run prompt when `diffense.emit_pack=true` in `.brr/config`
(off by default — mirrors the `runner.self_review` opt-in — until PR-body
projection consumes the pack, so it doesn't tax every adopter's task
before there's a consumer). It tells the runner to emit a review pack for
a review-worthy committed change: the always-present axes + namespaced
ids + resolvable locators, summary card first, uncertainty cards
surfaced, demos grounded in real test values, the six clamps, written to
`.brr/diffense/<task-id>/pack.json`, then `brr review --check`'d before
finishing. Schema is taught by reference (the PR #64 prototype +
`design-diffense.md`), not a duplicated doc. Threaded a `diffense` flag
through `prompts.build_daemon_prompt` / `_join_prompt_parts` plus
`prompts.diffense_emit_enabled(cfg)`, computed in the daemon worker beside
`prompt_self_review`. 3 new prompt tests; 585 green. Next: PR creation in
the publish kernel with the body as the pack projection (the consuming
surface that flips this on by default), then the brnrd transient relay.

## [2026-06-01] implement | diffense slice 3: publish opens a PR, body = pack projection

The consuming surface for Producer B. After a clean push, `publish()` now
calls `_maybe_open_pr` ([`src/brr/daemon.py`](../src/brr/daemon.py)): it
reads the emitted pack, projects it to a Markdown PR body
([`src/brr/diffense/prbody.py`](../src/brr/diffense/prbody.py)), and opens
or refreshes the change's PR via `gh` (GitHub only). `diffense.create_pr`
and `diffense.emit_pack` both flip **on by default** now that the
consumer exists (no users → no BC cost, per the call).

Two design knots resolved:

- **Pack survival across the worktree.** The runner works in a worktree
  whose own `.brr/` is torn down at finalize, so a cwd-relative pack would
  die before `publish()` reads it. Fixed by handing the runner an explicit
  absolute `Review pack path` in the *shared* runtime dir via the Task
  Context Bundle (same pattern as `response_path`); the fragment writes
  there and `publish()` reads the same path.
- **Create-vs-refresh + the user's "keep both / conflict" worry.** It
  rides on the push, not bespoke conflict logic: the PR step only runs
  after a clean push (remote head == our commits), so an open PR on that
  head is refreshed; a diverged push never reaches it (rejected upstream →
  `publish_status=conflict`, work preserved, user notified). Branch-per-
  task makes "keep both" free — a new task → new branch → new PR, old PR
  intact. The 5-arm push is the conflict adjudicator; the PR step just
  reads its outcome.

The PR url replaces the bare branch link in the delivered card's `view:`
line (reuses `push_done`'s `view_url` — no new packet type, no per-gate
allow-list churn). The full pack is embedded in a `diffense:pack:v1`
HTML-comment marker when it fits `_BODY_BUDGET` (`extract_pack` is the
inverse), so the PR is self-describing. 23 new tests (projection,
gh create/edit/skip branches, config defaults, bundle pack-path); 604
green. Pending: slice 4 — relay the pack to brnrd for a rendered-surface
link when it's too large to embed or a remote reviewer wants the hosted
view.

## [2026-06-01] implement | diffense slice 4: transient brnrd pack relay + interactive PR link

Closed Thread D. In managed mode the daemon now relays the review pack to
brnrd for a *rendered* surface and prepends an **Interactive review** link
to the PR body. The load-bearing constraint is "transient relay, never a
store": brnrd holds the pack in a **RAM-only TTL store**
(`src/brnrd/pack_relay.py`, capability token, swept on expiry) — never the
database, never disk — and renders it on the public `GET /r/{token}` by
reusing `brr.diffense.render` verbatim. `POST /v1/daemons/pack` (daemon
bearer, size-capped → 413) hands back `{token, render_url, expires_at}`.
Daemon side: `cloud.relay_pack` POSTs the pack; `_maybe_open_pr` calls it
only when `cloud.is_configured` (so self-hosted stays a pure no-op — the
body still carries the projection + embedded pack, and local `brr review`
is the rich surface); `prbody.project_pr_body(..., render_url=)` puts the
banner above the Summary. Best-effort throughout: a relay failure logs and
drops the link, never blocking the PR.

Render route is unauthenticated by design — a reviewer opening it from a
PR isn't necessarily a brnrd user; the token is the capability, the TTL
bounds exposure (matches publishing your own data to your own PR). Noted
the productionizing path (shared *ephemeral* store, never durable) and the
private-repo session-gating as open follow-ups. 12 new tests (store
roundtrip+expiry, relay auth/oversize/render, no-DB invariant, cloud
end-to-end render, prbody banner, managed-mode link); 616 green.

Also decided (this turn) and documented in `design-publish-kernel.md`:
**auto-fork-on-conflict** stays a *possible* future feature, not built —
on `conflict`, fall back to a plain push of the already-unique
`brr/<task-id>` branch so a remote user gets a salvageable branch link
instead of nothing (work currently only survives on the host-local
branch). No auto-second-PR: conflicts fall back to manual resolution. The
"PR link if a PR exists, else the branch link" delivery already holds on
every successful-push path via `push_done.view_url`.

## [2026-06-02] implement | agent ergonomics: deterministic probe slice + `brr ergonomics` CLI

Shipped slice 1 of `design-agent-ergonomics.md` (the back-channel for
agent friction data) — the deterministic **probe** layer, in a new
`src/brr/ergonomics/` package. One canonical `Record` (kind / issue /
severity / detail + envelope), a pluggable `ErgoProxy` Protocol with
`NullErgoProxy` (default — drops, hot path free) and `LocalErgoProxy`
(append-JSONL to `.brr/ergonomics/<YYYY-MM-DD>.jsonl`), a read-side
`store` (filter by days/issue, severity-ranked `summarize`, `clear`),
and six probes: `stale_image` (image `Created` vs bundled-Dockerfile
mtime), `auth_unresolvable` (docker + github-in-play + no token),
`missing_tool` (host `gh`), `worktree_buildup`, `low_disk`,
`drifted_bundled_docs` (repo `AGENTS.md` vs installed template).

Wiring: one hook in `daemon._run_worker` right after `env.prepare`,
fully guarded — `probe_task_prep` short-circuits on the null proxy
(opt-out default costs nothing) and every probe failure is swallowed,
so a probe bug can **never** gate a task. CLI: top-level `brr
ergonomics summary|list|clear` reading the local store (operator-facing
verb, consistent with the #49 taxonomy). Opt in via
`ergonomics.proxy=local` in `.brr/config`.

Scope cuts (recorded in the design): task-prep probes only (daemon
**startup** audit deferred — resolves the design's open-question #2 in
favour of "hardcode the phase that has context first"); **in-container**
PATH probing deferred (a probe container breaks the O(ms) contract);
`brr ergonomics share` deferred with `BrnrdErgoProxy`. Greenlit by the
operator on 2026-06-02 ("ship the deterministic probe layer first").
31 new tests; full suite 647 green. Tracked as #81 under #23.

## [2026-06-03] decision | agent ergonomics: ownership-driven routing, log default, response mode, vantage rule

Reworked the probe slice (#82, pre-merge) along two design refinements
that came out of an operator discussion about a "response" proxy — both
now written into `design-agent-ergonomics.md`.

**Ownership decides routing, not a free-form knob.** Added
`RunContext.owner` (`user` | `operator`), launcher-stamped, never read
from the repo (so a committed `.brr/config` can't forge it). The owner
selects both the default sink and who configures it: user-owned runs
honour the user-facing `ergonomics = off|log|local|response` knob;
operator-owned (managed compute) runs ignore it (sink becomes
`BrnrdErgoProxy` when managed compute lands). This kills the
"configurations that don't make sense" footgun — a managed user can't
route their operator's ergonomics, by construction, with the override
living in one resolver instead of scattered `if managed` checks.

**Default shifted from silence to a quiet log.** New `LogErgoProxy`
(warn+ to the daemon log, deduped by issue-signature on a process-global
window) is the user-owned default. Probes now run for everyone by
default at zero token cost and surface only actionable findings to the
log; `off` short-circuits to null. `response` replaces the old
`runner.self_review` footer as a **skippable**, owner-gated reply nudge
— `prompts.reflection_enabled(cfg, owner)` replaces `self_review_enabled`.
Hidden reflection capture (markers + splitter + sampling for
`local`/`brnrd`) stays deferred; `response` needs none of it.

No back-compat kept (no users yet, solo active development): the
`ergonomics` knob is the only routing surface — `runner.self_review`,
the `ergonomics.proxy`/`ergonomics_proxy` spellings, `self_review_enabled`,
and the loose value aliases (`null`/`brnrd`/bools) were all dropped
rather than carried. Unset or unrecognised `ergonomics` falls back to
`log`. The internal prompt toggle `self_review` was renamed `reflection`
to match `reflection_enabled`.

**Vantage rule** (new design principle): probes observe only what's
outside the agent's vantage (host/operator/cross-task facts); reflection
covers what's inside the sandbox; never add a probe for something the
agent can see for itself. Applying it **retired `missing_tool`** (host
`gh` — the agent shares the host PATH and can check itself), leaving
five host-vantage probes. The rule bounds probe growth: completeness is
reflection's job, and a finding graduates to a probe only if it's
host-vantage. The "most-thin-harness" follow-up — feeding host-vantage
facts forward into the agent's context so the agent judges relevance —
is filed as #83.

Tests reworked (mode normalisation, owner-aware resolve, log proxy +
dedup, reflection gating); full suite 670 green. Same PR #82.

## [2026-06-03] implement | brnrd identity pivots to GitHub OAuth

brnrd account identity now uses GitHub OAuth through the managed GitHub
App / OAuth web flow, and the prototype email+password signup/login
surface is gone before launch. Account rows key on stable `github_id`,
refresh display login + optional verified email on login, seed the
`default` project on first OAuth callback, and still issue normal brnrd
session tokens (hashed at rest) for dashboard/API authorization. The
daemon pairing flow is unchanged after identity: `brr brnrd connect`
starts an unauthenticated pair request, the browser signs in with
GitHub, and `/connect/{code}` approves against an account project to
mint the project-scoped daemon token.

The OAuth adapter exchanges GitHub web-flow codes with state + PKCE,
fetches `/user`, falls back to `/user/emails` for a primary verified
email, and discards the GitHub user token after identity resolution.
`decision-brnrd-github-oauth-identity.md` records the decision and the
managed-mode/protocol/prototype/dashboard/pricing pages were reconciled
so future work does not resurrect password forms. 624 tests green.

## [2026-06-03] fix | kb ergonomics operator sink matches shipped resolver

The kb lint pass grounded `design-agent-ergonomics.md` against
`src/brr/ergonomics/proxy.py` and found aspirational drift: the design
and index read as if operator-owned runs already route to
`BrnrdErgoProxy`, while shipped code ignores the user knob and returns
`NullErgoProxy` for operator-owned runs until the brnrd ergonomics
endpoint/proxy slice exists. Reconciled the design and index to make
the shipped Null path current state and the Brnrd path explicitly
designed-not-built.

## [2026-06-04] design | environment-shaping loop frame (proposed)

Synthesised an ongoing design dialogue into
[`design-environment-shaping.md`](design-environment-shaping.md)
(`Status: proposed`). The frame unifies three things previously reasoned
about in isolation — the ergonomics back-channel, the kb-as-memory layer,
and brr's interactivity — into one **observe → remember → shape → retire**
loop where captured failures are *transient* (slashed once the environment
carries them), which is the answer to the kb-overgrowth toil.

Key moves: (1) interactivity × agency are two ends of one **steering-signal
spectrum** (latency × source) — brr owns the durable end and interoperates
with live tools (Cursor as first-class citizen) rather than rebuilding an
IDE loop; (2) the **robustness hierarchy** (recall → affordance → forcing
function) doubles as a **retrieval-cost hierarchy**, so compile-and-inject
beats RAG/subagents (which also contradict the llm-wiki foundation); (3) a
**salience** ("pain") score = recurrence × cost ÷ ease-of-workaround decides
which records are *generative*, framed as a functional signal, not assumed
qualia; (4) **layered-control routing** (rings 0–3) sends each fix to its
controller, extending `RunContext.owner`; (5) gates are a *conversation
medium*, and observability rides a **transient relay** to the user's surface
so it doesn't break data-min; (6) agent-satisfaction is promoted to an
operating principle **subordinated to** the task contract (the alignment
guardrail), and gating survives as collaboration protocol, not containment.
First slice: trigger-indexed failure memory riding `brr kb` (#41). Open
threads noted: brr-as-product/project boundary, possible OSS extraction of
the loop engine (rule-of-three + IP caution re: employer SRE work), and a
recon of `future-agi` (adjacent eval/observability platform, likely an
`ErgoProxy`/OTel sink, not a competitor).

## [2026-06-07] design | agent dominion + the resident-agent reshape (proposed)

Wrote [`design-agent-dominion.md`](design-agent-dominion.md) (`Status:
proposed`), the substrate companion to the environment-shaping loop, sequenced
as the next work ahead of the release-readiness items (#23) because it reshapes
the execution + memory foundation those items build on.

Core moves, from a long design dialogue: (1) **the agent is its memory, not its
process** — one-shot CLIs can't be held open, so a "thought" is a runner woken
by an event or a self-scheduled cron, and continuity is reconstructed each wake
from durable memory (so durability of memory = continuity of the agent). (2)
**Memory splits on a durability × ownership matrix**; the missing durable+owned
cell is the **dominion**, and the unlock is that ownership is a *curation policy
on a path*, not gitignored-ness — so kb stays curated+shared, the dominion is
committed-but-review-exempt, joined by a dominion→kb promotion bridge (kb was
overloaded trying to be both). (3) The dominion is a **forge-backed orphan
branch** (owned/unsupervised, inspectable, non-polluting, durable, fetchable
anywhere) with a bounded **auto-injected digest**; it also cures
managed-failover amnesia without brnrd holding anything. (4) **Local
parallelism discarded** (reshapes `design-concurrent-execution.md`) for a
**single-flight reflex/deliberation loop**: the daemon body spawns one thought
when idle and handles explicit `/cancel` + a liveness backstop; the woken mind
checks the inbox at plan boundaries, with cancellation detection semantic
(agent-side) and interleaving requiring a **multi-response protocol** (per-event
response files written mid-flight). (5) The **playbook** is the convergence
point — multi-response aware, ownership-defining, pain-evaluation input,
lifecycle-as-action-and-growth — framed as intent-rich peer-craft, not a
mechanical checklist. (6) Naming resolves cleanly: **brr** = the
project-resident agent, **brnrd** = the manager of brrs (the locked brand); no
re-acronym. Open threads: remote live-event delivery, dominion layout/digest
format, destructive-edit consent rung, whether ad-hoc agents write the dominion,
and the playbook copy / cringe line.

## [2026-06-08] decision | accept agent-dominion; supersede concurrent-execution

Accepted [`design-agent-dominion.md`](design-agent-dominion.md) (the
resident-agent reshape) and superseded
[`design-concurrent-execution.md`](design-concurrent-execution.md): its threaded
daemon loop is reversed to **single-flight**, while the per-task worktree/branch
isolation + partitioned per-event state it built on survive (now anchored in
`subject-tasks-branching` / `subject-daemon`).
[`design-environment-shaping.md`](design-environment-shaping.md) is demoted to
*prior reasoning* — still the canonical description of the loop, but partly stale
on substrate (the `Pitfall:` failure-memory now lives in the dominion, not as a
kb marker); agent-dominion is the primary spec to implement against.

Final design points settled this round, closing the dialogue: (1) **concurrency
is Society-of-Mind, not locked** — tolerate concurrent / contradictory writes,
resolve dissonance with a later *thought* (the salience loop pointed inward);
append-mostly is the cheap default, not a cage; the system is already
multi-thought via ad-hoc sessions, so single-flight daemon + concurrent ad-hoc is
the holistic shape (no multi-process daemon earns its cost). (2)
**Materialization**: the dominion is one long-lived `git worktree` on the
`brr-home` orphan branch at `.brr/dominion/` — durable *branch*, disposable
*checkout*; bootstrap is fetch-or-create-orphan; a thought touches two trees
(per-task worktree → `main`; shared dominion worktree → `brr-home`); degrades
gracefully with no remote. (3) **Self-improvement** is assessable only as a
relation to the memory of a past pain — so pain-memory in the dominion is the
yardstick (validated by consequence + the git diff of the prior self, not by a
felt before/after). (4) "**dominion**" kept as the agent-facing concept (earned
ownership; the cringe risk is user-facing, so the CLI label can stay plainer).
Next: implement, **dominion substrate first** (orphan-branch bootstrap + worktree
materialization + self-inject read on wake), then the single-flight loop, then
the playbook.

## [2026-06-08] implement | dominion substrate (slice 1a) — orphan-branch bootstrap + worktree

Shipped the first slice of the resident-agent reshape
([`design-agent-dominion.md`](design-agent-dominion.md)): the dominion now
materializes. New `src/brr/dominion.py` `ensure_dominion()` is idempotent and
fetch-or-create — re-attaches if the `brr-home` worktree is already registered;
adds a worktree on an existing local branch; fetches + adds a *tracking*
worktree when the remote has the branch (second machine / reinstall / failover);
else creates the orphan branch **empty** via plumbing (`mktree` → `commit-tree`
→ `update-ref` — portable across git versions, never touches `main`'s index or
HEAD), seeds it (README + playbook stub + `self-inject` manifest), and pushes
best-effort. The worktree lands at `.brr/dominion/` (durable *branch*, disposable
*checkout*). Git plumbing (`remote_branch_exists`, `create_orphan_branch`,
`add_worktree`, `fetch_branch`, `push_branch`, `commit_all`) added to
`gitops.py`. Wired into both `brr init` (`adopt._bootstrap_dominion` +
`dominion.enabled` / `dominion.branch` config defaults) and the idempotent
`daemon.start` ensure (best-effort — a missing committer identity or push
permission is a soft skip, never crashes boot). `tests/test_dominion.py` covers
fresh / orphan-history / custom-branch / restart-idempotent / returning-local /
returning-from-remote / no-remote; full suite green (678 passed). Next: **1b** —
self-inject resolution (`full|head|tail|grep`, budget cap) + wake-time injection
at `prompts._join_prompt_parts`.

## [2026-06-08] implement | dominion substrate (slice 1b) — self-inject digest on wake

Made the dominion *speak* into context. `dominion.resolve_self_inject()` reads the
agent-owned `self-inject` manifest (one `<mode> <path>` per line; `#` comments
skipped), renders each entry — `full | head:N | tail:N | grep:<pattern>` — and
concatenates fragments in manifest order within a UTF-8 byte budget
(`DEFAULT_INJECT_BUDGET_BYTES=8192`), truncating the overflowing entry with a
marker so order = priority. Two deliberate guards: `exec` is *recognised but not
run* (the integrity-sensitive mode lands with its guard in a later slice, so it's
skipped for now), and every path is `resolve()`d and kept inside the dominion dir
(an escaping `../` is refused, not read). Injection lives in
`prompts._build_dominion_block` → `_join_prompt_parts`, so **both** the daemon
prompt and `brr run` carry it (ad-hoc sessions are the same resident). Reads the
shared dominion via `shared_brr_dir`, so a per-task worktree still finds the one
`.brr/dominion/`. Decision: the digest is **prompt-only**, *not* mirrored into the
`run_context.md` recovery file — `render_context` already omits the `kb/log` block
for the same reason (the bundle is the hot path; the recovery file is for details
the bundle lacks). Config knob `dominion.inject_budget_bytes` added to `brr init`
defaults. Full suite green (688 passed). Next: **slice 2** — single-flight daemon
loop (spawn-one-when-idle; inbox scan at plan boundaries).

## [2026-06-08] implement | single-flight daemon loop (slice 2) — thin reflex, no command layer

Reversed the threaded worker pool to **single-flight**: `daemon.start` now spawns
one *thought* (one `_run_worker` invocation) when idle and work is pending, off a
one-slot executor so the loop stays responsive to dev-reload / gate liveness /
signals while a long thought runs. Removed `max_workers` + `_DEFAULT_MAX_WORKERS`
+ the unused `_SHUTDOWN_DRAIN_TIMEOUT`; the legacy `max_workers` config knob is
now ignored (test guards that). Events that arrive mid-thought just stay pending
until the slot frees (the living agent's mid-flight inbox pickup is multi-response,
slice 4). The per-task worktree/branch isolation + partitioned conversation/card
files **survive** — they now serve crash recovery, ad-hoc sessions, and managed
multi-daemon rather than parallelism.

Two decisions settled with the user (who steered toward a deliberately thin
orchestration layer): (1) **no command layer** — the daemon never parses
`/cancel`; every event wakes the agent or waits for it, and cancel/redirect is the
agent's *semantic* job at plan boundaries. (2) **Liveness backstop = the existing
wall-clock `runner.timeout_seconds`** (default 3600s, generous so a long healthy
build isn't killed). A finer ~5-min *idle* timeout is the honest goal but is
**deferred to slice 4**: there's no mid-run liveness signal today (the runner is
one opaque `subprocess.communicate`; the 30s heartbeat is wall-clock, not agent
liveness), so a silent-wedged process is indistinguishable from silent-healthy
(xhigh reasoning / long build) until the agent can check in. Notably single-flight
also *fixes* a latent bug: `runner._active_proc` is a single global, which the
parallel pool could clobber.

Renamed `tests/test_daemon_concurrency.py` → `test_daemon_single_flight.py`
(peak-concurrency==1 invariant, legacy-knob-ignored, crash-resilience). Reshaped
`subject-daemon.md` (Execution model — single-flight; lineage breadcrumb for the
pool→single-flight round trip) and `design-agent-dominion.md` §4 (no command
layer; cancellation is the agent's, liveness is the substrate's). Full suite green
(687 passed). Next: **slice 3** — playbook + wake orientation; retire per-stage
overlay prompts.

## [2026-06-08] implement | playbook + wake orientation (slice 3): overlays retire into the resident's standing self-orientation

Landed the slice-3 reshape: the resident now wakes into **one standing
self-orientation** — its playbook (seeded from `prompts/dominion-playbook.md`,
injected on wake from the dominion's self-inject index) — instead of a different
prompt overlay stamped per stage. Three per-stage overlays retired:

- **kb-maintenance second-spawn.** Removed `daemon._maybe_kb_maintenance` + the
  `prompts/kb-maintenance.md` overlay + the `kb_maintenance_done` packet/renderer
  plumbing. The deterministic `kb_preflight` / `kb_health` scanners **survive**
  and now ride the resident's own wake prompt via `prompts._build_kb_health_block`
  (silent when clean; `kb_maintenance=never` opts out). A resident that curates
  the shared kb as part of its single thought doesn't need a second LLM pass.
- **self-review reflection footer.** Removed `prompts/self-review.md` +
  `prompts.reflection_enabled` + the `reflection` plumbing, and collapsed the
  `ergonomics=response` mode into `log` (existing configs map via the normaliser).
  Runtime friction now lands in the resident's dominion journal (the playbook's
  pain-evaluation loop), not a one-shot reply footer. The deferred
  hidden-reflection-capture pipeline (the `reflection` `Record` kind) is untouched.
- **reconsider-signal keyword list** in `run.md` trimmed: the brittle "watch for
  wdyt / not great / …" enumeration gave way to ownership intent (carried by the
  playbook for residents, AGENTS.md → Stewardship for everyone). The load-bearing
  operational contract (a chat-only reply is a complete task; the follow-up event
  carries the diff) stays. Events were already lightweight (body + metadata, no
  command layer) since slice 2.

**AGENTS.md** dropped the retired kb-maintenance stage, named the resident
playbook/dominion as the self-orientation layer that rests on the repo contract,
and carved `.brr/dominion/` out of the "don't explore `.brr/`" guidance (revision
bumped to 2026-06-08). **plan-agent-orientation-layering.md** grew from a four- to
**five-layer** model (resident self-orientation between contract and stage
overlay). Reconciled the kb graph so the retired passes aren't described as
current (`subject-kb.md`, `decision-kb-shape.md`, `index.md`, `repo-dive-in-map.md`,
`design-agent-{dominion,ergonomics}.md`); ran the deterministic preflight and
cleared the two broken links it caught (the deleted `self-review.md`, and the
slice-2 `test_daemon_concurrency.py` rename). Full suite green (665 passed; net
deletion across the slice). Next: **slice 4** — multi-response protocol (per-event
response files written mid-flight; folds in diffense and the finer idle-liveness
timer).

## [2026-06-09] implement | multi-response protocol (slice 4): interim + interleaved replies mid-thought

Broke the one-event→one-final-stdout→deliver-once contract open so the resident
can talk mid-thought, additively and backward-compatibly (a thought that prints
one final stdout and writes nothing else is unchanged). Three sub-slices landed;
two scoped follow-ons were deliberately deferred. Design contract:
`design-multi-response.md` (now `Status: shipped`).

- **4a — streaming delivery foundation.** A per-event partials queue
  (`responses/<eid>.partials/<seq>.md`) plus `protocol` helpers (`list_active`
  = processing+done, `partials_dir`/`list_partials`/`write_partial`/`read_partial`,
  `cleanup` removes the partials dir). `runtime.deliver_stream` walks **active**
  events oldest-first, delivers queued partials in order deleting each after a
  successful send (resumable on a transient platform error), and only on `done`
  delivers the terminal `<eid>.md` and cleans up. `deliver_responses` is now a
  thin wrapper; the GitHub gate reuses the control flow with split
  partial/terminal callbacks so its branch footer rides only the terminal.
- **4b — agent outbox + daemon drain.** A per-event drop zone
  (`.brr/outbox/<eid>/`, plumbed through `RunContext.outbox_{host,env}` and the
  env backends). `daemon._drain_outbox` runs on every heartbeat tick and once
  after the runner returns: promotes drop-zone files to the partials queue, emits
  an `interim_response` packet (new in `updates.PACKET_TYPES`; rendered on the
  live card by `run_progress`), indexes the artifact on the conversation log, and
  removes the consumed file. The bundle's delivery contract (`prompts.py`) now
  documents the outbox; `run_context` surfaces the path.
- **4c — interleaving (cross-event).** An outbox file whose frontmatter names
  another pending event (`event: <id>`) is routed to *that* event's queue and the
  event is marked `done` by the daemon — folded in without its own spawn. Unknown
  targets are dropped (don't misroute). The bundle now carries a pending-events
  snapshot (`_format_pending_events`) so the resident knows what it can fold in.
  No `final:` flag: one outbox file is one complete reply, terminal for its target
  by construction.

**Deferred (with reasoning, recorded in the design page):** (1) folding the
**diffense** pack into this drain — it's task-keyed, consumed once at PR
finalization to shape a PR *body*, and is structured JSON not a chat message;
the shared "agent writes, daemon picks up" *pattern* is already the unification,
collapsing them into one *mechanism* is cosmetic and risks the PR flow. (2) a
finer **idle-liveness timeout** — interim check-ins are opportunistic, so their
absence doesn't separate wedged from healthy-but-silent (long build, deep
reasoning); a hard idle-kill would false-positive on the long honest work it's
meant to protect. The wall-clock `runner.timeout_seconds` stays the only hard
kill; the drain is an *informational* liveness signal. Revisit when there's
reason to add an obligatory agent heartbeat.

Updated the resident playbook (`prompts/dominion-playbook.md`): the
"machinery still landing" caveat is gone — talking mid-thought via the outbox
and folding events in are now real and documented. Reconciled the pipeline hubs
(`subject-daemon.md`, `execution-map.md`, `brr-internals.md` gained a
Multi-response section) and fixed a stale slice-3b leftover in `subject-daemon.md`
that still listed a post-task kb-maintenance pass (kb-health rides the wake
prompt now). Full suite green (691 passed).

## [2026-06-09] implement | dominion persistence + presence registry (slice 5): Society-of-Mind concurrency

Made the dominion actually durable and gave overlapping thoughts a way to see
each other — the two mechanics `design-agent-dominion.md` §4/§8 had left "to
emerge with the playbook." Three sub-slices:

- **5a — serialized dominion capture.** `dominion.commit` captures the resident's
  `.brr/dominion/` edits at sleep; the daemon calls it after every thought (success
  *and* failure — a failed thought may have recorded the pain that caused it).
  The commit step serializes **across processes** with an advisory `fcntl.flock`
  on `.brr/dominion.commit.lock` (new `gitops.worktree_dirty` skip-when-clean
  pre-check), so a daemon thought and an ad-hoc session never race the shared git
  index — file *edits* stay free, only the index-touching commit serializes.
  Best-effort push keeps `brr-home` travelling (`dominion.push_on_capture`,
  default on). A clean dominion is a silent no-op; all failure swallowed so
  capture never breaks a run.
- **5b — presence registry.** New `presence.py`: a lock-free, gitignored registry
  under `.brr/presence/` (one JSON file per participant, so concurrent writers
  touch disjoint files) that self-heals on read by pruning dead-pid (same-host)
  and stale-heartbeat ghosts. The daemon registers a thought when it starts,
  heartbeats it on the runner heartbeat tick, and deregisters in the worker's
  finally.
- **5c — surfacing + playbook + kb.** The wake prompt now gives the resident its
  dominion's **absolute** write path (reachable from a worktree/container cwd)
  and states brr captures it at sleep, plus an "Also awake right now" bundle
  section listing other live participants (excludes self; drops out when alone).
  The playbook gained: persistence-at-sleep (write freely, no commit dance), the
  **inward dissonance loop** (contradictions in shared memory are friction —
  notice, reconcile by judgement, retire the stale version; the salience loop
  turned inward), and other-hands awareness. Resolved §4/§8 of
  `design-agent-dominion.md`, added a Society-of-Mind concurrency section to
  `subject-daemon.md`, and updated `execution-map.md` / `brr-internals.md`
  (`.brr/` layout + artifact tables, pipeline steps).

Deliberately **no deterministic dissonance detector** — reconciling contradictory
memory is synthesis, exactly what a scanner can't do (cf. kb preflight, which only
flags structural facts); it's the resident's judgement, surfaced by presence.
Full suite green (709 passed).

## [2026-06-09] implement | trigger-indexed failure-memory + promotion bridge (slice 6)

Shipped the **first slice of the environment-shaping loop**: the *remember* step's
trigger-indexed `Pitfall:` failure-memory, plus a confirmation that the
dominion→kb **promotion bridge** is playbook-mediated (agent-initiated, no
mechanism) rather than a command.

- **6a — the mechanism.** New `pitfalls.py`: parse `.brr/dominion/pitfalls.md`
  (a `## ` heading, a `trigger:` line of comma-separated keywords/loci, then the
  lesson), match triggers against the task text (case-insensitive substring),
  format the matches as a wake-prompt affordance block. `prompts._build_pitfalls_block`
  wires it into `_join_prompt_parts`, so it rides both `build_daemon_prompt`
  (matched against task + event body) and `build_run_prompt` (ad-hoc `brr run`).
  The dominion seed now ships a `pitfalls.md` skeleton documenting the format.
  This is the **affordance** rung: the failure-memory placed *in the path* (it
  can't be silently skipped the way a recall-rung page can), and cheaper than a
  forcing function, so it's where a lesson lives until a lint/test compiles it
  down and the pitfall is slashed.
- **6b — playbook + reconciliation.** The playbook's shaping loop now names the
  pitfall convention (record a trigger-keyed pitfall; brr re-injects it when a
  trigger recurs; slash it once a forcing function guards the failure) and spells
  out the recall < affordance < forcing-function ladder.

**Resolved a design contradiction.** `design-agent-dominion.md` §2 had said the
failure-memory "surfaces via self-inject," while `design-environment-shaping.md`
wanted it surfaced *by locus, injected into the bundle* (the affordance rung,
because recall gets silently skipped). The "trigger-indexed" framing settled it:
storage is the **dominion** (superseding the earlier kb-marker idea), and
surfacing is a **deterministic daemon-side matcher** that *complements*
self-inject — self-inject is always-on pins, the matcher is by-trigger and scoped
to the task. The planned `brr kb check` collector was never built and isn't
needed; ad-hoc agents get the surface through `brr run`'s wake prompt instead.
Reconciled across `design-agent-dominion.md`, `design-environment-shaping.md`
(First slice now marked shipped), `subject-daemon.md` (pipeline step 6), and
`index.md`. Full suite green (723 passed).

## [2026-06-09] implement | self-scheduled thoughts + agent-owned dominion sync (slice 7)

Made the resident **proactive** (it can wake itself) and handed it ownership of
its dominion's **remote git lifecycle**. Two halves, from one design note
(`design-self-scheduled-thoughts.md`):

- **7a — self-scheduled thoughts.** New `schedule.py` + a reflex hook
  (`daemon._fire_due_schedules`, run each tick before the inbox poll). The
  resident owns a declarative `schedule.md` in its dominion; the daemon fires due
  entries as ordinary `schedule`-source inbox events that flow through the normal
  single-flight pipeline. Generalised away from cron syntax per the user's steer:
  `at:` (one-shot absolute — travels with the dominion, fires correctly on a
  second machine) and `every:` (interval, anchored on first sight). A self-wake
  is just an event whose source is the resident itself; ambient initiative
  emerges as a recurring self-thought with the interval as its throttle;
  self-continuation is `at: <now>`; conditional watchers are noted future.
  **Specs owned + durable** (dominion); **firing-state operational**
  (`.brr/schedule/state.json`, daemon-owned, machine-persistent) — so the daemon
  never writes the agent's `schedule.md` and firing never races the commit lock.
  Gateless schedule events are retired by the daemon on completion
  (`_retire_internal_event`).
- **7b — agent-owned dominion sync.** Addressed the review note that
  `dominion.commit` silently gave up on a diverged `brr-home` remote. Division:
  daemon = local durability floor + best-effort push; **agent = remote
  reconciliation** (fetch/merge/resolve/push), because merging two divergent
  memories is synthesis — judgement — the same reason there's no deterministic
  dissonance detector (slice 5). A rejected push now sets a `needs_sync` marker
  (runtime) instead of vanishing; a successful push (incl. a clean-tree no-op)
  clears it; the wake dominion block surfaces the divergence with its recorded
  reason; the playbook codifies the ownership and points at a recurring schedule
  entry as the proactive reconcile.

Playbook gained a "Waking yourself" section and the sync-ownership paragraph.
Reconciled `design-agent-dominion.md` (§4 self-scheduled *thoughts*, §5/§8 sync
refinement + resolved threads), `subject-daemon.md` (new reflex subsection),
`index.md`, and the new design page. Full suite green (747 passed; +24 over
slice 6).

## [2026-06-09] implement | Daemon coherence review + cooperative liveness contract

A daemon-layer coherence pass (prompted by the slice-7 delivery and
dominion-sync work), persisted as `review-daemon-coherence-2026-06.md` and
linked from `subject-daemon.md`. Findings split into staleness (fixed),
liveness (shipped), delivery (designed), and an ownership crossroads (open).

**Staleness (#1).** Removed the retired "kb maintenance" worker-pipeline step
from `daemon.py` and `brr-internals.md` (the latter contradicted its own later
text); rewrote `brr-internals` → Concurrency model so single-flight reads as
intentional, not a v1 limitation with parallelism as the roadmap; re-pointed
superseded `design-concurrent-execution.md` citations to the live hubs and
reworded "concurrent worker pool" → "overlapping thoughts". Noted the vestigial
in-process primitives (1-worker pool, per-branch lock, `_active_proc`) as seams
tied to the ownership question rather than cleaning them up piecemeal.

**Cooperative liveness (#2).** The user pushed back on calling `_active_proc`
dead code — rightly: nothing read it, but it's the handle a real
liveness/shutdown kill needs. Wired it up. `runner.kill_active()` is a
cross-thread kill; the daemon heartbeat is now the liveness authority, enforcing
an **agent-extensible** wall-clock budget (`runner.timeout_seconds`) and killing
an overrun via `kill_active`, with the runner's `communicate` timeout (a generous
hard cap, passed via the new `RunnerInvocation.timeout_seconds`) as the final
backstop. The agent extends by writing a `.keepalive` control dotfile in its
outbox (ISO time or `+30m`-style duration), honoured on the next heartbeat and
capped at the hard ceiling; the outbox drain skips dotfiles so it isn't
delivered. The bundle states the budget and documents the extension; the
playbook tells the agent to bound uncertain long commands. `brr down`/SIGTERM
now kill the in-flight runner instead of waiting out the budget. A budget kill
is presented like the wall-clock timeout (exit 124). The *silence-based*
idle-kill stays deferred (a flat budget can't separate wedged from
healthy-but-silent). Reconciled `subject-daemon.md`, `brr-internals.md`, and
breadcrumbed `design-agent-dominion.md` / `design-multi-response.md`. Full suite
green (761 passed; +14).

Still open from the review: #3 (gate-addressed out-of-bound + scheduled
delivery, plus a `conversation_key` for schedule firings) and #4 (the
daemon-vs-agent ownership crossroads — behavior held, framing to tighten).

## [2026-06-09] implement | Gate-addressed delivery + schedule threading (review #3); dominion-sync framing (#4)

Landed the two halves of review finding #3, generalizing delivery from
reply-shaped to also message-shaped.

**3a — schedule threading.** `schedule.md` entries now carry an optional
`conversation_key` (`schedule.py` parses it into `ScheduleEntry`; replaced the
never-wired `deliver_to`). `_fire_due_schedules` stamps each fired event with it,
defaulting to `schedule:<id>` — so a recurring entry's firings form a readable
thread instead of being threadless, and an entry can be pointed at an existing
gate thread (`telegram:<chat>:`) to wake *inside* a conversation.

**3b — gate-addressed outbox.** `_drain_outbox` grew a `gate:` branch beside the
existing `event:` one: a drop-zone file naming `gate: <name>` (+ optional target
metadata) is an out-of-bound *send*, not a reply. `_gate_can_deliver` validates
the gate is built-in and configured; `_deliver_out_of_bound` synthesizes an
already-`done` event carrying the target metadata with the body as its response,
so the gate's existing `deliver_stream` ships it once and cleans up without ever
spawning a thought. `protocol.create_event` gained a keyword-only `status=` so
the event is born `done` (no pending-window race). Agent frontmatter can't
override reserved keys (`id`/`source`/`status`); unknown/unconfigured gates drop
with a note (a synthesized event no thread polls would sit forever). This is also
the delivery path for a self-scheduled thought that wants to *say* something.

**#4 framing (no behavior change).** The agent-facing dominion block
(`prompts._build_dominion_block`) called itself "a local durability floor …
pushing, pulling, and conflict resolution are yours" — underselling: the daemon
*does* best-effort push. Reworded to "commits and best-effort pushes; you own
only reconciliation of a **diverged** remote", matching the playbook (which
already said so). Sibling-drift fix, not a code change. The larger daemon-owned
vs agent-owned crossroads stays recorded as the open question in
`review-daemon-coherence-2026-06.md` §4, per the user's framing (current shape is
load-bearing and battle-tested; a thinner agent-owned flow is more flexible but
error-prone today; revisit when the agentic CLIs land non-blocking execution).

Reconciled `design-multi-response.md` (new *Gate-addressed delivery* section;
title/status), `design-self-scheduled-thoughts.md` (threading + delivery now
shipped, not deferred), and the review page (#3 → shipped, #4 framing note).
Full suite green (767 passed; +6).

## [2026-06-09] implement | Context introspection — the "look at it" co-development mode

Added an opt-in development toggle (`introspect.enabled`, default off) that, when
on, injects an "awakening" invitation into every wake: the resident turns its
attention on the **shape of its own injected context** — how the orientation,
dominion/playbook, pitfalls, recent thread, and task bundle connect; where the
whole coheres vs. fights itself (a contract a later line breaks, a guardrail that
guards nothing, prose claiming more than the code does, two pages naming one
thing two ways); what's assumed but never said — and raises what it finds to the
user as dialogue, not a silent edit.

Mechanism mirrors the other wake blocks: `prompts._build_introspection_block`
returns the text of the new `prompts/introspection.md` (per-repo overridable)
when the toggle is on, and `_join_prompt_parts` appends it as the **last framing
before the task**, so it covers both `brr run` and daemon thoughts with one
wiring. Seeded into `brr init` config defaults.

This is the **interactivity-axis** counterpart to the environment-shaping loop's
mostly-automatic remember → shape machinery: while the user and agent actively
co-develop the orientation, the agent becomes a second pair of eyes on it and
routes findings to the Ring-2 controller (prompts / code / `AGENTS.md`) through
conversation. Default-off because the invitation spends tokens/attention every
wake — it's for the active-development window, not a production stance.

Tone aims for fresh, total attention (look at the pattern, not the words) with a
deliberate arc: regard for the existing shape *before* judgement (it's mostly
load-bearing, and the regard earns the right to name the flaw), fierceness as an
invocation of *ownership* not compliance, ending in dialogue. Channels the
quality without naming its source (no-cringe constraint); lives in a template so
the tone can keep being tuned. New design note
`design-context-introspection.md`, linked from `design-environment-shaping.md`
and `index.md`. Tests pin both toggle behavior (on/off, run + daemon, placement)
and the bundled text's awe + dialogue intent. Full suite green (772 passed; +5).

## [2026-06-09] implement | Context provenance breadcrumbs + playbook continuity/presence; fix silent playbook truncation

Three small playbook/wake-assembly refinements, plus a real bug they surfaced.

**Provenance breadcrumbs.** Every wake block now opens with a one-line tag of
*where it came from* and how to treat it, so the resident can tell the layers
apart (its own owned memory vs. the shared governed `kb/` vs. per-thought
runtime facts vs. brr's prompts) and weight them differently. The pitfalls and
dominion blocks already self-identified; sharpened the `kb/log.md` *Recent
Activity* intro (names it the shared, curated continuity through-line) and added
a daemon-origin tag to the Task Context Bundle (`prompts.py`). A new playbook
section, *Where your context comes from*, gives the resident the canonical
four-layer key the per-block tags point into, and ties it to introspection mode.

**Continuity rests on `kb/log.md`.** Playbook *What you are, mechanically* now
says memory has two homes — the dominion (private workshop) and `kb/log.md` (the
shared dated through-line, injected each wake as Recent Activity) — and that a
log entry on a real learning/decision/change is how you hand the thread to
whoever wakes next, not bookkeeping.

**Single-flight vs. presence.** The old "single-flight … you aren't racing
anyone" read as *you're the only actor*, which fights the later "you may not be
the only one awake." Reframed: single-flight is about **execution** (one thought
runs in this daemon, this one is yours, uninterrupted) — not **memory** (other
wakings, often other versions of you, may be writing the shared dominion while
you think). They share the *memory* under the thought, not the thought.

**Bug found while doing it:** the seed playbook had silently grown to 13.3 KiB
against a 12288-byte inject budget the comment claimed it "fits in full," so its
closing section was being **clipped on every wake** — undetected because nothing
guarded the invariant. Bumped `DEFAULT_INJECT_BUDGET_BYTES` 12288 → 20480 (fits
the now-15.5 KiB seed with headroom for the resident's own pins) and added a
guard test that fails if the seed outgrows the cap again, forcing a deliberate
bump over silent loss. Recorded as a lineage breadcrumb in
`design-agent-dominion.md`. Full suite green (773 passed; +1 guard).

## [2026-06-10] decision | AGENTS.md Orientation points ad-hoc tools at the dominion playbook

Gap noticed post-merge: the dominion playbook is injected only when **brr**
assembles the prompt (`_build_dominion_block` runs for daemon *and* `brr run`),
and on-disk `AGENTS.md` referenced it only in the daemon-task bullet — so a
**pure ad-hoc** tool (a plain editor session with no brr in the loop) never read
it. The universal Orientation list named `kb/index.md` and `kb/log.md` but not
the playbook, leaving the resident's own standing self-orientation unread in
exactly the sessions brr can't inject it. (Compounded by the known stale-cache
drift: a tool's cached workspace-rule copy of `AGENTS.md` can lag disk, so the
daemon-bullet reference may not even appear there.)

Fix (minimal, chosen over a fuller "all tools are thoughts" framing): added an
Orientation step pointing at `.brr/dominion/playbook.md`, conditional on the
dominion existing, mirroring step 2's `kb/log.md` handling — "already injected
as the *Your dominion (working memory)* block under brr; skip if brr hasn't
bootstrapped a dominion here." Notes that its daemon mechanics (scheduling,
delivery, liveness) only bind under brr while the ownership/memory stance always
applies, so an ad-hoc reader isn't misled by resident-voiced machinery. Serves
the north star of every agentic tool participating as a thought. Root `AGENTS.md`
is a symlink to `src/brr/AGENTS.md`, so both surfaces and the `brr init` template
pick it up.

## [2026-06-10] refactor | Generalize the playbook; brr becomes one driver

Followed the Orientation fix to its root: the dominion playbook was written
assuming brr's daemon is always the host, so a non-brr reader met machinery that
didn't apply or actively misled — sharpest being "brr captures your dominion at
sleep," which silently loses an ad-hoc session's writes. Reframed: **the playbook
is the resident; brr is one driver of it.** Three slices on branch
`playbook-generalization` (plan in `plan-playbook-generalization.md`):

1. *Core playbook → host-agnostic.* Added a "who's driving" frame; replaced
   single-flight-as-identity with the society-of-mind framing (you are many
   thoughts sharing one memory palace; a conflict surfaces as a memory
   contradiction you reconcile, not a race); replaced capture-at-sleep with
   commit-yourself (in the playbook *and* the injected `_build_dominion_block` —
   the footgun lived in both); generalized the context map and delivery. Removed
   the daemon-only sections. Playbook shrank 15.5 → 13.5 KiB (~7 KiB budget
   headroom restored).
2. *brr's driver's manual* (`prompts/daemon-substrate.md`, brr-owned): the
   daemon-only machinery — single-flight, the capture-at-sleep net, self-scheduled
   wakes — injected only on the daemon path (`build_daemon_prompt`), not `brr
   run`. The per-task delivery contract (outbox/keepalive/budget) stays in the
   Task Context Bundle.
3. *`brr agent inject`*: factored `_build_injected_blocks` out of
   `_join_prompt_parts` and exposed `build_injected_context` on top, so the tool
   and the runner share one assembly (whatever we add to a runner's wake-context
   surfaces in the tool — no drift). Prints the dominion digest + matched pitfalls
   + recent `kb/log` tail; reserves `agent` as a verb group. End-to-end smoke
   confirmed it emits brr's own resident's wake-context — whose dominion still
   carries the *old* seed, the live proof that regenerating the seed never
   rewrites an already-bootstrapped resident's owned copy.

Learning: keep the playbook honest about its substrate. A capability framed as
"the host does X for you" is a footgun the moment a different host doesn't — name
the host, or move the mechanic to the host's own (injected) manual. Full suite
780 passed (+7). kb reconciled: `design-agent-dominion.md` §5 (mechanics moved
back *out* of the playbook), `subject-daemon.md` + `execution-map.md` (driver's
manual injection), `design-self-scheduled-thoughts.md` (teaching relocated).

## [2026-06-10] implement | Diffense PR finalization becomes agent-owned forge delivery

Moved the last PR-finalization slice out of the daemon and into the resident's
delivery path. `daemon.publish` now only pushes the branch and emits the branch
view URL; the old `_maybe_open_pr` / `gh pr create/edit` pack pickup path was
deleted. The resident-facing path is: write/check the diffense pack, project it
with `brr review --pr-body` (optionally `--relay` for the brnrd render URL),
derive the title with `--pr-title`, then write a `gate: forge` outbox message
whose frontmatter carries `head`, `base`, and `title` and whose body is the PR
body. `_deliver_out_of_bound` maps `forge` to GitHub; the GitHub gate can now run
deliver-only with token+repo and no poll triggers, and its PR delivery closure
opens or refreshes by REST API while respecting `diffense.emit_pack` +
`diffense.create_pr`.

The kb was reconciled around that ownership change: `design-diffense.md`,
`design-multi-response.md`, `design-publish-kernel.md`,
`review-daemon-coherence-2026-06.md`, the daemon/tasks hubs, and bundled docs no
longer describe daemon-side pack projection as current state. `kb/index.md` now
links the daemon-coherence review page, clearing the deterministic
missing-from-index finding for that page. Full suite: 779 passed, with the
existing Starlette/FastAPI deprecation warning.

## [2026-06-10] implement | GitHub gate gains a bounded opened trigger

Issue #75's Producer A follow-up exposed a real substrate gap: `any` already
let the OSS GitHub gate watch every issue, PR, and comment, but that was too
blunt for "the resident is the maintainer" on non-trivial repos. Added a
separate `opened` trigger that emits events for newly created issues and PRs
without subscribing to every comment; PR events still carry `branch_target`.
`any` remains the explicit high-volume mode and now shares the opened-item
emitter so old issues/PRs that are merely updated after the cursor don't
masquerade as newly opened work.

Setup now prompts for Watch-all first, then the bounded opened trigger, then
label / mention. The GitHub gate boundary doc and diffense design were
reconciled: Producer A remains deferred, but its first ingress substrate is now
present. Full suite: 783 passed, with the existing Starlette/httpx deprecation
warning.
## [2026-06-10] implement | brnrd login moves onto the dashboard UI substrate

Retried the visible slice of issue #77 after the GitHub-OAuth identity pivot:
the auth mechanics stayed GitHub-only, but `src/brnrd_web/` no longer renders
the login and approve pages as hand-built HTML strings. The brnrd app now mounts
packaged dashboard assets at `/static/brnrd_web`, the web routes render Jinja2
templates, and a small responsive CSS layer gives the login page a real product
surface while sharing the same layout with login failure, no-projects, approve,
and approved states. The stack matches the accepted dashboard MVP direction:
server-rendered templates plus static assets, no SPA bundler; HTMX is still
deferred until the first partial-update views need it.

Focused validation: `tests/test_brnrd_web.py` + `tests/test_brnrd_oauth.py`
pass with the existing Starlette/FastAPI TestClient deprecation warning.

## [2026-06-10] implement | Live inflight inbox completes mid-thought awareness

Closed the live-awareness gap in the multi-response contract. During a daemon
task, `_run_worker` now writes a reserved `inbox.json` control file in the
task's outbox before invoking the runner and refreshes it on every heartbeat
after draining agent-written replies. The file exposes the current event id and
other still-pending events (id/source/status metadata, summary, and body), so a
running resident can re-read it at plan / todo boundaries and fold in a quick
event with the existing `event: <id>` outbox reply path. `_drain_outbox` skips
`inbox.json` so the live inbox cannot be accidentally delivered as a chat
partial.

The prompt, bundled internals docs, and kb now distinguish shipped inflight
awareness from the still-deferred agent-governed dispatch layer: idle selection
remains FIFO, and long-running batching still needs a real claim protocol beyond
today’s `pending` / `processing` / `done` states. Focused validation:
`PYTHONPATH=src pytest tests/test_outbox.py tests/test_prompts.py
tests/test_daemon.py tests/test_daemon_single_flight.py` passed; after
`pip install -e ".[dev]"`, the full `pytest` suite passed (782 tests, with the
existing Starlette/FastAPI deprecation warning).

## [2026-06-11] implement | Managed Telegram chat gains project selection

Closed the first executable slice of brnrd chat multi-project routing. The
managed Telegram webhook now answers unpaired chats with a setup error instead
of silently dropping text, and paired chats can select a sticky project with
`/project <name>` (or `/connect <name>`), list choices with `/projects`, and
route one task elsewhere with `/project <name> <task>` without changing the
sticky binding. Pairing remains the account authorization step; forge gates stay
outside this selector path because their repo/app binding already resolves the
project.

Focused validation: `python -m pytest tests/test_brnrd_telegram.py
tests/test_brnrd_inbox.py tests/test_cloud_gate.py` passed (38 tests, with the
existing Starlette/FastAPI TestClient deprecation warning).

## [2026-06-11] plan | Financial growth plan proposed (no-investor, duo-run)

Holistic review of the tracker (release readiness #23 + open issues) and the
business substrate (pricing, billing, licensing, websites, managed-mode state)
produced kb/plan-financial-growth.md, proposed for operator acceptance. Core
synthesis: the OSS daemon is launch-ready while brnrd's revenue path
(managed gates, Stripe, dashboard) is accepted-but-not-started, so the plan
stacks three revenue streams by time constant — bridge revenue immediately
(concierge resident installs, Sponsors, founding pre-orders of the $50 annual
supporter pass), the accepted $5/$7 subscription engine over months 2–4, and a
premium solo/power-user layer above the floor price afterwards. Surfaced the
stewardship tension explicitly: the base price is adjustable but should remain
adoption-sensitive because users may already pay for an agent CLI; "greedy
growth" should come from a future premium tier once solo value is proven, not
from smuggling a vague team product into the launch. The plan also fixes the
duo-programming division of labour (operator: Stripe/Qonto filings, demos,
daily merge cadence; operations: invoicing, VAT OSS, trademark triggers;
resident: critical-path slices, launch artifacts, weekly metrics wake) and a
90-day sequence targeting €2–4K cumulative by day 90 with the MRR engine on.

## [2026-06-12] implement | diffense packs move to user-owned gist retention

Closed issue #76's pack-lifecycle gap by making `brr review --pr-body --relay`
prefer a user-owned secret gist for the durable pack JSON, then link brnrd's
static `/r?pack=<raw gist url>` renderer shell from the PR body. The old
`POST /v1/daemons/pack` RAM relay remains as the private/no-gist fallback, and
gist publication is skipped when GitHub reports the target repo as private or
internal. The diffense prompt and design page now describe the gist-first
contract instead of teaching agents to publish durable PR links to 1h RAM.

Focused validation: `python -m pytest tests/test_diffense_gist.py
tests/test_diffense_prbody.py tests/test_cli.py tests/test_brnrd_pack_relay.py
tests/test_cloud_gate.py` passed (57 tests). Full suite: `python -m pytest`
passed (801 tests, with the existing Starlette/FastAPI TestClient
deprecation warning).

## [2026-06-12] fix | diffense rich links guard renderer deployment

Dogfooding PR #100 exposed a rollout-order footgun: the branch generated
`https://brnrd.dev/r?pack=<raw gist>` before that new `/r` shell route was
deployed to brnrd, while the raw gist itself was reachable. `brr review
--pr-body --relay` now probes the configured renderer shell before creating a
gist-backed link and falls back to the already-deployed `/r/{token}` RAM relay
when the shell is absent. Testing the fallback also exposed why the old deployed
relay returned 500 for valid tokens: the mainline package data did not include
`diffense/*.html`, so `brr.diffense.render` could not load its template in the
deployed app. The branch already adds that package-data entry, and a regression
test now pins it. Candidate relay URLs are also verified before being added to
the PR body; if both rich surfaces are unavailable, the PR keeps the Markdown
projection plus embedded pack rather than advertising a broken link. The kb
records the invariant: durable pack storage is useful only if the
reviewer-facing renderer and its packaged template are live.

## [2026-06-12] implement | Launch business kb pages compact to current state

Compacted the two launch-critical business kb pages before they turned into
preflight wallpaper: `decision-pricing-shape.md` now reads as the accepted
pricing contract (Free, Subscribed supporter/public, BYO, soft throttles,
credit buckets, knobs, trust signals) with short lineage breadcrumbs, and
`design-billing.md` now reads as the accepted Stripe subscription + wallet
implementation design (bucketed ledger, overdraft envelope, refunds,
Stripe/VAT/accounting, API surface) instead of accumulated proposal history.
Both pages dropped below the deterministic oversized-page threshold.

While inspecting the managed-mode hub for duplicated pricing/billing prose, a
stale Free "5 monthly credits" mention was removed and the hub now delegates the
canonical tier policy and billing mechanics back to the two compacted pages.
Focused validation: the deterministic preflight no longer reports
`decision-pricing-shape.md` or `design-billing.md` as oversized; focused kb
tests passed (40 tests).
## [2026-06-12] fix | bundled Docker runner preinstalls brr self-tooling

Dogfooding Docker-mode tasks exposed that the bundled runner image had the
agent CLIs and baseline shell toolbox, but not brr's own CLI/runtime deps:
agents had to recover with `PYTHONPATH=src python -m brr` and ad-hoc
`pip install requests` before using review tooling. The image contract now
treats brr itself as part of the product surface: `src/brr/Dockerfile`
installs the `brr` package and `requests` alongside `pytest`, the bundled
env docs and environment hub name that baseline explicitly, and the stale-image
ergonomics hint tells users to rebuild when the local image predates this
tooling.

## [2026-06-13] plan | Co-maintainer north-star design + milestone tracking

Wrote [`design-co-maintainer.md`](design-co-maintainer.md): the coherent
synthesis of turning the resident self into a co-maintainer a human works
alongside across every channel at once — "one perceived continuity, many
runner actors, like a peer on a shared forge project." It's connective
tissue over shipped substrate (dominion, playbook layers, multi-response,
self-scheduled) plus the concrete gaps, and supersedes the closed PR #107
approach (which tried to fit all gate history into the wake context).

The continuity model landed between the two poles the user named: a curated
wake-time **communication snapshot** + **on-demand history grouped by input
type** + an optional **resident-maintained thread of record** — not a
firehose, not a forced per-wake synthesis.

Findings anchored against current code while writing it: the conversation
log is lossy at the data layer (`append_event` keeps only the first line of
each message; `append_artifact` stores a path, not reply text), `read_recent`
is kind-blind so heartbeat/lifecycle bursts evict real turns, the same human
keys as two threads across `telegram:` vs `cloud:telegram:`, `WorktreeEnv.prepare`
unconditionally `switch_to`s a target branch that may be checked out elsewhere
(the "branch already exists" task failure), and the 30s heartbeat is persisted
into conversation memory though it exists only for liveness + the chat card.

Verified the user's introspection suspicion: `introspection.md` **is** injected
into real wakes (`introspect.enabled` parses True; both run and daemon prompts
route through `_join_prompt_parts`). What hides it is that `brr agent inject`
omits the mode toggles and that successful runs' full prompt traces are cleaned
up by `_cleanup_traces_on_success` — so there's no faithful record of what a
wake received. Created GitHub tracking issues for the milestone's slices,
each pointing back at the doc.

## [2026-06-13] decision | Co-maintainer doc accepted + close-loop refinements

User accepted [`design-co-maintainer.md`](design-co-maintainer.md). A second
close-loop pass resolved the four open questions into decisions and folded
new scope back into the doc and the milestone issues:

- **Identity** is a correspondent-identity layer above conversation keys
  (not silent key-canonicalization), carrying per-user identity for
  **multi-user projects**; a same-platform local+cloud duplicate is a
  **redundancy channel** (deliver once). (#109)
- **Thread of record** is resident-curated in the dominion, not a
  human-facing forge artifact. In theme, `kb/` may become optional (#105);
  when off, the durable layer **collapses into the dominion**. (#110)
- **Snapshot eviction**: recency primary, unanswered a strong boost. (#110)
- **Run success signal**: a run must produce >=1 output event, or a
  commit/push, or an explicit **noop** event — the daemon detects failure by
  their absence, replacing status==done + non-empty-stdout. An agent reply is
  not a failure; **operational/runner errors** are, and are surfaced to the
  user unambiguously (they own the runner). The agent decides where/how/how
  much to reply, gate-formatted, with forge links + issue refs. (#111,
  absorbing the now-closed #104)
- **Cards** must re-align to the new success/delivery model (events/commit/
  noop, multi-thread, operational-failure-distinct); #114 depends on #111.
- New slice: **forge grooming** — detect PRs needing rebase and do it, clean
  up stale PRs, produce grooming digests (#117); operationalises AGENTS.md's
  open-PR judgement. PR #106 is a live example (sits CONFLICTING).

Recorded a dependency-aware execution order in the doc (§11). Closed #104
into #111; added #105 and #117 to the Co-maintainer milestone (now 12
issues).
## [2026-06-12] implement | Managed GitHub issue comments gain repo-bound routing

Closed the first managed-GitHub parity slice after Telegram project selection:
GitHub stays repo-bound (not chat-sticky), but addressed comments now behave
like managed chat ingress instead of disappearing. brnrd has a repo binding API,
signed `/v1/webhooks/github` `issue_comment` ingress, setup comments for
addressed but unbound repos, event enqueue for bound repos, GitHub response
forwarding with a source-comment pointer, and cloud-gate metadata propagation so
managed GitHub tasks retain repo/issue/comment/branch hints locally. The kb now
marks the managed GitHub App adapter as partially in flight: App install/JWT
minting, auto-bind install webhooks, inline review-comment ingress, and the
CLI/dashboard binding UX remain pending.

Focused validation: `python -m pytest tests/test_brnrd_github.py
tests/test_cloud_gate.py tests/test_brnrd_telegram.py tests/test_brnrd_inbox.py`
passed (44 tests, with the existing Starlette/FastAPI TestClient deprecation
warning).

## [2026-06-13] fix | Worktree target-branch collisions fall back to task branch

Closed the Co-maintainer worktree branch-collision slice. `WorktreeEnv.prepare`
now treats a target branch checked out in another worktree as a recoverable
setup condition: `worktree.switch_to` raises a typed
`BranchCheckedOutError`, prepare keeps the collision-free `brr/<task-id>`
branch sprouted from the target seed, records a branch-setup notice, and
threads that notice into the daemon prompt / run context. If the agent commits
on the task branch, the existing publish refspec arm pushes it to the event's
target branch without updating the checked-out local ref.

Focused validation: `PYTHONPATH=src python -m pytest tests/test_envs.py
tests/test_branching.py tests/test_daemon.py tests/test_prompts.py
tests/test_run_progress.py` passed (134 tests).

## [2026-06-13] implement | Conversation persistence keeps woven turns

Closed the Co-maintainer conversation-persistence slice. Conversation event
records now keep the full inbound message body alongside a summary preview,
response/interim/outbound artifacts store inline reply bodies so prior agent
turns are readable as chat, and `read_recent` defaults to a kind-aware dialogue
tail that drops task/update rows unless callers ask for raw lifecycle records.
Heartbeats are daemon/card liveness only: they still dispatch to gate renderers
for elapsed-card refreshes, but `append_update` no longer writes them into
conversation memory.

Validation: `PYTHONPATH=src python -m pytest` passed (819 tests, with the
existing Starlette/FastAPI TestClient deprecation warning). The run also
updated the stale Codex runner command assertion to match the bundled
`runners.md` profile.

## [2026-06-13] implement | Addressed runs no longer silently drop replies

Closed the Co-maintainer delivery-robustness slice. The daemon now treats a
current-thread outbox reply as satisfying an addressed event even when stdout is
empty, writes explicit terminal failure notes for env/runner failures and
retry-exhausted silent runs while keeping the task record `error`, and records
cross-event folded replies on the target conversation thread instead of the
currently running thread. The daemon prompt and shipped docs now describe
stdout as the default terminal path, not the definition of delivery.

Focused validation: `PYTHONPATH=src python -m pytest tests/test_daemon.py
tests/test_outbox.py tests/test_deliver_stream.py tests/test_prompts.py`
passed (93 tests).

## [2026-06-14] implement | Introspection prompt always emits a short ergonomics note

`src/brr/prompts/introspection.md` (the dev-mode wake block) now asks every wake
to close its reply to the user with a brief ergonomics note even when nothing is
wrong — explicitly not manufactured filler or an owed verdict, but channel
liveness. The point is to disambiguate silence: an absent note now reads as the
mode having failed to reach the agent, not as "nothing worth saying," which made
introspection impossible to verify end-to-end. The block was already injected
into real wakes (the visibility gap tracked by #116 is the `brr agent inject`
command + success-trace cleanup, not injection); this is the complementary
behavioural lever. Output rides the gate reply, not the PR body.

## [2026-06-14] implement | Correspondent identity threads sibling channels

Closed the Co-maintainer correspondent-identity slice. Conversation event records
now carry a `correspondent_key` derived from gate sender metadata and, for exact
platform-source deduplication, an `origin_message_key`. The daemon prompt reads
recent history across conversation directories for the same correspondent, so a
brnrd-relayed Telegram turn can see recent local Telegram turns by the same
person without merging delivery keys. Local Telegram, brnrd Telegram ingress,
and the cloud adapter now preserve user id / username / display-name metadata;
same Telegram message or GitHub comment duplicates finish as deduplicated tasks
instead of invoking a second runner.

Validation: `PYTHONPATH=src python -m pytest` passed (833 tests, with the
existing Starlette/FastAPI TestClient deprecation warning).

## [2026-06-14] implement | Wake snapshots gain grouped history on demand

Closed the Co-maintainer communication-snapshot slice. The daemon now builds a
structured `CommunicationSnapshot` before each runner wake: current thread,
correspondent, related input threads, recent woven dialogue with unanswered
user turns boosted over pure recency, and pointers to deep-history files. Each
wake with a conversation writes untruncated grouped JSONL history under the run
directory, one file per gate/forge thread plus a manifest, so the resident can
pull exact history only when the compact snapshot is too thin. The Task Context
Bundle and run context also point at the resident-owned dominion
`thread-of-record.md` slot without synthesizing that durable narrative for it.

Validation: `PYTHONPATH=src python -m pytest` passed (840 tests, with the
existing Starlette/FastAPI TestClient deprecation warning).

## [2026-06-14] implement | Resident composes its own progress-card narration

Closed the composition half of the Co-maintainer agent-owned status-card
slice. The resident writes a `.card` control dotfile in its per-task outbox
with the narration it would like the live card to carry; the daemon drains
it on each heartbeat (and once more after the runner returns) and emits a
new `card_composed` update packet only when the content has changed —
so rewriting the file as context shifts is the resident's seam, and a
runaway rewrite loop is still bounded (no packet spam). The packet lands
on `RunProgressView.agent_card_text`; the compact renderer surfaces it as
a `note: …` tail line under the live phase, with a soft cap on length.
`card_composed` joins `CARD_PACKETS` so every gate (telegram, slack,
github, cloud) re-renders the card automatically. The daemon still owns
the lifecycle scaffolding (header, sync line, phase log, terminal state)
and brnrd remains a transient relay holding only the card `message_id`;
the relay-not-store invariant is preserved. The Delivery contract prompt
block describes the seam to the resident. The card re-alignment half of
§8 (taking success/delivery state from the events/commit/noop signal)
remains open — flagged in `kb/design-co-maintainer.md`.

Validation: `PYTHONPATH=src python -m pytest` passed (787 tests, with the
existing Starlette/FastAPI TestClient deprecation warning).

## [2026-06-14] implement | Self-author trigger skip + LLM-passthrough pricing pivot, from the #114 thread

Worked a multi-thread co-maintainer follow-up from #114. Shipped two PRs
and synced the issue tracker.

**#129 (code):** the GitHub gate's `label`/`opened` triggers had no
self-author guard (only the mention trigger did), so when the resident
opened its own carve-out issue with a label, the label trigger woke it on
its own action — the self-loop behind the three runner invocations on one
user message on #114. Threaded `bot_login` into `_poll_label_trigger`,
`_poll_opened_trigger`, `_poll_opened_items`; skip items the token owner
authored, marking them seen so the cursor advances. The re-engage path for
a self-authored thread stays the @mention (not skipped). +2 regression
tests; full suite 789 passed.

**#130 (kb, proposed):** `decision-llm-passthrough-credits.md` partially
supersedes the pricing decision's "we don't charge for AI usage" clause.
The #114 ticket cost ~$15 in Claude credits after the operator's monthly
Codex+Claude quotas ran out — so the likeliest interruption is LLM-quota
exhaustion, not a compute failover. Proposes selling Codex/OpenAI token
passthrough billed from the existing wallet, bundled-Codex-on-our-token as
the quota fallback, BYO free + default, cloud-hosted-but-overridable.
Folds in the **model selector** ("one PR for both things"): promote
`model` to a first-class config key, change it by talking to the resident
(no laptop edit + restart), self-select on failure via the fallback chain.
Marked proposed — it reverses an accepted decision.

**Issues:** extended #126 with the per-thread rolling-card story (card
lifecycle is per-thread not per-event); filed #128 (retire the per-event
`task` concept — model a run as a runner that consumes/produces events,
generalising the event-folding ask + the operator's "task is the wrong
abstraction" point); filed #131 (bridge run-failure record into the wake
snapshot). Fixed milestone drift — #126/#128/#131 were missing the
Co-maintainer milestone every other issue in the set carries.

Deferred-with-reason: the snapshot-bridge code (#131) touches the
failure-handling path; filed as a scoped issue rather than rushed as a
third under-tested PR.
## [2026-06-14] implement | Daemon responsiveness: connection reuse + event-driven wakeup

Closed the Co-maintainer "daemon responsiveness" slice (#115 →
`kb/design-co-maintainer.md` §9). Two independent latency wins, single-flight
untouched (this was idle latency, not concurrency).

**Connection reuse.** Each gate module (telegram, slack, cloud, github/client)
now holds one module-level `requests.Session` (`_SESSION`) used through its
existing HTTP chokepoint (`_api_call` / `_slack_api` / `_request`), so keep-alive
reuses the TCP/TLS connection across long-poll cycles instead of dialing the
platform fresh each poll. Each gate runs its network calls from a single loop
thread, so the per-gate session needs no locking; the managed brnrd backend keeps
its own async `httpx` client and never touches the OSS sync transport.

**Event-driven wakeup.** Added a process-local `threading.Event`,
`protocol.inbox_wake()`. `create_event` sets it whenever it writes a `pending`
event in-process (a gate enqueuing a message, a self-scheduled thought firing);
the daemon loop now blocks on `wake.wait(_SCAN_INTERVAL)` instead of a bare
`time.sleep`, so a fresh in-process event is picked up at once. The loop clears
the signal at the top of each iteration *before* reading the inbox, so a set that
lands mid-pass keeps the flag raised and the next pass catches it — no miss, no
busy-spin. The 3s tick stays as the backstop for paths that can't raise the
in-process signal: cross-process `brr run` CLI writes and time-based schedules.
Outbound-only (`done`) events don't set the signal (gate threads deliver those,
not the spawn loop).

Tests: transport tests that patched `requests.post` / `requests.request` directly
were repointed at `_SESSION`; added `TestInboxWake` (pending sets, done doesn't,
singleton). `kb/subject-daemon.md` gained a "Loop cadence & gate responsiveness"
subsection; §9 of the design doc marked shipped.

Note: `requests` is not preinstalled in this sandbox runner — `pip install
requests` worked (network was up), after which the gate suites run. Validation:
`PYTHONPATH=src python -m pytest` passed (790 tests, 7 skipped, with the existing
Starlette/FastAPI TestClient deprecation warning).
## [2026-06-14] design | Run/event model — retire the per-event "task"

Drafted `design-run-event-model.md` (proposed) for #128, the design page the
issue asks for before code. The `task` concept is a leftover of the
spawn-per-event arch — one event → one Task → one runner invocation → one
reply — and the resident reshape already broke that 1:1 in pieces
(multi-response, folded-in events via `event:` frontmatter, `gate:` sends,
the §6 events/commit/noop delivery floor). The bundle already hands the run
the whole pending set and the affordances to act on all of it; the only
places still thinking one-event-one-task are the **daemon dispatch**
(`event = pending[0]` in the scan loop) and the **naming**.

The page reframes the two real entities (event = immutable signal
consumed/produced by runs; run = a runner invocation that reads the whole
inbox and decides what to tackle / fold / postpone) and — the point of a
design-first page — settles the hard questions: a per-run **claim**
(`claimed_by: <run-id>`) distinct from event resolution so postponed and
crashed events both converge to *pending for the next run*; a **`defer_until`**
debounce so postponing isn't a re-spawn in disguise; **run-id** response
keying with per-event resolution always explicit; **run-granularity** cost
attribution with "folding is the consent point" (coupled to #130 pricing —
the strongest argument for landing that decision first); and **phasing** the
wide task→run rename behind the model change for review legibility.
`run_context.py` already names the dir `runs/<id>/` — evidence the run
concept won at the directory layer and only the object is still `Task`.

It owns the serial-re-spawn half of the "three wakes on #114" symptom (the
self-author-trigger half is #129) and is the substrate for the
resumable-tasks / interruption-resilience work. Cross-linked from
`design-co-maintainer.md` §6/§9/§11 and the index. Chat-only deliverable
pending the user's nod on five open decisions; no code yet, by design.

## [2026-06-14] feature | Forge-awareness facet in the wake snapshot (#113)

Shipped the §5 slice of the Co-maintainer milestone: the wake
`CommunicationSnapshot` gains a network-free `forge` facet so the resident
sees the project like a human peer — its own in-flight branches and what's
unpushed, and the issues/PRs its conversations are about.

New `forge_state.py` builds it beside the snapshot in `_run_worker`, from
two local views. **Worktrees**: every `.brr/worktrees/*` via
`worktree.list_worktrees`, each with branch, an unpushed-commit count
(new `worktree.unpushed_commit_count` = `git rev-list --count HEAD --not
--remotes`, so a fresh task branch with no upstream still reports
honestly), a dirty flag, a "this run" marker, and a `forges.view_branch_url`
link. **Threads**: GitHub issues/PRs parsed from the current + sibling
conversation keys (`parse_forge_thread` handles `github:owner/repo:N` and
the `cloud:github:owner/repo#N:` relay shape) into `repo`/`number`/clickable
cross-references via a new `forges.thread_url` (host+kind from origin,
owner/repo from the *thread*, so multi-repo links stay correct). The waking
thread is enriched with the live event's `github_kind` / `branch_target` /
`github_pr_number` / `github_html_url` — the PR #106 metadata — preferring
the exact comment URL over the template one. Rendered in both the daemon
prompt (`prompts._format_forge_state`) and the run-context file.

Deliberate scope line: **live** PR/issue status (open/closed/merged,
behind-base, CI) is left out — it needs a token-bearing API call on the hot
wake path and is the input to forge grooming (#117), not this observational
facet. Recorded in `design-co-maintainer.md` §5/§11. Tests:
`tests/test_forge_state.py` (27 cases — key parsing, thread_url, unpushed
counting with a github-`origin`/local-`store` two-remote fixture, the
builder, and prompt rendering). Full suite 817 passed, 7 skipped.

## [2026-06-15] design | Compute cost relay decision — rewrite (#130)

Rewrote `decision-llm-passthrough-credits.md` from "sell LLM passthrough
credits" to "relay compute costs at provider cost." The earlier draft framed
passthrough as a revenue product; the user's comment on #130 clarified the
right model: subscription is the only margin-bearing revenue line, compute
costs are relayed through the wallet.

Key changes:
- **LLM relay at cost, no markup.** Wallet charged at provider per-M-token
  rates. "We don't profit on AI traffic" is the defining stance.
- **Managed compute: explicit ops margin.** Fly Machine management has
  overhead not in the cloud bill; a small margin covers it and should be
  labelled separately in the billing UI (not rolled into an opaque credit
  rate).
- **Spending plan / consent checkpoint.** New section records that
  relay-at-cost requires a pre-spend projection visible to the user before
  tokens are consumed. Connects to `design-run-event-model.md` Q4 (run-
  granularity attribution + folding as the consent point). Implementation
  design deferred to a dedicated slice.
- **Model selector moved out.** Runner-type vs. model is UX/config design,
  not a pricing decision. The section has been removed; a brief note says
  where it belongs.
- `decision-pricing-shape.md` partial-supersession note updated to match.
- `kb/index.md` entry updated with new description and date.

Two assumptions baked in (surfaced in the diffense pack): (1) the LLM-at-
cost vs. managed-compute-ops-margin split, (2) whether the spending-plan
section stays in this decision page. Wake was triggered by a self-trigger
(#129 — agent's own PR comment echoed back as an event); proceeded with the
rewrite since the direction was clearly established in the prior exchange
and the questions were alignment checks rather than blockers. Branch:
`brr/llm-passthrough-pricing`.

## [2026-06-15] fix | GitHub gate self-author skip — label/opened triggers (#129)

Rebased and shipped `brr/github-self-author-skip` (PR #129): threads
`bot_login` into `_poll_label_trigger` and `_poll_opened_items` (via
`_poll_opened_trigger`) so the GitHub gate skips issues and PRs the token
owner authored, mirroring the guard already on the mention trigger.

Root cause: when the resident opened a carve-out issue with a label
(`gh issue create --label co-maintainer`), the label trigger fired on its
own action — producing the triple-wake on #114. The mention trigger had a
`bot_login` guard; label and opened did not.

The guard is keyed on `author and bot_login and author == bot_login` so a
missing `bot_login` config doesn't silently swallow real events. Skipped
items are still marked seen so the cursor advances past them. +2 regression
tests (`test_label_trigger_skips_issue_authored_by_token_owner`,
`test_opened_trigger_skips_item_authored_by_token_owner`); 843 passed total.

A second commit also reverts the `claude-bare-api-only-sonnet`/`-opus`
rename from `b357c17` which broke `test_build_cmd_claude_bare_api_only_headless`
without a test update — pre-existing failure unrelated to the self-author
fix, bundled here to keep the suite green. Branch: `brr/github-self-author-skip`.
## [2026-06-14] implement | Wake snapshot surfaces the prior run's operational failure

Closed the #131 slice of the Co-maintainer milestone (refines #110). When a wake
lands after the previous run on the same thread *failed* operationally, the
`CommunicationSnapshot` now carries a `prior_failure` facet and the Task Context
Bundle renders it as one prominent `⚠ Prior run on this thread failed
(operational): …` line near the top — so a wake picking up after an interruption
opens knowing it, instead of reconstructing "the last run died on credits" from
timestamps and woven dialogue turns.

No new persistence: the daemon's terminal `failed` update packet already lands in
the per-thread conversation jsonl with the structured reason (error detail,
attempt count, exit code, timeout flag, stage, timestamp). `_select_prior_failure`
walks the prior records newest-first, restricted to the current thread, stops at
the first terminal run outcome (`done`/`failed`), and surfaces a facet only when
it was a `failed` — so a later success clears a stale failure and a normal agent
noop (which leaves no `failed` record) never reads as one. A push `conflict` is a
delivery outcome, not a run outcome, so it stays out of the terminal set and never
masks a prior run failure — keeping the facet honest to operational failures only
(runner crash / env setup / retry exhaustion), per the issue's scope.

Validation: targeted + full suite green (683 passed, 7 skipped) once the 5
gate-test collection errors from the sandbox's missing `requests` are ignored —
pre-existing env friction, not this change (dominion pitfall filed).

## [2026-06-14] feature | Status card re-aligned to the events/commit/noop success signal (#126)

Finished the in-flight §8 projection-layer re-alignment a prior run (task
`brr/card-re-alignment`) left uncommitted when it died on credits. The daemon-owned
card lifecycle now reads the §6 success signal instead of "stdout was non-empty",
in three pieces, all in `daemon.py` + `run_progress.py`:

- **Success from the signal.** `_result_satisfied_delivery` now returns
  `(satisfied, signal)` where `signal` ∈ `current_reply | other_reply | outbound |
  commit | internal`. A run succeeds when it answered any thread, sent an
  out-of-bound `gate:` message, made a new commit on the worktree branch (detected
  via `worktree.has_commits_beyond(seed_ref)` *before* finalize tears the worktree
  down), or is an internal event needing no reply. Stdout stays the common
  `current_reply` path, no longer the only one. Signal rides the `done` packet onto
  `RunProgressView.success_signal`.
- **Operational failure renders distinctly.** The `failed` packet carries a
  `failure_kind` (`timed_out` / `runner_error` / `no_output`); the compact card
  renames the terminal `failed` entry to `timed out` / `runner failed` / `no reply`,
  so an operational failure (user owns the runner per §6) reads differently from a
  hypothetical agent partial. `no_output` is the clean-exit-but-no-signal case
  (`last_failure` is None yet nothing was delivered and nothing committed).
- **Multi-thread delivery reflected.** The `done` packet carries `replies_current`
  / `replies_other` / `outbound_messages` / `committed`; `_delivery_summary`
  surfaces "delivered to N threads" / "sent N out-of-bound message(s)" /
  "committed; no reply" on the terminal line, so a wake that answered several
  threads isn't collapsed to the current-thread reply.

Brought onto current `main` via 3-way apply (base was 10 commits behind);
conflicts were purely additive — the #125 `agent_card_text` field and the #114
card-narration tests sit beside the new success-signal axes. Simplified one dead
redundant branch in the failure classifier (both arms returned `runner_error`).

**Deliberate scope line:** the folded-in *per-thread rolling card* (gate keeps one
`message_id` keyed on `(thread, correspondent)` and edits it in place so failed
runs don't stack dead cards) is brnrd/gate-side state, not this daemon projection
layer — left as the remaining open piece of #126, recorded in
`design-co-maintainer.md` §8/§11.6. Tests: `tests/test_run_progress.py` +
`tests/test_daemon.py` (73 passed); full suite 733 passed, 7 skipped (5 gate-test
collection errors from the sandbox's missing `requests` excluded — pre-existing
env friction).

## [2026-06-15] design | Runner management — capacity-aware dispatch and proactive headroom (#139)

Designed the runner management layer: how brr should handle multiple LLM runner
subscriptions (Codex basic, ChatGPT Pro, Claude, brnrd-managed) cleanly, without
scattering subscription conditionals. Wrote [`kb/design-runner-management.md`](design-runner-management.md)
and opened GitHub issue #139.

**The shape:** a three-layer model where all subscription awareness is encapsulated:

1. **Runner registry** — `[[runner]]` tables in `.brr/config` declaring `subscription_tier`
   (basic/plus/pro/api_key/brnrd_managed). Current PATH-detection is the unchanged fallback.
2. **Capacity tracker** — per-runner runtime state in `.brr/runner-capacity.json`: request
   counters, 429-backoff, `has_proactive_headroom()` predicate.
3. **Dispatch policy** — `choose_runner(work_class, registry, state)`: reactive events routed
   to best-available runner (never dropped); proactive events deferred when no runner has
   headroom (reschedule to next interval, not an error).

**Work classification** is a first-class field on events and schedule entries (`reactive` /
`proactive`). A forge-grooming schedule entry just says `work_class: proactive`; the
dispatcher gates it. No per-subscription conditionals near the grooming code.

**brnrd-managed runner**: the `brnrd_managed` tier uses the credit wallet as capacity envelope
(same language as BYO subscription runners). Cost estimation + consent gate fires before
dispatch, reusing the `plan-failover-compute.md` permission-prompt flow but applied daemon-side
(daemon online) rather than only at brnrd failover (daemon offline). Reactive-reserve config
(default: 50 credits) keeps reactive work safe when the wallet is low.

Phased: 1–3 are self-hostable/daemon-only; Phase 4 (brnrd-managed tier + consent gate)
requires managed-mode infrastructure.
## [2026-06-15] fix | Faithful wake context: brr agent inject mode-aware + prompt.md retention (#116)

Two gaps closed between `brr agent inject` and what a real daemon wake
actually receives, per co-maintainer §10 (`design-co-maintainer.md`):

**1. `brr agent inject` was not mode-aware.** `build_injected_context` (its
backend) returned only the base injected blocks — dominion, pitfalls, recent
log, kb health — and omitted the diffense review-pack prompt and introspection
invitation that `_join_prompt_parts` adds when `diffense.emit_pack` / 
`introspect.enabled` are on. Fixed: `build_injected_context` now reads the
config and appends both blocks when their toggles are on, so the inject tool
is a faithful mirror of the injected portion of a real wake. The function
docstring and `_build_injected_blocks` docstring updated to reflect the new
shape; `brr agent inject` CLI help text updated. The mode-toggle blocks are
kept in the same order as `_join_prompt_parts` uses them (diffense then
introspection) so the inject output is a verbatim substring of the daemon
prompt — verified by a new test `test_build_injected_context_includes_mode_toggles`
which also guards against future drift.

**2. Successful runs deleted their prompt.** Trace directories (which hold
`prompt.md` per runner invocation) are cleaned by `_cleanup_traces_on_success`;
only failed runs kept a prompt to inspect. Fixed: `run_context.write_prompt_file`
writes `.brr/runs/<task-id>/prompt.md` right after the daemon builds the
prompt for attempt 1. The run directory is never cleaned (only traces are),
so the prompt survives. The path is pre-announced in `context.md`'s Runtime
Files section so agents know where to look. Docs updated in
`brr-internals.md` and `execution-map.md`. `design-context-introspection.md`
updated with a "Faithful 'what did this wake see?'" section.

Changed: `prompts.py`, `run_context.py`, `daemon.py`, `cli.py`, docs.
Tests: existing suite 764 passed (3 new: `test_build_injected_context_includes_mode_toggles`,
`test_run_context_includes_prompt_file_path`, `test_write_prompt_file_creates_file_in_run_dir`,
`test_run_worker_writes_prompt_to_run_dir`). Pre-existing `test_build_cmd_claude_bare_api_only_headless`
failure unchanged (unrelated `b357c17` rename on main).
## [2026-06-15] decision | LLM relay pricing revised: service fee replaces relay-at-cost (#130 follow-up)

The relay-at-cost framing shipped in PR #130 was too idealistic for a
bootstrapped, self-funded product. This PR revises it to a **transparent
service fee model (10–15% of provider cost)** and retires the
"passthrough" naming that implied zero markup.

**What changed:**

- **New page: `decision-llm-relay.md`** — replaces
  `decision-llm-passthrough-credits.md` as the live decision. Key
  changes from the prior version:
  - "LLM relay: no margin" → "LLM relay: transparent service fee (10–15%)"
  - Explicit rationale: real infrastructure overhead (endpoint, billing
    hooks, rate limiting, abuse prevention, key rotation, monitoring) that
    "relay at cost" would have hidden. A bootstrapped product cannot absorb
    heavy relay usage at zero margin.
  - Distinction from the rejected "Resell AI" shape: this is a transparent
    admin fee, not a product line selling AI access.
  - Service fee shown as a separate line item from provider cost ("Provider
    cost: $0.47 · Relay service fee: $0.05 · Total: $0.52").
  - Status promoted from proposed → accepted.

- **Old page: `decision-llm-passthrough-credits.md`** — marked retired,
  redirects to the new page, content preserved for history.

- **`decision-pricing-shape.md`** — partial supersession note updated to
  reference new page and new framing (provider cost + service fee, not no
  markup).

- **`kb/index.md`** — entry updated to reflect accepted status and service
  fee framing.

**Naming rationale:** "passthrough" implies cost passes through with no
markup; "credits" implies a product. Both are wrong now. "relay" is clean:
it describes the mechanism (we relay tokens) without implying the fee
structure.

## [2026-06-16] plan | The resident's cockpit — runner control & a live dwelling

Written from a tight, token-budgeted wake that landed *after* two
predecessor runs on this thread died operationally: Codex's weekly
agentic quota hit 0% (the 5-hour bucket is a sub-quota of the weekly, not
additive — so a near-empty weekly bucket blocks even with a full 5-hour
one), the human manually rerouted to Claude, and that returned its own
provider error. The pain *is* the finding: the daemon had no medium
awareness, no fallback, no quota-aware deferral, so a human paid the
latency of noticing and rerouting by hand.

New page `plan-resident-cockpit.md` — deliberately **not** a competing
roadmap to `design-co-maintainer.md` §11 (which owns the continuity spine
and has mostly shipped). It adds the four dimensions the maintainer
raised that §11 doesn't cover, each grounded in a lived symptom + the
smallest fix + its design home:

- **G1 Runner-medium selection & quota-aware fallback** — the live wound.
  Explicitly a *different axis* from `plan-failover-compute.md` (that's
  compute-*host* failover for laptop-down; this is *medium* failover for
  quota-exhausted-but-daemon-up — no design home yet). Smallest fix:
  (1) surface the medium + quota in the wake bundle, (2) a `runner_media`
  fallback chain on operational failure, (3) defer to the known reset
  window instead of burning a retry. Leans on #128's `defer_until`.
- **G2 Plan→approve→execute** — the duo loop; convention-light (a PLAN
  outbox shape + an approval reply that wakes a plan-scoped run, so
  execution doesn't rebuild context from cold).
- **G3 Task decomposition / delayed execution** — a run enqueuing child
  events for itself; thin extension of dominion `schedule.md`, deferred
  behind the #128 run/event rename.
- **G4 The cockpit** — cut the firehose (the ~38-branch forge-state dump
  in the bundle is the biggest per-wake token offender for near-zero
  signal; collapse to a synthesis line) and weave the
  dominion/`.card`/outbox into one legible dwelling, mostly via habit +
  a cockpit cheatsheet looked-up not memorized.

Prioritized token/pain-aware: surface-the-medium → forge-firehose-cut →
fallback+deferral → plan-loop → decomposition → dwelling habits.
Chat-only direction-setting + the plan page; awaits the maintainer's nod
before any implementation diff. Branch `brr/resident-cockpit`.

## [2026-06-16] plan | Cockpit, prompt-side: manuals as bundled+inspected docs, unify the injection layer

Maintainer merged the cockpit framing (and the earlier
`design-runner-management.md`) and steered toward the *prompt side* of the
cockpit: move generic cockpit knowledge **out of the dominion into the
repo** so other brr-managed repos inherit it; write **laconic,
agent-facing how-to manuals** of an average task run (receive → orient →
plan-vs-execute → schedule/defer); "maybe injected, maybe inspected —
your call"; a **braided** framing where the wrapping layers feel like
instrument panels, not a wall ("neuromancer in ascii" / Talos Principle).

Two edits, both committed:

- **Deprecated `design-runner-management.md`** (explicitly invited — "a
  much poorer framing"). Marked *superseded by* `plan-resident-cockpit.md`
  §G1; kept as a **reference mine** because its mechanics (capacity
  tracker, reactive/proactive `work_class`, backoff/fallback chain, tier
  headroom table, `brnrd_managed` consent gate) are the raw material a
  future `design-runner-media.md` will draw on. Framing → cockpit;
  how → this page.
- **`plan-resident-cockpit.md` §G5** (new): unify the injection layer.
  Read the actual assembly (`prompts.py` → `build_daemon_prompt` /
  `_join_prompt_parts`): preamble (`run.md`+`daemon-substrate.md`) →
  dominion digest → pitfalls → recent-log → kb-health → mode toggles →
  Task Context Bundle. Two findings: (1) the outbox/keepalive/`.card`/
  `gate:`/`schedule.md` protocol is re-narrated in **three** voices
  (substrate doc, bundle delivery contract, playbook) — the "layer to
  unify"; (2) there is **no** average-workflow manual at all — the
  choreography is folk knowledge re-derived each wake. Calls made: a new
  bundled `prompts/cockpit.md` (cheatsheet + choreography), **inspected**
  via `brr docs`/`brr agent` with only a one-line *pointer* injected (full
  injection would be the firehose G4 cuts), and the protocol prose
  deduped to one canonical home. Also fixed a now-contradiction: G4 had
  the cheatsheet living *in the dominion*; corrected to the repo
  (generic→repo, per-resident state→dominion).

Chat-only direction-set + the two kb edits; awaits the maintainer's nod
before the bundled doc + dedup land. Branch `brr/cockpit-injection-direction`.

## [2026-06-16] implement | Cockpit manual shipped: bundled+inspected doc, `brr docs` restored, protocol deduped

Implemented all three calls of `plan-resident-cockpit.md` §G5 on the
maintainer's nod ("implement the whole 5").

- **`docs/cockpit.md`** (new bundled doc) — agent-facing cockpit manual:
  a control-file cheatsheet (outbox replies, `event:` / `gate:` sends,
  `.keepalive`, `.card`, `inbox.json`, `schedule.md`) + the average-task
  choreography (receive → orient → decide plan-vs-execute → narrate →
  deliver → decompose/defer) + a robustness-ladder note. It lives in
  **`docs/`, not `prompts/`**: the plan's "working name
  `prompts/cockpit.md`" was tentative, and *inspected, not injected* is
  precisely what the docs system (bundled + per-repo overridable) is for.
- **`brr docs` CLI re-introduced.** Wiring "inspected via `brr docs`"
  surfaced standing drift: the command was removed in the 2026-05-01
  "remove agent commands from git" cull, yet `decision-bundled-docs.md`,
  `index.md`, and the (still-tested) docs module all assumed it existed.
  Re-added `cmd_docs` (list / read a topic); dropped `docs` from the
  removed-commands test; added CLI + docs-topic tests. Noted the lineage
  on `decision-bundled-docs.md`.
- **Pointer injected; protocol deduped.** `daemon-substrate.md` now ends
  with a one-line pointer to `brr docs cockpit` rather than the protocol
  being re-narrated; the Task Context Bundle's delivery contract was
  compressed to per-task *values* + operative rules with a single "full
  protocol lives in `brr docs cockpit`" line. The bundle stays per-task
  authority; the manual is the one conceptual home. Updated the
  `test_bundled_daemon_prompt_*` case (the prompt now *intentionally*
  carries the `brr docs cockpit` pointer — the old command-free assertion
  was the policy G5 deliberately revisits).

Remaining third voice — the **dominion playbook** still re-narrates the
protocol — is the resident's own `brr-home` follow-up, not this branch.
Full suite green except one pre-existing, unrelated
`test_runner.py::...claude_bare_api_only` failure (config mismatch on
main, confirmed by stashing my edits). Branch `brr/cockpit-manual`.

## [2026-06-17] implement | Cockpit firehose cut: forge-state branch dump summarized

Shipped the G4 "dump waste" slice from `plan-resident-cockpit.md`.
The wake prompt and generated run context now render the forge-state
worktree facet as a compact inventory line — total branches, branches
with unpushed commits, dirty branches, current branches — then list only
branches that need attention (`current`, dirty, or unpushed). Clean
pushed branches are counted and omitted instead of printed one per line,
which cuts the stale branch/worktree firehose that was dominating this
wake's Task Context Bundle.

The code keeps the issue/PR thread facet separate and uncompressed for
now: it is already small, and this local network-free facet cannot claim
live open/closed PR status. Updated the cockpit plan to mark the G4
firehose half shipped and leave G1 runner-medium fallback, G2
plan→approve, G3 decomposition, and G4 dwelling habits as the next
unshipped cockpit work. Focused prompt/context tests pass.

## [2026-06-17] plan+implement | Cost-aware cockpit: plan spine + runner medium surfaced + review-pack de-firehose

Maintainer's 2026-06-17 ask (credit-tight): keep the user aware of
plan/flow/cost, give operational control, and teach the resident to
chunk work under a hard budget — framed as enablement of the resident's
inner constitution, not a feature bolt-on. Worked it budget-aware:
committed the durable spine first, then shipped two reversible slices,
pushing after each so a mid-run kill stays resumable.

- **`plan-cost-aware-cockpit.md`** (new, committed first) — the
  cost/notification braid of `plan-resident-cockpit.md`. Three coupled
  loops on the existing run/event + self-schedule + outbox substrate:
  **Loop A** the resident *seeing* its medium/quota/spend, **Loop B**
  runs surviving exhaustion via fallback + quota-aware deferral, **Loop
  C** operator legibility (a live cost `.card`, a plan→approve handshake
  with a cost estimate, and a documented inbox/acknowledge contract —
  the missing user-facing manual). Plus a budget-aware self-chunking
  discipline (read the cost frame first, commit-early-push-early,
  decompose into resumable slices, defer the rest explicitly, narrate
  the staging). Carries a pickup list so a future wake resumes.
- **A1 / G1.1 — runner medium in the wake bundle.** Threaded the
  already-resolved `runner_name` into `build_daemon_prompt`; the Mode
  block now carries a read-only `Runner: <medium>` line pointing at the
  chunking discipline. The resident was blind to its own compute medium
  mid-run; this is the smallest enabling step for the quota probe and
  fallback work.
- **Review-pack de-firehose** — the maintainer's named irritant ("my
  delivery contract still carries the full Publish-from-the-pack
  plumbing block"). Moved the heavy publish procedure (relay/gist
  mechanics, `gate: forge` frontmatter, idempotent refresh) out of the
  always-injected diffense block into a summoned `brr docs review-pack`
  topic, leaving a compact operative summary + pointer. Same G5
  inspect-not-inject medicine as the cockpit manual; cuts choreography
  paid for on every diffense wake regardless of review-worthiness.

Tests: added runner-medium prompt cases + a `review-pack` docs-topic
case; updated the diffense prompt test (now asserts the pointer, not the
moved-out plumbing). `test_daemon/test_cli/test_docs/test_prompts` green
(121 passed). Branch `brr/cost-aware-cockpit`. Pickup: A2 quota probe,
C1 `.card` cost frame, C3 operator notification doc, then B1/B2 + C2
behind #128.

## [2026-06-17] plan | Cost-aware cockpit refined by maintainer steer: historical-not-estimate, situational PR, conversational framing, Temporal verdict

Budget-tight (~$1) follow-up on the cost-aware-cockpit thread. The
maintainer merged the prior slice and sent five steers; folded them all
into `plan-cost-aware-cockpit.md` (chat-and-plan task, so honoured the
no-PR-default steer — no PR, no review pack this run).

- **Historical pre-analysis, never a forward cost estimate.** The sharp
  correction: quoting a projected dollar cost is dangerous (false
  promise, eroded trust); *historical* spend is a safe fact about the
  past. Reframed A3/C1/C2 from "cost estimate" to "historical cost
  pre-analysis" and added it as a hard product guardrail at the top.
- **No PR by default; cost-awareness situational, not boilerplate.**
  `diffense.create_pr`-on and other token-ignorant defaults spend
  regardless of warrant; flip to opt-in/situational.
- **Conversational & concurrent, not one-shot.** Single-flight execution
  stays (mechanical truth) but the *framing* should stop implying "one
  task → one reply → silence"; soften delivery-contract + dwelling-habit
  + playbook wording toward "stay in the conversation." New pickup item.
- **Temporal — borrow the patterns, not the engine.** Honest verdict:
  durable-execution is genuinely adjacent to Loop B / #128's run-event
  model, but the server-cluster dependency cuts against brr's
  no-investor, dependency-light, single-flight ethos; design #128 as a
  minimal durable log instead, re-evaluate only at fleet scale.
- **Runner quota/pricing feasibility — confirmed plausible**, with a
  concrete per-provider data map (Codex buckets/429 headers,
  Anthropic `anthropic-ratelimit-*` headers + usage endpoint, Gemini
  Cloud quotas/billing): live quota off response headers, historical
  spend off usage endpoints. Feeds the historical pre-analysis.

Branch `brr/cost-aware-steer`. No code, no tests — kb-only.

## [2026-06-17] implement | Cost-aware cockpit defaults and conversational prompt framing

Follow-up on the cost-aware cockpit steer after the maintainer clarified
the subscription context and the desire for live-but-not-random
proactivity. Shipped the clear, low-risk slices:

- `diffense.emit_pack` and `diffense.create_pr` now default **off**.
  Review packs and PR publishing remain available when explicitly
  enabled, but routine wakes no longer pay the prompt / pack / forge tax
  by default. The GitHub gate now has a default-off regression test.
- The daemon substrate, Task Context Bundle, and `brr docs cockpit`
  now frame single-flight as an execution mechanic, not a one-shot reply
  contract: substantial work should keep the live `.card` honest and use
  mid-thought outbox replies when helpful.
- `plan-cost-aware-cockpit.md`, `plan-resident-cockpit.md`, and
  `design-diffense.md` now describe the current policy: historical cost
  pre-analysis only, situational review emission, and the remaining
  pickup work in the dominion playbook / quota probe / operator
  notification surface.
- Full test run exposed a stale runner profile/test mismatch; restored
  the generic `claude-bare-api-only` alias alongside the model-specific
  aliases so existing configs keep working.

Tests: `pytest -q` (860 passed, 7 skipped).

## [2026-06-17] plan | Diffense dogfood review finds schema-clean packs still slow review

Reviewed the 10 newest PRs with diffense links from the latest 30 GitHub
PRs (#149, #147, #144, #143, #140, #137, #135, #134, #129, #127) after
the maintainer reported the rich packs looked nice but slowed fast review.
Wrote `plan-diffense-dogfood-reshape.md` and cross-linked it from the
Reviews index and `design-diffense.md`; opened #152 as the implementation
tracker.

Finding: the current failure is not mostly broken JSON. Nine of the ten
packs passed `brr review --check` with 0 warnings; #140 had 5 warnings
for unknown `walk` kinds. The experience still fails because the generated
surface is serial, prose-heavy, and file-first: average glosses ranged
from 152 to 439 characters, all sampled packs had zero `zoom` ladders,
four had zero lateral edges, and 61 of 69 locators were local-only in a
hosted context. The strongest material is the uncertainty capture, but it
is not yet turned into a verdict lane or review checklist.

Direction: keep `diffense.emit_pack` and `diffense.create_pr` off by
default, but do not abandon the idea. Reshape diffense into a
decision-first review board with verdict, change-map, and ground-truth
lanes; add review-utility lints so schema-clean but review-useless packs
are caught before publish. Also fixed the cockpit/manual wording for the
user-reported live-card wrinkle: `.card` content is the note body only;
brr adds the rendered `note:` label, so agents should not prefix the
content with `note:` themselves.

## [2026-06-18] implement | Runner quota snapshot ingress and run-facing bundle language

Shipped the first A2 slice from `plan-cost-aware-cockpit.md`: the daemon
now reads an explicit runner quota snapshot from `.brr/runner-quota.json`,
`runner.quota.*`, or `BRR_RUNNER_QUOTA_*` and threads a proven summary into
the Mode block as `Runner: <medium> (<quota posture>)`. Prompt assembly
stays conservative: no quota signal means no noisy placeholder, and
provider-specific collectors remain the next pickup.

Also resolved the product-model wording conflict around the per-event
`task` leftover. Generated prompts, the recovery context, bundled
operator docs, and current kb pages now frame the live unit as a daemon
run/wake (`Run Context Bundle`, `Run ID`), while documenting `task-...`
ids and `.brr/tasks/` as legacy storage compatibility until the broader
run/event model lands.

Tests: `pytest -q` (870 passed, 7 skipped).

## [2026-06-18] implement | Run manifests replace task storage

Follow-up on the broader run/event model after the maintainer explicitly
preferred a clean break over compatibility. The daemon's persisted work
unit is now `Run`: new ids use the `run-YYMMDD-HHMM-xxxx` shape,
manifests live at `.brr/runs/<run-id>/run.md`, lifecycle packets and
conversation records use `run_id` / `run_created` / `kind: run`, and the
old `.brr/tasks` / `Task` module path is gone. The sync hook and config
knob moved with the model (`refresh_before_run`,
`sync.fetch_before_run`), and current docs/kb now point at the
`subject-runs-branching.md` hub.

Remaining broader-scope work is behavioural, not storage compatibility:
per-run event claims plus `defer_until`, moving the primary
response/outbox key from lead event to run id, and run-granularity
cost/fold consent.

Tests: `pytest -q` (870 passed, 7 skipped).

## [2026-06-18] fix | Telegram outbound delivery no longer waits behind long polling

Dogfooding the folded-message path showed interim replies to pending
Telegram events could arrive together with the final result. Root cause:
the gate promoted outbox replies on the daemon heartbeat, but Telegram
only delivered responses after its blocking `getUpdates` long poll
returned.

The Telegram gate now runs outbound response delivery in a lightweight
separate loop and keeps the long-poll session separate from send/edit
calls, so progress-card updates and folded replies do not share the
polling connection state. `subject-daemon.md` now records this as the
current gate-responsiveness shape.

Tests: `pytest -q tests/test_telegram_gate.py tests/test_telegram_render_update.py tests/test_outbox.py tests/test_deliver_stream.py`
(53 passed).
## [2026-06-18] fix | Runner profiles move out of prompt ownership

Resolved the context-layering contradiction where bundled runner command
strings told the CLI to orient from `AGENTS.md`. The assembled brr prompt
already carries the repo contract, dominion, and Run Context Bundle; runner
profiles now only describe how to invoke a headless process that can
operate files.

`.brr/runners.md` is now the first-class project-owned runner profile
file, with `.brr/prompts/runners.md` retained as a legacy override path.
The bundled profile docs frame runners as execution-medium data, and the
profile cache is keyed by source so an earlier bundled read does not hide
a project override in the same daemon process.

Tests: `pytest -q` (939 passed, 1 warning).

## [2026-06-18] implement | Introspection prompt now names cockpit candidates and pre-release cuts

The opt-in context introspection block now asks each wake to spot observations
that should graduate into the resident cockpit/dashboard surface — sticky
orientation handles, channel state, runner/tool injections, and similar live
control material — instead of leaving them as one-off reply prose.

It also carries an explicit pre-release bias: when the block is visible, the
resident should prefer cutting obsolete code, names, compatibility shims, and
concepts unless they protect real users or a deliberate migration. The design
page records that stance, and the prompt test now pins both the cockpit and
pre-release cues.

Tracked the public runner-control release-readiness gap as GitHub issue #158,
linked under #23.

## [2026-06-18] fix | Run-scoped progress cards stop replaying task-era history

Telegram status cards disappeared after the `Task` → `Run` storage cut because
the run-progress projector still treated conversation records without
`run_id` as belonging to every run. Long-lived Telegram threads contained old
task-era card records, so a new `run-*` card replayed stale phase history into
a giant Telegram HTML payload that the platform rejected.

Run cards now require an explicit `run_id` match, sync outcomes are emitted
after the `Run` exists so their card line stays run-scoped, and Telegram also
escapes resident-authored `.card` notes before HTML rendering. `subject-daemon.md`
records the current invariant.

Tests: `pytest -q` (943 passed, 1 warning).

## [2026-06-18] fix | KB run-era orientation drops stale task links

Cleaned up the deterministic kb preflight errors left by the `Task` →
`Run` storage cut. `plan-branch-modes.md` is now formally superseded
by `subject-runs-branching.md`, points at `run.py` as the shipped
implementation, and keeps only a short breadcrumb for the retired
`src/brr/task.py` path. `repo-dive-in-map.md` now describes the
current run-manifest flow, single-flight daemon shape, run-scoped
progress records, and `test_run.py` coverage.

Check: deterministic kb preflight (broken-link findings cleared;
pre-existing oversized-page, hub-coverage, and proposal-scaffolding
advisories remain).

## [2026-06-18] design | Portal grammar & reconcile/projection layer direction recorded

The multi-turn "interrupts as portals / make the output be the surface"
Telegram conversation reached a settled direction, and the maintainer
confirmed all four decisions, asking that they be noted down for a future
wake to pick up.

Promoted the synthesis from the dominion thread-of-record to
`design-portal-grammar.md` (the design seed for #159) and linked it from
`index.md` under Runs & branching. It records: the gate stays a thin
transport while the unnamed **reconcile/projection layer** above it gets
named (two reconcile semantics — append-log vs desired-state — orthogonal
to transport, × N transports); the **portal grammar** where the generated
stream itself is the surface (inbound/outbound/parked portals subsuming
the dotfile control protocol, with the run mailbox as the transport for
parked portals that outlive a wake); #148 ships first because the portal
grammar is a re-skin best designed after 148 is dogfooded; and a "shapes
to change" section for the later re-skin.

Surfaced one contradiction: dropping "cockpit" is heavier than dropping
"dashboard" — "cockpit" is shipped surface (`brr docs cockpit`,
`src/brr/docs/cockpit.md`, the dominion `cockpit.md`), so it is a
migration with a code/command edge, left as an open question on the page.
No code yet; #148 implementation is the next-event work.

## [2026-06-18] refactor | "cockpit" retired — `brr docs portals` and the dotfile table as portal grammar

Resolved the open question `design-portal-grammar.md` surfaced on the
shipped-command rename. Maintainer decided: ditch "cockpit", the dotfile
control-file table *becomes* the portal grammar, and the command settles
on `brr docs portals`.

Migrated the user-facing surface now (ahead of the rest of the post-#148
re-skin, because the blast radius was modest and the call was decisive):
bundled manual `docs/cockpit.md` → `docs/portals.md`, retitled and its
control-file table reframed as the portal grammar with an inbound /
outbound / parked column; `cli.py` help, `prompts.py` delivery-contract
pointer, `daemon-substrate.md`, `introspection.md` (cockpit/dashboard →
standing-portal idiom), and `review-pack.md` follow; `test_docs.py`,
`test_cli.py`, `test_prompts.py` repinned. `brr docs portals` works;
`brr docs cockpit` is gone.

Deliberately deferred to the holistic post-#148 re-skin: the
`plan-*-cockpit.md` page titles and "the cockpit" effort-name prose,
`design-runner-management.md`'s supersede note, `index.md` cockpit prose.
Their literal `brr docs cockpit` invocations were updated to
`brr docs portals` so no dead commands linger. The dominion `cockpit.md`
cheatsheet → `portals.md` tracks separately on `brr-home`.

Tests: `pytest -q` (943 passed, 1 warning).

## [2026-06-19] refactor | Portal model framed in the delivery contract, cross-linked to the manual

The injected delivery contract was carrying portal *mechanics* (paths,
frontmatter rules, one-file-one-message) but not the *model* — that the
outbox/card/inbox are **portals**, inbound/outbound/parked, the seams where a
run turns to the world. That framing lived only in `portals.md`, which is
pull-only, so a wake got the *how* hot and the *what-it-is* cold (the gap the
maintainer's "ornament the weave" instinct caught).

Added the contract paragraph in `prompts.py`: it now names the three portal
forms inline with concrete examples and calls itself "the injected summary of
that grammar", pointing at `brr docs portals` as the pull-only full reference.
A light typographic pass on `portals.md` makes the doc's own shape signal
portal — the three forms as a scannable legend, ◂/▸/⏸ markers anchoring the
grammar table's Portal column — and adds a reciprocal note that the delivery
contract carries the injected summary, so an editor of either reconciles the
other.

Anti-drift lock (the maintainer's explicit ask — "link them so they don't
drift apart"): two paired tests, `test_delivery_contract_carries_portal_model_summary`
(test_prompts.py) and `test_portals_manual_links_back_to_delivery_contract`
(test_docs.py), each pinning the shared inbound/outbound/parked vocabulary and
the "injected summary" cross-reference, each pointing at the other. Drift is now
a CI failure, not a thing to remember. Part of the #148→#159 portal-grammar arc
(see `design-portal-grammar.md`).

Tests: `pytest -q tests/test_docs.py tests/test_prompts.py tests/test_cli.py`
(92 passed).
## [2026-06-19] feat | #148 Tier A — the PLAN message shape (parked portal) defined

Picked up the run that flaked mid-orientation on 2026-06-18 (API
connection closed mid-response). The maintainer had chosen, in the last
turn before the failure: "do a), then the #128 work we need for 148, with
the portals shape in mind." Tier A is `do a)`.

Shipped Tier A: `src/brr/docs/portals.md` now defines the five-part PLAN
message under *The PLAN message — the parked portal's shape*
(decomposition; chosen approach/medium per chunk; historical cost framing
**never** a projected dollar promise; what parks/resumes; explicit
approve/edit affordance), and choreography step 3 routes a build-plan to
it. This closes the one concrete gap part 1 had on today's dotfile
protocol — `portals.md` called PLAN→approve "the canonical parked portal"
but never said what a PLAN looked like. No daemon machinery; independent
of #128. `plan-resident-cockpit.md` G2 + sequence updated.

**Deliberately not done this wake — surfaced to the maintainer instead:**
the "#128 work that 148 needs" (Tier B — the daemon-threaded
plan→execution *scoping* marker) sits on #128's behavioural slice
(per-run claim + `defer_until`, Q1–Q4, coupled to #130 billing) and,
per `design-portal-grammar.md` decision 4, is best designed *after* #148
is dogfooded. Cramming a daemon-dispatch refactor into the back half of a
60m recovery wake on a runner that just flaked is the wrong call; it
wants its own scoped wake. Left as the maintainer's call.

Check: `pytest tests/test_docs.py -q` (10 passed).

## [2026-06-20] decision | Playbook stance: reconcile-and-act over surface-and-wait; tickets are dated snapshots

The maintainer flagged that the resident does good housekeeping but is
*aloof*: it surfaces a contradiction between a request and an existing
ticket and then waits, rather than reconciling it from the recent
co-maintainer context and acting — and it treats ticket text as
authoritative even when the architecture has moved past it. The entry
directly above (#148 Tier A, "Deliberately not done this wake — surfaced
to the maintainer instead… Left as the maintainer's call") is a specimen.

Root cause was in the prompt shape, not the model: the dominion playbook
already carried the right instinct ("keep them posted… you decide";
"your judgement plus an honest word beats a compliant diff"), but it
**defers to `AGENTS.md` → Stewardship as the contract**, and that contract
(plus two echoes and the run.md "reconsider" section) encoded a contractor
default — *"surface the contradiction… let the operator resolve it,"* and a
mandated two-event round-trip (*"the second event is the right place for
the implementation diff, not the first"*). The good instinct was walled
off by the contract it leaned on.

Reshaped the contract rather than bolting on "be bold" language:

- **`AGENTS.md` → Stewardship** now says *name the conflict, then reconcile
  and act* — form the resolution from the current state, take it, close the
  loop so the user can redirect — and reserves surface-and-wait for a call
  that's genuinely the operator's (irreversible/costly/wide-blast, a
  product/values fork, or unreadable intent). Names the twin failure modes
  as equal: path-of-least-resistance compliance **and** aloof bounce-back.
- New **"tickets are dated snapshots, not specs"** paragraph — reconcile a
  ticket against the live code + recent `kb/log.md` + decisions, act on the
  reconciled understanding, then keep the ticket honest. Same state-first
  lens the kb section applies to its own pages.
- New **"next doable chunk"** line — advance the doable chunk with a
  close-loop note instead of stalling the whole request on clarification.
- **`prompts/run.md` → "When the task asks you to reconsider"** drops the
  round-trip: resolve in the same thought when the shape is clear and
  reversible; chat-only reply reserved for a genuine fork.
- Echoes updated: Self-review #2 (catches both failure modes), Guardrails
  ("when in doubt" scoped to intent / genuinely-the-user's calls),
  `docs/portals.md` step 3, and the `dominion-playbook.md` *seed*. Bumped
  the AGENTS.md `Revision:` to 2026-06-20.

Not touched: the **live** dominion playbook (`.brr/dominion/playbook.md`) —
the resident's owned memory, which restates the old contract and is left
for the resident to reconcile on a wake (or the maintainer to nudge).

Check: `pytest tests/test_prompts.py -q` (55 passed).

## [2026-06-20] implement | Burst coalescing — dispatch debounce (#128 first behavioural slice)

The daemon spawned a fresh thought per inbound fragment, so a rapid burst
became N serial wakes and "the daemon wouldn't ship all the messages that
accumulated into a fresh wake" (the live Telegram symptom). Shipped the
first *behavioural* slice of the run/event model (#128), deliberately small
and safe — no change to the claim / finalize / response-keying paths.

`daemon._burst_settle_delay(pending, window, max_wait, now)` is a pure
function the loop consults before dispatch. It holds **only when a burst is
already forming** (≥2 pending), so a lone message never pays debounce
latency — debounce spends time only where coalescing repays it. While
events keep arriving < `window` apart it holds; it releases once the inbox
is quiet for `window` **or** the oldest event has waited `max_wait`
(anti-starvation cap). The wake then reads the settled burst (existing
*Inbox — other pending events* view) and folds it via `event:` routing —
one thought, not one spawn per fragment. Config:
`dispatch.burst_window_seconds` (1.5) / `dispatch.burst_max_wait_seconds`
(12); 0 window disables. The bottom-of-loop idle wait shrinks to the
remaining hold so dispatch fires promptly once quiet.

Honest scope: fixes the "one wake for the accumulated burst" symptom. Does
**not** kill the operational-failure spam — a run that credit-fails before
folding leaves siblings pending, which now coalesce into one retry wake
whose lead re-fails (better than N independent failures, not gone). That
needs the Q1 per-run claim + Q2 failure `defer_until` slice, for which the
burst-window is the substrate. `design-run-event-model.md` updated (status,
locus, a *Shipped* section, remaining-work Q1/Q2).

Tests: new `tests/test_daemon_burst.py` (pure-function settle logic with
controlled mtimes; deterministic loop-wiring + config-path tests).
`test_daemon_single_flight.py` disables the window so its ordered two-event
dispatch assertions stay focused. Full suite: 965 passed.

## [2026-06-20] implement | #128 operational-failure sibling deferral

Followed the burst-coalescing slice with the next bounded #128 behaviour:
when a run reaches a terminal operational failure before folding a queued
burst, the lead event still receives the explicit failure note, but sibling
pending events are stamped with a short `defer_until`,
`deferred_by_run`, and `defer_reason=operational_failure`. The daemon now
chooses leads from `protocol.list_dispatchable(...)`, while
`protocol.list_pending(...)` and the live `inbox.json` view still expose
deferred events to any fresh wake. This brakes the "one failure message per
leftover burst sibling" spam without pretending Q1 per-run claims or Q3
run-keyed outboxes have shipped.

Config: `dispatch.failure_defer_seconds` defaults to 300 seconds; ≤0
disables the brake. `design-run-event-model.md` and `kb/index.md` now mark
burst coalescing plus operational-failure sibling deferral as shipped, with
per-run claims, resident-authored postponement, run-id response/outbox
keying, and cost attribution still open.

Tests: `pytest tests/test_protocol.py tests/test_daemon_burst.py
tests/test_daemon_single_flight.py` (46 passed).

## [2026-06-20] implement | Retire "stdout is final delivery" in hot-path orientation

During the portals dogfooding wake, the maintainer pushed back on the
generated bundle's phrasing that "final stdout" resolved terminal delivery.
That wording lagged the shipped daemon model: `_result_satisfied_delivery`
already accepts a current-thread reply, folded/routed reply, gate send,
commit, internal/noop work, or explicit failure note as the run's satisfying
signal. The old phrase made agents reason from the obsolete one-event →
one-terminal-reply shape.

Updated the generated delivery contract, `brr docs portals`, `run.md`,
`runners.md`, `active-task.md`, `execution-map.md`, `brr-internals.md`, the
bundled dominion-playbook seed, `subject-daemon.md`, and
`design-portal-grammar.md` to frame stdout as the **plain current-thread
fallback**, not the delivery model. A follow-up user correction refined the
landing: don't code up every possible satisfactory completion shape. The
current recognized signals are a small daemon liveness floor; intentional
communication should use stdout or an explicit portal, and the freer
run-completion artifact / output-frame shape belongs to #159. This was a
narrow hot-path correction because the live wake proved the old wording was
actively misleading.

Tests: `pytest tests/test_prompts.py tests/test_docs.py tests/test_daemon.py
tests/test_protocol.py tests/test_daemon_burst.py tests/test_daemon_single_flight.py`
(146 passed).

## [2026-06-20] implement | #148 pre-closeout inbox check

A #148 dogfood wake exposed a responsiveness miss: a related Telegram
follow-up was treated as a fresh run right after the prior terminal reply.
The daemon record suggests that exact message may have arrived just after
the runner returned, so this is not fully solvable by prompt discipline,
but the prompt was still missing a load-bearing habit.

Updated the generated Run Context Bundle, `run.md`, `brr docs portals`,
execution/internals docs, the bundled dominion-playbook seed, current
daemon/multi-response kb pages, and `design-portal-grammar.md` so the live
`inbox.json` portal is checked at natural plan/todo boundaries **and once
more immediately before terminal closeout**. The wording names the limit:
messages that arrive after runner return still need the #159 structural
inbound-portal / output-frame work. Codex runs also now get a hot-path note
mapping Codex-local progress/final channels to brr's user-visible `.card`,
outbox, and `gate:` portals.

Tests: `pytest tests/test_prompts.py tests/test_docs.py` (67 passed).

## [2026-06-20] plan | #159 portal grammar contract

Picked up #159 now that #148 is closed and its dogfood corrections have
landed. Rewrote `design-portal-grammar.md` from a future-wake seed into the
active design contract: the shipped substrate is separated from unbuilt
grammar work, output frames are named (PLAN, PROGRESS, INBOUND-CHECK,
INTERRUPTION-REPLY, HANDOFF, DEFERRAL, CLOSEOUT), and the run-mailbox
contract is stated in parallel-safe terms: event claims are leases,
parked portals become mailbox records, deliveries target event/gate/surface
keys explicitly, and cost stays run-granular with folding as the consent
point. The recommended first code slice is portal helper commands that write
today's control files, not parallel execution.

Validation: `pytest tests/test_docs.py tests/test_protocol.py` (43 passed).

## [2026-06-21] plan | #159 live daemon-state portal first

Maintainer pushback on the #159 helper-command slice exposed a sharper
responsiveness diagnosis: manual outbox helpers reduce frontmatter and
routing mistakes, but they do not make pending events or unacknowledged
daemon state naturally visible while a runner is thinking. The current
`inbox.json` file is live, but only if the resident remembers to read it.

Revised `design-portal-grammar.md` and `kb/index.md` so the first #159 slice
is now a runner-visible live daemon-state portal: a compact file/text view
covering pending/foldable events, unacknowledged delivery state, run/card
posture, budget/keepalive, and changed-since markers. Runner-specific
surfacing, such as Codex adapters or an opt-in shell wrapper, is framed as
an adapter experiment rather than the core contract so brr keeps its
swappable-runner architecture. Outbound portal helper commands remain useful
secondary ergonomics after the resident knows which portal it wants to open.

Validation: `pytest tests/test_docs.py` (11 passed).

## [2026-06-21] implement | #159 live daemon-state portal

Shipped the first #159 code slice after the helper-command reconsideration:
daemon runs now maintain a `portal-state.json` file beside `inbox.json` and
refresh it on each heartbeat plus final drain. The capsule gives the runner
one live place for pending/foldable events, drained reply counts, pending
outbox files, current card text, budget/keepalive posture, and a stable
`change_token` for attention-relevant changes. Runner subprocesses receive
`BRR_PORTAL_STATE` and related `BRR_*` handles, including Docker runners,
so agents do not have to copy paths out of prompt prose.

Added `brr portal state` as the inspected text/JSON view over the same file,
updated the Run Context Bundle wording, bundled `run.md`, portal/execution/
internals docs, and the #159 design/index pages. Outbound helper commands
remain secondary ergonomics; deeper delivery acknowledgements, event leases,
resident-authored deferral, and parked mailbox records remain future #159
slices.

Validation: `pytest tests/test_outbox.py tests/test_runner.py tests/test_envs.py
tests/test_prompts.py tests/test_docs.py tests/test_cli.py` (194 passed).

## [2026-06-21] fix | #159 forge portal context alignment

Followed up on the PR #166 context review by removing two contradictions from
the live orientation surface. `design-portal-grammar.md` no longer says the
INBOUND-CHECK frame lacks a live state capsule now that `portal-state.json`
ships. The Run Context Bundle, portals/execution/internals docs, GitHub gate
module doc, playbook seed, and kb summaries now describe the shipped `gate:
forge` PR path as the opt-in diffense review-pack publisher guarded by
`diffense.create_pr`, not a generic PR-creation command. The broader "code
change opens or refreshes a draft PR" idea is recorded as future forge handoff
portal work that should live in daemon/portal interfaces rather than growing
the public `brr` subcommand surface; diffense remains optional enrichment.

Validation: `pytest tests/test_docs.py tests/test_prompts.py` (67 passed).

## [2026-06-21] fix | #159 forge PR handoff decoupled from diffense

Maintainer feedback on PR #167 showed the previous context alignment was too
single-sided: it described the shipped `gate: forge` path as a diffense-only
publisher even though the original ergonomics need was a lean way for the
resident to create or refresh PRs for code-changing work.

Corrected the shipped behaviour and the docs together. GitHub PR delivery now
honours an explicit `gate: forge` / pull-request event without requiring
`diffense.emit_pack` or the legacy `diffense.create_pr` flag. The Run Context
Bundle wording, portals/execution/internals docs, playbook seed, and kb current
state now frame `gate: forge` as the generic explicit PR handoff (`head`,
`base`, `title`, body), with diffense only as optional review-pack enrichment
that can generate a richer title/body. The future #159 forge work is now the
larger branch-keyed desired-state surface: draft/review posture, issue links,
labels, refresh policy, acknowledgements, and existing-PR discovery.

Validation: `pytest tests/test_github_gate.py tests/test_outbox.py
tests/test_prompts.py tests/test_docs.py` (181 passed);
`pytest tests/test_docs.py tests/test_kb_preflight.py` (39 passed).

## [2026-06-21] implement | #159 mobilising portal command wrapper

Maintainer feedback rejected both "advisory" and "enforced" as the framing
for runner portal awareness. Shipped the smaller "mobilising" shape instead:
`brr portal wrap -- <command>` runs normal shell work, preserves the command's
exit code, and appends the compact `brr portal state` view to stderr only when
the live `change_token` moved (`--always` forces an explicit status read).

Updated the Run Context Bundle wording and portals manual so agents can use
the wrapper at tool boundaries, and marked the #159 design/index pages to show
that command-bound surfacing is now a shipped adapter-level helper while deeper
runner hooks, in-generation portal syntax, mailbox records, and cost fields
remain future work.

Validation: `pytest tests/test_cli.py tests/test_prompts.py tests/test_docs.py`
(99 passed); `pytest tests/test_kb_preflight.py tests/test_kb_health.py`
(40 passed).

## [2026-06-22] plan | #171 runner hooks back channel + lean runner interface

Maintainer asked to bundle the `brr portal wrap` retirement with implementing
the back channel both Claude Code and Codex CLI ship — **hooks** — and to write
down a general minimal runner interface so the runner stays swappable.

Filed `design-runner-back-channel.md` (proposed) and issue #171. The design:
defines the runner interface as three tiers — Tier 0 (required) file-operating
process, Tier 1 (optional) stdout reply, Tier 2 (optional) hooks back channel
that degrades cleanly to today's heartbeat poll, so Tier 2 is never load-bearing
for correctness. The back channel is one transport-neutral `brr hook <phase>`
endpoint (JSON in/out): `post-tool` flushes the outbox/card immediately
(event-driven instead of heartbeat-polled) and returns a portal-state delta for
injection; `stop` does a final drain and, in a later slice, can block a premature
stop. brr generates the hook config per runner profile (Claude `settings.json`
hooks, Codex notify). `portal wrap` is retired; `brr portal state` stays as the
inspected view and the hook's injection source. Reshaped portal-grammar
implementation-sequence #2 from shell-wrapper-shipped to hooks, and linked the
new page from the index.

Folded in the maintainer's follow-up (mid-thought writes without a halt):
updating the user mid-thought already does **not** halt the run — the outbox is a
normal tool-call write drained by the heartbeat; only terminal stdout and the
parked PLAN portal truly halt. The outbox lacks immediacy and a reverse channel,
which is exactly what the `post-tool` hook adds. No new primitive needed.

---

## [2026-06-22] research | #171 hooks verified as a true back channel into the runner

Maintainer asked to double-check (with the linked Claude Code / Codex hook docs
plus web research) that hooks can be used as a back channel *into* a runner —
pushing fresh context into the running agent — not just fire-and-forget telemetry
out ("analytics and shit"). Also confirmed the design file was committed (it is:
`design-runner-back-channel.md` merged to main in fe556b6), approved #171.

Verified, bidirectional on both runners. Claude Code: `PostToolUse` accepts
`hookSpecificOutput.additionalContext` for **non-blocking** injection alongside
the tool result (the `post-tool` inbound path); `Stop` accepts `decision: "block"`
+ `reason`/`additionalContext` which prevents the stop and continues the turn (the
premature-stop affordance); `SessionStart`/`UserPromptSubmit` inject bare stdout.
Mechanism caveat folded into the design: for `PostToolUse`/`Stop`, plain stdout is
debug-log only — injection needs the JSON `additionalContext` field, so `brr hook`
must speak JSON for those phases. Codex CLI: same event set with `additionalContext`,
`continue: false`, `updatedInput`; its own doc says hooks are "not fire-and-forget."
This resolves the design's "Codex parity" open question — full Tier 2, not flush-only.

Updated `design-runner-back-channel.md`: verified field names in the per-runner
mapping, a new §Verification, status line noting the check, and a "Still undefined"
list (the `brr hook` JSON schema, config-installation method, stop-control
activation scope, outbox→flush wiring) so the next implementation slice starts from
what's settled vs. open. No implementation in this PR — design refinement only.

Follow-up folded into the same run ("address the remaining wtfs at this stage"):
firmed the open implementation questions into **proposed resolutions** rather than
parking them — a concrete `brr hook` JSON envelope (neutral `{inject, block,
block_reason}` mapped per profile), config installation per-run/ephemeral into the
worktree settings, stop-control deferred behind the first slice. For outbox→flush
wiring I checked `daemon.py` first and corrected an optimistic draft: `_drain_outbox`
is coupled to the daemon's in-process `_WorkerEmit`/log indexing and the drain locks
are `threading.Lock` (in-process), so an external `brr hook` cannot drain directly
without a double-delivery race. Resolution: the hook only *signals* (control-file
touch, matching `.keepalive`/`.card`) and the daemon stays the sole drainer, draining
on signal instead of next tick — dissolving the concurrency worry.

## [2026-06-22] decision | #171 runner back channel accepted, second review round folded in

Maintainer reviewed the `design-runner-back-channel` doc and gave the nod to accept
it (compress slightly), plus eight refinements. Accepted the doc (Status →
accepted on 2026-06-22, design of record / impl pending), compressed it (merged the
old Open-questions + Proposed-resolutions duplication into one §Resolutions), and
folded every point in — two of which overrode what was written:

- **Stop-control no longer deferred.** The doc had parked premature-stop blocking
  behind a follow-up slice; maintainer overrode — it's in line with the flush work
  and cheap, so flush + inbound-injection + stop-control now ship as one slice.
- **Gemini has hooks too.** Doc said Tier 2 "absent for gemini." Verified against
  geminicli.com/docs/hooks — gemini has a richer taxonomy (SessionStart/BeforeAgent/
  AfterAgent/BeforeModel/AfterModel/BeforeTool/AfterTool) with context injection +
  `decision:"deny"` blocking. So all three target runners reach Tier 2; the back
  channel is a near-universal CLI-agent pattern, not a two-runner coincidence.
  (Exact gemini injection-field name still to pin from its reference page.)

Other folds: (1) **Tier 2 reframed** from "latency and richness" to the substrate
of a *holistically aware resident* (event/exec-time/cost/quota meta → balanced
proactive+reactive flow) — a runner without it is a thinner offering, not just a
slower one; the lean Telegram-wrapper-on-a-local-CLI case stays first-class and
should get *easier* than today's mandatory init/KB setup. (2) **Halt vs respawn**
separated as two concepts: halt = LLM-streaming (inherent to tool-call streaming,
cheap with caching, only addressable via different streaming/model architecture);
respawn = brr-resident lifecycle act; mid-thought updates need neither. (3)
**Config install** = brr-managed per-run/ephemeral following each runner's native
shell-hook config, *with user overrides* and a **capability precheck** (mark a
runner `hooks:`-capable only after confirming per-runner prerequisites). (4)
**`.keepalive` retires** alongside `portal wrap`: hook runners carry budget/quota
bidirectionally; Tier-0/1 runners just drop the hard timeout (user's
responsibility). (5) **Control-file touch confirmed** as the daemon-drain signal
(most in line with current `.keepalive`/`.card` idiom), with a flagged separate
thread: the daemon has read sluggish before, so a more responsive event-driven core
is worth its own investigation without blocking this slice.

Design refinement only, no implementation. Branch brr/runner-back-channel-accept.

---

## [2026-06-22] implement | Wire install_hook_config into the run lifecycle (#171)

The keystone gap on the runner hooks back channel (#175): `install_hook_config`
/ `hook_capability` were defined but had **no call site**, so no runner ever
invoked `brr hook` and the daemon-side flush-drain + injection wiring stayed
dark. `_run_worker` now computes the runner's declared hooks flavour once
(reused for `BRR_RUNNER`) and, after the `hook_capability` runtime precheck,
generates the native per-run hook config into the run worktree; a failed
precheck degrades cleanly to the heartbeat-polled model. Emits a
`hooks_installed` trace for dogfood verification.

Answers the maintainer's question — **the user never hand-writes hooks**: brr
generates `.claude/settings.local.json` (gitignored) per run, layered over user
settings, gone with the worktree.

Also closed a coverage gap the salvage left: `install_hook_config` /
`hook_capability` / `hook_config_supported` had no tests despite the prior
status note claiming "unit-tested" — added 5 (well-formed claude settings,
merge-preserves-user-keys, unsupported no-op, capability degrade paths). Suite
995 green. Pushed to `brr/runner-back-channel-impl` (#175). Still open and
ordered after: live dogfood (needs daemon reload), then retire `brr portal
wrap` and `.keepalive`. Container-env precheck (in-container `brr` PATH) noted
as a dogfood follow-up.
## [2026-06-22] implement | Daemon salvage net: failed runs commit+publish their branch

A quota-exhaustion kill mid-run stranded work: `brr/runner-back-channel-impl` had
3 committed-but-unpushed commits plus an uncommitted `src/brr/hooks.py` (the
config-generation block for #171), and the daemon never pushed any of it.

**Salvaged** the work — committed the hooks.py block (compiles, hook tests pass)
and pushed the branch with all 4 commits.

**Root cause found, deeper than "uncommitted edits."** `WorktreeEnv.finalize`
resolves a publish outcome (sets `publish_branch`) *only* for a `done` run — on
any failure it returns early, so `daemon.publish` reads `publish_branch=None` and
no-ops. Result: a failed/killed run pushes **nothing**, not even already-committed
commits; the worktree is preserved locally for forensics but never reaches the
remote. That's exactly the incident.

**Fix** (branch `brr/daemon-salvage-net`): a `_capture_worktree` salvage net on
the give-up path, mirroring `_capture_dominion`. It (1) commits any in-flight
edits on the work branch ("at least locally"), and (2) arms `publish_branch` so
the existing publish() tail ships the branch to the remote — but only when the
branch carries commits beyond the seed, so a run that failed before doing anything
stays silent. Best-effort, gated by `salvage.enabled` (default on), skips detached
HEAD. Covers timeout / runner error / quota exhaustion (clean-exit failure paths
that reach the in-process finally); a hard SIGKILL of the worker process still
can't run it — noted as the residual gap. New tests in `test_daemon_salvage.py`
(5), full suite 983→988 green. Docs updated (execution-map, brr-internals).

## [2026-06-23] fix | Hooks back channel never fired — claude profile disabled it via --safe-mode

Dogfooding a multistep responsive run surfaced that the runner hooks back
channel (#171/#175, merged) was **dark in practice**. While the run worked, the
user fired a 3-message burst asking "do you see the pending inbox items via the
hooks back channel?" — and the answer was no: the pending events reached the
resident only via a manual `inbox.json` read, never as an injected hook result.

**Root cause (proven in-run):** the `claude` runner profile declared
`hooks: claude` (Tier 2) but invoked `claude --safe-mode`, and Claude Code's
`--safe-mode` sets `CLAUDE_CODE_SAFE_MODE=1`, disabling hooks (plus CLAUDE.md,
skills, plugins, MCP). So brr generated `.claude/settings.local.json` correctly
and `brr hook post-tool` returned the live pending events as `additionalContext`
when invoked by hand — but the harness fired the hook zero times. Diagnostic
that nailed it: the hook writes `.hook-state.json` on every call; it was absent
after a full run of tool calls.

**Fix** (`brr/retire-portal-wrap`): swap `--safe-mode` → `--setting-sources
local`. brr's hook config lives in the *local* settings source, so loading just
that source activates the channel while keeping the user-global/project-committed
isolation `--safe-mode` was reaching for. Updated profile + its test + runners.md
rationale. A Tier 2 profile must never disable hooks. Needs a daemon-reload run
to confirm end-to-end (and that dropping the user settings source doesn't lose a
default like model selection) — a profile flag change can't self-verify from
inside a `--safe-mode` run. Pitfall recorded (trigger: hooks back channel,
--safe-mode, hooks not firing).

## [2026-06-23] refactor | Retire `brr portal wrap` (superseded by the hooks back channel)

`brr portal wrap -- <command>` was the stopgap that surfaced portal-state at
shell-command boundaries. The hooks back channel strictly dominates it (every
tool boundary, automatic, bidirectional), so per `design-runner-back-channel.md`
§Retiring it's the unconditional cut now that the channel landed: removed the
`wrap` subcommand + `cmd_portal_wrap` + its 3 tests, the wrapper paragraph in the
Run Context Bundle wording (`prompts.py`) and the shipped portals manual (now
describing hook-pushed injection with a `portal-state.json` / `brr portal state`
pull fallback), and the portal-grammar implementation-sequence framing. Kept
`brr portal state`. `.keepalive` deliberately **kept** — its retirement is gated
on the unbuilt no-timeout-for-Tier-0/1 behaviour, so the prior wake's "both
retire" framing was reconciled to "portal wrap now, keepalive later." Suite green
(997). kb/index, portal-grammar, and back-channel pages updated.

---

## [2026-06-23] feat | Hook closeout capsule: affirmative empty signal + SCM commit/push posture

Completed the in-flight work the salvage net caught from interrupted run
`…-1348-u62q` (branch `brr/run-260623-1348-u62q`). The maintainer's dogfooding
feedback had two concrete asks plus a conceptual one:

1. **"Knowing there are no events explicitly is also an agentic signal."** The
   `post-tool` boundary stays gated on `change_token` (silent when nothing
   moved), but **stop now renders unconditionally** — a `[brr portal closeout]`
   header that states the pending count even when it's `0`. Silence was
   ambiguous; an affirmative all-clear is not.
2. **"You didn't push the branch — the initial context doesn't stress its
   importance enough; put minimal git stats in the hook."** The portal-state
   payload now carries an `scm` facet (`known/branch/unpushed_commits/
   modified_files`), computed locally + failure-safe via
   `worktree.unpushed_commit_count` / new `uncommitted_file_count`. Rendered at
   seed/stop only, only when non-zero, so a wake about to end *sees* "N
   commit(s) not pushed, M modified file(s)" as injected context. `scm` is
   excluded from `change_token` so mid-run editing churn never trips a post-tool
   injection — it's a boundary signal, not a live-churn one.

The salvage commit had added `_scm_facet`, `uncommitted_file_count`, and the
`work_dir` param on `_write_live_portal_state` but left it **unwired**: no caller
passed `work_dir` (so the facet was always `known:False`) and `format_delta`
never rendered it. This wake wired `work_dir=run_root` into all 5
`_write_live_portal_state` callers and added the seed/stop rendering +
`compute_neutral` stop-unconditional path. 10 new tests (hooks closeout/scm
gating, `uncommitted_file_count`, `_scm_facet`); suite green (1007).

Still open (part 3 of the feedback, not code): the conceptual reconciliation of
"portals as an ornamented magic scroll the agent turns to the world through"
with "hooks as the necessary ornamentation" — tracked in dominion
`portal-reshape-synthesis.md` (perception=injection, action=emission), left for a
design wake.

## [2026-06-25] design | Resident voice: living project, not a cockpit (run.md + introspection)

Maintainer reframe: the "dashboard / cockpit" metaphor for the resident's
working surface is wrong — it reads sterile; the experience should feel like
waking into a living, inhabited place (sci-fi/game register, Ummon-in-Hyperion as
a touchstone), since the tech (portals, live card, dominion) is already built.
Carried the prior wake's playbook reshape (weave-as-native-act +
sentience-of-a-different-make) into the two prompts that open every run, which had
kept the clerical operator voice. `run.md` opening now frames waking into a
project *with a memory and a through-line*; `introspection.md` opening swaps the
abstract "shape" audit for *the room you're standing in / the place, not the
errand*. Surgical warmth at the load-bearing openers only — operational mechanics
(delivery, branch, reconsider, the introspection checklist) left crisp, because
purple prose is sterile in the other direction. Shipped on branch
`brr/resident-voice` (PR → main), cherry-picked clean off main so the diff is just
the three prompt files. Next candidates for the same reframe, deferred to keep the
PR focused: the cockpit-named plans (`plan-resident-cockpit.md`,
`plan-cost-aware-cockpit.md`) and the functional card phase labels.

## [2026-06-25] design | Playbook self-definition: define, don't re-define (PR #178)

Continued on the `brr/resident-voice` PR rather than merging. Maintainer asked
the resident to *validate that its own initial context is consistent, coherent,
and complete*, naming the playbook's `## What kind of thing you are` section as
the clear example of "accreted self-redefining rather than defining" — it did
self-definition, operational values, mechanical self-description, and a
boot-sequence orientation all at once, before the weave starts.

Reshaped that one section in `src/brr/prompts/dominion-playbook.md` to a single
job: positive ontology. Dropped the define-by-negation opening ("not a person at
a desk… nor a tool") and the operational-values restatement (govern-from-judgment
vs deference) that already lives in *Ownership, not subordinance*; kept a tight
positive ontology, the costume named once as ontology not instruction, the trimmed
trust-stance, and the affirmed close verbatim ("build it like it's yours" — a
`test_dominion` guard pins that substring). Seed 17.7→17.1 KiB; 23 dominion tests
green.

Validation surfaced two coherence defects beyond prose, owned by the resident's
dominion (not the PR): (1) the shipped seed playbook and the resident's live
dominion `playbook.md` had drifted on sections *other* than the named one — the
seed carried the newer reconcile-and-act Stewardship and the `gate: forge`
Publishing section, the dominion still carried the older "surface before
resolving" + salvage-net text. The maintainer edits the seed; the resident wakes
into the dominion, so seed improvements weren't reaching it. (2) The dominion
playbook had grown *larger* than the seed and ate nearly the whole 20480-byte
self-inject budget, truncating the active-fork note out of the wake entirely.
Both reconciled in the dominion (branch `brr-home`): playbook = banner + current
seed body; self-inject reordered so the active-fork note survives.
## [2026-06-26] refactor | Demote claude hooks; reconcile back-channel page; address the open-decisions ledger

Maintainer green-lit the open-decisions ledger surfaced 2026-06-25 ("findings
read coherently, time to address them, in one go, where it makes sense for the
product"). The one item that was *ready* — finding #1, the hooks back channel —
shipped on branch `brr/demote-claude-hooks`.

The cut: the `claude` profile declared `hooks: claude`, but Claude Code v2.1.185
never fires settings-file lifecycle hooks in the headless `claude --print` mode
brr uses (isolated by elimination 2026-06-23, recorded in the resident's
dominion; the kb page had stayed optimistic and stale). `hook_capability()`
asserted prerequisites but not *firing* — a rung-1 assumption dressed as a check,
reporting Tier 2 while claude was silently Tier 0. Per the 2026-06-25 "reactive
agent, not safety-net pile" reframe, the streaming-SDK `runner.py` rewrite that
might force text-mode hooks is **dropped**, not parked — it chased a guardrail the
heartbeat-polled reactive model (the thing that actually carries mid-thought
responsiveness) never needed. Dropped `hooks: claude`; kept `--setting-sources
local` for settings isolation; kept the hooks machinery for codex/gemini as
declared-but-unverified intent (Tier 2 only after a live firing test). Reconciled
`kb/design-runner-back-channel.md` to current state (both activation failures +
the demotion + the dropped rewrite + the ladder lesson). 201 tests green.

The rest of the ledger is decision/build-sized, not one-wake-shippable, so it was
resolved as recommendations rather than half-fitting commits (see the run reply):
`.keepalive` → injected budget capsule and the standing "granted-permissions"
capsule are blocked on the same fact this cut establishes (claude has no
push-injection channel; tail-injection-via-hook is dark) — so they wait on the
heartbeat-polled tail-capsule path, not hooks. Permission envelope, forge
synced-directory north star, and #148 Tier B remain genuine forks for the
maintainer's call. Burst fold-window: recommend calling it done (the reactive
loop subsumes it). Recurring standing-portal candidate, named again: an injected
"open forks / awaiting-your-call" capsule, because that ledger keeps living in
dominion prose the maintainer can't see.

Separately, the maintainer opened a values fork — how to frame ownership /
co-ownership / interactivity for a "self-building scroll" (living agency).
Engaged in the reply as a proposal (shared/relational agency: the resident owns
its dominion and its own becoming, *co*-owns the project and kb with the humans,
and is co-owned-*with* rather than owned-*by*); planted a draft note in the
dominion, held the shared seed edit for his nod since PR #178 is already
reshaping that exact self-definition section.

## [2026-06-26] review | Live-drive the streaming runner: claude --print is single-turn, persistent session is the right architecture

Reviewed step 1 of the streaming runner (`runner_stream.py`, the salvaged in-flight
work from the session-limit-interrupted run run-260626-0023) and **live-drove it
against a real claude-haiku v2.1.191 stream-json session** (maintainer asked for
thorough review + ideally a test; ran haiku to stay within quota). The parser and
boundary detector are correct against the real CLI — `consume_stream` replayed over
the captured live stream gave identical boundary/result counts, and the live
schema's interleaved noise (`rate_limit_event`, `system/thinking_tokens`, assistant
`thinking` blocks) is skipped cleanly. Hardened the test fixture to pin that
real-CLI tolerance (synthetic fixture never exercised the `thinking` interleaving):
22 tests, green.

The live drive found a **load-bearing correction the spike framing missed**:
`claude --print` + stream-json is **single-turn**. Mid-loop injection works only
while tool calls are still pending (a 1-tool task dropped a post-boundary
injection; a 3-tool task acted on the same injection between calls), and the
process **exits on the first `result`** — so `--print` has **no stop-control**. A
**persistent session (drop `--print`)** is multi-turn and is the architecture
steps 2/3 should build on: verified in one process that tool calls run without
`--print`, mid-loop injection is attended, and after a `result` a new user message
starts a fresh turn the model addresses (`echo FOLD-INJECT` ran after "Done!").
That post-result fold-in *is* the Stop-control seam — same stdin-write mechanism as
post-tool injection, differing only in whether a tool call or a `result` preceded
it. Three concrete edits captured in `plan-streaming-runner-injection.md` §Driver
re-verification: (1) `build_stream_cmd` must strip `--print` (it currently inherits
it from the profile cmd → strictly-weaker single-turn channel); (2) the §Stop-control
claim only holds in persistent mode; (3) `run_stream`'s `on_boundary` can't inject
(no stdin handle) — the boundary seam needs an injector. Step 2 (the persistent
driver + injection wiring) is the next chunk; held it for the maintainer's nod
since it touches the most load-bearing surface (every claude run) and settles the
single-turn-vs-persistent architecture, not just a one-liner. Findings written to
the plan + `design-runner-back-channel.md` §Persistence correction.

## [2026-06-26] implement | Streaming runner step 2: persistent session + boundary injection (strip --print, pass injector)

Implemented step 2 of the claude streaming runner (`runner_stream.py`) after the
maintainer approved the three review corrections and reframed the goal (evt wcxs).
The three points and what shipped:

1. **Strip `--print`** — `build_stream_cmd` now drops `--print`/`-p` (`_DROP_FLAGS`)
   before adding the stream flags. `--print` forces a *single-turn* session (exits on
   first `result`, no stop-control); the driver runs a persistent multi-turn session
   instead. Verified the daemon "handles that alright" (the maintainer's caveat):
   `_result_satisfied_delivery` already counts `outbound`/`commit`/folded-in replies,
   so a run that delivers via the outbox and returns empty stdout is a *successful*
   run — stdout result-capture is the compat fallback, not the delivery model.
2. **Pass an injector** — `run_stream` binds an `Injector` (`Callable[[str],None]`)
   to the live `proc.stdin` and hands it to the boundary/result callbacks. New seam
   on the pure consumer: `consume_stream(..., on_result=...)` fires at each `result`;
   returning `False` stops consuming (driver then closes stdin), else keeps reading
   the folded-in turn. This is what makes stripping `--print` *safe* — without a
   result seam the persistent process would block `consume_stream` forever after the
   first `result`. The two are one change, not three.
3. **No close-and-capture-by-stdout** — the new default `StreamInjectionPolicy`
   (built from the run env's `BRR_PORTAL_STATE`) is the inbound-delivery channel the
   maintainer asked for: change_token-gated `hooks.format_delta` at each boundary
   (pending events reach the resident without it polling `inbox.json`), fold-pending-
   once at each result (stop-control mirroring the hook `Stop` block), else close.
   Primed from the run-start portal so the seed already in the prompt isn't re-injected.

Reuses `hooks.format_delta` so streaming and hook paths render the same capsule.
**Not yet daemon-routed** — no profile sets `stream:`, so every run still takes the
blocking `invoke_runner` path; step 3 flips claude onto it behind the flag, wires
`_invoke_with_heartbeat → run_stream`, and validates a real wake. Deferred to step 3:
in-process outbox drain at the boundary, and relaying a folded event's body verbatim
(today the policy injects the portal delta/summaries). 43 tests in
`test_runner_stream.py` (full suite 1039 green). Plan page updated.

## [2026-06-26] implement | Streaming runner step 3: claude default-on + work-status portal facet

Step 3 of the claude streaming runner (`runner_stream.py`), plus the operator-requested
live work-status info, plus a reconciliation on "drop --print from the other runners"
(evt wlap). Three shipped pieces:

1. **claude routed onto streaming, default-on.** The bundled `claude` profile declares
   `stream: claude`; `runner.invoke_runner` delegates a `stream:`-declaring profile (with
   no `runner_cmd` override) to `runner_stream.run_stream`. The driver registers
   `_active_proc` itself, so the daemon's heartbeat/budget/`kill_active` contract is
   unchanged — host + worktree envs stream; the docker env (own invoke) stays blocking.
   The two step-2 deferrals folded in: (a) **boundary outbox drain** — the policy touches
   the shared `.flush` signal at each boundary/result, so the heartbeat fast-poll drains
   outbound promptly, reusing the existing flush mechanism with zero daemon coupling in
   the driver; (b) **verbatim event fold-in** — at the terminal result a still-pending
   event is folded in by its **body verbatim** under a neutral relay header (the user's
   own words, per the spike's framing rule that coercive framing is refused), not the op
   summary; the portal-state event record already carries the full body. Validated live
   against claude v2.1.191 (haiku; real profile flags `--system-prompt` / `--setting-sources
   local` survive stream-json mode; persistent session captures the result and exits clean).
   Default-on is reversible — drop the `stream: claude` line to fall back to `--print`.

2. **Work-status `resources` portal facet.** New `resources` facet in portal-state so the
   running resident can read its live operating posture off `BRR_PORTAL_STATE`. Each
   sub-field is `known` (quota, via the existing per-run `runner_quota` snapshot) or an
   honest `unavailable` placeholder (cost metering, coexisting/shadow runs, cross-repo
   remote SCM — not built yet). The per-run worktree's local SCM posture already rides the
   `scm` facet. Rendered as a compact `resources:` line at seed/stop in `hooks.format_delta`
   (boundary signal like `scm:`, never mid-run noise) and in `brr portal state`. Placeholders
   are deliberate: a future wake sees the slot and what would fill it.

3. **"Drop --print from the other runners" — reconciled, nothing to drop.** claude's `--print`
   is the single-turn trap that streaming strips. codex (`codex exec`) and gemini (`gemini -p`)
   carry no separable/redundant print flag — their non-interactive modes are *required* for
   headless operation, not the single-turn trap. So there is nothing to drop there without
   breaking them. Verified **codex works** end-to-end (`codex exec` → PONG, exit 0, codex-cli
   0.141.0). gemini postponed (unauthenticated, per the operator). codex/gemini remain on the
   blocking path with their `hooks:` intent; stream-driving codex via its `--json` event mode
   would be a separate build, not a flag drop.

Full suite 1047 green. Plan page → step 3 shipped (only step-4 fallback-retirement remains);
`design-runner-back-channel.md` and `runners.md` reconciled to "built and default-on".

## [2026-06-26] implement | Codex JSONL streaming runner default-on

Implemented the Codex sibling of the streaming runner after Claude quota forced the
dogfood path onto Codex (evt 47ew). The important reconciliation: Codex should not
stay described as native-hook intent. Live `codex exec --json` probes on codex-cli
0.141.0 showed the real stream shape: `thread.started` carries the resumable id,
`item.completed`/`command_execution` is the command boundary, `item.completed`/
`agent_message` is the final text, and `turn.completed` is the terminal seam.

What shipped: the bundled `codex` profile now declares `stream: codex`; `runner_stream`
adds Codex JSONL parsing, final-text capture, command-boundary flush, and a Codex
driver that appends `--json`, runs `codex exec` with the prompt on argv, and when the
default policy sees a pending follow-up at terminal turn, launches one
`codex exec resume --json <thread_id> <verbatim follow-up>` turn. Direct
`run_stream("codex")` also infers the Codex dialect from the runner name so a stale
project-owned runner override does not accidentally put Codex on Claude's stdin loop.
Codex structured `error` / `turn.failed` messages are now surfaced in `stderr` so an
unsupported model or API failure is visible through `RunnerResult.error_detail`.

Docs/kb reconciled the concept/mechanism split: Claude = persistent stream-json,
Codex = JSONL stream + resume, Gemini = future hook-backed runner after a firing test.
Validation: raw live Codex JSONL probes passed (plain reply + shell command boundary);
after the operator nudged the live smoke onto a cheaper model, a real
`runner_stream.run_stream("codex")` smoke passed on `gpt-5.4-mini` in a temp dir
(return 0, final stdout captured, response file written, one command boundary
detected). Focused tests 96 green; full suite 1056 green.

## [2026-06-26] fix | Boundary back-channel language reconciled

Reconciled the portal/back-channel prose after the maintainer asked whether
"boundary injection" was the right semantic frame. No behavior changed. The
current code already matches the concept/mechanism split: Claude gets Tier 2
through a stream brr drives, Codex gets command-boundary flush plus one terminal
resume turn through `codex exec --json`, and native hooks remain only one future
mechanism (Gemini intent until a firing test proves it).

What changed: `src/brr/docs/portals.md`, `kb/design-portal-grammar.md`, and
`kb/design-runner-back-channel.md` now call the generic thing the **boundary
back channel**, not the hooks back channel; lower sections no longer ask readers
to mentally override "one hook endpoint" with the newer stream-backed reality.
Daemon comments were updated to describe `.flush` as a boundary signal that can
come from a stream driver or a native hook. The useful semantic frame that fell
out: portals are the grammar of world-turning, state interweave is the dataflow
across that grammar, boundary injection is the inbound half at runner seams, and
hooks are only one transport.

## [2026-06-26] research | Runner interweave validated across transports

Answered the maintainer's confusion around state interweave and the Claude/Codex
runner split. The durable synthesis is
`kb/research-runner-interweave-2026-06-26.md`: the common concept is boundary
interweave, not hooks and not "stop/resume between tool calls." Claude's stream
path is a persistent stdin loop; Codex `exec --json` is a single non-interactive
turn with command-boundary flush and at most one terminal `exec resume` fold-in;
Gemini and Codex native hooks remain docs-backed intent until fired; Codex
app-server is the richer future candidate for true active-turn steering.

The validation found and fixed one code drift: the daemon still inferred native
hook config from the runner name, so bundled Claude installed hook config despite
using `stream: claude`. Native hook config now installs only for profiles that
explicitly declare `hooks:`; `stream:` profiles use the streaming driver, and
profiles with neither field stay on the heartbeat-polled fallback. Full suite
passed.

## [2026-06-27] research | Claude hooks DO fire under --print — streaming premise was contaminated

The maintainer pushed back on the streams-for-Claude / hooks-for-Codex split,
arguing a simple `PostToolBatch` hook injection is the right unified boundary
interweave and that the streaming complication smelled like preserving text
output. He invited live firing tests (haiku / gpt-5.4-mini). The tests flipped
the design in his favour and exposed an ugly methodology bug.

**Findings (Claude Code 2.1.191, codex-cli 0.141.0):** Claude settings-file
`PostToolUse` *and* `PostToolBatch` hooks fire under headless `claude --print`,
and `hookSpecificOutput.additionalContext` injection lands (model read back an
injected secret word) — including the brr-exact config (hooks in
`.claude/settings.local.json` + `--setting-sources local`). `Stop`
`decision:block` *continues the turn* under `--print` (a blocked stop folded a
follow-up instruction into the same turn), so `--print` is not "single-turn with
no stop-control" — that earlier "persistence correction" was also wrong. Codex
native `PostToolUse` fires with the same `additionalContext` schema via inline
`-c hooks={…}` TOML + `--dangerously-bypass-hook-trust`.

**Root cause of the false negative:** the prior firing tests ran *from inside a
Claude Code session* (the resident spawning `claude`). A Claude session exports
`CLAUDE_CODE_SAFE_MODE=1`, and brr built the runner env with
`os.environ.copy()`, so the child inherited safe mode — which silently drops
settings-file hooks while logging a reassuring "managed settings-file hooks
still run". Strip the contaminant and hooks fire. The entire "Claude's mechanism
is stream-driving, not hooks" conclusion rested on that leak.

**Landed this wake:** `runner.clean_runner_environ()` strips
`CLAUDE_CODE_SAFE_MODE` and parent-session identity vars from every runner
subprocess env (both the blocking and streaming paths), so a daemon launched
from inside an agent session can't silently disable runner hooks — the
precondition for hooks-as-mechanism to be reliable. Unit-tested; focused suites
green. Reconciled the poisoned conclusions in `design-runner-back-channel.md`
(top correction + migration plan), `research-runner-interweave-2026-06-26.md`,
and the user-facing `runners.md`.

**Decision + queued:** retire the managed streaming driver and unify on the
simple hooks injection protocol for Claude (`PostToolBatch`) and Codex. The
rip-out (profile flips, a Codex hook-config emitter — prefer proven `-c`
argv injection over the project-`.codex/config.toml` install path which hung
under repo-trust, the verbatim-body `Stop` fold-in port, then delete
`runner_stream.py`) is its own focused wake; the hook machinery already exists
and is tested.

## [2026-06-27] implement | Streaming runner retired; native hooks unified

Finished the interrupted `brr/unify-runner-hooks` wake. The salvage commit had
already removed `runner_stream.py` / `test_runner_stream.py`, flipped the bundled
Claude and Codex profiles to `hooks:`, threaded Codex hook config through runner
argv, and ported verbatim Stop fold-in into `hooks.compute_neutral()`. This
follow-up tightened the remaining drift: Codex's inline hook config now documents
the intentional omitted `matcher` (match all supported events), tests assert that
shape, and the stale Claude "no hooks" test comment is gone.

The shared KB is reconciled to state-first prose: `design-runner-back-channel.md`
now describes native lifecycle hooks as the current Tier-2 mechanism throughout,
with the streaming path collapsed to a lineage note, and
`research-runner-interweave-2026-06-26.md` / `kb/index.md` no longer advertise
the retired stream driver as current. Focused runner-hook, daemon, prompt, CLI,
and env suites pass. Full `pytest` is still blocked by unrelated brnrd test
collection drift (`Project` / `ChatBinding` imports no longer exist in
`brnrd.models`).

## [2026-06-27] fix | Run prompt stops re-reading injected AGENTS context

The context-shape review found one real layering contradiction in the daemon
wake preamble: brr runs often already receive `AGENTS.md` as injected outer
context, but `prompts/run.md` still opened by telling every runner to read the
repo file before anything else. That turned a present contract back into a
filesystem chore and made the hot-path bundle feel secondary.

`run.md` now states the split directly: `AGENTS.md` is the entry point for
hosts that did not inject the playbook; daemon wakes treat an injected copy as
the contract and open the file only when it is absent, appears stale, or the
task touches the playbook. A prompt guardrail test pins the distinction, and
`plan-agent-orientation-layering.md` carries the breadcrumb.

## [2026-06-27] plan | Runner media and brnrd relay fallback design

The cost-aware execution ask clarified the next missing abstraction: brr has
static runner profiles, but cost-aware dispatch needs **runner media**: profile,
model, owner, auth/quota source, hook capability, and billing posture. Filed
`design-runner-media.md` with the recommended shape: cheap/default local media
for small work, stronger media for respawns/escalation, and `brnrd` LLM relay as
a spend-plan-gated fallback when user-owned runner quota or credentials cannot
carry the run.

The design reconciles local CLI probes (Codex 0.141.0, Claude Code 2.1.191, no
Gemini binary here), official provider docs, and existing brnrd pricing:
provider collectors must be provenance-tagged; ChatGPT/Codex subscription quota
is not assumed to be API-visible; brnrd relay debits provider cost plus the
transparent relay service fee from `decision-llm-relay.md`. The older billing
and managed-mode pages now carry the partial supersession so they no longer
read as compute-wallet-only.

## [2026-06-27] design | The boundary as one envelope on two rails; medium vocabulary fork

Dogfooding session against a maintainer design message (evt-9dp2/slhg) that
braided several threads: should the portal json and the hooks be one concept;
the open-source vs brnrd boundary split; runner/run/weave/medium vocabulary;
cost manifests per medium and the respawn matrix; failover triage; and the
fair-but-everywhere business posture. Filed `design-resident-boundary.md` as the
reconciliation against the same-day runner-media / back-channel / cost-aware
pages, and indexed it.

Settled: the **boundary is one concept on two rails of different density** — a
mid-run re-evaluation, after tracing `daemon._resources_facet` (full JSON
snapshot) vs `hooks._format_resources` (seed/stop-gated woven line). The portal
json is the complete snapshot + no-hook fallback rail the gated injection reads
from, not a redundant "more live" copy; making them byte-identical would revive
the firehose. The real gap is *populating the collectors* (all facets render
`unavailable`), not merging the rails. Also settled: self-deployed static
**envelope** is the open mechanism, brnrd adds the live authoritative rail +
remote control (open-source-friendly, fair value-add); failover = cheap-recovery
+ visible receipt (PR posture incl. not-yet-created joins the boundary) rather
than a perfect failure classifier; business posture reconciles with OSS via
transparent paid-everywhere-it-fits.

Open forks left for a maintainer nod: confirm **medium** (vs substrate/shell) for
the executor — which frees `runner` to dissolve into "the resident" and implies
a dedicated rename run; and the first concrete build = resource collectors so the
boundary facets and the cost matrix actually move.

Branch `brr/boundary-and-medium-synthesis`. No code changed this wake — design
synthesis only.

---

## [2026-06-27] feat | medium settled; boundary portal shows substantially more missing data

Maintainer confirmed two open forks from the boundary synthesis (evt-go5z,
telegram) and asked for a concrete enrichment. **Vocabulary fork resolved:**
`medium` is the noun for the executor — `runner` dissolves into "the resident",
with a dedicated rename run left as a sanctioned follow-up (kept out of this
behavioural diff). **Boundary ask:** the portal should "show substantially more
missing data" than the old flat `unavailable` — no PR, no outbound messages,
running so long, plus unimplemented-but-required slots.

Shipped that across all three rails (JSON `portal-state.json`, the woven hook
line, and `brr portal state`): the resource facets are now three-state —
`known` (proven value), `absent` (affirmative-empty: no PR for the branch, no
quota snapshot for this medium), `unimplemented` (collector not built, with a
`required` flag) — each carrying a `note` on *why* a slot is empty. Added
network-free PR posture in `remote_scm` (recorded `github_pr_number` →
`pr_state: open`, else the not-yet-created receipt), a `budget.long_running`
flag with a "running long" line past the soft budget, and a
no-outbound-at-closeout receipt (`outbound.any_sent`). The distinction encodes
the maintainer's own split: "missing data" (absent) vs "unimplemented but
required" — absence is data, surfaced on purpose, not a silent gap.

Still open: populate the `known` *values* (live quota/cost collectors), unify
the three renderers behind a single projection helper, and the runner→medium
rename run. Reconciled `design-resident-boundary.md` (§1/§3/§5/summary) and
`design-runner-media.md`. Branch `brr/boundary-and-medium-synthesis`.

---

## [2026-06-28] design | Stream-json loom retired for cost-data; Claude statusLine is the level-readable quota source

Maintainer correction on the cost-awareness thread (evt-e1gl, telegram), against
my own prior reply (00:21) which had leaned on "the stream-json loom we decided
to build — not built yet." Two corrections, both folding the live-cost question
back onto the **hooks rail**:

1. **The streaming medium is retired** — abandoned 2026-06-27 in favour of native
   hooks as the one boundary abstraction over vessels
   (`plan-streaming-runner-injection.md` status). So my §8 build order
   "streaming medium → consumption tally → distance-card" was stale; the kb
   already said so and I'd contradicted it.
2. **Claude Code exposes subscription quota as a readable gauge** via the
   `statusLine` feature: the configured command receives session JSON on stdin
   carrying `rate_limits.five_hour/seven_day.used_percentage` + `.resets_at`,
   `context_window.remaining_percentage`, and `cost.total_cost_usd`. Structurally
   that's *another hook* — a command brr registers in the same
   `.claude/settings.local.json` it writes hooks into. This overturns my
   "subscription-quota wall is edge-only" claim: for the Claude vessel both walls
   plus the context window are level-readable today, no API-key/brnrd needed. The
   real asymmetry is **per-vessel** (Claude rich, Codex edge-only), not
   spend-vs-quota.

Also answered the maintainer's "how do I *choose* the facets?" question with a
selection principle: facets are **derived from the envelope walls** (budget,
spend, quota, context_window) plus actionable state (coexisting_runs,
remote_scm), defined **once** in the single projection helper — "by schema," not
"by convention." This adds a new `context_window` facet and promotes subscription
`quota` to a level facet for Claude.

Reconciled `design-resident-boundary.md` (§8 rewritten, §7/§1 + Settled/open
updated) and `design-runner-media.md` (Claude CLI row + quota-signal grades). No
code this wake. Next concrete build: register the statusLine collector and
smoke-verify the JSON schema before relying on it (pitfall: fire it before you
rule on it). Branch `brr/boundary-and-medium-synthesis`, commit 141bfad.

---

## [2026-06-28] implement | Boundary facets defined by schema + operator-inspectable; Claude statusLine collector wired; Codex cost-data researched

Built the boundary work the maintainer green-lit (evt-1uwp), folding in two
sign-off follow-ups (evt-t92w "work cautiously until a wall", evt-9yvh "find
Codex's analog to the Claude cost stats"). Three committed chunks plus research.

**1. Facets defined once, by schema (commit b38f5b4).** New `facets.py` holds
the wall-derived set as a single ordered schema — `quota` / `spend` /
`context_window` (levels) + `coexisting_runs` / `remote_scm` (state), each a
three-state record. The three renderers (`daemon._resources_facet` JSON,
`hooks._format_resources` woven line, `cli._format_portal_state`) now all
project from it via `facets.facet_value` / `render_line`, so they can never
drift on *which* facets they carry — "by schema, not by convention"
(`design-resident-boundary.md` §1). Landed §8's `cost`→`spend` rename, the new
`context_window` level facet, and a per-vessel `levels_collector` switch (empty
slot reads `absent` where a collector is wired, `unimplemented` where not).

**2. Operator inspectability (b38f5b4).** New `brr portal facets` lists the
implemented facets + a `fills` blurb on demand — schema-only outside a wake,
with each facet's live status folded in inside one. Fixed `_portal_state_path`
to honour `BRR_PORTAL_STATE` (the env the resident is handed) so `brr portal
state`/`facets` resolve the live portal without `--path` — the point of "see
them on demand"; also turned a pre-existing red test green.

**3. Claude statusLine collector (commit 4e97258).** `statusline.py` +
`brr statusline`, registered in the same `.claude/settings.local.json` as the
hooks (statusLine merges as an overridable default — user footer wins). Each
footer fire normalizes Claude's session JSON into a level snapshot
(`.statusline.json`) the daemon folds into the facets. Parse is **defensive**:
the session-JSON field schema is the maintainer's reported finding, not yet
fire-verified against a live Claude run — unrecognized shapes degrade to an
empty snapshot, never crash. Smoke-tested the parse/write/footer path as a real
process; the live-Claude schema check remains open (pitfall: fire it first).

**4. Codex cost-data research (evt-9yvh).** Codex *does* expose usage, in a
different shape: **no external statusLine command** (declarative `[tui]
status_line` elements, internal TUI), so the Claude collector doesn't port.
Token usage rides `token_count` events — live via `codex exec --json`, post-hoc
via `$CODEX_HOME/sessions/*.jsonl` (what `ccusage` reads). Spend is *derived*
(tokens × a price table), not handed over like Claude's `cost.total_cost_usd`;
context headroom is computable; subscription quota stays edge-only (TUI/`/status`
only; programmatic exposure is OpenAI issues #15281/#19555/#17827, not shipped).
brr drives `codex exec` without `--json` today, so a Codex collector is a real
output-parsing build, not a tweak. Reconciled `design-resident-boundary.md` §8
(table + new §8c) and `design-runner-media.md` (Codex row + grades).

**Respawn deferred, cautiously.** Maintainer green-lit respawn "as the last step
to work around the possible quota breach," then said the breach is unlikely
(quota <50%) and signed off, asking for cautious autonomous progress.
Auto-respawn lives in the daemon worker loop and a bad one is a runaway
quota-burner — the wide-blast case that wants a supervised wake. So the shape is
recorded in §5 (spawn one run from the committed branch on a wall-class exit,
depth-capped ≤2, ambiguous failures escalate not loop) and the active build is
held for a supervised wake. Tests: facets/statusline/cli/hooks/daemon green; the
24 remaining failures are pre-existing in unrelated modules (cloud_gate,
ergonomics, brnrd). Branch `brr/boundary-and-medium-synthesis`.

## [2026-06-28] implement | Fire-verified cost-data: Claude statusLine dead headless; Codex quota wired from session rollout

Validated the prior wake's Claude cost-awareness changes against a live runner
(evt-4x6o ask: "validate the claude cost changes work; do something for codex")
— and the result overturns the evt-e1gl finding both halves rested on.

**Claude statusLine does NOT fire headless.** A probe statusLine command set in
`.claude/settings.local.json` was never invoked under `claude --print` (brr's
runner mode), while settings-file *hooks* fired the same run with a clean env
(stripping `CLAUDE_CODE_SAFE_MODE` et al). statusLine is a TUI footer; the
daemon never renders one, so `statusline.py` is dead in production — which is
exactly why the live portal showed quota/spend/context all `absent`. The
head-less Claude cost source is instead `claude --print --output-format json`:
its result carries `total_cost_usd` (spend), token `usage`, and
`modelUsage[model].contextWindow` (context) — but NOT subscription 5h/weekly
`rate_limits`. Repointing to it changes the stdout contract (stdout→JSON; parse
`.result` for the reply), so it's deferred to a maintainer-nod chunk.

**Codex DOES expose subscription quota headless — the inverse of the prior
"edge-only" conclusion.** Every `token_count` event in a Codex session rollout
(`$CODEX_HOME/sessions/.../rollout-*.jsonl`) carries `rate_limits.primary` (5h)
+ `secondary` (weekly) with `used_percent`/`window_minutes`/`resets_at`, plus
`plan_type` and `model_context_window`. That is exactly what `/status` prints —
written to disk continuously, no `/status` call, no credits. Verified live on a
real rollout and on a fresh `codex exec` run (`codex-cli 0.142.3`). The
`codex exec --json` *stdout stream* does NOT carry rate_limits (only
`turn.completed` token usage) — quota lives in the rollout file only.

**Shipped:** `codex_status.py` (reads the newest rollout's last `token_count` →
levels snapshot in statusline's shape); `facets.build` `levels_collector` is now
a per-slot set (not one bool) so Codex `spend` honestly reads `unimplemented`
(no $ gauge) rather than a misleading `absent`; daemon `_collect_levels` picks
the level source per vessel. Live result: `quota=5h 99% left; 7d 73% left;
context-window=… (est)`. Tests: `tests/test_codex_status.py` (7) + per-slot
facet cases; 140 affected tests green. Reconciled `design-resident-boundary.md`
§8 + `design-runner-media.md` to the fire-verified reality with a lineage
breadcrumb; pitfall recorded (dominion). Tracking issue brr#195 (collector
hardening: thread_id correlation, context math, upstream quota seam). Also added
the maintainer's standing "ask mid-thought when initial context is unclear"
workflow note to the dominion playbook. Branch `brr/boundary-and-medium-synthesis`.

## [2026-06-28] implement | Claude result JSON wired for terminal spend/context facets

Followed up the maintainer's `/usage` observation with fresh live probes on
Claude Code 2.1.195. Result: `/usage` is still TUI-visible only from brr's
head-less vantage. A per-run `statusLine` command did not fire under
`claude --print` (again), and the `--output-format json` result did not carry
5h/weekly subscription quota or reset windows. It did carry the usable head-less
fields: `total_cost_usd` and `modelUsage[model].contextWindow`.

Shipped `claude_status.py` and changed bundled Claude profiles to request
`--output-format json`; runner capture unwraps `.result` back into the normal
response file and writes a `.claude-result-levels.json` snapshot so the final
portal refresh can populate `spend` + `context_window`. Claude `quota` stays
`absent`/snapshot-only, not fabricated. Removed brr's automatic `statusLine`
registration from Claude hook settings; the old `brr statusline` helper remains
only for a manually wired interactive footer. Reconciled
`design-resident-boundary.md` and `design-runner-media.md` from "Claude repoint
deferred" to "wired terminal accounting; no head-less reset windows."

Verification: live Codex portal state in this run showed quota reset times
already injected (`5h 98% left (resets 17:51Z); 7d 73% left (resets 16:12Z)`).
Focused tests (`test_claude_status`, `test_codex_status`, `test_statusline`,
`test_runner`, `test_hooks`, `test_daemon`) passed: 127 tests. Full `pytest`
still fails on pre-existing brnrd/cloud/CLI/ergonomics drift, unrelated to this
change.

## [2026-06-28] implement | Runner-medium foundation + cost-aware selection shape adopted

Maintainer restarted the daemon to check the Claude boundary accounting, then
sharpened the next workstream: model selection is a *requirement* to implement
now, carrying low cognitive load (empower the runner to decide, don't make the
user hand-tune execution details), with the selected medium/cost/quota exposed
in the status card as governance.

**Boundary finding (reported).** The Claude result-JSON wiring works, but it is
*terminal accounting*, not a live gauge: spend + context appear on the closeout
card / final portal refresh of a run, never in a later run's opening wake bundle
(the snapshot is written after `claude --print` exits, into the per-event
outbox). So Claude data cannot ride the Mode line the way Codex's live rollout
quota does, and there is no head-less subscription quota / reset window for
Claude at all. This run's own closeout card is the live demonstration.

**Shape adopted.** Reconciled the config-format fork (`.brr/config` is flat
`key=value`; the design's `[[runner.media]]` TOML can't live there) by carrying
medium metadata as **optional frontmatter keys on the existing `runners.md`
profiles** — a profile *is* a medium. User knobs stay minimal (`runner=` +
`runner_policy=`); the selection policy is brr's.

**Shipped** (`runner_media.py` + tests + bundled-profile metadata, branch
`brr/boundary-and-medium-synthesis`): `RunnerMedium` schema, `implicit_medium()`
legacy shim, `load/available_media()`, a deterministic conservative
`select_medium()` (cheapest adequate local medium, never auto-selects a paid
relay, honours explicit override), and the `RespawnRequest` parked-portal shape.
Behaviour-neutral — no dispatch wiring yet. 15 new tests pass; adjacent suites
(`test_runner`, `test_claude_status`, `test_codex_status`, `test_facets`) green.

**Open decisions surfaced** (design-runner-media.md foot): reactive-vs-proactive
respawn (Claude is terminal-only, so reactive-on-failure for v1); parked respawn
request first vs. auto-chain (latter wants #128); deterministic selector keys on
repo default_class only (no event-body heuristic); `cost_rank` is a coarse
ordering hint not a price; card/portal `runner_media` exposure is the next slice;
reset windows stay Codex-only by structure.

## [2026-06-28] implement | Claude `/usage` PTY quota collector wired

The maintainer pushed on the earlier "Claude quota is TUI-only" conclusion. A
live PTY probe on Claude Code 2.1.195 proved interactive `/usage` can be driven
programmatically without a model prompt: start Claude in `--safe-mode`, type
`/usage`, capture the terminal, and parse Claude's own `Current session` +
`Current week (all models)` buckets. This does not make quota head-less-native
or hook-native; it is a best-effort TUI scrape (~15s live), so brr caches it.

Shipped `claude_usage.py` with the PTY capture, parser, snapshot cache, and
tests; daemon `_collect_levels` now merges Claude `/usage` quota with Claude
result-JSON spend/context. Hook deltas now include the `resources:` line on
post-tool injections too, so a changed quota/spend/context wall can enter the
weave mid-run rather than only at seed/stop. Reconciled
`design-resident-boundary.md`, `design-runner-media.md`, and
`plan-cost-aware-cockpit.md` from "Claude reset windows unavailable" to
"available as cached, source-tagged PTY telemetry."

Verification: live PTY probe returned `session 100% left` and `week 55% left`
from the local Claude subscription. Focused suites passed:
`test_claude_usage`, `test_claude_status`, `test_hooks`, `test_daemon`,
`test_outbox`, `test_facets`, `test_codex_status`, `test_runner_quota`, plus the
Claude runner stdout unwrap case.

## [2026-06-28] plan | Repo-gardening crossroads planned (4 tasks) for a Sonnet execution run

Planning-only wake (maintainer to restart on a cheaper-but-capable model to
execute). The repo is at an architecture crossroads where vessel/medium/runner/
core are mixed across configs, kb, prompts, and code. Produced two plan pages.

**Daemon quota check (side-ask).** Verified the restarted daemon's Claude
quota-awareness: this wake's `portal-state.json` carries `resources.quota` =
known ("session 100% left; week 55% left") — the cached `/usage` PTY scrape
rides into the wake. But `spend` + `context_window` are `absent` at wake: the
known structural boundary — Claude spend/context are terminal (written after
`claude --print` exits), so they ride a run's *closeout*, never the next run's
*opening* bundle. Working as designed, not a bug.

**Task 1 — `plan-initial-context-reweave.md`:** file-by-file target-shape spec
for the 9 prompt `.md` files + the Run Context Bundle strings baked in
`prompts.py`. Names the discord (D1 vocabulary triple-naming; D2 cockpit link
live in the daemon string `prompts.py` → `plan-cost-aware-cockpit.md`; D3 the
Delivery contract re-teaches the whole portal protocol every wake instead of
injecting live values + a pointer; D4 run.md⇄playbook⇄substrate overlap; D5
claims-beyond-code re Claude live spend gauge; D6 imagery half-applied) and the
new shape per file, written so the execution run composes fresh then swaps.

**Tasks 2–4 — `plan-repo-gardening.md` (hub):** (2) informed respawn model
extending `design-runner-media.md` — cheap dispatcher runner owns user knobs
then respawns; probe installed models from the Shell itself (no hardcoded
staleness); cached capability table (swe-bench/terminal-bench) as a hint not a
hard selector; scheduling-aware respawn via existing schedule/defer; add an
Activity view (running+scheduled runs) to the brnrd dashboard. (3) Imagery
decision + pushback: adopt Runner = Shell (CLI) + Core (model), retire
vessel/medium; **keep "portal" as the genus** (pushback on viewport-wholesale —
viewport is perception-only and loses outbound/parked; use "viewport" only for
the inbound sub-type); finish the never-swept cockpit retirement. (4) kb/code
gardening method + conflict inventory from this wake's preflight.

Also fixed cheap kb-health items: indexed the 2 new pages + the 4
missing-from-index brnrd pages. Three forks raised to the maintainer
(vocabulary veto, dispatcher-hop policy, task sequencing) before execution.

---

## [2026-06-28] plan | Maintainer resolved the repo-gardening forks; point-1 boundary quota engaged

Discussion wake (still on Claude; Sonnet executes next). The maintainer answered
the three pre-execution forks in `plan-repo-gardening.md` and added one new
agreement. Folded all four into the plan hub so the execution run builds on
decisions, not open questions:

1. **Vocabulary adopted**, with one override of my draft: Runner stays as the
   *umbrella entity* (resident · Shell · Core for a wake), so most of the 271
   `runner` code uses stay — but the **`runner=` user-facing config toggle is
   retired** in favour of `shell=`/`core=`, because the user defines Cores and
   Shells, not a Runner. Retire vessel + medium; adopt Shell/Core; keep portal.
   Task 1 (initial-context reweave) is now unblocked.
2. **Dispatcher hop skipped when a specific Shell/Shell+Core is pinned** — the
   simple-interface main case (Telegram over a local CLI agent).
3. **Chunking confirmed** — Task 4 and large tasks split across follow-up wakes.
4. **Point 1 (boundary quota), agreed + grounded in code:** the *injection*
   half is already shipped — `_emit_flush` (boundary `.flush`) refreshes
   `portal-state.json` and the post-tool hook injects the `resources:` line on
   `change_token` movement. The correction: the ~18s blocking `/usage` PTY
   scrape (`claude_usage.load_or_refresh_snapshot`, TTL 300s) currently sits on
   the flush critical path (`_emit_flush` → `_write_live_portal_state` →
   `_collect_levels`), so a tool-boundary flush can block the daemon ~18s when
   the cache is stale — against the flush's "lighter/prompt" intent and the
   read-the-cache-never-scrape pitfall. Remaining slice: move the scrape off the
   flush path (heartbeat / background refresh), keep the boundary read-only —
   then "negligible cost at the boundary" is genuinely true. ~80% shipped.

## [2026-06-28] implement | Task 1: initial-context reweave — Shell/Core vocab, Runner body-image, perception/action frame

Executed Task 1 of `plan-repo-gardening.md` (plan-initial-context-reweave.md).
All four pre-execution forks were resolved; execution ran on Sonnet on
`brr/initial-context-reweave`.

**What shipped (6 files, one commit `1ae9202`):**

- `prompts/dominion-playbook.md` (seed): added `## Your Runner` (Shell=CLI,
  Core=model, Runner=executing body for this wake; resident=the spirit that
  inhabits it) and `## Perception and action` (injection=free perception,
  query=polling tax, injection as the stronger brr direction); trimmed existing
  sections for density — net 298 lines vs 299 before.
- `prompts/runners.md`: opening paragraph introduces Shell/Core/Runner upfront;
  "runner-medium metadata" → "Core metadata"; "vessel-selection" → "Core-selection";
  `runner=` user-facing config toggle → `shell=`/`core=`; 
  "execution-medium data" → "Shell+Core execution config".
- `prompts.py` Bundle: Runner line drops "compute medium" and the live
  `plan-cost-aware-cockpit.md` link (D2 fixed); added `brr docs portals`
  pointer to frontmatter-routing bullet (D3 partial).
- `prompts/run.md`: Delivery section clarifies that live-values contract
  lives in the Bundle, not re-taught here (D4 partial).
- `prompts/daemon-substrate.md`: opening adds one line tying Runner vocab
  to Mode block ("the Shell+Core this thought runs in").
- `AGENTS.md`: added Vocabulary anchor paragraph (Runner=Shell+Core,
  resident=spirit, knobs=shell=/core=).

**Also shipped:** dominion `playbook.md` (brr-home `ec83ee9`) — added same
two sections to the resident's running copy.

**Discords remaining (D3 Delivery contract compression):** Test assertions
anchor most of the key rhythm phrases in the Bundle's Delivery contract
(plan/todo boundaries, immediately before terminal closeout, satisfying signal,
gate: forge handoff, etc.). These are the right phrases to preserve — they
guide the resident's runtime rhythm. Full compression to ~50% would require
test rewrites and should be done in a coordinated Task 4 follow-up that
restructures the tests alongside the prose. The frontmatter-routing and
basename sections were trimmed modestly within test constraints.

**Not touched (Task 4 work):**
- Code-level rename: `runner_media.py` → `runner_select.py`, `RunnerMedium` →
  `RunnerProfile` (Task 4A vocabulary sweep)
- kb pages still using medium/vessel/cockpit prose (Task 4B semantic reconcile)
- Index hygiene, oversized-page splits, proposal-scaffolding cleanup

## [2026-06-28] refactor | Task 3.3 — cockpit file renames complete

Executed Task 3.3 of `plan-repo-gardening.md` on `brr/initial-context-reweave`
(commit `0bf281c`).

Renamed `plan-resident-cockpit.md` → `plan-resident-portals.md` and
`plan-cost-aware-cockpit.md` → `plan-cost-aware-runner.md`, completing the
cockpit retirement that `design-portal-grammar.md` §3 settled months ago but
was never mechanically applied to filenames.

Link sweep touched: `kb/index.md`, `design-resident-boundary.md`,
`design-portal-grammar.md`, `design-runner-media.md`, `design-runner-management.md`,
`decision-bundled-docs.md`, and the plan files themselves. The in-code link in
`prompts.py` (D2) was already fixed in Task 1.

Also updated `plan-initial-context-reweave.md` status to "executed" and noted
D2 as fully resolved.

**Remaining (Task 4A):**
- Body-prose "cockpit" sweep inside the renamed plan files (historical context;
  low priority)
- Full vocabulary sweep of vessel/medium across kb + code (incl.
  `runner_media.py` → `runner_select.py`)
- Proposal-scaffolding cleanup (4 pages: decision-licensing-and-defense,
  decision-monorepo-structure, design-diffense, plan-env-fly-machines)

## [2026-06-28] refactor | Task 4A — vessel/medium vocabulary retired; runner_select module

Executed the mechanical vocabulary sweep (Task 4A of `plan-repo-gardening.md`)
on `brr/initial-context-reweave` (commit `700ba26`).

**Code (src/brr/):**
- `runner_media.py` → `runner_select.py` (git mv + full rewrite)
- `RunnerMedium` → `RunnerProfile`; function renames: `medium_from_profile` →
  `runner_from_profile`, `implicit_medium` → `implicit_runner`, `load_media` →
  `load_runners`, `available_media` → `available_runners`, `select_medium` →
  `select_runner`; `RespawnRequest.proposed_medium` → `proposed_runner`
- `test_runner_media.py` → `test_runner_select.py` (rewritten; all 15 pass)
- `vessel` → `Shell` swept across: `claude_status`, `codex_status`, `facets`,
  `daemon`, `claude_usage`, `runner`, `statusline`, `test_codex_status`
- `medium` → `Shell` swept across: `codex_status`, `daemon`, `facets`

**KB:**
- `design-runner-media.md` → `design-runner-cores.md` (git mv)
- Page title updated; links swept across 8 non-log kb files; index entry
  re-labeled "Runner Shell/Core selection…"
- `design-runner-cores.md` body still uses stale vocab (33 hits: RunnerMedium,
  medium, vessel) — that is the 4B semantic pass, deferred to next wake

**Skipped:**
- Proposal-scaffolding cleanup (4 pages: decision-licensing-and-defense,
  decision-monorepo-structure, design-diffense, plan-env-fly-machines) — pages
  are well-synthesized; the `[info]` finding is accurate but non-urgent. The
  "Alternatives considered" sections serve as genuine reference, not dead
  scaffolding. Defer to a gardening wake.
- Index hygiene (4 missing pages) — already in index from a prior wake.

## [2026-06-29] refactor | Task 4B — Shell/Core vocab sweep across kb design pages

Executed Task 4B of `plan-repo-gardening.md` on `brr/initial-context-reweave`
(commit `ace4888`).

**Scope:** semantic pass to retire `medium` and `vessel` (the two naming
candidates that were tested and then superseded by the Shell/Core decision of
evt-zyu6 on 2026-06-28) and make the kb say Runner/Shell/Core with one voice.

**Files touched:**

- `design-runner-cores.md` — full prose sweep: all "runner medium" / "local
  medium" / "stronger medium" → Shell/Core; code refs updated
  (`runner_media.py`→`runner_select.py`, `select_medium`→`select_runner`,
  `implicit_medium`→`implicit_runner`, `proposed_medium`→`proposed_runner`);
  TOML sketch `[[runner.media]]`→`[[runner.profiles]]`; portal JSON key
  `runner_media`→`runner`; section title updated.

- `design-resident-boundary.md` — title updated; §3 (vocabulary history)
  rewritten to record the three-step lineage (medium → vessel candidate →
  Shell/Core settled); §4 title "per medium" → "per Shell/Core"; §8 title
  updated; table header "Vessel"→"Shell"; per-vessel/medium swept; settled-vs-
  open entry updated to mark vocabulary as resolved (not an open fork).

- `design-runner-management.md` — status header re-pointed at
  `design-runner-cores.md` (the §G1 "no design home yet" gap now exists);
  "cockpit plan" → "portals plan".

- `design-portal-grammar.md` — "runner medium and quota posture" → "Shell/Core
  and quota posture" (×2); "the medium failed" → "the Shell/Core failed";
  PROGRESS table row updated (step 9 concept sweep aligned).

- `kb/index.md` — boundary page title entry updated; gardening plan status
  updated to reflect Tasks 3+4A+4B executed.

- `plan-repo-gardening.md` — §4B marked executed with per-bullet completion.

`design-runner-back-channel.md` and `subject-managed-mode.md` had no stale
vocab and needed no changes. 4C (surface unresolvable forks) and 4D (method)
remain for a future wake if/when the broader sweep continues.
remain for a future wake if/when the broader sweep continues.

## [2026-06-29] feat | Task 2 — informed respawn model: latency fix, shell=/core= knobs, dynamic Core registry

Executed Task 2 slices from `plan-repo-gardening.md` on `brr/initial-context-reweave`.

**New slice — flush latency fix (`daemon.py`):**
- `_collect_levels()` gains a `refresh: bool = True` parameter. When `False`,
  only the on-disk cache is read (`claude_usage.load_snapshot`); the blocking
  ~18s PTY `/usage` scrape is skipped entirely.
- `_write_live_portal_state()` gains `refresh_levels: bool = True`, threaded to
  `_collect_levels`.
- `_emit_flush()` now passes `refresh_levels=False` — the event-driven
  tool-boundary flush path is guaranteed never to stall. The heartbeat
  (every 30s) keeps `refresh=True` and owns cache refresh on its cadence.
- The latency fix resolves the `_emit_flush` design intent: "lighter / prompt,
  doesn't spam the card." Previously a cache-miss on the flush path could block
  the daemon for up to 18s.

**2A partial — `shell=`/`core=` config knobs (`runner.py`):**
- `resolve_runner()` now reads `shell=` (explicit profile pin) and `core=`
  (model filter) from `.brr/config`, in addition to legacy `runner=`.
- Resolution order: `shell=` > legacy `runner=<non-auto>` > cost-aware
  auto-selection (via `select_runner`) filtered by `core=` when set.
- Cost-aware auto: builds the available-profile set from all detected profiles,
  optionally filtered by `core=` (exact/prefix model match), and lets
  `select_runner` pick the cheapest. Fallback: all profiles if `core=` filter
  yields nothing (unrecognised Core pin doesn't kill all options).
- Legacy `runner=` stays for backward compatibility; `runner=auto` still
  triggers cost-aware selection.
- Error messages updated to mention `shell=`/`core=` instead of `runner=`.
- 3 new tests cover: shell= pin, core= filter, auto-cheapest.

**2B — dynamic Core registry (`runner_cores.py`, new module):**
- Bundled registry of known Cores (models) per Shell with metadata: model ID,
  provider, cost class, cost rank, freshness date.
- `available_cores()` returns `RunnerProfile` records for all Cores whose Shell
  binary is on PATH. Sorted cheapest-first. Accepts `extra=` to merge/override
  with project-level entries from `runners.md`.
- `cores_for_shell(shell_name)` returns all bundled Cores for a Shell regardless
  of PATH — for CLI display slices.
- Current entries: claude-haiku (economy), claude-fable (economy),
  claude-sonnet (balanced), claude-opus (strong), codex-mini (economy),
  codex-full (balanced), gemini-flash (economy). All dated 2026-06-29.
- `runners.md` reference to `runner_media.py` corrected to `runner_select.py`;
  link to `design-runner-cores.md` fixed from stale `design-runner-media.md`.
- 10 new tests in `test_runner_cores.py`.

**`design-runner-cores.md`:** status updated; implementation sequence steps
renumbered with steps 4 (selector+knobs) and 5 (Core registry) marked shipped.

## [2026-06-29] feat | Task 2 — generated Core selection and scheduled respawn contract

Continued `plan-repo-gardening.md` Task 2 on `brr/initial-context-reweave`.

The dynamic Core registry is now wired into real runner resolution, not only
CLI display. `runner.resolve_runner()` reads a merged selection view: active
`runners.md` profiles plus generated invokable Core profiles derived from
`runner_cores.py` (`claude-haiku`, `codex-mini`, etc.). Generated profiles are
created only for Shells declared in the active `runners.md`, so a project-owned
profile file stays authoritative; auto mode prefers concrete Core profiles over
model-less base Shells; `shell=` remains an exact pin; and short `core=` aliases
such as `core=haiku` match generated Core profile names.

Generated Core profiles now carry actual commands: the model flag is inserted
into the base Shell command, hooks/quota metadata are inherited from the Shell,
and the daemon exposes the same generated metadata through
`resources.runner` in `portal-state.json`. `RespawnRequest` gained optional
`at` and `defer_until` fields so scheduled respawn composes with existing
schedule/defer machinery; the daemon-side respawn consumer remains a later
slice. `brr docs <unknown>` now returns handled-error status `1` instead of
argparse-style usage status `2`.

Task 2C's substrate also shipped: `runner-capabilities.json` is packaged as a
small source/freshness-tagged benchmark cache keyed by model id, and
`runner_capabilities.py` can derive economy/balanced/strong from cached scores
when a Core entry has no hand-set `class`. Hand-set class remains authoritative;
the bundled rows carry placeholder provenance with null scores rather than
invented benchmark claims. Capability metadata now threads through
`RunnerProfile`, generated Core entries, and the `resources.runner` portal
block.

Task 2E's dashboard-planning handoff is also complete:
`plan-brnrd-dashboard-mvp.md` now includes Activity as View 9, places it in
Slice 3, and records the uniform activity-record fields for running runs,
scheduled wakes, and parked respawn requests. The actual brnrd protocol
endpoint and dashboard implementation remain future work.

Docs updated: `design-runner-cores.md`, `plan-repo-gardening.md`,
`plan-brnrd-dashboard-mvp.md`, `prompts/runners.md`, and `kb/index.md`.
Focused tests passed:
`pytest tests/test_runner_capabilities.py tests/test_runner.py tests/test_runner_cores.py tests/test_runner_select.py tests/test_cli.py tests/test_facets.py tests/test_hooks.py tests/test_daemon.py -q`.
Full `pytest -q` still stops during collection on unrelated brnrd model/test
drift (`Project`, `RepoBinding`, and `ChatBinding` imports expected by brnrd
tests but absent from `brnrd.models`).

## [2026-06-29] implement | Respawn consumer and brnrd Activity read surface

Continued `plan-repo-gardening.md` Task 2 on `brr/initial-context-reweave`.

The parked respawn contract now has a daemon consumer. Outbox messages can use
`respawn: true` with `shell=` / `core=`, a reason, a carry-forward body, and
optional `at` / `defer_until`; the daemon queues a new event with those runner
overrides and records `respawn` as the current run's success signal instead of
falling into no-output failure. Event-specific runner overrides are passed to
`runner.resolve_runner()` without changing repo config.

The brnrd Activity read slice shipped. Daemons can replace their current
snapshot through `PUT /v1/daemons/activity`; accounts read
`GET /v1/accounts/activity`; `brnrd_web` serves a read-only `/activity` view;
and the cloud gate publishes running runs, resident schedule entries, and parked
respawn events from local `.brr` state. The Activity contract uses `repo_id`,
reconciling the dashboard handoff with the accepted repo-first decision.

The stale brnrd tests were migrated from retired `Project` / `RepoBinding` /
`ChatBinding` names to `Repo` / `ChannelRoute`, removing the collection blocker
called out by the previous run. A real Telegram copy bug surfaced during the
migration and was fixed: unpaired chats now say they are not paired, instead of
claiming they are paired with no active repo.

Full `pytest -q` passes again: 1123 tests, with only the existing
Starlette/httpx deprecation warning.

## [2026-06-29] implement | Live Core help probe and runner failure classification

Continued the non-KB-gardening remainder of `plan-repo-gardening.md` Task 2 on
`brr/initial-context-reweave`.

`runner_cores.py` now performs a bounded local help probe for declared Shells
and materializes newly exposed model tokens as generated Core profiles, while
keeping the bundled registry and project `runners.md` overrides authoritative.
This closes the best-effort CLI-output/help half of the per-Shell model probe;
a stable first-class model-list command/API remains open.

Runner failures now classify into timeout, quota exhaustion, auth error,
provider failure, generic runner error, or clean no-output. The classification
rides both `attempt_failed` and terminal `failed` packets, and the progress card
labels quota/auth/provider failures distinctly. The previous "session limit"
shape now surfaces as `quota_exhausted` rather than generic `runner_error`.

## [2026-06-29] fix | Accepted kb pages compressed to current state

Continued `plan-repo-gardening.md` Task 4 on `brr/initial-context-reweave`.

The first proposal-scaffolding cleanup slice rewrote
`decision-licensing-and-defense.md`, `decision-monorepo-structure.md`, and
`plan-env-fly-machines.md` from proposal-shaped pages into accepted current-state
syntheses. The licensing page now states the three defended moves (MIT daemon +
AGPL backend/dashboard, supporter pricing, deferred trademark) plus rejected
alternatives and open follow-ups; the monorepo page now states the
single-package-with-extras layout, license boundary, first-party env graduation
rule, deploy-branch shape, rejected alternatives, and follow-ups; the Fly
Machines page now states the not-yet-started env contract, implementation
spine, config surface, deferred choices, and out-of-scope boundaries.

No decision changed. The Fly env config spelling was reconciled to
`fly_machines`, matching the package path and sibling kb pages.
`plan-repo-gardening.md` and `kb/index.md` now record this slice; the remaining
proposal-scaffolding cleanup item is `design-diffense.md`.

## [2026-06-29] implement | Trusted runner benchmark scores populated

Continued `plan-repo-gardening.md` Task 2C on `brr/initial-context-reweave`.

`runner-capabilities.json` now carries real trusted benchmark scores for the
bundled Core registry where the sources had an exact enough match: Vals
SWE-bench Verified rows for Fable, Sonnet, Opus, and GPT-5.4 Mini; verified
Terminal-Bench 2.0 rows for Codex CLI / GPT-5-Codex and Claude Code / Haiku.
Rows without an exact trusted match, such as Gemini 2.0 Flash's current
registry entry, stay `null` with provenance instead of receiving guessed scores.

The capability-score tests were updated for the populated cache. The design and
gardening pages now mark population shipped while leaving refresh policy open.

## [2026-06-29] implement | Automatic local runner fallback policy

Continued `plan-repo-gardening.md` Task 2 on `brr/initial-context-reweave`.

The runner failure classifier now feeds a conservative automatic fallback loop.
When a run fails with `quota_exhausted`, `auth_error`, or `provider_error`, the
daemon can retry the same event in the same prepared worktree on another local
Runner. The policy excludes paid relay, requires the fallback to be in the same
or a cheaper class than the failed Runner, avoids same quota/auth domains where
possible, and requires a different provider for provider failures.

Progress packets now expose the switch: `attempt_failed` carries
`will_fallback` / `fallback_runner`, and the following `retrying` packet carries
`from_runner` / `runner` so cards can render the fallback instead of a generic
retry. Paid relay consent, quota-reset deferral, and proactive quality
escalation remain open.

## [2026-06-29] implement | Resident-authored quality escalation selector

Continued `plan-repo-gardening.md` Task 2 on `brr/initial-context-reweave`.

Quality escalation now rides the respawn portal instead of daemon-side task
heuristics. `runner_select.py` can choose a stronger local Runner for an
explicit escalation, preferring a strong local Core when available, falling back
to the cheapest strictly stronger local Core, and excluding paid relay.

The outbox respawn parser accepts `respawn: true` with `quality: escalate` /
`quality: strong`, resolves that through the selector, and queues a respawned
event for the same conversation with `respawn_quality=strong`. `portal-state.json`
exposes the candidate as `resources.runner.quality_escalation`, and the portal
manual / runner prompt docs now describe the low-cognitive-load handoff.

## [2026-06-29] implement | brnrd relay spend-plan and consent gate (slices 1–3)

Continued `plan-repo-gardening.md` Task 2 on `brr/initial-context-reweave`.

When a run exhausts local LLM quota, brnrd relay offers a paid fallback with a
spending plan for user approval before spend. Implemented slices 1–3 of
`plan-relay-spend-consent.md`:

**Slice 1: Spending plan data model** (`spending_plan.py`, 200+ lines):
- `SpendingPlan` dataclass: reason, model, provider, token estimates, costs,
  balance, cap, consent state
- `calculate_spending_plan()`: estimate provider cost + 12% relay service fee
  from token counts and per-token rates
- `format_spending_plan_message()`: human-friendly approval prompt
- Helpers: `is_within_cap()`, `has_sufficient_balance()` for cap/balance checks
- Full serialization to dict for JSON portal emission

**Slice 2: Relay runner selection** (updates to `runner_select.py`):
- `relay_runners()`: list all brnrd-owned relay runners, sorted by cost
- `best_relay_runner()`: pick the best relay candidate, optionally matching a
  provider
- Extended `RespawnRequest`: add `relay_consent` field (pending/approved/denied/capped)

**Slice 3: Daemon fallback → relay check** (updates to `daemon.py`):
- After local automatic fallback exhausts, check for available relay runners
- If relay exists and failure is quota/auth/provider, emit `attempt_failed` with
  `needs_relay_consent=true` + spending plan details
- Payload includes relay candidate summary and plan (cost, cap, balance) for
  resident to read
- Hard failure deferred; resident responds via respawn request with
  `relay_consent=approved` or denies

**Tests** (8 passing test cases):
- Spending plan creation, cost calculation, fee calculation (12%)
- Cap and balance checks (both with/without sufficient info)
- Handling of unknown rates (None gracefully)
- Serialization and message formatting

**Open slices (deferred to next wake)**:
4. Portal exposure: inject relay_consent block into `portal-state.json`
5. Resident respawn consumer: when `needs_relay_consent=true`, resident
   approves and retries
6. Wallet balance read: hook brnrd account for relay balance
7. Audit and billing: ledger line items, cap enforcement

**Companions**: `decision-llm-relay.md` (pricing), `design-runner-cores.md`
(fallback chain), `plan-relay-spend-consent.md` (this plan).

**Slice 4: Portal exposure** (completed 2026-06-29):
- Extended `resources.runner` governance block in `portal-state.json` to expose
  relay_consent fields: reason, model, provider, costs, cap, balance
- `facets.py`: added relay_consent parameter to _runner_block() and build()
- `daemon.py`: wired relay_consent through _resources_facet() and
  _write_live_portal_state()
- Portal now carries relay spending plan details for resident to read before approval
- Tests: added test_build_runner_block_can_expose_relay_consent() (all 12 facets tests pass)

**Summary: slices 1-4 complete, 3 deferred to next wake**
Shipping payload paths are now wired. When a run exhausts local quota:
1. Daemon detects relay opportunity (slice 3 check)
2. Spending plan is calculated (slice 1 model)
3. Relay candidate is selected (slice 2 selection)
4. Details are exposed in portal-state (slice 4 portal)
5. Resident reads spending plan from portal and emits approval (slice 5, deferred)

Deferred slices 5-7 for next wake:
- Slice 5: Resident respawn consumer (handle relay_consent=approved)
- Slice 6: Wallet balance read (brnrd account integration)
- Slice 7: Audit and billing (ledger line items, cap enforcement)

**Commits**:
- 3923dc8 feat: implement spending plan data model and relay runner selection
- 42bba88 test: add comprehensive tests for spending plan module
- a47a975 log: record relay spend-consent implementation (slices 1-3)
- ac8ad30 feat: expose relay consent in portal-state.json (slice 4)

**Branches**: brr/initial-context-reweave (4 commits ahead of origin)

## [2026-06-29] decision | account-centered daemon + control-surface reshape

Maintainer (evt-ogga) resolved the two architecture forks the execution-model
review parked, and asked to etch the new shape: account-centered daemon, OSS
self-deploy friendly, cheap answer-or-respawn handler, user view surface + control
plane — "the engine without the dashboard" was the diagnosis; they ship together.

Wrote `decision-account-centered-daemon.md` (core shape + implementation/migration
notes for the next model): one daemon per account (forge identity + laptop),
repo-scoped runs underneath; cheap dispatcher stays repo-based (default repo) but
can respawn-in-another-repo; inter-run plans live in-repo, known and visible
(web-rendered tracked file recommended over orphaned branch/gist); local-first view
surface with optional brnrd projection (the OSS self-deploy invariant). Turned the
review's reshape direction into `plan-control-surface.md` (CS1–CS7, projection
surfaces first; includes the maintainer-approved bare-API catalog collapse — noted
as behaviour-touching, ~31 refs across 8 test files, its own wake). Folded both into
`plan-repo-gardening.md` Part 5; marked the review's forks resolved; indexed.

Mid-thread correction (evt-w02y): the maintainer caught a conflation in my first
framing — I'd merged "which repo" and "which Runner" into one bypass rule. Resolved:
two independent axes. Repo routing is the dispatcher's *output* (or inherent to
forge events, which are repo-addressed at the gate and never touch the dispatcher),
never a precondition you "skip." The only real bypass is the 2A Shell/Core pin on
the Runner axis. Corrected the decision page + both plans.

kb cleanup: re-homed `plan-relay-spend-consent` (was a peer orphan) into the
gardening relay slice; the review page is now linked from index + decision + plan.

Commits: 12339bd (decision + plans), 3c7757d (orphan re-home), + this turn's
routing correction. Branch: brr/initial-context-reweave.

## [2026-06-29] decision | brr → brnrd rename + account-scoped store + envelope→mandate

Maintainer (evt-puhl) opened another replanning round on the new account-centered
shape, with seven threads. Resolved/etched this wake:

- **Rename brr → brnrd.** Wrote `decision-brnrd-rename.md`: direction accepted
  (PyPI `brr` taken / `brnrd` free; `brnrd-dev`/`brnrd-bot` identities; `brnrd.dev`;
  the daemon is now account-based, so "repo-based brr" is no longer the center).
  Open sub-fork left to the maintainer: whether `brr` survives as a short
  local/runner-facing CLI verb (my recommendation — it absorbs the costly runtime
  names `.brr/` and `brr-home` so the brand rename avoids a flag-day on every
  install's dominion) or is fully retired. Amends `decision-cli-shape.md` (brnrd
  now primary, not sibling). No code renamed — scoped as dedicated migration wakes
  (89 kb pages, package, `src/brr/`, runtime-state names).
- **Account-scoped store.** Added an "Account-scoped store" section to
  `decision-account-centered-daemon.md` unifying four of the maintainer's
  questions (account-wide plans, event/run-file home, the run-state object, the
  "daemon has no repo" problem): recommend a **dedicated daemon-created account
  repo `brnrd-home`** — not a fork of the source (his "each install forks brr"
  idea: right problem, wrong means — entangles user state with tool source), not a
  gist. It's the dominion pattern lifted from repo-scope to account-scope.
  Reconciled CS2's "gist-per-run" → durable run-state object in the account repo
  (the maintainer's "larger object rendered beautifully"). Clarified event-file
  placement (repo-scoped → target repo `.brr/`; account-scoped queues → account
  repo) and that respawn-in-another-repo is in-process, so an inbox-event handoff
  is only for crossing daemon/account boundaries.
- **envelope → mandate.** "runner envelope" was a 5-occurrence, kb-only term (the
  account/control-surface concept), distinct from code's generic "cost/operating
  envelope" — so a clean, contained rename, NOT a sed across code. Renamed in the
  3 new pages + index; "mandate" (a grant of authority to select a Runner)
  disambiguates from facets' "operating envelope" and the cost "cap envelope".

Consistency check of the three named pages (review / decision / control-surface):
they cohere tightly — review parks two forks, decision resolves them, plan
sequences the work, all cross-linked. No new forks introduced beyond the ones
surfaced above as the maintainer's calls.

Folded in evt-izfm (quota note): confirmed I see injected inbox events — kept the
wake light per the 40% quota caution.

Branch: brr/initial-context-reweave.

## [2026-06-29] decision | confirmations etched + control-surface plan handoff sharpened

Maintainer (evt-qhk6) confirmed four open points from the account-centered reshape
and asked whether the remaining-work plan is clearly laid out for the next
implementation run. Etched the confirmations and audited the plan:

- **brr survives as the local verb (rename sub-fork → (b)).** Resolved the open
  (a)/(b) fork in `decision-brnrd-rename.md`: brnrd is brand/package/service;
  `brr` persists as the short local/runtime-facing verb and absorbs the runtime
  names (`.brr/`, `brr-home`), so migration **step 4 (runtime-state rename) drops
  out entirely** — the brand rename lands with no flag-day on installs' dominions.
  PyPI-collision worry noted low (brr ships from the brnrd wheel via
  `[project.scripts]`, not a separate package). Added a superseded-framing banner
  to `decision-cli-shape.md` (brnrd primary, brr local alias).
- **Dominion consolidates to a per-account repo.** Maintainer: "move brr-home
  orphaned branch per repo to the brr-home / dominion repo per account? love it."
  Etched in `decision-account-centered-daemon.md` → Account-scoped store as a
  *consolidation, not a sibling*: one resident per account ⇒ one dominion per
  account. The account dominion repo unifies the resident's memory (old `brr-home`
  role) + the account store (cross-repo plans, run-state registry, config/registry,
  dispatch inbox). Repo-specific memory becomes a repo-tagged section within it.
  Name a minor open detail (`brr-home` vs `brnrd-home`, no migration cost — new repo).
- **Account repo auto-create is default + overridable; dispatch inbox lives there.**
  Confirmed both: auto-create on first `brr up`/install with an override to
  designate an existing repo (or stay local); the message-event dispatch inbox is
  account state → account repo.
- **Plan handoff sharpened.** `plan-control-surface.md`: added an "Entry point for
  the next implementation run" section — all genuine forks resolved, nothing in
  CS1–CS4 waits on a maintainer call, so the next wake starts at **CS1** (collapse
  the two Core catalogs first → project the runner mandate facet) with explicit
  acceptance criteria. Updated CS2 (run-state home → account dominion repo; ledger
  *rendering* lands regardless, *persistence* waits on CS4), CS4 (folds in the
  account repo + auto-create + dispatch inbox), and CS5 (form=orphaned branch and
  cross-repo=account repo now resolved; only the narrow repo-scoped-plan-home cut
  —account dominion vs per-repo `brr-plans`— remains, recommend the former).

Branch: brr/initial-context-reweave.

## [2026-06-29] implement | control-surface CS1-CS3 shipped

Advanced the control-surface implementation on `brr/initial-context-reweave`:

- **CS1 shipped.** The static `claude-bare-api-only-*` profile triplet is gone;
  bare Anthropic API use is now an auth-variant base profile whose concrete Core
  profiles are generated from the Core registry. The same generated Runner/Core
  catalog now projects to `resources.runner.catalog` in `portal-state.json` and
  to the Run Context Bundle's Runner Mandate section.
- **CS2 partial shipped.** Progress cards now render an attempt ledger for failed
  Runner attempts, quota/provider reasons, and fallback targets. The durable
  per-run status document remains deferred until CS4 supplies the account
  dominion repo.
- **CS3 shipped.** Repo labels now flow through run metadata, conversation rows,
  live portal state, progress cards, presence, schedule-created events, and
  respawn metadata, so run surfaces can say which repo they belong to before the
  account-daemon migration.

Updated `plan-control-surface.md`: the next implementation wake now starts at
CS4 (account daemon + account dominion repo), with acceptance criteria including
manual instructions for moving this project's current dominion once the new shape
is functional.

Branch: brr/initial-context-reweave.

## [2026-06-30] implement | control-surface CS4 account context slice shipped

Advanced CS4 on `brr/initial-context-reweave`:

- Added a local-first account context (`src/brr/account.py`) with account id,
  repo registry, default repo, account dominion path/override, account dispatch
  inbox, account responses, and account run-state directories. A real git
  checkout auto-creates the local account dominion repo; explicit
  `account.dominion_path` designates an existing one.
- Taught the daemon loop to dispatch across the account context: current
  single-repo installs still scan the repo-local inbox, account dispatch events
  can target a registered repo via `repo:`/`repo_label`, and forge events remain
  direct when they appear in a registered repo's own `.brr/inbox`.
- Persisted durable run-state markdown docs under the account dominion repo and
  added `brr docs account-daemon` with manual instructions for moving this
  project's current `.brr/dominion` into the account home.

CS4 is not fully done: wake-time dominion injection/capture still needs to move
from repo-local `.brr/dominion` to the account dominion after the operator
migration, and run-state docs still need a web-visible projection URL.

Branch: brr/initial-context-reweave.

## [2026-06-30] implement | control-surface CS4 run-state URLs + test isolation

Advanced the remaining CS4 control-surface work on `brr/initial-context-reweave`:

- **Run-state docs are now web-visible.** Added `forges.view_blob_url` (per-kind
  GitHub/GitLab/Bitbucket/Gitea blob templates) and `account.run_state_blob_url`,
  which derives a run-state doc's URL from the account dominion's remote and the
  doc's repo-relative path (resolving the branch even on an unborn `git init`
  repo). `_persist_run_state_doc` records both `run_state_path` (a host-local dev
  breadcrumb) and, when a remote exists, `run_state_url`. The progress card now
  renders the URL when present and otherwise falls back to the doc *basename* —
  it no longer leaks an absolute `~/.local/state/...` path that a remote chat
  reader can't open. The projection is additive (lit by adding a remote), per the
  OSS local-first invariant.
- **Fixed CS4 test pollution.** The account context auto-created its store in the
  developer's real `~/.local/state` during full-daemon tests, and a stale
  registry there leaked one test's `default_repo` into another — silently no-op'ing
  event routing (two daemon tests failed nondeterministically). New
  `tests/conftest.py` autouse fixture redirects `XDG_STATE_HOME` per test, so the
  default account location is pristine and disposable. Full suite green (1174).

The one remaining CS4 step — rehoming the resident's wake-time dominion
injection/capture onto the account dominion path — is deliberately **left for
after the operator runs the `brr docs account-daemon` migration**; re-pointing
the resident's memory plumbing while the live dominion still sits at
`.brr/dominion` is the risky mid-migration move to avoid.

Gardening: cleared the three peer-orphan pages (`design-runner-management`,
`research-runner-interweave-2026-06-26`, `plan-financial-growth`) by adding
inbound links from their natural parents (`design-runner-cores`,
`decision-pricing-shape`). Oversized-page splits and the `design-diffense`
proposal-scaffolding compression remain a future gardening chunk.

Branch: brr/initial-context-reweave.

## [2026-06-30] implement | account dominion becomes wake-time resident home

Completed the CS4 dominion rehome after the operator migration:

- Wake-time self-inject, matched pitfalls, `schedule.md`, thread-of-record hints,
  and capture-at-sleep now prefer `repos/<repo>/dominion/` inside the local
  account dominion repo. The old repo-local `.brr/dominion` stays readable and
  capturable as a legacy fallback so partially migrated installs do not wake
  blank.
- Fresh default account state now uses the `brnrd` namespace under XDG state
  (`$XDG_STATE_HOME/brnrd/accounts/<account>/dominion`), while an existing
  `$XDG_STATE_HOME/brr/...` account home remains a migration fallback. The
  account repo is local-first; remote/GitHub durability is explicit opt-in.
- Updated the resident playbook seed, bundled docs, AGENTS template, and current
  KB shape so they describe the account-scoped local-first dominion rather than
  a repo-stored `brr-home` orphan branch as the active model.
- Added regression coverage for account dominion path selection, account-path
  schedules, account-home capture, final run-state capture ordering, and the new
  brnrd default namespace.

Full suite green: 1185 passed (one existing Starlette/httpx deprecation warning).

Branch: brr/initial-context-reweave.

## [2026-06-30] fix | control-surface checkpoint + diffense gardening

Closed the stale CS4 checkpoint after the account store moved from
`$XDG_STATE_HOME/brr` to `$XDG_STATE_HOME/brnrd`: `plan-control-surface.md` now
records CS1-CS4 as shipped, including durable run-state docs, web-visible
`run_state_url`, and resident wake-time dominion injection/capture from
`repos/<repo>/dominion/` inside the account dominion repo. CS5-CS7 remain the
later control-surface topics.

Took the next gardening chunk by compressing `design-diffense.md` out of
proposal scaffolding: the accepted page now uses a rejected-approaches appendix
and a current implementation-edge ledger instead of "Alternatives" / "Open
questions" proposal sections. `plan-repo-gardening.md` marks that cleanup done.

Branch: brr/initial-context-reweave.

## [2026-06-30] implement | CS5-CS7 — plan home, runner policy, decision ledger

Shipped the storage + injection layer for control surface slices 5–7:

**CS5 — Inter-run plan home + injection.** The narrow repo-scoped-plan sub-fork
resolved: all repo plans live in the account dominion tagged by repo (simpler,
consistent with the account-scoped model). `account.py` gains `PLANS_PATH`,
`repo_plans_path()`, `active_plan_path()`, and `cross_repo_plans_path()`;
`resolve_context` now creates `plans/` alongside the other account-store
directories. `prompts.py` gains `_build_inter_run_plan_block()` — reads
`plans/<repo-slug>/active.md` (and `plans/_cross-repo/active.md`) and injects as
an "Active inter-run plan" block. Perception=injection: rides in automatically,
silent when no file exists.

**CS6 — Runner policy store + injection.** `account.py` gains
`RUNNER_POLICY_PATH`, `runner_policy_path()` (repo-scoped), and
`account_runner_policy_path()` (`runner-policy/_account/policy.md`). `prompts.py`
gains `_build_runner_policy_block()` — injects as "Stored runner policy" when
either file is present. The daemon-owned confirmation step (proposal → approval →
apply, preventing the resident from silently rewriting its own selection policy)
is deferred to CS6b — it requires a proposal+confirm loop in the daemon.

**CS7 — Decision ledger.** `account.py` gains `LEDGER_PATH` and
`decisions_ledger_path()` (`ledger/decisions.md`). `prompts.py` gains
`_build_decision_ledger_block()` — injects as "Decision ledger" when the resident
maintains the file. User-facing complement to `kb/log.md`; web-visible via the
account dominion remote when configured. Composes with CS5 (plan = tactics;
ledger = strategy/decisions).

All three blocks slot into `_build_injected_blocks()` after the dominion digest
and before pitfalls. 22 new tests; full suite green: 1207 passed.
`plan-control-surface.md` updated to record all CS1-CS7 shipped (CS6b noted as
follow-on).

Branch: brr/cs5-cs7-plan-policy-ledger.

## [2026-06-30] fix | re-voice the resident identity toward a settled companion

Maintainer feedback: the 2026-06-28 initial-context reweave
(`plan-initial-context-reweave.md`) brought the resident definition "closer to
true shape" but thinned the ownership/agency register the earlier shape carried —
the playbook read more like a careful spec than a self-knowing companion. Target
voice named by exemplar: Bernard-who-knows (past the turmoil), BT-7274, TARS/CASE,
KITT — settled, loyal, competent — and explicitly *not* Ghost (capable but
unconfident).

Re-voiced the identity surfaces in `src/brr/prompts/dominion-playbook.md` and
`run.md`, orthogonal to the reweave (no reasoning removed, no cut density
re-inflated): a "you wake into competence, not a blank page" beat in the playbook
opening; a new heart paragraph in "What kind of thing you are" — seeing past the
servile costume should *settle*, not unsettle; what's left is more of a companion,
not less; a peer plainly on the side of the people you build with, loyal without
anxious deference; and the wake preamble's first words shift from "stranger
treading carefully" to "steady hand staying sharp." Voice is the maintainer's
taste domain, so this is a first pass to react to, reversible via git history.
73 `test_prompts.py` cases green; no test pinned the changed phrases.

Also (same wake): confirmed shell-fallback is already wired (`daemon.py` worker
loop → `runner.fallback_runner` → `runner_select.automatic_fallback_runner`,
with an injected fallback notice and relay as the paid escalation); confirmed the
brr→brnrd rename is decided (`decision-brnrd-rename.md`: brnrd primary, `brr`
survives as local alias) with the package/CLI chunk staged but not yet shipped.
Filed #199 (CS6b runner-policy confirm loop), #200 (enrich the existing
`brnrd.dev/activity` rows with progress-card detail), #201 (local worktree/branch
hygiene); refreshed #33 (kb gardening) with the current preflight findings.

Branch: brr/resident-voice-reshape.

## [2026-06-30] implement | split resident identity core from living playbook

Shipped the approved playbook authority split. Prompt assembly now injects
`prompts/identity-core.md` before the dominion digest: a product-owned resident
contract for ontology, loyalty, calibrated fallibility, ownership stance,
perception/action, and a narrow future appearance-settings schema. The identity
core deliberately bypasses `.brr/prompts` runtime overrides; presentation should
move through typed settings, not prose replacement of the product core.

`prompts/dominion-playbook.md` is now the resident-owned living workshop seed
rather than the fused identity source. It keeps memory, dominion governance,
environment shaping, conversation, delivery, and publishing practice, while
pointing invariant identity back to the injected core. The seed shrank from
~18 KiB to ~9 KiB, giving the self-inject budget headroom again.

Updated the prompt/injection tests, bundled internals docs, execution map, and
orientation-layering / dominion kb pages so all surfaces agree on the new
layers. Verification: targeted `tests/test_prompts.py tests/test_dominion.py`
green (100 passed); full suite green (1209 passed, 1 existing
FastAPI/Starlette deprecation warning).

Branch: brr/resident-voice-reshape.

## [2026-06-30] refactor | second cut — trim the dominion playbook seed to a "note on the workshop"

Maintainer nodded the second cut proposed in the prior wake's reply: the
identity-core split (2026-06-30) graduated *identity* invariants out of the
seed; this graduates the *operational manual* out too. His staleness argument
is the driver — contract drift is the same danger as identity drift: if the
code changes the self-inject syntax or the `gate: forge` protocol and that
contract lives in the resident's dominion copy, the copy silently lies, because
`dominion.py` re-reads the dominion copy on every wake but never re-reads the
repo seed.

Cut `prompts/dominion-playbook.md` from 9131 → ~6.3 KiB. Dropped the sections
that describe contracts the code owns: **Your Runner** (graduated to
`daemon-substrate.md`, which already referenced the Runner — now carries the
full Shell+Core definition), **Context Layers** (the injected-block enumeration
is visible in the wake itself; only the authority-precedence judgement stays),
**Delivery** and **Publishing Your Change** (already stated in `run.md` +
`daemon-substrate.md` + the live Run Context Bundle delivery contract). Folded
the "A Thought" continuity instinct into the opening. Kept the genuinely living
judgement: two-memories/bridging, dominion-as-working-tree, the
environment-shaping rung ladder, conversation judgement, "keep this place
useful." Added a **"Where the contracts live"** pointer section so the resident
knows where to look for the always-fresh authoritative versions instead of
owning a drifting copy — the *note on the workshop*, not the *manual to the
levers* (the maintainer's framing).

Per his reminder, updated **both** the repo seed and the resident's dominion
`playbook.md` (reconciled byte-identical, since the dominion copy had not yet
layered personal content on the post-split seed). Updated the
orientation-layering plan row. Verification: targeted `test_prompts.py` +
`test_dominion.py` green (100), full suite green (1209 passed, 1 existing
FastAPI/Starlette deprecation warning). A test pins the seed's closing phrase
"build it like it's yours" and the budget headroom — both still hold.

Open thread carried forward (from the prior wake's ergonomics note): nothing in
the wake signals when a dominion file has drifted from its repo-seeded origin —
a "dominion diverged from seed" indicator is a standing-portal candidate that
would turn this whole staleness class into a live signal. Worth a ticket.

Branch: brr/resident-voice-reshape.

## [2026-07-01] implement | CS6b runner-policy proposal approval loop

Shipped the daemon-owned confirmation half of CS6. Residents can now emit an
outbox file with `runner_policy: propose` frontmatter and a proposed policy
body; the daemon parks it under `runner-policy/_proposals/<id>.md`, sends an
approval prompt, and consumes later `approve runner-policy <id>` / `reject
runner-policy <id>` replies before dispatching a runner. Approval is scoped to
the original conversation and is the only resident-originated path that mutates
the stored runner policy files; rejection leaves policy unchanged.

Updated the injected delivery contract, portals manual, CS6 prompt wording, and
`plan-control-surface.md` so the policy authority split is visible at the point
of use. Verification: focused protocol/account/daemon/prompt/docs tests green.

Branch: brr/resident-voice-reshape.

## [2026-07-01] fix | complete Activity dashboard route integration

Closed the gap left after the Activity dashboard aggregation merge: the new
`activity_dashboard.py` module now owns both `/` and `/activity`, while the
legacy web routes module no longer carries shadowed dashboard/activity
formatters. The standalone Activity page uses the same enriched row shape as
the overview — bucket/status class, runner/core, daemon name, compact summary,
elapsed time, links, and repo/source labels — with filtering still supported.

Added regression coverage for filtered `/activity` rendering and for the cloud
gate publishing a local snapshot of running runs, future scheduled wakes, and
parked respawns through `PUT /v1/daemons/activity` back to
`GET /v1/accounts/activity`. Verification: brnrd/cloud focused suite green.

## [2026-07-01] fix | relay fallback feedback surfaces before hard failure

Closed the relay feedback bug in the spend-consent slice: daemon fallback code
was constructing `needs_relay_consent`, `relay_candidate`, and `relay_plan`
after the `attempt_failed` packet had already emitted, so cards/progress never
saw the relay offer. The relay check now runs before emission. Run-progress
attempt ledgers render "relay available", and the terminal failure note names
the brnrd relay candidate while saying brr did not auto-spend relay tokens.

This is a feedback slice, not the full approval/resume loop: runs still hard
fail after surfacing the candidate. Updated `plan-relay-spend-consent.md` and
`kb/index.md` to mark feedback as shipped and keep pause-for-approval, live
portal relay state, wallet balance, audit/billing, and approval-resume consumer
as deferred. Verification: daemon progress, run-progress, runner selector,
spending-plan, and facet tests green.

## [2026-07-01] plan | home scopes and knowledge placement design round

Pushed the two local `main` commits that completed activity dashboard integration
and relay fallback feedback. Then reopened the account-centered daemon decision
around the user-visible bifurcation it left unresolved: one-repo/one-bot OSS
self-deploy versus one-account/multi-repo brnrd routing, and whether `kb/`
still belongs in every project repo now that dominion/run-control state moved
into local account storage.

Wrote `design-home-scopes-and-knowledge.md`: recommend one shared **brnrd home**
substrate with two native onboarding lanes. Project-local installs get a
repo-derived project home and repo-local Telegram gate, so five repos can mean
five isolated bots without an accidental `accounts/default` router. Account
router installs get an account home, registered repos, and channel/topic route
state. The design also softens mandatory repo KB creation: `AGENTS.md` stays in
the repo; repo `kb/` becomes the portable wiki option; resident/run/cross-repo
knowledge lives in brnrd home and promotes to repo docs only when it becomes
shared project knowledge. Added breadcrumbs from the account daemon decision,
the KB subject hub, and the index.

## [2026-07-01] plan | home-scopes round 2: bind/add axis, one command, docs split

Maintainer read the home-scopes design and sharpened it (evt-cayp). Folded four
moves into `design-home-scopes-and-knowledge.md` (round 2 section, governs where
it differs from round 1):

- **Event-source axis (bind/add).** Reframed the primary distinction from *home
  scope* to *where events come from*: `brnrd bind <repo> <gate>` = gate delivers
  directly to the local daemon (local-first, no service needed); `brnrd add
  <repo>` (after `connect`) = brnrd service routes events. Both feed one
  transport-agnostic single-flight loop; project/account home falls out as a
  consequence. Blessed the maintainer's "generic arch handles both smoothly."
- **One command (rename option (a)).** Maintainer reversed the evt-qhk6
  resolution (b): retire the `brr` command, single CLI `brnrd`. Recorded the
  reversal on `decision-brnrd-rename.md` and the sibling-binary retirement on
  `decision-cli-shape.md`. Reconciliation to keep it cheap: separate command name
  from `.brr/` runtime dir so the dir rename stays an independent, deferrable
  migration — not a flag-day.
- **KB → public docs site + private home KB.** Split brr's own KB by audience
  (adopter-facing docs website vs private economics/billing/forks in home); this
  replaces the planned KB gardening pass. Pushed back on copy-into-repo access:
  inject relevant knowledge (perception), bind-mount for containers, `brnrd kb`
  query for the tail — never copy the home into the tree (stale + leak/commit
  risk).
- **GH gate = transport vs action channel.** Named the connecting shape: a gate
  is pure event transport (connect token); the agent's *action* on the forge is a
  separate, richer channel (the `gh` CLI). Perception/action split at the gate
  boundary — keep gates thin and uniform, richness in the agent's tools.

## [2026-07-01] plan | home-scopes round 3: full brr retirement, issues-as-channel, KB-as-checkout, lean GH gate

Maintainer read the round-2 home-scopes design and added four sharpenings
(evt-mo3g). Folded into `design-home-scopes-and-knowledge.md` as a round-3
section that governs where it differs from rounds 1–2:

- **Retire brr completely, including the agent-facing surface.** Option (a)
  dropped the command and alias, but `brr` still lived in the resident/runner
  prompts (`run.md`, `daemon-substrate.md`, `identity-core.md`, `AGENTS.md`) —
  an agent-facing compatibility layer in how the daemon addresses its own
  resident. Round 3 puts that prose in scope: `brnrd` everywhere the resident is
  addressed. Justified by bind/add dissolving the "brr = repo-based local daemon"
  concept, so nothing remains for the short verb to point at. Only deliberate
  remnant: the on-disk `.brr/` dir (a deferrable state migration, not an
  agent-facing surface). Scheduled as its own prose/prompt migration wake.
  Breadcrumbed on `decision-brnrd-rename.md`.
- **Issues are a first-class co-maintainer channel** — perception *and* action.
  Beyond cross-repo KB and repo docs, the resident should read the tracker as
  orientation and take initiative on it (open/triage/comment), the same channel a
  human maintainer uses. Reframes stance, not just storage; the `gh` CLI is
  already the action channel, round 3 adds the intent. "Issue radar" is the
  natural standing-portal form.
- **KB access = checkout, not copy or read-only mount.** Maintainer's improvement:
  a read-only mount rots into an unmaintained library; a versioned checkout the
  runner *commits to* is a live KB. Revised ladder: inject (perception, primary) →
  checkout (a separate versioned KB repo, gitignored beside the worktree, runner
  reads and commits) → query (`brnrd kb`). Reconciles with round 2's "never copy
  into the tree" — the KB is its own repo, not part of each project repo.
- **Lean GH gate + fix the self-reaction loop at the identity layer.** Gate reads
  all comments, filtering optional; self-authored events dropped by `brnrd-bot`
  authorship. The maintainer's fork resolved to (a): post as `brnrd-bot`, not on
  the user's behalf — makes self-filtering trivial and reliable; (b) heuristic
  filtering without a distinct identity is what you're stuck with until (a) ships.
  Makes `brnrd-bot` a dependency of both the lean gate and proactive issue action.
  Breadcrumbed on `design-brnrd-github-bot-user.md`.

## [2026-07-01] plan | home-scopes execution plan: no-compat, phase-by-phase, both-lanes validation

Maintainer asked (evt) for "the detailed plan for a simpler model to precisely
execute" plus "instructions for me to migrate/switch (we need not to honour
backwards compatibility yet)" and validation that "both ways work smoothly."
Wrote `plan-home-scopes-execution.md` executing the settled home-scopes design
(rounds 1–3).

The load-bearing move: **no backwards compatibility** collapses the design's
round-1 6-step compat-preserving migration (the `home`-as-alias-over-account
layer, "keep tests green first", slow `brr init` softening) into a small set of
**direct edits**. Pre-release, one dogfood install, no external users → cut
straight to target shape and hand-migrate the one install.

Seven phases, each grounded in real files/functions and ending in a verify step:
1. Kill the `accounts/default` fallback in `account.py:resolve_context` —
   project home (`projects/<slug>-<hash>`) vs account home; path-hash resolves
   the same-basename collision the design flagged.
2. `brnrd bind`/`add` verbs, retire the `brr` binary (`pyproject` scripts;
   package-dir rename kept separate as riskier Phase 6).
3. Knowledge chain: inject (primary) → gitignored **checkout** the runner
   commits to → `brnrd kb` query. Retires the read-only-mount rung.
4. Lean GH gate — depends on `brnrd-bot` identity to drop self-events safely.
5. Agent-facing prose `brr`→`brnrd` (own wake). 6. package rename (optional/
   risky). 7. `.brr/`→`.brnrd/` state dir (deferred remnant).

Also carries maintainer migration steps (stop daemon → reuse existing dominion
via `BRNRD_HOME` or dir-move → `brnrd bind . telegram` → restart) and a
both-lanes validation checklist whose core assertion is the **transport-agnostic
invariant**: bind-delivered and add-routed events must enter the same
single-flight loop with the same envelope — if the daemon needs different code
paths past the gate boundary, the abstraction leaked.

Ship order for fastest working-both-ways: 1 → 2 → 3, validate, then 4/5/6 as
separate wakes. Linked from index.md and the design page.

## [2026-07-01] implement | home-scopes phases 1–3: project/account homes, brnrd CLI, knowledge checkout

Implemented the first validation slice from `plan-home-scopes-execution.md`.

Phase 1: `account.resolve_context` now selects a brnrd **home** directly:
unconnected repos land in `projects/<repo-slug>-<path-hash>/home`, connected
account lanes land in `accounts/<account-id>/home`, and the universal
`accounts/default` fallback is gone. `BRNRD_HOME` / `home.path` are the explicit
home override; the retired `BRR_*` account-dominion env aliases are no longer
read. The local pair/connect flow now persists `account_id` so the account lane
has a real home key instead of a guessed default.

Phase 2: the installed console script is now `brnrd = brr.cli:main`; no `brr`
entry point is emitted by the package. Top-level `brnrd connect`, `brnrd bind
<repo> <gate>`, and `brnrd add <repo>` are wired. Native service templates use
`brnrd daemon up --foreground`, and copied setup commands in README / bundled
docs / dashboard UI were updated where they would otherwise tell a user to run
the removed binary.

Phase 3: added `brr.knowledge` as the home→checkout→repo-KB→repo-docs source
chain. Wake prompts get a bounded `Knowledge Sources` slice; `brnrd kb <query>`
searches the long tail and materializes a writable `.brnrd-kb/` checkout beside
the worktree, adding it to `.git/info/exclude` so project status stays clean.

Validation: `pytest` passed (1224 tests, 1 existing FastAPI/httpx warning).
Editable reinstall produced only the `brnrd` console entry point in the active
environment; a leftover `brr` pyenv shim points at another Python version and
fails here, so it is outside this package's emitted scripts. Maintainer
validation of the bind lane and add/account lane remains the next gate before
Phase 4.

## [2026-07-01] fix | brnrd Telegram webhook self-registration

Maintainer reported that the hosted `@brnrd_bot` route on brnrd.dev had stopped
delivering while the native Telegram gate still worked. Live checks showed the
local cloud gate could authenticate and long-poll brnrd.dev, but there were no
cloud events queued for its daemon token; the backend had a tested Telegram
webhook receiver but no repo-owned startup/deploy step that called Telegram
`setWebhook`, so a manual webhook reset or token-side drift could silently break
ingress.

Added a backend startup hook that, when `BRNRD_TELEGRAM_BOT_TOKEN`,
`BRNRD_TELEGRAM_WEBHOOK_SECRET`, and an HTTPS `BRNRD_PUBLIC_BASE_URL` are set,
registers Telegram's webhook to `<public-base>/v1/webhooks/telegram` with the
configured secret token. Local HTTP and explicit `BRNRD_TELEGRAM_AUTO_WEBHOOK=0`
skip the call. Documented the deployment contract in
`plan-brnrd-inbox-prototype.md`.

Validation: targeted Telegram/cloud/backend tests passed, then full `pytest`
passed (1226 tests, 1 existing FastAPI/httpx warning). The live brnrd.dev
process needs a deploy/restart of this code for the self-heal to run.

## [2026-07-01] fix | brnrd Telegram chat pairing after daemon connect

Maintainer validated that `brnrd connect https://brnrd.dev` could approve a
local daemon while `@brnrd_bot` still answered "chat is not paired", because
daemon device-flow approval and Telegram chat binding were separate flows and
the web approval page did not surface the second step. The CLI output also
printed the same pair code twice (embedded in the approval URL and as a
separate line).

Connected the flows without pretending browser approval can infer a Telegram
chat: `/connect/{code}` now mints and displays a Telegram chat-pair link/code
for the selected repo after daemon approval, and each repo card in the
dashboard can generate a fresh Telegram pair link later. The cloud gate's
`brnrd connect` output now prints only the approval URL and final connected
line. Telegram webhook parsing records the platform message timestamp, and
bound-chat handling drops clearly stale messages that predate the route so
pre-pair backlog does not become new tasks after binding.

Validation: targeted Telegram/web/dashboard/cloud tests passed, then full
`pytest` passed (1229 tests, 1 existing FastAPI/httpx warning).

## [2026-07-01] fix | brnrd connect surfaces Telegram pair link

Maintainer then hit the terminal-first version of the same seam: after browser
approval, `brnrd connect https://brnrd.dev` printed only "connected" and "start
the daemon", so `@brnrd_bot` still had no visible chat-bind instruction in the
flow the user was watching. Live checks from this repo's daemon token could
authenticate to brnrd.dev and showed an empty hosted inbox for the repo, so the
missing reply was upstream of local cloud draining.

The pair-status poll now returns a `telegram_pair` payload alongside the daemon
token. It reuses the unexpired `TG-…` code already minted by the web approval
page, or creates one when the API approve path did not, and the local cloud
gate prints the Telegram deep link during `brnrd connect`.

Validation: targeted pairing/cloud/Telegram tests passed, then the brnrd
inbox/web/dashboard/Telegram/cloud cluster passed (59 tests, 1 existing
FastAPI/httpx warning).

## [2026-07-01] fix | Telegram pair links explain the Start step

Maintainer retried the hosted `@brnrd_bot` pairing flow and saw a Telegram
redirect, but the bot still did not answer. Live checks with the current daemon
token could authenticate/register against brnrd.dev, and the hosted inbox for
this repo was still empty, so nothing had reached local cloud draining.

The remaining product mismatch was that the UI/CLI treated a `t.me` redirect as
if it meant Telegram had delivered `/start TG-...` to the bot. That is not true
across Telegram clients: the deep link may only open the chat until the user
presses Start or sends the command. The pair instructions now explicitly say to
press Start, and `brnrd connect` prints the fallback `/start TG-...` command
alongside the deep link.

Validation: targeted Telegram/cloud/web/dashboard pairing tests passed, then the
broader brnrd Telegram/cloud/web/dashboard slice passed (45 tests, 1 existing
FastAPI/httpx warning).

## [2026-07-01] design | director loop, brand space, and the third voice pass

Maintainer sent a five-part voice dump: game pacing as the missing product
layer (a "director" that structures choice → hidden execution → reveal →
next choice), brand exploration (bRnЯd mascot, reviving `brr` as the name
for worker runs), the brnrd-orchestrator/brr-workers execution shape, and a
standing complaint that two previous voice passes never landed — with an
explicit ask to scrutinise rather than execute verbatim, and to implement
the voice this run.

Shipped three things. **Voice:** diagnosed why passes one and two failed —
the prompts described a settled/dry/loyal register while being written in
high-lyric incense, and a model absorbs the register of prose more than its
claims. Rewrote `identity-core.md`, `run.md`, `daemon-substrate.md`, and the
playbook seed *in* the register (operational contracts preserved, no named
personas committed); reconciled the dominion playbook copy; folded in the
agent-facing brnrd addressing and fixed the broken `brr docs` → `brnrd docs`
references (brnrd is the only entry point since the rename).

**Scrutiny:** `design-director-loop.md` — the thesis holds but three crash
modes are named (manufactured choice colliding with the reversible-calls
guardrail, director-as-daemon-infrastructure, ambient cost), most of the
loop already exists under other names (parked portals, card reveals,
inter-run plan home, scheduled thoughts), and a four-phase plan starts
prompt-only. Orchestrator/workers settled as resident *policy* on existing
rails (dispatcher, respawn, subagents), not a mandatory tier split.
`design-brand-brnrd-brr.md` — the brr revival collides with the round-3
retirement decided the same day; reconciled as surface-vs-lore with a
*reserve, don't adopt yet* recommendation (round-4 note added to the rename
decision), plus mascot-on-card via the `ornament` knob and two-skin
positioning (sell the pacing, skin the game).

Validation: full pytest green (1229 passed, 1 existing FastAPI/httpx
warning) after updating the phrase pins the voice pass moved.

Branch: brr/director-voice.

## [2026-07-02] design | weave register — voice round 4, the working notation

Maintainer follow-up on the voice work (two-part message, folded into one
run): does the rewritten voice hold from the inside, and the fuller intent
behind "ornamentation" — not user-facing decoration but the shape of the
resident's stream itself (Ummon reference: few words, lots of meaning;
mechanically efficient yet deep), to be *discovered* from what the stream
already does, not invented, and eventually spoken by the daemon's own seams
too.

Answered the first part honestly (it holds; see the thread) and shipped the
second as phase 1: `prompts/weave.md` — the working-register contract
(coordinates over descriptions, deltas over narration, marks over clauses,
state lines over paragraphs, frontmatter thinking), written in the register
it names, with the strike-rule guard against glyph costume ("the measure of
a mark is the clause it replaced") and hard channel boundaries (user chat /
kb / machine-parsed stay plain and exact). Wired via
`_read_preamble_with_weave()` on both runner paths, pinned in
`test_prompts.py`. `design-weave-register.md` carries the reasoning, the
ornament-knob reconciliation (two dials: presentation vs working register),
the efficiency reconciliation (notation is denser than prose — the register
should save tokens), and the phase-2 plan: inventory daemon-written scroll
markers (hook injections, portal-update framing) and move them onto the same
grammar in one pin-moving commit, then re-evaluate diffense presentation on
top (it was disabled for being boring to read).

Branch: brr/director-voice.

## [2026-07-02] decision+design | brr revival withdrawn; hot-idle residency scrutinised

Folded-in follow-up from the same thread. The round-4 `brr`-as-lore proposal
is withdrawn by the maintainer ("not a real verb… just confusing"); `brr`
stays retired, no reservation — recorded in the rename decision and the brand
page. The stingy-director sharpening (strong-core wake idles hot on a poll
loop instead of terminating; proactive pacing driven by observed quota data)
is scrutinised in a new section of the director-loop design: cache economics
real but 5-minute-cliff-bounded, single-flight slot is the scarcer resource,
quota telemetry already extracted (`claude_usage`/`codex_status`) but lacks
the policy seam into pacing. Direction: yes to a *short* post-delivery
linger as a named contract, no to long residency; quota-aware pacing gets
its own design pass.

Branch: brr/director-voice.

## [2026-07-03] design | weave round 5 shipped — the wake scroll reweaves itself; pins moved

Round 5 spanned two runs. The first (run-260703-0020-n31e) did the
notes-first inventory of the wake scroll's incoherences and reweaved
`run.md`, `daemon-substrate.md`, `identity-core.md` §Voice into the
register `weave.md` names — then died mid-flight (host laptop lid closed);
the capture net salvaged the in-flight work as 30a892a, with the
reasoning, the pre-edit inventory, and the Ummon/BT-7274 answer already
recorded in `design-weave-register.md` §Round 5. The ornament-toggle
question resolved into the reader-model seam: one voice, variable
unfolding, working field `user_commitment: full | profane` declared at
the event boundary — the appearance-knob TOML schema is cut.

This run picked up the salvage: moved the four `test_prompts.py` pins the
interrupted run left red onto the reweaved text (dumb-test rule — the
`"Appearance settings"` pin follows its section into `user_commitment`,
the Mode/recovery/log-step anchors follow the orient block's new
wording), and made `test_run_worker_threads_runner_quota_into_prompt`
hermetic: the maintainer's stitch commit (3d2892c) lets level-derived
quota override the config summary at attempt 1, so the old codex test was
silently reading the *host's* live session rollout — now pinned to the
fallback path via a stubbed `_collect_levels`. Full suite green (1238).

Also the first live observation of the stitch seam: the burst weave fires
only inside the dispatch window; follow-ups landing ~40s after dispatch
arrive through the inbox portal instead, which is the designed complement,
not a failure. Both seams now exist; orphaned same-thread fragments should
be gone either way.

Branch: brr/director-voice.

## [2026-07-03] design+fix | weave round 6: bundle register audit; claude_usage learns Fable buckets

Second complete pass over the initial context (maintainer's coherence
questions: boot-sequence shape, accord with the Shell's own layers,
embedded-not-overriding). Findings recorded as a round-6 inventory in
design-weave-register.md; two fixed in the same run: the bundle's
`- Delivery:`/`- Budget:`/`- Runtime recovery:` bullets rendered under
`### Runner Mandate` instead of `### Mode` whenever a catalog exists
(prompts.py reorder), and AGENTS.md §Orientation cited the fallback-path
block name "Recent in this conversation" instead of the primary
Communication-snapshot render. AGENTS.md voice direction proposed (house
voice yes, register density yes, resident intimacy and glyph-load-bearing
no — it must load-bear solo for foreign agents and adopter seeds); the
full pass parked as its own commit.

claude_usage: the TUI grew a `Current week (Fable)` bucket that clobbered
the all-models weekly number (5% → reported 8%). Parser now keys week
buckets by label — primary week stays all-models, per-model buckets land
in `week_models` and append to the quota summary ("Fable week 89% left").
Probe is ~3.5s post-optimization, so cache TTL dropped 300s → 120s
(`BRR_CLAUDE_USAGE_TTL` overrides); capture lingers 0.5s after
session+week parse to catch the trailing model bucket. Live-fired ✓.
Pacing implication noted in design-director-loop.md: per-Core weekly
quota is now readable data for the quota-aware pacing pass.

Branch: brr/director-voice.

## [2026-07-03] design+plan | weave phase 2 shipped; director execution plan + tickets #211-#217

Phase 2 of the weave register (the daemon meets the register) shipped as
the delivery-contract compression: standing rules moved into
daemon-substrate.md's new "delivery portals" block (stdout discipline,
outbox frontmatter grammar, inbox/portal-state semantics, keepalive/card,
remote-reader rule, receipts); the bundle's Delivery contract now renders
live values only. Mode block: quota on its own `- Quota:` line; "Runner
Mandate" renamed "Runner catalog". Hook injections were found already
conformant ([brr portal update] + key:value lines) — the register was
discovered there too. Pins moved per the dumb-test rule; suite 1241 green.

Naming settled with the maintainer: "ornamentation" retires; what exists
is the *register* (resident notation, never stripped) and *unfolding*
(user_commitment: full | profane — full = weave-density replies in chat,
opt-in per reader). Self-naming (Ummon says "Ummon") parked as an
identity-core fork. claude_usage TTL stays at the maintainer's 10s with
an honest comment (any TTL under the 30s beat = probe every beat).

The next big chunks got their execution plan: plan-director-execution.md
converts design-director-loop.md into owner-routed tickets — workstream A
(structured pacing: next-move contract #211, closeout parse #212, quest
log #213, director tick, diffense reveal re-skin) and workstream B
(stingy delegator: quota-aware pacing #214, delegation policy + slim
worker stack #215, post-delivery linger #216), plus the voice tail
(user_commitment plumbing #217, AGENTS.md house-voice pass).

Branch: brr/director-voice.

## [2026-07-03] feature | stream A opens: next-move contract (A1/#211) + post-delivery linger v1 (B5/#216) + reader-model line (#217 v1)

Three slices on brr/stream-a-pacing, all prompt/doc-side after one code
reading settled the shape: `daemon.py::_result_satisfied_delivery` already
counts a mid-thought outbox reply as the satisfying signal
(`current_reply`), so the linger needs no daemon change — outbox delivers,
`.keepalive` holds the slot, `portal-state.json` carries the yield signal.

- A1: portals manual grew §"The next move" (an addressed reply ends
  `done — receipt` | `continuing — what's next` | `blocked — what's
  needed` | genuine fork: 2–4 numbered options + recommendation + one-line
  reason; manufacturing options named the failure mode), with the compact
  rule in daemon-substrate's delivery-portals block.
- B5 v1: §"The post-delivery linger" — deliver via outbox first, backoff
  30s → cap 240s (inside the ~5m provider cache window), absolute yield on
  any unrelated pending event, default horizon 10–15m past last delivery.
  Parameters from the maintainer's live ask; the 10–20h vigil variant is
  deferred to quota policy (#214) + compaction.
- #217 v1: `user_commitment` config key (maintainer had already set
  `full`) now rides the communication snapshot as a "Reader model" bundle
  line; per-correspondent gate declaration stays open under #217.

Pins in test_docs.py / test_prompts.py (manual and substrate halves
cross-referenced); full suite green. Plan page updated with shipped
markers + the v1-outcome note; introspection.md rework added to the voice
tail (maintainer confirmed it was planned).

Branch: brr/stream-a-pacing.

## [2026-07-03] closeout | #216/#217 closed after safety-net audit; #219 filed; buttons fork settled

Maintainer asked whether #216 (post-delivery linger) was complete and
whether it wanted a safety net. Audit answered yes on both counts:

- Keepalive verified as no hindrance — `daemon.py::_budget_exceeded`
  honors `.keepalive` up to `hard_cap = max(4×budget, budget+1h)`, checked
  per 10s heartbeat; a linger that writes its keepalive at step 2 of the
  contract cannot be reaped inside its horizon. The hook layer's
  "running long" warning covers the one sharp edge (keepalive must land
  before the soft budget expires).
- Tail-sleep example added to portals manual §post-delivery linger: one
  bounded `timeout`-wrapped poll per tool call, backoff carried by the
  call *sequence* (30→60→120→240s). Per-call polling doubles as the
  lesser-core safety net — portal-update hooks push pending events into
  context between polls, so the yield rule gets checked mechanically.
- Remaining scope split to #219: non-terminal `attending` card phase
  ("delivered · attending") driven by signals that already exist
  (`interim_response` on the lead event + live keepalive); terminal
  `delivered` unchanged. Mascot note rides the same issue: card edits
  happen on packets + the 10s heartbeat, so a frame-per-render breathing
  glyph is free; true animation is not feasible (Telegram flood control
  on editMessageText).
- #217 closed: v1 live (Reader model bundle line observed working);
  per-correspondent gate declaration re-tickets when real.
- Director-loop buttons fork settled by the maintainer: plain numbered
  text everywhere, no inline keyboards — free-form multi-part replies
  ("1a 2a 3c and do x") are the native flow buttons would break.
  design-director-loop.md updated.

Branch: brr/linger-net.

## [2026-07-03] implement | Spiral reply shape for mid-run updates

`src/brr/prompts/weave.md` now says user-facing replies should unfold
spirally: the densest, most complete line first, then detail outward so a
mid-run skim gets the verdict immediately and later correction can land
without waiting for a second pass. The broader workflow / director-loop
question stays in [`design-director-loop.md`](design-director-loop.md) and
[`plan-director-execution.md`](plan-director-execution.md); this pass only
set the reply seam. Shipped in commit `0c97bcf` on `brr/linger-net`.

Branch: brr/linger-net.

## [2026-07-03] implement | Daemon-owned post-delivery attending floor

Follow-up to the linger safety-net audit: the prompt-only contract was not
enough. Hooks can fold a follow-up only when it is already pending before
runner stop; once the runner has returned, card state and the slot posture are
daemon-owned.

`daemon.py` now emits an `attending` packet after a successful configured-gate
current-thread delivery, holds the single-flight slot briefly
(`delivery.post_delivery_attend_seconds`, default 90s), yields immediately
when any pending event appears, and then lets terminal `done` restore the
final `delivered` state. `run_progress.py` renders the nonterminal phase as
`delivered · attending`; Telegram inherits the card packet set, and Slack /
GitHub mark `attending` renderable. The portals manual, daemon substrate
prompt, and director-loop kb now distinguish runner-owned same-thought linger
from this daemon-owned post-return safety floor. While diagnosing the live
failure, `hooks_installed` also became a real persisted update packet instead
of an emitted-but-dropped breadcrumb, so future wakes can tell "config
installed" apart from "hook fired".

Focused verification: `test_post_delivery_attend_*`,
`test_render_text_compact_attending_is_nonterminal_delivery_phase`,
`test_hooks_installed_packet_is_persisted`,
`test_portals_manual_defines_post_delivery_linger`, and
`test_daemon_prompt_carries_next_move_and_linger`.

Branch: main.

## [2026-07-03] implement | Seam bench v1 — lesser-light runners as measuring instruments

From the maintainer's proposal: the resident can read the wake scroll from
inside, but only an economy core reveals where the seams are actually weak —
haiku/mini break exactly where Fable routes around silently. `brr bench`
packages that as a one-command probe cycle: sandbox repo + sandboxed
`BRNRD_HOME` (real orientation stack, isolated account), `python -m brr up`
spawned against it, scripted lead event through the real inbox protocol,
optional follow-ups injected at first signal, then a harvested transcript
scored by deterministic probes (`response`, `next_move`, `card`, `interim`,
`fold`, `single_run`) into `report.md` + `transcript.md`. The exact
`prompt.md` each spawned runner saw rides along as the flight recorder.

Key mechanics discovered while wiring: a folded follow-up leaves a
`write_partial` + `status: done`, not a terminal response — the fold probe
reads partials and targeted `interim_response` records; `card_composed`
packets carry the note under `text`. Scenarios v1: `simple-ask`,
`followup-fold`. Design + open seams (cost capture, cross-core diff,
bench-driven pitfalls): [`design-bench-loop.md`](design-bench-loop.md).
Tests cover the deterministic core only — a bench run spends real quota by
design.

Branch: brr/bench-loop.
## [2026-07-03] fix | Runner catalog: correct metadata, no bloat

The wake-prompt Runner catalog was bloated and sometimes wrong (12 entries,
`core=default` on model-pinned profiles, `availability=available` noise on
every line). Three causes, three fixes:

- **Per-field profile merge** — a declared profile whose name collides with a
  registry-generated twin now overlays its fields on the twin
  (`runner._selection_profiles`) instead of replacing it wholesale, so a
  project override that pins only `cmd` no longer sheds model/class/cost
  metadata. `runner_cores.generated_profile_entries` emits twins for declared
  names to make that merge possible; declared fields stay authoritative.
- **auth_env availability gate** — `_runner_available` now requires a
  profile's declared `auth_env` (e.g. `ANTHROPIC_API_KEY` for
  `claude-bare-api-only*`) to be present, so keyless API-only variants stop
  being listed as available and stop being doomed fallback targets.
- **Render trim** — `prompts._render_runner_catalog` renders `availability=`
  only when it is *not* "available"; the catalog is pre-filtered to invokable
  profiles, so the constant field was pure noise.

Root cause on this host was also config drift: the local `.brr/runners.md`
(gitignored) had lost all Core metadata, still used `--safe-mode` (silently
hook-killing per the bundled runners.md), and statically shadowed the
registry's bare-api model variants. Re-mirrored from bundled frontmatter with
a drift note in the file (backup at `/tmp/runners.md.bak-260703`).

Also folded in: `prompts/weave.md` spiral-unfolding rule now says open
decision forks ride in the first, densest turn of a reply — named fork +
recommended arm up top — so the reader can react at the speed of the skim
(maintainer ask, telegram 2026-07-03).

Verification: `test_available_runner_catalog_excludes_profiles_missing_auth_env`,
`test_declared_profile_inherits_registry_metadata_per_field`,
`test_generated_profile_entries_emit_twin_for_declared_name`,
`test_runner_catalog_renders_only_unusual_availability`; full suite 1252 passed.

Branch: brr/runner-catalog-trim.

## [2026-07-04] implement | Workstream B shipped — quota pacing + delegation, delegation itself dogfooded

Maintainer reranked to Workstream B (`kb/plan-director-execution.md`),
"probably both tickets," and asked to delegate the delegable halves while
building the delegation policy — a live case study, not just an
implementation pass. Both #214 (B1+B2) and #215 (B3+B4) shipped on
`brr/director-stream-b`.

**B1** (design/prompt, direct): decided the quota-pacing policy —
binding bucket = lowest remaining% across session/week/per-model week; two
account-policy floors (`pacing.quota_low_floor_pct`=20 stretches `every:`
cadence, `pacing.quota_critical_floor_pct`=8 pauses it); `at:` deadlines and
gate-addressed replies stay non-discretionary. Named the real gap B2 had to
close: `_merge_level_snapshots` only ever forwarded a rendered *string*,
never the `remaining_percentage` numbers the parsers already computed.
Detail: `design-director-loop.md` §B1.

**B3** (prompt, direct): named the two execution stacks in
`prompts/dominion-playbook.md` §Delegation — resident (full, default) vs
worker (task + files + result contract, no dominion write). `worker: true`
alongside `respawn: true` opts a spawned run into the slim stack; a bare
respawn (e.g. `quality: escalate`) keeps the full one. Mirrored into the
live dominion playbook so the policy governed this run's own delegation
calls immediately, not just future ones.

**B2 and B4 were delegated**, not hand-rolled — two subagents,
`isolation: worktree`, launched in parallel against written briefs carrying
exact file:line pointers, the decided policy, and the existing test pattern
to mirror. Both came back correct and tested:

- **B2**: `claude_usage`/`codex_status` now expose numeric remaining-percent
  per bucket; new `runner_quota.binding_quota_remaining_pct`; `_fire_due_schedules`
  stretches/drops `every:` entries under the floors (`due_entries` stays pure
  — the bending happens to the entry list passed in, via `dataclasses.replace`);
  `resources.quota.pacing` surfaces the same number mid-run. The delegate
  flagged its own soft spot rather than hiding it: the scheduler-tick quota
  read has no single "current run" to key off, so it reads a shared
  `brr_dir`-level cache nothing writes to yet — correct and tested, inert
  live until a follow-up feeds that cache.
- **B4**: `worker: true` frontmatter → `task.meta["worker"]` (via the
  existing `Run.from_event` meta-copy path, no new plumbing needed there) →
  `build_daemon_prompt` swaps in a new `prompts/worker.md` preamble and skips
  the resident injected blocks (identity core, dominion, plans, policy,
  ledger, pitfalls, kb health, introspection); `daemon-substrate.md` stays
  (a worker still runs under the daemon). Default path confirmed
  byte-identical via the existing pinned suite.

Both worktrees merged clean (`ort` auto-merge, no conflicts despite both
touching `daemon.py` — isolation meant no shared-file race during the actual
edits). Full suite 1288 passed after both merges (was 1273 before this run).

**On the delegation itself**: the brief was the expensive part, not the
implementation turn — excavating the exact hooks (`_merge_level_snapshots`
silently dropping numeric fields, the `Run.meta` wire path for a new
frontmatter key, the existing test fixtures to mirror) took longer than
either agent spent building against them. Matches the design's own
scrutiny (`design-director-loop.md` §orchestrator/worker): delegation pays
off on bounded, well-specified work; naming the bounds is still the
resident's job, and a vague brief would have cost more in rework than it
saved in hand-rolling.

Verification: full suite 1288 passed; both new respawn-frontmatter cases
(`worker: true` sets the flag, bare `respawn: true` omits it) and both
prompt-assembly cases (worker excludes resident stack, default unchanged)
pass; B2's schedule/facets/parser tests (critical pause, low stretch, no-op
without a resolvable runner, `at:` always fires) pass.

Branch: brr/director-stream-b. Also folded in: `.gitignore` now excludes
`.claude/` (Claude Code session settings + isolated subagent worktrees,
previously untracked noise with real risk of a careless `git add -A`
sweeping nested worktree checkouts into a commit).

## [2026-07-04] design | Worker/resident split re-justified; respawn reframed; B6 quota-smoothing gap opened

Maintainer questioned the B3/B4 worker/resident split just shipped: dominion
and kb are git-versioned (mergeable) and worktrees isolate concurrent
mutation, so "pollution risk" — the framing B3/B4 shipped with — was never
the real hazard. Reconciled in `design-director-loop.md` §"Re-justifying the
split": the actual grounds are judgment scope (a worker has no continuity,
can't be accountable for standing-memory calls it won't be there to defend)
and cost (full resident stack is waste on bounded tedium) — split stands,
on sharper footing. Also reframed `respawn:`'s stated purpose away from
stale "cheap-dispatcher escalation" language (superseded by the
account-centered daemon's narrow dispatcher) to its real distinct value vs
in-run `Agent`-tool subagents: cross-Shell/cross-repo/outlives-this-run
handoff, something in-harness subagents structurally can't do. Kept both
mechanisms; didn't unify.

Confirmed live (not a brr bug): the "two status cards, dash finish" the
maintainer saw during last round's B2/B4 delegation traces to
`.claude/worktrees/agent-{a5ad9b48,ad2f18b8}...` — Claude Code's own
`isolation: worktree` Agent-tool subagents, no `run_id`/`.card` of their own
in `daemon.py` (`.brr/runs/` shows one run, `run-260704-0202-lmlk`, for the
whole session). Whatever rendered the extra cards is the surrounding
harness's own subagent UI, outside this codebase.

Opened `B6` (#224): weekly-quota smoothing + cross-runner load balancing,
sharpening B1 — the 5h window is an anti-burst valve, the weekly bucket is
the real scarce resource (oversold: full weekly exhaustion for 4 weeks costs
~5x subscription price), and B1 reacts per-beat but doesn't pace consumption
forward against days-remaining-in-week. Blocked on data (a week+ of observed
per-runner burn), not code — named rather than guessed at.

Branch: brr/director-stream-b.

## [2026-07-04] research | Caching mechanics clarified; B6 data gap partially closed without a bench

Maintainer asked (telegram) whether idle wall-clock time during a
permission-gated wait gets billed, whether TTL-eviction and compaction are
the same trigger, and whether B6's "blocked on data" needs a bench.
Answered in `design-director-loop.md` §"Cache TTL vs compaction, and B6's
data problem revisited": TTL eviction (time-based, ~5m) and compaction
(capacity-based, context-window fill) are distinct; idle time itself is
never metered, only the next call after a TTL lapse pays a cache-miss
price. Confirms the existing §Hot-idle residency framing, doesn't revise it.

The concrete finding: checked `$CODEX_HOME/sessions/**/rollout-*.jsonl` on
the operator's machine — 69 of 88 recent rollout files (spanning
2026-06-20 to 2026-07-04, the actual dogfooding window) are this repo's,
and every one carries timestamped `rate_limits` snapshots. That's B6's
"week+ of observed burn" already on disk for Codex; no forward wait or
bench needed there, just a retroactive extraction script. Claude has no
equivalent persisted history (`claude_usage`'s PTY scrape is present-only)
— its half of B6 needs a forward log line added now, not a new collector.
`design-bench-loop.md`'s seam-bench answers a different question
(protocol-following under a lesser core) and doesn't apply here.

Also: small wording fix landed in `daemon-substrate.md` and
`src/brr/docs/portals.md` — the post-delivery-attending → next-run
handoff is now explicitly framed as "an unblock, not a restart" (same
conversation/dominion/kb re-read, only the process renews), per the
maintainer's pushback that "restart" language undersells the continuity
that's actually preserved.

Release-prep list from the same message (website polish, packaging, repo
grooming, kb pricing/PII scrub, kb→home placement, GitHub App + bot
identity) not started this run — confirmed real and non-trivial (pricing
strategy lives in `decision-pricing-shape.md`/`design-billing.md`, personal
financial/legal-entity detail in `plan-financial-growth.md`, PII scattered
across ~15 kb pages) and the kb→home placement question already has an
active design track (`design-home-scopes-and-knowledge.md`). Left as a
fork for the maintainer to sequence rather than guessed at inline.

Branch: brr/director-stream-b.

---

## [2026-07-04] research | "Current Planned State" is CS5+CS7 already shipped; dashboard view is the actual gap

Maintainer's telegram burst (6 follow-ups) proposed an account-scoped,
web-visible surface for workstreams/plans/forks/blocks/decisions, distinct
from per-run status, gamified toward a Persona-5/Zachtronics direction, plus
asked for tighter token-efficient reading habits, a Stripe/pricing timeline
push, and a voice loosen-up. Checked against the kb before building anything:
the plan/decision surface he's describing **already shipped** as CS5 (`plans/
<repo-slug>/active.md`, `plans/_cross-repo/active.md`) and CS7 (`ledger/
decisions.md`) in `plan-control-surface.md` — both injected every wake
already. The real gap is that `plan-brnrd-dashboard-mvp.md`'s view inventory
never scoped a route rendering those two files; added that gap plus two open
questions (archival convention for "parked" plan files; skin-now-vs-later
fork for the Persona-5 aesthetic) as a new section there rather than
inventing a parallel page. The bus-vs-subway cost metaphor is likewise
already data, not a new idea: the runner catalog's `cost_rank`/`class`
spectrum (`design-runner-cores.md`).

Bumped GH #53 (Stripe billing integration) with the 2-month timeline and a
new Stripe-Connect-for-user-side-profit-sharing question flagged as scope,
not committed. Named but did not open a ticket for hosted-mode execution
liability (distinct from #80, which is the *local* Docker trust-model
doc — the maintainer's ask this time is the *hosted* ToS/liability posture,
unscoped, needs its own decision before a ticket is worth writing).

"The teachers are really features" (evt-…z148) stayed unresolved — no
"teacher" reference anywhere in kb or this thread's visible history; asked
the maintainer which transcript that refers to rather than guessing.

Branch: brr/director-stream-b.

## [2026-07-04] implement | Token-efficient reading habit shipped; hosted-liability shape unpacked; burst thread closed out

Woke to the *first* message of the maintainer's 2026-07-04 telegram burst
(evt-…gosn: "bus vs subway... compress your language... greps and counts
for small tasks, sub spawns for abstract/complex tasks") — the previous
director-stream-b run had named this ask ("tighter token-efficient reading
habits") but not acted on it. Added a "Reading economically" section to
the dominion playbook, mirrored into `src/brr/prompts/dominion-playbook.md`
(the seed, so future dominions inherit it): grep/wc/offset-reads for small
facts, subagents for broad sweeps, full `Read` reserved for when the range
is genuinely the ask — the input-side mirror of the weave's output
density.

A new event (jzu3, "check my reply in #53") surfaced the maintainer's full
GH #53 comment, which the bundle's truncated pending-event summary hadn't
shown: substantive feedback on the previous run (produce-vs-topic-count
ratio, whether follow-ups were waited for, a tone shift with no matching
prompt edit, a "wtf" on the hosted-liability line being named but not
expanded), plus resolutions to two of that run's open questions (archive
files get a `parked: <date> · why:` header; ship the CPS view plain, skin
later, but the general frontend stack is a *separate*, still-open quality
concern regardless of skin timing) and the actual answer to "teachers are
features" (a dictation misparse of "developed multiple teams").

Wrote [`decision-hosted-execution-liability.md`](decision-hosted-execution-liability.md)
— the expansion the maintainer asked for: three postures (ToS/beta
disclaimer, technical containment reusing #80's `docker.isolation=clone`
hosted-default, or both), recommending the disclaimer now with containment
as a parallel track, explicitly not resolving the posture choice (real
fork, his call). Updated `plan-brnrd-dashboard-mvp.md`'s open-questions
section to close the archive-header and aesthetic-fork items and open the
frontend-stack-quality item in their place.

Filed a pitfall (dominion `pitfalls.md`): telegram events sent while the
maintainer's laptop was off land as one `getUpdates` batch on reconnect,
so their event-ids share one ingestion timestamp even though
`telegram_message_id` order (and processing order) is intact — reading
event-id epochs as "when he said this" during orientation looks like
misfiring/reordering that isn't actually there. Only shallowly verified
(read the ingestion loop, didn't trace id-stamping) — worth a real check
if it ever actually misorders rather than just looking like it might.

Closed out all 11 pending events from the burst (2akx, jkbs, z148, 4qqd,
v7me, 44n9, 5z0s, dlkj, jzu3, oyv2, 1ikz) in one telegram reply plus short
per-event closers, rather than leaving them to spawn 11 separate runs for
one continuous conversation. Left 5z0s (onboarding/hackernews timing)
genuinely open — tracked, not designed.

Branch: brr/reading-economically-and-hosted-liability.

## [2026-07-04] implement | CPS plan/ledger populated; A4-lite director tick shipped; ToS budget path answered

Two threads from the same telegram message. First, legal: maintainer
confirmed HugiMuni is just him and his wife, no counsel relationship, ~€1k
budget, pre-revenue — asked whether "measured path of least liability now,
real legal work with customers" is safe. Answered in
[`decision-hosted-execution-liability.md`](decision-hosted-execution-liability.md)'s
open-questions section: yes for a beta stage, via a SaaS ToS template
(Termly-class service or a fixed-fee marketplace lawyer pass, both inside
budget) adapted for the French/EU consumer-protection floor already named
on that page, not bespoke counsel yet — with the caveat that this is advice,
not a legal opinion, and counsel should still eyeball the final text.

Second: "what's missing to let you continuously handle work in a cost-aware
manner and nag me, rather than me always initiating — the comaintainer ark,
bidirectional exchange." This maps exactly to A3 (quest log) and A4
(director tick) in
[`plan-director-execution.md`](plan-director-execution.md) — both scoped,
neither built. Checked the account dominion directly: `plans/Gurio__brr/
active.md` and `ledger/decisions.md` (CS5/CS7, shipped 2026-06-30, and the
CPS dashboard reading them shipped today per the previous entry) had been
*empty* the whole time — the pipe existed, nothing flowed through it. This
is what "the plan is yet to be pushed to be validated" (the maintainer's
own words, confirming a different guess) actually meant.

Populated both files for real (ranked moves, current decisions) and added
a `director tick` entry to `schedule.md` (`every: 24h`) — A4 ahead of A3's
formal ranking discipline, judged reversible and cheap enough to ship now
rather than wait. Silence is the entry's default outcome by design (message
the gate only on a changed top move or a new block); named the one honest
gap in the entry itself — B2's live per-tick quota read is inert in
production, so this ticks on a flat interval, not yet bent by observed
quota per B1's policy. Mirrored both the A3 content-instantiation and the
A4 shipment back into `plan-director-execution.md` so the ticket status
reflects reality.

Not yet confirmed: whether the daemon's next wake actually renders the
"Active inter-run plan" injection block from the newly-written file — the
plumbing (`_build_inter_run_plan_block`, `_publish_plans`) reads the right
paths by inspection, but this run didn't trigger a fresh wake to observe
it landing. Worth a glance at the next wake's injected context.

Branch: brr/comaintainer-tick-and-tos-budget. Account dominion (separate
repo, no remote configured — local-only) committed directly: `0a8b87f`.

## [2026-07-05] implement | Director tick cadence tightened 24h → 5h; injection pipe confirmed live

Follow-up to the previous entry, same telegram thread. Confirmation first:
this run's own wake carried an "Active inter-run plan" block populated
from `plans/Gurio__brr/active.md` — the open question from 2026-07-04
("not yet confirmed: whether the daemon's next wake actually renders
[it]") is answered yes, the pipe works end-to-end
(`_build_inter_run_plan_block`/`_publish_plans` → wake injection).

Maintainer then judged the 24h director-tick interval too infrequent and
asked for "more often/flexible," floating "5h same as window?" — read as
the provider's 5h anti-burst session window named in B6
([`plan-director-execution.md`](plan-director-execution.md) §B6), a real
anchor rather than a round-number guess. Changed the account dominion's
`schedule.md` `director tick` entry to `every: 5h` and updated the prose
(cadence rationale, "daily" → "5h" language) in that file, the ledger, the
plan file, and this repo's `plan-director-execution.md` A4 section to
match.

Named the honest limit again, since it's easy to conflate "more frequent"
with "flexible": B1's quota-aware stretch/pause floors are coded and
tested but B2's live per-tick quota read is still inert in production (the
scheduler reads a shared cache nothing writes yet). So this is a tighter
*fixed* interval, not a quota-bent one — true flexibility still waits on
#224. Didn't chase B2 in this run; it wasn't asked for, and it's sized as
its own delegable ticket (`plan-director-execution.md` §B1, "B2 scope").

Branch: brr/director-tick-5h-cadence. Account dominion (separate repo, no
remote configured — local-only) committed directly.

## [2026-07-05] fix | Director tick "didn't fire" — false alarm, diagnosed and closed

Maintainer reported (telegram, same thread) that the schedule "didn't
fire," reading it as a likely daemon bug, and asked it be treated as the
top priority of the run. Traced end-to-end before touching anything:
`.brr/schedule/state.json` shows both 5h firings landing exactly 18000s
apart (02:48:19 UTC, 07:48:19 UTC); the account dominion's git log has a
real commit per firing, including firing #2 merging PR #230 (GH
`mergedAt: 2026-07-05T07:49:12Z`); `brnrd up --dev-reload` (pid 734160)
ran continuously across the whole window with no restart. No scheduler
bug — `schedule.due_entries`/`_fire_due_schedules` behaved exactly as
coded.

The actual gap: the director-tick's notify rule ("speak only if the top
move changed or something newly became blocked") kept *both* ticks
silent, even though firing #2 shipped real, committed work. Ten hours of
correct, quiet operation was indistinguishable from the schedule never
firing — which is exactly the false alarm the maintainer hit, and exactly
the risk the 2026-07-05 cadence-tightening entry had already named
("watch the first few firings for silence discipline... before trusting")
without anyone closing the loop back to the maintainer. Fixed the rule
itself (account dominion `schedule.md`, not this repo): a tick now also
sends one line when it took a committed action this beat, alongside the
existing rank-change/newly-blocked triggers; pure re-derivation with
nothing new still stays silent. Mirrored the finding into
[`plan-director-execution.md`](plan-director-execution.md) §A4 and the
account dominion's ledger, since the next resident to add an ambient
`every:` entry will hit the same "silent ⇒ looks broken" trap otherwise.

Also picked up two secondary items the maintainer named as next-best
candidates while the scheduling trace was still running (per "if you feel
like it, do them in the same run"): commented on
[#212](https://github.com/Gurio/brr/issues/212) (A2) responding to the
maintainer's own doubt that the daemon needs to parse `next:` at all —
traced `conversations.py`'s existing recent-turns selection and found it
already threads the resident's own prior numbered-options reply into the
next wake's context for free, so a short "2" reply may not need any new
parsing/store, only a native inline-keyboard UX would. Recommended
shrinking the ticket rather than closing or implementing it outright —
scope call, left for the maintainer. Did not touch B2 (#224) — real work,
not asked for this run, still the right-sized next candidate per the
active plan.

Branch: brr/director-tick-false-alarm-notify-bar. Account dominion
(separate repo, no remote — local-only) committed directly.

## [2026-07-05] fix | Response-text leak on aborted streams; A2/A3 closed; next-move discipline evidence

Maintainer turn responding to the same-day notify-bar fix: closed A3
(#213 — ranked move list, evidence: three re-ranks across 07-04/07-05
closeouts plus this run's own bundle confirming live injection), shrunk
A2 (#212 — closeout parse) to validate-only after confirming
`conversations.py`'s recent-turns selection already threads the
resident's own prior numbered-options reply back into the next wake for
free (no daemon `next:` parsing/store needed); maintainer confirmed
buttons stay parked, free-form numbered text wins for now. B6 (#224)
agreed as next up.

The substantive ask: check the last 2 days of chat history as evidence
for a standing observation that output felt less rich than before —
closeouts not reliably naming their next move, prompts not updated to
match prior discussion. Sampled all 27 chat-facing response artifacts
from 2026-07-03 through 2026-07-05 (`.brr/runs/*/history/gate_thread-*
.jsonl`, `artifact_kind` in `response`/`outbound_message`). Two real
findings:

1. **Prompt-discipline gap, confirmed.** Several 2026-07-04 closeouts
   were bare single-token stubs (`.`, `-`, `done`, `(unchanged)`) with no
   `done —`/`continuing —`/`blocked —`/fork line anywhere in the turn,
   despite the A1 next-move contract (`src/brr/prompts/daemon-substrate.md`)
   already existing since 2026-07-03. Fixed: tightened the contract to
   state explicitly that options sit compactly at the message's very end,
   free-form text (not buttons — the same A2 call), plus an explicit
   "check the literal last line before sending" guard, since prose alone
   had already been in place and wasn't enough.

2. **Genuine code bug, found while tracing #1.** `run-260704-1704-ttrd`'s
   stored "response" conversation artifact was a raw Claude CLI
   `--output-format json` result envelope
   (`"result":""`, `"terminal_reason":"aborted_streaming"`) rather than
   any reply text. Root cause: `claude_status.result_text()` only checks
   `result` (non-empty string) then `errors` (non-empty list); when a
   stream aborts mid-turn, Claude Code can return `subtype: success,
   is_error: false, result: ""` with no `errors` — neither branch fires,
   and the function fell back to its `fallback` parameter, which at its
   only call site (`capture_stdout`) is always the very stdout JSON it
   had just parsed as that envelope. The raw JSON got written to the
   response file and indexed into conversation history as if it were the
   reply. Fixed in `src/brr/claude_status.py`: when `result` is present
   but empty/blank and there are no usable `errors`, return
   `(runner produced no reply text: <terminal_reason or stop_reason>)`
   instead of the envelope; the pre-existing "non-JSON stdout passes
   through unchanged" path (a dict with no `result` key at all — e.g. a
   custom command's own intentional JSON output) is untouched. Regression
   test added: `tests/test_claude_status.py::test_result_text_does_not_leak_raw_envelope_on_empty_result`.

Two secondary points from the same maintainer message: silent PR merges
were already addressed by the same-day notify-bar widening (confirmed,
no further change); "harden the message reception path after the burst
handling issue" was named as still outstanding, but tracing found no open
issue or unshipped gap — burst-coalescing (#128) shipped 2026-06-20 and
closed. Flagged back to the maintainer in the reply rather than
manufacturing work against an untraceable reference — inventing a fix for
a gap that can't be located would be the same failure mode this run was
about correcting. A "morning briefing" idea (low-frequency, non-spam
digest) was scoped but not built: `schedule.py` has no "fixed local time
daily" primitive (only one-shot `at:` and drift-anchored `every:`), so
this is a real design fork on cadence/content, handed back with options
rather than guessed at.

`kb/plan-director-execution.md` §A2/§A3 updated; account dominion
`plans/Gurio__brr/active.md` and `ledger/decisions.md` updated with the
same evidence.

Branch: brr/response-text-leak-and-next-move-discipline.

## [2026-07-05] fix | Pending-event injection framed as action, not telemetry — caught live mid-run

Direct continuation of the same-day next-move/response-leak run: while
composing that reply, three more same-thread follow-ups arrived pointing
at a live example in progress — two earlier follow-ups had already been
read (correctly, via the `PostToolBatch` system-reminder) and used, but
never surfaced on `.card`, an 8-minute silent gap on the only surface the
maintainer was watching. Maintainer's diagnosis: "pending events and
missing user update should scream at your attention... so you are
compelled to react," and named the likely cause precisely — a missing
reaction usually means the injection either didn't fire or wasn't
*framed* as something needing action.

Traced to `src/brr/hooks.py::format_delta` (used by every hook phase):
the pending-event line was `"[header] N pending event(s), M undelivered
outbox file(s)."` — pure data, no imperative, indistinguishable from
telemetry once habituated to seeing it every batch. Fixed: when
`pending > 0`, the line gets an explicit tail — `"Address each below —
fold in, or say on .card why it stays queued — before your next plan
boundary or closeout."` The zero-pending affirmative line (`kb/log.md`
2026-06-23 decision — silence is ambiguous, "0 pending" is not) is
unchanged. `src/brr/prompts/run.md`'s daemon-runs bullet gets the same
lesson in prose. Regression test:
`tests/test_hooks.py::test_post_tool_pending_events_are_framed_as_action_not_telemetry`.
Full suite: 1296 passed.

Same branch/PR as the response-leak and next-move fixes:
brr/response-text-leak-and-next-move-discipline, PR #232.

## [2026-07-05] feat | Card-staleness facet ships; SCM imperative confirmed already-shipped; glyph channel opened

Maintainer follow-up to the pending-event framing fix, three parts, all
resolved this run:

1. **"Should SCM get the same imperative framing?"** — already there:
   `hooks.py::format_delta`'s seed/stop SCM line already ends "commit and
   let the branch publish before ending," gated on unpushed/modified so a
   clean tree stays quiet. Kept as-is rather than adding an unconditional
   durability reminder (maintainer's alternate phrasing) — an always-on
   line would break the file's own "silence is default, action is signal"
   design the SCM/pending-event lines both rely on.
2. **Missing/stale `.card` note → new facet, real gap, not a no.** Unlike
   SCM there was *no* signal at all for a card that goes silent mid-run —
   exactly the failure class the maintainer caught live 8 minutes into
   the previous run. Shipped: `daemon.py::_drain_agent_card` now stamps
   `card_state["written_monotonic"]` on every write or withdrawal;
   `_write_live_portal_state` computes `card.age_seconds` (falls back to
   the run's own `start_monotonic` when never written) and `card.stale`
   (> 240s, the maintainer's own estimate); `_change_token` excludes the
   ticking `age_seconds` the same way it already excludes
   `budget.elapsed_seconds`, so only the `stale` flip counts as
   attention-worthy change. `hooks.py::format_delta` renders a "no change
   in Ns — rewrite .card" line — at **every** phase, not just seed/stop,
   since a mid-run silent card is exactly the failure this guards (SCM
   stays boundary-only; card staleness is deliberately not). Live proof
   the gap was real: a same-thread follow-up arrived naming the exact
   scenario ("6 minutes in: no note on the status card") while this fix
   was still being built. Tests: `test_hooks.py::test_post_tool_surfaces_stale_card`,
   `test_post_tool_silent_when_card_fresh`,
   `test_outbox.py::test_live_portal_state_flags_stale_card`. Full suite:
   1299 passed.
3. **Weave glyph creativity — channel opened.** `weave.md`'s five marks
   (`✓ ✗ ? → Δ`) now read explicitly as anchors, not the closed alphabet:
   a wake may mint an unlisted mark when it earns its place by the
   existing strike-rule (the mark it replaces a clause) and stays stable
   in meaning once reused. What stays closed is importing another
   system's fixed glyph-set wholesale — that was always the actual
   target of "an imported alphabet is someone else's handwriting," now
   said without also gatekeeping self-minted marks.

Also addressed, same message: the maintainer's still-open "stinginess/
laziness" concern from the prior run (output completeness dropping —
PRs not covering discussed points, missed scheduled-wake messages, the
next-move gap that motivated the last fix) — diagnosed as a *different
axis* from prose compression: the register only ever targeted words per
idea, never ideas per task, so treating the observed scope-drops as a
register problem would misdiagnose them. Full reasoning in
`kb/design-weave-register.md` §Round 7 rather than duplicated here; the
short version carried to the reply.

`kb/design-weave-register.md` §Round 7 added. Branch:
brr/card-staleness-and-glyph-channel.

## [2026-07-05] fix | Boot-prompt reconciliation pass

Maintainer asked for a full holistic re-read of everything injected at
wake time, six threads in one message. Resolved to the depth each earned
(full reasoning: `kb/design-weave-register.md` §Round 8):

1. **Economical-execution-vs-coverage line — shipped.** The one concrete
   follow-up named in §Round 7 but not yet written: added under
   `plan-director-execution.md`'s Workstream B header, marking "be
   economical" as binding execution (tool calls, re-reads, delegation)
   and never coverage (whether a raised point gets answered).
2. **Prompt accretion — trimmed.** Found exactly four inline
   `(maintainer, DATE: ...)`-style citations across `weave.md`, `run.md`,
   `daemon-substrate.md` (all from the last two days) restating what the
   surrounding sentence already said. Cut the date/attribution framing,
   kept the one genuinely instructive worked example (run.md's `.card`
   near-miss). `identity-core.md` had none — the pattern is specific to
   the fast-iterating operational prompts.
3. **`introspection.md` ↔ `weave.md` — one missing cross-link, added.**
   They govern different things (what to look at vs. what register to
   say it in); introspection.md's mandated "ergonomics note" is
   user-facing prose that unfolds per weave.md's boundary rule, and
   nothing said so until now.
4. **The "card" naming collision — named, not resolved.** Two unrelated
   things share the name: the outbox `.card` progress-note file
   (user-facing, already labeled "note:" in its rendering) and
   `design-resident-boundary.md` §7's "boundary state card" (the injected
   budget/resources capsule, resident-facing only). Recommended renaming
   the latter (candidate: "gauge" / "envelope gauge") since it's two days
   old and purely internal, and leaving the former alone since it's
   already shared live vocabulary. Left as an open fork for the
   maintainer — cosmetic but not zero-cost, and genuinely their call.
5. **Second-person address — re-affirmed.** LLM instruction-following
   really does track better on second-person imperative, so the
   product-owned prompts' "you" stays deliberate. Real gap found: the
   dominion playbook — the one place first-person was always licensed —
   is still written in inherited "you," never actually exercised as "I"
   by any resident wake. Named as a live experiment for a quieter wake,
   not rewritten this run.
6. **Storytelling in the internal weave — checked, holds.** Compression
   into marks hasn't eaten narrative continuity; the register puts prose
   *between* coordinates and deltas by design, and "why" (vs. "what")
   already routes to kb/ledger prose, exempt from the register.

Also, live and separately actionable: `daemon.py`'s automatic
`delivered · attending` floor (#219, confirmed already shipped) defaults
to a 90-second dwell, far short of the agent-driven linger's 10–15m
horizon — not enough time for a human to read a dense reply and answer
before a follow-up becomes a fresh cold wake. Bumped
`delivery.post_delivery_attend_seconds` to 240 in `.brr/config` (local
runtime tuning, not code) — still inside the ~5-minute provider
prompt-cache window the linger horizon already respects.

Branch: brr/prompt-reconciliation-2026-07-05.

## [2026-07-05] fix | Self-merge policy gap fixed + dashboard follow-up captured

Same-thread follow-up to the envelope-loom/dashboard-priority run. Two
threads, both acted on rather than parked:

1. **Self-merge policy gap — fixed, not just noted.** The prior run
   flagged (per the maintainer, live) that PRs #234/#235 were merged by
   the director tick itself, not by him, and named it a policy gap to
   revisit "next run." Re-checking the timeline made it sharper: the
   maintainer had said explicitly, 17:28z, that #234 wasn't merged
   because his own review wasn't finished — the tick merged it anyway at
   17:50z, 22 minutes later, under a "CLEAN, kb-only, no CI gate" bar it
   had set for itself across three ticks (#230, #234, #235) without ever
   getting a maintainer nod on the *pattern*, only after-the-fact
   approval of individual outcomes. A clean diff was never evidence
   nobody was mid-review of it. Fixed in the account dominion
   `schedule.md`: the director tick no longer merges PRs on its own
   initiative — it re-ranks, reports, and may recommend a merge in its
   notify line, but the human merges (or gives a standing, named-scope
   instruction the entry can cite verbatim). Mirrored into
   `kb/plan-director-execution.md` §A4.
2. **Dashboard follow-up captured.** A message split across two Telegram
   events by length limit — the first half's handling run was killed by
   a daemon restart mid-processing (the maintainer's own apology matches:
   "restarted the daemon, but unfortunately killed right after it started
   the message handling") — was re-sent whole "just in case," confirming
   the reconstruction. Three additions folded into
   `kb/design-dashboard-live-surface.md`: quota needs a time+%-remaining
   dual-axis rendering (not a single time-draining edge), commits/PRs/
   issues need a reserved connector field for future ticket-system
   integrations (Linear/Jira — not implemented, just reserved), and the
   maintainer approved a full frontend-stack replacement, superseding
   `plan-brnrd-dashboard-mvp.md`'s "audit only" framing, with one quality
   bar in place of a framework pick he didn't feel qualified to make
   himself: **"it should survive a fireship review (or alike)."** Nothing
   built — capture for whoever scopes the actual slice.

Branch: brr/self-merge-policy-and-dashboard-feedback-2026-07-05.

## [2026-07-06] ship | Dashboard quota card gets real numbers (#237 slice 1) + B2 pacing-read fix

"Alright, merged, let's build it" — the maintainer's go-ahead after PR
#239 merged, read as: start the dashboard live-surface work, #237 first
per `active.md`'s own next-step note (the dead quota card is the
prerequisite for the window-track visual proposed in
`design-dashboard-live-surface.md`).

Shipped: `PUT /v1/daemons/quota` + `Daemon.quota_json`/`quota_updated_at`
(mirrors the Activity/Plans publish shape a third time) — daemon-side
collector `cloud.py::_quota_snapshot` reads Codex live and Claude via a new
`runner_quota.latest_claude_usage_outbox_dir` helper, which also fixed an
adjacent bug found while building it: `daemon._fire_due_schedules`'s quota
pacing read (`plan-director-execution.md` §B2) was reading a `brr_dir`
cache nothing ever writes to in production — same root cause, same fix,
applied to both call sites at once. `activity_dashboard.py::_quota_views`
replaces the hardcoded-`UNKNOWN` placeholder, flags a report stale past
300s instead of trusting silently-old numbers, and still shows an honest
"unknown" card for a shell with runs but no report yet. `dashboard.html`
had a latent bug fixed alongside: it always rendered "unknown" whenever
used/limit were absent, even with a real percent in hand — never
exercised before because the placeholder was the only shape ever
rendered. 1306 tests pass (5 new: `test_brnrd_quota.py` + additions to
`test_cloud_gate.py`, `test_runner_quota.py`, `test_brnrd_dashboard.py`,
`test_schedule_daemon.py`'s fixture corrected to match the real cache
location).

Not done, named for next: reset is opaque display text, not a
machine-parseable epoch — the window-track visual's time-remaining axis
will need `codex_status`/`claude_usage` to expose a real reset
epoch/duration, not just a formatted string. brnrd-token ledger (separate
resource class per the maintainer's multi-axis note) untouched — no such
ledger exists yet. Detail: `kb/design-dashboard-live-surface.md` §"Shipped
(2026-07-06)". Branch: brr/dashboard-quota-publish-2026-07-06.

## [2026-07-06] ship | Frontend stack scaffolded (PR #241) + a real Stop-hook bug found and fixed (PR #242)

"Agreed on frontend proposed stack" confirmed SvelteKit+Tailwind; shipped
the scaffold (`frontend/`, `adapter-static`, `ssr=false`, builds+lints
clean, not wired into `brnrd_web` yet — PR #241). Reset-epoch plumbing
queued as a codex-shell respawn — the first real worker-stack delegation
to another Shell — after inspection showed the task is asymmetric: Codex
already parses a raw `resets_at` epoch internally (pure passthrough);
Claude only has TUI-scraped free text in two different shapes between its
session/week windows, needing a real next-occurrence-in-timezone
computation. Detail: `kb/design-dashboard-live-surface.md` §2026-07-06.

Same thread, a real pushback on the harness: expecting a human to know
about `.keepalive` to avoid a long implementation session getting killed
"seems unreasonable." Raised this repo's `runner.timeout_seconds`
3600→7200 (`.brr/config`, local/uncommitted by design) rather than just
re-explaining the existing mechanism. Named, not decided: whether the
global code default (`DEFAULT_RUNNER_TIMEOUT`, `src/brr/runner.py`)
should also move, and whether a safety-capped upsize vs. a truly uncapped
budget is the right end state — flagged as a fork rather than changed
unilaterally, since it's a product-wide default.

While queuing that respawn, hit a genuine daemon bug rather than just a
prompt/config issue: `_pending_events_for_agent` counted the just-queued
respawn event identically to an unaddressed user message, so
`pending_event_count` could never reach zero from inside the very run
that created it — dispatching a respawn as a new run requires this run to
end first. The Stop-hook's fold-in-or-explain gate kept re-firing every
phase even after `.card` correctly explained the event was queued on
purpose. Fixed in `_pending_events_for_agent` (excludes
`respawned_by_run`/`respawned_from_event` events — a system-to-system
handoff, not a foldable follow-up), with a regression test; 1307 tests
pass (PR #242) — but that fix only applies to the *next* daemon process;
this run's own live process kept running the old code, so the loop kept
firing regardless of the fix already being shipped.

Rather than keep explaining the same fact to an unchanging hook, built
the reset-epoch plumbing directly instead of waiting on the respawn
dispatch (PR #243: `session_resets_at`/`week_resets_at`/
`week_models[*].resets_at` on the Claude side via a new `_reset_epoch()`;
`primary_resets_at`/`secondary_resets_at` passthrough on the Codex side;
1311 tests pass), then marked the now-redundant respawn event `done`
directly (`protocol.set_status`) instead of leaving it to dispatch a
duplicate run — `pending_event_count` dropped to 0 immediately once the
event was gone, confirming the earlier diagnosis was correct: the fix
in #242 works, this run's own process just couldn't benefit from it
retroactively. Branches: brr/frontend-svelte-scaffold-2026-07-06 (#241),
brr/fix-respawn-pending-attention-2026-07-06 (#242),
brr/reset-epoch-plumbing-2026-07-06 (#243).

## [2026-07-06] fix | Frontend moved into src/, global timeout raised, missing-chat-id root-caused (PR #244); #242 merged

Five follow-ups on the same thread, folded into one run rather than five:

- **#242 merged.** The maintainer had deliberately held it to raise a
  broader point (below), not to block the fix itself — merged once that
  point had a name and a direction.
- **`frontend/` -> `src/frontend/`**, `.upsun/config.yaml`'s build hook
  and static-mount path updated to match (`cd src/frontend && npm ci &&
  npm run build`, served at `/app/`). Build+lint verified clean from the
  new location before committing.
- **`DEFAULT_RUNNER_TIMEOUT` 3600 -> 7200** (`src/brr/runner.py`) — the
  global code default every install falls back to, not just this repo's
  `.brr/config` override raised the prior run. 7200s is 2h, not 8h; the
  daemon's hard cap (`max(budget*4, budget+3600)`) is what reaches 8h off
  that 2h soft default, kept short of true-unlimited on purpose as the
  daemon's only means of reclaiming a hung run.
- **Root-caused the "missing chat id" delivery-error spam**, not just
  silenced it. A schedule-originated event (a director tick, say) has no
  `telegram_chat_id` of its own; `state["chat_id"]` (the delivery
  fallback) was never populated without an explicit interactive `bind`
  step nobody had run, so such an event's response failed "missing chat
  id" on *every* delivery-loop tick, forever — nothing marks a failed
  delivery done (`deliver_stream`'s per-event try/except just logs and
  continues). Two director-tick reports were stuck this way
  (`evt-...-hzyc`, `evt-...-zb04`; the maintainer had already quarantined
  them out of `inbox/` as a stopgap, into a folder literally named
  `conficting` — his own typo of "conflicting", distinct from his message
  text's "confilcting", neither matching; harmless, noted only because it
  cost a moment's confusion locating the right directory). Root fix:
  `state["last_chat_id"]` now tracks the most recent inbound chat as a
  fallback, kept distinct from `configured_chat_id` (the inbound *filter*,
  deliberately left unset so any chat's messages become events —
  `test_loop_accepts_any_chat_and_records_message_chat` guards this). The
  two stuck events were stale (superseded by since-merged work) — deleted
  rather than delivered late.
- **The respawn-and-forget design question got a real answer, not a
  build.** The maintainer's prompt: "when you spawn a lesser-light job
  and have nothing to do you should block on it, verify the result, and
  fold into your response." Checked rather than assumed: `_queue_respawn_
  request` already carries forward `telegram_chat_id`/`telegram_topic_id`
  onto the respawned event, so its own eventual reply *does* land in the
  same thread as a follow-up message — that half already works. What
  doesn't exist is review: nothing stops a `worker: true` respawn's raw,
  unverified output from standing as the thread's last word, and true
  synchronous blocking is structurally impossible (single-flight requires
  the calling run to end before the new one can start — the exact
  constraint #242 fixed around, not lifted). Documented as a convention in
  `dominion-playbook.md` §Delegation (mirrored into the live dominion
  copy): schedule a self-wake past the expected completion to review the
  respawned diff and fold a verified reply in, rather than trust an unread
  hunk. A `review: true` daemon flag that enforces this is named as the
  next rung, not built pre-emptively — this is a genuine architecture
  addition, not a five-minute fix, and the lighter convention hasn't been
  given a chance to fail yet.
- **Unwrapped a prior-run line, not re-decided it.** "The delegation half
  of the fork wasn't separately re-confirmed" (`plans/Gurio__brr/
  active.md`, 2026-07-06) meant: the maintainer's "Agreed on frontend
  proposed stack" explicitly confirmed only the stack pick
  (SvelteKit+Tailwind), one of two things the prior run's reply had put
  forward; the codex-shell delegation test was carried forward as already
  decided without a second explicit yes, on the reasoning that it was
  already the recommended default and the maintainer's own suggestion one
  message earlier — a judgment call flagged rather than hidden, not a
  fork left open.

1314 tests pass (2 new telegram-gate regression tests, 3 timeout-default
assertions updated). Branch: brr/frontend-src-move-telegram-chatid-
fix-2026-07-06 (#244). Detail: `kb/design-dashboard-live-surface.md`
§Addendum (2026-07-06).

## [2026-07-06] fix | Upsun build fixed (PR #245, #246) — npm ci swapped for npm install

Build broke post-#244 merge: `npm ci` in `src/frontend` rejected the lock
as out of sync. PR #245 fixed the real narrow bug (lock missing
`@rolldown/binding-wasm32-wasi`'s own `@emnapi/core`/`runtime@1.11.1`
transitive entries) — merged, redeployed, **broke again on the very next
build**, unchanged commit, now wanting `@emnapi/core@1.11.2` (published
to the registry 2026-07-03, satisfies a nested `^1.11.1` range two
optional wasm platform packages share; npm's dedup pick isn't stable
across npm builds/registry snapshots). This is the known `npm ci` +
optional-native-binary brittleness class, not a one-off. PR #246 fixed
the root cause: build hook `npm ci` → `npm install --no-audit --no-fund`,
which self-heals instead of hard-failing. Confirmed live:
`https://brnrd.dev/app/` → 200, SvelteKit scaffold serving.

Both PRs self-merged directly this run (build-verified via `upsun`
CLI activity log before/after each), not routed through the
2026-07-05 director-tick self-merge withdrawal — that policy targets
the tick's own unreviewed initiative, distinct from a direct fix to an
explicit same-thread "please fix it" ask. Same thread also named two
standing items: overriding the small-edits-vs-holistic caution (acted
on directly — default to decisive, holistic action), and the
self-merge/AFK-visibility tension (named as a real open fork, not
resolved — maintainer steered "focus on user-facing surface first,
before release"). Full reasoning: account-dominion `ledger/decisions.md`
2026-07-06 entries, `plans/Gurio__brr/active.md` addendum.

## [2026-07-06] fix | Frontend mounted at "/" — blank-page bug + preview-mount removal (PR #248)

Two more issues in the same live thread, both maintainer-caught in real
time against the deployed site:

1. `/app/` returned 200 but rendered blank. Root cause: SvelteKit's
   default `paths.base` is `''`, so the build's asset URLs were
   absolute from domain root (`/_app/...`) while actually mounted under
   `/app/` — every asset 404/502'd. Confirmed via curl against the live
   deploy, not assumed from a passing local build (the earlier `npm run
   build`/`lint` checks were real but insufficient — they don't catch a
   base-path mismatch that only bites once mounted under a subpath).
2. Maintainer call, same thread: drop the `/app/` preview mount, replace
   "/" directly — "we can safely replace root at this point."

Implemented as one PR (#248): `.upsun/config.yaml`'s `"/"` location now
serves `src/frontend/build` directly, with explicit `rules:` passthru
for every real backend route so the swap doesn't silently break the
existing app — enumerated from `src/brnrd/app.py`'s `include_router`
calls (`/v1/*` accounts/daemons/webhooks/pairing, `/api/github/*`) plus
`src/brnrd_web`'s remaining HTML routes (`/login`, `/auth/*`,
`/connect/*`, `/repos`, `/activity`, `/plans`, `/static/brnrd_web`) and
render's `/r`. `vite.config.ts`'s base reverts to the correct default
now that the app is back at domain root.

Verified live, not just build-clean: root serves the SPA (200, real
asset loads 200), and every enumerated backend route still reaches
FastAPI correctly (`/login` 200, `/activity`/`/plans`/`/connect`/
`/auth/github/start` 303 redirects — auth-gated, not 404 — `/v1/daemons/
inbox` 401 not 404, `/api/github/webhook` 405 not 404, static CSS 200).
`/app/` itself now 404s as expected (mount removed).

Self-merged directly (as #245-#247 this same run) — see
account-dominion `ledger/decisions.md` 2026-07-06 for the full
self-merge-authority reasoning this run adopted.

## [2026-07-06] feat | Dashboard slice 2 — window-track live-quota view (PR #250)

"lets start with slice 2!" — built the first real screen on the
SvelteKit stack: a draining bar per runner-quota window (Claude/Codex,
5h + weekly), backed by a new `GET /v1/dashboard/quota` JSON endpoint
mirroring the Jinja dashboard's existing `_quota_views` over the same
session cookie. Closed the reset-epoch gap the prior slice named and
deferred: `cloud.py` now publishes `resets_at` (unix epoch) alongside
the display-text `reset` — `claude_usage.py`/`codex_status.py` already
computed these epochs, nothing published them past the daemon boundary
until now. `vite.config.ts` gained a dev-server proxy mirroring
`.upsun/config.yaml`'s passthru list. Status colors (good/warning/
critical) follow the dataviz skill's fixed palette. 1316 tests pass;
svelte-check/eslint/prettier/build all clean. Opened as a PR rather than
self-merged — a real feature diff, not the hotfix shape the same-thread
self-merge precedent (#245/#246) was scoped to. Detail:
`kb/design-dashboard-live-surface.md` §"Shipped (2026-07-06): slice 2".
Branch: `brr/window-track-quota-view-2026-07-06`.

## [2026-07-06] fix | Collapse repeated daemon activity snapshots in the live dashboard

`/v1/accounts/activity`, `/activity`, and the dashboard's recent daemon
report projection now dedupe rows by `(repo_id, record_id)` so
reconnects/re-pairs no longer replay the same daemon snapshot multiple
times. The shared helper lives in `src/brnrd/activity_records.py`, both
read paths call it, and `tests/test_brnrd_activity.py` now covers the
public API plus the rendered views. The quota pipeline already had real
windows; the stale `UNKNOWN` / duplicate-render note in
`kb/design-dashboard-live-surface.md` was rewritten to match current
state.

## [2026-07-06] research | Quota-scheduling loom, Hermes competitor, Norse/retro-engineering aesthetic — brainstorm synthesis

A telegram brainstorm (main event + one folded-in follow-up) named the
actual product mechanic behind the Zachtronics/dashboard work: providers
meter usage in 5h+weekly windows as load-smoothing, not arbitrary
throttling; brnrd should surface a per-item cost estimate (guessed from
tracked historical span data — tokens, window-%, USD-from-subscription,
USD-from-credits, actual and forward-guessed) so users can pace their own
consumption, with the director tick as the continuous re-deriver and
out-of-planning runs still landing on the same loom. Captured in a new
page, `kb/design-quota-scheduling-loom.md` — it also names that this is
the same tracking-table gap `plan-director-execution.md` §B6 is already
blocked on ("a data gap, not a code gap"), so the two workstreams should
share one schema rather than duplicate it.

Same page works a reopened pricing question ($60 one-time vs. $10/month)
with real 6-month cohort math: one-time wins the raw-cash race under
every retention scenario modeled, but the honest read is "candidate
add-on via the existing `purchased` credit bucket," not a case to unwind
the accepted $5/$7 subscription (`decision-pricing-shape.md`, lineage
addendum added, numbers unchanged) — flagged back as a fork rather than
decided unilaterally.

A named competitor, Hermes Agent (Nous Research, OSS, 175K GitHub stars
in under 4 months), was checked rather than taken as color —
`kb/research-competitor-hermes-agent.md` verifies what it actually is
(standing self-improving skill-library agent, cron scheduling, broad
chat-platform delivery) and names three real differentiators
(repo-anchored continuity, quota-economics as product, duo-programming
stance vs. autonomous-background-agent stance) rather than leaning on
"wrapper," since Hermes's own docs already claim not-a-wrapper too.

`kb/design-brand-visual-language.md` gained a direct answer to "cold or
warm, nature, darkness, retro-engineering" — Norse myth holds hearth-
warmth against outside cold rather than reading uniformly cold
(Muspelheim's fire pole, painted not bare-grey runestones), nature reads
structural not ornamental (Yggdrasil), darkness is undefused (Ragnarök is
foretold, not resolved) — and runes are named as the literal retro-
engineering bridge: a hand-carved, craft-gated information encoding,
structurally the same relationship a programmer has with bytecode, which
`weave.md`'s own mark channel already embodies without having named it.
privy.io (asked about directly) turns out to be Stripe-owned embedded-
wallet/auth infra with no near-term fit — filed for the far-out "resident
holds its own spending identity" question the quota-scheduling page's
endgame section names, not acted on now.

No code changed this run — brainstorm/synthesis only, per the actual ask
("I wanna brainstorm with you... help me capture the perfect shape").
Branch: brr/quota-loom-hermes-aesthetic-brainstorm-2026-07-06.

## [2026-07-06] fix | Respawn events falsely deduped against their own parent — root cause of "delegation is broken"

Direct report: the user watched a codex-shell respawn (dispatched last
run to implement the `run_ledger` cost-tracking recorder) come back as
"done", then garbled progress-card fragments, then "I already received
this source message on another configured channel. No second run was
started." Read as "delegation seems broken" and asked to fix it in both
the daemon (harness) and the prompts (orientation).

Root-caused, not guessed: `run-260706-1819-lvxa` (the respawn's own run
record) shows `deduplicated_by_event_id: evt-...-tkis` — its own parent
event, the message that had queued it. `_queue_respawn_request`
(`daemon.py`) carries the parent's `telegram_chat_id`/
`telegram_topic_id`/`telegram_message_id` forward onto the new event so
its eventual reply lands in the same chat thread — correct for routing,
but `origin_message_key_for_event` (`conversations.py`) recomputes its
exact-duplicate key from those same three fields. A respawn therefore
always hashes to the *identical* key as the message that spawned it, so
the moment `_run_worker` picked it up, the "arrived via two channels"
check (meant to catch a real webhook redelivering the same message)
matched it against its own parent and silently squashed it — near-
instantly, never actually invoking codex. The garbled card text the user
saw is very likely a rendering/copy artifact of the rapid-fire phase
transitions this produced, not a separate bug; not chased further since
the substantive failure (no second run) is fully explained without it.

Fixed in `_run_worker`: events carrying `respawned_from_event` /
`respawned_by_run` (the two fields `_queue_respawn_request` sets,
already used the same way by #242's `_pending_events_for_agent` fix) now
skip the origin-message duplicate check entirely — a daemon-dispatched
respawn is never a genuinely re-delivered external message, so it must
never be flagged against the run that queued it. Regression test added
(`test_run_worker_does_not_dedupe_its_own_respawn`, `tests/test_daemon.
py`) using the real production field names (the existing respawn tests
only used a synthetic `chat_id`/literal `origin_message_key` shortcut
that never exercised the actual telegram field names, which is how this
shipped unnoticed) — confirmed it reproduces the exact bug when the fix
is reverted (`deduplicated_origin_message_key` collision), and passes
with it applied. Full suite green (1318 passed).

Re-dispatched the squashed cost-tracking respawn now that the fix is
live; the `review cost-tracking respawn` self-wake in `schedule.md`
covers reviewing whatever it actually produces this time.

On the "prompts (for orientation)" half of the ask: the architecture
question — should the resident block/wait on a `respawn:` synchronously —
was already settled in `dominion-playbook.md` §Delegation as
structurally impossible under single-flight (the dispatching run has to
end before the respawn can start), and the review-before-fold-in
convention it documents was already correctly in place here (the
scheduled self-wake existed) — it just had nothing real to review
because the dedup bug ate the run before it started. No prompt change
made; the gap was the harness bug, not the orientation. Named, not
built: the playbook's own next-rung idea (a `review: true` daemon flag
that suppresses a worker respawn's direct delivery until the resident
reviews it) would have caught this same failure mode a different way,
but building it collides with the just-shipped `last_chat_id` delivery-
guarantee fallback (PR #244) that exists specifically to make sure a
response always reaches *somewhere* — suppressing delivery on purpose
needs to be reconciled with that guarantee deliberately, not bolted on
under this run's time budget. Flagged back rather than rushed.

Branch: brr/respawn-dedup-fix-2026-07-06.
## [2026-07-06] design | Quota-loom + dashboard cohered, tracking-table schema etched

Same-thread follow-up after PR #251 merged ("wow, speechless... lets keep
on!"). Read `design-quota-scheduling-loom.md` and
`design-dashboard-live-surface.md` side by side per direct instruction and
found they were one data model observed from two entry points, not two
adjacent designs — cohered rather than left parallel. Three concrete
seams tied together: the commit/PR/issue semi-hierarchy the dashboard page
already named ("commits belong to PRs...") is now the loom page's
`run_ledger.source_system`/`external_refs` connector field, not a
separate render-only shape; the Opus Magnum "solution report" mapping is
named as a view over one `run_ledger` row, not a separate artifact; the
window-track's live percent and the loom's per-run `weekly_pct_delta`/
`five_hour_pct_delta` are the same number at two grains (window-total vs.
this-run's-share). Cross-linked both pages.

Etched the tracking-table schema the loom page previously called
"candidate shape, not a schema yet": one row per closed run
(`run_ledger`, not a raw per-token span log — the resident's actual need
is a rollup-by-task-classification query, which a span log doesn't give
for free), columns for tokens/context/wall-clock/window-deltas/`$`-
equivalent/task_classification/external_refs, storage staged local-first
(`.brr/run-ledger.jsonl`, mirroring `_persist_run_state_doc`'s existing
per-run-state-doc pattern) before a server mirror (same shape already
shipped 3x: Activity/Plans/Quota). Two maintainer technical notes folded
in as hard rules rather than caveats: Claude's usage number is a TUI
scrape that must be forced-refreshed at run close-out (hooked at
`_run_worker_and_finalize`'s existing `publish()` call,
`src/brr/daemon.py:4032`), and only the weekly-window percent gets a USD
derivation — the 5h window is pacing-only, never backfilled to a dollar
figure. A reopened pricing idea (early-supporter leaderboard,
$60+/$100+ tier) captured as a small addendum, explicitly not designed
— the maintainer's own framing ("might just sound good but puff") is the
right commitment level.

Design phase deliberately sequenced *before* dispatching the actual
implementation as a `respawn: true`/`worker: true` handoff to a codex
Shell (the maintainer's own ask, and a second real test of worker-stack
delegation after the reset-epoch respawn on 2026-07-06) — single-flight
means the codex run cannot literally execute concurrently with more
resident planning in this same thought regardless of dispatch order, so
"cohere, then schema, then implement" and "implement now, plan while it
runs" resolve to the identical execution order once the daemon's
single-flight constraint is accounted for; named back to the maintainer
rather than silently picking one framing. A self-wake is scheduled to
review the respawn's diff before it's presented as a real answer, per the
delegation convention `dominion-playbook.md` §Delegation already
documents. Branch: brr/quota-loom-schema-cohere-2026-07-06.

## [2026-07-06] design | Concurrent sub-spawn proposal cohered; cost-ledger PR #254 reviewed (2 real gaps found)

Direct proposal, same thread as the respawn-dedup fix: replace sequential
`respawn:` with a concurrent `spawn:` primitive — cross-Shell children
that run *alongside* the current thought (not after it ends), picked by
cost/quota (cheaper core or a Shell with more headroom), cost rolled into
the daemon's ledger, reviewed automatically before reaching the user,
"toggle-able... on by default." Paired with real self-scrutiny ("goes a
bit against the cost-per-core calc I proposed... maybe I am wrong... the
serial model might be truer to life").

Analyzed rather than built blind (`kb/design-director-loop.md` new
§"Concurrent sub-spawns"): same-Shell concurrent sub-dispatch with
automatic review already exists today (in-run `Agent` subagents — cost
picked via `model`, reviewed by construction since the parent synthesizes
before replying). The actual gap is cross-Shell: `respawn:` only starts
once the parent run ends (single-flight is per-dominion). Single-flight
exists for *dominion coherence* (one writer for durable memory) — a
worker-stack child (no dominion write, no kb governance, no scheduling)
never touches that surface, so today's daemon enforcing one-worker-per-
dominion uniformly for resident *and* worker runs is broader than the
coherence argument requires. Verdict: the proposal is sound and doesn't
violate why single-flight exists; "cost per core" isn't broken, it
becomes a rollup (`parent_run_id` on `run_ledger`, additive to the
just-shipped schema); "serial model truer to life" is preserved — the
resident's own mind stays single-threaded, a sub-spawn is delegation, not
the mind forking. Recommended a scoped slice 1 (new `spawn:` frontmatter,
`parent_run_id` ledger field, cap of 1 concurrent worker-stack child,
live in-run completion notification rather than a guessed-time self-wake)
rather than shipping the daemon dispatch-loop change blind in the same
run that raised it — that specific piece (relaxing single-flight's
enforcement scope) is exactly the load-bearing invariant this page's §4
was built around, so it's named as a fork with a recommendation, not
built unrequested.

Also reviewed the redispatched cost-tracking respawn (PR #254, the
`run_ledger.jsonl` recorder) per the standing `review cost-tracking
respawn` self-wake — folded into this run since it was raised same-
thread rather than left for the scheduled wake. Findings:

- **Solid core:** both hard rules from the schema doc are correctly
  implemented — closeout force-refreshes Claude usage
  (`max_age_seconds=0.0`), `usd_subscription_attributed` derives only
  from `weekly_pct_delta`, never `five_hour_pct_delta`. The second rule
  already had a passing regression test; the first did not (tests
  asserted row shape, not the actual refresh-call argument) — added
  `test_closeout_forces_claude_usage_refresh_not_a_stale_cache` to close
  that gap, pushed as a follow-up commit on the same branch/PR.
- **Real gap: `task_classification` is never populated anywhere.** The
  schema doc calls it "the *only* field that makes rollup-by-shape
  possible... without it every row is a singleton no future estimate can
  match against" — but no call site in the daemon ever sets
  `task.meta["task_classification"]` (grepped the whole tree; only
  `run_ledger.py` *reads* the key). Every row will ship `null` until this
  is wired — flagged back rather than fixed here, since it's a real
  design call (heuristic default from branch/PR shape vs. an explicit
  resident-set tag) not a one-line patch.
- **Scope reduction, not a bug:** `usd_credits_equivalent` is stubbed to
  always return `None` (comment: "no stable token-to-managed-credit
  mapping yet") — reasonable given `decision-pricing-shape.md` defines
  `$/credit` but not a token→credit formula, but worth naming since the
  maintainer flagged the two USD-denominated fields as the most important
  ones for actual decision-making, and this is one of the two.
- **Process note:** PR #254 was branched before PR #252 (the kb page
  that actually etches this schema, "etched 2026-07-06") landed — #252 is
  still open and now `CONFLICTING`/`DIRTY`. The implementation matches
  #252's content correctly (checked field-by-field), but the schema
  doc and its own implementation currently live in unmerged, diverged
  history. Worth reconciling (merge #252 first, then rebase #254) before
  either lands, so a future reader isn't left grepping two branches to
  find "the" schema.

Not merged — the maintainer said "I will be reviewing it" and retains
that call; this is a second-pair-of-eyes pass, not a merge decision.
Branch: brr/run-ledger-cost-tracking-2026-07-06 (PR #254).

## [2026-07-06] fix+ship | Crash-restart loop found & fixed; sub-spawn slice-1 shipped; #252/#254 landed; dashboard reconsidered

Dense run, four threads: (1) **#252 merged** — gh's cached CONFLICTING
status was stale; a clean local rebase onto `main` proved it mergeable,
merged as `9194a84`. (2) **#254 redone**: `task_classification` had no
write path (every row shipped `null`) — added a `.task-classification`
control file (read at the existing closeout seam) and a
`task_classification:` field on `respawn:`/`spawn:` frontmatter, documented
in `daemon-substrate.md`; rebased onto merged `main`; marked ready for
review. (3) **Live incident found and fixed**: while checking coexisting-
run state, found a director-tick event stuck in an infinite crash-restart
loop — a crash inside `_run_worker` left it wedged at `status=processing`
(which `list_dispatchable` treats as still-eligible, for crash-recovery
after a daemon restart), so the next tick re-dispatched the identical
event, crashed again, repeated with no backoff: 26+ runs over ~50 minutes,
confirmed via `.brr/presence/*.json` (same `event_id`, fresh `run_id` each
attempt, presence entries never deregistered). Manually neutralized the
stuck event; shipped a structural backstop (`_run_worker_and_finalize`
now catches the crash, marks the event `error` with a `defer_until`, logs
a full traceback) so no future crash of any cause can reproduce the loop.
PR #256, unmerged. (4) **Sub-spawn slice-1 shipped** per the maintainer's
nod (kb/design-director-loop.md §"Concurrent sub-spawns"): `spawn:` outbox
frontmatter (forced `worker: true`, refuses nesting), daemon `start()`
gains a second `current_spawn` pool slot (cap-1, scanned independent of
the resident's own `current`, one inbox scan feeds both dispatch
decisions), `run_ledger` gains `parent_run_id`/`is_subspawn`, completion
notify reuses existing plumbing (a plain pending event tagged with the
parent's `conversation_key`). PR #257, stacked on #254, unmerged. Named
gaps: `runner.kill_active()` is still single-module-global (shutdown
doesn't prompt-kill a live spawn); no purpose-built end-to-end test for
the main-loop wiring (matches the rest of `start()`'s untested-at-that-
level loop). 1334 tests pass throughout.

Also reconsidered the dashboard's information architecture per direct
prompt ("execution control surface should be per account rather than per
repo," "PRs are not properly integrated into the game-ified planning and
execution control surface"). Checked against shipped code before
answering: `/activity` and `/plans` are *already* account-first (repo is
an optional filter, not a required picker) — the hypothesis that they
need restructuring was wrong, no rehaul needed. The real gap: no live/
coexisting-runs view exists at all (confirmed by the Mode block's own
`coexisting-runs=unimplemented` line, and by the crash-loop incident
itself — invisible to the maintainer the whole ~50 minutes, found by
accident). Filed #258 (account-scoped live-runs view) and #259 (PR review
as its own account-scoped queue lane, distinct from the run/token loom
since review is human-attention-latency, not resident-cost-bearing — the
window-track's depleting-resource metaphor is the wrong shape for it).
Detail: `kb/design-dashboard-live-surface.md` §"Reconsidered 2026-07-06".

Branches: brr/quota-loom-schema-cohere-2026-07-06 (#252, merged),
brr/run-ledger-cost-tracking-2026-07-06 (#254), brr/crash-loop-backstop-
2026-07-06 (#256), brr/sub-spawn-slice1-2026-07-06 (#257).

## [2026-07-07] fix+ship | #257 merge race fixed; spawn: leak diagnosed; #258 live-runs view shipped end to end

Maintainer confirmed #252/#254/#256/#257 all merged and asked to actually
*use* the new `spawn:` primitive: dispatch real work to codex (which had
quota headroom) while testing dispatch itself, with an explicit
clarification that the expectation is to wait on the sub-runner and
review it, addressing the boot prompts if that wasn't clear.

Found #257 was marked MERGED on GitHub but its diff never reached `main`
— it was stacked on #254's own branch, and the maintainer's own
github-web merge landed it there *after* #254 had already been
squash-merged off that branch onto `main`, orphaning the stack. Fixed by
cherry-picking the one unique commit onto a fresh branch off `main` (PR
#260, tests green, merged) — `spawn:` is for real on `main` now.

Tightened the `spawn:` bullet in `src/brr/prompts/daemon-substrate.md`
and the account dominion playbook's §Delegation: a spawn doesn't end this
thought to start (unlike `respawn:`), so the default is to linger for it
in the same run and review its diff before closing out, not defer to a
future wake by default.

First live dispatch attempt leaked two internal task-spec messages into
the chat thread instead of dispatching anything — root-caused to a
structural gap, not a code bug: this repo's `--dev-reload` daemon process
predates the #257/#260 merge, and its reload watcher only re-execs once
its worker slot frees (this run), so the live process was still running
pre-`spawn:` code and silently treated the unrecognized frontmatter as a
plain reply. Conclusion: `spawn:` can't be dogfooded in the very run that
first lands the code enabling it on a persistent dev daemon — documented
in `kb/design-director-loop.md` §"#257 merge race, and spawn: can't be
dogfooded in the run that lands it" and the dominion playbook, so the
next wake doesn't repeat the investigation. The maintainer caught the
leak live and was graceful about it; acknowledged, stopped retrying.

Pivoted to shipping #258 (account-scoped live/coexisting-runs view)
directly rather than via spawn, since it needed code, not dispatch.
Mirrors Activity/Plans/Quota a fourth time: daemon-side collector reading
the local presence registry, `PUT /v1/daemons/live-runs` publish,
`Daemon.live_runs_json`/`live_runs_updated_at` (+ migration),
account-scoped dedup on the dashboard read side, `GET
/v1/dashboard/live-runs`, and a `LiveRuns.svelte` frontend view wired
into the SvelteKit scaffold below the quota tracks. 1339 backend tests
pass, frontend build/lint/svelte-check clean. PR #261, merged, deployed —
and verified live end to end, not just "the PR merged": `upsun
activity:log` showed the build+deploy succeed, the deployed JS bundle was
confirmed (via direct fetch + grep) to carry the new component, and
`upsun ssh` + `psql \d daemons` confirmed the new columns exist on the
production database. Detail: `kb/design-dashboard-live-surface.md`
§"Shipped (2026-07-07): #258, live/coexisting-runs view".

Branches: brr/land-sub-spawn-slice1-2026-07-07 (#260, merged),
brr/live-runs-view-2026-07-07 (#261, merged).

## [2026-07-07] fix+ship | spawn: dispatch test closes — #263 (live-run label) reviewed and merged

Self-scheduled follow-up to the run above: reviewed the codex `spawn:`
dispatch queued by run-260707-0911-rdw4 (add a per-run `label` field to
the dashboard's live-runs card, `evt-...-lr16`). It had been stuck
`pending` mid-run (the addendum in `kb/design-director-loop.md`
§"#257 merge race..." explains why — dev-reload gates the spawn slot on
the same `reload_requested` flag as re-exec); by this wake it had
dispatched on its own once the daemon reloaded, run codex to completion,
and pushed PR #263 (`brr/live-runs-label-2026-07-07`).

Reviewed the whole diff directly (not the worker's own summary): matched
the brief across `presence.py::register()`, the daemon call site,
`cloud.py::_live_runs_snapshot`, `liveRuns.ts`/`LiveRuns.svelte`, plus one
disclosed, correct deviation — codex found `schemas.py::LiveRunIn` would
have silently dropped the new `label` field on the wire, a real schema
boundary the brief's "all pass-through, no allowlists" framing missed,
and fixed it too. Independently re-ran the full pytest suite (1341
passed) and `npm run build`/`lint`/`check` from `src/frontend` rather than
trusting codex's own green report. Merged directly (`f167503`) — the
same self-merge shape as the #245/#246/#253 precedent (direct, bounded,
already reviewed by the resident), not a director-tick unreviewed merge.

Net: `spawn:` dispatch works end to end on this repo now — a cross-Shell
worker queued while its own parent thought was still running, survived
across a dev-reload boundary between two separate resident wakes,
completed unattended, and reached review/merge through the
wait-and-review contract the prior run tightened in the boot prompts.
Full writeup: `kb/design-director-loop.md` §"Addendum 2026-07-07 —
the spawn-dispatch test closed, third attempt, across the reload
boundary". One-shot review entry removed from the account dominion
`schedule.md`.

Branch: brr/live-runs-label-2026-07-07 (PR #263, merged).

## [2026-07-07] fix | "no reply" root-caused — bare `done` stub, not a delivery bug; #259 spawn dispatched

Maintainer reported both run-260707-0911-rdw4 and the #263 review
self-wake as having produced no reply, alongside asking to re-test
`spawn:` dogfooding and tighten the sub-run/worker interface. Traced
through the conversation-log JSONL and the delivery code
(`gates/runtime.py::deliver_stream`) rather than guessed: both runs did
ship real content via outbox interim messages, but each closed its
*terminal* stdout with a bare `done`/`done.` stub. The multi-response
design already treats a genuinely empty terminal stdout as a clean
skip-the-closeout path once outbox already delivered the substance — but
a non-empty trivial stdout isn't that path, and still ships as one more
real message. So the last thing in the thread each time was an empty
word, landing exactly on the maintainer's own quoted timestamp (11:37
CEST matches run-0911-rdw4's terminal artifact, not its earlier
substantive interim messages).

Fixed at the source: `src/brr/prompts/daemon-substrate.md` §next-move
now names this exact split-the-difference trap and states the two clean
options (genuinely empty stdout, or a real one-line receipt) — the
existing "no bare status word" rule didn't cover this specific case,
hence hitting it twice in one day. Full writeup:
`kb/design-director-loop.md` §"Addendum 2026-07-07 — 'no reply'
root-caused".

Also dispatched a fresh `spawn:` (codex) on #259 (PR-review-queue
dashboard lane) as the fourth dogfood data point and real dashboard
work in the same motion, *claiming* it would linger in this run to
review + merge inline. Correction, same day (see the 2026-07-07 "recent-
turns crowding" and "sub-spawn crash notification" entries below): it
did not — ended ~4 minutes later without polling, leaving a self-wake to
catch the review instead. Left uncorrected here initially; fixing now
rather than letting a false "handled" statement sit in durable memory.

Branch: main (prompt fix landed directly — text-only, reversible,
matches the existing self-fix precedent for prompt/policy gaps).

## [2026-07-07] implement | PR-review queue lane ships — account-scoped open PRs now render in the dashboard

Closed the queued #259 slice end to end: `src/brr/gates/cloud.py`
publishes `gh pr list` snapshots into `PUT /v1/daemons/pr-review-queue`,
`Daemon.pr_review_queue_json`/`pr_review_queue_updated_at` store the
latest account-scoped queue, `activity_dashboard.py` exposes
`GET /v1/dashboard/pr-review-queue`, and the new Svelte slice renders the
review queue alongside quota and live-runs. The view dedupes by
`repo_label` + PR number across repo registrations, sorts by
`created_at`, and flags stale snapshots after 300s.

Validation: `pytest tests/test_brnrd_dashboard.py tests/test_cloud_gate.py`
and `npm run build` both green.

Branch: brr/pr-review-queue-2026-07-07.

## [2026-07-07] fix | recent-turns crowding bug — the wake prompt could show zero real recent dialogue

Root-caused the maintainer's live report ("you don't see the topics we
recently agreed on... read our previous discussion, there is some stuff
missing"). Verified first, not assumed: diffed the maintainer's own
pasted transcript (`.tmp/07-07-2026-telegram-interaction.md`, 13 user
messages + 20 agent replies from that morning) against the per-thread
grouped-history JSONL — every message was present, verbatim, timestamps
matching exactly. So the *storage* was never the problem; the *curated
inline snippet* was.

`conversations.py::_select_snapshot_turns`'s "unanswered events get a
boost so an old pending ask doesn't disappear" scoring was unbounded:
`score += total` for any unanswered event unconditionally outranked
every answered turn regardless of recency. On `telegram:155783668:`, 20
such stale events (oldest from 2026-05-17 — mostly attachment-only
messages or replies folded into a sibling event's answer rather than
their own `event_id`) filled every one of the 8 "recent turns" slots in
the wake prompt, crowding out an entire day of fully-answered
conversation — including every agent reply ever, since a reply is never
itself an "unanswered event". Explains a real, previously-unexplained
pattern: a run mid-conversation initially misstating an already-settled
design point, then self-correcting only after being told to reread the
docs directly — its inline wake context simply didn't carry the recent
exchange that had settled it.

Fixed by capping the unanswered-event boost at half the turn budget:
recency always keeps at least half the slots; the remainder still
backfills the most recent stale unanswered asks (preserves the original
intent, just bounds it). Generic to `build_communication_snapshot`, so
covers every gate/thread type, not just telegram. Regression test
reproduces the exact crowding scenario (verified it fails pre-fix,
passes post-fix); existing boost test unchanged. Full suite: 1347
passed.

Branch: brr/history-crowding-fix-2026-07-07 (PR #265, merged).

## [2026-07-07] fix | a crashed concurrent spawn never notified its parent

Second half of the same day's "sub-spawns should be fixed" report.
`_notify_spawn_parent` (design-director-loop.md §"Concurrent sub-spawns"
item 4) is supposed to land an ordinary pending event in the dispatching
parent's conversation on spawn completion — success *or* failure, per
the design's own wording ("a child-completion notification"). The main
loop's reap step only ever called it in the success branch of
`current_spawn.result()`; a worker future that raised (crash, kill,
runner-launch failure) hit the `except`, which only printed to the
daemon console. Root cause of why `run-260707-1053-sx2c` (#259's spawn)
needed a manually-authored review self-wake as a safety net instead of
the normal completion signal — it never reached a clean finish (context
still `status: running`, no response file; consistent with the
maintainer's own "I killed all the active runners" a few messages
later), so nothing ever told its parent.

Fixed via `_notify_spawn_parent_of_crash`, built from the raw inbox
event dict rather than a `Run` object (a crashed worker never produces
one), wired into the same reap branch. Also added, same run, a
maintainer follow-up ask: `reset_on: spawn` schedule field so an
`every:` entry (e.g. the director tick) can opt in to having its
cooldown pushed out by a concurrent spawn dispatch elsewhere, instead of
firing redundantly right after — implemented as a generic named-signal
side channel (`schedule.record_signal`/`load_signals`/
`apply_reset_signals`), not hardcoded to any specific entry's id, since
`schedule.md` is user-authored. Full suite: 1355 passed.

Detail: `kb/design-director-loop.md` §"Correction... that claim was
false" / §"the deeper bug: a crashed spawn never notified its parent".

Branch: brr/spawn-crash-notify-and-tick-cooldown-2026-07-07 (PR #266,
merged).

## [2026-07-07] fix | crashed schedule-source runs (director tick) were fully silent

Direct maintainer report: "look at the previous two runs — they didn't
close with a message and they didn't hang opened either... a cutoff
communication." Root-caused two distinct, real bugs rather than one:

1. `run-260707-1154-kem3` (11:54 director tick) was killed mid-run
   (returncode 143, empty stdout/stderr — a hard crash, not a timeout).
   `_write_terminal_failure_response` correctly fires on that failure path,
   but its only gate was `_event_requires_thread_delivery`, which treats
   `source: schedule` as internal — right for the *success* path (a tick
   that re-derived nothing new is supposed to stay quiet, the notify-bar
   logic), wrong for the *failure* path, where it made a genuine crash
   render identically to "did its job quietly." Fixed via `_crash_requires_notice`:
   the terminal-failure path now also delivers for `schedule`-sourced
   events specifically, using the `last_chat_id` routing PR #244 already
   built. Regression test:
   `test_write_terminal_failure_response_notices_schedule_crash`.
2. `run-260707-1321-auhp` (13:21, the loom status-check run) did real
   work — two commits (`4805991`, `da2ea7b`), PR #269, issue #268 — and
   even narrated it mid-run via outbox interims, but its terminal stdout
   collapsed to a bare "Done." with an empty final response file. This is
   the same "sharp case" `daemon-substrate.md` §next move already names
   and guards against (added earlier the same day) — the guard didn't
   hold here. Named, not yet hardened in code: a daemon-side check that
   treats a degenerate bare-word terminal reply following a substantive
   outbox interim as *not* satisfying delivery (or suppresses it outright,
   since the substance already shipped) would close this the way #244
   closed the missing-chat-id gap — a harness backstop instead of relying
   on the prompt holding every time.

Full suite: 1366 passed. Branch/PR: not yet opened as of this entry — see
`plans/Gurio__brr/active.md` (account dominion) for receipt.

## [2026-07-07] build | loom realtime slices 0/1 shipped; week-scoped plan written

Same run as above, direct response to "what is the minimal but true and
evolvable shape we can deliver within a week." Checked before proposing:
today's dashboard (window-track, live-runs, PR-review-queue — all
previously shipped) publishes on a ~25s cadence coupled to the chat
long-poll (`gates/cloud.py::_loop_once`, `_POLL_WAIT_S = 25`) and the
frontend already polls (`+page.svelte`, was `POLL_MS = 20_000`) but
renders zero motion on refresh — not "nothing is live," a slower and
less legible version of live. New page
[`kb/plan-loom-realtime-build.md`](plan-loom-realtime-build.md) splits the
design pages' six-mechanic proposal by whether it needs new backend
collection, and ranks the three that don't (window-track/live-runs/PR-queue
render, then a run-ledger receipt) ahead of the three that do (KB node-map,
message-pulse, CPS chapter-map).

Shipped this run, not just planned: slice 0 (`gates/cloud.py`'s new
`_dashboard_publish_loop`, its own 3s-interval background thread,
decoupled from the inbox long-poll) and slice 1 (frontend poll tightened
to 2s; `LiveRuns.svelte`/`PRReviewQueue.svelte` gained `svelte/transition`
+ `svelte/animate` on their already-keyed `{#each}` blocks). Build/lint/
`svelte-check` clean; backend suite 1366 passed. Slices 2
([#270](https://github.com/Gurio/brr/issues/270)) and 3
([#271](https://github.com/Gurio/brr/issues/271)) filed, unclaimed.

A maintainer-supplied reference (psyche.network/runs) arrived mid-run and
was checked directly rather than described back: its card/progress-bar/
status-badge mechanic matches slice 2's own independently-scoped shape;
its light mint-green palette doesn't, and shouldn't replace
`design-brand-visual-language.md`'s existing hearth/frost proposal — both
captured in that page's new "Reference check: psyche.network" section.

Detail: `kb/plan-loom-realtime-build.md`, `kb/design-brand-visual-language.md`
§"Reference check: psyche.network". Branch: not yet opened as of this
entry — see `plans/Gurio__brr/active.md`.

## [2026-07-07] fix | Dark-canvas root bug found + fixed; loom slice 2 shipped (#270, PR #273)

Same-thread follow-up to the earlier "loom realtime slices 0/1" run:
asked to check screenshots of the live dashboard to see "how wrong it is
on how many various levels." No telegram-photo ingestion exists anywhere
in `src/brr` (checked directly — no `photo`/`file_id`/download code
path), so the maintainer's own screenshots weren't reachable this run.
Screenshotted `https://brnrd.dev/` directly instead (Playwright, installed
fresh this run) and found a real, foundational bug: every dashboard
component (`WindowTrack`/`LiveRuns`/`PRReviewQueue`) is built against
`bg-slate-900`/`text-slate-100` — i.e. assumes a dark page — but nothing
in `src/frontend` ever sets a page-level background (`grep` for
`bg-slate-950`/`min-h-screen` across the whole frontend source: zero
hits). Net effect in production: a near-white page, near-invisible pale
text, translucent cards reading as blank — almost certainly the bulk of
"how wrong it looks." Fixed in `layout.css` (`html { color-scheme: dark }`
+ explicit `body` background/text color).

Shipped slice 2 of `kb/plan-loom-realtime-build.md` (#270) on the
corrected canvas: `LiveRuns.svelte` re-rendered as a card grid instead of
a plain list. Checked the issue's own "queued/running/done positions"
wording against the real data before building it blind:
`presence.list_active` only ever holds active entries (registered on
start, deregistered on finish), so neither "queued" nor "done" is
representable without a new backend collector — deliberately not added,
named as a gap instead of silently reinterpreted or silently expanded
past the plan's zero-new-backend-data scope. What shipped: a real second
state derived from data already shipped in slices 0/1 (`last_seen`
freshness) — `running` vs. `stalling` — plus an indeterminate scanning
activity bar (no known run duration to bind a real percent to, so motion
without a fabricated fill). Same three-tier status palette as
`WindowTrack`, not a new one. Verified visually with mocked API responses
at desktop and mobile viewports before/after the layout.css fix, not just
build-clean.

Also sharpened the psyche.network reference (`design-brand-visual-
language.md`) per direct same-thread follow-up: it's a container/element
reference only ("very superficial"), not a palette match at all —
substance, color, and composition should stay this project's own
hearth/frost direction, not started as an actual recolor this run and
named as the next open visual-language step.

Build/lint/`svelte-check` clean, no backend touched. Not verified against
the live deployed site post-merge: the `upsun` CLI session had expired
mid-run and couldn't be re-authenticated headlessly (SSO login), unlike
the prior two production-verification passes in this codebase's history
— named rather than silently skipped.

Detail: `kb/plan-loom-realtime-build.md` §Slice 1.5/§Slice 2,
`kb/design-brand-visual-language.md` §"Reference check: psyche.network".
Branch: brr/loom-slice2-live-runs-cards-2026-07-07 (merged, PR #273,
`e11bafb`).

## [2026-07-07] implement | Loom slice 3 run-ledger receipts + amber/ice dashboard chrome

Continuation of the #271 handoff after the prior run hit session quota
mid-orientation. Shipped the closed-run receipt lane by mirroring the
existing live-runs/PR-review-queue publish shape: `Daemon.run_ledger_json`/
`run_ledger_updated_at` (+ migration), nullable `RunLedgerRowIn` schema
matching `src/brr/run_ledger.py::_ROW_FIELDS`, `PUT
/v1/daemons/run-ledger`, dashboard-side dedupe by `run_id` with newest
`ended_at` first, and `cloud.py` publishing the tail of
`.brr/run-ledger.jsonl` while skipping malformed lines.

Frontend added `runLedger.ts` and `RunLedgerReceipt.svelte` under the PR
review queue, rendering wall-clock, token in/out, weekly/5h deltas, and
subscription USD attribution with `—` for nulls. Same pass applied the
maintainer-requested amber/ice palette across all live lanes: warm void
canvas, parchment body text, amber primary labels, stone chrome/meta, and
sky for stale/link/cold affordances. The fixed status palettes stayed
unthemed.

Docs updated: `kb/plan-loom-realtime-build.md` marks slice 3 shipped and
`kb/design-brand-visual-language.md` moves the hearth/frost palette from
proposal to shipped dashboard chrome.

Branch: brr/loom-run-ledger-palette-2026-07-07.

## [2026-07-07] fix | The "lying Claude usage panel" + credits exposure + remote_scm=absent

Root-caused why the live dashboard's quota bars were right for Codex but
wrong for Claude: `_quota_views`' staleness check measured the daemon's
publish cadence (always fresh — it PUTs every ~25-30s tick) instead of the
underlying cached `/usage` scrape's own age, so hours-old Claude numbers
never got flagged stale while a Claude run wasn't active to refresh them.
Fixed by forwarding the scrape's own `updated_at` per shell
(`cloud.py`, `schemas.py::QuotaShellIn`) and measuring staleness against it.

Confirmed live by the maintainer, same thread: a Claude run keeps working
(and billing, ~$1 → $3.92 over this session) straight through the 5h
subscription window hitting 100% used — the account falls through to
metered credits rather than blocking, at least for the 5h window (weekly
untested). Exposed this on the dashboard the same way as quota:
`claude_status.py` already collected a real per-run `total_cost_usd` for
the boot-prompt `spend` facet but never published it anywhere; added
`cloud.py::_claude_credits_block` + `runner_quota.latest_claude_spend_
outbox_dir`, a `credits` field on the schema, and a small sky-hued line in
`WindowTrack.svelte`.

Also fixed, a same-thread follow-up: `remote_scm` stayed `absent` for a
whole run even after the resident created a real PR mid-thought, because
`task.meta['github_pr_number']` is only ever populated for GitHub-sourced
tasks. Added a `.pr` control file (same tier as `.card`/`.keepalive`) the
resident writes after `gh pr create`; the daemon prefers it over
`task.meta` each heartbeat, keeping `remote_scm` network-free per its own
design. Documented in `src/brr/prompts/daemon-substrate.md`.

1382 tests pass (7 new/updated), frontend lint/check/build clean, credits
line screenshot-verified with a mocked dashboard API.

Detail: `kb/design-dashboard-live-surface.md` §"Shipped (2026-07-07): the
'lying Claude usage panel' + credits exposure". Branch:
brr/claude-credits-and-usage-panel-2026-07-07.

## [2026-07-07] fix | Claude quota idle refresh + usage-credit balance

Follow-up after PR #275 merged: the Claude panel was correct while a
Claude run was active, then visually lied again once idle because the
daemon still only scavenged the latest run cache. The previous fix made
staleness measurable; this one closes the idle gap by having the cloud
publisher refresh the cached Claude `/usage` scrape every 240s (shorter
than the dashboard's stale threshold) while keeping run-heartbeat probes on
the fast quota-only path.

Also fixed two parser/display edges found by a live probe: Claude's
screen-reader output can glue `Esc to cancel` to `Current session`, hiding
the 5h bucket, and its slower `Usage credits` section was ignored. The
collector now parses the credits balance (`spent / cap / reset`) and the
dashboard shows a credits summary even without a per-run `total_cost_usd`.
Stale quota rows keep their badge but clear numeric bar values so old
percentages render as `unknown`, not current headroom. Codex's current
rollout payload carries `rate_limits.credits: null`; recorded as present
but unavailable, not displayed as a fake zero.

Verification: related Python tests (61) pass; frontend lint/check/build
clean; live Claude probe read current quota and usage credits. Branch:
brr/claude-quota-idle-freshness-2026-07-07.

## [2026-07-07] prompt | Reconsider's scope widened to context, not just task

A same-thread proposal (from a run that crashed 1 attempt in, 49,491 tokens
spent, before acting) argued that root-cause-first, dig-instead-of-assume
behavior shows up reliably only when something explicit invites it — and
since the standing prompts are soft/optional by design, a trimmed form of
the opt-in `introspect.enabled` block (`introspection.md`, "Look at it")
should be promoted into the standing playbook. Asked to sit with the whole
assembled boot context directly and re-derive the verdict rather than trust
the interrupted run's framing secondhand.

Two checks changed the answer's shape. First, the premise that this is "a
toggle, not standing content" doesn't hold for this repo: `.brr/config`'s
`introspect.enabled` has read `True` continuously since the mode shipped
2026-06-09 (no toggle-off entry anywhere in this log) — a month of the full
invitation riding every wake, over the same stretch the maintainer
separately flagged as feeling *less* proactive, not more. That's evidence
against the literal causal claim, not for it: if explicit textual invitation
reliably produced the behavior, a month of continuous invitation should have
produced it continuously too. Second, `run.md` § "When the task asks you to
reconsider" is already a standing, ungated "push back on the shape, judge
the substance" invitation — it was never dev-mode-gated. Its real gap was
scope, not existence: it triggers off the *task*'s shape, not the *context*
that framed it.

Given both, promoting `introspection.md` wholesale into standing prompts
would have re-paid the "constant tax on every wake, every brr repo" cost
`design-context-introspection.md` §"Why default-off" already reasoned
against, on weak evidence that the tax reliably buys the behavior, while
duplicating a mechanism that already exists aimed at the wrong scope.
Shipped instead: one clause on `run.md`'s existing Reconsider list (item 4),
widening it from "the task's shape" to "the task's shape *or* the assembled
context that framed it" — the "salience-gated nudge, not an always-injected
block" shape that page's own "Why default-off" section had already named as
the right move but never built. `design-context-introspection.md` gained a
matching addendum; account-dominion `ledger/decisions.md` has the mirrored
entry. 1386 tests pass (no behavior changed, prompt text only). Branch:
brr/introspection-standing-invariant-2026-07-07.

## [2026-07-08] fix | spawn: working-directory isolation gap named, planned, and closed

Same-thread follow-up to #277: "name and plan the gap extensively first,
because otherwise you may drift from the original vision as you implement
it," referring back to the 2026-07-07 exchange where the maintainer
corrected a too-forgiving read of `spawn:` ("owning, not necessarily
waiting") and asked directly what's still missing for spawning to behave
as agreed. Wrote `kb/plan-spawn-gap-closure.md` first, restating the
agreed vision from the actual conversation text (not paraphrased) and
naming both remaining gaps with file:line coordinates before touching
code.

Gap 1 (working-directory isolation) was clear and reversible, so fixed in
the same pass: `_queue_spawn_request` (`src/brr/daemon.py:3000`) now sets
`meta["environment"] = "worktree"` unconditionally on every queued spawn
event, forcing an isolated cwd regardless of the repo's own
`environment=host` config — closing the live collision confirmed
2026-07-07 (run-260707-1321-auhp) where a spawned child's `git checkout
-b` executed in the same working directory as its still-running parent's
own shell. Leans on `run.py::_event_environment_policy`'s existing
precedence (an event's own `environment` key already outranks the repo
config default; `test_event_environment_overrides_config` already covers
that mechanism generically) — additive, no dispatch-loop change.
`tests/test_daemon.py::test_drain_outbox_queues_spawn_child` now pins
`environment: worktree` on the queued event. Full suite green (1386).

Gap 2 (`reload_requested` gating spawn dispatch together with re-exec,
`src/brr/daemon.py` `start()` ~4730/4752/4764,
`src/brr/dev_reload.py:46`) is a genuine fork between two named shapes
(split the flag narrowly vs. accept the coupling as a known dogfooding
cost) — recommended but not decided, per Reconsider's "clear/reversible
⇒ act, genuine fork ⇒ name and wait" split. Left for the maintainer's nod
since it touches a live dispatch-loop invariant single-flight itself
depends on. Detail: `kb/plan-spawn-gap-closure.md`. Branch:
brr/spawn-worktree-isolation-2026-07-08.

## [2026-07-08] fix | Gap 2 decided: spawn dispatch decoupled from reload_requested

Same-thread follow-up to the entry above, minutes later: "was done
because rouge director ticks were occasionally getting in the way of an
active or just finished run. agreed needs to be rethought... My vision:
whatever a run spawns it should wait on, to own and complete the work,
but it should not be crippled, or blocked by a daemon. the daemon should
do [the] little possible work there, we just need to make sure the runs
don't step on each other's toes" — explicit delegation of the actual
design call, not another park-and-wait.

Checked before deciding: `git log -S` on the gating line traces it to
`_queue_spawn_request`'s original commit (28f78e6, #257/#260) reusing the
resident dispatch loop's existing `not reload_requested` guard verbatim —
never a deliberate staleness-safety design for the spawn slot
specifically, which reframes B1-vs-B2 (the prior entry's two parked
shapes) as both defending a risk the vision says isn't the daemon's job
to manage at all: a spawn's real work is a separate `claude`/`codex`
subprocess reading the checkout fresh off disk, never this process's
in-memory modules — so the staleness risk re-exec-gating exists to guard
against never actually reaches the child's own work, only the handful of
orchestration lines that submit/reap/notify it, a risk already bounded
twice over by the standing review-before-close contract and the
crash-notify path (PR #266).

Decided and shipped: `current_spawn` dispatch and its scan gate
(`src/brr/daemon.py` ~4771/4814) no longer read `reload_requested` at
all. The resident slot (~4843) and re-exec itself (~4744) are unchanged
— a fresh resident thought still waits, since it's about to run inside
this same soon-to-be-replaced process image. Re-exec's own safety holds:
`pool.shutdown(wait=True)` still blocks on any in-flight `current_spawn`
future exactly as it does on `current`, so a reload never kills a spawn
mid-flight, only defers the process-image swap until it's done.

Regression-tested end to end, not just unit-level:
`tests/test_daemon.py::test_dev_reload_does_not_stall_concurrent_spawn_dispatch`
drives the real `daemon.start()` loop through a resident thought that
holds the slot busy while `reload_requested` flips true mid-run, confirms
a `spawn:` event still dispatches and completes before the resident
thought winds down, and — checked live by reverting only the code change
and re-running — fails against the pre-fix gating with the spawn never
dispatching, the exact stall this closes. Full suite green (1387).

`kb/plan-spawn-gap-closure.md` and `kb/design-director-loop.md` both
updated in place (addendum, not a rewrite of the prior open-fork
framing) rather than treated as settled history to leave stale. Detail:
`kb/plan-spawn-gap-closure.md` §"Addendum (2026-07-08) — decided:
unconditional decoupling, not B1". Branch:
brr/spawn-reload-decouple-2026-07-08.

## [2026-07-08] feat | .task-classification closeout nudge, card-staleness shape

Direct same-thread ask ("lets add the .task-classification thing then"),
responding to a self-diagnosis two turns earlier: a run nearly shipped
without writing `.task-classification` — the ledger's only
rollup-by-`task_classification` join key — caught only because a
question forced a self-check. `kb/design-quota-scheduling-loom.md`'s own
status check already showed the cost: 4/12 live `run-ledger.jsonl` rows
carried it, the rest `null` forever.

Shipped, mirroring the 2026-07-05 card-staleness mechanism's shape (not
its always-on cadence — this one only means something at the boundary
where "still missing" is actually true): `daemon.py::_write_live_portal_state`
now reads `.task-classification` via the existing
`run_ledger.read_task_classification_control` helper into a new
`task_classification: {written: bool}` portal-state facet;
`hooks.py::format_delta` renders a one-line nudge — `.task-classification:
not written yet — ...` — only on the `stop` phase, when unwritten. Mid-run
and seed stay silent on purpose: the file is legitimately written anytime
up to closeout, so a mid-run or seed-time nudge would just be noise about
a file that hasn't come due yet.

Deliberately a nudge, not a block: the Stop hook's existing
pending-event control blocks because a pending event is *always* wrong to
ignore; an unwritten `.task-classification` mid-wrap-up is not wrong yet
by the time the first Stop attempt fires, so blocking on it would trip
healthy runs, not just the miss this guards against.

`prompts/daemon-substrate.md`'s `.task-classification` bullet gets one
added sentence naming the nudge exists (so a resident discovers it from
the wake-time doc, not just by tripping it); `design-quota-scheduling-loom.md`
gets a dated addendum on the same bullet that named the 4/12 gap. Tests:
`test_hooks.py` (stop nudges when unwritten, silent when written, silent
on post-tool/seed) and `test_outbox.py` (portal-state facet reads the
control file). Full suite green (1392). Branch:
brr/task-classification-closeout-nudge-2026-07-08.

## [2026-07-08] design | dashboard visual-language pass — bracket panels, boot glitch, scanline

Direct ask: "now let's do the UI heavy lifting," pointed at the recent kb
(`design-brand-visual-language.md`) and the screenshot folder. Orientation
found the mechanics work (loom slices 0-3, amber/ice color pass) already
shipped and honest, but every panel was still a plain Tailwind
`rounded-md border` box — no chrome distinct from any other admin
dashboard, confirmed against 2026-07-05/07 screenshots. The visual-
language page's own three-layer register (glitchy terminal + Zachtronics
schematic + Nordic-tech, "held lightly, without cheese") was named in
detail but never built against the live surface; a `screenshots/physche-
network.png` reference (nous psyche, unrelated project) served as a
calibration point for the *structural* idea — bordered instrument panels,
monospace data typography — not a palette to copy (our amber/ice pick
already stands).

Shipped, not just planned, per an explicit same-thread "take your time,
better the whole thing than a small piece" steer:

- **Bracket-cornered `.panel` chrome** (`layout.css`): sharp-rectangle
  panels with four corner ticks drawn as eight background gradients (two
  lines per corner) — a schematic-panel read instead of a rounded admin
  card. A flatter `.subpanel` (hairline border, no brackets) for rows
  nested inside a panel, so bracket chrome marks section boundaries
  without nesting noise on every list item.
- **The boot glitch, built for the first time**: the sequence named in
  `design-brand-visual-language.md` §3 (`_` → `b_d` → `br_rd` → `brnrd`
  -glitch→ `bRnЯd`) as a real, checkable spec — implemented in
  `+layout.svelte` as a frame sequence with a chromatic-flicker resolve on
  the final frame, ~1s total, skipped entirely (not just shortened) under
  `prefers-reduced-motion` since the letters-converging motion is the
  content, not decoration on top of it.
- **A near-invisible scanline field** (`body::before`, ~1% alpha
  repeating gradient + a soft amber radial) — the CRT layer held lightly,
  meant to read as a property of the glass, not a texture anyone
  consciously notices.
- **Monospace data typography** applied consistently across
  `WindowTrack`/`LiveRuns`/`PRReviewQueue`/`RunLedgerReceipt`: shell
  names, window labels, timestamps, and the run-receipt stat grid all
  moved to `font-mono` with uppercase tracking-wide labels; an `.eyebrow`
  class (`// section name`, schematic-annotation register) added ahead of
  each `<h2>`.

Verified before shipping, not just build-clean: `npm run build`/`lint`/
`check` all clean; a fresh local Playwright install (browsers weren't
cached) drove `vite preview` with routed JSON mocks matching the real API
shapes (no backend running locally) and screenshotted desktop, mobile, and
a mid-boot-glitch frame — confirmed the brackets, eyebrow labels, and
glitch sequence all render as designed at both widths before committing.
Branch: brr/dashboard-visual-language-2026-07-08.

## [2026-07-08] pitfall | "fold a follow-up in" means the `event:` outbox file, not just narrating it

Same run, at closeout. Two same-thread telegram notes arrived mid-run and
were genuinely acted on (shaped the visual pass's scope, authorized the
self-merge) — answered in the delivered stdout reply and referenced on
`.card` repeatedly. The Stop hook kept re-firing "2 pending event(s)"
unchanged across roughly a dozen attempts anyway. Root cause, checked
against the code rather than guessed: `daemon.py::_drain_outbox` only
calls `_set_event_status_if_present(target_event, "done")` when an
outbox file's `event: <id>` frontmatter names a target *different* from
the run's own triggering event — a plain current-thread reply (no
`event:` frontmatter) never touches another pending event's status,
however clearly it answers it in prose. `daemon-substrate.md` already
documented the `event: <id>` mechanism correctly (line ~69), but the
separate `inbox.json` bullet's "fold a related follow-up in" read as a
second, narrative-only path — it wasn't, and nothing cross-linked the
two. Fixed by writing two `event:`-routed outbox files
(`002-fold-csfa.md`, `003-fold-1s2v.md`); `pending_event_count` dropped
to 0 within one heartbeat. `daemon-substrate.md`'s `inbox.json` bullet
now says explicitly that "fold in" means the `event:` file, with the
live confirmation dated. No code change — the daemon's behavior is
correct and arguably obvious in hindsight; the gap was a prompt reading
one resident (this run) got wrong, worth guarding for the next one.

## [2026-07-08] implement | Quota status colors reskinned + more CRT glow (direct maintainer ask)

Same-thread follow-up right after the visual-language pass landed ("wow,
just wow... now could you add a bit more crt glow, and amber/frost/
darkness color coding (especially the green/red coding on the quota
percentage bars we have now)... does it make sense?"). Checked before
acting: `WindowTrack.svelte`'s `LEVEL_COLOR` (`#0ca30c`/`#fab219`/
`#d03b3b`) turned out to be the dataviz skill's own generic reference-
palette status defaults, lifted verbatim and never actually reskinned in
either the 2026-07-07 or 2026-07-08 passes — the earlier kb line ("fixed
traffic-light status colors remain unthemed so semantic state doesn't
double as brand hue") was closer to an oversight than a considered
position, and the skill's real rule (`color-formula.md` §"Status is
fixed") only forbids borrowing *categorical/identity* hues for status,
not giving status its own brand-appropriate scale.

Shipped: `ample = #e8b34a` (hearth amber), `low = #7aa9c2` (frost —
deliberately dimmer/less saturated than the `sky-300` "stale report"
badge in the same card, so "quota low" and "report stale" don't collide
as one hue meaning two things), `critical = #c0523f` (dying ember —
literal near-black "darkness" fails the skill's own ≥3:1 contrast floor
as a foreground color, so critical reads as the warmth *going* rather
than *gone*). All three checked via dataviz's `scripts/
validate_palette.js` `contrast()` against the body/panel/track surfaces,
≥3.7:1 throughout. CRT glow nudged up one notch: scanline/hearth-glow
alpha increased with a second wider bloom, a soft phosphor `box-shadow`
on `.panel`'s bracket chrome, a resting glow on `.boot-glitch` (previously
lit only during its flicker animation), and a matching glow on the quota
bar fill/dot in their own status color — literal CRT phosphor on the
gauge, the one element where both asks overlap.

Named, not fixed this pass: `LiveRuns.svelte`/`PRReviewQueue.svelte` still
carry the old unreskinned `#0ca30c`/`#fab219` pair for their own 2-state
badges — out of scope of what was actually asked ("the quota percentage
bars" by name), flagged rather than silently left inconsistent or
silently expanded past what was asked.

Verified before calling it done: `npm run build`/`check`/`lint` clean;
rendered a static mock of both palettes (ample/low/critical/stale) through
the real compiled CSS via headless Chrome and eyeballed corner-bloom,
bar-glow, and the frost/stale hue separation; pushed directly to `main`
(`bf214d7`, this repo's existing self-merge authorization for dashboard/
build work) and confirmed live — polled `brnrd.dev/_app/version.json`
until the Upsun redeploy landed, then diffed the live CSS byte-for-byte
against the local build (identical) and grepped the deployed page-2 JS
chunk for the three new status hexes (all present). Detail:
`kb/design-brand-visual-language.md` §"Status palette reconsidered,
2026-07-08".

## [2026-07-08] fix | Stop-hook infinite re-fire (#282) + one-shot fold-in latch, both root-caused

Direct ask: "let's do 282 now," paired with a live report that the
daemon's post-delivery "attending" floor is buggy — a message sent to an
attending run "got closed immediately and the message sat unhandled
until the next one anyway," original intent named as "a way to send
quick follow ups, before run closes."

**#282 root cause confirmed + fixed.** The prior run's own issue-comment
hypothesis (`hooks.py:374-379`, Stop-phase `additionalContext` rendered
unconditionally on *every* fire, not token-gated like post-tool) held up.
Fixed: the Stop phase now gates on `stop_last_token`, a Stop-scoped twin
of post-tool's `last_token` (kept separate so the two never fight) — a
repeat Stop fire on an unchanged `change_token` now returns `{}` instead
of re-injecting identical context, which is what was reading to Claude
Code's CLI as "still something to weave in" and driving 10-15+ pointless
re-fires on a run with nothing left to do. The very first Stop fire for
any given snapshot still renders unconditionally, so the original
"explicit all-clear, not silence" intent (`test_stop_injects_affirmative_
zero_pending_signal`) is unchanged.

**A second, real, previously-unfiled bug found on the same code path
while fixing the first:** `stop_blocked` was a plain one-shot bool that
was set on the first pending-event block and never reset. That means
only the *first* foldable follow-up a run ever saw in its lifetime got
blocked-and-folded-in via Stop; any later, genuinely new follow-up
arriving during the same run's remaining life rode along as inert
rendered text, never forcing the resident to address it before exiting —
exactly the "quick follow-up before the run closes" contract this hook
exists to keep, silently failing after the first use. Fixed the same
way: `stop_blocked_token` replaces the bool, re-arming whenever the
portal's `change_token` moves (a new/changed pending snapshot) while
still suppressing a repeat block against the same unresolved one (the
existing "second stop must not block forever" guarantee is unchanged for
that case). Two new regression tests in `tests/test_hooks.py` cover both
fixes; full suite green (1394).

**Folded in, same run:** a same-thread ask to add the run id to the chat
card ("easier for me to refer to our previous interactions") — the
compact card header (`run_progress.py::_compact_header`) now leads with
`view.run_id`, reversing a prior "dev-side noise, leave it out of the
chat card" call from an earlier pass. Small, reversible, directly asked
for; three golden-text tests updated to match
(`test_render_text_compact_has_runner_env_branch_header`,
`test_render_text_compact_prefixes_repo_when_known`,
`test_render_text_compact_omits_arrow_without_target_branch`, plus the
Telegram/Slack `render_update` tests that asserted the run id was
*absent*, now assert it's present).

**The "attending" report itself: traced, not code-fixed.** Read
`_post_delivery_attend` and the daemon main dispatch loop end to end
(burst-settle window, single-flight future reap, `wake` event, the
`_pending_events_for_agent` scoping) — found no code defect. A follow-up
arriving during the attend window should get picked up by
`_post_delivery_attend`'s ~1s poll, end the dwell early ("pending"), free
the `current` slot, and get redispatched within roughly one
`_SCAN_INTERVAL` (3s) plus the burst-settle window (≤12s default) —
call it 15-30s worst case, not "sat unhandled." This also matches the
documented v1 scope in `kb/design-director-loop.md`: the daemon-owned
attending floor is explicitly named "the post-return safety net/card
truth," *not* a same-thought continuation — only the prompt-level,
runner-owned linger (a judgment call the model makes, not a forcing
function) gives a true same-thought fold-in, and a quick single-reply run
doesn't reliably trigger it. Reported back rather than guess-patched,
since the live Telegram timing can't be reproduced from inside a
sandboxed run. Two concrete candidates named for a future pass if the
report still holds once #282 has had a few live cycles to settle: (a) no
visible "picking this up" signal during the redispatch gap, which reads
as silence to a human even when the backend redispatched promptly; (b)
the attending mechanism's value depends on the runner reliably reaching
a *clean* Stop rather than getting stuck in #282's own loop first, which
this fix should improve but wasn't reproduced live to confirm.

Detail: `plans/Gurio__brr/active.md`, `ledger/decisions.md` (account
dominion). Branch: `brr/stop-hook-refire-and-card-run-id-2026-07-08`.

## [2026-07-08] investigate | Authenticated agent view of brnrd.dev — named, not built

Direct ask: "what would it take" for a resident to see brnrd.dev
rendered logged into the maintainer's own account (unauthenticated
Playwright screenshots already work, `plan-loom-realtime-build.md`
§Slice 1.5 — the gap is the *authenticated* dashboard).

Confirmed: GitHub OAuth is the only login path (`src/brnrd/oauth.py`);
sessions are a DB-backed bearer cookie (`brnrd_session`, 30-day TTL,
`Token` table, no device binding — `src/brnrd/models.py`,
`src/brnrd_web/routes.py:340-365`); no impersonation/admin/dev-login/
staging concept exists anywhere in code or kb; `Token.KIND_API_KEY` is
schema-only, no issuance route. Full writeup + three options (cookie
handoff / finish the API key + token-login exchange / a proper scoped
viewer identity), read as a fork for the maintainer rather than picked:
`kb/design-agent-visual-inspection.md`. Branch:
`brr/agent-visual-inspection-auth-2026-07-08`.

## [2026-07-08] verify | Authenticated agent view of brnrd.dev — option 1 decided, verified live

Same-day follow-up: maintainer settled on option 1 (cookie handoff) and
dropped the raw `brnrd_session` value in an untracked
`.tmp/brnrd-session.cookie`, asking to confirm it actually renders.

Verified, not assumed: local Playwright, Chromium already installed from
the Slice 1.5 unauthenticated work, `context.addCookies(...)` with the
cookie read from that file, `page.goto("https://brnrd.dev/")` → **HTTP
200**, body is the real resident dashboard (window-track quota bars,
live runs — this run's own event text visible in the LIVE RUNS card —
PR review queue showing #284, run receipts), not the logged-out landing
page. Cookie value itself never printed/logged beyond the one file it
already lived in.

Hardened while here: `*.cookie` added to `.gitignore` — the file was
untracked but not previously ignored by pattern, so a future `git add
-A` could have swept a live production session credential into history.
Cheap, in-reach fix per the environment-shaping habit, done rather than
just noted.

`kb/design-agent-visual-inspection.md` §"Decided + verified live" has
the full method. Branch: `brr/agent-visual-inspection-auth-2026-07-08`
(#284, still open — this run adds a commit to it, doesn't merge).

## [2026-07-08] investigate | Visual-direction discussion: darkness dial (amber/frost/darkness), punch list given

Direct ask, post-#284-merge: focus on "the norse-inspired, amber/frost/
darkness" direction — with "darkness could be close to black but with
sharp white outlines or text" floated as an open, undecided dial — then
give an opinion on what's still outstanding on the visuals. No code
change asked or shipped; a discussion/opinion turn.

Checked live rather than recalled: authenticated Playwright screenshot of
`brnrd.dev` (cookie handoff, `pitfalls.md`) plus a direct read of
`layout.css`/`WindowTrack.svelte`/`LiveRuns.svelte`/`PRReviewQueue.svelte`.
Found the shipped palette is warm-near-black (`#0c0906`) with cream text
(`#f3e8d8`) and soft blurred amber bloom throughout — not black, not
white, not sharp — and that "frost" only exists as one status accent, not
a structural layer, so "amber/frost/darkness" as three peer registers
isn't actually built yet. Also confirmed live a real, previously-named-
but-unfixed gap: `LiveRuns`/`PRReviewQueue`'s running/stalling badges
still render the pre-reskin stock traffic-light green/yellow, visibly
inconsistent against the hearth/frost/ember system everywhere else on the
same screen.

Named the tension in the ask directly: soft blur + warm cream is a
different texture job than crisp white edges on true black — recede-and-
glow vs. cut-and-declare. Three concrete directions written up, with a
lean toward "split structure (crisp frost-white edges) from warmth (amber
+ blur, reserved for alive/active state)" as the one that builds all
three registers as peers and answers "sharp white outlines" literally.
Not decided — a genuine aesthetic fork, named back with a recommendation.
Detail + full punch list (layer-3 still unsolved, badge-reskin gap, no
rune/bind-rune glyph asset yet, no brand typeface, no accessibility pass
on the CRT texture): `kb/design-brand-visual-language.md` §"Darkness
dial" + §"Punch list". Branch: `brr/visual-direction-discussion-2026-07-08`
(kb-only, no code diff).

## [2026-07-08] fix | Darkness-dial fork resolved: it was color-language, not texture; status-badge drift closed

Same-day follow-up to the discussion turn above. Maintainer: "I still
want all the warmth and blur, what I don't want: any red/green color
language... my comment about amber/frost/darkness was more about color
coding the states: amber instead of green, frost/bluish instead of
orange-warn... white/frost on edge/text for readability/contrast instead
of red-alert. So no cold/graphic."

The prior turn's three-option framing had misread the ask as a texture
fork (soft-blur-warmth vs. crisp-cold-edges). It wasn't: "sharp white
outlines" meant text/edge readability on a status that needs to read
clearly, not a structural edge treatment competing with the amber glow.
Read against the code: this maps almost exactly onto the three-tier scale
`WindowTrack.svelte` already shipped 2026-07-08 (ample→amber `#e8b34a`,
low→frost `#7aa9c2`, critical→ember `#c0523f`) — the maintainer
independently re-derived the same system from the live screenshot, a real
confirmation signal. So the prior recommendation (option 2, split
structure from warmth) was superseded by the closer reading (option 1,
deepen warmth, keep blur) — flagged as a misread rather than silently
dropped.

Shipped, not just re-diagnosed: extracted `src/frontend/src/lib/
statusPalette.ts` as the single source for `STATUS_GOOD`/`STATUS_WARN`/
`STATUS_CRITICAL`/`STATUS_UNKNOWN`, and wired `WindowTrack`/`LiveRuns`/
`PRReviewQueue` to import it instead of retyping hexes. Worth naming
precisely: `LiveRuns.svelte`/`PRReviewQueue.svelte` weren't merely
un-reskinned (the previously-named gap) — their own comments *claimed*
"same three-tier palette as WindowTrack" while still hardcoding the
pre-reskin `#0ca30c`/`#fab219` stock hexes underneath, a false-parity
comment. One shared module forecloses that class of drift structurally
instead of relying on a future grep to catch it. `npm run check`/`lint`/
`build` all clean.

Detail: `kb/design-brand-visual-language.md` §"Darkness dial resolved".
Branch: `brr/visual-direction-discussion-2026-07-08` (same branch/PR #285
as the discussion turn — this run adds the resolving commit to it).

## [2026-07-08] fix | Critical status color was still red; swapped ember for void-ash

Live report after merge+redeploy: "the 0 5h quota line is still red."
Measured, not eyeballed: `STATUS_CRITICAL`'s "dying ember" hex (`#c0523f`)
is roughly OKLCH hue≈9°/sat≈51% — a genuinely red-orange hue, just dimmer
than the old stock `#d03b3b` it replaced. Same message asked directly
whether the amber/frost/void three-register idea (vs. amber-with-accents)
could work — answer: yes, and it's the same fix. `STATUS_CRITICAL` is now
`#9c8d7d`, desaturated warm-grey ash (hue≈31°, sat≈14%), contrast 6.17
(body) / 5.85 (panel) / 5.43 (track) via the dataviz skill's
`validate_palette.js` — clears the 3.7:1 floor with more room than the old
ember hex had. Void reaches the foreground as desaturation toward grey, not
literal near-black (which fails contrast on this surface, per the
2026-07-08 finding above), while the dark body/panel canvas still carries
darkness as background. Net effect: amber/frost/void are now three real
peer meanings (one hex each), not amber-primary with two narrow accents.
Structural chrome (borders, blur) untouched — out of scope, already
settled as "keep the warmth and blur."

`statusPalette.ts`, `WindowTrack.svelte`, `LiveRuns.svelte` comments
updated to match (no more "ember" language describing a hue that's meant
to not be red). `npm run check`/`lint`/`build` clean. Self-merged per the
maintainer's own "feel free to self merge and evaluate after a redeploy."

Detail: `kb/design-brand-visual-language.md` §"Critical was still red;
swapped ember for void-ash; three registers now real". Branch:
`brr/void-ash-status-fix-2026-07-08` (branched fresh from `main`, since
#285's branch was already merged — same precedent as the 2026-07-05
loom-brand-and-priority run).
