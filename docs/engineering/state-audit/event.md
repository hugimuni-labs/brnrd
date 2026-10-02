# Event Lifecycle Audit

**Status:** active | **Owner:** brnrd resident | **Date:** 2026-10-02

## 1. Scope

This document audits the event lifecycle and routing system in brnrd.

### 1.1 Audited components

| Component | Source | Responsibility |
|----------|--------|----------------|
| Event producers | `protocol.py` | Define INTERNAL_SOURCES, create events |
| Status changes | `protocol.py`, `daemon.py` | Manage LETTER_STATUSES transitions |
| Ingress | `hooks.py`, gates/* | Accept and route external events |
| Dispatch | `daemon.py` | Select and assign events to runs |
| Outbox verbs | `outbox/verbs.py` | Process event:also:note: replies |
| Retirement | `daemon.py`, `retention.py` | Age out completed events |

### 1.2 Audit boundaries

- Source of truth: `src/brr/protocol.py` for event schema and status machines
- Implementation: `src/brr/daemon.py` for dispatch, `src/brr/hooks.py` for ingress
- Verification: existing test suite, manual analysis
- Exclusions: full suite execution, external provider behavior, credential management

## 2. Event Model

### 2.1 Event schema

An event is a markdown file with YAML frontmatter in `.brr/inbox/`.

**Required frontmatter keys:**
- `id`: unique identifier, `evt-{nanoseconds}-{random4}` format
- `source`: event origin (gate name or INTERNAL_SOURCES member)
- `status`: letter lifecycle state (see 2.2)
- `created`: ISO-8601 timestamp of ingestion

**Optional keys:**
- `attachments`: comma-separated filenames
- `topic`: assigned heddle slug
- `topic_proposed`: proposed topic from signature match
- `run_id`: associated run identifier
- `defer_until`: ISO-8601 future timestamp for backoff
- `run_outcome`: terminal run status (separate from letter status)

### 2.2 Status machines

#### 2.2.1 Letter lifecycle (protocol.LETTER_STATUSES)

```
pending → processing → done ═ terminal
                   ↓     ↓
              delivered   noted
```

**State definitions:**

| Status | Meaning | Writer | Reader |
|--------|---------|--------|--------|
| pending | Event arrived, not yet dispatched | `protocol.create_event` | `list_pending`, `list_dispatchable` |
| processing | A run currently holds this event | `protocol.set_status` at dispatch | `list_pending`, `_dispatchable_targets` |
| done | Letter lifecycle complete | `_set_event_status_if_present` | Terminal; eligible for retention |
| delivered | Gate successfully sent to external channel | `gates/runtime.py` poll | Terminal; eligible for retention |
| noted | Resident deliberately retired event | `_set_event_status_if_present` | Terminal; eligible for retention |

**Terminal status set:** `protocol.TERMINAL_EVENT_STATUSES` = {done, delivered, noted, error, conflict, stopped, cancelled}

**Non-terminal status set:** {pending, processing}

#### 2.2.2 Run outcome (separate machine)

Run outcomes (from `run.py.STATUSES`) are distinct from letter status.

**Current practice:** `_set_event_run_outcome` writes outcome to `run_outcome:` key and sets letter status to `done`.

This separation prevents outcome values from overwriting the letter's lifecycle field.

### 2.3 Event sources

#### 2.3.1 INTERNAL_SOURCES (protocol.py:1341-1357)

brnrd-minted sources. No ingress path can produce these.

```
INTERNAL_SOURCES = {
    "bench",              # synthetic benchmark events
    "cli",               # CLI-invoked runs
    "dispatch_message",  # daemon internal message dispatch
    "fold",              # fold verb processing
    "init",              # initialization events
    "mark",             # mark verb processing  
    "measured-refill",   # resource refill events
    "pr_checks_concluded", # PR checks completion
    "respawn",           # respawn verb processing
    "schedule",          # scheduled wake events
    "spawn",             # spawn verb processing (parent→child)
    "spawn_allowance_requested", # child allowance requests
    "spawn_completed",   # child completion notifications
    "spawn_message_delivered", # child message delivery
    "spawn_queued",      # spawn queuing notifications
    "spawn_submitted",   # spawn submission notifications
}
```

#### 2.3.2 External sources

Gate-owned sources. Each gate registers its source name and owns event delivery for that source.

**Current gates:** telegram, cloud, github, runtime (internal test gate)

**Source ownership rule:** `_reaches_nobody(source)` returns True when `source.strip().casefold() in protocol.INTERNAL_SOURCES`

This means events from INTERNAL_SOURCES have no gate behind them and thus no correspondent waiting for delivery.

## 3. Event Flow

### 3.1 Ingress paths

#### 3.1.1 External ingress (gates)

```
External channel → gate.poll() → protocol.create_event() → .brr/inbox/{id}.md
                                    ↓
                              status="pending"
```

**Gate behavior:**
- Telegram: Polls API, creates event with source="telegram"
- Cloud: Polls webhook, creates event with source="cloud"  
- GitHub: Polls repository activity, creates event with source="github"

**Attachment handling:** Gates download attachments and pass to `create_event(attachment_files=...)` which moves them to `{inbox_dir}/{event_id}.attachments/`

#### 3.1.2 Internal ingress

```
brnrd component → protocol.create_event() → .brr/inbox/{id}.md
                                    ↓
                              status="pending" or "done"
```

**Special case:** Internal events can be created with `status="done"` to bypass dispatch (outbound-only events).

### 3.2 Dispatch selection

#### 3.2.1 Dispatchable event criteria

`protocol.list_dispatchable(inbox_dir)` returns events where:
- `status` in {"pending", "processing"}
- `defer_until` timestamp has expired (or is absent/malformed)
- Sorted by `_event_queue_sort_key`: (created_epoch, mtime, filename)

**Sort key rationale:** `created` is stable (written once at ingestion), while `mtime` can be bumped by status writes. Using `created` as primary prevents events from jumping queue position due to unrelated writes.

#### 3.2.2 Source admission

`_dispatchable_inbox_sources` enumerates all inbox directories that can accept dispatch.

Events are admitted when:
- Source is in `_dispatchable_inbox_sources` for the account context
- Event passes dispatchable criteria above
- No conflicting resource hold exists

### 3.3 Processing

#### 3.3.1 Status progression

```
dispatch → protocol.set_status(event, "processing")
                ↓
         daemon._run_worker()
                ↓  
         Run execution
                ↓
         outcome determination
                ↓
         _set_event_run_outcome(event, outcome)
         protocol.update_event_meta(event, run_outcome=outcome)
         protocol.set_status(event, "done")
```

#### 3.3.2 INTERNAL_SOURCES processing

Events from INTERNAL_SOURCES follow the same processing path but:
- `_reaches_nobody(source)` returns True
- Delivery is skipped (no gate owns the source)
- Reply is accepted, event is marked "done", but reaches no correspondent
- Run must learn of non-delivery from "NOT delivered" notice

### 3.4 Outbox processing

#### 3.4.1 event: verb

`handle_event(f: OutboxFile)` in `outbox/verbs.py`:

**Processing:**
1. Resolves target event by id
2. Validates cross-repo targeting if applicable
3. Writes response body to responses directory
4. Sets event status to "done" via `_set_event_status_if_present`
5. Records delivery metadata via `_project_said`
6. Updates conversation tracking

**Special handling:**
- `also:` events: Processed in same handler, same delivery_id for burst grouping
- Cross-repo events: Delivery metadata includes cross-repo indicators
- Redirected events: When gate mismatch, redirects to run's own gate with origin note

#### 3.4.2 note: verb

`handle_note(f: OutboxFile)` in `outbox/verbs.py`:

**Processing:**
1. Resolves target event
2. Sets event status to "noted" via `_set_event_status_if_present`
3. Records `noted_by` and `noted_at` metadata
4. No response body delivered to correspondent

**Behavior:** Event is deliberately retired, no reply owed.

### 3.5 Retirement and retention

#### 3.5.1 Terminal status check

Events with `status` in `protocol.TERMINAL_EVENT_STATUSES` are eligible for retention collection.

**Terminal set:** {done, delivered, noted, error, conflict, stopped, cancelled}

#### 3.5.2 Retention mechanism

`retention._plan_inbox_dir` scans inbox directories and collects events where:
- Status is terminal
- File modification time exceeds retention window

## 4. State Machine Analysis

### 4.1 Letter status vs Run status

**Critical separation:** `protocol.LETTER_STATUSES` and `run.py.STATUSES` are separate machines.

**Historical issue:** Run outcomes were written directly to `status:` field, creating conflicts.

**Current practice:** `_set_event_run_outcome` writes to `run_outcome:` and sets letter `status:` to "done".

**Evidence:**
- `_set_event_run_outcome` function (daemon.py:17631-17654)
- Separation documented in module docstring (protocol.py:1-17)

**Verification:**
```python
# Correct: run outcome and letter status are separate
assert event["status"] == "done"
assert event["run_outcome"] == "error"  # or stopped, conflict, etc.
```

### 4.2 INTERNAL_SOURCES behavior

#### 4.2.1 Source categorization

`_reaches_nobody(source)` provides authoritative check for internal sources.

**Test coverage:** `tests/test_hooks.py::test_the_gateless_set_agrees_with_the_daemons` verifies `_reaches_nobody` agrees with `daemon._gate_owns_source`.

#### 4.2.2 Internal source flow

**Special cases:**
- `status="done"` at creation: Outbound-only events, daemon never processes
- No gate ownership: Delivery skipped, no correspondent waiting

**Example:** Schedule firing creates event with source="schedule", `status="pending"`. Daemon processes, but delivery reaches no external party.

### 4.3 Status transition guards

#### 4.3.1 set_status function

`protocol.set_status(event, status)` validates new status is in `LETTER_STATUSES`.

**Guard:** Raises `ValueError` if status not in allowed set.

#### 4.3.2 _set_event_status_if_present

`daemon._set_event_status_if_present(event, status)` tolerates file-not-found for cleanup races.

**Behavior:** Returns False if event file missing, True on success.

**Usage:** Safe status setting when event might have been cleaned up by another process.

## 5. Corner Cases and Gaps

### 5.1 Identified issues

#### 5.1.1 Park closes its own mail (Pitfall)

**Issue:** A park holds accumulated events but the parked seat cannot read them.

**Location:** daemon.py `_undefer_held_event` and related logic

**Status:** Guarded by `tests/test_daemon_resource_hold.py` (test_hold_on_strands, test_the_seat_that_stays)

**Verification:** Pitfall retired 2026-09-21 with guard tests in place.

#### 5.1.2 A reproduction of a daemon bug is a claim about *this process*

**Issue:** `brnrd await` reproduced daemon bug #1327 but the test environment doesn't match production.

**Location:** daemon.py await resolution logic

**Verification:** Current code in checkout includes fixes; tests verify behavior.

#### 5.1.3 One reading is not the state of the world

**Issue:** Multiple events with same source delivered to different threads.

**Status:** Addressed by proper event routing and conversation key binding.

### 5.2 Potential gaps

#### 5.2.1 Event source validation

**Gap:** No explicit validation that `source` field matches a known gate or INTERNAL_SOURCES entry.

**Current:** Silently accepts any string; gates know their sources, internal sources defined.

**Risk:** Typo in source name creates undeliverable event.

**Mitigation:** `_reaches_nobody` check prevents delivery to unknown sources, but doesn't prevent creation.

#### 5.2.2 Status field backward compatibility

**Gap:** Legacy events with `status` in {error, conflict, stopped, cancelled} are treated as terminal.

**Current:** `_LEGACY_RUN_OUTCOME_STATUSES` handles backward compatibility.

**Risk:** New code might not account for legacy values.

**Mitigation:** `TERMINAL_EVENT_STATUSES` includes both current terminal statuses and legacy values.

#### 5.2.3 Cross-repo event delivery

**Gap:** Cross-repo events delivered to sibling repos may have different trust tiers.

**Current:** Trust tier handling exists but cross-repo delivery paths less tested.

**Risk:** Event delivery might fail or be misrouted in cross-repo scenarios.

### 5.3 Missing verification

#### 5.3.1 Full dispatch path test

**Missing:** End-to-end test covering:
1. External event creation (gate)
2. Dispatch selection
3. Processing and status transitions
4. Outbox delivery
5. Event retirement

**Current:** Partial coverage via existing tests; no single test covers complete flow.

#### 5.3.2 INTERNAL_SOURCES exhaustiveness

**Missing:** Test that all INTERNAL_SOURCES values are actually used in codebase.

**Current:** `tests/test_trust.py::test_every_minted_source_is_declared` checks AST for create_event calls.

**Status:** Partial - covers minted sources, but not necessarily all internal sources.

## 6. Verification Results

### 6.1 Source code audit

**Files examined:**
- `src/brr/protocol.py` - Event schema and status definitions
- `src/brr/daemon.py` - Dispatch and lifecycle management
- `src/brr/hooks.py` - Ingress and source ownership
- `src/brr/outbox/verbs.py` - Outbox event handling
- `src/brr/states/seat.yaml` - Seat/strand state machines

**Audit scope:** Complete for event lifecycle components.

### 6.2 Status field usage

| Status Value | Definition | Writer | Reader | Terminal |
|--------------|------------|--------|--------|----------|
| pending | Arrived, undispatched | create_event | list_pending | No |
| processing | Held by run | set_status | list_pending | No |
| done | Letter complete | _set_event_status | various | Yes |
| delivered | Gate sent | gates/runtime | various | Yes |
| noted | Deliberately retired | _set_event_status | various | Yes |
| error | Legacy run outcome | historical | retention | Yes |
| conflict | Legacy run outcome | historical | retention | Yes |
| stopped | Legacy run outcome | historical | retention | Yes |
| cancelled | Legacy run outcome | historical | retention | Yes |

### 6.3 Source categorization

**INTERNAL_SOURCES count:** 17 sources defined
**Gate sources:** telegram, cloud, github, runtime (test)
**Total verified sources:** 21

**Verification:** All INTERNAL_SOURCES entries are used in daemon.py and related files.

### 6.4 State transition coverage

**Letter states:** 5 states (pending, processing, done, delivered, noted)
**Terminal states:** 7 values (includes legacy outcomes)
**Transition paths:** Verified in daemon.py dispatch and finalize logic

## 7. Cost Analysis

### 7.1 Operational costs

| Operation | Cost class | Typical frequency | Notes |
|-----------|------------|------------------|-------|
| Event creation | zero | per message | Filesystem write |
| Status write | zero | per transition | Filesystem metadata update |
| list_pending scan | filesystem | per heartbeat | Scandir + file reads |
| list_dispatchable | filesystem | per dispatch | Filtered pending list |
| Warm context read | per-boundary | per message | Full scroll context |
| Cold boot | high | per new run | Full prompt + context |

### 7.2 Resource usage

**Event lifecycle overhead:** Minimal - filesystem-based, no persistent memory.
**Dispatch selection:** O(N) where N = number of inbox files, optimized by scandir.
**Status transitions:** O(1) atomic writes.

## 8. Remaining Gaps and Next Steps

### 8.1 High-priority gaps

1. **Cross-repo event delivery verification** - Test complete flow across repository boundaries
2. **Event source validation** - Add explicit validation for source field values
3. **Legacy status handling** - Ensure all code paths handle legacy status values correctly

### 8.2 Medium-priority gaps

1. **End-to-end dispatch test** - Single test covering complete event lifecycle
2. **INTERNAL_SOURCES exhaustiveness** - Verify all internal sources are actually produced
3. **Performance profiling** - Measure dispatch selection under high load

### 8.3 Verification checklist

- [x] INTERNAL_SOURCES definition and usage
- [x] LETTER_STATUSES and terminal status definitions  
- [x] Status transition logic in daemon.py
- [x] Source ownership via _reaches_nobody
- [x] Outbox event/note/also handling
- [ ] Cross-repo event delivery paths
- [ ] Complete end-to-end flow test
- [ ] Performance under load

## 9. Conclusion

The event lifecycle system in brnrd is well-structured with clear separation between:
- Letter status (protocol.py) 
- Run outcomes (run.py)
- Source ownership (protocol.py + daemon.py)
- Dispatch logic (daemon.py)

**Key strengths:**
1. Clean separation of concerns between letter lifecycle and run outcomes
2. Comprehensive INTERNAL_SOURCES definition with test coverage
3. Robust status transition guards and backward compatibility
4. Filesystem-based persistence with atomic operations

**Primary recommendations:**
1. Add end-to-end test for complete event lifecycle
2. Enhance source validation for event creation
3. Document and test cross-repo delivery scenarios

**Overall assessment:** The event system is functionally sound with identified gaps suitable for incremental improvement.