# Design: brand space — brnrd the character, brr the worker, the voice

Status: active — exploration opened 2026-07-01 from the maintainer's
five-part voice dump; naming/trademark forks are the maintainer's.
Companion product thesis: [`design-director-loop.md`](design-director-loop.md).
Voice implementation shipped this wake (see "The voice" below).

## The contradiction to name first

[`decision-brnrd-rename.md`](decision-brnrd-rename.md) round 3 (evt-mo3g,
**2026-07-01, the same day as this exploration**) retired `brr` from
*agent-facing prose* entirely: brnrd everywhere the resident and runner are
addressed, `.brr/` runtime dir the only deliberate remnant. The new proposal
revives brr as "the essence of repo-based runs" — worker incarnations brnrd
spawns ("brrs", bee-like, the money-printer-goes-brrr resonance).

These reconcile only if the categories are kept strict. Round 3 killed brr
as a **surface** — a command users type, a name prompts obey, a compat layer
that made two things claim to be the product. The revival proposes brr as
**lore** — a noun inside brnrd's own vocabulary for a thing it spawns. Those
can coexist:

- **Never again a surface:** no `brr` command, no brr-addressed prompt
  layer, no "brr vs brnrd" in install/docs. The round-3 cut stands.
- **As lore, when the referent exists:** "spawned two brrs for the sweep" on
  a card is charming and true *once worker runs are a visible product
  object*. Today they are subagents/respawns without a public face.

**Recommendation: don't re-introduce the word yet.** Adopting "brrs" now
names machinery users can't see, restarts the two-name confusion the rename
just paid to end, and burns the option cheaply. Park it as the *reserved
name* for worker runs, to be spent when the director loop
([`design-director-loop.md`](design-director-loop.md) phases 2–3) makes
spawned work user-visible. A name is a reveal; reveals are pacing. This is
the maintainer's call — it's brand, and it's his taste domain — but the
sequencing argument is architecture, not taste.

**Resolved 2026-07-02:** the maintainer withdrew the revival — "not a real
verb… just confusing; how the thoughts are called doesn't matter, the
essence is important." `brr` stays retired, no reservation held; worker runs
get named (or not) when they become a visible product object. Recorded in
[`decision-brnrd-rename.md`](decision-brnrd-rename.md) round 4.

## The character space

The raw material is good and unusually coherent:

- **bRnЯd as textual mascot.** The name is its own kaomoji: `b` and `d`
  hands, `R`/`Я` glasses-and-moustache head outline, rageously screaming or
  serenely working. Loading frames `bR\Яd → bR|Яd → bR/Яd → bR-Яd` are a
  one-line spinner that no competitor can trademark past us. It lives
  natively in every surface we already own: terminal, status card, chat
  message, dashboard header. Zero asset pipeline.
- **The CRT gear lineage.** The readme gif (yellow-glow CRT, glitchy gear,
  two-frame tilt) already set the register: retro-sci-fi, slightly deranged,
  tongue-in-cheek, ruthlessly efficient on small resources. The mascot and
  the gear are the same brand at two zoom levels.
- **Where it renders:** the card and CLI are the honest venues. The
  `ornament` knob in the identity core's appearance schema (`quiet |
  moderate | rich`) is the pre-built volume control — mascot animation is
  `rich`, static glyph `moderate`, off `quiet`. Enterprise screenshots run
  `quiet`. This resolves the cheese risk with a setting instead of a debate.

## Positioning: sell the pacing, skin the game

The Zachtronics/EVE/Marathon observation in the notes is the right
reference class, read correctly: people pay for *the fantasy of competent
operation* — manuals, dashboards, structured progress — not for XP bars. The
category claim must never be "coding is a game"; it is **"a colleague that
paces the work"** (steering, legible progress, decisions that matter,
receipts). Two skins on one engine:

- **Dev-facing** (site, readme, social, CLI): game-literate, winking,
  CRT-flavored. This audience recognises the Zachtronics lineage and buys
  it. The viral surface the maintainer wants lives here — the mascot
  reacting on a live card *is* the demo clip.
- **B2B-facing** (pricing page, security/enterprise copy): sober vocabulary
  for the same mechanics — orchestration, review gates, audit trail
  (`kb/log.md` as flight recorder), spend policy. Nothing renamed, only
  re-described; `ornament=quiet` in every screenshot.

The internal design vocabulary (director, reveal, quest log) stays internal
— per the notes' own pushback, the magic is pacing, not labels.

## The voice (implemented this wake)

The third voicing complaint was diagnosed and acted on: previous passes
*described* the target register (settled/dry/loyal, the maintainer's
TARS-BT/KITT/Mimir/Calcifer blend) while writing the prompts in a high-lyric
register — and a model absorbs the register of the prose more than its
claims. The wake scroll was raising every thought on incense and asking it
to be dry.

The fix shipped: `identity-core.md`, `run.md`, `daemon-substrate.md`, and
the playbook seed were rewritten *in* the register — shorter declaratives,
deadpan beats, candor about odds, no ceremony — with every operational
contract preserved and no named persona committed (the core's own rule; the
maintainer re-affirmed it: "abstract enough to be professional, at the same
time really strongly game"). The persona blend remains a steering reference
in conversation, never a committed character sheet.

The bicameral observation in the notes maps onto structure that already
exists: repo-owned voice (identity core + prompt layers — the product's
timbre) vs resident-owned voice (dominion playbook + notes — the lived
interpretation). That split shipped 2026-06-30; what was missing until this
wake was the repo side actually *sounding* like anything. Maintenance rule
going forward: **voice edits to the prompt layers are register work, not
content work** — done by a strong model, reviewed by ear, never delegated to
a model that can't hear the difference (the maintainer's constraint, and
correct).

## Forks left to the maintainer

1. **Reserve-or-adopt "brrs"** — recommendation above is *reserve*; adopting
   now re-opens round 3 of the rename a day after it closed.
2. **Trademark scope** — `decision-licensing-and-defense.md` already plans
   brr+brnrd filings post-launch; if brr is only reserved lore, the brnrd
   filing is the one that matters.
3. **Mascot as default ornament level** — `moderate` (static glyph) vs
   `quiet` out of the box. Taste call with onboarding implications.
