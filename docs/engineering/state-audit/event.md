# Event state audit: writers, selectors, reply retirement

Seeded from origin/main 03c1b0a5. Line numbers are for that commit's
`src/brr/*` (the fixes below move some lines). This page replaces the
rejected Vibe draft (952b5633) and inherits nothing from it.

**Scope.** Event file, status writers, selectors, reply retirement. Not
covered: seat state, strand state, routing inventory, provider behavior.

**Terms.**
- *Zero cost* means zero model tokens. It does not mean zero I/O or time.
- *Proof*: `driven` = a test runs a real producer into a real reader.
  `read` = code read only. `schema` = a test checks shape only.
- Missing native proof is a proof gap. It is not a runtime bug.

## 1. Five separate things

| Name | What it is |
| --- | --- |
| `status` | Letter lifecycle. |
| `run_outcome` | Outcome of the run that handled the letter. |
| transport state | State of one outbound message row. Not an event field. |
| `observed_by` | Parent run id that rendered a `spawn_completed` letter. |
| read / answered | No field. See the last rows below. |

**`status`** values: `pending`, `processing`, `done`, `delivered`, `noted`
(`LETTER_STATUSES`, protocol.py:726). Legacy `error`, `conflict`, `stopped`,
`cancelled` still count as terminal. Writers: `protocol.set_status` (:1019),
`_set_event_status_if_present` (daemon.py:17621), gates (runtime.py:531
writes `error`, :686 writes `delivered`).

**`run_outcome`** is a separate key. `_set_event_run_outcome`
(daemon.py:17640) writes `run_outcome`, then also sets `status: done`. So the
field is separate, but the call changes `status` too.

**Transport state.** `message_store` rows: pending, delivered,
undeliverable. `deliver_stream` (runtime.py:601) moves a row to delivered
after the platform confirms.

**`observed_by`.** Written by `_pending_events_for_agent` (daemon.py:3640)
when the render is by the declared parent run. Read by
`_retire_internal_event` (daemon.py:11385).

**Read / answered.** "Answered" = status `done`, `delivered` or `noted`,
plus a partial or response file. "Read" has no durable record. `observed_by`
is the nearest fact: it proves a render, not a read.

`set_status` does no validation. It accepts any string and does not raise.
`protocol.INTERNAL_SOURCES` lists 16 sources (protocol.py:453). The daemon
has a second set, `_INTERNAL_EVENT_SOURCES = {"schedule"}` (daemon.py:259).

## 2. Transitions

Each block: writer, receiver, precondition, then what persists and how
failure, duplicates and late mail behave. Cost is zero for all rows.

### T1 create → `pending`
- Writer: `protocol.create_event`. Receiver: `list_pending` (:754),
  `list_dispatchable` (:907).
- Persists: `id`, `source`, `status`, `created`, meta.
- Late mail: a new file never changes older files. Proof: driven.

### T2 pending → deferred (still `pending`)
- Writer: `_defer_pending_siblings_after_failure` (daemon.py:15488).
- Precondition: a lead run failed or parked.
- Persists: `defer_until`, `defer_reason`, `deferred_by_run`.
- `list_pending` still returns it. `list_dispatchable` omits it until
  the time passes. Proof: driven.

### T3 deferred → eligible
- Writer: `_undefer_held_event` (daemon.py:16467).
- Persists: clears the three defer keys.
- An id found in no drawer prints one line and returns. No retry.
  Proof: driven.

### T4 `processing` → `done` + `run_outcome`
- Writer: `_set_event_run_outcome` (daemon.py:17640). Precondition: run ended.
- Persists: `run_outcome`, then `status: done`.
- If the meta write raises `OSError`, it returns `False` and `status` is
  unchanged. If the file is gone, the status step returns `False`.
- Mail created before this write stays `pending` and dispatchable.
  Proof: driven for the write only. The worker tail is not run.

### T5 `event:` reply retires another letter
- Writer: `_deliver_event` (outbox/verbs.py:1197) via
  `_set_event_status_if_present`.
- Precondition: target resolves and is not ambiguous. A strand may address
  only its own event or a parent steer (`_strand_may_address`, daemon.py:5227).
- Persists: `status: done`, partial under `responses/`.
- Ambiguous, unknown or foreign target: notice `refused`, row staged
  `undeliverable`, no status change. Proof: schema (`tests/test_outbox.py`).

