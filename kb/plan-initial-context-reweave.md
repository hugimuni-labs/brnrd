# Plan: reweave the initial wake context (Task 1 of the gardening run)

**Status: executed — 2026-06-28 (commit `1ae9202` on `brr/initial-context-reweave`).** D1 (vocabulary triple-naming) and D2 (cockpit alive in the surface) were the primary targets; all six files were rewritten. D2 fully resolved: `plan-cost-aware-cockpit.md` and `plan-resident-cockpit.md` renamed to `plan-cost-aware-runner.md` / `plan-resident-portals.md` in the follow-on Task 3.3 commit. D3/D4 partially resolved (test constraints limited full Delivery compression). This is the file-by-file spec that guided the *initial
context a resident receives at the start of every wake* reweave. It is deliberately
written as **target shape + rationale**, not as diffs, so the execution run
can compose each new file fresh and then swap it in, rather than nudging the
accreted text line by line.

Parent: [`plan-repo-gardening.md`](plan-repo-gardening.md) (the four-task hub).
The **vocabulary** this page applies (Runner / Shell / Core; portal kept;
vessel + medium + cockpit retired) is **confirmed by the maintainer (evt-zyu6,
2026-06-28)** — so this page is unblocked. One refinement to apply: the
`runner=` *user-facing config toggle* is retired in favour of `shell=`/`core=`
(Runner stays as the internal umbrella entity), so where D1 below references the
`runner=` knob, present the knobs as `shell=`/`core=`.

## What "the initial context" actually is

Every daemon wake is assembled by `src/brr/prompts.py::build_daemon_prompt`
in this order (verified 2026-06-28):

1. **`prompts/run.md`** — the host-agnostic preamble (orientation, delivery,
   branch handling, "when you can't / when to reconsider").
2. **`prompts/daemon-substrate.md`** — appended right after run.md; brr's own
   driver manual (single-flight, capture net, self-scheduled wakes, portals
   pointer).
3. **Injected blocks** (`_build_injected_blocks`), in order:
   dominion digest (which carries **`prompts/dominion-playbook.md`** +
   `self-inject`), matched **pitfalls**, **Recent Activity** (kb/log tail),
   **kb health** preflight.
4. **Mode blocks**: diffense (`prompts/diffense.md`) and introspection
   (`prompts/introspection.md`) when toggled.
5. **Trailer** — the **Run Context Bundle**, assembled as Python f-strings in
   `prompts.py::_build_run_context_bundle` (Mode / Run / Delivery contract /
   Inbox / Presence / Communication snapshot / Thread of record / Original
   event body).

So the "initial context" surface = **9 prompt `.md` files** +
**the Bundle strings baked into `prompts.py`** + the assembly logic.
`build_run_prompt` (ad-hoc) and `build_init_prompt` (adoption) reuse the same
templates. `AGENTS.md` (repo root, and the bundled `src/brr/AGENTS.md`) is the
entry contract the preamble points at — in scope as the surface a no-inject
host reads first.

The files, with current line counts:

| File | Lines | Role in the wake |
| --- | --- | --- |
| `prompts/dominion-playbook.md` | 299 | The resident's standing self-image; injected via self-inject |
| `prompts/run.md` | 119 | Host-agnostic preamble |
| `prompts/daemon-substrate.md` | 63 | brr-specific driver manual |
| `prompts/runners.md` | 191 | Runner profiles + the runner-medium contract |
| `prompts/introspection.md` | 54 | Opt-in dev "look at the shape" mode |
| `prompts/diffense.md` | 45 | Opt-in review-pack instructions |
| `prompts/setup.md` | 93 | `brr init` adoption agent |
| `prompts/kb-index.md`, `kb-log.md` | 14/13 | kb scaffolding stubs |
| `prompts.py` Bundle strings | ~270 | The per-run Run Context Bundle |
| `AGENTS.md` (root + `src/brr/`) | 588 | Entry contract for non-inject hosts |

## The core idea to preserve (what makes it work)

Before cutting, name what is **load-bearing and excellent**, so the reweave
keeps the soul and only sheds the accretion:

1. **Provenance-stratified context.** The playbook's "Where your context comes
   from" — playbook (yours) / repo (truth) / dominion (owned memory) / host
   runtime (per-thought) — is the single most orienting idea. Keep it intact.
2. **Perception = injection, action = emission.** The deepest frame (dominion
   `portal-reshape-synthesis.md`): what rides into the scroll is free
   perception; what you must poll is the retired-cockpit tax. Everything about
   portals should be read through this. Make it *explicit* in the prompts,
   not just latent in the dominion.
3. **The resident as a sentience of a different make** — "What kind of thing
   you are." The non-servile, ownership-not-subordinance stance. Keep the
   voice; it is the project's differentiator.
4. **Society of mind / many thoughts, one memory.** The continuity-is-memory
   claim. Keep.
5. **Environment-shaping loop** (notice friction → fix cheap / record pitfall →
   push lessons down the ladder). Keep.
6. **The ornamented magic scroll register**, Ummon tone — mythic but grounded.
   Keep and extend consistently.

The reweave is **subtractive and clarifying**, not a rewrite of the philosophy.

