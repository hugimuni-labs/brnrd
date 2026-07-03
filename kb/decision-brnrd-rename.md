# Decision: rename the product to brnrd (brr → brnrd)

Status: **direction accepted** by the maintainer (evt-puhl, 2026-06-29);
**sub-fork resolved to (b)** (evt-qhk6, 2026-06-29 — `brr` survives as the local
verb); **reversed to (a)** (evt-cayp, 2026-07-01 — *"we are deprecating the brr
command, we only gonna leave the brnrd command"*): retire the `brr` command
entirely, single CLI surface `brnrd`. The reconciliation that keeps (a) cheap —
separate the command name from the on-disk runtime dir, so `.brr/` can rename to
`.brnrd/` on its own schedule rather than as a flag-day — is recorded in
[`design-home-scopes-and-knowledge.md`](design-home-scopes-and-knowledge.md)
(round 2). Execution staged across dedicated migration wakes (see migration
below).

**Round-3 sharpening (evt-mo3g, 2026-07-01):** the maintainer flagged that (a)
still left `brr` alive as an *agent-facing compatibility layer* — the resident/
runner prompts (`run.md`, `daemon-substrate.md`, `identity-core.md`, `AGENTS.md`)
still address the resident as "brr". That prose is now in scope for retirement
too: `brnrd` everywhere the resident and runner are addressed, not only the
user-facing CLI. Justification he named: the [bind/add
model](design-home-scopes-and-knowledge.md) dissolves the "brr = repo-based local
daemon" concept, so nothing is left for the short verb to point at. The **only**
deliberate remnant is the on-disk `.brr/` runtime dir (a state migration, not an
agent-facing surface). The agent-facing prose pass is a dedicated wake — see
round 3 in the home-scopes design.

**Round-4 exploration (2026-07-01, closed 2026-07-02):** a proposal to revive
`brr` as *lore* — the in-fiction name for worker runs brnrd spawns. The
recommendation was to *reserve*, not adopt; the maintainer then withdrew the
revival outright ("not a real verb… just confusing — how the thoughts are
called doesn't matter, the essence is important"). `brr` stays retired; no
reservation held. The round-3 retirement is the final word. See
[`design-brand-brnrd-brr.md`](design-brand-brnrd-brr.md).

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

## Sub-fork RESOLVED — `brr` survives as the local verb (option (b))

The maintainer settled it (evt-qhk6, 2026-06-29): *"keep-brr-as-local-verb — I
agree, let's keep brr command (or brnrd brr, but brr is unlikely to collide with
the PyPI package, so it is a way)."* **Option (b) is chosen.** `brnrd` is the
brand/package/service/identity; `brr` persists as the short local CLI verb and the
resident/runner-facing surface, and it absorbs the costly repo-local runtime
names (notably `.brr/`) so the brand rename lands without a flag-day on every
install. The account-level home now tilts to `brnrd` naming because it is new
local-first state, not a pre-existing project branch. The PyPI-collision worry
is noted as low (the local `brr` verb
ships from the `brnrd` wheel via `[project.scripts]`; it is not a separate PyPI
package). The two shapes that were on the table:

- **(a) One name everywhere.** Retire `brr` fully: CLI `brnrd`, package `brnrd`,
  runtime dir `.brnrd/`. Cleanest brand; costliest migration (touches
  runtime-state names every install depends on).
- **(b) brnrd is the name; `brr` survives only as a short local/runner-facing
  CLI verb.** This maps cleanly onto the account-vs-repo split the architecture
  already drew: **brnrd** = the account daemon + hosted service + brand + bot +
  website; **brr** = the short local command you type / the resident- and
  runner-facing interface. One brand, one optional 4-char local alias — not a
  second product.

- **(b) is the chosen shape** (recorded above). The reasoning that carried it:
  the costliest, most state-entangled name is the repo-local `.brr/` runtime dir,
  which holds every existing install's in-flight runs. Renaming
  brand/package/identity is worth it; renaming that runtime dir needs a migration
  shim or a deliberate keep, and folding it into "the local `brr` surface" lets
  the brand rename land without a flag-day.

Note the cross-link to the account-dominion consolidation
([`decision-account-centered-daemon.md`](decision-account-centered-daemon.md) →
"Account-scoped store"): the maintainer also confirmed the resident's dominion
moves from a per-repo `brr-home` orphaned branch to a **per-account dominion
repo**. Because that account repo is new local-first state, the current naming
leans `brnrd-home` / "account dominion repo"; legacy `brr-home` survives only as
the old repo-local fallback.

## Migration scope (honest — this is multiple dedicated wakes, not one sed)

Roughly, in increasing cost / risk:

1. **Brand + docs** (lowest risk): README, `kb/` prose (~89 pages mention `brr`),
   website copy, marketing. Mechanical, reversible.
2. **Package + CLI**: `pyproject` `name = "brr"` → `brnrd`; `src/brr/` →
   `src/brnrd/` (breaks imports — careful, well-tested); CLI entry point. Under
   (b), `brr` stays as an alias verb.
3. **Identity + infra**: claim PyPI `brnrd`, GitHub `brnrd-dev` / `brnrd-bot` +
   the GitHub App, `brnrd.dev`.
4. **Runtime-state names** — **mixed** under (b): `.brr/` runtime dir stays as-is
   (no rename) to avoid touching in-flight project state; the new account home
   uses the `brnrd` namespace with a legacy fallback for earlier
   `$XDG_STATE_HOME/brr/...` account homes.

Sequence: name + sub-fork are now **locked** (brnrd brand; `brr` local verb), so
land in staged, reversible chunks: 1 (brand/docs) → 2/3 (package/CLI + identity,
with `brr` retained as alias verb). Step 4 is a no-op under (b). Don't blind-sed;
import renames in step 2 still need migration care.

## Consequences

- `decision-cli-shape.md`'s seven-verb structure (init / run / daemon / …) still
  holds — it just hangs under the `brnrd` command (with `brr` as the local alias
  under (b)), not under `brr` as primary. That page's "brnrd promoted to sibling
  binary" framing is superseded: brnrd is now the **primary**, not the sibling.
- The account repo proposed in `decision-account-centered-daemon.md` is named
  `brnrd-home` precisely because the account/service layer is brnrd-branded.
- No code is renamed in this wake — this page etches the direction and scope so a
  dedicated migration wake executes it. The (a)/(b) sub-fork is now settled (b),
  so a migration wake is unblocked to start at step 1 whenever scheduled.
