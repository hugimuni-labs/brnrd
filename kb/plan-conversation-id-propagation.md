# Plan: conversation_id propagation — daemon-side enabler for cross-gate continuity

**Status: accepted 2026-05-26** (locked in PR #40 MR review,
locking pass IV — **plan scope reframed as "ID propagation,
not context expansion"**; `conversation_id` now defined as
the existing `conversation_key` (the human-readable gate
fingerprint already implemented in
`src/brr/conversations.py`), not a separate ULID — closes
the naming gap surfaced by the implementation audit;
**token-budget mindfulness** flagged as a discipline to
carry forward as features layer additional context, not as
a separate plan). Not started; safe to land as soon as
[`design-brnrd-protocol.md`](design-brnrd-protocol.md)'s
conversation-context machinery is real on the brnrd side.

Small daemon-side slice that makes brnrd's metadata-only
conversation graph (see
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Conversation context for failover and dashboard") possible
without brnrd holding any conversation contents. Two pieces:

1. Stamp `Brnrd-Conversation-Id: <conversation_key>` as a git
   commit trailer on every commit brr creates during a task.
2. Include the `conversation_id` (= `conversation_key`) field
   in the `POST /v1/daemons/responses` payload.

Together these make conversation_id a first-class, persistent,
git-sourced identity that brnrd can re-derive from any branch
without holding the linkage table — the metadata index becomes a
cache, not a source of truth.

## What this plan is + isn't (clarified pass IV)

**This plan adds identity propagation, not prompt context.**
The local daemon already injects rich context into the
runner prompt — `prompts/run.md` preamble + the tail of
`kb/log.md` (capped at 10 entries / 4096 UTF-8 bytes) + the
Task Context Bundle (mode / task / delivery contract / branch
metadata / original event body) + the 8 most-recent records
from `.brr/conversations/<key>/<event-id>.jsonl`. This plan
adds **none** of that. All it adds is a stable identity
stamp on commits + a field on response POSTs so brnrd's
cross-gate metadata index can be re-derived from git
without brnrd holding the linkage table itself.

**`conversation_id` = the existing `conversation_key`, not
a separate ULID.** The pre-pass-IV draft of this plan
introduced a ULID `conversation_id` as a parallel identifier
without specifying how it relates to the existing
`conversation_key` (e.g. `telegram:123:`, `github:owner/repo:42`,
`slack:T0CHANNEL:thread_ts`). The implementation audit
showed there's no bridge between them today, and inventing
one would mean carrying two IDs forever. The cleanup: adopt
`conversation_key` as the canonical id. It's already stable
(deterministic from the gate event), already used to key
`.brr/conversations/<safe-key>/`, already human-readable
(self-documenting in git logs), and already implemented.
The trailer becomes:

```
Brnrd-Conversation-Id: telegram:-1001234567890:
```

readable at a glance, immune to ULID-vs-key drift. brnrd-side
the metadata index keys by the same string. No new id type
to maintain.

(If we ever need a ULID-shaped opaque identifier — e.g. for
a public-facing url that shouldn't leak chat ids — derive it
deterministically from the conversation_key with a stable
hash. That's a v-next concern, not a launch one.)

**Token-budget discipline (not a separate plan).** The
implementation already has byte-caps on the `kb/log.md`
injection (4096 UTF-8 bytes, max 10 entries) but no overall
prompt-token budget, no truncation of `event.body`, no
platform-history fetch with truncation. As future features
layer richer context into the prompt (cross-gate recall,
semantic-store lookups, voice transcripts, visual-graph
summaries — see
[`subject-managed-mode.md`](subject-managed-mode.md)), the
shape that grows naturally is: each context source declares
its byte / token budget; the prompt assembler enforces a
total budget with per-source minimums + best-effort
expansions. That's the discipline to hold onto as features
land; this plan doesn't add to the budget (identity stamp
is zero-cost), so no separate plan page is needed.

## Status (implementation)

**Not started.** Blocked on:

- [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
  acceptance — the conversation-context machinery on brnrd's
  side consumes what this plan produces; the daemon-side work
  should match the contract. **(Now accepted as of pass III;
  unblocked.)**
- Existing brr conversation tracking lives in
  `src/brr/conversations.py`; this plan extends rather than
  replaces it.

## Goals

- Every brr-created commit carries a `Brnrd-Conversation-Id`
  trailer linking it to the conversation it belongs to.
- Every `POST /v1/daemons/responses` carries the
  `conversation_id` field so brnrd's metadata index stays current
  without re-walking git on every event.
- Self-hosted brnrd users (and OSS users without brnrd at all)
  see the trailer as harmless metadata — no behavioural change.
- The conversation_id is deterministic from existing brr
  conversation tracking — no schema migration needed.

## Done definition

- `git commit` calls from brr (in `src/brr/git.py` /
  `src/brr/publish.py` / wherever the runtime publish kernel
  composes commits) include `--trailer "Brnrd-Conversation-Id:
  <ulid>"` on every commit it creates.
- The runner CLI, when invoked by brr, is instructed (via prompt
  prologue or runner config) to preserve the trailer on any
  commits it makes — so commits authored by the agent inside
  the env also carry the trailer.
- `POST /v1/daemons/responses` payload schema gains
  `conversation_id: <ulid>` as a required field; cloud-gate
  adapter populates it from the conversation the response
  resolves.
- Unit tests cover: trailer round-trip (commit + read back),
  conversation_id POST present in response payload,
  conversation_id stability across daemon restarts (same
  conversation → same id).
- Documentation in `src/brr/docs/conversations.md` documents the
  trailer convention — short, factual, "brr stamps every commit
  with a `Brnrd-Conversation-Id` trailer so the managed-mode
  cross-gate graph can re-derive conversation linkage from any
  branch."

## Slices

### Slice 1 — Trailer stamping in the publish kernel

Steps:

1. Locate every place brr invokes `git commit` (publish kernel,
   daemon-side housekeeping commits, kb-log squash commits, etc.).
2. Extend each call to add `--trailer "Brnrd-Conversation-Id:
   <conversation_key>"` where `<conversation_key>` is the
   current task's `conversation_key` from
   `src/brr/conversations.py` (already derived per-task; see
   `conversations.conversation_key_for_event`).
3. For the *runner's* commits (made inside the env by the
   agent's `git commit`), pass the conversation_key to the
   runner via env var `BRR_CONVERSATION_ID` and include in
   the prompt prologue a one-line instruction: "When you
   commit, include
   `--trailer 'Brnrd-Conversation-Id: $BRR_CONVERSATION_ID'`."
   (Light nudge; if the agent forgets, the brnrd-side fallback
   is the inference rules from the design page.)
4. For envs that wrap `git commit` (e.g., docker env's
   post-task auto-commit on uncommitted changes), include the
   trailer automatically.

**Estimate.** ~50 LOC across the publish kernel + ~30 LOC tests.

### Slice 2 — conversation_id in response payload

Steps:

1. Extend the `Response` data type (wherever it's defined in
   `src/brr/responses/` or equivalent) with a `conversation_id`
   field, populated from the originating event's conversation
   binding.
2. Update the cloud-gate adapter's response-post loop to include
   `conversation_id` in the POST payload to
   `/v1/daemons/responses`.
3. BYO gates (TG / GH / Slack on the daemon side) don't need
   this — they post responses directly to the platform, no
   `/v1/daemons/responses` involved.
4. brnrd-side: accept the new field, update the
   `event_metadata` row's `conversation_id` if previously null.

**Estimate.** ~30 LOC daemon-side + ~50 LOC brnrd-side + ~50
LOC tests.

### Slice 3 — Documentation + cross-references

Steps:

1. `src/brr/docs/conversations.md` — add a "Conversation
   identity in git" section explaining the trailer, why it
   exists, and noting it's harmless metadata for OSS users.
2. `src/brr/docs/managed-mode.md` — cross-reference the trailer
   when explaining how cross-gate continuity works in failover.
3. AGENTS.md (or equivalent runner-side prompt prologue
   template) — one-line mention of the trailer for the agent.

**Estimate.** ~100 LOC docs.

## What ships where

| Component | Lives at |
|-----------|----------|
| Trailer stamping on brr-side commits | `src/brr/publish.py` (or wherever the publish kernel lives) |
| `BRR_CONVERSATION_ID` env-var passthrough to runners | `src/brr/runner.py` / env-protocol implementations |
| Prompt prologue nudge for agent commits | Runner-side prompt template |
| `conversation_id` field on Response | `src/brr/responses/` or equivalent |
| Cloud-gate adapter response-post payload | `src/brr/gates/cloud.py` |
| brnrd-side acceptance + metadata update | `src/brnrd/` |
| Docs | `src/brr/docs/conversations.md` + `src/brr/docs/managed-mode.md` |

## Out of scope

- **Re-deriving conversation_id for historical commits without
  the trailer.** Pre-existing branches don't have the trailer;
  brnrd handles this via the inference rules in the design
  page (branch-based lookup, reply-to chain, sticky chat
  binding, fallback new id). No backfill.
- **Cross-conversation aggregation** (e.g., "all conversations
  about deploys"). Tags / labels are a separate concern; not in
  scope here.
- **Conversation_id rotation / renaming.** Conversation IDs
  are stable `conversation_key` strings derived from the gate
  event; once assigned, they don't change. Rename / merge is
  a v-next operation if anyone asks.
- **Encrypting the trailer.** The trailer is metadata (ULID
  only); not sensitive. Plaintext is fine.

## Risks

- **Agent forgets the trailer.** The runner's `git commit`
  inside the env might not include the trailer if the prompt
  nudge fails. Mitigation: brnrd-side inference rules in the
  design page recover the linkage from branch name + reply-to
  chain + sticky chat binding; the trailer is a faster path,
  not the only path. If the agent reliably misses the trailer
  in practice, escalate to a post-task git-rebase pass that
  adds trailers to brr-task commits.
- **Trailer noise on commits.** Some users may not want extra
  trailers cluttering their git log. Mitigation: the trailer is
  one short line, follows git convention, doesn't break tooling.
  Self-hosted users can opt out with a config flag if it
  becomes a real complaint.
- **Conversation_id drift between daemon restarts.** If the
  daemon loses conversation state across restarts and assigns
  a new id to what should be the same conversation, the graph
  fragments. Mitigation: `conversation_key` is **derived from
  the gate event** (`platform:chat_id:` for chat platforms,
  `github:owner/repo:issue_number` for GitHub), not assigned
  by the daemon — it's deterministic by construction and
  stable across daemon restarts. `.brr/conversations/<safe-key>/`
  directories already carry it in the path.
- **Stamping on commits made before brnrd integration.**
  Trailing rebase of pre-brnrd commits is risky. Mitigation:
  only stamp from a forward date; pre-existing commits use the
  inference fallbacks on the brnrd side.

## Read next

1. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
   "Conversation context for failover and dashboard" for the
   brnrd-side machinery this plan feeds.
2. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic context (data minimization, cross-gate continuity).
3. [`src/brr/docs/conversations.md`](../src/brr/docs/conversations.md)
   for the existing per-gate-thread conversation log this
   extends.

## Lineage

- 2026-05-25 — drafted as part of the brnrd-naming-keep +
  cross-gate-conversation-context reshape pass (pass 3). The
  trailer + POST-field mechanics make the metadata-only
  conversation graph on brnrd possible without brnrd holding
  conversation contents. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (fourth reframe breadcrumb).
- 2026-05-26 (locking pass IV — scope clarification, key
  unification, token-budget framing). Three updates, no
  contract change to brnrd:
  1. **Scope reframed** from ambiguous-context-plus-identity
     to **identity propagation only**. A new "What this plan
     is + isn't (clarified pass IV)" section names that the
     local daemon already injects rich context (run.md
     preamble + capped kb/log.md tail + Task Context Bundle +
     8 recent conversation records), this plan adds **none**
     of that — only the identity stamp on commits + the field
     on response POSTs.
  2. **`conversation_id` = `conversation_key`** (the
     human-readable gate-fingerprint string already
     implemented in `src/brr/conversations.py`), not a
     separate ULID as the pre-pass-IV draft suggested. The
     implementation audit showed there's no bridge between
     the two today; inventing one would mean carrying two
     ids forever. Adopting the existing key closes the gap
     at zero migration cost. Slice 1 + Slice 2 step
     descriptions + the Risks section updated to match.
     Trailer example now reads
     `Brnrd-Conversation-Id: telegram:-1001234567890:` —
     self-documenting in git logs.
  3. **Token-budget discipline** flagged as a discipline
     to carry forward as features layer richer prompt
     context (cross-gate recall, semantic store, voice /
     graphs), with the natural shape sketched
     (per-source byte/token budgets + assembler enforcing
     a total with per-source minimums + best-effort
     expansions). **Not a separate plan** per the user's
     "I wouldn't add a new plan for prompt budgeting, it
     is just something we need to be mindful I guess" —
     captured here as a discipline note so it doesn't
     get lost. This plan itself adds zero to the prompt
     budget (identity stamp is zero-cost). Status
     promoted from "Not started" framing to "accepted +
     unblocked" — `design-brnrd-protocol.md` was
     accepted in locking passes II + IV.
     Driven by the user's "please check against what is
     already implemented... we need to be mindful of the
     token consumption. So we need a balance between
     supplying enough details to understand the full
     context, and blurring the context with a too large
     initial scope."
