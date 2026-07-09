# Plan: move brr's own committed `kb/` out of the repo

Status: active (2026-07-09). Execution plan for the "move the kb out of the
repo" ask (evt-1783554621272761188-04p5, `telegram:155783668:`), read
together with the already-accepted
[`design-home-scopes-and-knowledge.md`](design-home-scopes-and-knowledge.md)
(2026-07-01, rounds 1-3) and its
[`plan-home-scopes-execution.md`](plan-home-scopes-execution.md) (phases
1-3 already shipped: `home`/`account` split, `bind`/`add` verbs, the
`knowledge.py` chain — inject → checkout → `brnrd kb` query). This page is
the missing piece: the actual page-by-page move of *this repo's own*
kb, which every prior round left as "dogfood as repo-kb for now."

## Why now, precisely

1. The maintainer asked directly, twice in the same thread: move the kb out
   of the repo; support per-account + per-repo (or account-only) storage;
   stand up the account-wide backing repo and hook it up; then delegate the
   actual heavy lifting to sub-runs.
2. **Confirmed live, not assumed:** `Gurio/brr` is a **public** GitHub repo
   (0 stars, created 2026-03-28, still being pushed to daily). Its committed
   `kb/log.md` and the account-dominion decision ledger already carry the
   maintainer's personal/family context (pregnancy, newborn, pets, a
   France move) and a €1k legal-budget figure, publicly, in git history,
   today. This plan does **not** scrub that — moving `kb/` out of the tree
   going forward only stops *future* exposure. Rewriting already-public
   history is a separate, maintainer-owned decision (flagged in-thread,
   2026-07-09) — don't fold it into this migration unprompted.

## What already exists (verified against code, not the kb prose)

- `account.py` / `knowledge.py` (this repo, branch
  `brr/kb-out-of-repo-migration-2026-07-09`, PR #307): `HomeContext` with
  `kind: "project" | "account"`; `account.repo_knowledge_path(ctx, label)` →
  `<home>/knowledge/repos/<slug>/`; `account.account_knowledge_path(ctx)` →
  `<home>/knowledge/_cross-repo/`; `account.knowledge_split_mode(cfg)`
  (`per-repo` default for account homes, `account-only` opt-out).
  `knowledge.sources()` / `render_injection()` / `search()` already resolve
  through this split. `prompts._read_recent_log` already falls back to the
  home-knowledge `log.md` when repo `kb/log.md` is absent.
- `knowledge.ensure_checkout()` already git-inits `<home>/knowledge/` as its
  own repo and clones it to `<worktree>/.brnrd-kb/` (gitignored via
  `git/info/exclude`, never committed to the project).
- `account.py`'s home `.gitignore` now excludes `/knowledge/` (this branch —
  a real nested-repo bug, found and fixed while grounding this plan: the
  home's own git tracking would otherwise have swallowed `knowledge/`'s own
  `.git` the moment it materialized).
- **Verified live** against the maintainer's own account
  (`acc_bdda426da378d4f0c3cad2eb`): `Gurio/brr` already resolves to that
  account home (connected via `.brr/gates/cloud.json`, not `.brr/config`).
  `account.repo_label(".", cfg)` → `"Gurio/brr"` →
  `account.repo_knowledge_path(ctx, "Gurio/brr")` resolves to
  `<home>/knowledge/repos/Gurio__brr/`. That directory is this migration's
  destination. `<home>/knowledge/` itself is git-init'd and has `origin`
  pointing at **`hugimuni-labs/brnrd-knowledge`** (private, created this
  run) as its backing remote.
- **Not yet done anywhere**: the actual page move; `AGENTS.md`'s "Knowledge
  base" section still describes repo `kb/` as the default; `kb_preflight.py`
  still hardcodes `repo_root / "kb"` and returns `[]` (silently inert, not an
  error) the moment that directory is gone; `README.md` may reference `kb/`.

## What moves, what stays

