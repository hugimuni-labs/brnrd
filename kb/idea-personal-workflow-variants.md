# Idea: Personal Workflow Variants

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

## Sketch

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

## Open questions

- Does the overlay travel by filesystem path, by git remote, or by
  package (pip-installable)?
- How does an overlay express that it depends on a specific brr
  machinery version?
- Do we really need this layer, or can per-repo overrides + a shared
  git submodule cover the same use case with less mechanism?

## Status

Follow-up idea, not scheduled. Captured so it doesn't get lost. If we
pick it up, start by auditing what a user actually wants to change
across all their repos — that tells us what belongs in the overlay vs.
the machinery.
