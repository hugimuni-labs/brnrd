"""diffense — the kb-first PR review surface (see kb/design-diffense.md).

A review *pack* is the JSON contract (defined in the design) that a
runner emits to describe a change; renderers and the PR-creation slice
all read it. Two pieces live here:

- ``pack.py`` — the **locked schema + validator**: the engine behind
  ``brnrd review --check``. It loads a pack, validates the always-present
  axes and the card graph (open-core kinds; dangling card edges and
  reading-order references are errors), resolves every code/kb locator
  against the repo, and runs the cheap end of the six-clamp discipline as
  lints. A non-zero exit blocks publish of a broken pack.
- ``template.html`` + ``render.py`` — the renderer **spike**: a generic,
  dependency-free web view that inlines a pack into a self-contained HTML
  file, or serves a browser-side shell that fetches ``?pack=<url>`` from a
  user-owned gist. It validated the card / zoom / navigation read model
  against the PR #64 prototype pack; the local ``brnrd review`` serve step
  grows from it.
- ``gist.py`` — the durable publication seam: write the pack JSON to a
  secret gist owned by the user's GitHub account and compose a brnrd
  renderer-shell URL. The old brnrd RAM relay remains the fallback.

Deliberately light and brnrd-independent, per the design's "keep it
light" constraint: no framework, no build step, zero runtime deps.
"""
