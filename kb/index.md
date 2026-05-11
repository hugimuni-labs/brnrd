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
- [Developer daemon reload design](design-daemon-dev-reload.md) —
  *shipped*. Opt-in brr self-development reload mode: editable install
  plus quiescent re-exec between tasks when brr package files change;
  kept explicit via `--dev-reload` / `dev_reload=true`, not a default.
- [`AGENTS.md`](../AGENTS.md) — universal agent playbook (canonical
  copy lives at `src/brr/AGENTS.md`, symlinked here).

## Environments

- [Env Interface design](design-env-interface.md) — *in flight (3/5
  envs shipped)*. `Env` Protocol, durability contract, decentralised
  merging, and the plugin point. Tactical companion to the env slice
  of the fleet deck.
- [Concurrent Worktrees Plan](plan-concurrent-worktrees.md) —
  *shipped (one-task-per-worktree slice; merge-coordinator path
  abandoned)*. Original architecture for parallel task execution;
  read for the reasoning that informed the current `worktree.py` +
  env protocol shape.

## Tasks & branching

- **Hub: [tasks and branching](subject-tasks-branching.md)** —
  synthesis of mechanical task construction, environment resolution,
  agent-owned runtime branching, worktree finalization, and the active
  branch-intent design that removes both ambient host checkout state and
  hidden universal landing-branch config from daemon-produced commits.
- [Daemon branch intent design](design-daemon-landing-branch.md) —
  *accepted*. Resolve seed refs and optional auto-land targets from
  explicit structured event data, conversation/thread branch context,
  host branch context, and fallback policy without adding a pre-run LLM
  branch selector; richer source-specific issue/PR/task metadata remains
  a future expansion point.
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
- [LLM Wiki framing](llm-wiki.md) — the source framing this project
  takes inspiration from for the wiki/synthesis layer.

## Research

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
