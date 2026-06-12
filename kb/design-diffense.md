# Design: diffense — kb-first PR review experience

Status: accepted on 2026-05-29 — the card / zoom / navigation model is
validated by a working renderer spike
([`src/brr/diffense/`](../src/brr/diffense), rendered against the
[PR #64 pack](diffense-prototype-pr64.md)). Remaining work (pack schema
lock, transport, runner wiring) is implementation-plan detail, not
design-blocking. (Drafted 2026-05-28; reshaped 2026-05-29, passes 6–9.)

diffense (a working name: *diff* + *sense*, the surface that helps a
reviewer make sense of a diff; and *diff* + *defense*, what guards the
merge against shallow review) is brr's answer to a problem the project
feels acutely in its own dogfooding: reviewing a brr-generated PR in a
generic forge diff view is hostile to the way brr actually works,
because roughly half of a typical brr PR's value lives in `kb/` changes
that read poorly as raw diff and well as rendered, cross-linked Markdown.

This page is a design with research-flavoured sections inside. The
cornerstones below are settled enough to design against. A hand-authored
prototype (the [PR #64 pack](diffense-prototype-pr64.md)) and a working
renderer spike ([`src/brr/diffense/`](../src/brr/diffense)) have since
validated the shape and closed two formerly-open dimensions —
inter-card navigation and code-rendering-at-a-locator (see "Rendering the
zoom"). What remains open (pack schema lock, transport, aesthetic
locking) is marked as such and deferred to an implementation plan.

Companion to:

- [`subject-kb.md`](subject-kb.md) — the kb pattern diffense reads from;
  the lifecycle markers, subject hubs, and decision/design/plan
  separation are diffense's richest structured input.
- [`plan-kb-subcommand.md`](plan-kb-subcommand.md) — the `brr kb` read
  surface (`pages`, `doc`, `log`) that diffense's renderers compose
  against rather than re-implementing.
- [`design-publish-kernel.md`](design-publish-kernel.md) — the publish
  step diffense's pack generation hangs off: the runner emits the pack
  before publish; the resident projects it and addresses the forge gate.
- [`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md)
  — the gate-side review-event boundary; diffense's feedback loop rides
  the `pr-review-comment` handling described there.
- [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md) — the
  home for diffense's hosted web renderer (and the answer to mobile
  review).
- [`design-agent-ergonomics.md`](design-agent-ergonomics.md) — the
  *back* channel (agent friction → operator). diffense is the *forward*
  product channel (the agent's understanding of its own change → the
  human reviewer). Shared source, split audience — see "Relationship to
  the ergo proxy."

## Problem

Generic PR tools (the GitHub diff view, and the review tools layered on
top of it) assume the unit of review is a code hunk and the reviewer's
job is to read hunks top to bottom. brr breaks both assumptions:

- **~50% of a brr PR is kb.** The kb is Markdown designed to be read
  *rendered* — cross-linked, with lifecycle markers and subject-hub
  context that a raw diff strips away. Reviewing `kb/` changes as
  unified-diff text is reading a graph as if it were a scroll.
- **The reviewer needs the surrounding mental model, not just the
  delta.** "Which subject hub does this land in? What decision does it
  implement? What did the conversation that drove it actually decide?"
  are the questions that make a kb change reviewable, and none of them
  are answerable from the diff alone.
- **Tests already encode user stories but read mechanically.** A good
  integration test is a compressed user story (`setup → action →
  assertion`), but it is written to validate a spec, not to explain
  behaviour, so nobody enjoys reading tests as the explanation.
- **An honest picture of what the change *enables*, and of what the
  agent itself was *uncertain* about, is nowhere.** The diff shows what
  changed; it does not show what becomes possible, nor where the agent
  guessed, assumed, or disagreed while producing it.

The reviewer's real cognitive task is not reading — it is *fitting the
diff into a mental model of the system*, cross-referencing context items
the diff view leaves scattered. Humans are a bottleneck on merging
agentic work, and they are bad at holding lots of context after a single
linear read. The tool should do the vector-packing and cross-referencing
for them, and present the result as a navigable surface, not another
wall of text.

## Target audience

Solo developers through large teams. The audience filter is **depth of
engagement, not team size**: diffense is optimized for a reviewer who
has, or aspires to have, full context of the change — the project owner
who plans features and implements them, and their peers who want to
understand a change rather than rubber-stamp it.

diffense is deliberately *not* optimized for skim-approvers; someone who
only wants to glance and click "approve" will find a shallower tool more
comfortable, and that is fine. The kb-aware advantage applies identically
to a solo dogfooder and to a small team sharing a brr-managed repo —
team size does not change the shape of the surface, only how many people
open it.

## Alternatives briefly considered

The research dimension of this design: shapes weighed and set aside.

- **Plain PR body only, no inspect mode.** A structured PR body is a
  genuinely useful *lossy fallback* (see "PR body as a lossy
  projection"), but anchoring the *whole design* on a text-first surface
  under-shapes the product — it leaves the zoomable graph, the locators,
  and the feedback loop on the table and risks the full shape never
  forming. The PR body is a projection of the pack, not the thing we
  design around.
- **Forge-hosted rendered artifact (a generated PR comment or static
  HTML gist).** Hosting the *rendered surface* on the forge means
  PR-comment-shaped UX: no interactivity, no zoom, drift the moment it is
  written, and a hard coupling to one forge's comment semantics. This no
  longer rejects a user-owned gist as the durable **pack JSON** backing a
  browser-rendered interactive view (accepted 2026-06-12).
- **TUI-only viewer.** Power-user-friendly, but it locks out peers
  without brr installed and is the wrong surface for phone review (see
  "Surfaces and what to build first").
- **Hosted-web-only viewer.** Requires brnrd / network; breaks
  dogfooding on a laptop with no connection.
- **A per-team review tool integrated with brr (the Reviewable /
  Graphite shape).** These optimize for a different reviewer profile —
  skim-approval, threaded comment management, stacked-PR mechanics.
  diffense's depth-first, kb-aware shape *complements* those tools rather
  than replacing them; a team can use Graphite for stack mechanics and
  diffense for understanding the change. Trying to be both produces a
  worse version of each.

## Reframings the discussion converged on

Each reframing moves a noun from a heavyweight thing-to-build into a
projection over state brr already has, or sharpens the interaction model:

- **"Review document" → "review surface."** A projection over structured
  state, not a separately-authored, drifting document.
- **"Hosted on the forge" → "forge as data source."** The forge hosts the
  PR; the review surface reads from it.
- **"Spiraling story" → "progressive disclosure with pre-loaded
  mental-model slots."** The reviewer's model has predictable slots; a
  good surface pre-loads them in reading order.
- **"Linear PR scroll" → "a *zoomable* graph of inspection cards."** Two
  navigation axes — lateral (peers) and zoom (summary → detail → ground
  truth). This is the structural centerpiece (see "The card model").
- **"Tests as spec validation" → "tests as grounding evidence for honest
  user-perspective demos."**
- **"One renderer per surface" → "one pack, multiple renderers."** The
  *pack* is the contract; renderers are cheap relative to it. (The
  earlier "one Textual substrate for TUI + web" idea is dropped: the
  near-term surface is a single responsive web renderer; a CLI/TUI is a
  follow-up over the same pack — see "Renderers.")
- **"Agent always confident" → "agent uncertainty as first-class output,
  prominently surfaced."**
- **"Static read-only surface" → "a read surface wired into the existing
  feedback loop."** A reviewer who spots something flags a card; that
  becomes a forge comment; the existing gate turns it into a task. See
  "The feedback loop."

## brr-specific inputs nobody else has

diffense is buildable as a good product *because brr already produces the
structured state a generic tool lacks*:

- **The diff and PR metadata** — `gh pr view --json` + `gh pr diff`.
- **The commit graph between base and head** — the "what" already broken
  into atomic, message-annotated chunks.
- **The conversation that drove the work** — `.brr/conversations/`
  (per-gate-thread logs, covered by
  [`src/brr/conversations.py`](../src/brr/conversations.py)); the source
  of intent and of the agent's in-run reasoning.
- **The kb graph** — `kb/` with lifecycle markers, subject hubs, and the
  decision/design/plan separation.
- **The kb read surface** — `brr kb pages` and `brr kb doc <page>` from
  [`plan-kb-subcommand.md`](plan-kb-subcommand.md).
- **The curated narrative** — [`kb/log.md`](log.md).
- **Commit messages** — the conventional-style "why" brr's commit
  contract enforces.
- **The test suite** — grounding evidence for usage demos.
- **The runner's own state during the run** — the basis for uncertainty
  cards. No other tool has this: only the agent that did the work knows
  where it guessed, assumed, or disagreed.

## Architecture: pack as data, renderers as targets

Two layers, cleanly separated, so the data outlives any one renderer and
one generation step feeds every surface.

```mermaid
flowchart TD
    Inputs["brr inputs:<br/>conversations · kb graph · diff + PR meta<br/>commits · tests · runner run-state"] --> Runner["runner agent<br/>(publish time)"]
    Runner --> Validate["brr review --check<br/>(schema + clamp + render lint)"]
    Validate --> Pack["review pack (JSON)<br/>a zoomable graph of cards:<br/>nodes + lateral edges + zoom tree + locators"]
    Pack --> Web["responsive web — the surface we build now<br/>(terminal aesthetic; local 'brr review', hosted later)"]
    Pack --> Body["PR-body projection<br/>(lossy fallback, ships alongside)"]
    Pack -. follow-on .-> Later["CLI/TUI · hosted brnrd · live agent<br/>(same pack, later)"]
    Web --> Reviewer
    Body --> Forge["forge PR page"]
    Forge --> Reviewer
    Reviewer -->|"flag a card becomes an anchored comment"| Forge
    Forge -->|"existing GH gate: mention becomes a task"| Runner
```

- **Pack (data layer).** A structured JSON artifact, language-agnostic.
  Nodes are cards (item / walkthrough / uncertainty); each node carries a
  **zoom tree** (gloss → detail → ground-truth leaf) and **locators**
  (resolvable references into code / kb / diff); edges are lateral
  relations; metadata carries PR id, conversation id, branch, base, and
  generation time. Generated by the runner at publish time. The pack is
  the contract — everything else is a renderer.
- **Renderers (targets over the pack).**

| Renderer | Surface | Tenancy | Status |
| --- | --- | --- | --- |
| Responsive web | terminal-aesthetic HTML; `brr review` serves it locally | self-hosted (local; LAN/tunnel for phone) | **render model spiked** ([`src/brr/diffense/`](../src/brr/diffense)); local server + runner wiring next |
| PR-body projection | humanized Markdown in the PR body | any forge reader | **ships** (2026-06-01; ownership moved 2026-06-10) — [`prbody.py`](../src/brr/diffense/prbody.py), projected by the resident via `brr review --pr-body`; pack embedded in a marker, `extract_pack` recovers it |
| CLI / TUI | `brr review` in the terminal | self-hosted | follow-up, same pack |
| Hosted web | brnrd serves a static renderer shell at `/r?pack=<raw gist url>`; `/r/{token}` remains the RAM-only relay fallback | hosted (managed mode) | **gist-backed shell ships** (2026-06-12) after the transient relay slice (2026-06-01) — pack JSON lives in the user's secret gist; brnrd serves code or a short-lived fallback, never durable pack storage |
| Live agent | in-context Q&A over the pack | both | future |

The payoff of the split: the hardest, most valuable work (assembling the
pack from brr's structured inputs) happens once; adding or improving a
renderer never re-touches generation. Near term that work feeds exactly
one real renderer (web) plus the free PR-body fallback — everything else
is a later consumer of the same pack.

## Renderers: one web surface now, CLI/TUI and hosted later

The plan went through a Textual-substrate phase (one component model
serving a TUI and a `textual serve` web target). The mobile requirement
already weakened it — `textual serve` is a terminal emulator in the
browser, the wrong surface on a phone — and the decision is now cleaner:
**drop the near-term TUI entirely and build a single responsive web
renderer.** A CLI/TUI is a follow-up over the same pack once the web
shape is proven.

This *resolves* the substrate-split tension rather than carrying it.
Consequences:

- **One renderer to build:** responsive HTML, mobile-friendly, no app
  to install (a PWA at most — explicitly *not* a native app). Served
  locally by `brr review` (an ephemeral local server); reachable from a
  phone over LAN or a tunnel until the hosted brnrd renderer exists.
- **The terminal aesthetic lives in the web renderer**, not in an actual
  terminal: ascii/terminal-*looking* cards (see "Aesthetic" and
  "Rendering the zoom"). We keep the hacker-terminal feel without paying
  for a second rendering stack.
- **Self-hosting tradeoff, accepted.** A local web tool is slightly more
  friction than a TUI would have been for a terminal-native self-hoster
  (start a server, open a browser, vs. a pane in the terminal). It is
  doable, it serves phone review and the future hosted path with the
  same code, and the CLI/TUI follow-up closes the terminal-native gap
  later. Worth the trade.
- **Keep it light.** diffense is being built *before* brnrd exists and
  must not depend on it or get buried under another project's
  complexity. The web renderer is a small, self-contained app over the
  pack with minimal dependencies; brnrd later renders the same pack
  without diffense having to know brnrd is there.

The card / zoom / navigation model is now validated by a small renderer
spike ([`src/brr/diffense/`](../src/brr/diffense)) against the
hand-authored [PR #64 pack](diffense-prototype-pr64.md): a generic,
dependency-free HTML renderer (`template.html`) plus an inliner
(`render.py`, the seed of `brr review`'s render step). It confirmed the
terminal aesthetic carries to the web and reflows to a phone (see
"Rendering the zoom").

## Aesthetic stance: hacker-terminal-text-games leaning, held against the substrate-honest clamp

A leaning, not a lock — and now expressed entirely in the web renderer,
since there is no near-term terminal target. The direction still feels
right: dense, information-first layout; ascii/terminal-*looking* cards
(monospace, line-drawn frames, low-key palette); a terminal-text-game
personality. The web medium carries the *look and spirit* (density,
glanceable stat blocks, lateral exploration) while honouring touch and
responsive reflow — terminal-flavoured, not a literal terminal cosplayed
on a phone.

The **substrate-honest clamp** (see Discipline) keeps this from sliding
into cosplay: every aesthetic choice earns its space by improving
readability, navigation, or usefulness. The reference points (the
inspection screens in Souls-likes, Devil May Cry's ability menus) are
captivating because every screen is information-dense and the visual
choices serve readability — not because they pile on effects. The
Zachtronics games (SpaceChem, Opus Magnum, Exapunks) are the companion
reference: they make an invisible process *visible in motion* — the layout
is the program, the animation is the data flow — and they frame quality as
*position on cost axes against a distribution*, never a verdict. Both
transfer directly: the data-trace walkthrough and the animated-flow
direction take the first; the visual, distribution-shaped stat panel and
the non-prescriptive clamp take the second. Validated alongside the
renderer spike, not locked here.

## The card model: a zoomable graph

The core of the product. The review surface is a **graph of cards with
two navigation axes**:

- **Lateral** — typed edges to related peer cards (`calls`, `implements`,
  `referenced-by`, `shares-invariant`, `part-of-same-decision`, …).
- **Zoom** — each card descends from a one-line **gloss** through
  progressively more detailed **summary levels** to a **ground-truth
  leaf**: the actual rendered kb page, the raw diff hunk, or the code at
  a locator. Summary levels are LLM-authored and clamp-gated; **the leaf
  is mechanical** — the real artifact, pulled deterministically.

This is the spiral made concrete: you skim the gloss, and you can descend
toward ground truth exactly as far as you need, on any card. Two
properties fall out and matter:

- **Honesty is structural.** Because every card bottoms out at the real
  diff / file / rendered page, no summary can hide the truth — you can
  always zoom past it. The summary earns trust by being checkable.
- **Token cost is bounded.** Summaries are small and LLM-authored; leaves
  are not generated (they are the existing diff/file). The pack carries
  summaries + locators, not regenerated copies of the code.

### Three first-class card kinds

- **Item cards** — a typed unit of change: `code-fn-edit`,
  `code-fn-new`, `code-fn-delete`, `code-module-split` /
  `code-restructure`, `code-move`, `kb-page-edit`, `kb-page-new`,
  `kb-page-split`, `lifecycle-flip`, `test-add`, `dep-add`, and so on.
  The kind is a discriminator; the schema differs per kind. (The
  `code-module-split` / `code-move` kinds were added after the
  [PR #64 prototype](diffense-prototype-pr64.md) showed a 1052-line
  file → 12-module split had no honest home among the per-function
  kinds — a delete-plus-twelve-new would lie about what happened.)
- **Walkthrough cards** — a **composite card**: its gloss is a
  `setup → action → outcome` story; zooming reveals its *ordered member
  cards*. This is "a card containing a group of cards" — the zoom axis
  applied to a story. It renders as a plain story at gloss level and as
  an expandable group when zoomed, so it serves both the quick read and
  the deep dive. A multi-item flow gets one; a single-item change
  sometimes does too, when the story needs framing the item card alone
  cannot carry.
- **Uncertainty cards** — the agent's honest expression of an assumption,
  concern, dilemma, out-of-scope flag, or follow-up formed during the
  run. A first-class kind (see "Failure modes").

Plus a **summary card** — one per pack, the orienting header. It is not
a change card; it is the pack's on-ramp, and it always renders first (see
"Reading order").

#### The kind set is an open core, not a closed enum

A small set of well-known change-card kinds (above) is special-cased by
renderers and `--check`; beyond them the agent may **declare a `custom`
kind** inline — a card that names its own kind, carries a one-line "why
this needed its own shape," and degrades to a generic card in any
renderer. Two things keep this honest rather than chaotic: a custom card
still obeys the always-present axes and the six clamps, and the agent is
expected to **raise the gap as a meta uncertainty card** ("I emitted a
custom `X` because the core lacked it — consider promoting it"; see
"Failure modes → Meta uncertainty"). Recurring custom kinds get promoted
into the core. `code-module-split` is the first worked example: it began
as a provisional kind in the [PR #64 prototype](diffense-prototype-pr64.md)
and was promoted here. The taxonomy improves from real use — the way the
kb does — not from an up-front enum nobody can extend.

### Always-present axes (every card carries these)

- **Identity + locator** — what this is (file + symbol + line range, kb
  page + section, walkthrough id, or an uncertainty trigger) *and* a
  resolvable locator: a commit-pinned forge permalink
  (`…/blob/<sha>/<path>#L…`) plus a local `path:line`. Rich renderers
  open the locator inline (the zoom leaf); the PR-body and minimal
  renderers link out to it. **Any card that mentions a code item carries
  a locator to it** — no dead references.
- **Kind** — the discriminator.
- **Gloss (descriptive lore)** — what this is, in plain language (1–2
  sentences). For `test-add` and walkthrough cards the gloss *is* the
  story; for uncertainty cards it is the worry stated in one plain
  sentence. **The gloss leads.** Whatever the kind, the first
  human-readable line a renderer paints is the gloss; the identity, the
  exact locator, and any formal tension notation descend *below* it. (The
  PR #64 prototype's first uncertainty render violated this — it opened on
  `id` / `tension: bounded cursor state ⟂ exactly-once delivery` /
  `where: polling.py:283` before saying in words what the worry actually
  was. The fix is to ease the reader in with the plain sentence first,
  then drop to the specifics. It is easy to get backwards precisely
  because the agent already has the specifics in hand.)
- **Zoom tree** — the ordered summary levels down to the ground-truth
  leaf (the levels themselves vary by kind; see "Zoom levels").
- **Stat block** — the kind-specific load-bearing numbers.
- **Invariant frame** — what the change holds *constant* (the contract or
  property it preserves), because a shape delta is only legible against
  what didn't move: "public surface preserved", "exactly-once delivery",
  "the seen-set stays bounded". A *threatened* invariant is precisely what
  an uncertainty card's tension points at (see "Failure modes"). Nearly
  every change has a nameable one; omitted only when none exists.
- **Provenance** — which conversation message, which commit, which
  run-state moment produced this.

### Conditional axes (emitted iff honest and load-bearing)

- **Possibility lore** — what becomes possible / what constraint is
  lifted (property-flavoured, never narrative-flavoured).
- **Before/after content** — kind-dependent (a deletion is before-only).
- **Lateral edges** — peer relations; for walkthroughs, the ordered
  member-card ids; for uncertainty cards, the related cards.
- **Usage-perspective demo** — when there is a tangible surface change
  worth showing. Text-based at v0 (fenced transcripts, before/after
  caller snippets, kb-navigation walks, benchmark output); a data-trace
  demo carries the datum's shape per stage. Structured **steppable** so an
  animated renderer is a drop-in (see "Animated data flow is a native
  direction").
- **Exercising-tests link** — which tests anchor the demo / exercise this
  item.
- **Tension references** (uncertainty-specific) — pointers to the
  conflicting parts: most often the input prompt span, but also a code
  locator, a kb claim, or another card. See "Failure modes."
- **Severity** (uncertainty-specific) — `low` / `med` /
  `blocking-for-merge`.
- **Proposed resolution** (uncertainty-specific).
- **Locked-abilities axis** — deferred future direction.

### Zoom levels (progressive disclosure, per kind)

The zoom tree is what turns the kb summary-tree idea into a general
mechanic. Concretely, by kind:

- **`kb-page-edit`** — L0 gloss ("what changed + lifecycle delta") → L1
  per-section summaries of what moved → L2 rendered before/after of each
  changed section → leaf: the full rendered page (and the raw diff as a
  sibling leaf). This is the "tree of summarized info" descending into
  the actual file change.
- **`code-fn-edit`** — L0 gloss + signature delta → L1 behaviour summary
  + stat block → leaf: the diff hunk, and the function at its locator
  (opened inline in rich renderers).
- **walkthrough** — L0 story → L1 the ordered member glosses → descend
  into each member card's own zoom tree.

Leaves are always the real artifact. The agent authors L0..Ln; the leaf
is resolved mechanically from the diff / repo / locator.

### kb changes get kb-native axes (not flattened code cards)

A kb change has no state/data/invariant the way code does, and flattening
it into a code-shaped card is why a kb card can read thin. Its shape is a
**graph** change, and its native axes are:

- **Claim delta** — what the page now *asserts* that it didn't (or
  reverses): the actual prose change, rendered, not a raw diff.
- **Graph position** — the link-graph delta: inbound / outbound edges
  added or removed, which subject hub it lands under, an orphan check.
  This is the kb analogue of "surface / contract delta", and it renders as
  a small graph-delta indicator in the same pre-attentive spirit as the
  code meters.
- **Lifecycle** — marker flips (`active → accepted`, `superseded by …`)
  and successor-link validity.

kb also has its own **data-trace**: *walking the link graph* — following a
concept across pages — is the kb counterpart of following a datum through
code, and the renderer can make it navigable (it already renders kb to a
zoomable leaf). So kb changes do fit the model; they just need their own
axes instead of borrowed code ones. A genuinely small kb change (a new
boundary doc, a one-line lifecycle flip) is legitimately lighter — that is
the emit-iff-honest clamp working, not a gap.

### Walkthroughs trace control *or* data (follow the datum)

A walkthrough can follow either of two axes, and the default has been the
weaker one:

- **Control-trace** — the *execution* path: "the code runs setup → action
  → outcome." This is what walkthroughs have been.
- **Data-trace** — **follow the datum**: a single piece of data through
  its transformations ("the @-mention becomes a `pr-review-comment` event
  becomes a task becomes an in-thread reply"), showing the value's *shape
  at each hop*. This is the data flow — and it is exactly the property a
  normal diff view cannot show, so it is the higher-value trace for
  review.

A data-trace therefore carries, per stage, the datum's shape at that point
(not just a prose step). Two consequences:

- **Zooming a stage opens the datum's shape there** — the cheap,
  no-animation form of "watch it move", reusing the zoom mechanic.
- **The trace is structured as ordered, self-contained stages from day
  one**, so *animating* it later is a renderer-only upgrade, never a
  re-authoring (see below).

### Animated data flow is a native direction, not a someday-GIF

Motion is pre-attentive: an animated text flow conveys *transformation and
causality* in a way printed before/after does not — you see the datum
change rather than diffing two snapshots in your head. v0 renders the
data-trace statically (the datum's shape at each stage, zoomable), but the
animated / steppable trace is a **named, kept-in-view direction**, not a
deferred maybe: the pack structures the trace as ordered stages with
per-stage shapes precisely so the animated renderer is a drop-in over the
same data. (This supersedes the earlier "GIFs deferred, revisit if
insufficient" framing — that under-sold the demo axis.)

### Rendering the zoom: nested heading-bar stacks (web)

The concrete interaction the web renderer is built around. Cards are
loosely rendered, terminal/ascii-*looking* blocks. **Opening a nested
card collapses its parent to just its full-width heading bar**, and this
**nests indefinitely**: as you descend, the screen becomes a stack of
parent heading bars (the path from root to where you are) above the card
you are currently inspecting. The stack *is* the breadcrumb — each bar is
a click back up a level, so depth never loses your place. This keeps the
glance/dive rhythm physical: one bar = one level of context held, the
focused card gets the room.

The renderer spike ([`src/brr/diffense/`](../src/brr/diffense)) resolved
the two interaction questions that were left open here:

- **Inter-card / graph navigation — resolved: one breadcrumb stack for
  both axes.** Lateral moves (clicking an edge chip, or opening a
  walkthrough's member) and zoom-drills both *push onto the same
  heading-bar stack*, so a single breadcrumb records the whole path
  however you got there; within-card zoom (the gloss → L1 → leaf ladder)
  is in-place progressive disclosure, not a push. Unifying both axes on
  one stack beat a separate "graph view" — it is the simplest model that
  never loses your place. A dedicated overview/graph view stays a possible
  later enhancement, not a v0 need.
- **Code rendering at a locator — resolved: jump-to-forge at v0.** A code
  leaf renders the commit-pinned forge permalink as an "open ↗" action,
  with the `path:line` shown inline; the zoom ladder's leaf rows do the
  same. Inline syntax-highlighted blocks and side-by-side diffs are a
  later enhancement — the locator already resolves deterministically, so
  upgrading leaf rendering never touches the pack.

The spike is **render-only**: it proves the read model (cards, zoom, the
two navigation axes, the responsive terminal aesthetic) against the PR #64
pack. The *flag-a-card* action, the local `brr review` server, and runner
wiring are not in it — they are named in "The feedback loop" and "Where
the runner / publish kernel wire in" and remain to build.

### Two-axis lore

Borrowed from the Souls / DMC menu framing: every card answers two
questions, and the second is what makes the surface captivating rather
than merely informative.

- **Descriptive lore** — what the change literally is. *"Hashes payload +
  idempotency key; returns the prior result on collision."*
- **Possibility lore** — what becomes possible / what constraint is
  lifted, stated as a property and never prescribing a use. *"POST /tasks
  is now safely retryable; per-consumer dedup is no longer required."*

The possibility axis lets the reviewer project forward ("with this, I
could…") the way a weapon's stat screen lets a player imagine the next
fight — an honest *perceived gain*, not a recommendation.

### Tests as grounding evidence for usage demos

A good integration test is a user story compressed into code. The agent
reads `setup → action → assertion`, extracts the user-flavoured shape,
and writes the usage demo with *real values pulled from the test* rather
than invented ones — which keeps the demo honest. Test-add cards are the
special case where the gloss *is* the story the test encodes; walkthrough
cards lean hardest on integration tests, because a cross-cutting flow is
precisely what a good integration test already exercises end to end.

### Worked-example cards

Illustrative mocks (Markdown stand-ins for what a renderer paints; the
shapes, not a schema). Each emits only the conditional axes that are
honest and load-bearing for it.

**1. `code-fn-edit` — conditional polling in the GitHub gate (with locator + zoom).**

```
┌ code-fn-edit ─────────────────────────────────────────────┐
│ id        item:cache.get_with_etag                         │
│ where     src/brr/gates/github/cache.py :: get_with_etag   │
│ locator   github …/blob/a1b2c3d/src/brr/gates/github/      │
│           cache.py#L40-L78   ·   local cache.py:40         │
│                                                            │
│ what      Sends If-None-Match with the last stored ETag    │
│           on high-volume GETs; returns the cached body     │
│           unchanged on a 304.                              │
│ enables   Quiet repos stop spending REST budget on polls   │
│           (304s are free); steady-state consumption drops  │
│           ~10x. Self-heals — a stale ETag costs one 200.   │
│                                                            │
│ stats     signature   (path) -> Resp  =>  (path, *, etag)  │
│           callers     3 in repo / 3 updated / 0 unchanged  │
│           error paths +0 (304 is a success branch)         │
│           coverage    +2 tests (was 0 direct)              │
│                                                            │
│ zoom  ▸L1 behaviour summary  ▸leaf diff hunk  ▸leaf code   │
│ demo      # before: every poll spends budget               │
│           GET /issues/comments      -> 200 (rate -1)       │
│           # after: unchanged repo, no spend                │
│           GET /issues/comments      -> 304 (rate  0)       │
│                                                            │
│ tests     tests/test_github_gate.py::test_etag_304_is_free │
│ edges     called-by polling.poll_once                      │
│           shares-invariant cursor.etags store              │
│ from      commit a1b2c3d · conversation msg #14            │
└────────────────────────────────────────────────────────────┘
```

**2. `kb-page-edit` + `lifecycle-flip` — a plan slice ships (zoom tree; no demo).**

```
┌ kb-page-edit · lifecycle-flip ────────────────────────────┐
│ id        item:plan-laptop-daemoning.linux-slice           │
│ where     kb/plan-laptop-daemoning.md :: Status            │
│ locator   github …/blob/d4e5f6a/kb/plan-laptop-daemoning.md│
│                                                            │
│ what      Marks the Linux systemd slice shipped; macOS +   │
│           multi-project runtime stay open follow-ups.      │
│                                                            │
│ stats     marker     active => partly shipped 2026-05-26   │
│           inbound    5 -> 6 (subject-daemon now links it)  │
│           siblings   subject-daemon.md  in sync ✓          │
│           successor  n/a (not superseded)                  │
│                                                            │
│ zoom  ▸L1 per-section summary  ▸L2 rendered before/after   │
│       ▸leaf full rendered page  ▸leaf raw diff             │
│ (no usage demo — kb-internal change, nothing to run)       │
│ edges     referenced-by subject-daemon.md                  │
│           part-of-same-decision design-config-layout.md    │
│ from      commit d4e5f6a · conversation msg #7             │
└────────────────────────────────────────────────────────────┘
```

The *absence* of a usage demo is deliberate signal: a kb-internal change
has nothing to run, and the card says so rather than faking a demo.

**3. `test-add` — gloss is the story.**

```
┌ test-add ─────────────────────────────────────────────────┐
│ id        item:test.pr_review_summary_event               │
│ where     tests/test_github_gate.py ::                     │
│             test_pr_review_summary_emits_event             │
│ story     A maintainer leaves a PR review whose summary    │
│           @-mentions the bot. The gate fetches the parent  │
│           review once, sees the mention, and emits a       │
│           pr-review event carrying review id + state.      │
│ stats     exercises   polling.poll_once -> pr-review path  │
│           assertion   event shape + dedup via seen ids     │
│           fixtures    shares _gh_stub with 6 gate tests    │
│ edges     exercises item:polling.detect_pr_review          │
│ from      commit a1b2c3d · conversation msg #19            │
└────────────────────────────────────────────────────────────┘
```

**4. `walkthrough` — a composite card (gloss = story; zoom = member cards).**

```
┌ walkthrough ──────────────────────────────────────────────┐
│ id        walk:byo-failover-dispatch                       │
│ title     A subscriber's BYO creds carry a spawn when the  │
│           managed pool is unhealthy                         │
│                                                            │
│ setup     subscriber account, cloud-platform creds present,│
│           managed Fly pool reporting unhealthy             │
│ action    a task event needs a managed-compute spawn       │
│ outcome   dispatcher takes the BYO branch; spawn runs on   │
│           the subscriber's own Fly account; audit records  │
│           spawn_byo (not debit_spawn); wallet untouched    │
│                                                            │
│ zoom ▸ members (ordered) — each opens its own card:        │
│   1 → item:dispatch.route          (branch on creds)       │
│   2 → item:pool.health_check        (unhealthy gate)       │
│   3 → item:audit.record_spawn_byo   (wallet bypass)        │
│                                                            │
│ grounded  tests/test_dispatch.py::                         │
│             test_byo_when_creds_present_and_pool_unhealthy │
│ from      commits a1b2c3d..d4e5f6a · conversation msgs     │
│           #22-#31                                          │
└────────────────────────────────────────────────────────────┘
```

**5. `uncertainty` (concern) — references the tension explicitly.**

```
┌ uncertainty · concern ────────────────────────────────────┐
│ id        unc:audit-op-naming-mismatch                     │
│ severity  med  (not blocking, but you may want to fix here)│
│ tension   design-billing.md  ⟂  src/brnrd/audit.py         │
│           (design page says spawn_byo; the code's existing │
│            ops use a debit_/credit_ verb prefix)           │
│                                                            │
│ unclear   I added the BYO wallet-bypass audit op and named │
│           it spawn_byo to match design-billing.md, which   │
│           breaks the debit_/credit_ prefix the other ops   │
│           use. I followed the design page over the code    │
│           convention; flagging because you didn't ask me   │
│           to reconcile the two.                            │
│ proposed  Rename to bypass_spawn_byo for prefix consistency│
│           OR note in design-billing.md that audit-op naming│
│           is intentionally domain-led.                     │
│ edges     related item:audit.record_spawn_byo              │
│           related walk:byo-failover-dispatch               │
│ from      conversation msg #27 (where I made the call)     │
└────────────────────────────────────────────────────────────┘
```

**6. `uncertainty` (follow-up) — near-future work for max value, held out of scope.**

```
┌ uncertainty · follow-up ──────────────────────────────────┐
│ id        unc:byo-needs-cred-rotation                      │
│ severity  low  (this change is complete; this is next)     │
│ tension   task scope  ⟂  full user value                   │
│           (the task asked only for the dispatch branch)    │
│                                                            │
│ unclear   BYO dispatch works, but a subscriber whose cloud │
│           token expires gets a hard spawn failure with no  │
│           rotation path. To make BYO actually dependable   │
│           the near-future work is a cred-health probe +    │
│           a re-auth nudge on the dashboard. Out of scope   │
│           here; flagging so it doesn't get lost.           │
│ proposed  File as a follow-up; it's ~a day and it's what   │
│           turns BYO from "works" into "trustworthy."       │
│ edges     related walk:byo-failover-dispatch               │
│ from      conversation msg #33                             │
└────────────────────────────────────────────────────────────┘
```

### Stats are load-bearing

Every stat answers a question a reviewer actually asks; a stat that does
not is cosmetic and fails the honest clamp. Per-kind starting sets:

- **`code-fn-edit`** — signature delta (state/behaviour) **and a separate
  data-shape delta** (new/changed types, event kinds, schema); callers in
  repo / updated / unchanged; complexity delta; new error paths;
  test-coverage delta.
- **`kb-page-edit`** — lifecycle-marker delta; inbound-link-count delta;
  sibling-page sync check; successor-link validity.
- **`test-add`** — production code path exercised; assertion shape;
  fixture sharing.
- **`new-file`** — location justification; inbound-link wiring;
  sibling-file pattern adherence.
- **`deletion`** — what replaced it; broken-reference flagging.
- **`uncertainty`** — severity; blast radius (which other cards it
  touches).

The "+/- against equipped item" pattern from the game reference becomes
"delta against the codebase as it currently stands" — the agent that did
the work already has these values in context.

**What the stats measure: state shape, data shape, invariant.** A change
has three shapes worth a reviewer's attention, and good stats name all
three rather than collapsing them to size:

- **State shape** — control / behaviour: the branches, the routing, the
  state machine. Owned by the code.
- **Data shape** — the types and schema the values carry, distinct from
  the signature: a new event kind, a cursor field, a migrated record. A
  code card surfaces a **data-shape delta** separately from its
  signature/behaviour delta; they answer different questions.
- **Invariant** — what is held constant across the change, the reference
  frame the other two are read against (see "Invariant frame").

A caveat on the tempting split "code = state, tests = data": in typed code
much of the data *shape* is already in the code (the types), so the
cleaner cut is **possible vs actual** — the code is the *grammar* (which
state/data shapes are reachable at all), the tests are the *sentences*
(the ones that actually occur). That is exactly why "tests as grounding
evidence with real values" works: a demo lifts an actual sentence up to
make the grammar legible (see "Tests as grounding evidence").

## Reading order: orient, surface concerns, then explore

Earlier drafts said bluntly "uncertainty cards first." Pressure-testing
the prototype refined that: a reviewer dropped *straight* into the
sharpest, most specific concern (`sorted(seen)[-_SEEN_CAP:]`, ×7) with no
frame is jarring, even when the concern is exactly right. The surface
needs a beat of orientation before the scrutiny — but concerns must still
be surfaced early, not buried. The order that satisfies both:

1. **A single summary card opens the pack** — the on-ramp / header (see
   below). Five seconds; it names what the change *is* and points at the
   concerns.
2. **Uncertainty cards come next** — still the first *substantive* read,
   still answering "what should I scrutinize hardest?", and pointed at by
   the summary so nothing is hidden behind the orientation.
3. **Walkthroughs and item cards follow** — the change itself, explored
   at whatever depth the reviewer chooses.

Why orient before scrutinize: you cannot judge *what to scrutinize
hardest* without a five-second *what is this even*. The summary is the
frame that makes the first concern legible; it is deliberately tiny, and
it carries the concern count and severity, so surfacing-early is
preserved — the concern is one glance away, just not the literal first
pixels with no context.

### The summary card (the header — context, not just counts)

One per pack, always first. It answers "what is this PR, in shape?" The
"there should be a header here" instinct and the "PR stats, but they're
too detailed and contextless" instinct resolve to the same card: numbers
in service of a shape, never raw numbers alone.

- **What it's for** — one paragraph of plain intent.
- **The shape** — the story arcs the change splits into (e.g. *fix ·
  refactor · feature*); the surface area (which subsystems, which kb
  pages).
- **The risk pointer** — "N concerns (M blocking / med) — read next," so
  the reviewer knows up front whether this is a rubber-stamp or a
  scrutinize.
- **The entry-stat panel** — its own region (below).

**Entry stats are a rollup of the card axes into distributions.** This is
the principle that keeps the panel honest rather than ad-hoc: the summary
does not author new numbers, it *aggregates* the per-card axes. Size is
the one stat that is **not** a rollup — which is exactly why it is the
least useful, and why it is demoted. What the panel rolls up:

- **change-kind mix** — the distribution of card kinds (fix / refactor /
  feature; code / kb / test), as a proportion, not a count.
- **surface / contract delta** — the rollup of per-card signatures and
  public surface: *how far does this ripple?* Often the single most
  decision-relevant number ("12-module split, public surface preserved"
  reads low-risk despite a huge line count).
- **invariants** — established vs threatened (threatened ones link the
  concerns).
- **data-shape delta** — new/changed event kinds, schemas, cursor fields.
- **cost axes, before→after** — coverage, new error paths, an effect
  budget (e.g. steady-state REST cost). The Zachtronics "where do you land
  on the cost axes" view, not a scalar.

**The panel is visual, and pre-attentive by design.** Bars / meters /
heat-indicators, not a table of numbers — the point is that the shape
*imprints* on a glance rather than being read to be understood (a stale
ETag bar at zero, a "surface preserved" meter pinned full, two amber heat
dots for the concerns). Restrained, not fancy, and held to the
substrate-honest clamp: a meter earns its pixels by being faster to absorb
than its number, or it does not appear.

Raw `+/−` and file counts survive as a demoted secondary line (or a zoom
level), never the headline: "23 files, 2642+/1221−" is precise and tells a
reviewer nothing about what to *do*. The summary turns that into "three
braided arcs across the GitHub gate plus one new kb design page; two
concerns, one med." When there are no uncertainty cards, the summary says
so — absence-as-signal preserved, and the uncertainty section collapses.

## Failure modes: agent uncertainty as first-class output

**Why this exists.** The rest of the card model implicitly assumes the
agent executed a well-scoped task successfully. Real life is messier:
tasks arrive half-defined, sometimes barely scoped, sometimes not fully
understood; the agent forms opinions, hits forks, and makes assumptions
mid-run. Suppressing all of that to present a tidy "everything is fine"
surface produces a *dishonest* and brittle review. So the agent is not
only allowed but expected to express confusion and clarify its WTFs, as
first-class cards.

**Uncertainty cards reference the tension.** Every uncertainty card
points at the *conflicting parts* via tension references — the surface
should show *what is in tension with what*, not just that the agent felt
unsure. The most common reference is **the input prompt**: the task was
too shallow, it carried an implication that wasn't true, or the actual
code contradicted the mental model the prompt assumed. Other references:
a code locator, a kb claim, or another card. Uncertainty can also attach
to **the agent's own choice** that raised tension during execution — not
only external confusion.

**Subkinds:**

- **assumption** — "your prompt didn't specify X; I assumed Y; here's
  why; flag if wrong." (tension: prompt ⟂ reality)
- **concern** — "Y seems wrong upstream but you didn't ask; I left it;
  flagging in case you want to fix it here." (Card 5 above.)
- **dilemma** — "I had to choose between A and B; chose A because of
  constraint Z; here's the path not taken." (tension: the agent's own
  choice ⟂ a viable alternative)
- **out-of-scope-flag** — "the task implied Z but I didn't do it because
  it reads as out of scope; you may want it."
- **follow-up** — "this change is complete, but the near-future work that
  would make it *maximally* valuable, from my perspective, is W; it was
  out of scope." (Card 6 above.)

**`follow-up` vs the non-prescriptive clamp.** The clamp forbids
prescribing *how to interpret the change under review*. It does **not**
forbid honest foresight about *what work would come next* — that is
information the reviewer wants. A follow-up card states the next work as
the agent's perspective ("from where I stood, W unlocks the value"),
references the tension (task scope ⟂ full user value), and never dresses
it as a verdict on the current change. That keeps foresight honest
without smuggling prescription back in.

**Meta uncertainty (about the pack, not the change).** Uncertainty can
also point *inward* — at the review artifact itself. The clearest case:
the change didn't fit the card taxonomy, so the agent emitted a `custom`
kind (see "The kind set is an open core") and flags it — "I represented
this as `custom:X` because no core kind fit; consider promoting it." This
is how a missing kind surfaces *to the reviewer* and feeds taxonomy
promotion rather than being silently forced into an ill-fitting kind.
Meta uncertainty is rare and reads like any other concern, but its
tension is `the change ⟂ the representation` rather than anything about
the code.

**Honesty applies to the agent's own state.** The honest clamp, for
uncertainty cards, means the agent reports its actual run-time state —
where it guessed, assumed, or disagreed — not only facts about the diff.
A pack that *always* reads "everything is clean" cannot be telling the
truth, and a reviewer learns to distrust it. Surfacing uncertainty is
what makes the rest of the surface credible.

## The feedback loop: read → flag → gate → iterate

diffense is a read surface, but review is not read-only: a reviewer who
spots something wants to act on it. Rather than invent a feedback
mechanism, diffense **composes the gate brr already shipped**.

```mermaid
flowchart LR
    Read["reviewer reads a card"] --> Decide{decision}
    Decide -->|"approve / note"| Local["local reviewer state (.brr)"]
    Decide -->|"ask"| Live["live agent: ephemeral Q and A"]
    Decide -->|"request change"| Comment["anchored forge review comment<br/>(at the card's locator)"]
    Comment --> Gate["existing GH gate:<br/>pr-review-comment becomes a task"]
    Gate --> Iterate["agent iterates, commits"]
    Iterate --> Repack["re-emit pack"]
    Repack --> Read
```

- **Approve / note** — local reviewer state, kept in `.brr/` (see "Where
  packs live"); optionally roundtripped to forge review state.
- **Ask** — the live agent answers in-context from the pack, without
  spawning a task. Ephemeral; good for "why this approach?" It does *not*
  change code.
- **Request change** — diffense authors a forge review comment
  **anchored to the card's locator** (file:line for code, section for
  kb). The existing `pr-review-comment` handling
  ([`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md))
  turns the @mention into a task; the agent iterates, commits, and
  re-emits the pack; diffense re-renders.

The live agent is only *half* useful if it can answer but not act; the
durable half is this loop, where a flagged card becomes real follow-up
work through machinery that already exists. **Open:** pack versioning
across iterations (a PR accrues successive packs) and a "what changed
since I last reviewed" pack-diff view — named in Open questions.

## The pack validation / render tool

The agent that emits the pack confirms it before publishing — a malformed
or clamp-violating pack is a silent quality regression. So diffense ships
a checker the agent runs as the last step of pack generation:

**`brr review --check <pack>`** (shipped 2026-06-01,
[`src/brr/diffense/pack.py`](../src/brr/diffense/pack.py)):

- **schema-validates** — the always-present axes are present, card ids
  are unique, the kind set is an *open core* (an unknown kind warns and
  degrades to generic rather than failing), and any card that names a
  file carries a `locator`;
- **resolves the card graph** — every reading-order entry and every
  card-namespaced lateral edge / walkthrough member / data-trace stage
  resolves to a real card; a dangling card reference is an error, while a
  free reference (a bare symbol, a kb anchor) is left alone;
- **resolves locators against the repo** — a `locator.local` whose file
  is missing or whose line is past end-of-file is an error (the check
  that would have caught the design draft's invented
  `cache.get_with_etag`); a named `identity.symbol` absent from the file
  is a heuristic warning;
- **clamp-lints** the cheap clamps — oversized glosses (*sharp*), empty
  conditional axes emitted anyway (*emit-iff-honest*), prescriptive
  phrasing (*non-prescriptive*).

`--json` emits the machine-readable issue list; a non-zero exit blocks
publish of a broken pack, and the step folds into the runner's
self-review (see "Where the runner / publish kernel wire in"). The
render-check arm — dry-rendering each renderer's view — stays deferred:
the only renderer today is the schema-driven JS spike, so schema validity
*is* its renderability; it returns as a real step once a server-side
renderer exists.

## Discipline: the six clamps

The cards must be Occam's-razor sharp, not a place to get lost. Six
clamps gate what the agent emits; a card passes only if it clears all
six:

1. **Sharp.** Skimmable in 5–10 seconds at gloss level; every element
   earns its space (a *form* constraint). Zoom carries the depth, so the
   gloss stays sharp.
2. **Helpful.** Every element load-bears for a reviewer decision (a
   *function* constraint). Distinct from sharp: a card can be small *and*
   useless. Both required; they fail independently.
3. **Honest.** Every stat answers a real question; possibility lore
   states properties actually true; usage demos use real values; zoom
   leaves are the real artifact; uncertainty cards report the agent's
   actual state.
4. **Non-prescriptive.** Cards describe; the reviewer composes the
   verdict. No "this is the cornerstone of strategy X." (Follow-up cards
   are honest foresight about *next work*, not prescription about the
   *current change* — see Failure modes.)
5. **Emit-iff-honest.** Conditional axes appear only when there is real
   material; absence is signal.
6. **Substrate-honest, not cosplay.** Aesthetic choices earn their space
   by improving readability / navigation / usefulness.

**The Occam's-razor reading-order test.** A reviewer should reach
"approve / dive deeper / ask" within seconds of landing on a card. If
they cannot, the card is doing too much, or the wrong thing.

## Experience principles: glance, dive, or wander (the Marathon test)

The felt experience, stated as a north star so renderers can be judged
against it. A good game menu lets you read an item's description, the
description of its class, the slots it fits, and compare it against
siblings — *or* just glance and go try it. You don't hang out for ten
hours before playing; you skim, try, die, loot, read a little more, go
again.

diffense should feel the same:

- **Glance** — the gloss + stat block answer "approve / dive / ask" in
  seconds (sharp + helpful). The default path is fast.
- **Dive** — the zoom axis descends to ground truth on any card, with
  escape hatches to the real diff / file / rendered page always one step
  away.
- **Wander** — lateral edges let curiosity roam to peers and context
  without losing your place.

Depth and exploration are **opt-in, never forced**. The surface saves
time by default (the clamps) and rewards curiosity on demand (the zoom +
lateral axes). It is not a place to get lost; it is a place you *can*
explore when a change earns it.

### On "making review entertaining"

The goal is not gamification bolted on top. Review burden splits in two:
**accidental burden** (context scattered across tabs, wall-of-text, no
signal about where to look, no agency to act from where you are) and the
**irreducible core** (you must make a judgment call). diffense's entire
job is to strip the accidental burden until what is left is the
genuinely interesting part — exploring what a change *enables*, hunting
the agent's flagged WTFs, and deciding. That residue has *stakes*, and
stakes are what make a thing engaging rather than tedious; the design
keeps the decision, it just clears everything around it.

Two honesty constraints on this goal, so it doesn't curdle:

- **Enjoyment is downstream of trust.** A fluffy or wrong card flips from
  delightful to insulting instantly. The honest clamp and structural
  honesty (you can always zoom to ground truth) are what *protect* the
  experience, not just correctness features.
- **The aesthetic is a multiplier, not the substance.** The
  terminal-game look makes a burden-free surface delightful; it cannot
  rescue one that fails to reduce burden. Substance first (the clamps,
  the zoom, the feedback loop), spice second (the look).

## PR body as a lossy projection (not the design anchor)

A stable Markdown template, projected from the pack, written into the PR
body so *any* forge reader gets a real improvement with zero tooling —
including the phone reader who never opens a richer surface. It is a
**lossy fallback**, deliberately not the surface the design is anchored
on (anchoring on text-first would under-shape the product). Sections
labelled by producer (`LLM` = generated prose, `mechanical` = derived):

```markdown
## Summary         what this PR is, in shape: the arcs it splits into,
                   the surface area, and "N concerns (M med) below."
                   Numbers in service of a shape.   (LLM + mechanical)

## ⚠ Concerns      <!-- only present when uncertainty cards exist -->
- **[concern · med]** audit-op naming breaks the debit_/credit_ prefix
  pattern — left as out of scope.   (LLM)

## Narrative       the setup → action → outcome arc of the change   (LLM)

## Touched         (mechanical)
- src/brr/gates/github/client.py — _request gains conditional-GET etag_store
- kb/plan-laptop-daemoning.md — Linux slice flipped to shipped
- tests/test_github_gate.py — +2 cases

## Reading order   1. Summary  2. Concerns  3. highest-signal cards …   (mechanical)

## Deferred / open what this change deliberately did not do   (LLM)
```

The full pack rides alongside this body (see "Where packs live") so a
reader with `brr review` or the hosted view gets the zoomable surface,
while the forge-only reader still gets the orientation up top and the
agent's flagged doubts one section down.

**Shipped** (2026-06-01) as [`prbody.py`](../src/brr/diffense/prbody.py),
a pure pack → Markdown function. The card→section mapping is mechanical:
`summary` card → **Summary** (gloss + `shape.arcs` + `surface_area` + a
concern-count line); `uncertainty` cards split by `subkind` — doubts
(`concern`/`assumption`/`dilemma`/`meta`, and any unknown subkind) →
**⚠ Concerns**, scope notes (`out-of-scope-flag`/`follow-up`) →
**Deferred / open**; a `walkthrough` card's gloss → **Narrative** (the
section appears only when such a card exists); every change card naming a
file → **Touched**; `reading_order` → **Reading order** (ids resolved to
labels). Each section renders only when the pack has material for it.

## Where packs live

The pack is a run artifact with a contract; it needs to be (a) cached
where the producing machine can re-render it and (b) reachable by any
reviewer's surface.

- **Local cache** — `.brr/diffense/<task>/pack.json` (the gitignored
  runtime dir, matching brr's "artifacts in `.brr/`, not the worktree"
  convention). Keyed by **task id, not PR number**: a run always has a
  task id but not always a PR (brr publishes a branch — see issue #68). The
  local `brr review` reads from here.
- **Travels with the PR the user publishes** — the static, humanized
  PR-body projection (see "PR body as a lossy projection") always rides
  the forge, so a reviewer with only forge access gets the orientation and
  the flagged concerns for free. The *full* interactive pack can ride
  alongside it, embedded in the body inside an HTML-comment marker block
  (reusing the ergo proxy's invisible `<!-- … -->` technique), size
  permitting. This is the user publishing their own data to their own PR —
  not a third party storing it.
- **User-owned gist for durable rich review** (**shipped 2026-06-12**).
  `brr review --pr-body --relay` first writes the pack JSON to a secret
  gist in the user's own GitHub account and links brnrd's static renderer
  shell as `/r?pack=<raw gist url>`. The browser fetches the raw gist
  directly; brnrd serves the renderer code and never receives or stores
  the durable pack bytes. A secret gist is an unlisted capability URL, so
  brr declines this durable path when GitHub reports the target repo as
  private or internal.
- **brnrd is a transient relay fallback, never a pack store**
  (config-gated; **shipped 2026-06-01**, demoted to fallback
  2026-06-12). When gist publication is unavailable or the pack should not
  leave a private repo as a public capability URL, the daemon can relay the
  pack to brnrd (`POST /v1/daemons/pack`), which holds it in a RAM-only TTL
  store behind an unguessable token and renders it on `GET /r/{token}` via
  `brr.diffense.render` — it does **not** persist it, mirroring brnrd's
  event-content-transient, "data ownership stays at the metadata-graph
  level" stance ([`design-brnrd-protocol.md`](design-brnrd-protocol.md)).
  Persisting the pack server-side would break that stance, since the pack
  is derived from the conversation and the diff. (Earlier drafts said brnrd
  *stored* the pack keyed by PR / conversation id; corrected 2026-05-31 —
  it contradicted the protocol's data-ownership stance.)
- **Self-served** — `brr review` over LAN / a tunnel from the producer's
  machine closes the remote case with no brnrd at all.

This keeps generation in one place; every renderer reads the same bytes
from the producer — locally, embedded in the user's own PR body, from the
user's own gist, or transiently relayed — and no third-party brr service
persists the pack.

## Where the live agent fits

An in-context Q&A surface over the cards, reading the pack as grounding
context so answers stay anchored to the actual change — the "there's a
silicon mind out there we can just ask" loop, attached to the surface.
It handles the **ask** branch of the feedback loop (ephemeral, no code
change); durable change-requests route through the **request-change**
branch (forge comment → gate → task). Constrained by the same six clamps.
Future; named so it is not reinvented.

## Relationship to the ergo proxy

diffense and the ergo proxy
([`design-agent-ergonomics.md`](design-agent-ergonomics.md)) are both
agent-emitted side channels at the task boundary, about meta-information
beyond the code change. They are **not** the same channel, and should not
be merged — but they share a source and a transport, and folding them at
those seams avoids duplicate machinery.

| | diffense | ergo proxy |
| --- | --- | --- |
| Audience | always the **user / reviewer** | the **operator** (self-hoster, or platform operator in managed mode) |
| Subject | the **result** — the change + the agent's understanding/uncertainty about it | the agent's **ability to execute** — environment friction + task clarity |
| Surface | the review pack / cards | the ergonomics record, routed by tenancy |

The **overlap is task clarity.** A shallow or self-contradictory task
produces *both* a diffense uncertainty card (an `assumption`/`concern`
referencing the prompt — the user reviewing *this change* should know it
rests on a guess) *and* an ergo signal (the operator should know this
source's tasks tend to be underspecified). The fold:

- **Share the reflection-elicitation step.** One prompt step asks the
  agent for its honest run-time reflection (confusions, assumptions,
  task-clarity friction). diffense renders the change-relevant slice as
  uncertainty cards for the user; the ergo proxy routes the
  capability-relevant slice to the operator.
- **Share the marker transport.** Both ride HTML-comment marker blocks on
  the response / PR body, stripped before the user-facing text.
- **Keep the split.** Route by audience: never show the operator-facing
  ergonomics slice to the reviewer, and never burden the reviewer's
  uncertainty cards with environment-friction noise that isn't about
  this change.

## Project boundary

The pack format is the contract; renderers can live anywhere. Current
leanings (decided in the implementation plan, not locked here):

- **Pack generation + the validation tool** live in brr — part of the
  runner's publish-time work.
- **The web renderer** lives in brr, served via a `brr review` verb (the
  CLI/TUI follow-up shares it). Settled in-tree at
  [`src/brr/diffense/`](../src/brr/diffense) — the renderer spike lives
  there now, and its install footprint is nil (a stdlib-only inliner plus
  a static HTML template, zero runtime deps), so the "own
  extras-installed package" alternative
  ([`decision-monorepo-structure.md`](decision-monorepo-structure.md))
  isn't warranted.
- **Built before brnrd, and decoupled from it.** diffense ships first,
  for the self-hosting story, and must stay light: a small,
  minimal-dependency web app over the pack that does **not** depend on
  brnrd or absorb its complexity. The hosted view is a *future*
  brnrd-dashboard renderer over the same pack — it becomes the clean
  mobile answer once brnrd exists, but diffense neither waits for it nor
  knows about it.

## Surfaces and what to build first

Settled this pass: **web-first, no TUI in the first cut, built before
brnrd.** The constraints — phone review is first-class, no native app,
low friction, must work for the self-hosting story today — point away
from both "text-first" and "TUI-first."

1. **Build the pack + the validation tool first.** Everything renders
   from the pack; nothing is real until the pack is.
2. **Then the responsive web renderer**, served locally: `brr review`
   spins an ephemeral local server (self-hosted, laptop), reachable from
   a phone over LAN or a tunnel. This is the surface that proves the rich
   model the design is built on. Keep it light and brnrd-independent (see
   "Project boundary").
3. **The PR-body projection falls out for free** as the lossy fallback,
   shipped from day one for forge-only and phone readers — and it doubles
   as a forcing function (it makes us generate a real pack against a real
   PR early). Useful, deliberately not the anchor.
4. **CLI/TUI and the hosted brnrd renderer are follow-ups**, same pack,
   when the web shape is proven (TUI closes the terminal-native gap;
   brnrd becomes the clean mobile-without-a-tunnel path once it exists).

The honest tradeoffs, stated plainly rather than hand-waved: a local web
tool is a touch more friction than a TUI for a terminal-native
self-hoster (start a server + open a browser vs. a terminal pane) — the
CLI/TUI follow-up closes that. And good *phone* review for a self-hoster
before brnrd exists means LAN/tunnel access to the local server, or the
degraded PR-body; brnrd is the eventual clean answer. Building before
brnrd is not ideal in the abstract, but it lets diffense define a strong
self-hosting shape on its own terms — provided it stays light enough not
to get buried under brnrd's complexity later.

## Where the runner / publish kernel wire in

diffense hangs off the publish step
([`design-publish-kernel.md`](design-publish-kernel.md)): the runner
emits the pack as the last step before publish, runs `brr review --check`
on it, projects the checked pack with `brr review --pr-body`, and sends
the resulting PR body through the forge-addressed outbox. The daemon still
pushes the branch; the resident owns the review surface and the PR
open-or-refresh send.

**Producer B — runner emission — ships** (2026-06-01) as a gated prompt
fragment ([`src/brr/prompts/diffense.md`](../src/brr/prompts/diffense.md)),
appended to the daemon run prompt by `diffense_emit_enabled` (on by
default since slice 3 wired the consumer; opt out with
`diffense.emit_pack=false`). It instructs: *produce the pack for a
review-worthy committed change under the six clamps; give each card its
gloss → ground-truth-leaf locator; open with a summary, surface
uncertainty cards (assumption / concern / dilemma / out-of-scope /
follow-up) first; ground demos in real test values; then `brr review
--check` it and fix every error before finishing.* When PR creation is
enabled, the same fragment tells the resident to derive the title and
body with `brr review --pr-title` / `--pr-body --relay` and write a
`gate: forge` outbox file carrying `head`, `base`, and `title`
frontmatter. Pack shape is taught by example ([the PR #64 prototype](diffense-prototype-pr64.md)) + this
page, not a duplicated schema doc.

The pack path is **handed to the runner, not assumed.** The runner works
in a worktree whose own `.brr/` is torn down at finalize, so a
cwd-relative `.brr/diffense/...` would die before the resident could
validate and project it. The daemon computes an absolute path in the
*shared* runtime dir and puts it in the Task Context Bundle as `Review
pack path` (`<shared .brr>/diffense/<task-id>/pack.json`); the fragment
writes there, `brr review --check` validates it, and `brr review
--pr-body` projects it. Same plumbing pattern as `response_path` — brr
names the file, the agent fills and consumes it through explicit helper
commands.

**PR creation — agent-owned gate send — ships** (2026-06-10; earlier
daemon-owned creation shipped 2026-06-01 and was removed when the
resident took ownership). Before this the publish kernel picked up the
task-keyed pack after a clean push and `_maybe_open_pr` projected it
inside `daemon.publish`. Now the resident writes a `gate: forge` (or
`gate: github` + `github_action: pull_request`) outbox file whose body is
already the projected PR body. `_deliver_out_of_bound` maps `forge` to
the GitHub gate, and the GitHub delivery loop opens or refreshes the PR
idempotently through the REST API. Policy:

- **On by default** only when both `diffense.emit_pack` and
  `diffense.create_pr` are enabled. Disabling emission removes the prompt
  path that asks for a pack; disabling creation makes the GitHub delivery
  closure refuse the PR send.
- **Create-if-absent, else refresh** keys off the PR head branch. The
  resident sends `head`, `base`, and `title`; the gate lists open PRs for
  that head, creates when absent, and updates title/body when present.
  The push remains the conflict adjudicator: if the daemon cannot push the
  branch, the resident has no remote head worth publishing.
- **The pack travels with the PR.** `project_pr_body` embeds the full pack
  in a trailing `diffense:pack:v1` HTML-comment marker when it fits the
  forge body budget (`extract_pack` is the inverse), so a reader can
  recover the exact pack from the body with no side channel.

**Slice 4 — durable gist-backed renderer with transient fallback — ships**
(transient relay 2026-06-01; gist-first ownership moved 2026-06-12). The
resident still passes `--relay` to `brr review --pr-body`, but that flag
now means "publish a rich review link": create a secret gist containing
the pack JSON, compose brnrd's static shell URL
`/r?pack=<raw gist url>`, and prepend the **Interactive review** link plus
the gist source to the projected body. If gist publication is unavailable
or the repo is private/internal, the old `cloud.relay_pack`
(`POST /v1/daemons/pack` → `GET /r/{token}`) remains the RAM-only
fallback. Best-effort and self-hosted-safe: no gist and no cloud config →
no rich link → the body still carries the projection + embedded pack, and
local `brr review` is the rich surface.

## Adjacencies that ship-or-shipped already

- **The `pr-review` / `pr-review-comment` event handling** in
  [`src/brr/gates/github/`](../src/brr/gates/github) (shipped 2026-05-27)
  is the gate-side of the review loop: brr *reacts* to a human's review.
  diffense is the human-side counterpart (brr *produces* a surface humans
  review well) **and** the consumer of that handling for its feedback
  loop. Boundary doc:
  [`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md).
- **The `brr kb` plan** ([`plan-kb-subcommand.md`](plan-kb-subcommand.md))
  is composable infrastructure for the renderers — `kb doc <page>` feeds
  kb-page cards' zoom levels.
- **The brnrd dashboard plan**
  ([`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)) is the
  home for the hosted web renderer.
- **The ergo proxy** ([`design-agent-ergonomics.md`](design-agent-ergonomics.md))
  shares the reflection-elicitation step and marker transport.

## Productization potential (noted, not chased)

A deliberate aside, because the shape keeps gesturing at it: **strip the
kb-specific parts and the code-change-card format generalizes to any PR
review, on any project.** The zoomable card, the locator anchoring, the
two-axis lore, the uncertainty-first orientation, the tests-as-grounding
demos — none of that depends on brr's kb. A reviewer on a plain Python or
TypeScript repo would get most of the value from the code cards alone.
brr's kb-awareness is the *unfair advantage* (a generic tool cannot read
a `kb/` graph it doesn't have), not a prerequisite for the rest.

This is worth writing down and worth *not* chasing yet. The near-term job
is dogfooding diffense on brr's own PRs and proving the rich shape; a
general-purpose "review cards for any repo" product is a fork in the road
for later, once the pack format and the web renderer have earned their
keep here. Noted so the option isn't forgotten, explicitly deferred so it
doesn't pull focus.

**Framed as two producers of one pack.** The pack format is the
contract; what changes is who fills it. *Producer B* — the runner, at
publish time (see "Where the runner / publish kernel wire in") — is the
near-term one: it has the deepest inputs (conversation, kb graph,
run-state, tests), and the pack is a byproduct of work brr already does,
so it both lifts review and tends to make the run more structured.
*Producer A* — a post-hoc agent triggered on a PR (mention-bot or Action,
pay-as-you-go, self-hostable) over just the diff + repo — is the broader,
standalone story but the shallower pack, and it would ride the same
GitHub App ingress managed mode needs anyway. Decision (2026-06-01):
**B first** — it's the steak (real review lift, in-tree, no new ingress);
A is a self-contained demo deferred until B and the pack format have
earned their keep. Producer A's first substrate slice did land on
2026-06-10: the OSS GitHub gate has a bounded `opened` trigger for newly
created issues and PRs, so a future post-PR producer can wake on PR
creation without opting into every comment. Both emit the same pack into
the same renderers, which is the whole point of the producer/pack split.

## Open questions

- **Pack JSON schema — locked as the `--check` contract (2026-06-01).**
  The discriminated union over item kinds + walkthroughs + uncertainty
  subkinds is now encoded in
  [`src/brr/diffense/pack.py`](../src/brr/diffense/pack.py) and enforced
  by `brr review --check` (v0.1). The
  [PR #64 prototype](diffense-prototype-pr64.md) findings are folded in:
  `code-module-split` / `code-move` are core kinds, the kind set is an
  open core (custom kinds warn, never fail), card-namespaced edge targets
  must resolve while free references don't, and uncertainty cards carry
  `honest_nuance`. Deliberately *not* yet enforced, pending real
  emission: mechanical-stat derivation (the prototype authored stats by
  hand) and `provenance.conversation_msg`, the one field only a
  brr-*produced* pack (Producer B) exercises. A `schema_version` bump
  policy lands with that emission.
- **Graph / inter-card navigation + code rendering at a locator —
  resolved by the spike.** Lateral edges and zoom-drills share one
  breadcrumb heading-bar stack; a code leaf is jump-to-forge at v0
  (`path:line` inline, inline-diff deferred). See "Rendering the zoom."
- **Pack transport — resolved.** The pack stays the producer's; brnrd is
  a transient relay, never a store (see "Where packs live"). The PR-body
  embed threshold is a concrete budget (`_BODY_BUDGET` in `prbody.py`):
  under it the full pack rides in the body marker, over it the embed
  degrades to a "render locally" pointer. The transient brnrd relay
  (`/v1/daemons/pack` + `/r/{token}`, shipped) carries the rich hosted
  view for oversized packs and remote reviewers. Still open: a richer
  hosted renderer (the brnrd dashboard) beyond the reused static render,
  and gating private-repo render links behind the reviewer's session.
- **Pack versioning across iterations.** A PR accrues successive packs as
  the feedback loop iterates; how to store them and render a "what
  changed since I last reviewed" pack-diff.
- **Card-level reviewer state.** Local-only in `.brr/`, or roundtripped
  to forge review state? Likely local-first, forge-roundtrip optional.
- **Aesthetic locking.** The terminal-via-CSS look is validated by the
  spike (dense on desktop, reflows on a phone without literal
  box-drawing). The exact palette/typography lock is still open; the
  direction holds.
- **Animated data-flow trace (the live demo axis).** Promoted from a
  deferred "GIF" to a native direction (see "Animated data flow is a
  native direction"): v0 renders the trace static + zoomable, but the pack
  keeps it steppable so animation is a renderer-only upgrade. Open: the
  animation substrate (CSS/JS step-player vs recorded GIF) and when it
  lands.
- **Locked-abilities axis.** Deferred future direction.
- **LLM token budget for pack generation.** Bounded by the
  always-vs-conditional split, the gloss-not-leaf rule (leaves are
  mechanical), and the six clamps; revisit on real runs.
- **Live agent on cards.** Cost/value addressed when that slice opens;
  same clamps.
- **Naming.** `diffense` adopted as the working name; `pensieve` /
  `holocron` set aside as cosplay-leaning next to the `brr` / `brnrd`
  house style. `diffuse` was tempting on typing ergonomics (a frequent
  fingers-typo) but rejected on meaning: "diffuse" = *scatter / dilute*,
  the opposite of a tool that *concentrates* scattered context into
  focus — and the typed verb is `brr review`, so codename ergonomics
  don't load-bear anyway. The `brr review` verb stays regardless.
  Locking deferred until the spike confirms the brand fits the surface.

## Read next

1. [`diffense-prototype-pr64.md`](diffense-prototype-pr64.md) — the first
   hand-authored pack (against PR #64), rendered, with the pressure-test
   findings that sharpen the schema; the most concrete thing to look at.
2. [`plan-kb-subcommand.md`](plan-kb-subcommand.md) — the kb read surface
   the renderers compose against.
3. [`design-publish-kernel.md`](design-publish-kernel.md) — where pack
   emission, `--check`, and body projection wire into publish.
4. [`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md)
   — the gate-side review-event handling the feedback loop rides.
5. [`subject-kb.md`](subject-kb.md) — the kb graph the pack renders.
6. [`design-agent-ergonomics.md`](design-agent-ergonomics.md) — the
   shared-source / split-audience sibling channel.
7. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md) — the
   home for the hosted web renderer.

## Lineage

Drafted 2026-05-28, reshaped 2026-05-29, out of a conversation across
2026-05-27 – 2026-05-31 that converged the shape over ten refinement
passes:

1. **Audience + generation.** Generic power-user persona; LLM-driven
   generation by the agent that did the work.
2. **Inspect-mode + the Souls-menu-hangout hypothesis.** Reviews as a
   navigable graph of inspection cards; curiosity-driven, not
   gamification.
3. **Perceived gain + the sharp/honest/non-prescriptive clamps.** The
   two-axis lore; discipline against the wall-of-text relapse.
4. **Tests-as-grounding + walkthrough kind + parallel CLI/web rendering +
   promotion to design.**
5. **Small-team audience + the helpful clamp + the `diffense` name +
   uncertainty cards as first-class.**
6. **The zoomable graph + locators + feedback loop + web-first reversal.**
   Cards gain a zoom axis (gloss → detail → ground-truth leaf), unifying
   the kb summary-tree and walkthrough-as-card-group ideas; identity
   gains a resolvable locator; the feedback loop closes through the
   shipped `pr-review-comment` gate; the `follow-up` uncertainty subkind
   and explicit tension references land; a pack validation/render tool
   (`brr review --check`) and a packs-live-in-`.brr`-and-travel-with-the-PR
   transport are specified; mobile + no-app + low-friction reverse the
   surface ordering to web-first (responsive HTML renderer distinct from
   the Textual TUI), demoting the PR body to a lossy fallback; and the
   ergo proxy folds in as a shared-source / split-audience sibling.
7. **TUI dropped near-term + web renderer locked in.** The Textual TUI
   leaves the first cut (a CLI/TUI is a follow-up), which *resolves* the
   substrate-split tension rather than carrying it: one light,
   brnrd-independent responsive-web renderer, built before brnrd for the
   self-hosting story, with the terminal aesthetic expressed in the web
   medium. The concrete zoom interaction lands — ascii-looking cards
   where opening a nested card collapses its parent to a full-width
   heading bar, nesting indefinitely into a breadcrumb stack (lateral
   graph navigation and code-leaf rendering left explicitly open). The
   "make review entertaining" goal is framed as removing *accidental*
   burden (not gamification), gated on trust and with the aesthetic as a
   multiplier. `diffuse` considered and rejected on meaning.
8. **First prototype + ease-in reshaping.** A pack was hand-authored
   against [PR #64](diffense-prototype-pr64.md), validating the schema and
   surfacing fixes folded back here: an **open card-kind taxonomy** (the
   agent declares a `custom` kind and raises the gap as a *meta*
   uncertainty card; recurring kinds get promoted — `code-module-split`
   was the first); a **summary card** opening every pack, reconciling the
   "uncertainty-first" rule into orient → surface-concerns → explore (and
   absorbing the "header / PR-stats-with-context" want); a **gloss-first**
   convention (uncertainty cards lead with the worry in one plain
   sentence, specifics descend); `--check` rejecting unresolvable locators
   as a *blocking* failure (it would have caught the design's own invented
   `cache.get_with_etag`); and a noted-not-chased **productization** path
   (the code-change cards generalize beyond brr's kb). A code gap also
   surfaced (brr publishes a branch, not a PR, so origin context isn't
   threaded — issue #68); no design change.
9. **Renderer spike + two open questions closed.** A generic,
   dependency-free web renderer ([`src/brr/diffense/`](../src/brr/diffense):
   `template.html` + `render.py`) was built and run against the PR #64
   pack, validating the card / zoom / navigation model end to end and
   resolving the two interaction questions pass 7 left open: lateral edges
   and zoom-drills **share one breadcrumb heading-bar stack** (no separate
   graph view at v0), and a **code leaf is jump-to-forge** (the
   commit-pinned permalink, `path:line` inline; inline-diff deferred). The
   terminal aesthetic was confirmed to carry to the web and reflow to a
   phone. Render-only — the flag-a-card action, the local server, and
   runner wiring remain to build.
10. **Conceptual axes + transport correction (post-acceptance refinement,
    2026-05-31).** Added the **invariant** axis (the conserved frame a
    delta is read against; a *threatened* invariant is what an uncertainty
    tension points at) and a **data-shape delta** distinct from the
    signature; reframed the summary card's **entry stats as a rollup of
    the card axes into visual, pre-attentive distributions** (bars / meters
    / heat) with raw size demoted; split walkthroughs into **control-trace
    vs data-trace** ("follow the datum", shape-per-stage) and promoted
    **animated data flow to a native direction** (the trace stays steppable
    so animation is a renderer-only upgrade); gave **kb changes kb-native
    axes** (claim / graph-position / lifecycle; the link-graph walk as kb's
    data-trace) so they stop reading as flattened code cards; and
    **corrected the pack transport** — brnrd is a *transient relay*, never
    a pack store, matching its "data ownership stays at the metadata-graph
    level" stance, with the producer's local `.brr/diffense/<task>/` the
    home and the user-published PR body the durable forge artifact.
    Zachtronics (cost-axis distributions, visible data-in-motion) joins
    Souls / DMC as a stated inspiration. The PR #64 prototype + renderer
    were updated to demonstrate the new axes.

Accepted 2026-05-29 (format refined 2026-05-31): both gates are met — the hand-authored prototype
([`diffense-prototype-pr64.md`](diffense-prototype-pr64.md)) and the
renderer spike ([`src/brr/diffense/`](../src/brr/diffense)) validated the
pack shape and the read model. Implementation (pack generation in the
runner, the `brr review` local server, schema lock, transport) follows in
an implementation plan.
