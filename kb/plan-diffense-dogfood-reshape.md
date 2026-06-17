# Plan: diffense dogfood reshape

Status: active

Companion to [`design-diffense.md`](design-diffense.md). This page is
the 2026-06-17 dogfood correction: the accepted diffense model still has
the right ambition, but recent generated packs do not yet make review
faster or more engaging enough to justify default emission.

Tracker: #152.

## Evidence sample

Sampled the 10 newest PRs with diffense links from the latest 30 GitHub
PRs, including the links the maintainer collected: #149, #147, #144,
#143, #140, #137, #135, #134, #129, and #127.

| PR | Cards | Unc. | Locators | Forge locators | Edges | Zoom | Avg gloss |
|----|------:|-----:|---------:|---------------:|------:|-----:|----------:|
| #149 | 8 | 2 | 5 | 0 | 0 | 0 | 268.5 |
| #147 | 7 | 1 | 6 | 0 | 4 | 0 | 152.1 |
| #144 | 10 | 5 | 8 | 8 | 0 | 0 | 237.1 |
| #143 | 7 | 3 | 5 | 0 | 3 | 0 | 399.1 |
| #140 | 11 | 5 | 10 | 0 | 0 | 0 | 279.3 |
| #137 | 7 | 2 | 6 | 0 | 7 | 0 | 158.3 |
| #135 | 10 | 3 | 9 | 0 | 6 | 0 | 340.2 |
| #134 | 8 | 3 | 6 | 0 | 7 | 0 | 327.5 |
| #129 | 7 | 1 | 6 | 0 | 10 | 0 | 154.3 |
| #127 | 12 | 3 | 8 | 0 | 0 | 0 | 438.8 |

Nine packs passed `brr review --check` with 0 warnings. #140 passed with
0 errors and 5 warnings for unknown `walk` kinds. That is the important
finding: the painful experience is not mostly a schema-validity problem.
The generated surface can be valid while still failing the review job.

## What does not work

1. **The default composition is still a serial document.** The renderer
   opens with a full summary card, then concern rows, then "the change" as
   rows. That is prettier than a PR body, but it is still a linear read.
   The accepted design says glance / dive / wander; the sample had zero
   `zoom` ladders across all 10 packs, and four packs had zero lateral
   edges.
2. **The gloss layer is too verbose to skim.** Average gloss length ranged
   from 152 to 439 characters per pack. Several index rows read like
   paragraphs. The surface asks the reviewer to keep reading rather than
   letting them decide where to spend attention.
3. **Cards are file-first, not decision-first.** Most "change" cards map
   to files or kb pages. That is useful as a receipt, but it does not
   answer the reviewer's first question: "what judgment do I need to make
   before merging this?"
4. **Code and kb locators are often not actionable in the hosted view.**
   Of 69 locators in the sample, 61 were local-only. Eight had forge
   links, all from one pack. A hosted reviewer usually sees a path/line
   label instead of a click target, and the links that exist point at blob
   lines rather than PR diff hunks or rendered kb pages.
5. **Uncertainty cards are the best material, but they are under-turned.**
   The cards often contain exactly the human-useful tension: assumptions,
   dilemmas, and scope flags. The renderer gives them a section, but it
   does not turn them into a verdict lane, review checklist, or first
   action.
6. **The validator protects structure, not review utility.** `--check`
   catches broken locators and missing axes. It does not fail a pack that
   has no dive path, local-only hosted locators, paragraph-sized glosses,
   or a file inventory masquerading as a review map.

## Reshape

Keep diffense, but stop treating it as a nicer PR description. The next
shape should be a **decision-first review board**: the first screen should
answer "what should I decide, where is the evidence, and can I merge this
now?" The graph can still exist, but as a way to move from decision to
evidence, not as a decorative card taxonomy.

Default lanes:

- **Verdict lane.** Must-read uncertainties, merge blockers, explicit
  assumptions, and scope flags. Each item should say whether it blocks
  merge, asks for maintainer judgment, or is just context.
- **Change map.** Three to five behavior / design slices, not file rows.
  Each slice gets: why it exists, what user-visible or maintainer-visible
  behavior changes, which invariant it preserves or threatens, and the
  test/kb receipt.
- **Ground truth.** Every slice has immediate links to PR diff hunks,
  rendered kb pages, or exact files at the commit. Hosted review must not
  strand the user at a local path label.
- **Context drawer.** Conversation and kb background appear only when they
  change the review decision. Background should be expandable, not part of
  the default reading path.

Producer changes:

- Generate fewer, stronger cards: one summary/verdict card, one to three
  decision or risk cards, three to five slice cards, and optional
  walkthrough cards only when a datum or flow genuinely benefits from
  stepping.
- Make every slice card answer a reviewer question, not merely identify a
  file.
- Treat uncertainty as first-class review input: each uncertainty carries
  `blocks_merge: true|false`, `needs_user_judgment: true|false`, or an
  equivalent explicit review status.
- Prefer compact structured fields over paragraphs. A card can keep depth,
  but the first screen should be dense and scannable.

Renderer changes:

- Replace the summary-card-plus-index landing view with a board/table:
  verdict lane first, change slices second, evidence links third.
- Make the "open code" action target PR diff hunks or rendered kb pages
  when the pack is shown from a PR. Blob-line links are a fallback, not the
  default.
- Keep the terminal aesthetic only where it improves readability. The
  aesthetic cannot compensate for a prose-heavy structure.

Validator changes:

- Add a review-utility lint tier. Candidate lints:
  - hosted/PR packs warn or fail when changed-file locators are local-only;
  - packs with more than a few cards need at least one real dive path
    (`zoom`, walkthrough stages, or evidence links);
  - gloss medians above a tight threshold warn;
  - item cards that name files but lack a reviewer question warn;
  - unknown kinds are warnings today, but published packs should include a
    meta uncertainty or use `custom` deliberately.

## Policy while reshaping

Keep `diffense.emit_pack` and `diffense.create_pr` off by default. Use
diffense only for explicit dogfood or review experiments until the board
shape lands. The current surface can slow fast review down, which is worse
than no surface because it spends attention while claiming to save it.

Do not delete the idea. The sampled packs show real value in uncertainty
capture, summary shaping, and kb/code receipts. The failure is composition:
the useful material is presented as a tasteful transcript instead of a
review instrument.

## Acceptance criteria

- A reviewer can land on the hosted view and identify merge blockers,
  judgment calls, and the core change slices in under one minute.
- Every change slice has a working hosted evidence link to a PR diff hunk,
  rendered kb page, or exact source file.
- The landing view is not a paragraph list. It is a compact board or table
  whose rows support triage.
- The validator can reject or warn about packs that are schema-clean but
  review-useless in the ways this sample exposed.
- Re-test against at least #147 (small code+kb fix), #134 (runtime code),
  and #143/#140 (large design/kb-heavy work) before re-enabling default
  review-pack generation.
