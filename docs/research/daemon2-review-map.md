# daemon2 review map

## 1. Module map

Reviewed source: `c4022c1df0ee0699c33ee5aa9a52598157771a9b` (2026-10-04).
Status: complete static review; published on `brr/daemon2-review-map`.
Daemon submission blocked; see delivery status below. Code unchanged. Coordinates below belong to
that snapshot. `d2/` means `src/brr/daemon2/`; other source coordinates use
`src/brr/` as their prefix. Line counts include comments and blank lines.

Primitive labels use the three-part vocabulary of `design-the-target-daemon.md`
(facts, leases, asks); a mixed label describes the implementation, not a promise
that the target design is fully wired. Legacy imports below are **direct imports
outside daemon2**, including function-local imports; transitive imports are not
an exhaustive dependency graph.

| Module | Lines | Primitive | One-sentence job and source anchor | Direct legacy `brr` imports |
| --- | ---: | --- | --- | --- |
| `__main__` | 52 | glue | Parse once/serve and role options, construct `Daemon2`, and turn SIGTERM/SIGINT into a request to stop after dispatch (`d2/__main__.py:14`, `d2/__main__.py:40`). | None (`d2/__main__.py:11` imports internal runtime). |
| `authority` | 30 | facts / glue | Authorize owner controls from pending-letter facts and handovers from the parked seat's exact run/generation (`d2/authority.py:14`). | None (`d2/authority.py:5`). |
| `controls` | 144 | legacy bridge | Mirror control files into legacy Run records, cards, menus, topics and update packets (`d2/controls.py:29`, `d2/controls.py:47`). | `account`, `card`, `card_frame`, `menus`, `promises`, `protocol`, `relics`, `run_ledger`, `run_topic`, `updates`, `run.Run` (`d2/controls.py:10`, `d2/controls.py:11`). |
| `doors` | 174 | facts / legacy bridge | Adapt existing event/response/outbox files and project letter facts onto the old status vocabulary (`d2/doors.py:53`, `d2/doors.py:87`, `d2/doors.py:112`). | `portals`, `protocol`, `outbox.table` (`d2/doors.py:12`, `d2/doors.py:13`). |
| `facts` | 272 | facts | Persist versioned fact streams, order their union causally/HLC, and fold letter dispositions including migration (`d2/facts.py:37`, `d2/facts.py:74`, `d2/facts.py:109`, `d2/facts.py:215`). | None (`d2/facts.py:10`). |
| `leases` | 196 | leases | Implement local locked expiring claims and generation-fenced, receipt-recorded effects (`d2/leases.py:55`, `d2/leases.py:103`, `d2/leases.py:161`). | None (`d2/leases.py:21` imports internal facts). |
| `letters` | 109 | facts / leases | Import old letter dispositions and claim, answer, retire or release letters through facts and leases (`d2/letters.py:26`, `d2/letters.py:49`, `d2/letters.py:81`). | None (`d2/letters.py:8`). |
| `placement` | 124 | legacy bridge | Allocate isolated strand clones, pin git to them, land/push their branches and preserve unsuccessful or dirty clones (`d2/placement.py:36`, `d2/placement.py:58`, `d2/placement.py:100`). | `gitops`, `worktree` (`d2/placement.py:26`). |
| `router` | 90 | asks / legacy bridge | Resolve an explicit conversation, optional ask and child edge before placement, retaining unaddressed letters on individual triage seats (`d2/router.py:37`, `d2/router.py:76`). | `conversations`, `protocol` (`d2/router.py:8`). |
| `runtime` | 1,640 | glue / legacy bridge | Coordinate dispatch, lease heartbeats, seat recovery, outbox verbs, portals and Shell invocation (`d2/runtime.py:47`, `d2/runtime.py:466`, `d2/runtime.py:1195`, `d2/runtime.py:1291`). | `account`, `allowance`, `await_verb`, `closekeyword`, `config`, `conversations`, `course`, `cut_verb`, `dev_reload`, `gates`, `gitops`, `halt_verb`, `halts`, `hold_verb`, `hud`, `message_store`, `portals`, `presence`, `promises`, `prompts`, `protocol`, `relics`, `run_ledger`, `runner`, `trust`, `updates`, `worktree` (`d2/runtime.py:22`); function-local `daemon` (`d2/runtime.py:174`, `d2/runtime.py:221`, `d2/runtime.py:288`, `d2/runtime.py:570`, `d2/runtime.py:1108`, `d2/runtime.py:1152`, `d2/runtime.py:1256`, `d2/runtime.py:1436`, `d2/runtime.py:1582`). No direct `hooks` import. |
| `seat` | 326 | glue (seat checkpoint) | Store generation-checked mutable seat snapshots and apply statechart transitions and typed wake predicates (`d2/seat.py:110`, `d2/seat.py:166`, `d2/seat.py:208`). | None (`d2/seat.py:19` imports internal statecharts). |
| `statecharts` | 48 | glue | Load JSON-shaped `.yaml` resources, check reachability and reject illegal transitions (`d2/statecharts.py:16`, `d2/statecharts.py:23`, `d2/statecharts.py:39`). | None (`d2/statecharts.py:5`). |
| `states/` | 44 total: ask 12, letter 13, seat 19 | asks / facts / glue | Declare ask, letter and seat transition graphs (`d2/states/ask.yaml:2`, `d2/states/letter.yaml:2`, `d2/states/seat.yaml:2`). | None; data resources read by `d2/statecharts.py:42`. |
| `supervisor` | 91 | asks / facts | Fold ask-scoped child facts and register/attest exact conversation-parent-edge returns (`d2/supervisor.py:29`, `d2/supervisor.py:53`, `d2/supervisor.py:67`, `d2/supervisor.py:78`). | None (`d2/supervisor.py:8`). |
| `transport` | 46 | leases / legacy bridge | Install a self-lease-fenced wrapper around existing gate delivery callbacks and record undeliverable sends (`d2/transport.py:20`, `d2/transport.py:26`, `d2/transport.py:42`). | `gates.runtime` (`d2/transport.py:8`). |

