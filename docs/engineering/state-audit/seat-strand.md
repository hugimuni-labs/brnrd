# Seat and strand: wait and release seams

Source head: 03c1b0a5 (origin/main, 2026-10-02). Author: strand of run-261002-1102-dr7r.
The parent owns synthesis and architecture decisions. This page gives facts and open forks.
The rejected Vibe report (brr/the-seat-and-strand-transitions-audited5451e49c) is not a source.
This page does not use any number from it.

## 1. Terms

- **Seat**: the one resident Run that a repository can park and wake.
- **Strand**: a Run that a seat dispatched with `spawn: true`.
- **Hold record**: `Run.meta["resource_hold"]`. It is the daemon's authority for "parked".
- **Claim**: the file `pending-resume.json` (`pending_resume.py`). One claim per seat.
- **Same-process return**: `brnrd await` returns inside the live Shell process. No new Run starts.
  The model context stays in the process. Source: `cli.cmd_await`, `await_verb.evaluate`.
- **Native session claim**: the daemon wrote a claim that holds a provider session id.
  The next dispatch that leads can pass the id to `codex exec resume` or `claude --resume`.
  A new process starts. The Shell reopens the stored transcript. Source: `worker/prepare.py:304`,
  `daemon._resume_session_for_runner`, `runner.py:2877`.
- **Cold start**: a new process with no session id. The wake text is the only inheritance.
- A claim is not proof. The daemon can prove it wrote the claim. Only the provider can prove the
  session still exists. No test here reaches a provider.
- **Process startup** (fork, exec, hooks) is not **model initialization** (reading the wake and,
  on a native resume, the stored scroll). Cost differs for each. Do not add them as one number.

## 2. Shared columns

Columns: source state, trigger and actor, guard and writer, receiver, process, persistence,
model cost, evidence. "Evidence" names the test that drives the edge, or says none.
Cost words: **tokens** = the model reads or writes. **zero model tokens** = no model call.
Zero model tokens is not zero I/O or zero time: the daemon heartbeat (about 10 s) still runs.

### 2.1 Await (inside a live seat or strand)

| Source | Trigger, actor | Guard and writer | Receiver | Process | Persistence | Model cost | Evidence |
|---|---|---|---|---|---|---|---|
| running | `brnrd await` by resident | `outbox/verbs.handle_await` writes `meta["await"]` | daemon heartbeat | alive | run meta | tokens for the call | test_await_verb |
| armed | pending event, daemon | `await_verb.evaluate`: event outranks file | resident | alive | sticky `resolved` | warm read of the scroll | test_wait_release_seams (seam 1) |
| armed | deadline, daemon | `_resolve_await_state`: `now >= deadline` and no event | resident | alive | sticky | same | test_seat_state_machine |
| armed | initiative timeout, daemon | `_initiative_timeout`: not strand, enabled, no pending hold, no working child, correspondent older than live window, pace ahead | resident | alive | sticky, outcome `timeout` | same | test_seat_state_machine, seam 1 |
| armed | call lease ends | `cli.cmd_await`: `ceiling` or `shell_cap` | resident | alive | wait stays armed | one boundary at full context | test_await_lease |
| armed | park, daemon | starvation floor, or opted-in hold cost | none | ends | hold record | zero | test_starvation_hold |

Lease limits (read from source, not measured):
- Codex and unknown Shells: `_AWAIT_SLICE_CEILING_SECONDS` = 480 s, from `cli._await_slice_ceiling_seconds`.
  With a stamped cap the CLI returns at `cap - CALL_CAP_MARGIN_SECONDS` (45 s). The 480 s is a
  fallback. The per-Shell table `_AWAIT_SLICE_CEILING_BY_SHELL` can override it.
- Claude: `LEASE_DEFAULT_SECONDS` = 50 min. The hook can stamp `BRNRD_AWAIT_CALL_CAP_MS`.
  A stamp lowers the call bound. No stamp falls back to the per-Shell slice. It is not universal.
- Each `pending` return costs one model boundary. The cost is one read of the scroll.
  Formula: `reads = ceil(wait_seconds / call_bound)`. Cost per read = scroll tokens times the
  cache rate (cached or uncached). Neither value is measured here. Unknown. Do not give a dollar ratio.

An initiative timeout returns the Run to `running`. It does not park. Only `hold_costlier_than_boot`
and starvation park, and the first is off by default (`seat.park_on_hold_cost`).

### 2.2 Seat holds and releases

