# Activity Log

Newest entries at the bottom. Format:

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
