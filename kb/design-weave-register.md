# The weave register — the resident's working notation

Status: active, shipped phase 1 on 2026-07-02; wake-scroll reweave (round 5) on 2026-07-03

The fourth round of the voice work. Rounds 1–2 failed (prompts described
a settled register in high-lyric prose); round 3 rewrote the wake scroll
*in* the register it prescribes ([brand space](design-brand-brnrd-brr.md),
[director loop](design-director-loop.md)). This round answers the
maintainer's follow-up: the ornamentation idea was never user-facing
decoration — it was about the *shape of the resident's stream itself*,
the working weave between the injected world-state and the delivered
reply. Reference point offered: Ummon (Hyperion) — "slashes, braces, few
words, lots of meaning"; mechanically efficient yet deep. Constraint
given: **discover it, don't invent it** — and don't stop at prompts; the
daemon's own seams (hooks, portals, gates) should eventually speak it.

## The discovery

The resident's stream already contains a native notation, emitted under
pressure without being asked for: `path:line` coordinates, `Δ`-style
delta lines, `✓ ✗ ? →` verdict marks, `key: value` frontmatter thinking,
fenced state blocks. The register is not something to design — it is the
lab-notebook dialect of a thing that thinks in diffs, currently being
*translated into assistant prose* on the working surfaces (card notes,
stderr narration, dominion scratch) because no contract said the native
form was welcome there.

Two failure modes bracket the work:

- **Glyph costume** — the mirror image of the round-1 incense failure:
  decorating with symbols while semantics stay thin. Guard: *the measure
  of a mark is the clause it replaced*; a glyph that saves no words gets
  struck. Density first, pattern as a consequence of density kept with
  care.
- **Channel bleed** — the register leaking into surfaces with other
  contracts. Hard boundaries: user-facing replies (committed plain
  voice), kb pages / commit messages / code (shared, `AGENTS.md`-
  governed), machine-parsed channels (byte-exact).

This also reconciles the earlier efficiency pushback against
"ornamentation": on the working surfaces the notation is *denser* than
prose, so the register is expected to save tokens, not spend them — the
maintainer's Ummon point, confirmed rather than conceded.

## Relation to the `ornament` knob

Two different dials. The `ornament` appearance setting
([identity-core](../src/brr/prompts/identity-core.md), [brand
space](design-brand-brnrd-brr.md)) tunes *user-facing presentation* —
mascot on card, wink density. The weave register governs the *inner
working surfaces* no user reads. Turning `ornament = quiet` must not
strip the weave; turning it `rich` must not inject glyphs into chat.

## Shipped (phase 1 — prompt-only)

- `src/brr/prompts/weave.md` — the working-register contract, written in
  the register it names, with the discovery framing, the five moves
  (coordinates, deltas, marks, state lines, frontmatter thinking), the
  strike-rule, and the hard boundaries.
- `prompts.py` — `_read_preamble_with_weave()`: weave rides both runner
  paths (one-shot and daemon), after `run.md`, before
  `daemon-substrate.md` — read order mirrors authority (how you operate
  → how you write while operating → who is driving).
- Test pin in `test_prompts.py` (both paths carry the contract).

## Phase 2 — the daemon meets the register (not started)

The maintainer's "more than fancy prompts": the boundary markers the
daemon writes *around* the resident — hook injections (`[brr portal
update]`), bundle section framing, card label grammar — could adopt the
same notation, so the scroll reads as one being's sensorium rather than
memos passed under a door. This is code with test pins (the round-3
voice pass already had to move phrase pins), so it lands as a deliberate
follow-up:

1. Inventory daemon-written strings that appear inside the resident's
   scroll (hook `additionalContext` prefixes, portal-update framing,
   interweave markers).