The package also has a 12-line export-only `__init__.py`
(`d2/__init__.py:6`) and a portal template loaded by doors
(`d2/doors.py:16`); neither is an additional runtime service.

## 2. The seat's lifecycle as the code runs it

The persisted states are `running`, `awaiting`, `parked`, `ended`; a missing
record starts as `parked`, generation zero (`d2/states/seat.yaml:2`,
`d2/seat.py:110`, `d2/seat.py:159`). State transitions call
`Machine.next` and then the locked, fsynced atomic snapshot replacement;
ordinary checkpoint and queued-letter writes also increment generation
(`d2/seat.py:221`, `d2/seat.py:166`, `d2/seat.py:248`, `d2/seat.py:278`).

### Dispatch and transitions

`serve()` calls `once()` synchronously; a resident needs the account `self`
lease, a strand uses `strand:<event-id>`, and each dispatched letter gets its
own claim (`d2/runtime.py:1268`, `d2/runtime.py:1308`,
`d2/runtime.py:1322`). Resident seat identity is the routed conversation;
strand seat identity adds `#strand:<run-id>` (`d2/runtime.py:1374`).

| From → to | Actual function / caller | Trigger and effect |
| --- | --- | --- |
| `parked` or `ended` → `running` | `Seat.start`, `d2/seat.py:227`; `Daemon2.once`, `d2/runtime.py:1400`, `d2/runtime.py:1405` | Dispatch with no saved wake predicates, or a new dispatch after an ended record; set run ID. |
| `parked` → `running` | `Seat.wake`, `d2/seat.py:286`; `once`, `d2/runtime.py:1380` | Construct handover, schedule or mail signal; match a saved predicate, increment resume generation, choose native/checkpoint mode. A mismatch returns without starting a Shell (`d2/runtime.py:1398`). |
| `running` or `awaiting` → `running` | `Seat.recover`, `d2/seat.py:308`; `once`, `d2/runtime.py:1402` | An interrupted record is recovered into a new run, choosing native/checkpoint mode and incrementing resume generation. |
| `running` → `awaiting` | `Seat.await_signal`, `d2/seat.py:230`; `_handle_outbox`, `d2/runtime.py:601` | Accept `await:` and save an M predicate; the detailed file/deadline/arming record lives in the dispatch's in-memory `state["await"]` (`d2/runtime.py:614`). |
| `awaiting` → `running` | `Seat.resolve_await`, `d2/seat.py:243`; `_tick`, `d2/runtime.py:1070` | Visible pending mail, an awaited file, or deadline resolves the live await. A capped call can re-arm while still awaiting without another seat transition (`d2/runtime.py:606`). `Seat.signal` offers predicate-based resolution, but this runtime calls `resolve_await` (`d2/seat.py:233`, `d2/runtime.py:1080`). |
| `running` or `awaiting` → `parked` | `Seat.park`, `d2/seat.py:260`; hold handler `d2/runtime.py:563` | Accept a measured resource hold through legacy helpers, persist wake predicates and quota reason (`d2/runtime.py:574`, `d2/runtime.py:594`). |
| `running` → `parked` | `Seat.handover`, `d2/seat.py:266`; halt/respawn handler `d2/runtime.py:853` | Create successor letter first, settle the waking letter, persist carry and exact source run/generation, then queue successor ID (`d2/runtime.py:856`, `d2/runtime.py:866`). |
| `running`, `awaiting` or `parked` → `ended` | `Seat.end`, `d2/seat.py:325`; uncarried halt `d2/runtime.py:873`, stopped strand `d2/runtime.py:1553` | End after uncarried halt or a stopped child. Graph permits all three source states (`d2/states/seat.yaml:15`). |
| `running` → `parked` | cut handler `d2/runtime.py:909` | Save minimal checkpoint and handed-off strand obligations with `native_session=None`; park on child/general predicates (`d2/runtime.py:921`, `d2/runtime.py:927`). |
| `running` or `awaiting` → `parked` | `once` turn-end tail, `d2/runtime.py:1560` | Normal turn exit without halt/cut parks with reason `turn_ended`; details below. |

