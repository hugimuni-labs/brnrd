# Design: portal grammar & the reconcile/projection layer

Status: active (2026-06-20; #159 design contract drafted after #148
closed; implementation slices pending).

This page is the current design contract for
[#159](https://github.com/Gurio/brr/issues/159): the output-frame grammar
and the run-mailbox assumptions that must survive a future parallel-run
experiment. #148 has now shipped the current-protocol control loop, so
this page names the next shape rather than waiting for more dogfood.

The issue title still says "cockpit" because that was the live label
when it was opened. The settled product idiom is **scrolls and portals**:
the resident's generated stream is the surface, and portals are the
marked places where that stream turns to the world.

> Provenance: a multi-turn Telegram design conversation (2026-06-17 to
> 2026-06-18) that started from the "forge as a synced directory" idea
> (#117) and turned into "interrupts as portals / make the output be the
> surface." The 2026-06-19 to 2026-06-20 dogfood passes supplied the
> concrete evidence: PLAN shape, live `inbox.json` habits, stdout wording
> drift, outbox frontmatter footguns, burst coalescing, and failure
> deferral.

## Current shipped surface

This is the live substrate #159 builds on:

- The **gate stays thin**. Telegram, forge, and future transports should
  remain pipes. Rendering, deduplication, desired-state reconciliation,
  and portal ownership live one layer above the gate.
- `brr docs portals` is the inspected manual for today's control-file
  grammar: outbox markdown files, `event:` and `gate:` routes,
  `.card`, `.keepalive`, and `inbox.json`.
- #148 shipped the current-protocol control loop: PLAN message shape,
  live `.card`/outbox dwelling habits, Codex runner adapter wording,
  and the explicit pre-closeout `inbox.json` read.
- #128 shipped the run/task storage rename, burst-coalescing dispatch,
  and operational-failure sibling deferral. Per-run claims,
  resident-authored postponement, and run-keyed response/outbox routing
  remain open.
- The outbox parser now tolerates the common missing-opening-fence
  shape (`event: <id>\n---\nbody`) so it does not leak selector text or
  misroute to the lead event.

What is **not** shipped: in-generation portal syntax, run-keyed primary
outbox/response paths, event leases, resident-authored event deferral,
parked-portal mailbox records, portal helper commands, and any parallel
local execution. Single-flight remains the default executor.

## Settled design calls

1. **Reconcile/projection is the floor above gates.** Append-log and
   desired-state are reconcile semantics, not transports. Both can ride a
   messenger, forge, or any later gate.
2. **Portal grammar is the output frame.** The resident's generated
   stream should carry the shapes the user sees *and* the daemon can act
   on. Today's files are the implementation; the product contract is the
   frame.
3. **Drop "dashboard" and "cockpit" from the conceptual model.** They
   imply fixed slots someone else drew. Portals are generated surfaces:
   the resident opens them because the conversation or run needs them.
4. **Parallelism is a compatibility constraint, not the next feature.**
   #159 should make the mailbox and output frame safe for future
   parallel runs, while preserving single-flight as the shipped local
   behaviour.

## Reconcile/projection layer

The old mental bucket "messenger = append-log" was wrong. Two semantics
cut across every transport:

- **Append-log**: chat messages, issue comments, PR comments, response
  partials. Ordered, additive; you emit and it goes.
- **Desired-state**: the live `.card`, PR branch/diff/labels, issue
  state, any future status object. One surface is reconciled in place.

The gate should not know which product idea it is carrying. It receives
an append-log send or a desired-state reconcile request and performs the
transport-specific IO. The projection layer above it owns:

- the surface key (`conversation_key`, PR number, issue number, run card,
  portal id);
- idempotence and last-write rules for desired-state surfaces;
- deduplication across redundant transports;
- rendering choices for the human-facing surface.

Single-flight means last-write-wins is enough for today's `.card`: one
run writes the card at a time. If future parallel runs share a visible
surface, last-write-wins remains valid only inside one owner/surface key;
another run needs an explicit handoff or a distinct surface.

## Output-frame grammar

An **output frame** is a resident-authored unit in the generated stream
that is both human-legible and daemon-actionable. Today the daemon sees
these through control files and stdout; the contract below defines what
each frame must mean before any new syntax is built.

| Frame | Portal form | Human surface | Required affordance | Current implementation |
| --- | --- | --- | --- | --- |
| PLAN | parked + outbound append-log | chat / issue comment | approve or edit; what resumes | ordinary outbox message using the five-part PLAN shape |
| PROGRESS | outbound desired-state | live `.card` | current phase; why chunking; medium/quota if known | `.card` control file |
| INBOUND-CHECK | inbound | not always user-visible | what was checked; fold/defer/leave decision when it matters | read `inbox.json` at boundaries and pre-closeout |
| INTERRUPTION-REPLY | outbound append-log to event | target event's thread | one complete reply; no duplicate answer | outbox file with `event:` route |
| HANDOFF | outbound append-log or desired-state | PR, issue, branch note, chat | what changed; where review continues | `gate: forge`, issue comment, final reply |
| DEFERRAL | parked | chat note, schedule, or mailbox record | why parked; when / what resumes it | dominion `schedule.md` or daemon-authored `defer_until` today |
| CLOSEOUT | outbound append-log fallback | current thread | outcome, tests, branch/commit, pending-input choice | stdout or explicit reply portal |

Frame names are conceptual, not literal syntax yet. A future helper or
stream marker may spell them differently; the contract is the behaviour.

### Inbound portals

An inbound portal asks the daemon, "what input is waiting at this point
in the run?" It must be opened at deliberate boundaries: after a plan is
formed, after a major todo completes, before terminal closeout, or when a
long-running step returns.

The resident's decision for each visible event is one of:

- **fold and resolve**: answer it now via `event:` routing;
- **park/defer**: leave it pending with an explicit resume condition;
- **leave for another wake**: scope or branch differs enough that a new
  run is healthier.

The shipped manual makes this a habit ("read `inbox.json`"). #159's
structural version moves the check into the output frame so the resident
does not have to remember a filename. The user should see the decision
when it affects them, not the `.brr/outbox` path.

### Outbound portals

An outbound portal emits to a surface. Every emission must declare:

- its reconcile semantic: append-log or desired-state;
- its target: current event, another event, a gate destination, or a
  desired-state surface key;
- whether it resolves an event, updates a surface, or only narrates
  progress.

The frontmatter footgun proved the robustness point. A human-facing
message should not depend on the resident hand-writing hidden protocol
correctly. The near-term fix is helper commands that write today's files;
the deeper fix is portal markers in the stream that the runner adapter
extracts structurally.

### Parked portals

A parked portal emits and then pauses the continuation until something
refluxes back. PLAN->approve is canonical, but the same shape covers
deferred work, child-run requests, quota waits, and "resume when a new
event arrives."

A parked portal record needs:

- `portal_id`;
- owning `run_id` and, if applicable, owning event ids;
- the rendered message or artifact that the human saw;
- the resume condition: approval reply, time, new ingress, forge state,
  child-run completion, or cancellation;
- a pointer to continuation context: plan text, branch, commit, kb page,
  run manifest, or schedule entry;
- cost/consent policy: what can resume automatically and what needs a
  human nod.

Today's approximation is conversational: the PLAN is visible in history
and the approval wake reconstructs from woven context. That is acceptable
for #148. #159's mailbox work makes the parked portal a first-class
record so a future wake resumes from an explicit continuation, not only
from prose.

## Parallel-safe run mailbox

The mailbox contract borrows actor ideas without importing a runtime:
runs, gates, schedules, and forge reconcilers exchange explicit messages;
they do not mutate shared event state unless they hold the lease for that
event or surface.

### Event claim lease

An event claim is a **lease**, not resolution. The future claim fields
should extend the existing event frontmatter rather than replacing the
event file model:

- `claimed_by: <run-id>` names the current owner;
- `claim_expires_at: <ISO timestamp>` lets the daemon clear orphaned
  claims after a crash or killed runner;
- an optional `claim_token` / epoch protects against stale writes if true
  parallel local runs ever exist.

Only the claiming run may resolve, postpone, or release the event. The
daemon may clear expired/orphaned claims. In single-flight, this can land
with fewer safeguards, but it must not lean on global `processing` as the
only ownership marker; that would fail the parallel compatibility test.

### Event outcomes

At run exit, every event the run touched converges to one explicit
outcome:

- **resolved**: response/gate action/noop recorded; event becomes `done`;
- **released**: not handled; claim clears and it returns to pending;
- **postponed**: claim clears, event stays pending with `defer_until`
  and a resident-authored reason/resume condition;
- **operational failure**: lead receives the explicit failure note when
  addressed; siblings may receive daemon-authored short deferral as
  shipped in #128.

This keeps "I chose not to handle it," "I crashed before handling it,"
and "the medium failed" distinguishable without spawning one noisy wake
per leftover event.

### Run mailbox records

A run mailbox record is an append-only message between actors. It is the
durable form of a parked portal or a cross-run handoff. Minimum fields:

- `mailbox_id`;
- `from_run` / `to_run` or `to_daemon`;
- `portal_id` when the message resumes or parks a portal;
- target event ids or surface keys;
- state: `open`, `parked`, `resumed`, `cancelled`, `expired`;
- the continuation pointer and cost/consent policy.

The first implementation does not need a new database. It can be a small
file/log beside run manifests or an extension of run metadata. What
matters is the ownership rule: a parked continuation is an explicit
mailbox message, not a guess reconstructed from whichever thread happens
to wake next.

### Parallel compatibility rules

If brr later allows more than one run at once, the rules are:

- one owner per claimed event;
- no run writes to another run's primary outbox;
- every delivery names an event, gate, or desired-state surface key;
- desired-state surfaces have an owner/surface key, and cross-owner
  updates require handoff;
- cost is attributed at run granularity, because the runner invocation is
  the billable unit;
- folding multiple events into one run is the consent point. If folding a
  stuck or expensive event into a cheap fresh one is ambiguous, park it
  and say why.

## Cost-aware pacing

The output frame should make pacing visible without inventing promises.

- Show the runner medium and quota posture when the bundle exposes them.
- Use historical cost facts only; never present a projected dollar figure
  as a quote.
- Explain chunking when it affects the user's wait or approval path.
- Treat expensive folding as a consent decision. The resident can fold,
  park, or defer, but the reason should be visible in PLAN, DEFERRAL, or
  CLOSEOUT frames.

The unresolved pricing/spend policy still lives with #130. #159 should
not split one run's cost across events; it should make the fold decision
legible enough that #130 can attach policy to the run.

## Operator legibility

The user should understand the control surface from the output itself:

- a PLAN says "approve or edit" and what approval starts;
- a PROGRESS card says what is happening now, not just that the daemon is
  alive;
- an INTERRUPTION-REPLY answers the right thread and avoids duplicate
  coverage;
- a DEFERRAL says what is parked and when it will wake;
- a CLOSEOUT names changed artifacts, tests, branch/commit, and whether
  related pending input was folded or intentionally left queued.

Do not require the user to know `.brr/outbox`, frontmatter fences, or
`inbox.json`. Those are implementation details until helper commands or
stream markers make them structurally hard to misuse.

## Implementation sequence

1. **Portal helper commands.** Add small helpers that write today's
   control files: `card`, `reply --event`, `send --gate`, `inbox`, and a
   PLAN/deferral writer if the shape stays useful. This is the cheapest
   way to move from "remember frontmatter" to "use a tool" without
   changing daemon storage.
2. **Resident-authored deferral.** Let a run deliberately postpone a
   pending event with `defer_until`, reason, and resume condition. This
   completes the non-failure half of #128 Q2.
3. **Run-key primary outbox/response.** Move the primary response/outbox
   key from lead event id to run id; require explicit `event:`/`gate:` or
   current-thread target for deliveries. This is #128 Q3 and removes the
   "which event owns the run" question.
4. **Event claims.** Add per-run claim leases and exit-time outcome
   handling. Keep single-flight, but make the storage model parallel-safe.
5. **Parked-portal mailbox records.** Persist PLAN approvals, deferrals,
   child-run requests, and resume conditions as mailbox records instead
   of relying only on conversation history.
6. **Concept prose sweep.** After the primitives land, reconcile
   `plan-resident-cockpit.md`, `plan-cost-aware-cockpit.md`,
   `design-managed-delivery.md`, `subject-managed-mode.md`, and the index
   so the graph consistently says portals / projection instead of
   dashboard / cockpit.

The first code slice should be helper-shaped, not a parallel executor.
It attacks the real dogfood pain (manual file/frontmatter protocol) while
leaving the mailbox storage change crisp and reviewable.

## Standing portal candidates

This wake also confirms which context belongs on a live, summonable
surface rather than in always-injected prose:

- live inbox / queued-event state;
- runner medium and quota posture;
- branch dirt, unpushed commits, and prior-run artifact state;
- forge issue/PR state when relevant;
- kb-health findings as a pullable diagnostic, with only urgent drift
  injected.

These are not all #159 implementation work, but they share the same
principle: inject the facts a run cannot miss; make everything else a
portal the resident can summon when the turn needs it.

## See also

- [`src/brr/docs/portals.md`](../src/brr/docs/portals.md) — today's
  shipped control-file manual.
- [`design-run-event-model.md`](design-run-event-model.md) — run/event
  substrate, including the open claim/deferral/response-key questions.
- [`plan-resident-cockpit.md`](plan-resident-cockpit.md) — parent plan
  whose "cockpit" language is now a historical label awaiting the
  concept sweep.
- [`plan-cost-aware-cockpit.md`](plan-cost-aware-cockpit.md) — cost and
  operator-control braid.
- [`design-managed-delivery.md`](design-managed-delivery.md) and
  [`subject-managed-mode.md`](subject-managed-mode.md) — gate transport
  and managed-mode context this projection layer must reconcile.
- [`design-co-maintainer.md`](design-co-maintainer.md) §11 — continuity
  and delivery spine.