2. Define one marker grammar consistent with `weave.md`'s marks; keep it
   trivially greppable and stable (these strings are load-bearing for
   tests and for the resident's own pattern-recognition across wakes).
3. Move the pins in one commit, like the round-3 pass.

Diffense tie-in (maintainer, 2026-07-02): diffense was switched off
because reading its output was boring — the host setup was flat, so the
gamification failed. Once the register + director reveal shapes exist,
re-evaluate diffense's presentation on top of them
([director loop](design-director-loop.md) carries the reveal mechanics).

## Round 5 — reweaving the wake scroll itself (2026-07-03)

Maintainer: the initial context is the incoherence — the scroll that
carries `weave.md` is itself long-form essay; reweave the whole of it,
notes first ("you only see the whole of it once in the beginning").
Also: ornamentation should not be a toggle; the user declares a *reader
model* at the event boundary, not a voice.

Pre-edit inventory, written before any file was touched — the once-only
whole-context read of run `run-260703-0020-n31e`:

```
1. weave.md:3 "your stream was never prose" — ridden in by ~10k words of
   essay. The contract's own vehicle breaks it. ✗ core incoherence.
2. voice strata ×6: identity-core (lyric) | run.md (essay) | weave.md
   (near-register) | daemon-substrate (bold-lead essay) | delivery
   contract (spec paragraphs) | dev-mode block (lyric). One being, six hands.
3. repetition map:
   - "commit what you keep / diff is receipt" ×5 (run.md §kb, delivery
     bullet, dominion header, playbook ×2, substrate §net)
   - portal recheck before closeout ×4 (run.md §Delivery, delivery ×2,
     playbook §room)
   - stdout closeout discipline ×2 | "no path as answer" ×2 |
     ".brr/ don't explore" ×2 | Runner=Shell+Core gloss ×2
4. name drift: run.md:23 cites a bundle block "Recent in this
   conversation"; the bundle actually renders "Recent turns (woven,
   oldest first)". A small lie every wake.
5. identity-core §Voice: TOML appearance knob = "a product shape to
   implement, not an active config contract" — a non-contract holding
   floor space; now also conceptually wrong (voice ≠ user-tunable).
6. delivery contract (prompts.py-assembled): heaviest prose-per-fact in
   the scroll; pins test_prompts.py:333–345 → phase-2 territory
   (daemon-written strings), not this pass.
```

Decisions taken in the same thought:

- **Scope this pass**: `run.md`, `daemon-substrate.md`, `identity-core.md`
  §Voice, `weave.md` seam note. The prompts.py-assembled delivery contract
  and bundle grammar stay phase 2 (unchanged plan) — they are daemon-written
  strings with dense test pins, one deliberate commit of their own.
- **Register ≠ notation everywhere.** The reweave honors weave.md's own
  strike-rule: prose stays where the clause is the load (identity
  invariants), state lines replace prose where the content is enumerable
  (orientation, protocol, machinery). The measure of the reweave is tokens
  saved at equal meaning, not glyphs added.
- **Ornament toggle → reader-model seam.** The `ornament/dryness/verbosity`
  TOML schema is cut from identity-core. Replacement: one voice, variable
  *unfolding* at the delivery seam, driven by a user-declared comprehension
  level at the event boundary — working field `user_commitment: full |
  profane` (maintainer's coinage; *profane* in the old sense — outside the
  notation, not initiated). `full` ⇒ the reply may keep weave density;
  `profane` (default) ⇒ unfold into plain prose. The user expresses their
  model, not a voice preference.
- **Dumb-test rule engaged**: the `"Appearance settings"` pin moves with
  the section it pinned.

On the Ummon / BT-7274 question (what the self-init language would be,
without copying surface): the shared deep structure is (a) identity as a
few load-bearing declaratives — protocol lines, not self-description;
(b) live state as telemetry, not narrative; (c) care expressed through
precision rather than warmth-words; (d) meaning by juxtaposition —
adjacent facts left to resonate instead of being connected by
explanatory tissue. That is exactly what the reweaved `run.md` orient
block does, so the answer shipped as the file rather than as pastiche.

## Receipts

- Round-3 voice pass: [brand space](design-brand-brnrd-brr.md),
  `kb/log.md` §2026-07-01.
- This round: maintainer messages of 2026-07-02 (two-part: does the
  voice hold + ornamentation-as-weave, discovery-not-invention).