The complete graph is `d2/states/seat.yaml:4`; the table distinguishes its
edges from runtime callers. Accepted halt/cut/hold change the persisted seat
inside the outbox handler, but those handlers do not themselves kill the Shell
(`d2/runtime.py:597`, `d2/runtime.py:881`, `d2/runtime.py:934`).

### Shell exits after a turn without `halt:`

1. `runner.invoke_runner()` returns to `once`; an allocated strand first gets
   a last drain and placement publication, then the outbox pump is stopped
   (`d2/runtime.py:1519`, `d2/runtime.py:1525`, `d2/runtime.py:1529`,
   `d2/runtime.py:1538`).
2. Lease-loss exits early, leaving the seat/letter for expiry and recovery;
   a stopped strand instead retires its claimed letter and ends its seat
   (`d2/runtime.py:1541`, `d2/runtime.py:1546`).
3. Otherwise a final `_tick` runs; nonempty stdout answers the waking letter
   if it has not already been settled. This test does **not** require return
   code zero (`d2/runtime.py:1556`, `d2/runtime.py:1557`).
4. A seat still `awaiting` is parked immediately, without a new checkpoint
   (`d2/runtime.py:1563`). A seat still `running` saves only `{run, last_event}`
   and either no obligations or `letter:<id>` when unanswered, explicitly
   writes `native_session=None`, then parks (`d2/runtime.py:1566`). Both use
   `legacy_wake_on("any")`: schedule, mail, authorized handover/control, with
   no child edge supplied at this call (`d2/runtime.py:1565`,
   `d2/runtime.py:1574`, `d2/seat.py:102`). An already parked hold stays parked;
   halted/cut state skips this tail (`d2/runtime.py:1561`).
