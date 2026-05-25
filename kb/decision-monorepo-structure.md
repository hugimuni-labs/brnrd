# Decision: monorepo structure for brr, brr.run, dashboard, plugins

**Status: proposed, not yet accepted on 2026-05-25.** Names the
repo / package layout for the brr family of components, so the
brr.run backend, dashboard, and first-party plugins can grow
alongside the daemon without fragmenting the shared kb or
inventing a multi-repo release dance prematurely.

## Decision

**One monorepo (`brr/`), multiple sub-packages.** The kb stays
shared. The daemon core stays at `src/brr/` (no path change for
existing code). The brr.run backend and dashboard land as
siblings under `src/`. First-party plugins start vendored under
`src/` and split into their own repos when they mature.

```
brr/  (repo root, the existing brr repo)
├── src/
│   ├── brr/                   daemon core (today's location, unchanged)
│   ├── brr_run/               brr.run backend (FastAPI + workers + sandbox image build)
│   ├── brr_run_web/           dashboard (HTMX templates first; SPA later if needed)
│   └── brr_env_fly_machines/  first cloud-runner plugin (vendored at first)
├── kb/                        shared kb (unchanged)
├── tests/                     tests for all sub-packages
├── deploy/                    shared deployment templates (Upsun first for brr.run; Fly / Render / VPS for daemon hosting)
├── pyproject.toml             multi-package config; sub-packages declared as optional-deps
└── README.md                  monorepo overview
```

**Pip-install surfaces:**

- `pip install brr` — the daemon core; today's user experience,
  unchanged.
- `pip install brr[backend]` — brr.run backend (FastAPI app +
  workers). Self-hosters use this.
- `pip install brr-env-fly-machines` — first-party plugin
  installable independently; lives in `src/brr_env_fly_machines/`
  but published as its own pypi name. Splits out into its own
  git repo when it matures.

The dashboard (`src/brr_run_web/`) is not pip-installable
directly — it's bundled into the brr.run backend's static-serve
path. HTMX-first; if it grows into an SPA, an `npm run build`
step lands in the brr.run build pipeline.

## Plugin packages — when to split out

Vendored at first (lives in `src/brr_env_*/` in the monorepo),
split into its own repo when **any** of these is true:

- The plugin has its own maintainer cadence (different release
  schedule, different bug-fix priority).
- The plugin's user base diverges from brr core (e.g. a
  cloud-provider-specific plugin that platform users adopt
  without using brr's other surfaces).
- The plugin grows >2k LOC.
- The plugin's tests dominate CI time for unrelated brr changes.

Until one of those triggers fires, vendoring is cheaper: shared
kb, shared CI, shared release coordination, shared shipping
discipline.

## Why a monorepo

Five reasons, in declining order of weight:

1. **The kb is a shared graph.** Splitting brr core, brr.run, and
   plugins into separate repos either forces the kb to live in
   one of them (the others lose visibility) or fragments the kb
   into N copies (closely-related ideas get split). Either is
   worse than a unified kb in one repo. The user's explicit
   constraint — "be mindful of the KB, ideally not splitting the
   closely related projects and ideas" — points at monorepo by
   construction.
2. **One contributor, many surfaces.** At brr's current scale,
   single-maintainer iteration crosses brr / brr.run / dashboard
   in a typical week. Multi-repo would mean N PRs for one
   conceptual change, with the change history scattered across
   repos. Monorepo lets a single PR touch the daemon protocol,
   the brr.run server, the dashboard view, and the docs in one
   reviewable unit.
3. **Tight conceptual coupling.** brr.run's protocol literally is
   the daemon's cloud-gate adapter contract. Changes to one
   require coordinated changes to the other. Same repo means the
   change is one diff; separate repos means coordinating two
   independent merges with version-pin gymnastics in between.
4. **Release coordination.** brr.run + the daemon's cloud-gate
   adapter need to ship compatible versions. Same repo, one
   `version.py`, one tag, one release. Separate repos means a
   matrix of which-version-works-with-which.
5. **Lower barrier to contribution.** A fresh contributor clones
   one repo, runs one test suite, reads one kb. Multi-repo means
   "ok, but which repo has the bit I care about?"

## Why some things still split out as separate repos

Plugin packages eventually split because:

1. **Independent versioning matters per platform.** When the Fly
   Machines API changes and brr-env-fly-machines needs a patch
   release tomorrow, the brr core release calendar shouldn't gate
   it. Independent repos = independent release cadence.
2. **Different user populations.** Someone who uses brr only for
   Codespaces shouldn't have to care about Fly's CI failing.
3. **Vendoring everything makes CI slow.** Once N plugins each
   have integration tests against real platforms, vendoring them
   all means every brr-core PR pays the integration-test bill.
4. **Discoverable by `pip search`.** Independent packages get
   their own pypi page, README, install story — better
   discoverability than "look in the brr monorepo for the env
   sub-package."

The first-party split-out criterion (see above) is roughly the
heuristic for when those benefits start outweighing the kb-graph
benefit of vendoring.

## What the existing brr repo becomes

