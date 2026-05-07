# Decision: Bundled Docs Location

## Context

brr needed a stable home for tool-level agent documentation —
pipeline explanations, `.brr/` layout, the kb-maintenance trigger
logic. The first attempt put `execution-map.md` in `kb/`, which
conflated two different audiences:

- **kb/** is owned by agents working in a specific project and is
  committed to that project's repo.
- **Tool documentation** is the same for every brr user; its lifecycle
  tracks the installed brr version, not the host repo.

## Alternatives considered

1. **Keep it in `kb/`.** Simple, but every `brr init` would need to
   copy it into the new repo, and updates to brr would silently drift
   out of sync with each repo's copy. Also muddies `kb/` ownership —
   agents editing kb/ would be editing tool docs, which is not their
   job.

2. **Put it in `.brr/docs/` at runtime, written by `brr init`.** Still
   creates on-disk copies that can drift and need refresh. Raises the
   "auto-refresh on start" problem: any refresh mechanism risks
   overwriting user customisations.

3. **Ship it as package data (`src/brr/docs/`) and expose via
   `brr docs`.** No on-disk copy in user repos. Updates happen when
   brr itself is upgraded. Users can still override a topic by
   dropping a file in `.brr/docs/<topic>.md`; the loader checks the
   override path first.

## Decision

Option 3. Bundled docs live in `src/brr/docs/*.md` and are accessed
via the `brr docs` CLI. Override mechanism mirrors the pattern we
already use for prompts (`src/brr/prompts/` with `.brr/prompts/`
overrides).

## Consequences

- Tool docs upgrade with the package; no `brr init --refresh` needed.
- `kb/` stays strictly project-specific.
- Agents running under brr are told (in `prompts/run.md`) to run
  `brr docs brr-internals` if the environment is unclear, rather than
  being handed a large on-disk doc up front.
- User overrides remain possible per-repo via `.brr/docs/<topic>.md`.
