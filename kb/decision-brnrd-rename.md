# Decision: rename the product to brnrd (brr → brnrd)

Status: **direction accepted** by the maintainer (evt-puhl, 2026-06-29);
**execution staged and partly open** (see sub-fork + migration below).

Amends [`decision-cli-shape.md`](decision-cli-shape.md) (which had `brr` and
`brnrd` as sibling binaries from one package, with `brr` primary). Rests on
[`decision-account-centered-daemon.md`](decision-account-centered-daemon.md)
(the shift that makes brnrd the natural center) and
[`decision-brnrd-repo-first-model.md`](decision-brnrd-repo-first-model.md).

## Context

Until now `brr` was the product/package/CLI name and `brnrd` was the hosted
*service* sibling — "brr the repo-based daemon, brnrd the service part." Two
shifts dissolve that split and make **brnrd** the right primary name:

1. **The daemon moved from repo-based to account/host-based**
   (`decision-account-centered-daemon.md`). "brr the repo-based daemon" is no
   longer the center of gravity; the account daemon — the thing with an identity,
   a service, a website — is. That thing is brnrd.
2. **External naming facts now favor brnrd** (the maintainer's drivers):
   - PyPI `brr` is **taken** (not us); `brnrd` is **free**.
   - GitHub identities `brnrd-dev` and `brnrd-bot` are **available** (the bot user
     and app the forge integration needs — see
     [`design-brnrd-github-bot-user.md`](design-brnrd-github-bot-user.md)).
   - The domain is **`brnrd.dev`**.

The brand, the package, the bot identity, and the website all want one name, and
that name can only be brnrd.

## Decision

**brnrd becomes the product / brand / package / service name.** `brr` is retired
as the *product* name.

## Open sub-fork (the maintainer's call) — what, if anything, `brr` remains

The maintainer flagged it: *"no reason to keep brr (apart from maybe a
runner-facing interface / cli?)."* Two coherent shapes:

- **(a) One name everywhere.** Retire `brr` fully: CLI `brnrd`, package `brnrd`,
  dominion `brnrd-home`, runtime dir `.brnrd/`. Cleanest brand; costliest
  migration (touches runtime-state names every install depends on).
- **(b) brnrd is the name; `brr` survives only as a short local/runner-facing
  CLI verb.** This maps cleanly onto the account-vs-repo split the architecture
  already drew: **brnrd** = the account daemon + hosted service + brand + bot +
  website; **brr** = the short local command you type / the resident- and
  runner-facing interface. One brand, one optional 4-char local alias — not a
  second product.

**Recommendation: (b).** Keep the brand single (brnrd) while letting `brr`
persist *only* as the local CLI ergonomic, because the costliest, most
state-entangled names are exactly the local/runtime ones — the dominion branch
`brr-home` and the runtime dir `.brr/` are load-bearing for **every existing
install's memory and in-flight runs**. Renaming brand/package/identity is worth
it; renaming those runtime-state names needs a migration shim or a deliberate
keep, and folding them into "the local `brr` surface" lets the brand rename land
without a flag-day on everyone's dominion. The maintainer decides (a) vs (b).

## Migration scope (honest — this is multiple dedicated wakes, not one sed)

Roughly, in increasing cost / risk:

1. **Brand + docs** (lowest risk): README, `kb/` prose (~89 pages mention `brr`),
   website copy, marketing. Mechanical, reversible.
2. **Package + CLI**: `pyproject` `name = "brr"` → `brnrd`; `src/brr/` →
   `src/brnrd/` (breaks imports — careful, well-tested); CLI entry point. Under
   (b), `brr` stays as an alias verb.
3. **Identity + infra**: claim PyPI `brnrd`, GitHub `brnrd-dev` / `brnrd-bot` +
   the GitHub App, `brnrd.dev`.
4. **Runtime-state names** (highest risk, gated on the sub-fork): `brr-home`
   dominion branch and `.brr/` runtime dir. Renaming these is **state-touching**
   for every install — needs a migration/compat shim or a deliberate keep (the
   (b) recommendation keeps them under the local `brr` surface).

Sequence: **lock the name + the (a)/(b) sub-fork first**, then land in staged,
reversible chunks (1 → 2/3 → 4-if-(a)). Don't blind-sed; runtime-state and
import renames need migration care.

## Consequences

- `decision-cli-shape.md`'s seven-verb structure (init / run / daemon / …) still
  holds — it just hangs under the `brnrd` command (with `brr` as the local alias
  under (b)), not under `brr` as primary. That page's "brnrd promoted to sibling
  binary" framing is superseded: brnrd is now the **primary**, not the sibling.
- The account repo proposed in `decision-account-centered-daemon.md` is named
  `brnrd-home` precisely because the account/service layer is brnrd-branded.
- No code is renamed in this wake — this page etches the direction and scope so a
  dedicated migration wake (after the maintainer settles (a)/(b)) executes it.