### T6 reply to a source no gate owns
- Writer: `_deliver_event`, branch `retires_target`.
- Persists: `status: done`, row `undeliverable`.
- Notice is `advisory` for `spawn_completed` and `schedule`, `dropped`
  for other sources. Proof: read.

### T7 `also:` burst
- Writer: `_resolve_also_targets` (daemon.py:5503), then `_deliver_event`.
- Precondition: every id resolves, is pending, same thread, same
  correspondent.
- Persists: `status: done` per id, one partial.
- Any bad id refuses the whole directive: no send, no status change, one
  `refused` notice. Proof: driven, with a gate-call recorder.

### T8 `note:` retire
- Writer: `handle_note` (verbs.py:496) → `_note_event_closed`
  (daemon.py:5407).
- Persists: `noted_by`, `noted_at`, then `status: noted`.
- A meta `OSError` is swallowed and the status write still runs. A failed
  status write gives a `dropped` notice. Proof: schema + read.

### T9 `done` → `delivered`
- Writer: `deliver_stream` (runtime.py:601). Precondition: `done`, due,
  terminal body readable or none.
- Persists: `status: delivered`, row `delivered`.
- A send exception backs off. `PermanentDeliveryError` or the failure
  ceiling sets `error`. Proof: read; `tests/test_delivery_retry.py` drives
  retry.

### T10 park accumulates mail (limited)
- Writers: `_handle_resource_held_events` (existing, driven by
  `tests/test_the_seat_that_stays.py`) → `accumulate_event`;
  `_apply_resource_hold_resume` → `_undefer_held_event`.
- Persists: hold `accumulated_event_ids`; letter defer keys.
- `status` stays `pending` while held. Only the release clears the defer
  keys. Proof: driven from the held handler through release into the fresh
  selector. **Limited:** the arming producer `_finalize_resource_hold`
  (daemon.py:15936) and a real wake or await are not run.

### T11 `observed_by` and retirement
- Stamp: the selector sets `observed_by`/`observed_at` when
  `spawn_parent_run_id` equals the observing run and the source is
  `spawn_completed`.
- Retire: `_retire_internal_event` sets `delivered` only for completions
  with that stamp. A parked run retires none. Unstamped completions stay
  `pending` for a successor. Proof: driven.
- Three facts stay apart: *rendered* (stamp), *handled* (reply or `note:`),
  *retired* (`delivered` by this function).

## 3. Selectors

- `_pending_events_for_agent` builds the seat view from the account drawer
  union. A strand sees only its own edge traffic.
- A person's cross-repo letter stays visible and gets `foreign_repo`, so a
  seat can free its slot when a person writes about another repo.
- `_strand_may_address` is the write-side twin of the strand read rule.

## 4. Findings

**F1 (fixed, driven).** The foreign-repo branch used
`_event_requires_thread_delivery`, which excludes only `schedule`. Its own
comment says strand returns stay scoped. A foreign `spawn_completed` or
`dispatch_message` still reached the seat. Fix: that branch now also skips
every source in `protocol.INTERNAL_SOURCES`. Same-repo, unlabelled and
strand edge traffic is unchanged (tested). Other callers of
`_event_requires_thread_delivery` are unchanged.

**F2 (defensive, driven).** `set_status` replaced the text
`status: <cached old value>`. If the file had changed, the replace matched
nothing and the dict still claimed success. Fix: rewrite the frontmatter
`status:` line. A file with no status line is still not written. A
malformed value is replaced. The body is untouched (tested). **No runtime
interleaving that triggers this is proven.**

**F3 (open, read only).** `set_status` and `update_event_meta` are separate
unlocked read-modify-write cycles. Two writers can lose one update. Fixing
it is a design choice (lock or single writer), so it is only reported.

## 5. Acceptance cases

All in `tests/test_event_delivery_seams.py`.

| Case | Status | Limit |
| --- | --- | --- |
| 1 cross-repo person vs child edge | driven | selector only; no `inbox.json` writer or await |
| 2 park accumulates mail | driven (limited) | arming path and real wake not run |
| 3 invalid `also:` burst | driven | recorder is `deliver_stream`; no real platform |
| 4 mail during finalization | **open** | only the outcome write is driven; the worker tail is not |

## 6. Next experiments

- Drive `_finalize_resource_hold` with a real failing worker.
- Drive the finalizer tail (case 4) if a scaffold exists.
- Delivery of a `done` event whose partial write failed: not driven.
- No claim of general soundness is made.