Everything under `kb/` in this repo is internal design/decision/log
material — none of it is adopter-facing documentation (that's `README.md`
and any future public docs site, per round 2's audience split). So:

| Path | Destination | Why |
|---|---|---|
| `kb/*.md` (all 112 pages: `subject-*`, `decision-*`, `design-*`, `plan-*`, `research-*`, `index.md`, `log.md`, and the handful of one-off pages — `llm-wiki.md`, `notes-pondering-fleet.md`, `repo-dive-in-map.md`, `deck-brr-fleet-steering.md`, `diffense-prototype-pr64.md`, `review-*.md`) | `<account-home>/knowledge/repos/Gurio__brr/` (same relative filenames — this is a `cp -r`, not a re-sort) | Repo-specific design/decision tracking; exactly the "mostly for tracking design and ideas" the ask names. |
| `AGENTS.md` | stays in the repo (it's a symlink to `src/brr/AGENTS.md`) | Schema/contract must travel with the code for any AI tool reading the repo cold — round 1's own placement table already settled this. |
| `README.md`, any `docs/` | stays in the repo | Adopter-facing, not internal tracking. |
| Code (`src/brr/**`, `tests/**`) | stays, obviously | — |

Nothing moves to `_cross-repo/` in this pass — there is exactly one
registered repo today, so "cross-repo" material doesn't yet exist. Revisit
once a second repo (e.g. the hugimuni-website registrant named in
`design-multi-workstream-concurrency.md`) actually joins the account.

## Execution steps, in order

1. **Snapshot import first, garden second — two commits, not one.** Copy
   `kb/*.md` verbatim into `knowledge/repos/Gurio__brr/` inside the
   `knowledge/` repo (`git -C <home>/knowledge add -A && commit -m
   "Import Gurio/brr's kb/ verbatim, pre-gardening snapshot (from
   <project-repo-sha>)"`). This is the rollback point — if the gardening
   pass in step 2 loses or garbles something, the snapshot commit still has
   it byte-for-byte. Do not skip straight to a gardened copy.
2. **Garden, in the new location, as a second commit.** Apply the state-first
   principle `AGENTS.md` → "Knowledge base" already states for this kb:
   rewrite pages to describe current state, collapse changelog-style
   inline diffs into lineage breadcrumbs, re-check `index.md` graph
   reachability (every page linked from a subject heading), re-run the
   orphan/broken-link checks `kb_preflight.py` already implements (point it
   at `knowledge/repos/Gurio__brr/` for this one pass — see step 4). Bar
   for this pass: no worse than today, better where a page has visibly
   accreted (the "Plan-surface hygiene" pattern already named in
   `design-dashboard-live-surface.md` and the account-dominion `active.md`
   collapse-on-sight rule) — not a rewrite-everything project.

   Concrete, not hand-wavy — `kb_preflight.scan()` run live against this
   repo on 2026-07-09 already names the backlog, unrelated to this move but
   worth clearing while every page is open for editing anyway: 13 pages over
   the 32KB oversized-page threshold (`design-brnrd-protocol.md` 104KB and
   `design-diffense.md` 88KB are the extremes — real split-into-hub-plus-
   daughters candidates, not just trims), `decision-cli-shape.md` missing
   its `Status:` marker, `design-brand-visual-language.md` flagged
   revision-history-heavy (8 running-diff phrases to collapse into
   breadcrumbs), and two index sections (`§Research`, `§Reviews`) with
   enough material for a subject hub that doesn't exist yet. Fix what's
   cheap in the same pass; leave a note here for what isn't rather than
   silently dropping it.
3. **Push the `knowledge/` repo** to `hugimuni-labs/brnrd-knowledge` (`git -C
   <home>/knowledge push -u origin main`). Private remote already
   configured as `origin`.
4. **Update the code that assumed repo `kb/`:**
   - `src/brr/kb_preflight.py::scan()` hardcodes `repo_root / "kb"`. Repoint
     it at whichever knowledge source is actually live for this repo
     (reuse `knowledge.sources()`'s resolution instead of a second
     resolution path) rather than leaving it silently inert. If that
     rework is bigger than fits cleanly here, it is acceptable to leave it
     as a **named, explicit gap** in this same PR's description (inert-not-
     broken, per its own skip-fast contract) — do not silently leave it
     unmentioned.
   - `src/brr/prompts.py` already has the `_read_recent_log` fallback
     (this branch) — no further change needed there, just verify it
     actually fires once `kb/log.md` is gone from the tree (add/adjust a
     test with a real repo fixture if the existing ones don't already
     cover "repo kb/ absent, home knowledge present").
   - `AGENTS.md` → "Knowledge base" section: rewrite to describe the actual
     current shape — home-knowledge-first (this repo dogfoods "no repo
     kb/"), `knowledge.py`'s inject → checkout → query ladder, and that a
     *new* adopter repo still gets the choice (`brr init --with-repo-kb` or
     equivalent, per round 1's placement table) rather than losing the
     portable-wiki path entirely. Don't just delete the section — replace
     it with what's true now.
   - `README.md`: fix any `kb/`-as-committed-directory claims.
5. **Remove `kb/` from the project repo.** `git rm -r kb/` in one commit
   whose message points at the new location (`<home>/knowledge/repos/
   Gurio__brr/`, plus the `hugimuni-knowledge` remote) so `git blame`/`git
   log -- kb/` on this repo still tells a future reader where it went. No
   stub file left behind — the redirect lives in `AGENTS.md` (step 4), not
   in a `kb/README.md` ghost.
6. **Verify.** Full `pytest -q` green. `brnrd kb "<term known to be in a
   moved page>"` (the existing CLI query rung) returns a hit from the new
   location. A fresh clone of `Gurio/brr` has no `kb/` directory and
   `AGENTS.md` correctly describes where the knowledge lives now.
7. **Update PR #307** (this branch) with the final diff rather than opening
   a second PR — this is one story, not several small edits accreting
   across the week.

## Deliberately deferred, named so it doesn't read as forgotten

- **`kb_preflight.py`'s full repoint** (step 4's escape hatch) if the
  mechanical move doesn't leave room to do it properly in the same pass —
  the scanner's link-resolution helpers (`_resolve_relative`, the
  `_check_broken_links` family) assume `repo_root`-relative paths
  throughout; making them work against an arbitrary `kb_dir` outside the
  project repo is a real, separate, well-scoped follow-up, not a rename.
- **Cross-repo (`_cross-repo/`) population** — nothing qualifies yet with
  one registered repo.
- **Git-history scrub of `Gurio/brr`'s existing public exposure** —
  maintainer's call, flagged in-thread 2026-07-09, not part of this
  migration's scope.
- **A public docs site** (round 2's "audience split") — separate, larger
  project; this migration only handles the *private* side of that split.

## Read these next

1. [`design-home-scopes-and-knowledge.md`](design-home-scopes-and-knowledge.md)
   — the accepted direction this executes.
2. [`plan-home-scopes-execution.md`](plan-home-scopes-execution.md) —
   phases 1-3 (already shipped) that this plan's Phase 3 knowledge-chain
   code builds on.
3. [`subject-kb.md`](subject-kb.md) — the kb schema/graph rules this
   migration's "garden" pass (step 2) has to keep honoring, just in a new
   physical location.
