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

## Norse warmth vs. cold, nature, and retro-engineering (2026-07-06)

Direct question from the maintainer, worth answering plainly rather than
leaving as mood-board vibes: "when you think of norse myths, do you see
cold, or warmth? maybe nature? maybe darkness? how does it connect with
retro engineering? what visual code do we speak?"

**Norse myth is not uniformly cold — that's a modern flattening of it.**
The actual material holds warmth and cold in the same frame, deliberately:

- **The hearth-hall against the outside dark.** Norse cosmology's default
  state is a small, firelit, timber mead-hall ringed by an enormous cold
  unknown (the sea, the ice, Jötunheim). The warmth isn't decorative — it's
  *earned* against the cold outside it, which is exactly the register
  layer 3 (§Three-layer visual register, above) is reaching for and not
  yet finding: not ambient warmth, warmth-as-refuge. A glowing terminal in
  a dark room is the same image with the furniture changed.
- **Fire is load-bearing, not absent.** Muspelheim (the fire-realm) is one
  of the two primordial poles the whole cosmology is built from (paired
  with Niflheim, the ice-realm) — the myths open with fire and ice meeting
  and *making* the world, not with cold alone. Runestones themselves were
  often painted in ochre/red pigment, not left as bare grey stone — the
  "ancient and cold" reading of Norse material is largely a modern
  museum-lighting artifact, not the source culture's own palette.