5. ControlMirror separately marks the legacy Run `done` for return code zero,
   or error/held for its other cases; it emits the finish update and persists
   a legacy state document (`d2/runtime.py:1575`, `d2/controls.py:133`,
   `d2/runtime.py:1582`). The seat can therefore be parked while the Run reads
   done. Presence is removed and an execution lease owned by this dispatch is
   released in `finally` (`d2/runtime.py:1592`). The account self lease passed
   by `serve` stays held until serve ends (`d2/runtime.py:1597`,
   `d2/runtime.py:1281`).

### `native_session`: writer, reader, missing connection

The field defaults to `None` and is deserialized from seat JSON
(`d2/seat.py:118`, `d2/seat.py:138`). `Seat.checkpoint()` can write a supplied
session dictionary (`d2/seat.py:248`, `d2/seat.py:255`), but **both production
checkpoint calls in daemon2 supply `None`**: cut (`d2/runtime.py:925`) and
running turn-end (`d2/runtime.py:1571`). No production path that writes a real
native session to the daemon2 SeatStore is established. The tests manually
supply native records (`tests/test_daemon2_seat_router.py:23`,
`tests/test_daemon2_seat_router.py:112`).

`Seat.wake()` and `Seat.recover()` read a stored record, requiring matching
`shell`, nonempty `id`, future `valid_until`, and `valid_for` contained in the
available capabilities (`d2/seat.py:298`, `d2/seat.py:314`). `once` passes the
selected **profile name** as `shell` and an empty capability set
(`d2/runtime.py:1395`, `d2/runtime.py:1403`). If those checks succeed,
`Wake.session_id` reaches `RunnerInvocation.resume_native_session_id`
(`d2/runtime.py:1515`), which the legacy runner converts into Codex/Claude
resume argv (`runner.py:2877`, `runner.py:2886`). Otherwise the next prompt
gets checkpoint, carry, obligations and queued-letter JSON
(`d2/runtime.py:1415`).

The runner already extracts real IDs into `RunnerResult.codex_thread_id` and
`.claude_session_id` (`runner.py:2979`, `runner.py:3026`, `runner.py:3080`),
but `once` consumes stdout/returncode rather than saving those IDs
(`d2/runtime.py:1557`, `d2/runtime.py:1590`). A warm resume generated by the
normal daemon2 dispatch path is therefore **not established**; the reader and
argv plumbing alone do not establish capture.

## 3. The process boundary

### Start, wait, kill

The daemon constructs a legacy `RunnerInvocation` and calls
`runner.invoke_runner` synchronously (`d2/runtime.py:1499`,
`d2/runtime.py:1519`). The actual Shell `subprocess.Popen` is in
`runner.start_registered_process` (`runner.py:128`, `runner.py:143`), called
from `invoke_runner` with cwd, environment, optional stdin and stdout/stderr
files (`runner.py:2917`). Its wait is `proc.wait(timeout=timeout)`
(`runner.py:2950`). daemon2 supplies no invocation timeout; the default is
`None` (`d2/runtime.py:1499`, `runner.py:736`).

A dispatch heartbeat renews execution and tracked letter leases every TTL/3;
a stopped child or failed renewal calls `_terminate_runner`
(`d2/runtime.py:1346`). That helper looks up the process in the local runner
registry, sends SIGTERM, waits 0.5 seconds by default, then calls
`runner.kill_matching` (`d2/runtime.py:104`). The registry is process-local
(`runner.py:93`); the final kill calls `Popen.kill`, not a process-group kill
(`runner.py:202`, `runner.py:173`). Parent `stop:` records a child-stopped fact
and attempts the same kill (`d2/runtime.py:765`); a separate follower observes
the fact in its own heartbeat (`d2/runtime.py:1349`). Runner timeout, when
configured by other callers, also kills and reaps (`runner.py:2951`).

