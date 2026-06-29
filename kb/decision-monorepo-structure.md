# Decision: monorepo structure for brr, brnrd, dashboard, envs

Status: accepted on 2026-05-26 (locked in PR #40 MR review).

The brr family lives in one repository and ships as one `brr` Python package
with optional extras. This keeps the daemon, brnrd backend, dashboard,
first-party envs, tests, deployment templates, and kb in one reviewable graph
while still letting optional runtime dependencies stay opt-in.

## Current Decision

The existing `brr` repo is the monorepo:

```text
brr/
├── src/
│   ├── brr/                    daemon core and built-in envs
│   │   └── envs/
│   │       ├── host.py
│   │       ├── worktree.py
│   │       ├── docker.py
│   │       ├── fly_machines/   first-party cloud env, extra: brr[fly]
│   │       └── ...
│   ├── brnrd/                  hosted backend
│   └── brnrd_web/              dashboard
├── deploy/                     deployment templates
├── kb/                         shared knowledge base
├── tests/
├── pyproject.toml
└── README.md
```

Install surfaces stay under one package name:

```text
pip install brr
pip install brr[backend]
pip install brr[fly]
pip install brr[backend,fly,modal]
pip install brr[all]
```

The dashboard is bundled with the backend extra and served by the backend. It
starts HTMX-first; if it becomes a larger SPA, its build step belongs in the
brnrd packaging and deploy pipeline.

Third-party envs use the existing `brr.envs` entry-point mechanism from
[`design-env-interface.md`](design-env-interface.md). First-party envs start
vendored under `src/brr/envs/<name>/` and gated by extras.

## Why This Shape

The kb is shared project memory. Splitting daemon, backend, dashboard, and envs
into separate repos would either fragment that memory or make one repo a stale
source of truth for the others.

The daemon cloud gate and brnrd server are two sides of the same protocol.
Protocol changes should be one diff, not a coordinated release across repos.
Single-maintainer iteration routinely crosses daemon, backend, dashboard, tests,
deploy, and kb; the monorepo keeps those changes reviewable together.

The single-package-with-extras surface is also intentionally boring Python:
users install only the optional dependencies they need, release coordination
stays one version, and first-party env discovery is simple. A separate package
name is reserved for envs whose maturity justifies the overhead.

## Package And License Boundary

The package layout is also the license layout:

- `src/brr/` remains MIT.
- `src/brnrd/` and `src/brnrd_web/` ship AGPLv3.
- First-party envs under `src/brr/envs/` follow the daemon's MIT posture unless
  a later env-specific decision says otherwise.

This split is defined in
[`decision-licensing-and-defense.md`](decision-licensing-and-defense.md). The
root license remains MIT; backend/dashboard directories carry their own AGPLv3
license files; a license overview explains the mixed tree.

## First-Party Env Graduation

First-party envs stay in-tree as extras until the operational cost says
otherwise. Split an env into its own `brr-env-<name>` repo and PyPI package when
one of these triggers is true:

- It needs an independent maintainer or release cadence.
- Its users diverge materially from brr core users.
- It grows large enough to dominate install or review footprint.
- Its tests dominate CI time for unrelated brr changes.

When a split happens, the new package registers through `brr.envs`. A
transitional brr release can either re-export the external package or remove the
extra in a clear breaking release, depending on how widely the in-tree extra was
used.

## Deploy Branch

The live brnrd.dev Upsun deployment runs from a public `deploy` branch off
`main`. Main keeps the generic deployment template under `deploy/upsun/` and
does not carry a root `.upsun/`. The `deploy` branch adds the root Upsun config,
preferably as symlinks back to the template; if the platform will not follow
symlinks, the sync step copies the files.

A GitHub Action can merge `main` into `deploy` and push, letting Upsun redeploy.
Secrets remain in Upsun's encrypted variable store. Keeping the deploy branch
public dogfoods the self-host template and avoids a second deploy-only repo with
cross-repo auth and version pins.

## Rejected Alternatives

- **Multiple repos from day one.** This fragments the kb, multiplies PRs for
  coupled changes, and adds release-matrix friction before the project has the
  scale to benefit.
- **Multiple PyPI packages inside one monorepo.** Separate names such as
  `brnrd`, `brnrd-web`, or `brr-env-fly-machines` create version skew and
  maintenance overhead without enough payoff for first-party components.
- **Workspace tooling such as Bazel, Nx, or Pants.** Too much process for a
  Python-first repo with light dashboard code. Revisit only if frontend or
  multi-language build complexity becomes a real constraint.
- **Keeping brr standalone and moving the SaaS layer elsewhere.** This preserves
  a pure daemon repo at the cost of splitting the protocol, deployment, and kb
  graph that are already conceptually one project.

## Open Follow-Ups

- Keep one CI job until backend/dashboard/frontend tests dominate runtime; then
  split jobs by surface.
- Add `src/brr/envs/README.md` when the first extras-gated cloud env lands,
  listing built-ins and optional envs with install commands.
- Write the self-hoster onboarding page with the first brnrd release: clone,
  install `brr[backend]`, configure, deploy from `deploy/upsun/`.
- Document the first env graduation when it happens, including history extract,
  entry-point registration, and transition behavior.

## Read Next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the hosted product
   shape that points back to this layout.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for the protocol that
   makes daemon and brnrd server co-resident.
3. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md) for the dashboard
   at `src/brnrd_web/`.
4. [`plan-env-fly-machines.md`](plan-env-fly-machines.md) for the first
   extras-gated first-party cloud env.
5. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   for the `deploy/` template shape.
6. [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md) for
   the license split the package boundary carries.

## Lineage

- 2026-05-25 - Drafted when brnrd, dashboard, and first-party cloud envs became
  concrete enough to need an explicit repository decision.
- 2026-05-25 - Shifted from separate first-party env packages to one package
  with extras after maintainer pushback that separate `brr-env-*` packages were
  over-engineered for first-party code.
- 2026-05-26 - Added the license-boundary callout tying `src/brr/` to MIT and
  `src/brnrd/` / `src/brnrd_web/` to AGPLv3.
- 2026-06-29 - Compressed from a proposal-shaped page into current-state
  synthesis; no decision changed.
