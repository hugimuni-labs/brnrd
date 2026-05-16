# Knowledge Base Index

Pages are grouped by **subject area** — Environments, Tasks &
branching, Conversations & responses, Documentation strategy, Fleet &
overlays, KB itself, Research. The grouping is editorial: the kb is
ultimately a graph (see [`AGENTS.md`](../AGENTS.md) → "Knowledge base
shape" and [`decision-kb-shape.md`](decision-kb-shape.md)). The index
is the canonical entry point; once a subject accretes a real hub
page, link it at the top of its section.

Tool-level documentation (how brr itself works, pipeline / artifact
map, internals) ships with the package. Run `brr docs` to list it.
This index covers only this repo's project knowledge.

Lifecycle markers on a link reflect the page's current status:

- *active* — current state of thinking; safe to follow.
- *shipped* — the work has landed; the page is now context for the
  decisions that survive in the codebase.
- *blocked* / *paused* — held behind another piece of work; the page
  says what would unblock it.

Pages without a marker are reference (research, decisions, the
dive-in map) and are stable until something contradicts them.

## Architecture & orientation

- [Repo Dive-In Map](repo-dive-in-map.md) — bottom-up source map for
  understanding the repo file by file, with branch-neutral relative
  links, core entity cross-references, runtime invariants, and
  recommended reading paths.
- **Hub: [daemon and process lifecycle](subject-daemon.md)** —
  synthesis of the foreground `brr up` process, gate/file-protocol
  boundary, serial worker lifecycle, local process control, and where
  developer reload fits without becoming broad product UX.
- [Git layer rework design](design-git-layer-rework.md) — *shipped
  on 2026-05-15*. Reframes the deleted tasks-folder gate around what
  it was conflating: daemon-side freshness (pre-task fetch+ff with
  the seed-ref invariant), a real GitHub gate (built-in, stdlib,
  polling, label + mention triggers, PR-comment events carrying
  `branch_target`), and a prompt-level mitigation for runner
  thoughtfulness on design-loaded tasks (revisit-signal section in
  the run prompt + a self-review bullet).
- [Developer daemon reload design](design-daemon-dev-reload.md) —
  *shipped*. Opt-in brr self-development reload mode: editable install
  plus quiescent re-exec between tasks when brr package files change;
  kept explicit via `--dev-reload` / `dev_reload=true`, not a default.
- [`AGENTS.md`](../AGENTS.md) — universal agent playbook (canonical
  copy lives at `src/brr/AGENTS.md`, symlinked here).

## Environments

- **Hub: [environments](subject-envs.md)** — synthesis of the `Env`
  Protocol (three-phase `prepare → invoke → finalize`), the durability
  contract enforced from the host, the outcome-aware salvage rule,
  decentralised fast-forward merging, and which envs ship today
  (`host` / `worktree` / `docker`) versus designed-but-pending
  (`ssh` / `devcontainer` / env plugins).
- [Env protocol design](design-env-interface.md) — *accepted on
  2026-05-06*. Full protocol, per-env mechanics, response-path split,
  accepted plugin / script-env model, and configuration surface.
  Tactical companion to the env slice of the fleet deck.
- [Concurrent Worktrees Plan](plan-concurrent-worktrees.md) —
  *superseded on 2026-05-16 by*
  [`design-concurrent-execution.md`](design-concurrent-execution.md).
  Preserved for the reasoning that informed the current `worktree.py`
  + env protocol shape; the merge-coordinator design described there
  was abandoned and never came back.
- [Concurrent execution design](design-concurrent-execution.md) —
  *accepted on 2026-05-16*. Partitioned per-event/per-task state
  removes the shared-mutable surfaces a serial daemon was hiding;
  threaded loop on top synchronises only on per-branch ff and push.

## Tasks & branching

- **Hub: [tasks and branching](subject-tasks-branching.md)** —
  synthesis of mechanical task construction, environment resolution,
  agent-owned runtime branching, worktree finalization, and the active
  branch-intent design that removes both ambient host checkout state and
  hidden universal landing-branch config from daemon-produced commits.
- [Daemon branch intent design](design-daemon-landing-branch.md) —
  *accepted on 2026-05-12; amended on 2026-05-18*. Resolve seed refs
  and optional auto-land targets from explicit structured event data;
  conversation branch facts are prompt context only after the
  2026-05-12 amendment, and explicit target rewrites publish through a
  leased PR-rebase path after the 2026-05-18 amendment.
- [Branch Modes Plan](plan-branch-modes.md) — *shipped, with
  revisions*. Branch and env are task properties, the agent owns
  branching at runtime. Triage and `needs_context` were reversed —
  see the decision below.
- [Remove the triage stage](decision-remove-triage.md) — why the
  LLM-driven triage step and the frontmatter-as-stdout contract were
  removed in favour of mechanical task construction, agent-decided
  branching, and plain-text responses.

## Conversations & responses

- [Drop streams; conversations are routing+history, not identity](decision-drop-streams.md) —
  why the workstream layer was removed and replaced with a thin
  per-conversation log; lessons from the 2026-05-05 frozen-intent
  incident.
- [Conversations bundled doc](../src/brr/docs/conversations.md) —
  package documentation for the per-gate-thread conversation log.

## Documentation strategy

- [Bundled Docs Location](decision-bundled-docs.md) — why tool-level
  docs live in `src/brr/docs/` and ship with the package rather than
  in `kb/`.

## Fleet & overlays *(paused — env axis is the only active strand)*

- **Hub: [fleet and overlays](subject-fleet-overlays.md)** —
  synthesis of the three-axis split: overlays as user-level steering,
  `brnrd` as a future fleet operator outside repo-local brr, and
  environments as the active axis now handled by the env hub.
- [Deck: brr fleet & steering](deck-brr-fleet-steering.md) —
  *roadmap (env axis active, overlays/brnrd paused)*. Three-axis
  framing (overlays · brnrd · environments); read for the strategic
  shape, not as a current spec — see decision pages and the env
  design for the live state.
- [Overlays plan](plan-overlays.md) — *blocked* on the env work and
  a research gate for single-file vs multi-file overlays.
- [Notes: Fleet pondering](notes-pondering-fleet.md) — *paused*.
  Capture-only thinking: open questions on overlays-as-single-file,
  dropping `brr eject`, self-maintaining repo registry,
  brnrd-as-agentic-operator, cross-platform supervisor, decentralised
  merge.

## Knowledge base itself

- **Hub: [the kb itself](subject-kb.md)** — synthesis of the kb
  pattern in brr today: four memory layers, graph topology with
  index reachability and lifecycle markers, when to create a subject
  hub, cross-tool maintenance via AGENTS.md schema + brr's
  preflight + LLM redundancy pass, what was tried and rejected.
- [kb shape decision](decision-kb-shape.md) — four memory layers
  (raw / episodic-thin / semantic+decisional / schema), kb as a graph
  with explicit linking discipline, lifecycle markers, the subject
  genesis rule, brr's daemon kb-maintenance reframed as a redundancy
  pass; staged execution plan.
- [State-first kb maintenance plan](plan-kb-state-first-maintenance.md) —
  *active*. Refine the kb shape around current-state synthesis plus
  short breadcrumbs to git history, and replace hidden post-task LLM
  cleanup with explicit, first-class maintenance tasks.
- [Agent orientation layering](plan-agent-orientation-layering.md) —
  *active (slices 1+2 shipped 2026-05-16)*. Synthesis of the two
  same-day ergonomics reviews into a four-layer model (repository
  contract / stage overlay / runtime state packet / subject
  knowledge), with shipped, in-flight, and open follow-up slices
  marked.
- [LLM Wiki framing](llm-wiki.md) — the source framing this project
  takes inspiration from for the wiki/synthesis layer.

## Research

- [Cursor orientation ergonomics, 2026-05-16](research-cursor-orientation-ergonomics-2026-05-16.md) —
  *shipped*. External Cursor session view: AGENTS.md mode-blindness,
  the orientation read cost (~4,200 lines for a session that uses
  ~25-30%), specific redundancy across README / AGENTS.md / index /
  log / dive-in-map. Headline recommendations absorbed into
  [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md).
- [Cursor orientation ergonomics — follow-up, 2026-05-16](research-cursor-orientation-ergonomics-followup-2026-05-16.md) —
  *active*. Same-day second-pass review after slices 1+2 shipped.
  Surfaces a Cursor workspace-rule cache that delivers a stale
  `AGENTS.md` to the agent, confirms the user-flagged
  README ↔ AGENTS.md elevator-pitch / Build-and-run duplication, and
  recommends dropping the plan's slice 3 (snapshot test) as low ROI.
- [Runner orientation ergonomics, 2026-05-16](research-runner-orientation-ergonomics-2026-05-16.md) —
  *shipped*. Same-day daemon-launched-runner view of the same
  problem from inside Docker: pinpoints the stage-vs-environment
  axis confusion, the missing Mode block on the Task Context
  Bundle, and the run-context-file duplication. Converged
  independently with the Cursor review.
- [Stdlib dependency policy, 2026-05-16](research-stdlib-dependency-policy-2026-05-16.md) —
  architecture review of the zero-runtime-dependency rule: keep a
  dependency-free core as the default, but treat dependencies as
  acceptable at explicit gates/env/plugin edges when they delete real
  complexity. Also records the current contradiction that env plugin
  support is described in the kb but not implemented by `get_env`.
- [Test suite grooming, 2026-05-16](research-test-suite-grooming-2026-05-16.md) —
  *shipped*. Map of bloat, cross-file helper duplication, and
  intent-quality gaps in `tests/`; the high-leverage moves
  (`test_integration.py` removal, `tests/_helpers.py` extraction,
  `_forge_view_url` stub-based rewrite, docker-mounts parametrize)
  were executed in the same pass.
- [Branch plan simplification, 2026-05-12](research-branch-plan-simplification-2026-05-12.md) —
  follow-up critique of the accepted branch-intent implementation:
  preserve the mechanical seed/auto-land/finalization contract, but
  shrink branch planning back to landing defaults and stop treating
  inferred conversation branch history as hidden auto-land authority.
- [Daemon runner context ergonomics, 2026-05-09](research-runner-context-ergonomics-2026-05-09.md) —
  point-in-time review of a live brr daemon run: how much context the
  agent had to read, which prompt/runtime surfaces helped, where the
  Task Context Bundle was noisy, stale bundled-doc contradictions, and
  Docker image tooling gaps for brr self-work.
- [brr vs gh-aw](research-brr-vs-gh-aw.md) — deep comparison with
  GitHub Agentic Workflows: substrate / transport / durability /
  security / fleet axes, market fit for the remote-controlled
  repo-first CLI runner use case, which gh-aw ideas brr could
  credibly adopt vs. not.