Stopping **the daemon** is a different boundary: SIGINT/SIGTERM handlers call
`Daemon2.stop`, which sets `_stop_serve` and waits for the current synchronous
dispatch to finish (`d2/__main__.py:40`, `d2/runtime.py:1286`,
`d2/runtime.py:1269`; installed CLI equivalent `cli.py:7325`). CLI launch starts
strand-only follower processes; after serve returns it terminates, waits up
to ten seconds, then kills unfinished follower processes (`cli.py:7337`,
`cli.py:7346`). Descendant Shell cleanup after a forcibly killed follower is
**not established** by that block or the local Shell kill helper
(`cli.py:7354`, `runner.py:173`).

### What survives Shell death

| Surface | What remains / recovery meaning | Source |
| --- | --- | --- |
| Facts | Versioned JSONL under `home/daemon2/facts`; appends fsync, and letter state folds on read. | `d2/runtime.py:75`, `d2/facts.py:113`, `d2/facts.py:135`, `d2/letters.py:23` |
| Seat | Atomic, fsynced `home/daemon2/seats` JSON: checkpoint/carry/predicates/obligations remain, while native capture is missing as above. | `d2/runtime.py:78`, `d2/seat.py:110`, `d2/seat.py:166` |
| Leases | Locked atomic JSON under `home/daemon2/leases`; a live daemon can keep renewing after a Shell exit until dispatch cleanup, while daemon death leaves expiry to make claims acquirable. | `d2/runtime.py:76`, `d2/runtime.py:1346`, `d2/runtime.py:1592`, `d2/leases.py:103`, `d2/leases.py:134` |
| Letters and responses | Legacy inbox carriers remain; facts project status, and deterministic response files plus send receipts survive. Answering releases the letter lease; otherwise it is left to expire after the heartbeat stops. | `d2/doors.py:53`, `d2/doors.py:87`, `d2/letters.py:90`, `d2/runtime.py:1592` |
| Run context and controls | `runs/<run>/context.md`, outbox/control files and legacy Run/card/state documents remain; the pump consumes and unlinks ordinary directives. | `d2/runtime.py:1407`, `d2/runtime.py:1435`, `d2/controls.py:43`, `d2/controls.py:94`, `d2/runtime.py:1063`, `d2/runtime.py:1583` |
| Shell streams | Streams go to disk during execution; nonzero Shell process return codes retain the capture and return-code file, while zero removes it. | `runner.py:2847`, `runner.py:2914`, `runner.py:2629` |
| Strand work | Committed branch is landed/pushed before clone cleanup; dirty or failed publication retains the clone. This is finalization behavior, not a daemon-crash salvage guarantee. | `d2/runtime.py:1527`, `d2/placement.py:100` |

The live in-memory await record and process registry are not seat checkpoints:
the await handler saves only an M predicate to the seat and keeps its file and
deadline in the dispatch dictionary (`d2/runtime.py:614`, `d2/runtime.py:615`);
the process registry is a Python dictionary (`runner.py:93`).

### Legacy hooks: interface retained, installation not established

There is no direct `hooks.py` import in daemon2's runtime imports
(`d2/runtime.py:22`). It writes `BRR_PORTAL_STATE`, outbox, conversation and
run variables and publishes a legacy HUD (`d2/runtime.py:1504`,
`d2/runtime.py:1129`), leaving the existing hook interface usable. If hooks
are installed for that Shell, their roles remain:

- `PHASE_SESSION_START`: seed orientation from the portal (`hooks.py:6533`).
- `PHASE_POST_TOOL`: portal delta and inbound steering at tool boundaries
  (`hooks.py:6914`, `hooks.py:6888`).
- `PHASE_STOP`: pending-event and closeout blocking before Shell exit
  (`hooks.py:6811`, `hooks.py:6826`, `hooks.py:6861`).