- **Nature is present but not gentle.** Yggdrasil (the world-tree,
  literally holding the nine realms in its roots/branches) is the
  organizing image of the whole cosmology — but it's gnawed by a serpent
  at its root and grazed by deer, decaying and alive at once. If nature
  shows up in the visual language, it should read as *structural* (a root
  system, a tree diagram, the kb's own graph shape) rather than
  ornamental foliage — and it should carry some wear, not be pristine.
- **Darkness is real and not resolved.** Ragnarök is foretold, not
  avoidable, and the myths don't pretend otherwise — this is the tonal
  argument for "darker than Loki" (already captured above): Loki's TVA
  plays its cosmic stakes for bureaucratic comedy; the actual Norse
  material holds the dread without defusing it. Huginn/Muninn's own
  master traded an eye for knowledge — the mythology's price for
  competence is literally a wound, which is a sharper, weirder image for
  "an agentic resident that knows things" than a clean sci-fi assistant
  trope would give us.

**Retro engineering is the actual bridge, and it's a closer fit than it
first looks.** Runes are not just "old letters that look cool" — they
were a real information-encoding technology: carved by hand into wood or
stone, requiring a craft to cut correctly and a trained reader to decode,
often deliberately obscured (bind-runes stacking several letters into one
glyph, ambiguous or riddling inscriptions meant to be worked out rather
than read at a glance). That is structurally the same relationship a
programmer has with assembly or bytecode — a low-level, effortful,
craft-gated encoding underneath the readable surface. **This is the
literal retro-engineering connection**: a CRT terminal rendering runic
glyphs isn't two unrelated aesthetics mashed together, it's one aesthetic
(hand-carved, craft-gated, information-dense encoding) skinned twice, a
thousand years apart. And it isn't a separate thread from the mascot
already specced above (§3, the boot-glitch animation) — it's the *same*
device: bind-runes routinely fuse several letterforms into one glyph
along a shared stem or mirror axis, which is exactly what `bRnЯd`'s own
`b`/`d` and `R`/`Я` mirror-letterform kaomoji already does. The maintainer
asking to "fold [bRnЯd] into the aesthetic and visual identity stream"
(2026-07-06 follow-up) is correct on the merits, not just as a filing
instruction: the mascot's mirror-axis *is* a bind-rune, read forwards
and backwards at once, and the boot animation (two halves converging on
a center cursor) is a bind-rune being carved in real time. The `weave.md`
register (coordinates, deltas, glyph marks in place of clauses) is
*already* runic in this precise sense — dense marks that carry meaning a
casual reader has to learn to parse, not
decoration. Naming that connection explicitly is new; the practice
already exists.

**Palette and texture, now partially shipped (2026-07-07):** the first
dashboard pass uses a warm void body canvas (`#0c0906`) with parchment text
(`#f3e8d8`), amber primary/heading labels for the "inside the hall" read,
stone chrome/meta/tracks instead of blue-slate, and sky as the cold
signifier for stale reports and links. That makes the old proposal concrete:
hearth/ember is the working state; frost/sky is outside the firelight
(stale, unauthenticated, inactive).

**Status palette reconsidered, 2026-07-08** (direct maintainer ask, same
message that asked for more CRT glow): the line above — "fixed traffic-
light status colors remain unthemed" — is superseded for `WindowTrack`'s
quota-level scale specifically. On inspection those three hexes
(`#0ca30c`/`#fab219`/`#d03b3b`) were never a deliberate brand call; they're
the dataviz skill's own generic reference-palette status defaults
(`palette.md`), never actually reskinned when everything else got the
2026-07-07/08 pass — closer to an oversight than a held position. The
skill's real rule (`references/color-formula.md` §"Status is fixed") is
narrower than "never touch the hue": status stays a small fixed scale,
distinct from *categorical/series* slots, always icon+label — it doesn't
forbid a design system giving its status scale its own brand-appropriate
hues, it just can't borrow the theme's identity-carrying colors wholesale.
Reskinned to `ample = #e8b34a` (hearth amber), `low = #7aa9c2` (frost,
cooling — deliberately dimmer/less saturated than the `sky-300` "stale
report" badge in the same card, so "resource low" and "report stale" read
as related-but-distinct, not one recolored hue meaning two things), and
`critical = #c0523f` (dying ember) — "darkness" itself isn't reachable as
a legible foreground color on this dark-void surface (a true near-black
fails the skill's own ≥3:1 contrast floor), so critical reads as the
warmth *going* rather than *gone*; the void does the rest of that work as
background. All three checked against body/panel/track surfaces via
`scripts/validate_palette.js`'s `contrast()`, ≥3.7:1 throughout. `low`/
`stalling` 2-state badges in `LiveRuns.svelte`/`PRReviewQueue.svelte`
still carry the old unreskinned `#0ca30c`/`#fab219` pair — named, not
fixed this pass (out of the scope actually asked: "the quota percentage
bars" by name).

Also nudged up one notch, same message: the scanline/hearth-glow texture
(`body::before`), a soft phosphor `box-shadow` bloom on `.panel`'s
bracket chrome, a resting glow on `.boot-glitch` (previously only lit
during the flicker animation), and a matching glow on the quota bar
fill/dot themselves — literal CRT phosphor on the gauge, tying both asks
together in the one element they overlap on. Still meant to recede per
this page's own "held lightly, without cheese" register, not a filter
demo.

Still only proposed: runic/bind-rune-style glyphs as a *display* treatment
for the weave's own mark channel (✓ ✗ ? → Δ). The marks already exist and
already carry meaning; a bind-rune rendering would be skin over a real
semantic channel, not new invention.

### Reference check: psyche.network (2026-07-07)

Maintainer-supplied reference, checked directly rather than taken on
description: [Nous Psyche](https://psyche.network/runs)'s training-runs
page — a distributed-compute network's live dashboard. His own read: "almost
matches my vision... needs [this page's own dim-amber/hearth proposal above]
applied [to it]." Precise about which half of the match is real: the
*mechanic* is a strong hit — a card grid, one card per run, each carrying a
live token-count, a bounded progress bar, and a status badge
(`PAUSED`/`WAITING FOR COMPUTE`/`COMPLETED`) — structurally the same shape
[`plan-loom-realtime-build.md`](plan-loom-realtime-build.md) slice 2 (live
runs as SpaceChem-molecule cards, not a plain list) is already scoped to
build, independently arrived at. The *palette* is not a match and
shouldn't become one: psyche renders in a light mint-green paper theme,
which is their brand, not this page's hearth-vs-frost structure — the
card/progress-bar/status-badge mechanic is the reusable idea, the color
answer stays this page's own dim-amber/ember-vs-cold-blue-white-and-void
proposal above, unchanged. Cross-linked so slice 2's implementer has both
halves in one place instead of re-deriving the mechanic from a screenshot
with no note attached.

**Sharpened same-thread, 2026-07-07 (run-260707-1849-hnj8):** the
maintainer went further than "keep our palette" — psyche is "very
superficial" as a reference, container/element-outline only; "the
substance and the colors and the composition, almost everything, should
be loom-focused." Read as tightening, not contradicting, the line above:
don't treat psyche as a design partner to reconcile with, treat it as
proof a card-grid-with-status-badges *shape* reads as live-and-legible,
full stop. Slice 2 kept the existing dashboard slate palette deliberately
to avoid a partial recolor; slice 3 then shipped the hearth/frost chrome
across all live lanes in one pass (`WindowTrack`, `LiveRuns`,
`PRReviewQueue`, and `RunLedgerReceipt`). That does not make psyche the
palette source — it closes the open "loom-focused composition" step this
page already named.

## What is privy.io, and does it fit here? (asked same thread)

Checked directly rather than guessed: [Privy](https://www.privy.io/) is an
embedded-wallet and authentication SDK — email/phone/social-login/passkey
onboarding plus non-custodial crypto wallets generated inside a Trusted
Execution Environment (2-of-3 Shamir-split keys), aimed at Web3 apps that
want users to never touch a seed phrase. It was **acquired by Stripe in
June 2025**, and its stated use cases now explicitly include "AI and
Onchain Agents" — agents holding their own wallet/identity, not just
humans. ([privy.io](https://www.privy.io/),
[privy.io/wallets](https://www.privy.io/wallets),
[dextools.io Privy guide, 2026](https://www.dextools.io/tutorials/what-is-privy-embedded-wallet-auth-guide-2026))

**Direct fit today: none.** brnrd's billing is Stripe-subscription-and-
wallet-credits, already decided and shipped
(`decision-pricing-shape.md`), with no crypto/stablecoin rail anywhere in
that model — adopting Privy now would be solving a problem brnrd doesn't
have (it doesn't do Web3 auth or want users managing wallets).

**Where it's actually interesting**: Privy's "AI and Onchain Agents" use
case is agents holding *their own* spending identity — a genuinely
resonant idea for a future where a brnrd resident might need to transact
autonomously (buying its own compute, paying a provider directly, the
"own quotas and credits" endgame named in
[`design-quota-scheduling-loom.md`](design-quota-scheduling-loom.md)).
That's speculative and far out — not a near-term integration, and Stripe
now owning Privy means the two aren't really separate ecosystem bets
anymore anyway. Worth remembering the name exists for that future
question, not worth spending effort on now.

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

## Darkness dial: soft warm hearth vs. sharp cold-white edges (2026-07-08, resolved same day — see addendum below)

Direct ask, named explicitly as open: "amber/frost/darkness (the darkness
could be close to black but with sharp white outlines or text, I don't
have a clear vision yet, so open for discussion)." Checked against what's
actually live (code + a fresh authenticated Playwright screenshot of
`brnrd.dev`, not assumed from memory) before answering, since the palette
section above was written before the 2026-07-07/08 shipped pass and could
have drifted:

- **Void isn't black — it's warm near-black.** `layout.css` body is
  `#0c0906` (a brown-black, hearth-adjacent), not a true `#000`/near-`#000`
  neutral. Text is `#f3e8d8` (parchment/cream), not white. This is the
  *warmth* half of the hearth-vs-cold-outside frame (§Norse warmth vs.
  cold, above) — deliberately, not an oversight.
- **Structure is soft, not sharp.** Panel chrome (`.panel`) uses a
  low-alpha amber corner-bracket (`rgba(217,164,65,0.55)`) plus a blurred
  `box-shadow` phosphor bloom (inner + outer glow) — the "more CRT glow"
  ask from 2026-07-08 pushed this further toward soft, not less. Borders
  are a dim, low-contrast warm brown hairline (`rgba(120,95,55,0.28)`).
  Nothing in the current chrome is a crisp, high-contrast line.
- **Frost is a status accent, not a structural layer.** `#7aa9c2` only
  appears on `WindowTrack`'s `low` quota state. There's no cold/white
  presence anywhere in borders, panel structure, or body text today — so
  "amber/frost/darkness" as three peer registers isn't actually built yet;
  what's built is amber-primary-with-two-narrow-accent-states.
- **Live, confirmed gap while checking this** (screenshot, this run):
  `LiveRuns.svelte`/`PRReviewQueue.svelte` still hardcode the pre-reskin
  stock traffic-light `#0ca30c`/`#fab219` for their running/stalling
  2-state badges — the live "RUNNING" dot on the dashboard right now
  renders stock green, not any hearth/frost/ember hue. Already named as
  scope-excluded in the §Status palette reconsidered entry above ("named,
  not fixed this pass"), now visually confirmed rather than just
  code-read.

**The actual tension in the ask**: "near-black + sharp white outlines/
text" is a different *texture* from what's shipped, not just a darker
version of it. Soft blurred amber bloom + warm cream text is optimized
for "glowing CRT phosphor in a firelit hall" — the hearth-warmth half of
the Norse frame. Crisp white edges on a true near-black pulls toward a
harder, more graphic register — closer to a rune cut into stone and lit
by cold moonlight, or a woodcut/engraving: high-contrast line work, no
blur. Both are legitimate Norse-adjacent readings (hearth-warmth vs.
rune-in-the-dark), but stacking blur-heavy amber glow and crisp white
hairlines in the same panel would fight itself — recede-and-glow vs.
cut-and-declare are different jobs for an edge to do.

Three concrete directions, not decided here — a genuine aesthetic call,
named back rather than picked:

1. **Deepen warmth, don't sharpen it.** Push the void darker
   (`#0c0906` → something like `#070502`), keep parchment text and the
   soft bloom as-is, let frost spread into more structural surface
   (dividers, secondary borders) instead of staying status-only. Lowest
   effort, most continuous with what's shipped and already
   maintainer-approved (2026-07-07/08 passes).
2. **Split "structure" from "warmth" as two different edge treatments**
   (closest literal reading of "sharp white outlines"): keep amber +
   blur reserved for *alive/warm* signal only — active runs, healthy
   quota, headings, the boot-glitch mascot — and render *structural*
   chrome (panel borders, brackets, dividers) as crisp, un-blurred
   frost-white hairlines instead of dim amber-brown. Reads as three
   legible registers at once (amber = alive, frost-white = structure,
   near-black = void) rather than amber-primary-with-accents, and gives
   the "rune carved in stone" reading a real foothold — a carved line is
   sharp, the fire nearby is not. Medium effort: touches `.panel`/
   `.subpanel`/`.eyebrow` border and shadow rules, not the color
   variables wired through every component.
3. **Push further cold/graphic** (full sharp-white-on-black, amber
   demoted to a minor accent) — flagged as the direction most likely to
   drift toward the already-named-and-rejected Severance "too sterile"
   anti-reference if taken all the way; workable only if paired with
   real texture (grain, scanline, glow) to keep it from reading clinical.

Leaning recommendation: **(2)** — it's the only option that actually
builds "amber/frost/darkness" as three peers instead of amber-plus-
accents, it directly answers "sharp white outlines" literally rather than
metaphorically, and it's small enough to prototype on one component
(`WindowTrack`, already the most-iterated file) before committing across
the whole chrome system. Not started; this section is the discussion,
not a build.

### Darkness dial resolved: it was never a texture fork (2026-07-08, same day)

Maintainer follow-up, direct: "I lack frontend experience, so bear with
me... I still want all the warmth and blur, what I don't want: any
red/green color language... my comment about amber/frost/darkness was
more about color coding the states: amber instead of green, frost/bluish
instead of orange-warn, dark/void theme, with white/frost on edge/text for
readability/contrast instead of red-alert. So no cold/graphic."

The three-option framing above misread the ask — "sharp white outlines"
was never a bid to swap blur-and-warmth for crisp-and-cold (option 3,
correctly flagged as the Severance-ish risk, is explicitly what's *not*
wanted); it meant white/frost text reserved for *readability on a status
that needs to read clearly*, not a structural edge treatment competing
with the amber glow. Read against the code rather than the prose: this
maps almost exactly onto the three-tier semantic scale `WindowTrack.svelte`
already shipped 2026-07-08 (`ample`→amber `#e8b34a`, `low`→frost
`#7aa9c2`, `critical`→ember `#c0523f`) — the maintainer independently
re-derived the same hearth/frost/ember system from the live screenshot,
which is a real confirmation signal, not a coincidence to wave past. So
option (1) — deepen warmth, keep the blur, let frost do more work — was
the closer reading all along; option (2)'s structural crisp/blur split was
never the ask. Void could still go slightly darker/more retro-sci-fi per
"could be shifted even slightly more" in the same message, but that's a
dial to nudge later, not a fork to resolve now.

Closed the same run, not left as a second round-trip: extracted
`src/frontend/src/lib/statusPalette.ts` (`STATUS_GOOD`/`STATUS_WARN`/
`STATUS_CRITICAL`/`STATUS_UNKNOWN`, the exact hexes above) as the single
source, and wired `WindowTrack`/`LiveRuns`/`PRReviewQueue` to import it —
closing punch-list item 3 below. Worth naming precisely: `LiveRuns.svelte`
and `PRReviewQueue.svelte` weren't merely un-reskinned, their own comments
*claimed* "same three-tier palette as WindowTrack" while still hardcoding
the pre-reskin `#0ca30c`/`#fab219` stock hexes underneath — a false-parity
comment, not just a missed pass. One module structurally forecloses that
drift instead of relying on a future grep to catch it (the "palette drift
check" the maintainer's own visual-inspection run flagged as a portal
worth having, 2026-07-08 same day). Build+lint+typecheck clean.

### Critical was still red; swapped ember for void-ash; three registers now real (2026-07-08 evening)

Live report after merge+redeploy: "the 0 5h quota line is still red." Real,
not a caching/deploy artifact — checked by measuring, not eyeballing:
`STATUS_CRITICAL`'s "dying ember" hex (`#c0523f`) sits at roughly OKLCH
hue≈9°/sat≈51%, i.e. a genuinely red-orange hue at moderate saturation —
"ember" was a warmer *name* for a color that was still squarely in the
red/orange family the same-day pass had just banned everywhere else. The
bug was real, and it was exactly the family the maintainer named as
unwanted, just dimmer than the old stock `#d03b3b`.

Same message asked the sharper question directly: "your amber/frost/void 3
registers rather than amber with accents proposal" — i.e. option 2 from
§"Darkness dial" above, but read correctly this time: not a structural
crisp-vs-blur split (settled, rejected, stays closed), but *status* void as
a real third peer alongside amber and frost, rather than amber-primary with
frost and ember as two narrow accents hanging off it.

Answer, shipped not just discussed: yes, and the two questions turn out to
be the same fix. `STATUS_CRITICAL` is now `#9c8d7d` — desaturated warm-grey
ash (hue≈31°, sat≈14%), the fire spent rather than the fire turned red-hot.
Contrast checked against all three surfaces via the dataviz skill's
`validate_palette.js` `contrast()`: body 6.17:1, panel 5.85:1, track
5.43:1 — comfortably clearing the 3.7:1 floor the old ember hex barely
cleared (4.27/4.06/3.76). This is what makes "void" actually reachable as a
foreground hue without repeating the earlier finding that true near-black
text fails contrast on this surface (§"Status palette reconsidered,
2026-07-08" above): void does its work as *desaturation toward grey*, not
as literal near-black, while the dark body/panel canvas still carries the
darkness half as background. Net result, unprompted but real: amber (alive)
/ frost (cooling) / void-ash (spent) are now three actual peer meanings, one
hex each, not amber-primary-with-two-accents — the literal thing asked for,
arrived at by fixing a bug rather than by a separate restyling pass.

Structural chrome (panel borders, brackets, blur) is unchanged and stays
out of scope here — that fork was closed by the maintainer's own words in
the addendum above ("still want all the warmth and blur") and reopening it
wasn't what this question was about. Void nudging darker/more retro-sci-fi
overall (the still-open dial from the same addendum) is also untouched;
this is the status-color fix and the register question, not a full
re-tone.

Shipped: `src/frontend/src/lib/statusPalette.ts` (hex + comment rewritten
in place, not just a diff — the old ember reasoning is now wrong and
staying it would mislead the next reader), `WindowTrack.svelte`/
`LiveRuns.svelte` comment references to "ember" updated to "void ash" for
the same reason. `npm run check`/`lint`/`build` all clean. Self-merged
directly per the maintainer's own "feel free to self merge and evaluate
after a redeploy" — verify-then-merge, not a clean-diff-shaped guess (same
bar as the 2026-07-06 Upsun self-merges).

## Punch list: what's still open on the visuals (2026-07-08 check-in)

Asked directly: "what we still gotta address at the visuals." In order
of how load-bearing each gap is, checked against the live site and the
kb record above, not just recalled:

1. ~~The darkness-dial fork above~~ — **resolved same day** (§"Darkness
   dial resolved"): it was never a texture fork, keep warmth+blur, no
   cold/graphic. Void could still nudge darker/more retro-sci-fi later —
   a dial, not a blocker.
2. **Layer 3 stays unsolved, and stays the real one.** Both this page and
   `design-dashboard-live-surface.md` name it explicitly: bracket panels
   and a boot glitch are terminal/structural chrome, not an answer to "how
   does a dashboard represent dialogue with an agentic, not-fully-
   programmed resident." No shipped screen attempts this yet.
3. ~~Status-badge reskin gap~~ — **closed same day**: `LiveRuns`/
   `PRReviewQueue` now import `statusPalette.ts` (`WindowTrack`'s own
   hearth/frost/ember hexes) instead of the stock traffic-light values
   their comments falsely claimed parity with.
4. **Runic/bind-rune glyph treatment for the weave's own mark channel**
   (✓ ✗ ? → Δ) — still only proposed, never built, and still the most
   direct place this project's own "retro-engineering = runes" thesis
   (§Norse warmth, above) could show up as an actual asset rather than an
   essay.
5. **No brand typeface decision** — the whole dashboard still renders in
   system fonts; the visual-language pass explicitly left this untouched.
6. **No accessibility pass on the CRT texture** — scanlines/glitch/bloom
   have a `prefers-reduced-motion` guard on the glitch animation, nothing
   checked yet for screen-reader or low-vision contrast against the
   low-alpha borders and glow-heavy chrome.

## Read next

- [`design-brand-brnrd-brr.md`](design-brand-brnrd-brr.md) — character,
  voice, naming; the settled ground this page builds on top of.
- [`design-dashboard-live-surface.md`](design-dashboard-live-surface.md) —
  where layers 1 and 2 (terminal + live-flow game surface) have to actually
  render; the Zachtronics-mechanics deconstruction lives there, not here.
- [`weave.md`](../src/brr/prompts/weave.md) (repo prompt, not kb) — the
  resident's own "loom" of working notation; the Loki resonance above is
  evidence this metaphor already does real work on both sides of the
  product (resident-facing notation, user-facing UI naming); the runic
  bind-mark reading above is the same evidence a second way.
- [`design-quota-scheduling-loom.md`](design-quota-scheduling-loom.md) —
  the economics this visual language has to render alongside the
  Zachtronics motion (good/bad quota states, the hearth/cold-outside
  color mapping proposed above).
- #34 (open, unscoped) — "move to hugimuni github org"; this page is the
  visual-identity context that ticket didn't have when filed.
