# Event state audit: writers, selectors, reply retirement

Seeded from origin/main 03c1b0a5. Line numbers are for this commit's
`src/brr/*`. This page replaces the rejected Vibe draft (952b5633). It
inherits nothing from that draft.

**Scope.** This page covers the event file, its status writers, the
selectors that read it, and reply retirement. It does not cover seat state,
strand state, or the routing inventory. This audit did not read those.

**Terms.**
- *Zero cost* means zero model tokens. It does not mean zero I/O or zero time.
- *Proof level*: `driven` = a test runs the real producer into the real
  reader. `read` = the author read the code only. `schema` = a test checks
  shape only.
- *Missing native proof* is a proof gap. It is not a runtime bug.

## 1. Five separate fields

| Field | Meaning | Writers (actual) |
| --- | --- | --- |
| `status` | Letter lifecycle. Values: `pending`, `processing`, `done`, `delivered`, `noted` (`LETTER_STATUSES`, protocol.py:726). Legacy `error`, `conflict`, `stopped`, `cancelled` still count as terminal. | `protocol.set_status` (:1019); `_set_event_status_if_present` (daemon.py:17621); gates (`runtime.py:686`, `:531` writes `error`). |
| `run_outcome` | Outcome of the run that handled the letter. It does not change `status`. | `_set_event_run_outcome` (daemon.py:17640). It writes `run_outcome`, then sets `status: done`. |
| transport state | State of one outbound message row (`message_store`: pending, delivered, undeliverable). It is not an event field. | `deliver_stream` (runtime.py:601) after the platform confirms a send. |
| `observed_by` | Parent run id that rendered a `spawn_completed` letter. | `_pending_events_for_agent` (daemon.py:3640). Not re-audited here. |
| read or answered | No field exists. "Answered" means the letter is `done`, `delivered` or `noted` plus a partial or response on disk. | `_deliver_event` and `handle_note` (outbox/verbs.py:1197, :496). |

`set_status` has no validation. It accepts any string. It does not raise.
`LETTER_STATUSES` is a documentation set and a retention input. It is not a
guard on writes. (The rejected draft claimed the opposite.)

`protocol.INTERNAL_SOURCES` has 16 entries (protocol.py:453). The daemon has
a second, separate set: `_INTERNAL_EVENT_SOURCES = {"schedule"}`
(daemon.py:259). The two sets answer different questions. See finding F1.

## 2. Transitions

Each row: writer, selector or receiver, precondition, persisted fields,
failure/duplicate/late behavior, model cost, proof.

| # | Transition | Writer and receiver | Precondition | Persists | Failure / duplicate / late | Cost | Proof |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | create → `pending` | `protocol.create_event`; selector `list_pending` / `list_dispatchable` (:754, :907) | none | `id`, `source`, `status`, `created`, meta | A new file never changes older files. | zero | driven (T4 test below) |
| T2 | `pending` → deferred (still `pending`) | `_defer_pending_siblings_after_failure` (daemon.py:15488) | a lead run failed or parked | `defer_until`, `defer_reason`, `deferred_by_run` | `list_pending` still returns it. `list_dispatchable` omits it until the time passes. | zero | driven |
| T3 | deferred → eligible | `_undefer_held_event` (daemon.py:16467) | id found in some drawer | clears the three defer keys | An unknown id prints a line and returns. No retry. | zero | driven |
| T4 | `processing` → `done` + `run_outcome` | `_set_event_run_outcome` (daemon.py:17640) | the run ended | `run_outcome`, `status: done` | If the file is gone, `_set_event_status_if_present` returns `False`. `run_outcome` write failing on `OSError` returns `False` before `status` changes. | zero | driven |
| T5 | → `done` by `event:` reply to another letter | `_deliver_event` (verbs.py:1197) → `_set_event_status_if_present` | target resolves, is not ambiguous; a strand may address only its own event or a parent steer | `status: done`; partial file under `responses/` | Ambiguous, unknown or not-addressable target: notice `refused`, message staged `undeliverable`, no status change. | zero | schema + read (`tests/test_outbox.py`) |
| T6 | → `done` for undeliverable sources | `_deliver_event` (`retires_target`) | cross target, no gate owns the source | `status: done`; undeliverable message row | Notice kind `advisory` for `spawn_completed` and `schedule`, `dropped` for others. | zero | read |
| T7 | → `done` for all `also:` ids | `_resolve_also_targets` (daemon.py:5503) then `_deliver_event` | every id resolves, is pending, same thread, same correspondent | `status: done` per id; one partial | Any bad id refuses the whole directive. Nothing is sent and no status changes. | zero | driven (T3 test below) |
| T8 | → `noted` | `handle_note` → `_note_event_closed` (daemon.py:5407) | target resolves; strand rule as T5 | `noted_by`, `noted_at`, `status: noted` | `update_event_meta` `OSError` is swallowed, then the status write proceeds. The status write failing gives a `dropped` notice. | zero | schema + read |
| T9 | `done` → `delivered` | `gates.runtime.deliver_stream` (runtime.py:601) | event is `done`; `_delivery_due`; terminal body readable or none | `status: delivered`; message row `delivered` | A send exception backs off. A `PermanentDeliveryError`, or the failure ceiling, sets `error`. | zero (gate I/O) | read; `tests/test_delivery_retry.py` drives the retry |
| T10 | seat park accumulates mail | `_finalize_resource_hold` (daemon.py:15975-16000) + `resource_hold.accumulate_event` | hold armed | `accumulated_event_ids` on the hold, defer keys on the letter | List is de-duplicated. The letter's `status` stays `pending`. Only `status` moves it, so a park never retires mail. | zero | driven (T2 test below) |