- `PHASE_PRE_TOOL`: Claude's rooted-write/Monitor/Bash seam, including await
  timeout rewriting; Codex's generated hook args cover only the other three
  phases (`hooks.py:7128`, `hooks.py:7533`, `hooks.py:7160`). daemon2 widens
  Claude's Bash cap through `_await_lease_env` (`d2/runtime.py:1601`).

**Important reading boundary:** daemon2 does not call the engine-1 worker
preparation that installs these hooks (`worker/prepare.py:1426`), nor supply
`extra_runner_args` in its invocation (`d2/runtime.py:1499`;
`runner.py:746`, `runner.py:2838`). The legacy runner builds argv from profile
cmd plus explicitly supplied extras (`runner.py:2183`); it does not install
hooks at launch (`runner.py:2917`). Thus hook installation/firing for a fresh
daemon2 Shell is **not established**. Existing settings or a custom profile
may provide it, but that is not guaranteed by this dispatch path. The outbox
pump and lease renewal themselves run on daemon threads, independent of hook
firing (`d2/runtime.py:1346`, `d2/runtime.py:1474`).

## 4. Reading order

Read these eight functions first; the order follows the executable path and
then the storage guarantees.

1. **`Daemon2.once` — `d2/runtime.py:1291`.** The spine: select/claim letter,
   recover seat, assemble prompt, start pump, invoke Shell and park on exit;
   read through `d2/runtime.py:1598` before trusting the module names.
2. **`Daemon2.serve` — `d2/runtime.py:1195`.** See who holds the account self
   lease, why dispatch is serial, and how follower roles differ; compare its
   caller at `cli.py:7337` for concurrent strand processes.
3. **`Daemon2._handle_outbox` — `d2/runtime.py:466`.** The act surface: await,
   spawn, submit, stop/to, hold, halt/respawn, cut and sends. This is where
   most seat transitions and legacy-policy coupling actually live.
4. **`Daemon2._tick` — `d2/runtime.py:1059`.** See directive draining, await
   resolution, control/HUD projections and the periphery calls that run
   while the Shell blocks.
5. **`Seat.wake` — `d2/seat.py:286`.** The persisted predicate and native-vs-
   checkpoint decision; read sibling `recover` at `d2/seat.py:308` immediately
   afterward to compare crash and parked recovery.
6. **`SeatStore.change` — `d2/seat.py:166`.** The actual seat consistency
   boundary: file lock, expected generation, replace and fsync; compare
   `d2/states/seat.yaml:4` with `Seat._transition` at `d2/seat.py:221`.
7. **`LetterService.claim` — `d2/letters.py:49`.** See how a lease and letter
   fact history jointly decide who may run; compare `answer` at
   `d2/letters.py:81` for the settlement/effect ordering.
8. **`LocalLeaseAuthority.effect_once` — `d2/leases.py:161`.** See local
   generation checking, send lease, intent and receipt, and the explicit
   remote-idempotency requirement. Compare the actual callback at
   `d2/transport.py:26` before inferring distributed fencing.

## 5. Smells — claims, not fixes

These are source-backed review questions; no implementation changes are
proposed or made here.

1. **Native resume has consumers but no real daemon2 producer.** Production
   checkpoint callers write `None`; runner results already expose session
   IDs that this runtime never saves (`d2/runtime.py:925`,
   `d2/runtime.py:1571`, `runner.py:3080`). The store/API tests can exercise
   native wake without proving end-to-end capture
   (`tests/test_daemon2_seat_router.py:23`, `d2/seat.py:298`).
2. **Seat status and legacy Run status tell different stories.** The seat
   parks at turn end while ControlMirror records a zero-return Shell as done
   (`d2/runtime.py:1573`, `d2/controls.py:137`). Run objects are saved for HUD
   and cards independently of SeatStore (`d2/controls.py:29`,
   `d2/seat.py:166`); this is two persisted lifecycle vocabularies, not one
   fold from seat facts.
