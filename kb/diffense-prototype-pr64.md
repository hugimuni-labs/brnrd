# diffense prototype: a hand-authored review pack for PR #64

Status: prototype artifact (2026-05-29) — validates the
[`design-diffense.md`](design-diffense.md) card schema against a real PR
before that schema is locked. Not a runner emission; hand-authored from
the repo and [PR #64](https://github.com/Gurio/brr/pull/64).

The pack itself is [`diffense-prototype-pr64-pack.json`](diffense-prototype-pr64-pack.json)
(the contract instance the future web renderer / spike consumes; runtime
home would be `.brr/diffense/64/pack.json`). This page is the
human-readable companion: the cards **rendered** (so you can look at the
shape without a renderer), then the **pressure-test findings** — what the
schema expressed well, what it could not, and the concrete schema changes
this exercise surfaced.

## Why PR #64

[#64](https://github.com/Gurio/brr/pull/64) (`fix: poll GitHub PR review
comments for @-mention triggers`, 2642+/1221−, 23 files) is three stories
braided into one PR — the **fix** (review comments live on
`/pulls/comments`, were invisible), a **monolith→package refactor**
(`github.py` 1052 lines → 12 modules), and a **feature** (conditional
polling + review-summary events) — plus a **new kb design page** with
cross-link wiring. That braid is the stress I wanted: it tests whether
curated cards + a walkthrough can untangle the stories instead of
mirroring 3863 diff lines, and the big refactor probes whether the schema
even *has* the right card kind for a module split (spoiler: it does not —
finding 1). It is also thematically apt: the gate code here is the exact
`pr-review-comment` path diffense's own feedback loop will ride.

Ten curated cards stand in for 23 files. That ratio is the point — the
pack is a *story*, not a diff.

## The pack, rendered (reading order — uncertainty first)

### 1. `uncertainty · concern` — the seen-id cap

```
┌ uncertainty · concern ─────────────────────────────── med ─┐
│ id        unc:seen-cap-dedup                                │
│ tension   bounded cursor state  ⟂  exactly-once delivery    │
│ where     polling.py:283  (sorted(seen)[-_SEEN_CAP:], ×7)   │
│           _SEEN_CAP = 500  (constants.py:26)                │
│                                                             │
│ unclear   Each poller keeps only the 500 highest (newest)   │
│           seen ids and evicts the oldest. On a very busy PR │
│           an evicted id could re-fire.                      │
│ nuance    Narrower than it looks: `since` advances to       │
│           latest_seen, so an evicted id only re-fires if    │
│           that old comment is *edited* after eviction. The  │
│           since-cursor + seen-set is belt-and-suspenders.   │
│ proposed  Raise the cap for review cursors, switch to a     │
│           timestamp-window dedup, or scope seen per-PR.     │
│ edges     related item:polling.review-comments              │
│ from      commit 297bfaaca                                  │
└─────────────────────────────────────────────────────────────┘
```

### 2. `uncertainty · out-of-scope` — summary-only reviews

```
┌ uncertainty · out-of-scope-flag ───────────────────── low ─┐
│ id        unc:summary-only-reviews                          │
│ tension   poll-based discovery within budget  ⟂  full       │
│           review coverage                                   │
│ unclear   A reviewer who Approves with a summary and no     │
│           line comments makes a pull_request_review that    │
│           neither /issues/comments nor /pulls/comments      │
│           surfaces. OSS can't see it without per-PR         │
│           /reviews scans (API budget). Left to brnrd.       │
│ proposed  No OSS action; brnrd's webhook closes it at zero  │
│           polling cost. Documented in the new design page.  │
│ edges     related item:kb.gate-vs-brnrd-design              │
│ from      commit 6922cd3fc · kb-grounded, not code-guessed  │
└─────────────────────────────────────────────────────────────┘
```

### 3. `uncertainty · follow-up` — reactions-as-signal

```
┌ uncertainty · follow-up ───────────────────────────── low ─┐
│ id        unc:reactions-followup                            │
│ tension   this PR's scope  ⟂  full reviewer ergonomics      │
│ next      A +1 reaction as a one-tap "approve" for the      │
│           coming permission-prompt UX, so users needn't     │
│           type @brr-bot approve. Filed, brnrd-side first.   │
│ edges     related item:kb.gate-vs-brnrd-design              │
│ from      commit 6922cd3fc                                  │
└─────────────────────────────────────────────────────────────┘
```

### 4. `walkthrough` — review-comment round-trip (the fix, end to end)

```
┌ walkthrough ───────────────────────────────────────────────┐
│ id        walk:review-comment-round-trip                    │
│ story     An @-mention on an inline diff comment becomes a  │
│           task and an in-thread reply.                      │
│ setup     reviewer mentions @brr on a diff line of a PR     │
│ action    mention poller reads /pulls/comments, extracts    │
│           pr/path/line, emits a pr-review-comment event     │
│ outcome   daemon scopes a task to that hunk; reply posts    │
│           in-thread via /pulls/{n}/comments/{cid}/replies   │
│ members   1 → item:polling.review-comments  (poll)          │
│           2 → item:delivery.in-thread-reply (reply)         │
│ grounded  test_mention_trigger_creates_event_for_pr_review_ │
│           comment · test_response_to_pr_review_comment_     │
│           replies_in_thread                                 │
│ note      this *is* diffense's own feedback-loop path       │
└─────────────────────────────────────────────────────────────┘
```

### 5. `code-fn-new` — the poller pass that catches review comments

```
┌ code-fn-new ───────────────────────────────────────────────┐
│ id        item:polling.review-comments                      │
│ where     polling._poll_mention_review_comments             │
│           polling.py:201-295                                │
│ what      Reads inline review-line comments from            │
│           /pulls/comments, filters for the mention, emits   │
│           pr-review-comment events with path + line.        │
│ enables   Inline diff-thread mentions reach the gate at     │
│           all — before this they were silently dropped.     │
│ stats     emits   pr-review-comment                         │
│           polls   /repos/{repo}/pulls/comments              │
│           cursors +review_comments_since,                   │
│                   +seen_review_comment_ids                  │
│           tests   +1 integration                            │
│ demo      GET /pulls/comments?since=… → 200                 │
│           → {kind: pr-review-comment, path:'src/x.py',      │
│              line:42, pr:64, branch_target:'feature/x'}     │
│ edges     called-by polling._poll_mention_trigger           │
│           calls     _poll_mention_review_summaries          │
│           shares-invariant unc:seen-cap-dedup               │
│           part-of  walk:review-comment-round-trip           │
│ from      commit 297bfaaca                                  │
└─────────────────────────────────────────────────────────────┘
```

### 6. `code-fn-edit` — conditional GET (the *real* ETag home)

```
┌ code-fn-edit ──────────────────────────────────────────────┐
│ id        item:client.conditional-get                       │
│ where     client._request (etag_store) / _api_get           │
│           client.py:41-137                                  │
│ what      Optional etag_store sends If-None-Match, returns  │
│           (None, headers) on 304 without raising, refreshes │
│           the store on 2xx. Pollers thread cursor['etags']. │
│ enables   Quiet-repo polling costs ~0 REST budget — 304s    │
│           are free; a stale ETag self-heals for one 200.    │
│ stats     sig Δ  + etag_store kwarg (_request, _api_get)    │
│           conditional /issues · /issues/comments ·          │
│                       /pulls/comments                       │
│           tests +3 transport + 1 cursor-threading           │
│ demo      GET /issues/comments              → 200 ETag "abc"│
│           GET … If-None-Match: "abc"        → 304 (rate 0)  │
│ tests     test_request_sends_if_none_match_when_etag_cached │
│ ⚠ note    design mock card #1 attributed this to a          │
│           'cache.get_with_etag' symbol that does NOT exist; │
│           grounding put it where it really lives.           │
│ from      commit 7c3d3d8ae                                  │
└─────────────────────────────────────────────────────────────┘
```

### 7. `code-fn-edit` — in-thread reply routing

```
┌ code-fn-edit ──────────────────────────────────────────────┐
│ id        item:delivery.in-thread-reply                     │
│ where     delivery._deliver_responses / _thread_reply_body  │
│           delivery.py:69-135                                │
│ what      Routes pr-review-comment responses to             │
│           /pulls/{n}/comments/{cid}/replies (in-thread),    │
│           others to /issues/{n}/comments; quote-prefaced.   │
│ enables   Replies land in the diff thread the reviewer      │
│           asked in, not as orphan top-level comments.       │
│ tests     test_response_to_pr_review_comment_replies_in_    │
│           thread                                            │
│ edges     part-of walk:review-comment-round-trip            │
│ from      commit 297bfaaca                                  │
└─────────────────────────────────────────────────────────────┘
```

### 8. `code-module-split` — the monolith → package (PROVISIONAL kind)

```
┌ code-module-split  ⟪kind not in the design — see finding 1⟫ ┐
│ id        item:gate.package-split                           │
│ where     github.py (1052 lines)  →  github/ (12 modules)   │
│ what      Splits the monolith into a package; public        │
│           surface preserved verbatim in __init__.__all__.   │
│ enables   Separates a brnrd-reusable pure core (paths,      │
│           cache, parse) from OSS-only transport (client,    │
│           state, wizard, polling, delivery, progress,       │
│           loop) — the seam codified in the new design page. │
│ stats     before 1 file / 1052 lines · after 12 files       │
│           surface preserved (10 names in __all__)           │
│           reusable paths·cache·parse                        │
│ edges     implements / part-of-same-decision                │
│           item:kb.gate-vs-brnrd-design                      │
│ from      commit 5c3e589b9                                  │
└─────────────────────────────────────────────────────────────┘
```

### 9. `kb-page-new` — the OSS-vs-brnrd boundary doc

```
┌ kb-page-new ───────────────────────────────────────────────┐
│ id        item:kb.gate-vs-brnrd-design                      │
│ where     kb/design-github-gate-vs-brnrd-app.md (227 lines) │
│ what      New boundary doc (Status: accepted 2026-05-27):   │
│           what OSS owns, what brnrd owns, which modules      │
│           brnrd imports from brr.gates.github.              │
│ enables   The OSS/managed code seam is a citable contract;  │
│           the managed-gates plan leans on it.               │
│ stats     new page · lifecycle accepted                     │
│           inbound-links 0 → 6                                │
│           siblings plan-managed-gates-launch + design-git-  │
│                    layer-rework updated to point here       │
│ zoom      gloss → section summaries → rendered page         │
│ edges     implemented-by item:gate.package-split            │
│           referenced-by subject-managed-mode                │
│ from      commit 6922cd3fc                                  │
└─────────────────────────────────────────────────────────────┘
```

### 10. `test-add` — 304 is free

```
┌ test-add ──────────────────────────────────────────────────┐
│ id        item:test.etag-304                                │
│ where     test_request_304_returns_none_and_preserves_      │
│           cached_etag  (tests/test_github_gate.py:224)      │
│ story     A 304 makes _request return (None, headers)       │
│           without raising and leaves the cached ETag in     │
│           place, so the next poll stays conditional.        │
│ stats     exercises client._request (304 branch)            │
│           asserts   payload None; etag preserved            │
│           fixtures  shares _FakeGitHubResponse              │
│ edges     exercises item:client.conditional-get             │
│ from      commit 7c3d3d8ae                                  │
└─────────────────────────────────────────────────────────────┘
```

## What the schema handled well

- **Curation held on a big PR.** 23 files / 3863 diff lines compressed to
  ten cards that tell the PR's story without a 1:1 hunk dump. The
  braided-stories problem (fix / refactor / feature) was real, and one
  walkthrough plus edge-linked item cards untangled it. This is the
  central thesis surviving contact with a real change.
- **Leaves-by-reference kept the pack small and honest.** Zoom leaves are
  locators, not pasted code; the pack is ~430 lines of JSON for a
  3863-line diff. Token cost stays bounded exactly as the design claims,
  and ground truth is always one resolve away.
- **Two-axis lore earned its place.** "what it is" + "what it enables" was
  natural to write for every code card, and the possibility axis
  (304s are free; inline mentions now reach the gate; the pure core is
  brnrd-reusable) is the part a raw diff never tells you.
- **Uncertainty-first reading order surfaced the real risks** (the
  seen-cap, the summary-only gap) at the top, above the mechanics.
- **The kb-aware advantage is concrete.** `kb-page-new`'s "inbound-links
  0 → 6" stat is precisely the review signal a raw diff hides, and it is
  mechanically computable from the kb graph.

## Pressure-test findings (proposed schema changes)

The point of the exercise: what the schema needs *before* it is locked.
Feeds [`design-diffense.md`](design-diffense.md) → "Open questions → Pack
JSON schema."

1. **Add a `code-module-split` / `code-restructure` item kind (and a
   `code-move`).** The single most important change in #64 — `github.py`
   (1052 lines) → a 12-module package — has no honest home in the
   enumerated kinds. A `code-fn-delete` + twelve `code-fn-new` would lie
   (nothing was created; code *moved*), and a per-function card storm
   would bury the one fact that matters: the public surface is preserved
   and the split axis is brnrd-reuse. The kind wants stats for
   before/after file count, a *surface-preserved* invariant (the
   `__all__` list), and the split rationale. It generalizes the existing
   `kb-page-split`. A sibling `code-move` (a symbol relocated unchanged)
   is implied by the same PR.

2. **`--check` must resolve locators — and that is load-bearing.** The
   validator stand-in (`python3`, below) passed only because the cards
   are grounded in the repo. The same check would have *rejected* the
   design doc's mock `cache.get_with_etag` (no such symbol; the real ETag
   logic is `client._request(etag_store=…)`). Confirms two design rules
   with teeth: locators are commit-pinned (I used the PR **head** SHA, not
   the merge commit — a merged-PR reviewer wants head), and resolution is
   a hard gate, not a nicety.

3. **Edges target either a card or a bare repo symbol — the schema must
   say which.** Some edges point at card ids (`item:` / `unc:` /
   `walk:`); others point at peers not promoted to cards
   (`polling._poll_mention_trigger`, `cursor.etags store`,
   `constants._COMMENT_KINDS`). The validator can only resolve the
   former. Proposal: `edge.target` is `{card: <id>}` **or**
   `{locator: …}`, so a non-carded edge still carries something
   resolvable instead of a free-text string.

4. **Uncertainty cards need an `honest_nuance` slot.** The design's mock
   concern overstated the seen-cap risk ("could re-surface an
   already-handled comment"). Grounding forced the true, narrower version
   (only an *edited* old comment, past the cap, on a busy PR — because the
   `since` cursor is belt-and-suspenders). Without a dedicated place for
   the honest bound, uncertainty cards drift into FUD; with it, the honest
   clamp has somewhere to bite. Severity should track the nuance.

5. **The braided-PR lens wants a `story` / `theme` grouping — but
   provenance-by-commit nearly gives it for free.** #64 is three commits =
   three stories. I carried one walkthrough (the fix) and tied the
   refactor/feature cards by `part-of-same-decision` edges, but a renderer
   would want a "group by story" view. Since every card already records
   `provenance.commit`, a renderer can derive the grouping; the open
   question is whether to also allow an explicit pack-level `themes` list
   for stories that span commits.

6. **Provenance's `conversation_msg` is the field a hand-authored
   prototype can't exercise.** It is `null` here (no `.brr/conversations/`
   for a hand-authored pack). On a real runner emission it anchors each
   card to the message where the agent decided it — the richest, most
   diffense-specific provenance. **Next prototype should run on a
   brr-*produced* PR** to pressure-test that field and the uncertainty
   cards' honesty under real run-state.

7. **Derive mechanical stats, don't hand-author them.** The
   `inbound-links 0 → 6` count and the callers-updated counts are
   mechanical (walk the kb graph; grep callers). I hand-counted them; the
   real generator computes them, and `--check` should flag a stat that
   claims a number the repo contradicts.

8. **Minor: a `.json` in `kb/` is a slight smell.** kb is a Markdown
   graph; this data sibling sits fine as a linked artifact for a
   prototype, but once `src/brr/diffense/` exists, prototype packs likely
   belong under an `examples/` tree there, with `kb/` holding only the
   findings prose.

None of these block the design; all of them sharpen the schema the
implementation plan will lock.

## How it was validated (a `brr review --check` stand-in)

A throwaway `python3` script checked the pack the way the future
`brr review --check` will: JSON well-formedness, every `reading_order` id
maps to a card, all card kinds present, **every locator's file exists and
line is in range**, and every card-id-shaped edge resolves. All passed —
and finding 2 is the lesson: the locator-resolution pass is the cheap
guard that keeps invented symbols out of a pack.

## Read next

- [`design-diffense.md`](design-diffense.md) — the design this validates;
  see "The card model" and "Open questions → Pack JSON schema."
- [`diffense-prototype-pr64-pack.json`](diffense-prototype-pr64-pack.json)
  — the pack itself (the contract instance).
- [`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md)
  — the boundary doc PR #64 added, cited by several cards.
