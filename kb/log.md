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