3. **Seat persistence is mutable snapshots, not a fold of seat facts.**
   `SeatStore.change` replaces JSON, while cut acceptance separately appends
   to the `seats` fact stream (`d2/seat.py:180`, `d2/runtime.py:900`). Recovery
   reads the snapshot, not that fact stream (`d2/seat.py:159`,
   `d2/runtime.py:1378`).
4. **Letter expiry has two stored clocks.** The claimed fact records an
   `until`, and the fold/claim uses it for expiry; runtime renewals update
   lease JSON and in-memory Claims without appending a refreshed claimed
   fact (`d2/letters.py:77`, `d2/facts.py:253`, `d2/letters.py:69`,
   `d2/runtime.py:1358`, `d2/leases.py:141`). Door projection also calls
   `state()` without `now` (`d2/doors.py:56`). These readers do not all derive
   eligibility from the same current lease record; live consequences are
   not established by this static review.
5. **The letter/ask statecharts are not runtime enforcement.** Seat loads its
   chart (`d2/seat.py:215`); letter folding implements its own if/elif machine
   (`d2/facts.py:215`), and Supervisor folds child facts without loading an
   ask chart (`d2/supervisor.py:29`). `d2/states/ask.yaml:4` describes ask delivery
   and acceptance, but runtime ask facts here are child/allowance operations
   (`d2/runtime.py:638`, `d2/runtime.py:687`), not an enforced ask lifecycle.
6. **Typed wake vocabulary exceeds dispatch's signal production.** The
   predicate matcher supports M/S/C/H/R/T/U (`d2/seat.py:44`), but parked
   dispatch constructs only handover, schedule or mail signals
   (`d2/runtime.py:1382`). `Supervisor.returned` produces a child Signal
   (`d2/supervisor.py:91`), but submit discards it and creates a protocol
   letter instead (`d2/runtime.py:712`, `d2/runtime.py:717`). R/T/U production
   into `Seat.wake` is not established; a resource predicate by itself does
   not establish automatic refill wake (`d2/runtime.py:594`).
7. **Legacy daemon helpers are mandatory, not a removable facade.** Normal
   dispatch imports it for repo/presence labeling before Shell start
   (`d2/runtime.py:1436`); every pump calls periphery and later legacy
   heartbeat/state-document helpers (`d2/runtime.py:1061`,
   `d2/runtime.py:1152`, `d2/runtime.py:1111`, `d2/runtime.py:1133`). Gate
   startup, scheduling, hold admission and run finalization also use it
   (`d2/runtime.py:1256`, `d2/runtime.py:1160`, `d2/runtime.py:574`,
   `d2/runtime.py:1582`). `runner`, `prompts`, `protocol`, HUD and git organs
   remain actual execution dependencies (`d2/runtime.py:1424`,
   `d2/runtime.py:1519`, `d2/runtime.py:1129`, `d2/placement.py:65`).
8. **Hook-dependent steering/Stop guards have no fresh installation path.**
   daemon2's invocation lacks the hook extras/settings preparation used by
   engine 1 (`d2/runtime.py:1499`, `worker/prepare.py:1437`). Polling portal
   files does not itself inject them into a Shell's next model call; that
   interface is in legacy hooks (`hooks.py:6533`, `hooks.py:6826`). Firing on
   this machine is not established by dispatch source alone.
9. **Local lease fencing is weaker than the target's remote guarantee.**
   `effect_once` explicitly requires the remote endpoint to enforce key/gen
   (`d2/leases.py:165`), while GateTransport drops those callback arguments
   before calling `deliver(event, body)` (`d2/transport.py:29`). A local
   receipt does not establish crash-safe remote exactly-once delivery. Runtime
   chooses `LocalLeaseAuthority`, not a cloud authority (`d2/runtime.py:76`).
