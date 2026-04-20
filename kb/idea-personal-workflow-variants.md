# Idea: Personal Workflow Variants

> **Superseded.** This page's scope was folded into the broader
> fleet-and-steering design. See:
>
> - [deck-brr-current.md](deck-brr-current.md) — bird's-eye of the
>   system as it exists today, for context.
> - [deck-brr-fleet-steering.md](deck-brr-fleet-steering.md) — the
>   three-axis design (overlays, `brnrd`, environments) that absorbs
>   this idea as **Axis 1**.
>
> Decisions recorded there (single-slot profile, pull-on-next-run,
> `~/.config/brr/` ownership, prompts+config scope) are the canonical
> answers to the open questions below. This page is kept for provenance.

## Context

brr currently has two layers:

- **Bundled templates** shipped in the package (`src/brr/prompts/`,
  `src/brr/docs/`).
- **Per-repo overrides** under `.brr/prompts/` and `.brr/docs/`.

This covers the "default tool behaviour vs. this one project needs a
tweak" axis. It does not cover the "I have a consistent personal
workflow across N projects and I don't want to re-override in every
repo" axis.

Example use cases:

- A user who always wants a specific commit-message style, or a
  specific kb structure, or different log format, across all their
  work.
- A user who runs brr against multiple projects of different kinds
  (work vs. personal) and wants a different default behaviour per
  category.

## Sketch (original — kept for history)

Split the brr distribution (or its prompts/docs) into:

1. **Machinery** — daemon, gates, runner plumbing, triage. Stable,
   shipped with the package. Not user-editable conceptually.
2. **Workflow overlay** — prompt templates, docs, default kb
   conventions. Opinionated, swappable.

The overlay would be selectable: a user could point brr at a personal
overlay repo / directory (e.g. `~/.config/brr/overlays/work/`) and
brr would layer it between "bundled defaults" and "per-repo
overrides":

```
bundled → user overlay → per-repo override
```

Multiple overlays (`work`, `personal`) could be selected per repo via
a config setting or env var.

## Resolution (see deck-brr-fleet-steering.md for full design)

- **Shape:** four-layer chain
  `bundled → ~/.config/brr/default/ → ~/.config/brr/profiles/<name>/ → .brr/prompts/`.
- **Composition:** single slot (`profile=<name>` in `.brr/config`).
- **Transport:** filesystem path only in v1. Git-remote overlays are a
  trivial user-side workflow (`git clone` into `~/.config/brr/`). No
  special mechanism needed.
- **Update model:** pull-on-next-run. Overlay files are read live; no
  copy into `.brr/`.
- **Scope:** prompts and `.brr/config` defaults. Bundled docs stay
  bundled — they describe brr itself, not agent behaviour.
- **Versioning:** not needed in v1. Overlay is a directory of plain
  markdown; if brr's prompt contract changes, the user updates the
  overlay the same way they'd update any of their own dotfiles.
- **Relation to fleet:** overlay selection is per-repo, but fleet-wide
  rollout is covered by `brnrd all --profile=<name> <cmd>` (Axis 2).

## Status

Absorbed into the fleet-and-steering roadmap as **Phase 1**
(~200 LOC, roughly one week). The overlay is the minimum compelling
slice — it unblocks the "one edit, N repos converge" scenario even
before `brnrd` exists, because each repo just reads its overlay on the
next run.