## 3. Selectors

- `_pending_events_for_agent` (daemon.py:3640) builds a seat's view from the
  account drawer union. A strand sees only its own edge traffic.
- A cross-repo letter from a person stays visible and gets
  `foreign_repo`. This lets a seat in repo A free its slot when a person
  writes about repo B.
- `_strand_may_address` (daemon.py:5227) is the write-side twin of the
  strand read rule. It allows the waking event and `spawn_message_for_event`
  matches only.

## 4. Findings

**F1 (fixed, driven).** The foreign-repo branch of
`_pending_events_for_agent` used `_event_requires_thread_delivery`, which
only excludes `schedule`. Its own comment says strand returns stay scoped.
A foreign `spawn_completed` or `dispatch_message` still reached the seat.
Fix: skip any source in `protocol.INTERNAL_SOURCES` in that branch. The
other callers of `_event_requires_thread_delivery` are unchanged.
Test: `test_foreign_person_event_reaches_seat_but_foreign_child_edge_does_not`.

**F2 (fixed, driven).** `protocol.set_status` replaced the text
`status: <cached old value>`. If another writer had changed the file, the
replace matched nothing. The file kept its status and the caller's dict
claimed the new one. Fix: rewrite the frontmatter `status:` line itself.
Test: `test_set_status_survives_a_stale_cached_status`. No production
interleaving that triggers this is proven. The fix is defensive.

**F3 (open, read only).** `set_status` and `update_event_meta` are separate
read-modify-write cycles with no lock. Two writers on one file can lose one
update. Not driven. Fixing it is a design choice (lock or single writer),
so this audit reports it and does not change it.

## 5. Driven cases

| Case | Test (`tests/test_event_delivery_seams.py`) | Fixture limit |
| --- | --- | --- |
| 1. foreign person reaches seat, foreign child edge does not | `test_foreign_person_event_reaches_seat_but_foreign_child_edge_does_not` | Calls the selector. It does not run the `inbox.json` writer or an `await`. |
| 2. park keeps mail pending | `test_park_defers_mail_but_never_retires_it_until_a_successor_handles_it` | Hold meta is built by `accumulate_event`. The full arming path is not run. |
| 3. invalid `also:` burst | `test_invalid_also_burst_retires_nothing_and_the_gate_sends_nothing` | Gate recorder is `deliver_stream` with a recording callback. No real platform. |
| 4. mail during finalization | `test_event_arriving_before_the_outcome_write_stays_dispatchable` | Worker tail is not run. The interleaving is created by hand. |

## 6. Not covered, and next experiment

- Held-event undefer through the real `_apply_resource_hold_resume`: not
  driven. Next: drive it with a held `Run` and two accumulated letters.
- `observed_by` stamping and `_retire_internal_event`: not read.
- Delivery of a `done` event whose partial write failed: not driven.
- No claim of general soundness is made.