10. **Cross-ask child discovery and mutation use different addresses.**
    `_seat_children` finds children across asks (`d2/runtime.py:1186`), but
    `stop:` records `child_stopped` under `state["ask"]`, not `child.ask`
    (`d2/runtime.py:765`); allowance grants similarly use the seat ask
    (`d2/runtime.py:801`). Supervisor folds each child's own ask stream
    (`d2/supervisor.py:31`). Cut's handoff lookup again reads only the seat
    ask (`d2/runtime.py:913`). These coordinates expose a remaining address
    mismatch even though discovery spans asks.
11. **Submit changes a live child's supervisor status away from steerable.**
    Submit records `child_returned` while the Shell can remain alive
    (`d2/runtime.py:712`); the fold calls that status `returned`
    (`d2/supervisor.py:37`), and `to:` only permits `running`
    (`d2/runtime.py:791`). The documented submit-then-await workflow is not
    evidence that post-submit steering is accepted by this code.
12. **Checkpoint timing is sparse.** Production checkpoints occur at cut or
    running turn end (`d2/runtime.py:921`, `d2/runtime.py:1567`); the live
    await handler/pump does not capture work/carry into a quiet checkpoint
    (`d2/runtime.py:601`, `d2/runtime.py:1059`). A crash can therefore recover
    an older/minimal checkpoint, not necessarily the last substantive act
    (`d2/runtime.py:1415`).

13. **Prompt/control placement metadata can disagree with execution.** For a
    successfully placed strand, the prompt is built with the host checkout as
    `execution_root` and `environment="host"` before clone allocation
    (`d2/runtime.py:1426`, `d2/runtime.py:1428`, `d2/runtime.py:1488`). The
    invocation then uses the allocated strand root (`d2/runtime.py:1499`),
    while ControlMirror still constructs a Run with `env="host"`
    (`d2/controls.py:36`). A reader must distinguish the declared placement
    from the actual cwd/git pin (`d2/placement.py:36`).

## What could not be verified

- **Live hook installation and native warm resume:** not established. Resolve
  with a fresh daemon2 dispatch on each supported Shell, recording actual
  hook firings and the persisted SeatRecord/session ID; source provides the
  interface but not the missing capture/install calls
  (`d2/runtime.py:1499`, `d2/runtime.py:1571`).
- **Remote fencing/idempotency across crashes or machines:** not established.
  Resolve against the concrete delivery endpoints plus crash-after-send
  tests; this review traced the local wrapper only (`d2/transport.py:26`,
  `d2/leases.py:165`).
- **Resource/control/child predicates waking parked seats in live service:**
  not established beyond the signal construction traced above. Resolve with
  end-to-end parked-seat wakes, not only predicate unit tests
  (`d2/runtime.py:1382`, `d2/seat.py:44`).
- **Shell descendants after follower/daemon death:** not established. Resolve
  with process-tree observation under stop, lease loss and kill-9; inspected
  code kills registered direct processes (`runner.py:173`, `cli.py:7354`).

Verification for this artifact is static: module inventory, direct imports,
call sites and source coordinates were checked at the pinned revision. No
daemon restart or end-to-end lifecycle test was performed, and no executable
code was changed. The running daemon’s delivery/await controls were used for
this report’s handoff, as recorded below.

## Delivery status

The declared branch is committed and pushed; the only code-tree diff is this
report. `git diff --check` is clean, and all 254 unique source coordinates were
validated against existing files and line bounds. No executable tests were
run for this report-only change.

The daemon refused `submit: true` with `submit requires its declared branch
and stat-able report` (2026-10-04 15:48:55Z). The dispatch's declared report is
the relative `docs/research/daemon2-review-map.md`; the report exists in this
strand's worktree and published branch, but not in the host checkout. The
handler checks `Path(report).is_file()` before consulting the allocated
worktree (`d2/runtime.py:693`, `d2/runtime.py:695`, `d2/runtime.py:697`).
Successful daemon submission is **not established**. The report and branch
are ready for parent review; this strand awaits steering without writing into
the maintainer's checkout or changing daemon-owned event metadata.
