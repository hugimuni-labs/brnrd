"""diffense — the kb-first PR review surface (see kb/design-diffense.md).

Current contents are a **spike**, not the finished product: a generic,
dependency-free web renderer over a review *pack* (the JSON contract
defined in the design). The spike exists to validate the card / zoom /
navigation model against the hand-authored PR #64 prototype pack before
the schema and the renderer are locked.

- ``template.html`` — the renderer (HTML + CSS + vanilla JS), generic
  over any pack. Loads the pack from an embedded ``<script>`` tag (so the
  built file opens with no server) or, failing that, ``?pack=<url>``.
- ``render.py`` — inlines a pack JSON into the template, producing a
  self-contained HTML file. The seed of the eventual ``brr review``
  publish/serve step.

Deliberately light and brnrd-independent, per the design's "keep it
light" constraint: no framework, no build step, zero runtime deps.
"""
