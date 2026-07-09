# brr-docs

User-facing documentation site for [brr](https://github.com/Gurio/brr) /
brnrd, built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/).

Deployed to GitHub Pages from `main` on every push (see
`.github/workflows/deploy.yml`); once Pages is enabled for this repo it
serves at `https://gurio.github.io/brr-docs/`, and `brr.dev` (once
registered, per `decision-websites.md` in the brr project's own
knowledge base) is expected to point here.

## Local development

```bash
pip install -r requirements.txt
mkdocs serve
```

## Scope and sourcing

This site does **not** mirror brr's internal knowledge base. See
`decision-docs-site-sourcing.md` (brr's private knowledge base,
`hugimuni-labs/brnrd-knowledge`) for the reasoning: this repo draws from
`src/brr/docs/` (bundled, already-adopter-facing tool docs) and
hand-curated, rewritten distillations of reference material — never a
bulk import of design/decision/planning history.

## Status

Early scaffold (2026-07-09): navigation shape, quickstart, install, CLI
reference, and three concept pages (`AGENTS.md` + kb, the daemon, gates
and portals) are seeded and accurate as of that date. Self-hosting and
hosted-product pages are placeholders pending
`plan-brnrd-dashboard-mvp.md` / `decision-websites.md`'s brnrd.dev slice.
Contributions and corrections welcome via PR.
