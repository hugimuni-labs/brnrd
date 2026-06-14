# Run / event model — retire the per-event "task"

Status: **proposed (2026-06-14)**. A slice of the **Co-maintainer**
milestone ([`design-co-maintainer.md`](design-co-maintainer.md) §6
run↔reply decoupling, §9 responsiveness, §11 execution order) and the
design page issue **#128** asks for before code. It reframes a core
concept (`task`) rather than adding behaviour, so it lands as its own
page that §6/§9/§11 reference, instead of bloating the co-maintainer
hub. Subsumes the narrower "batch pending events per correspondent into
one wake" idea raised on #114.

## Why

`task` is a leftover of the **spawn-per-event** architecture: one inbound
event → one `Task` → one runner invocation → one terminal reply. The
resident reshape already broke that 1:1 in pieces:

- **Multi-response** ([`design-multi-response.md`](design-multi-response.md))
  — one wake writes zero, one, or many deliveries.
- **Folded-in events** — the bundle already renders an *Inbox — other
  pending events* section and lets a wake answer a *different* pending
  event inline via `event: <id>` frontmatter
  ([`../src/brr/prompts.py`](../src/brr/prompts.py) `_format_pending_events`).
- **`gate:` sends** and the single-flight reflex loop — a wake initiates,
  not only answers.
- **Delivery floor (§6)** — a run's success signal is already "≥1 output
  event / commit / noop," not stdout.

So the abstraction the code still carries — *a task is an event to
answer* — now fights the reality the rest of the system already lives in:
*a run is a runner waking that reads the whole inbox and decides*. The
bundle hands the resident the whole pending set and the affordances to
act on all of it; only the **daemon's dispatch** and the **naming** still
think one-event-one-task.

### The symptom that surfaced it

A single user message produced **three runner invocations** on #114. Two
causes, only one of which this page owns:

