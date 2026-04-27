# Knowledge Base Index

Pages are organized by category. Update this file whenever you create
or remove a page.

Tool-level documentation (how brr itself works, pipeline/artifact
map, internals) ships with the package. Run `brr docs` to list it.
This index only covers this repo's project-specific knowledge.

## Architecture

- [Concurrent Worktrees Plan](plan-concurrent-worktrees.md) — v2: concurrent task execution via worktrees, task abstraction, per-task logs, env abstraction
- [Branch Modes Plan](plan-branch-modes.md) — v2: branch as task property, agent-decided branching, needs-context status, execution environments
- [Workstreams](../src/brr/docs/streams.md) — bundled tool doc covering the workstream model, runtime layout, lifecycle update packets, reply routing, and CLI surface (`brr streams`, `brr stream show`)

## Decisions

- [Bundled Docs Location](decision-bundled-docs.md) — why tool-level docs live in `src/brr/docs/` and ship with the package rather than in `kb/`

## Design decks

- [Deck: brr today](deck-brr-current.md) — Marp bird's-eye of the current system (file protocol, pipeline, CLI surface, state layout, override model, pain points)
- [Deck: brr fleet & steering](deck-brr-fleet-steering.md) — Marp three-axis design (overlays, `brnrd`, environments) with locked decisions, roadmap, and the minimum compelling slice

## Active design

- [Env Interface design](design-env-interface.md) — actionable spec for the in-flight worktree PR: `Env` Protocol, durability contract, `local`/`worktree`/`docker`/`ssh`/`devcontainer` built-ins, salvage rule on `error`/`conflict`, decentralised merging via `git merge --ff-only` + `conflict` status, dual plugin model (Python entry points + drop-in script envs)
- [Overlays plan](plan-overlays.md) — **blocked** on the env PR and a research gate (`kb/research-overlay-shape.md`) picking single-file vs multi-file overlays; covers XDG paths, git-backed `~/.config/brr/`, `brr overlay init|sync|show`, and the staged `brr eject` retirement

## Ideas / Follow-ups

- [Personal Workflow Variants](idea-personal-workflow-variants.md) — absorbed into the fleet-&-steering deck as Axis 1; kept for provenance
- [Notes: Fleet pondering](notes-pondering-fleet.md) — open thinking on overlays-as-single-file, dropping `brr eject`, self-maintaining repo registry, brnrd-as-agentic-operator, cross-platform supervisor, decentralised merge examples — capture-only while env work ships

## Research

- [PR #1 Review](review-pr-1.md) — deep review notes for task abstraction PR and follow-up notes after wiring the triage path
- [Concurrency Follow-up Review](review-concurrency-followup-2026-04-14.md) — second review pass clarifying that concurrency scaffolding exists but the merge coordinator and worker pool are not implemented yet
- [brr vs gh-aw](research-brr-vs-gh-aw.md) — deep comparison with GitHub Agentic Workflows: axes of opposition (substrate, transport, durability, security, fleet), market fit, verdict for the remote-controlled repo-first CLI runner use case, and which ideas brr could credibly adopt (`safe-outputs`, rate-limits, XPIA) vs. not (compile step, frontmatter DSL, GitHub-shaped worldview)

## Agent ergonomics evaluations

- [Task Context Bundle runner review](agent-ergonomics-evaluation/task-context-bundle-runner-review-2026-04-28.md) — review from inside a live daemon worktree task after the workstream and Task Context Bundle changes, covering context recovery effort, stream/task artifact discoverability, per-task log lifecycle, prompt/docs clarity, and prioritized runner-UX recommendations
- [Task log: Task Context Bundle runner review](log-task-1777333195-8ed7.md) — per-task log entry for the 2026-04-28 runner ergonomics evaluation
