# Decision: monorepo structure for brr, brnrd, dashboard, envs

**Status: accepted 2026-05-26** (locked in PR #40 MR review).
Names the repo / package layout for the brr family of
components, so the brnrd backend, dashboard, and first-party
envs (including cloud envs) can grow alongside the daemon
without fragmenting the shared kb or inventing a multi-repo
release dance prematurely.

## Decision

**One monorepo (`brr/`), single pip-installable package with
optional extras.** The kb stays shared. The daemon core stays at
`src/brr/` (no path change for existing code). The brnrd backend
and dashboard land as siblings under `src/`. First-party envs
(including cloud envs like Fly Machines) live under
`src/brr/envs/` next to the existing built-ins
(`host`, `worktree`, `docker`), gated by pip extras for the
optional dependency footprint. Third-party envs use the existing
`brr.envs` entry-point mechanism per
[`design-env-interface.md`](design-env-interface.md).

```
brr/  (repo root, the existing brr repo)
├── src/
│   ├── brr/                    daemon core (today's location)
│   │   ├── envs/
│   │   │   ├── host.py         built-in (always available)
│   │   │   ├── worktree.py     built-in (always available)
│   │   │   ├── docker.py       built-in (always available)
│   │   │   ├── fly_machines/   first-party cloud env (extra: brr[fly])
│   │   │   ├── modal/          first-party cloud env (extra: brr[modal])
│   │   │   └── ...
│   │   └── ...
│   ├── brnrd/                  brnrd backend (FastAPI + workers + sandbox image build)
│   └── brnrd_web/              dashboard (HTMX templates first; SPA later if needed)
├── kb/                         shared kb (unchanged)
├── tests/                      tests for all sub-packages
├── deploy/                     shared deployment templates (Upsun first for brnrd; Fly / Render / VPS for daemon hosting)
├── pyproject.toml              single package with optional-deps groups
└── README.md                   monorepo overview
```

**Pip-install surfaces (all the same package, gated by extras):**

```
pip install brr                       # daemon core (today's UX, unchanged)
pip install brr[backend]              # + brnrd backend (FastAPI + workers + dashboard)
pip install brr[fly]                  # + Fly Machines env (one cloud)
pip install brr[modal,daytona]        # + multiple cloud envs (each its own extra)
pip install brr[backend,fly,modal]    # combine freely
pip install brr[all]                  # everything first-party
```

The dashboard (`src/brnrd_web/`) is not separately pip-installable —
it's bundled into the `brr[backend]` extra and served from the
backend's static-serve path. HTMX-first; if it grows into an SPA,
an `npm run build` step lands in the brnrd build pipeline.

**Why a single package with extras** (vs separate pypi names like
`brr-env-fly-machines`):

- Single version surface — no plugin/core version-skew bugs.
- One repo, one CI, one release.
- Discovery is trivial (`pip install brr[fly]` is obvious;
  finding a separate package on pypi is not).
- Third-party env authors still get the `brr.envs` entry-point
  path for true separation; first-party doesn't need that
  ceremony.
- Optional-dependency groups are well-established Python (used
  by every major package: requests, sqlalchemy, fastapi, etc.).
- Plugins that mature into their own thing can still split out
  later via the entry-point mechanism — the extras approach
  doesn't prevent that.

## First-party envs — when to split out

First-party envs stay vendored under `src/brr/envs/<name>/` as
extras until **any** of these triggers fires:

- The env has its own maintainer cadence (different release
  schedule, different bug-fix priority).
- The env's user base diverges from brr core (e.g. a cloud-
  provider-specific env that platform users adopt without using
  brr's other surfaces).
- The env grows >2k LOC and dominates the install footprint
  even when not selected.
- The env's tests dominate CI time for unrelated brr changes.

Splitting out moves the code to its own repo + its own pypi
name (`brr-env-<x>`); it registers via the existing `brr.envs`
entry-point mechanism (per
[`design-env-interface.md`](design-env-interface.md) §Registry).
Users install it independently: `pip install brr-env-<x>`. The
brr CLI discovers it the same way as third-party envs.

Until a trigger fires, extras-as-a-package is cheaper: shared
kb, shared CI, shared release coordination, shared shipping
discipline, no version-skew risk.

## Why a monorepo

Five reasons, in declining order of weight:

1. **The kb is a shared graph.** Splitting brr core, brnrd, and
   plugins into separate repos either forces the kb to live in
   one of them (the others lose visibility) or fragments the kb
   into N copies (closely-related ideas get split). Either is
   worse than a unified kb in one repo. The user's explicit
   constraint — "be mindful of the KB, ideally not splitting the
   closely related projects and ideas" — points at monorepo by
   construction.
2. **One contributor, many surfaces.** At brr's current scale,
   single-maintainer iteration crosses brr / brnrd / dashboard
   in a typical week. Multi-repo would mean N PRs for one
   conceptual change, with the change history scattered across
   repos. Monorepo lets a single PR touch the daemon protocol,
   the brnrd server, the dashboard view, and the docs in one
   reviewable unit.
3. **Tight conceptual coupling.** brnrd's protocol literally is
   the daemon's cloud-gate adapter contract. Changes to one
   require coordinated changes to the other. Same repo means the
   change is one diff; separate repos means coordinating two
   independent merges with version-pin gymnastics in between.
4. **Release coordination.** brnrd + the daemon's cloud-gate
   adapter need to ship compatible versions. Same repo, one
   `version.py`, one tag, one release. Separate repos means a
   matrix of which-version-works-with-which.
5. **Lower barrier to contribution.** A fresh contributor clones
   one repo, runs one test suite, reads one kb. Multi-repo means
   "ok, but which repo has the bit I care about?"

## License boundary aligns with the package boundary

The sub-package layout is also the license layout: the
daemon (`src/brr/`) ships under the existing permissive
license (MIT); the brnrd backend (`src/brnrd/`) and
dashboard (`src/brnrd_web/`) ship under **AGPLv3** as part
of the competitive-defense posture decided in
[`decision-licensing-and-defense.md`](decision-licensing-and-defense.md).
Per-package `LICENSE` files at `src/brr/LICENSE`,
`src/brnrd/LICENSE`, and `src/brnrd_web/LICENSE`; the
repo-root `LICENSE` stays MIT and a top-level
`LICENSE-OVERVIEW.md` documents the split. The `pyproject.toml`
optional-dependencies metadata declares the per-extra
license accordingly (`brr` = MIT; `brr[backend]` = AGPLv3;
envs follow the daemon's MIT). This is intentional and is
the reason the monorepo restructuring + the
licensing decision land in the same implementation window —
the package boundaries make the license split unambiguous,
and the license split is what makes shipping the backend
as OSS defensible against managed-service competitors.

## Why some envs eventually split out as separate repos

First-party envs vendored as extras eventually split because:

1. **Independent versioning matters per platform.** When the Fly
   Machines API changes and the Fly env needs a patch release
   tomorrow, the brr core release calendar shouldn't gate it.
   Independent repos = independent release cadence.
2. **Different user populations.** Someone who uses brr only
   with Codespaces shouldn't have to care about Fly's CI
   failing or its install footprint.
3. **Vendoring everything makes CI slow.** Once N cloud envs
   each have integration tests against real platforms, even
   gated extras + skip-tests-without-the-extra has a CI cost
   for every brr-core PR.
4. **Discoverability by `pip search`.** Independent packages
   get their own pypi page, README, install story — better
   discoverability for an env aimed at a non-brr-native
   audience (e.g. "the Fly Machines env that you can use
   standalone with brr").

The first-party split-out criterion (see above) is the
heuristic for when those benefits start outweighing the
shared-monorepo benefits. When a split happens, the env moves
to its own repo + its own `brr-env-<name>` pypi name, uses the
`brr.envs` entry-point registry, and the `brr[fly]` etc.
extras get a transitional release that re-exports the new
location (or drops the extra in a major version).

## What the existing brr repo becomes

The existing `brr` repo becomes the monorepo. No new git repo
created. Existing `src/brr/` location preserved (daemon code
doesn't move). New siblings:

- `src/brnrd/` for the brnrd backend (new).
- `src/brnrd_web/` for the dashboard (new).
- `src/brr/envs/<name>/` for each first-party env that needs
  optional dependencies (Fly first per
  [`plan-env-fly-machines.md`](plan-env-fly-machines.md);
  Modal / Daytona / etc. follow).

`pyproject.toml` updates to declare the single-package layout
with `[project.optional-dependencies]` groups for `backend`,
`fly`, `modal`, `daytona`, `all`. Build backend stays whichever
brr currently uses; extras work the same regardless.

`deploy/` already implied; this decision formalises it as the
home for both brnrd backend deploy templates (Upsun first;
Fly / Render / etc. follow per
[`plan-failover-compute.md`](plan-failover-compute.md)) and
daemon-hosting templates (per
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)).

**The live brnrd.dev deployment runs from a `deploy` branch, off
`main` (decided 2026-06-01).** Upsun (and most PaaS) want a
`.upsun/` config at the *repo root*; `upsun project:init` against
the monorepo drops one there — exactly the ops-in-the-OSS-tree
smell this layout avoids. Resolution: the canonical, generic Upsun
config lives in-tree under `deploy/upsun/` (a backend variant
alongside the daemon template), and `main` carries **no** root
`.upsun/`. A long-lived **`deploy` branch** adds the root config on
top — ideally a symlink (`.upsun/config.yaml` →
`../deploy/upsun/config.yaml`, and root `.environment` likewise) so
the live config *is* the published template, zero divergence; if
Upsun won't follow the symlink at build, the sync step copies it
instead. The config is fully env-derived (DB URL + base URL from
`PLATFORM_*` via `.environment`), so one file serves as both
template and live config.

**Public, autosynced by clean merge.** The deploy artifact is
public — secrets live in Upsun's encrypted variable store
regardless, so nothing sensitive sits in the branch, and a public
deploy path keeps the self-hoster `deploy/upsun/` template
dogfooded and bit-rot-free (brnrd's pitch is self-host parity). A
GitHub Action on push-to-`main` checks out `deploy`, `git merge`s
`main`, and pushes; Upsun redeploys on that push. Because the only
deploy-only content is the root symlink (which `main` never
touches), the merge is always conflict-free and needs no version
pin — the branch *is* the source at that ref; protect `deploy` so
only the Action writes it. (Supersedes an earlier sketch of a
separate deploy repo pinning `brr[backend]@<sha>` with an Action
bumping the SHA — the branch is simpler for an app that deploys its
own monorepo source: no cross-repo auth, no pin dance. Visibility
was weighed private vs public; public won on the dogfooding/parity
argument.) Template shape:
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md);
backend specifics:
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) → "Upsun
deployment notes".

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
- The split-out criteria for plugins don't apply to brnrd
  (which is conceptually inseparable from the protocol it
  serves).

### Alt 2 — Multiple distinct Python packages in the monorepo

`brr` (daemon), `brnrd` (backend), `brnrd-web` (dashboard),
`brr-env-fly-machines` (env plugin) — each its own pypi name
published from the same monorepo. Rejected because:

- Adds version-skew bugs at the boundaries (daemon vN +
  brnrd-env vM compatibility matrix).
- Multiplies pypi maintenance work (multiple package pages,
  multiple release flows, multiple READMEs to keep aligned).
- Discoverability problem (`pip install brr-env-fly-machines`
  vs `pip install brr[fly]` — the latter is obviously a brr
  thing).
- Doesn't buy enough at single-maintainer scale.

The single-package-with-extras approach
(`pip install brr[backend,fly]`) preserves the monorepo shape
AND keeps the pip-install surface minimal per use case AND
avoids version-skew. Third-party envs still get the entry-point
path for true separation; first-party doesn't need that
ceremony.

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
- The brr daemon's cloud-gate adapter and the brnrd server
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
- **First-party env discoverability.** A `src/brr/envs/README.md`
  enumerating built-in + extras-gated envs with one-line
  descriptions and the `pip install brr[<extra>]` invocation.
  Land with the first vendored cloud env (Fly).
- **Self-hoster's onboarding.** "Clone brr, install with
  `[backend]`, deploy with `deploy/upsun/`" should be the
  one-page README. Land that page with the first brnrd
  release.
- **First-party env split-out mechanics.** When a vendored env
  graduates to its own repo + its own `brr-env-<name>` pypi
  name, what's the migration story? `git filter-repo` extract
  the env's history, publish under the new pypi name, register
  via the `brr.envs` entry point in the new package, ship a
  transitional brr release that drops the in-tree env from
  `src/brr/envs/<name>/` (or keeps a deprecation shim importing
  from the new package) and removes the extra. Document in
  this page when the first graduation happens.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   "where the code lives" section that points back here.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for
   the protocol the daemon-side adapter and the brnrd server
   share — the tight coupling that makes monorepo right.
3. [`plan-failover-compute.md`](plan-failover-compute.md) for
   the brnrd backend's first major feature, all of which lives
   at `src/brnrd/`.
4. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   for the dashboard at `src/brnrd_web/`.
5. [`plan-env-fly-machines.md`](plan-env-fly-machines.md) for
   the first cloud-runner plugin, which lands vendored at
   `src/brr_env_fly_machines/`.
6. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   for the `deploy/` folder shape.
7. [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md)
   for the per-package license decisions (MIT daemon + AGPLv3
   backend / dashboard) the package boundary enables.

## Lineage

- 2026-05-25 — drafted as part of the managed-mode reshape
  that introduced the brnrd backend, dashboard, and the first
  plugin into the project shape. The user's explicit preference
  for "reasonable monorepos, mindful of the kb" pushed this
  page over the threshold from implicit assumption to explicit
  decision. Initially used `src/brr_run/` and `src/brr_run_web/`
  as the sub-package names; renamed to `src/brnrd/` and
  `src/brnrd_web/` later the same day when the hosted-product
  name settled on `brnrd` (canonical domain `brnrd.dev`).
- 2026-05-25 (pass 4) — reshaped to a single-package +
  optional-extras model after the user pushed back on separate
  `pip install brr-env-*` pypi packages for first-party envs
  ("over-engineered for first-party; should be a plugin /
  component"). First-party envs now live under
  `src/brr/envs/<name>/` (next to existing built-ins
  `host`/`worktree`/`docker`) and ship gated by extras
  (`pip install brr[fly]`). Third-party envs still use the
  existing `brr.envs` entry-point mechanism — the registry from
  [`design-env-interface.md`](design-env-interface.md) was
  always plugin-capable; extras for first-party just removes
  the pypi-naming ceremony. Alt 2 (multiple-pypi-packages-in-
  monorepo) added to alternatives. First-party split-out
  criterion now describes the env-graduates-to-its-own-repo
  path more precisely.
- 2026-05-26 (license-boundary callout) — added "License
  boundary aligns with the package boundary" section noting
  that `src/brr/` stays MIT and `src/brnrd/` + `src/brnrd_web/`
  ship AGPLv3 per
  [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md).
  The monorepo restructuring PR and the per-package LICENSE
  files should land together so the split is unambiguous from
  the first commit that introduces the new packages. Read-next
  expanded with the licensing-and-defense decision page.