| Source | Trigger, actor | Guard and writer | Receiver | Process | Persistence | Model cost | Evidence |
|---|---|---|---|---|---|---|---|
| running | cut with live `handoff` row, daemon | `_park_bolt_on_live_strands`: row names a child in `_working_child_controls` | worker tail `_finalize_resource_hold` | ends | `resource_hold` `strands` | zero | test_hold_on_strands, test_seat_state_machine |
| running | turn end, nothing armed | `_park_seat_on_turn_end` | none | ends | `resource_hold` `any` | zero | test_the_seat_that_stays |
| running | usage limit, provider | `_maybe_arm_resource_hold_on_failure` | none | ends | `operator` hold + session id | zero | test_daemon_resource_hold |
| running | quota under floor, daemon | `_starvation_facet` | none | ends | `refill` hold + quota block | zero | test_starvation_hold |
| held `strands` | child event, strand | `strand_event_releases`: source in `STRAND_RELEASE_SOURCES`, parent id match | dispatch | new process | hold released, claim armed | native: stored scroll read; cold: wake only | test_seat_state_machine, seam 3 |
| held `strands` or `any` | `schedule` event | `schedule_event_releases`: any non-wall hold | dispatch | new process | same | same | seam 3 |
| held `refill` | measured refill, daemon | `_release_reset_holds_due`: `refill_condition_met` | synthetic `measured-refill` event | new process | claim armed if session id | same | test_seat_refill_resume, seam 2 |
| held `refill` | message, user | `refuses_correspondent`: stays armed, answered with reading | none | none | event accumulated | zero | test_starvation_hold |
| held (non-wall) | message, user | `_apply_resource_hold_resume` | dispatch | new process | same | same | test_resource_hold |
| held | handover event | `handover_event_releases`: wall or not | dispatch | new process | claim cleared | cold by design | test_resource_hold |

Release writers:
- `release_held_run` consumes the hold and moves the Run to `done`. It is the one writer.
- `_apply_resource_hold_resume` arms a claim only if `native_session_id` exists and `resume_kind`
  is `native`. `_release_reset_holds_due` arms a claim if `native_session_id` exists. It does not read
  `resume_kind`. `resource_hold.build` forces `unsupported` when no id exists. No arming site found
  passes an id with `unsupported`. The two guards differ but no reachable input shows the difference.
- `pending_resume.consume` refuses a claim armed for another conversation key and re-arms it.
  It deletes a claim older than 24 h. It does not check provider. `_resume_session_for_runner` does.
  A provider mismatch gives a cold start and sets `resume_cold_reason`.
- `_arm_resource_hold` sets `generation = previous generation + 1` for the same Run and supersedes
  every other active seat hold in the repo. The shuttle row and `pending-resume.json` live in the
  account home or the repo `.brr`. A cold daemon reads the hold from `run.md`. It keeps no memory.

### 2.3 Strand

