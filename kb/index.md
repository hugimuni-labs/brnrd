# Knowledge Base Index

Pages are organized by category. Update this file whenever you create
or remove a page.

Tool-level documentation (how brr itself works, pipeline/artifact
map, internals) ships with the package. Run `brr docs` to list it.
This index only covers this repo's project-specific knowledge.

## Architecture

- [Concurrent Worktrees Plan](plan-concurrent-worktrees.md) — v2: concurrent task execution via worktrees, task abstraction, per-task logs, env abstraction
- [Branch Modes Plan](plan-branch-modes.md) — v2: branch as task property, agent-decided branching, needs-context status, execution environments

## Decisions

- [Bundled Docs Location](decision-bundled-docs.md) — why tool-level docs live in `src/brr/docs/` and ship with the package rather than in `kb/`

## Ideas / Follow-ups

- [Personal Workflow Variants](idea-personal-workflow-variants.md) — split brr into machinery vs. a personal workflow overlay so users don't need per-repo overrides

## Research

- [PR #1 Review](review-pr-1.md) — deep review notes for task abstraction PR and follow-up notes after wiring the triage path
- [Concurrency Follow-up Review](review-concurrency-followup-2026-04-14.md) — second review pass clarifying that concurrency scaffolding exists but the merge coordinator and worker pool are not implemented yet
