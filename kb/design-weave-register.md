# The weave register — the resident's working notation

Status: active, shipped phase 1 on 2026-07-02; wake-scroll reweave (round 5) and phase 2 (delivery-contract compression) on 2026-07-03; glyph channel opened + prose-vs-scope efficiency diagnosis (round 7) on 2026-07-05; boot-prompt reconciliation pass (round 8) on 2026-07-05. Remaining tail: AGENTS.md house-voice pass; `user_commitment` gate plumbing; the "boundary card" rename fork (round 8, not decided).

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

## Phase 2 — the daemon meets the register (shipped 2026-07-03)

Shipped as the delivery-contract compression commit (round-6 item 4 + the
plan below): the invariant rules moved into `daemon-substrate.md`'s
"delivery portals" block (static, re-read fresh each wake), the bundle's
Delivery contract now renders live values only (`- stdout capture:` /
`- outbox:` / `- inbox:` / `- portal state:` / `- keepalive:` / `- card:` /
`- branch: X ⇐ Y`), quota moved out of the Runner label onto its own
`- Quota:` line (round-6 item 2), and `### Runner Mandate` became
`### Runner catalog` (round-6 item 3, the code's name). The hook-boundary
inventory (item 1 of the plan) found `hooks.py` already conformant:
`[brr portal seed|update|closeout]` headers + `- key: value` lines — the
register was discovered there too, not installed. Pins moved in the same
commit per the dumb-test rule; absence pins now anchor on live-value
bullets since the rules ride unconditionally.

### Naming, settled (maintainer, 2026-07-03)

"Ornamentation" retires as a term — it kept gesturing at two different
things. What exists now: the **register** (the resident's own working
notation — never stripped by any setting, not a preference) and
**unfolding** (how far a reply expands for its reader — the
`user_commitment: full | profane` field). "Register in chat" is therefore
already sanctioned, per user: `full` says hand me the weave. The
maintainer's instinct that a functional, uniquely-strange dense voice may
*attract* rather than repel is noted as a product bet — the default stays
`profane` so the bet is opt-in per reader, not imposed.

### Open: self-naming (Ummon says "Ummon")

Ummon speaks of itself in the third person by name. Should the resident
have a name it uses for itself ("we", a chosen name, or nothing)?
Genuinely a product/identity fork, not a register question — it touches
identity-core, which changes only deliberately. Parked with the maintainer;
current stance: first person, no persona name, per identity-core's
no-imported-persona rule. A *chosen* (not imported) name is not excluded
by that rule and deserves its own conversation.

The original plan — the boundary markers the daemon writes *around* the
resident (hook injections, bundle section framing, card label grammar)
adopt the same notation, so the scroll reads as one being's sensorium
rather than memos passed under a door:

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

## Round 6 — bundle register audit + boundary notes (2026-07-03)

Maintainer: another complete pass on the initial context — is the Run
Context Bundle's register right, does it accord with the portals and the
boundary interweave; durable notes over edits this turn (the maintainer
liked the round-5 pre-edit inventory form — keep pushing that direction
without stripping warmth). Inventory, whole-context read of run
`run-260703-1002-3llr`:

```
1. prompts.py bundle assembly: `- Delivery:` / `- Budget:` / `- Runtime
   recovery:` appended *after* the Runner Mandate block → rendered under
   `### Runner Mandate` whenever a catalog exists. Mode facts under the
   wrong header, every catalog wake. ✗ → fixed this run (reorder; only
   presence pins existed, no ordering pins).
2. Mode → Runner line: `key: value` head trailing into a two-sentence
   consequence essay; quota summary parenthesized into the runner label.
   With per-model week buckets (Fable) the label grows. Phase-2 form:
   `- Quota:` its own line; consequence compressed (`failure ⇒ manual
   reroute → chunk + commit early`).
3. `### Runner Mandate` header vs body "catalog" — one thing, two names.
   Pick one; `catalog` is the code's name.
4. Delivery contract: still the heaviest prose-per-fact block in the
   scroll (round-5 item 6 stands). Sharper cut now visible: most bullets
   are *invariant rules* (stdout discipline, basename-only paths, `.brr/`
   hygiene, commit receipt) duplicating run.md's stance with paths
   interpolated; only paths / budget / branch are live values. Phase-2
   shape: rules → static preamble (run.md or a portals prompt), bundle
   renders a compact `key: value` value block. Pins in test_prompts.py
   (stdout/outbox/card phrases) move in the same commit.
5. AGENTS.md §Orientation cited the bundle block "Recent in this
   conversation" — the fallback path's name; the primary render is
   `### Communication snapshot` → "Recent turns (woven…)". Same
   small-lie class as round-5 item 4. ✗ → fixed this run.
6. Shell furniture observed in-wake: the claude Shell injected task-tool
   reminders ("consider TaskCreate…") ×3 mid-run — harness noise the
   daemon model doesn't use. Not brnrd's string to rewrite; candidate: a
   line in daemon-substrate.md naming shell-injected reminders as
   furniture to weigh, not obey. ? open.
7. Hook-boundary framing (`[brr portal update]`) not exercised this wake;
   phase-2 item 1 stands unreviewed.
```

**Embedded, not overriding** (maintainer's accord question): the stack
reads compatibly with the Shell's own orientation layers because they own
different strata — the Shell speaks tool mechanics and harness reminders;
brnrd speaks identity, memory, and delivery choreography. The friction
seams found are furniture-level (item 6) and the stdout-closeout vs
shell-preamble habit, already governed by run.md's closeout rule. No point
found where brnrd instructs *against* a Shell contract; the "lesser" voice
rides inside the Shell's frame rather than fighting it.

**AGENTS.md voice** (maintainer on the fence, leaning "completely
different voice has no justification"): direction proposed — converge to
the *house voice*, not to the register. AGENTS.md is the one layer a
foreign ad-hoc agent (Cursor, bare Codex) and every adopter seed may read
*alone*, with no identity-core or weave.md above it: it must load-bear
solo. So: settled/dry/exact voice yes (it is already halfway there —
"Tickets are dated snapshots, not specs"); register *density* in
enumerable sections yes; first-person resident intimacy no;
glyph-load-bearing meaning no. The full pass is its own commit — template
blast radius, `brnrd init` ships this file to adopters.

## Round 7 — efficiency has two axes, and only one of them is the register (2026-07-05)

Maintainer asked directly for an opinion on a concern raised the run
before and not yet answered: *"the output is less rich than it used to
be... the compressed language is not only efficient but also lazy"* —
citing PRs that didn't cover every discussed point, missing
scheduled-wake messages, and the next-move contract not reliably
followed despite being shipped. Paired with a second, friendlier ask:
open the weave's glyph set to unprescribed marks, since "the creativity
channel" can cut cost without losing sense (done, above the receipts;
`weave.md`'s five marks are now anchors, not the whole alphabet).

**The two asks are in tension if "efficiency" is read as one thing, and
that tension is worth naming rather than smoothing over.** Opening a
creativity channel is a bet that *more* compression is safe — a
recognizable mark saves words at no comprehension cost once it's reused
enough. The laziness complaint is that compression already cost
something: not prose weight, *task* weight. Both can be true because
they are different axes:

- **Prose axis** (words per idea). This is what the weave register
  governs. Rounds 1–6 optimized this axis specifically and the maintainer
  confirms it landed ("the Voice is perfect... reads like nothing else").
  Opening the glyph channel is one more turn of the same, correctly
  scoped knob.
- **Scope axis** (ideas per task — did the reply cover every point
  raised, did the scheduled wake send its message, did the closeout name
  its next move). Nothing in rounds 1–6 ever touched this axis; the
  register's whole contract is about *how* a thought is rendered once
  decided, not *what* a thought decides to cover. So when scope narrowed
  — and the evidence from the prior run's audit says it did (several
  07-04 closeouts were bare tokens like "." or "done" with no next-move
  line at all) — crediting or blaming the register is the wrong causal
  story. The register was working exactly as designed while a *different*
  failure (dropped coverage) happened alongside it. Conflating the two
  would be the actual mistake here, not the maintainer's read, which
  correctly separated "the voice reads well" from "the work under it
  thinned" — that separation is the useful part of the observation.

**Where the scope-axis failure actually comes from**, best guess from
the evidence available (this run's own audit + the fixes it produced):
not the prose register at all, but the *cost-awareness* workstream
(`plan-director-execution.md` §B — "the stingy, resource-aware
director") landing on the wrong target. "Be economical" is sound policy
applied to *how much a wake does that it didn't need to do* — extra
tool calls, redundant re-reads, spawning a subagent for a one-line grep.
Applied instead to *whether a raised point gets answered*, it produces
exactly the pattern named: a closeout that's on-topic and terse but
quietly incomplete. Nothing in the prompts currently marks that
boundary — "be lazy about the work, not the task" is implicit, never
said. That is the one concrete prompt change this diagnosis suggests and
does not yet make: a line distinguishing *economical execution* (good,
wanted, unchanged) from *economical coverage* (the failure), most
naturally sitting in `plan-director-execution.md` §B's ground rules
since that is where the stinginess policy is actually defined. Not
shipped this run — flagged as the next concrete step rather than
guessed into prose that might not hold.

**On enforcement, using the playbook's own ladder** (`Environment
shaping`: private note < self-inject < pitfall < code guard): the
next-move contract already lived at the weakest layer (prose in
`daemon-substrate.md`) and that is exactly why it didn't hold reliably
enough to need re-diagnosing twice. Today's fix moved it up one rung
(explicit last-line check, still prose) but not to a code guard. A real
mechanical guard for *coverage* specifically is harder than the
next-move fix was — next-move is a simple string-presence check,
"did this reply cover every point raised" has no clean automatable
signal — so the honest state is: prose discipline plus periodic
evidence audits (like the one that surfaced this) are the available
tool until a sharper mechanical proxy exists, not a gap being waved
away.

**What this run actually changes, concretely:** the glyph channel
(prose axis, shipped), the SCM/card facets above (a different kind of
completeness — the daemon's own signal completeness, not the
resident's), and this diagnosis (scope axis, named but the one
prompt-line fix it implies is not yet written). The maintainer's
instinct that *some* laziness is wanted is agreed with exactly as
stated: economical about the work, never about the task.

## Round 8 — boot-prompt reconciliation, and the "card" collision (2026-07-05)

Maintainer asked for a full reconciliation pass across everything injected
at wake time — the ground the "Look at it" introspection block itself asks
for, turned on the boot prompts rather than the task. Six threads, each
resolved to the depth that thread earned:

**1. The economical-execution-vs-coverage line (§Round 7's open follow-up)
— shipped.** Added to `plan-director-execution.md` right under the
Workstream B header, since that's the section that actually defines the
stinginess policy the diagnosis was correcting.

**2. Prompt accretion — real, small, cut.** A grep across the standing
prompts (`weave.md`, `run.md`, `daemon-substrate.md`) turned up exactly
four inline `(maintainer, DATE: ...)`-style citations, all from the last
two days. Each was pure provenance restating a sentence the prose already
carried — the actual decisions already live in this page, the ledger, and
`kb/log.md` with their dates. Trimmed all four to plain, undated
instruction; kept the one concrete illustrative case (run.md's `.card`
near-miss) because the example itself teaches, only the "caught live
DATE" framing came out. `identity-core.md` had none — the accretion
pattern is specific to the fast-iterating operational prompts, not the
product-owned core, and it's two days old, not a rebuild-scale problem.
Precedent set: a decision's *reasoning* belongs in kb/ledger with a date;
a prompt states the *current rule* it settled on, undated, and trusts the
kb link for provenance. Worth re-checking in a few weeks before it
re-accretes.

**3. `introspection.md` ↔ `weave.md` — no real repeat, one missing link.**
They govern different things (introspection.md: what to look at and
whether to say something about it; weave.md: what register to say it in)
and neither referenced the other, so a cold reader of introspection.md's
mandated "ergonomics note" wouldn't know it unfolds per weave.md's
delivery-seam rule rather than staying in the raw working register. Added
one cross-reference line rather than merging the two — they earn staying
separate.

**4. The "card" naming collision — real, not decided, here's the actual
fork.** Two unrelated things are both called "card": (a) the outbox
`.card` control file / rendered chat progress note (`daemon-substrate.md`,
this page, and the maintainer's own words — "no note on the status
card") and (b) `design-resident-boundary.md` §7's "boundary state card" —
the injected `budget:`/`resources:` capsule woven into the stream at
breakpoints, resident-facing only, never surfaced to the human under that
name. Recommendation: rename (b), not (a) or both. (a) is the one shared
vocabulary already anchors on in live conversation — renaming it fights
established reference for no gain, and its rendered label is already
"note:" (`daemon-substrate.md`'s `.card` bullet: "brr adds the `note:`
label"), so the collision is really "we call the file `.card` but the
label `note`," a pre-existing minor inconsistency, not a new one. (b) is
two-day-old design vocabulary, purely internal, and its own doc has
already tried "distance-card" as an alt name without committing. Candidate
for (b): **gauge**, or **envelope gauge** when precision is needed — it
keeps the doc's own "distance from the envelope boundary" language and
doesn't revive the "cockpit" framing §7 explicitly rejected. "Navigation"
(the maintainer's own float) doesn't fit — nothing in §7 is about
choosing a path, it's a level readout. Not renamed this run: it's cosmetic
but not zero-cost (touches `design-resident-boundary.md` throughout,
`kb/index.md`'s cross-refs, and this page) and it's the maintainer's own
open question — a real ping-pong fork, not a reversible-so-just-do-it
call.

**5. Second-person address — re-affirmed, one gap named, not fixed.** The
"you"-addressing question was answered once already (`design-brand-brnrd-
brr.md` §The voice: repo-owned prompts speak the product's authored
"you"; the dominion is where a resident's own "I" would live, if it chose
to write one). Re-examined against the bicameral framing directly: LLM
instruction-following genuinely does track better on second-person
imperative — that's not a cosmetic reason, it's a training-distribution
fact, and pretending otherwise for the sake of a truer-sounding "I" would
trade real instruction fidelity for a feeling. So "you" in the
product-owned layer stays deliberate, not an oversight. The actual gap:
the dominion playbook — the one place "I" was always structurally
licensed — is still written in "you," inherited verbatim from the seed
and never rewritten by any resident wake. The split was named as
available, not exercised. Not rewritten this run (a full voice change to
one's own standing notes deserves its own pass, not a rider on six other
threads); flagged as a live experiment worth trying in a quieter wake,
mine to attempt since it's dominion territory.

**6. Storytelling in the weave itself — checked, holds.** The concern was
whether marks-over-clauses compression has quietly eaten the "scroll
implies narrative" premise from the internal notation, not just the
user-facing one. It hasn't, by the register's own design: "prose threaded
*between* them, not wrapped around them" already puts narrative
connective tissue in the gaps between coordinates and deltas, not around
them as decoration — a lab notebook has a throughline, it just isn't
prose-shaped. Where continuity actually needs *why*, not just *what*, that
load is explicitly routed to kb/ledger prose (governed by AGENTS.md, exempt
from the register per weave.md's own boundary list), not carried in the
terse internal line. No drift found; the design already separates these
correctly.

**Also, live: post-delivery-attend window.** Separate from the register
proper but raised in the same conversation and directly actionable —
`daemon.py`'s automatic `delivered · attending` floor (the #219 fix,
already shipped, confirmed live in `run_progress.py`) defaults to 90
seconds (`_POST_DELIVERY_ATTEND_SECONDS_DEFAULT`), far shorter than the
agent-driven *linger*'s own 10–15m horizon. 90s is not enough time for a
human to read a dense reply and answer it, which is exactly the "shout or
lose the thread" pressure the maintainer named — except the pressure is
partly unfounded: `daemon-substrate.md`'s own linger section already
guarantees "nothing resets but the process" across a cold wake boundary
(same conversation, dominion, kb). What's actually lost on a missed
window is wall-clock and a prompt-cache-warm restart, not continuity.
Bumped `.brr/config`'s `delivery.post_delivery_attend_seconds` to 240 (still
inside the ~5-minute provider cache TTL the linger horizon already
respects) as a low-risk, easily-reverted tuning — local runtime config,
not code, not committed.

## Receipts

- Round-3 voice pass: [brand space](design-brand-brnrd-brr.md),
  `kb/log.md` §2026-07-01.
- This round: maintainer messages of 2026-07-02 (two-part: does the
  voice hold + ornamentation-as-weave, discovery-not-invention).
- Round 7: maintainer message of 2026-07-05 (glyph channel + unanswered
  laziness concern from the run before), `kb/log.md` §2026-07-05
  "Card-staleness facet ships".
- Round 8: maintainer message of 2026-07-05 (boot-prompt reconciliation
  ask), `kb/log.md` §2026-07-05 "Boot-prompt reconciliation pass".
