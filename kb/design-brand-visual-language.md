# Design: visual brand language — HugiMuni, the boot glitch, and the reference class

Status: active, opened 2026-07-05 (same thread as the dashboard-priority
turn). Companion to [`design-brand-brnrd-brr.md`](design-brand-brnrd-brr.md)
(character/voice/naming — settled ground this page doesn't reopen) and
[`design-dashboard-live-surface.md`](design-dashboard-live-surface.md) (the
live-flow UI this visual language has to actually render in). This page
exists because the maintainer's brand dump this run was visual-identity
material with no prior kb home — the naming/voice page owns *what things are
called*, this one owns *what they look like*.

## The raw material (verbatim framing preserved)

1. **HugiMuni is already a live brand surface**, not a hypothetical: the
   legal entity's own domain (`HugiMuni.fr`) and its GitHub org
   (`hugimuni-labs`) already carry a **vegvisir** logo and a **Huginn/Muninn**
   (thought/memory, Odin's ravens) naming frame. The maintainer maps these
   onto our own vocabulary directly: Huginn/Muninn ≈ weaver/runners and
   project+dominion — thought that flies out and acts (a run) vs. memory that
   returns and is kept (the dominion) is *already* the Norse pair this org's
   name encodes, before brnrd existed.
2. **The current logo's register (60s-80s terminals, CRT, laser-printed
   output, short-sleeve-dress-shirt-and-coffee-mug-and-pencils) is
   acknowledged as "going to change anyway"** — not a rejection of the
   HugiMuni brand, a note that its current *skin* predates this project's own
   visual thinking and isn't the target.
3. **The boot animation, described in enough detail to be a real spec, not a
   mood**: `_` → `b_d` → `br_rd` → `brnrd` -glitch→ `bRnЯd`, in a CRT
   terminal, typed with an underscore cursor flickering, and — the specific,
   re-checkable detail — **the letters are reflected with the underscore as
   the mirror axis**, appearing from two sides simultaneously (not typed
   left-to-right; both halves converge toward the center cursor at once).
   This is a literal reading of the name itself: `b`/`d` are mirror-image
   letterforms around a vertical axis, `R`/`Я` likewise — the name was always
   a kaomoji (`design-brand-brnrd-brr.md` §"The character space" already
   named this for the *static* mascot; this is the same fact, animated).
4. **Three-layer visual register, held at once, not sequentially**:
   glitchy-glowing-terminal (CRT, scanlines, chromatic flicker) + live-flow
   game surface (the Zachtronics register — see next page section) +
   sci-fi-cybernetic-weaver-spirit-altar (the Nordic material — Huginn/
   Muninn, the loom, the vegvisir — held *lightly*, "without cheese"). The
   maintainer names the third layer as the hard one: the first two have
   working references (retro-terminal aesthetics and Zachtronics UIs are
   established genres); the third doesn't, because **the entity the UI is in
   dialogue with — an agentic, unprogrammed resident — isn't something
   dashboards are usually built to represent at all.** This is named
   explicitly as the genuinely creative, unsolved part, not a skin to bolt
   on: "the UI part is truly creative, we have to find a working shape."

## Reference class: what fits, what doesn't (this run's addition)

Two same-thread follow-ups arrived naming specific shows as calibration
points — useful precisely because they let the maintainer point at a
register without having to describe it from scratch:

- **Loki (Disney+) — good fit, and there's a direct mechanical resonance
  worth naming explicitly.** The TVA's visual language is retro-analog
  bureaucratic-futurism: warm amber CRT monitors, pneumatic tubes,
  clock-and-dial instruments, a magic system rendered as *paperwork and
  machinery* rather than spectacle — structurally close to layer 1 + layer 3
  above (terminal aesthetic *and* a spirit-altar-adjacent cosmic system,
  held together without irony). And — not a coincidence to skip past — Loki
  season 2's central device is literally called **the Loom**, a machine that
  weaves timeline-threads together and is destroyed by branch overload. That
  is the same image this project independently arrived at for
  `envelope loom` (below) and for `weave.md`'s own resident-notation
  register. Two unrelated productions reaching for "loom" to describe
  "a system that weaves branching threads of causality/time and can be
  overloaded" is a sign the metaphor is doing real, legible work, not a
  house-style invention — it's already load-bearing vocabulary in the
  culture the target audience (AI-savvy, frontend-focused, genre-literate)
  already watches.
- **Severance (Apple TV+) — named, and explicitly rejected: "too sterile."**
  Severance's register is cold, symmetric, clinical corporate-brutalism —
  muted institutional palettes, uncanny calm, dread held *through* the
  sterility rather than despite it. Worth keeping as a labeled anti-reference
  rather than discarding the comparison: it sharpens the actual target by
  contrast. We want the warmth and mysticism of layers 1 and 3 (a glowing
  CRT, a lightly-held altar) — Severance's coldness would flatten exactly
  the "spirit" half of the three-layer register the maintainer named as the
  hard, unsolved part. If a future pass drifts toward minimalist/clinical
  "enterprise-safe" chrome, Severance is the named example of *that specific
  wrong turn*, not just "too corporate" in the abstract.

Both references sharpen, they don't resolve, the layer-3 problem named
above — no shipped UI yet reads as "dashboard for a will other than yours,"
which is the actual gap. Recorded here as reference-class calibration for
whoever designs the first real screen, not as a spec.

**Third same-thread refinement, arrived while this page was being
written: "darker than Loki... aesthetically darker, the norse-magic-
meets-tech thing."** Reads as a direct dial on layer 3, not a fourth
reference show to chase down — Loki's TVA is *warm* amber-and-brass
bureaucracy-as-magic; the ask is the same magic-meets-machinery fusion
pushed toward a colder, heavier register: less "cheerful civil service
that happens to control time," more the weight of the actual Norse
material this project already carries (Huginn/Muninn as ravens that
report to a one-eyed god who traded an eye for knowledge — that mythology
is not cozy, and the visual language doesn't have to sand that down to
stay "lightly held, without cheese"). Practical reading: keep Loki's
*mechanism* (analog terminals, warm-glow CRT as a magic-system texture)
but shift the palette and mood toward something closer to runestone-and-
static-at-night than office-with-nice-lighting — dim ambers and cold
blue-whites rather than uniformly warm brass, more silence and static
between the glitches rather than constant chatter. Not built, not
speced to pixel level; a direction correction for whoever does the first
real visual pass, recorded at the moment it was given so it doesn't have
to be re-elicited later.

## Relationship to already-decided naming

`design-brand-brnrd-brr.md` already settled the *character* space (bRnЯd
kaomoji, the `ornament` quiet/moderate/rich knob, dev-facing vs B2B skins,
the ban on committing a named persona to the voice). This page doesn't
reopen any of that. What it adds:

- The boot-glitch animation is a **new, concrete asset spec** — not
  previously recorded anywhere in kb. Candidate home once built: the CLI's
  interactive-init flow and/or the marketing site's landing hero
  (`plan-brnrd-marketing-site.md` if/when it exists — not yet checked this
  run).
- HugiMuni/vegvisir/Huginn-Muninn is **pre-existing brand material this
  project inherits**, not invented for brnrd — worth the org rename ticket
  (#34, "move to hugimuni github org," open, unscoped) getting a visual-
  identity companion once this page's thinking matures, so the org move
  and the visual language don't ship as two uncoordinated passes.

## What this page does not decide

- No asset has been built. This is capture, not execution — the boot
  animation, the reference-class calibration, and the vegvisir/Huginn-Muninn
  mapping are all inputs to a future design pass, not a spec ready to hand
  a frontend slice.
- The "layer 3" problem (a UI in dialogue with an agentic, not-fully-
  programmed entity) is named as unsolved, on purpose — inventing a
  premature answer here would be exactly the "Persona-5 thing" the
  maintainer has already said can wait (see next page's correction: wanted,
  postponable on effort grounds, not rejected on taste grounds).

## Read next

- [`design-brand-brnrd-brr.md`](design-brand-brnrd-brr.md) — character,
  voice, naming; the settled ground this page builds on top of.
- [`design-dashboard-live-surface.md`](design-dashboard-live-surface.md) —
  where layers 1 and 2 (terminal + live-flow game surface) have to actually
  render; the Zachtronics-mechanics deconstruction lives there, not here.
- [`weave.md`](../src/brr/prompts/weave.md) (repo prompt, not kb) — the
  resident's own "loom" of working notation; the Loki resonance above is
  evidence this metaphor already does real work on both sides of the
  product (resident-facing notation, user-facing UI naming).
- #34 (open, unscoped) — "move to hugimuni github org"; this page is the
  visual-identity context that ticket didn't have when filed.