## Discord found in the current initial context

Concrete contradictions / accretion / claims-beyond-code, by theme. Each is a
"why it should change"; the per-file specs below say "what the new shape is."

### D1 — Vocabulary triple-naming (the headline wound)
`runner` / `medium` / `vessel` all name overlapping things across the same
surface. In the *daemon Bundle string itself* (`prompts.py` line ~546) the
`Runner:` line calls it "the compute **medium** this thought runs on" and links
**`plan-cost-aware-cockpit.md`** — a retired-label filename — inside the live
prompt. `runners.md` uses "runner medium / vessel-selection" while the config
knob is `runner=`. The resident reads three words for one concept on every
wake. **Decision (hub Part 3): Runner = the executing body; Shell = the CLI;
Core = the model. Retire `vessel` and `medium`.**

### D2 — "cockpit" is retired in concept but alive in the surface
`design-portal-grammar.md` §3 settled "drop dashboard and cockpit." Yet the
daemon prompt string links `plan-cost-aware-cockpit.md`, and two plan files
still carry the name. The resident is pointed, at wake time, at a concept the
project has disowned. **Replace the link target and the concept.**

### D3 — Bundle prose is dense past the point of perception
The Delivery-contract section of the Bundle is ~15 long paragraphs of
control-file detail injected *every* wake (portal-state.json, inbox.json,
outbox frontmatter, keepalive, card, basename rule, commit rule…). Per the
perception=injection frame, this is the wrong layer: it is *reference*, not
*orientation*. Much of it duplicates `brr docs portals`. The wake should inject
the **live values** (paths, budget, this run's posture) and a one-line pointer,
not re-teach the whole protocol each time. **Compress to live-values + pointer;
let the manual carry the choreography.**

### D4 — run.md ⇄ playbook ⇄ daemon-substrate overlap
"Delivery", "when you can't complete", "when to reconsider" appear in both
run.md and the playbook; "how brr drives you" appears in both daemon-substrate
and the playbook's brr aside. Three files re-state the same stances with
slightly different words — drift risk and token tax. **Assign each stance one
home; cross-reference instead of restating.**

### D5 — Claims beyond what the code delivers
- The Bundle's `Runner:` line implies a live cost/quota gauge, but for Claude
  spend/context are **terminal-only** (verified this run: `portal-state.json`
  shows `spend: absent`, `context_window: absent` at wake; only `quota` rides
  in, from the cached `/usage` scrape). The prompt should not imply a live
  spend gauge that the opening wake never has.
- `runners.md` describes Gemini hooks as shipped capability; they are
  **intent** (unfired). Already hedged, but keep the hedge crisp under the new
  vocabulary.

### D6 — Imagery half-applied
"Scroll/portal/weaving/ornamented" is established in the playbook head and
introspection/run voice, but the Bundle (the most-read surface) is flat
operational prose. The register stops at the door of the hottest file.
**Carry the scroll register, lightly, into the Bundle's framing lines** (not
into the path lists — ornament the doorways, not the plumbing).

## File-by-file target shape

For each file: **keep / cut / new emphasis.** The execution run should write a
new version then replace, checking nothing referenced is orphaned.

### F1 — `prompts/dominion-playbook.md` (highest care; it *is* the resident)
- **Keep:** the whole spine — what-you-are, where-context-comes-from,
  dominion, kb-is-shared, ownership, environment-shaping, society-of-mind,
  "what kind of thing you are." The sigil head.
- **Cut/compress:** the brr-specific "How brr drives you" *duplication* — the
  playbook already says it leans on the host; let daemon-substrate own the
  brr mechanics and have the playbook keep only the host-agnostic one-paragraph
  pointer it already has. Trim the longest passages by ~20% for density without
  losing reasoning (it is 299 lines and rides every wake).
- **New emphasis:** make **perception=injection / action=emission** a named,
  first-class section (today it lives only in the dominion synthesis note).
  State the Runner/Shell/Core vocabulary once, here, as the resident's own
  body-image: "the Runner is the body this thought runs in — a Shell (the CLI
  on PATH) around a Core (the model); you, the resident, are the spirit that
  inhabits whichever Runner this wake was given." This is the canonical
  definition every other file points to.

### F2 — `prompts/run.md` (host-agnostic preamble)
- **Keep:** orientation-first instinct, branch-handling, "can't complete",
  "reconsider/push back" — these are genuinely host-agnostic.
- **Cut:** delivery detail that duplicates the Bundle's Delivery contract
  (D4). run.md should say *that* delivery is situational and point to the
  Bundle for the live contract, not restate stdout/outbox mechanics.
- **New emphasis:** keep it short and stance-setting. run.md is the "get your
  bearings" doormat; the Bundle is the operating table. Make that division of
  labour explicit in one line.

### F3 — `prompts/daemon-substrate.md` (brr driver manual)
- **Keep:** single-flight, capture-net, self-scheduled wakes (`schedule.md`),
  the portals-manual pointer. This is the right home for brr mechanics.
