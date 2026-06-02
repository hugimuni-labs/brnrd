# License overview

This repository is a monorepo whose sub-packages ship under
different licenses, aligned with the package boundary (see
[`kb/decision-monorepo-structure.md`](kb/decision-monorepo-structure.md)
and [`kb/decision-licensing-and-defense.md`](kb/decision-licensing-and-defense.md)).

| Path | Component | License |
|------|-----------|---------|
| repo root `LICENSE` | the brr daemon core distribution | MIT |
| `src/brr/` (`src/brr/LICENSE`) | daemon core | MIT |
| `src/brnrd/` (`src/brnrd/LICENSE`) | brnrd backend (`brr[backend]` extra) | AGPLv3 |
| `src/brnrd_web/` | dashboard (not yet present) | AGPLv3 when it lands |

What this means in practice:

- `pip install brr` installs only the MIT-licensed daemon core.
- `pip install brr[backend]` additionally installs the
  AGPLv3-licensed brnrd backend. Running a modified brnrd backend
  as a network service triggers the AGPL's source-availability
  obligation; the permissive daemon core is unaffected.

The split is intentional: the package boundaries make the
license boundary unambiguous, and the AGPL on the backend is the
competitive-defense posture that makes shipping it as open
source defensible against managed-service competitors.
