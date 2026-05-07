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