The existing `brr` repo becomes the monorepo. No new git repo
created. Existing `src/brr/` location preserved (daemon code
doesn't move). New siblings:

- `src/brr_run/` for the brr.run backend (new).
- `src/brr_run_web/` for the dashboard (new).
- `src/brr_env_fly_machines/` when the first plugin lands (new,
  per [`plan-env-fly-machines.md`](plan-env-fly-machines.md)).

`pyproject.toml` updates to declare the multi-package layout via
`[tool.hatch.build.targets.wheel.shared-data]` or equivalent
(specifics TBD pre-implementation; whichever build backend brr
currently uses extends most naturally).

`deploy/` already implied; this decision formalises it as the
home for both brr.run backend deploy templates (Upsun first;
Fly / Render / etc. follow per
[`plan-failover-compute.md`](plan-failover-compute.md)) and
daemon-hosting templates (per
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)).

## Alternatives considered

### Alt 1 — Multi-repo from day one

`brr` (daemon), `brr-run` (backend), `brr-run-web` (dashboard),
`brr-env-fly-machines`, etc. — each its own git repo.

Rejected because:

- Fragments the kb (the user's explicit concern).
- Forces N-way PR coordination for tightly-coupled changes
  (protocol-on-both-sides).
- Adds release-matrix friction without buying anything at
  single-maintainer scale.
- The split-out criteria for plugins don't apply to brr.run
  (which is conceptually inseparable from the protocol it
  serves).

### Alt 2 — Monorepo with one Python package

Single `brr` package, sub-modules for `brr.run` and `brr.web`.
Rejected because:

- Forces `pip install brr` users to pull down the FastAPI /
  HTMX / DB dependencies they don't need.
- Conflates the daemon (which has minimal deps) with the
  backend (which needs a real web stack).
- Makes self-hosting brr.run harder to communicate ("install
  brr, but only some of brr").

The optional-dependency split (`pip install brr[backend]`)
preserves the monorepo shape while keeping the install surfaces
minimal per use case.

### Alt 3 — Monorepo with workspace tooling (Bazel / Nx / Pants)

Use real polyglot monorepo tooling. Rejected because:

- Overkill for the current scale (one human maintainer, three
  sub-packages, light JS in the dashboard at most).
- Adds a new tool to learn for any contributor.
- Hatch / setuptools sub-packages cover the actual need.

Worth revisiting if the dashboard grows into a real SPA and the
JS build pipeline starts demanding attention; not before.

### Alt 4 — Keep brr standalone; new `brr-run` repo for everything else

The "brr stays sacred; the SaaS layer lives elsewhere" framing.
Rejected because:

- Splits the kb (the user's concern again).
- Forces protocol changes to coordinate across two repos
  (back to the N-way PR problem).
- The brr daemon's cloud-gate adapter and the brr.run server
  are two sides of the same protocol; they want to live next to
  each other.

## Open questions

- **Build backend specifics.** brr currently uses [whichever
  build backend it's using — check pyproject.toml before
  implementation]. The multi-package layout works with hatch,
  setuptools, and PDM with minor variations; pick during the
  monorepo restructuring PR.
- **CI fan-out vs uniformity.** Should each sub-package have its
  own CI job, or one big job? Sub-package jobs are faster to fail
  but more complex to maintain. Start with one CI job; split when
  the dashboard's frontend tests start dominating runtime.
- **Plugin discoverability story.** A README in `src/` explaining
  the layout would help. Or a `plugins/` index page in the kb.
  Decide when the first vendored plugin (`brr-env-fly-machines`)
  lands.
- **Self-hoster's onboarding.** "Clone brr, install with
  `[backend]`, deploy with `deploy/upsun/`" should be the
  one-page README. Land that page with the first brr.run
  release.
- **Plugin split-out mechanics.** When a vendored plugin
  graduates to its own repo, what's the migration story?
  `git filter-repo` extract, plus a redirect note in
  `src/brr_env_X/README.md` pointing at the new repo. Document
  in this page when the first graduation happens.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   "where the code lives" section that points back here.
2. [`design-brr-run-protocol.md`](design-brr-run-protocol.md) for
   the protocol the daemon-side adapter and the brr.run server
   share — the tight coupling that makes monorepo right.
3. [`plan-failover-compute.md`](plan-failover-compute.md) for
   the brr.run backend's first major feature, all of which lives
   at `src/brr_run/`.
4. [`plan-brr-run-dashboard-mvp.md`](plan-brr-run-dashboard-mvp.md)
   for the dashboard at `src/brr_run_web/`.
5. [`plan-env-fly-machines.md`](plan-env-fly-machines.md) for
   the first cloud-runner plugin, which lands vendored at
   `src/brr_env_fly_machines/`.
6. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   for the `deploy/` folder shape.

## Lineage

- 2026-05-25 — drafted as part of the brr.run reshape that
  introduced the brr.run backend, dashboard, and the first
  plugin into the project shape. The user's explicit preference
  for "reasonable monorepos, mindful of the kb" pushed this
  page over the threshold from implicit assumption to explicit
  decision.
