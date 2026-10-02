# Seat and strand transitions

Checked against main 03c1b0a5 on 2026-10-02. This branch repairs claim expiry and delayed await arming. It also adds the schedule-release edge to the source specification. These changes do not change the hold or stop policy.

## Terms

| Term | Meaning |
| --- | --- |
| Seat | The resident's continuing place in an account conversation. It can span repository work and several Runs. |
| Run | One hosted execution of a Shell process. Its saved record survives the process. |
| Strand | A child Run with its own process, branch and report. The parent owns its control edge. |
| Hold | A saved resource record that allows the daemon to park and release a Run. |
| Resume claim | A daemon-owned, one-use permission to reopen a stored provider session. |

An event, a Run and a transport message have separate states. A rendered event is not necessarily handled. A saved session id does not prove that the provider will accept it.

## Cost classes

| Operation | Model cost |
| --- | --- |
| Create, select or save an event | No model call. File I/O and heartbeat time still apply. |
| Stay blocked inside await | No model call while blocked. |
| Return from await | One boundary that reads the current scroll. Cache behavior determines the read cost. |
| Start a new Run with a claim | Process startup, wake input and a read of the stored session. Provider acceptance is required. |
| Start without a claim | Process startup and wake input. No stored session is requested. |
| Strand work | Its own boot and model calls. The parent also spends on dispatch and review. |

This audit has no dollar meter or comparable boot sample. It gives no universal warm-to-cold ratio. Process startup and model input are different costs.

## Live await

| Transition | Trigger and result |
| --- | --- |
| Running to awaiting | The resident calls `brnrd await`. The outbox parser arms a wait generation. The same Shell stays alive. |
| Awaiting to running | An eligible event wins. It also outranks the optional file condition. |
| Awaiting to running | An explicit deadline expires, or an eligible idle initiative deadline expires. Initiative does not park the Run. |
| Awaiting to awaiting | The call reaches its existing lease or Shell cap. It returns `pending`. The daemon-side wait remains armed. |
| Awaiting to held | A measured quota wall, or an opted-in hold-cost rule, resolves the wait as `park`. |

The wait resolver records a sticky outcome. A later tick cannot replace an event outcome with timeout. An observed completion from this Run can be excluded at arm time. A person's message already pending at arm time still resolves the wait.

### Repair: delayed arming

The old CLI gave the outbox drain 30 seconds. A request could arm after the CLI had already returned an error. This happened during this audit and cost the child its live review room.

The repaired CLI uses the existing lease and Shell call bound for both arming and waiting. One absolute deadline bounds the whole call. A refused directive still fails. A queued request can arm after the call ends; the return does not cancel it.

Proof: a real drain fixture waits 40 seconds before arming and supplies an event at 60 seconds. It failed before the repair and passes after it. A two-second call still returns on time and leaves its queued request. The repaired CLI also completed an actual daemon file wait in this run. That call does not establish a drain delay over 30 seconds.

Codex and unknown Shells use a 480-second fallback slice unless a Shell override or stamped cap applies. The stamped bound reserves 45 seconds. Claude can use the 50-minute lease through its hook; this is not a universal Codex lease.

For a long blocked wait, approximate returned boundaries as `ceil(wait duration / call bound)`. Each return reads the scroll. Cache price and scroll size must be measured before converting this to dollars.

## Parked seat

| Transition | Guard and saved result |
| --- | --- |
| Running to held on strands | An accepted cut names a live child as `handoff`. The finalizer saves the hold. |
| Running to held on any event | The turn-end safety net parks a seat when configured and no other hold applies. |
| Running to held on refill | The measured binding quota is below the starvation floor. |
| Held on strands to dispatch | An owned child submits, finishes or asks for allowance. The parent id must match. |
| Held on strands to dispatch | A schedule event arrives. This hold is not a wall. The child can remain live. |
| Held on refill to dispatch | The measured binding quota reaches the refill floor. |
| Held on refill stays held | A schedule event or ordinary message arrives without a permitted release. Its mail is accumulated. |
| Non-wall hold to dispatch | A person's addressed message releases it. |
| Held to fresh successor | A handover releases the old hold and clears the old claim. |

Release marks the old Run done. The next dispatch starts a new Run. With a compatible claim it requests native session resume. Without a claim it starts cold. This differs from returning to the same process inside await.

### Repair: claim age and concurrent consumers

A dispatch on the wrong thread used to re-arm the claim. Re-arming gave it a new timestamp. It could renew an already expired claim.

The repair checks the original age first. A wrong-thread dispatch restores the original record without replacing a newer claim. Each consumer uses a unique private claim path, including when two workers share a daemon process.

Four driven cases cover expiry, preserved original age, a new claim arriving during restoration, and overlapping consumers in the same process. The expiry remains 24 hours. These tests do not prove provider-session validity.

### Remaining claim limit

A claimless refill can leave a previous unconsumed claim. A fixture demonstrates the state, but this audit found no production sequence that creates it across two releases. Do not clear all claims from this observation alone. Trace the actual producer sequence first.

The normal message release requires a native resume kind. The refill release checks for a session id. No current arming site was found that gives an unsupported hold a session id. The differing guards are not a reproduced fault.

## Strand

| Transition | Result |
| --- | --- |
| Spawn admitted to queued | The daemon saves a child dispatch event. No child process exists yet. |
| Queued to running | A pool slot starts a new child Run. |
| Running to submitted | The branch is published and the report exists. The parent gets a submit event. The child stays alive. |
| Submitted to running | A parent steer reaches the child. |
| Live to stopped | The `stop:` verb flags and ends the owned child. |
| Running to completed | Normal reap sends a completion event. |
| Crashed to completed | Crash reap sends a failure completion event to the parent. |
| Orphaned dispatch to reconciled | Boot recovery proves it stale, records an error outcome and sends one failure completion. |

A retired submit does not replay into a later hold. A fresh submit generation can release it. A submit from another parent cannot release it.

A cut row that says `stopped`, `converged` or `abandoned` is a declaration. It does not invoke `stop:`. Tests drive both the declaration and the real stop. Changing this policy needs a separate decision. The audit keeps it unchanged.

The seat's hold lives in its Run record. The parked child's owner edge is a different record. Ready, unmerged #2161 repairs that owner record across daemon re-exec and binds it to the child's hold generation. This branch does not duplicate that repair.

## Evidence and limits

| Driven path | Test location |
| --- | --- |
| Message versus initiative; sticky outcome | `tests/test_wait_release_seams.py` |
| Saved hold, refill, fresh generation, foreign parent | Same file; quota and control inputs use fixtures. |
| Schedule releases parent while child remains live | Same file; the control is registered before and checked after release. |
| Declaration versus actual stop | Same file; no provider process is killed. |
| Claim age, newer claim and overlapping consumers | `tests/test_resume_claim_expiry.py` |
| Delayed drain and shared call deadline | `tests/test_await_lease.py` |
| Crash dispatch and reap notification | `tests/test_daemon.py::test_crashed_spawn_notifies_parent_end_to_end` |
| Boot orphan reconciliation and no duplicate notice | Two `test_orphaned_spawn_*` cases in `tests/test_daemon.py`. |

The crash and boot cases run the real daemon dispatch or recovery path with a fake worker and isolated files. They do not restart a live provider. The generated state checker verifies structure, named symbols and listed outcomes. It cannot prove delivery or guard truth. The source specification now includes the schedule exit from `held_strands`.

Still unproved: provider acceptance of a native session, a recorded native failure during armed await, live cleanup and restart, and token/cache costs for comparable warm and cold cases. The unsigned three-state design remains a proposal.
