# License overview

brnrd is open source and intends to be adopted, forked, embedded and vendored.
It is also a business. Those two facts are reconciled by *where* the license
boundary sits — not by how permissive it is.

Copyright (C) 2026 HugiMuni SAS.

| Path | Component | License |
|------|-----------|---------|
| repo root `LICENSE` | the daemon core (the installed tool) | MIT |
| `src/brr/` (`src/brr/LICENSE`) | daemon core — the local runtime substrate | MIT |
| `src/brnrd/` (`src/brnrd/LICENSE`) | managed backend (`brnrd[backend]` extra) | AGPLv3 |
| `src/frontend/` (`src/frontend/LICENSE`) | dashboard | AGPLv3 |

`pyproject.toml` declares this as SPDX `MIT AND AGPL-3.0-only`; that metadata,
not this table, is what a license scanner reads.

In practice:

- The wheel contains both Python import packages (`brr`, `brnrd`). Extras
  select runtime dependencies, not which licensed source files are present.
  `src/frontend/` is a separate SvelteKit project, built and deployed on its
  own — it never ships inside the wheel.
- `pip install brnrd` installs what the MIT daemon core needs. **What you run on
  your own machine is MIT.** Fork it, vendor it, embed it in a proprietary
  toolchain — no obligations beyond attribution.
- `pip install brnrd[backend]` adds the AGPLv3 backend and dashboard. Running a
  *modified* backend as a network service triggers the AGPL's
  source-availability obligation. Running the published code — self-hosting for
  yourself — does not.

Inbound contributions carry the license of the package they touch. No CLA.

## Why the split, and why it is not the Elasticsearch story

The obvious question — *why not unify the licenses now and avoid an
Elastic/OpenSearch mess later?* — has the answer backwards, and it is worth
writing down once.

Elastic's mess was not caused by having a license boundary. It was caused by
**not having one.** Elasticsearch shipped Apache-2.0 across the whole product,
including the server that was its commercial surface. When a hyperscaler
rehosted that server as a managed service, Apache-2.0 offered no defense — so
Elastic relicensed to SSPL, *after* a decade of adoption, under users who had
already built on the old terms. **The relicense is what triggered the fork.**
AWS took the last Apache-2.0 commit and became OpenSearch; the product split,
the community split, and the trust cost all followed from that single moment.

The lesson is not "avoid two licenses." It is:

> **Never be permissive on the surface you intend to charge for, and never
> change the terms after people have adopted you.**

A boundary drawn *before* adoption costs nothing. A boundary drawn *after*
adoption costs the community.

So the split here is not a step toward the Elastic problem — it is the device
that makes it structurally unnecessary:

- The competitively exposed surface (backend + dashboard) is **AGPLv3 from day
  one, before adoption.** The managed-service clone is already answered. There
  is no future moment where we discover we are defenseless and have to change
  the terms under anyone. The relicense that forked Elastic is a move we never
  need to make.
- The surface we *want* copied (the local daemon) is **MIT** — the strongest
  adoption signal available, on exactly the code we want living inside other
  people's toolchains.

Unifying would mean choosing one of two worse positions:

- **MIT everywhere** is Elastic's *starting* position: permissive on the
  commercial surface. It buys goodwill now and hands us the same forced
  relicense in a few years — the expensive kind, with users already onboarded.
- **AGPL everywhere** puts copyleft on the local tool, which is exactly where we
  want zero legal friction. It would send every corporate user to a legal review
  before they can `pip install` us, to defend a surface that was never exposed.

AGPLv3 rather than BUSL / SSPL / ELv2 is the same reasoning once more: AGPL is
OSI-approved, so a company's legal team already has a policy for it. It buys the
anti-rehosting defense without the "source-available, not open-source" fight.
The defense is real; the community cost is near zero.

Alternatives considered, plus the pricing and trademark posture that sits beside
this: `decision-licensing-and-defense.md` in the knowledge base.