| Source | Trigger, actor | Guard and writer | Receiver | Process | Persistence | Model cost | Evidence |
|---|---|---|---|---|---|---|---|
| none | `spawn: true` | `_queue_spawn_request` | pool | none | event | zero | test_spawn_row_contract |
| queued | slot free | `_run_worker` | strand | new process, cold | run.md | cold boot of the Core | test_spawn_row_contract |
| running | `submit: true` | `_queue_submit_request`: branch published, report file exists, parent known | parent as `spawn_submitted` | alive | event | tokens (one turn) | test_submit_probe_root |
| submitted | parent `to:` | `_queue_child_message` | strand | alive | event | warm | test_strand_work_survives |
| any live | `stop: <id>` | `_apply_run_stop`: flag then kill by label, or cancel the event | none | ends | control `stopped` | zero | test_spawn_stop |
| running | exit | `_notify_spawn_parent` writes `spawn_completed` | parent | ends | event | zero | test_spawn_row_contract |
| running | crash or late reap | `_notify_spawn_parent_of_crash` | parent | ends | event | zero | source only, see section 4 |
| running | usage wall | strand parks, `_park_run_control` | parent keeps edge | ends | hold + edge record | zero | test_parked_controls_after_reexec (open PR #2161; see seam 2) |

A strand never is the seat. `_arm_resource_hold` returns early for a strand. The claim carries the
seat key, so a strand under `run:<parent>` cannot take it.

## 3. The four seams

Tests: `tests/test_wait_release_seams.py`. Mocks: quota reading, pace reading, run-control registry.
No Shell runs. No provider is called.

### Seam 1: initiative timeout and a message
- The deadline test is after `await_verb.evaluate`. A pending event wins. Outcome is `event`.
- A message also moves `hold_correspondent_at`. That makes the initiative ineligible on later ticks.
- `armed_pending_ids` excludes only observed `spawn_completed` events of this run. A message already
  pending at arm time is not excluded. It resolves the wait on the first tick.
- No hold is staged or armed on this path. The Run returns to `running`.
- Tests: message beats timeout; event outcome is sticky; message pending at arm resolves; observed
  completion at arm does not stop initiative.

### Seam 2: refill release, generations, claim versus proof
- Daemon authority: hold record `released_by = refill`, one claim file, one synthetic event.
- Provider proof: none. The session id is a string the daemon copied at park time.
- An old released hold keeps its own receipt. A new seat hold is read from `run.md` by a cold process.
- With no session id the release writes no claim and the dispatch is a cold start. The record says
  `resume_kind = unsupported`.
- **Finding F1.** A claimless release does not clear an earlier unconsumed claim. The claim stays
  until the same conversation key consumes it or 24 h pass. The handover path calls
  `pending_resume.clear`. The refill path does not. Test `test_a_claimless_refill_leaves_...` pins
  the present behavior. It is a finding, not a goal. Fix is one line, but see fork F-A.
- Open PR #2161 (read in full: `daemon.py` +309, one test file) repairs a different record. On main
  `_run_controls` is memory only. A parked strand loses its parent edge on a fresh daemon image.
  #2161 writes `runs/<child>/edge.json` with the current owner, bound to the child's hold generation
  and `armed_at`. Boot recovery rejects a record from another hold or one the adoption fence could not
  produce. That is the owner-record seam for strands. It is not the seat hold record.
  The seat's own hold already lives in `run.md`, so a cold image reads it. A `strands` release does
  not need `_run_controls`: it matches `spawn_parent_run_id` or the hold's `child_run_ids`.
  Seam 2 here tests the seat record and the claim. It adds no owner-record case.

### Seam 3: retired submit, handoff park, next tick, fresh generation
- A retired (`delivered`) `spawn_submitted` is not in `list_pending`. It cannot release a hold.
- A fresh submit from the same parent releases a `strands` hold (`released_by = strand`).
  The hold generation does not change. It counts holds, not submits.
- A submit from another parent does not release it.
- **Finding F2.** A `schedule` event releases a `strands` hold while the child still works.
  `seat.yaml` lists only "any message" and "release" as exits of `held_strands`. The code is
  intentional (`schedule_event_releases` docstring). The spec omits the edge. A refill wall is not
  released by a tick: the event is accumulated.
- After release, a later strand event finds no active hold and passes to normal dispatch.

### Seam 4: stopped or converged declaration versus a real stop
- A cut `strands:` row is a declaration. Only `handoff` for a working child changes behavior.
- `stopped`, `converged` and `abandoned` rows do not stop the child. The child stays in
  `_working_child_controls`. `control["stopped"]` stays unset. The seat closes with a live child.
- `_apply_run_stop` (the `stop:` verb) sets the flag. Then `_owned_child_controls` drops the child.
- **Finding F3.** A seat can declare `stopped` for a strand it did not stop. The bolt does not
  compare the word with the registry. The code docstring says this is deliberate ("force the
  declaration, never police it").
- A `handoff` row for a child that is not live closes normally.

## 4. Source-limited rows

- Native resume provider, session or time mismatch: provider mismatch gives a cold start with a
  reason (tested). Session mismatch (id no longer valid) is not testable without a provider.
  Time: a 24 h claim expiry is tested. The provider's own expiry is unknown.
- Late or fallback reap: `_notify_spawn_parent` and `_notify_spawn_parent_of_crash` write the
  completion. Call sites are in `daemon.py` near lines 15104, 18669 and 18678. `seat.yaml` names only
  the first. The comment at `daemon.py:6412` names `_reap_parked_or_retire`. That function does not
  exist in this tree. The comment is stale. No test here drives the crash path. Not verified.
- #2151 ending-frame repair belongs to brr/the-frame-keeps-its-cause. Not touched here.

## 5. Checker limits

`tests/test_seat_state_machine.py` and `src/brr/states/seat.yaml` prove that named symbols exist
and are reachable. They do not prove that a trigger arrives, that a guard matches the stated
words, or that a native session continues. Example: the yaml edge `held_any -> running` names
`_handle_resource_held_events`. The code also releases `held_strands` on a tick. The yaml has
no such edge. The checker cannot see it.

## 6. Open forks for the parent

- **F-A (claimless release and old claim).** Options: (1) clear the claim on every release that
  arms none; (2) keep as is, rely on key and 24 h expiry. Recommendation: 1. Not done here. It
  changes resume policy, and the parent said not to migrate policy.
- **F-B (tick wakes a strands hold).** Options: (1) keep, add the edge to `seat.yaml`;
  (2) make `strands` a wall for ticks. Cost of a tick wake: one boot with the child still working.
  Recommendation: 1 now, decide 2 with cost data. No data exists here.
- **F-C (declared stop).** Options: (1) keep; (2) bounce a `stopped` row for a live child.
  Recommendation: 2, because the bounce ladder already exists. Not done.

## 7. What this audit did not measure

Token costs, cache rates, boot costs and quota effects. The formulas are in section 2.1.
Provider acceptance of a resumed session. The crash and reap path. Real daemon restart.
