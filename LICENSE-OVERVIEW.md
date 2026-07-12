# License overview

This repository is a monorepo whose sub-packages ship under different
licenses, aligned with the package boundaries below.

| Path | Component | License |
|------|-----------|---------|
| repo root `LICENSE` | the brnrd distribution's daemon core | MIT |
| `src/brr/` (`src/brr/LICENSE`) | daemon core | MIT |
| `src/brnrd/` (`src/brnrd/LICENSE`) | brnrd backend (`brnrd[backend]` extra) | AGPLv3 |
| `src/brnrd_web/` (`src/brnrd_web/LICENSE`) | dashboard | AGPLv3 |

What this means in practice:

- The wheel contains all three import packages; extras select their runtime
  dependencies, not which licensed source files are present.
- `pip install brnrd` installs the dependencies needed by the MIT-licensed
  daemon core.
- `pip install brnrd[backend]` additionally installs the dependencies needed
  to run the AGPLv3-licensed backend and dashboard. Running a modified backend
  as a network service triggers the AGPL's source-availability
  obligation; the permissive daemon core is unaffected.

The split is intentional: the package boundaries make the
license boundary unambiguous, and the AGPL on the backend is the
competitive-defense posture that makes shipping it as open
source defensible against managed-service competitors.