1. *Self-author trigger* — the bot woke on its own labelled issue. Fixed
   separately in `brr/github-self-author-skip` (#129); **not** this page.
2. *Serial re-spawn* — failed/interrupted runs correctly **don't** resolve
   their input event, but the daemon loop then opens a **fresh wake per
   pending event** instead of letting one wake see them all. This is the
   piece this page owns.

The literal locus is one line. In the daemon scan loop
([`../src/brr/daemon.py`](../src/brr/daemon.py), ~L1940):

```python
pending = protocol.list_pending(inbox_dir)
if pending:
    event = pending[0]          # ← the per-event coupling
    protocol.set_status(event, "processing")
    current = pool.submit(_run_worker_and_finalize, event, ...)
```

The daemon picks `pending[0]`, builds a `Task` from it
([`../src/brr/task.py`](../src/brr/task.py) `Task.from_event`), and the
"task" *is* that one event for the run's whole life. Everything
downstream — `run_context`, the response key (`responses/<event_id>.md`),
the bundle's `### Task` framing, the diffense pack path, the branch name
`brr/<task-id>` — inherits that 1:1.

## Want

Retire `task` as the load-bearing unit. Model the two real entities:

- **Event** — an immutable signal (gate ingress, forge item, self-schedule
  firing). Events are **consumed and produced by runs**; no event is owned
  1:1 by a runner invocation. This is already mostly true at the file
  layer (`protocol.create_event` / `list_pending` / `set_status`); what
  changes is that nothing *derives a task identity* from a single event.
- **Run** — a runner invocation. A run **reads all currently-pending
  events**, **decides** what to tackle first, what to fold in, and what to
  postpone, and may **consume / produce events** and **interact with /
  create forge items** during its life. It already reacts live (the
  heartbeat `inbox.json` view); this makes that the **model**, not a
  bolt-on.

Concretely:

- At wake start the run is handed the **full pending set**, with the same
  delivery-contract affordances it has mid-thought (`event: <id>` to
  resolve a specific one; leave the rest pending to postpone).
- The **selection** decision (which event leads, which fold in, which
  wait) moves **from the daemon to the run**. The daemon stops choosing
  `pending[0]`; it dispatches a run against the inbox.
- Failed/interrupted runs still **don't** resolve their input events
  (correct today) — but the **next run** sees those un-resolved events as
  pending and decides about them, instead of the daemon serially
  re-spawning one wake per stuck event.
- Naming/IDs, run-context, response-routing keys, and the bundle's "Task"
  framing move from task-centric to **run + events**.

## What stays the same

- **Single-flight.** Still one run at a time off the main thread. This
  page changes *what a run sees and decides*, not concurrency. The
  society-of-mind sharing is in the dominion, not the executor.
- **The delivery floor (§6).** Success = ≥1 delivery / commit / explicit
  noop. Multi-event runs extend, not replace, that signal (see below).
- **Event immutability + file CRUD.** `protocol.py`'s event files stay the
  substrate; this is a dispatch + naming reshape, not a storage rewrite.

## The hard questions a design must settle before code

These are why #128 asks for a page first. Each carries a recommended
resolution; the user's nod (or amendment) is what turns them into spec.

### Q1 — Event lifecycle: what does a run "claim"?

Today: the daemon marks `pending[0]` → `processing` before dispatch, so a
crash leaves a stuck `processing` event the next loop re-picks. With a run
reading *all* pending events, marking them all `processing` would hide the
ones the run chose to **postpone** behind a status that reads as "owned by
a live run."

**Recommendation:** introduce a **per-run claim** distinct from event
resolution. The daemon stamps each pending event it hands to a run with
the **run id** (`claimed_by: <run-id>`), not a global `processing`. On run
exit:

- events the run **resolved** (delivered / noop'd) → `done` (cleanup as
  today);
- events the run **left pending** (postponed *or* never reached) → claim
  cleared, back to `pending`, so the next run sees them;
- events orphaned by a **crash** (claim still names a run that's no longer
  in flight) → claim cleared by the daemon on reap, back to `pending`.

This makes "postpone" and "crashed mid-run" both resolve to *pending for
the next run*, which is the correct convergence — the difference is only
whether the run *chose* it.

### Q2 — Re-wake debounce: don't spin on postponed events

If a postponed event simply returns to `pending`, the daemon's next loop
iteration dispatches a new run immediately — a busy-spin on work the
resident **deliberately deferred**. The narrow "batch per correspondent"
idea avoided this by folding; the general model needs an explicit brake.

**Recommendation:** a postponed event carries a **`defer_until`** the run
sets (an ISO time or "after the next *new* event"), mirroring the
self-schedule `at:`/`every:` shape the resident already owns. The daemon
only dispatches a run when there is at least one pending event whose
`defer_until` has passed **or** when a *new* event arrives. A run that
postpones everything with no new ingress goes quiet until its own deadline
— the same lever as `schedule.md`, applied to inbound events. This is the
seam that makes "decide what to postpone" real rather than a re-spawn in
disguise.

### Q3 — Response routing across N events

Today responses key on `event_id` (`responses/<eid>.md`,
`<eid>.partials/`, the outbox dir `outbox/<eid>/`). A run consuming N
events needs each resolved event to route its reply correctly. The
`event: <id>` frontmatter contract **already does this** for folded-in
events; the change is making it the **norm**, not the exception:

- A run is dispatched against the inbox; its **primary** outbox/response
  dir can stay keyed to a "lead" event for backward-compatible single-event
  delivery, but every delivery names its target event (`event:`) or gate
  (`gate:`) explicitly.
- An event with **no** output at run end is *not* resolved — it stays
  pending (Q1) — so "I never answered this" and "I postponed this" share a
  mechanism, and silence on an event is honestly visible as still-pending,
  not falsely `done`.

**Open sub-question:** the run/outbox/response directory is named per
event today. If a run has no single "lead" event (e.g. woken purely by a
self-schedule that then reads inbound events), the natural key is the
**run id**, and per-event delivery routes via frontmatter. Recommend
moving the primary key to **run id**, with event resolution always
explicit — this is the cleanest end state and removes the "which event is
the task" question entirely.

### Q4 — Billing / retry interaction (the real cost question)

Folding `evt-A` (credit-failed last run) + a fresh `evt-B` into one run
**pays for both at once** vs. serially. This matters because:

- A run that bundles a stuck expensive event with a cheap fresh one
  charges the cheap one's correspondent for the retry of the expensive
  one (correspondents may differ — multi-user projects, §4.4).
- The consent/spend model the user wants centred on **projected spend**
  (the credit-base reframe, pricing pivot #130) needs to attribute cost to
  *something*. If the unit is the **run**, and a run spans multiple
  events/correspondents, attribution gets ambiguous.

**Recommendation:** keep cost attribution at the **run** granularity (one
runner invocation = one billable unit), and make the run's **decision to
fold** the consent point — the resident already owns "what to tackle / fold
/ postpone," so folding an expensive stuck event is a *choice it can
defer* if cost/consent says so. Don't try to split one runner invocation's
cost across events. This keeps billing aligned with the actual compute
unit and leaves the policy ("should I fold the credit-failed event in, or
postpone it until the user tops up?") where it belongs — in the resident's
judgement, informed by the spend projection once #130 lands. **This
question is genuinely coupled to #130** and is the strongest argument for
landing the pricing/spend decision before the rename's billing-facing
edges.

### Q5 — The rename surface (cosmetic but wide)

`task` → `run` touches a broad surface, all mechanical:

- [`../src/brr/task.py`](../src/brr/task.py) — `Task` → `Run`,
  `from_event` → a constructor that doesn't imply 1:1, `.brr/tasks/` dir.
- [`../src/brr/daemon.py`](../src/brr/daemon.py) — the dispatch loop,
  `_run_worker`, `_run_worker_and_finalize`, `_cleanup_traces_on_success`,
  publish (`task.meta["publish_branch"]`).
- [`../src/brr/run_context.py`](../src/brr/run_context.py) — already named
  `run`! The context file is `runs/<task-id>/context.md`; the "Task ID"
  label is the only task-ism. (Evidence the *run* concept already won at
  the directory layer; only the object is still `Task`.)
- [`../src/brr/prompts.py`](../src/brr/prompts.py) — bundle `### Task`,
  `Task ID:`, the "Task Context Bundle" name, `_format_pending_events`.
- Response/outbox keys, the diffense pack path `diffense/<task-id>/`, the
  branch name `brr/<task-id>`.

**Recommendation: phase it.** The model change (Q1–Q4) is the substance
and ships first; the **rename is a separate, mechanical follow-up** so a
wide diff doesn't obscure the behavioural change in review. Keep the
existing **id string** stable across the rename (a run id can keep the
`task-…` shape transitionally) to avoid churning branch names, response
paths, and persisted bundles mid-flight. The user named the rename as
in-scope; phasing is about *review legibility*, not dropping it.

## Resilience tie-in (why this matters beyond #114)

The user's resumable-tasks thread (intermediate commits, partial-work
pickup after a credit/OOM interruption, `.brr/` possibly discarded) rides
on this model. Once a failed run leaves its events **pending for the next
run** rather than serially re-spawning, "the next run picks up where the
last left off" becomes the *default* behaviour of the dispatch loop, not a
special resume path. The remaining resilience work (seed the next run's
branch from the prior run's `brr/<id>` instead of `main`; intermediate
commits; pre-delivery squash) layers on top cleanly. That work is its own
ticket; this page is its substrate.

## Relationships

- **Slice of** [`design-co-maintainer.md`](design-co-maintainer.md) §6 / §9
  / §11; this page is the model under §11's "responsiveness / event-driven"
  line.
- **Subsumes** the narrower "batch pending events per correspondent into
  one wake."
- **Interacts with #115** (daemon responsiveness — the prompt-wake path
  feeds the same dispatch loop; the `threading.Event` wake and this
  inbox-reading run are the same loop seen from two angles).
- **Builds on #110** (the communication snapshot already assembles
  cross-thread pending context — the run reads *that*) and the
  multi-response + single-flight substrate.
- **Coupled to #130** (pricing / spend) via Q4 — the billing-facing edges
  of the rename want the spend decision first.
- **Substrate for** the resumable-tasks / interruption-resilience work.

## Open decisions for the user

1. **Q1/Q2** — accept the per-run claim + `defer_until` debounce, or a
   simpler "mark all processing, clear on reap" with no postpone brake?
2. **Q3** — move the primary response/outbox key to **run id** (cleanest),
   or keep a "lead event" key for backward compatibility?
3. **Q4** — confirm **run-granularity** cost attribution and "folding is
   the consent point," sequenced after #130.
4. **Q5** — confirm **phase the rename** behind the model change, and
   whether to keep the `task-…` id-string shape transitionally.
5. **Home** — this dedicated page, or fold into `design-co-maintainer.md`
   as a new §? (Recommended: dedicated, per the oversized-hub signal.)