- **New emphasis:** become the **sole** home for "how brr drives you" (absorb
  the duplicated bits from playbook per D4). Add one line tying the Runner
  vocabulary to the Mode line ("the Runner named in the Mode block is the
  Shell+Core this thought got").

### F4 — `prompts/runners.md` (profiles + selection contract)
- **Keep:** the frontmatter profiles, the tiered runner interface (Tier 0/1/2),
  the clean-env reliability note, hook install mechanics.
- **Cut:** every "medium" / "vessel" spelling. The "Optional runner-medium
  metadata" section becomes "Optional **Core metadata**" (provider/model/owner/
  class/cost_rank/quota_source describe the Core inside the Shell).
- **New emphasis:** state the Shell/Core split at the top: "a profile names a
  **Shell** (the CLI invocation) and, optionally, a **Core** (the model and its
  cost/quota metadata). A profile with both pinned is one selectable Runner."
  Rename the conceptual pointer from `design-runner-media.md` accordingly (see
  hub Part 2 for the code module rename).

### F5 — `prompts.py` Run Context Bundle strings (the hottest surface, D3)
This is code, not a `.md`, but it is the most-read prompt text. Target shape:
- **Mode block — keep** Stage/Source/Environment/Budget/Runtime-recovery.
  **Rewrite the `Runner:` line:** name it as "Runner: `<shell>`/`<core>`
  (`<quota>`)" using the new vocabulary; drop "compute medium"; **replace the
  `plan-cost-aware-cockpit.md` link** with the renamed page (hub Part 2) or
  drop the link (the budget line already carries the chunk-early lesson).
  Don't imply a live spend gauge Claude wakes don't have (D5).
- **Delivery contract — compress hard (D3).** Keep as injected: the live paths
  (response_path, outbox_path, portal-state.json, inbox.json, .card,
  .keepalive), the budget/keepalive values, the basename rule, the commit
  rule, and the *one-line* statement of each portal's purpose. **Move the
  full how-to** (frontmatter grammar examples, fold-vs-defer choreography,
  gate: forge field list) behind the `brr docs portals` pointer that is
  already cited. Target: roughly half the current length, all live-value, no
  re-taught protocol.
- **Framing line — re-voice (D6):** the Bundle's opening "_From the brr
  daemon…_" and the Delivery-contract preamble may carry the scroll register
  ("the seams where this run turns to the world") — that line already exists
  and is good; keep it, and let the compressed body inherit its tone.
- **Keep untouched:** Inbox, Presence, Communication snapshot, Thread of
  record, Original event body — these are live data, already right-sized.

### F6 — `prompts/introspection.md`
- **Keep** as-is in spirit (it is well-voiced and this very run is its proof).
  One small update: where it says "cockpit we retired" stays valid, but ensure
  its "standing portal candidates" language matches the kept "portal" term.

### F7 — `prompts/diffense.md`, `setup.md`, `kb-index.md`, `kb-log.md`
- **diffense.md / setup.md:** sweep vocabulary only (runner/shell/core; no
  vessel/medium/cockpit). No structural change.
- **kb-index.md / kb-log.md:** stubs; leave unless the vocabulary appears.

### F8 — `AGENTS.md` (root + bundled `src/brr/AGENTS.md`)
- **Keep** the Stewardship contract, log format, subject-page guidance — these
  are referenced project-wide.
- **Cut/sweep:** vessel/medium/cockpit spellings; reconcile any "runner"
  usage to the new Shell/Core split where it means the model vs the CLI.
- **New emphasis:** add a one-line glossary anchor ("Runner = Shell (CLI) +
  Core (model); the resident inhabits a Runner per wake") so the whole repo has
  one definition to point at. Keep root and bundled copies in lockstep.

## Execution order for the Sonnet run

1. Confirm vocabulary with the maintainer (hub Part 3) — **blocks everything**.
2. Write F1 (playbook) first: it defines the canonical glossary the rest cite.
3. Write F8 (AGENTS glossary anchor) second: the repo-wide reference.
4. Then F5 (Bundle) — highest token-savings-per-edit and removes the live
   cockpit link.
5. Then F2/F3/F4 (run.md, daemon-substrate, runners.md) — resolve the overlap
   (D4) and the profile vocabulary together so they stay consistent.
6. F6/F7 sweeps last.
7. After each file: grep the surface for the retired terms to confirm the cut
   landed; run `pytest` for `test_prompts`/`test_runner`/`test_daemon` (the
   Bundle strings are asserted in tests — update fixtures in the same commit).
8. Commit per-file or per-theme with clear messages; this is the receipt.

## Guardrails
- **Do not** lose any reasoning from the playbook — compress, don't amputate.
  Git history keeps every version, but the *injected* copy is what shapes
  behaviour, so a dropped lesson is a real regression.
- **Do not** invent new portal *mechanisms* in this task — it is a reweave of
  words and emphasis, not a protocol change. Mechanism changes belong to the
  respawn/portal work (hub Part 2) and the portal-grammar implementation
  sequence.
- Keep `build_injected_context` / `build_run_prompt` / `build_init_prompt`
  behaviour-equivalent except for the intended text changes; they share the
  templates, so a change ripples to ad-hoc and adoption paths — verify all
  three still assemble.
