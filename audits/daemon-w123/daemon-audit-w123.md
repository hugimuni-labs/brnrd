# Daemon audit against the target daemon (w-123)

**Conversation-owned continuity requires replacing the address and parking model, not deleting most orchestration.** No source files changed; no product changes or PR.
Source: `origin/main` at `25fc6e29bfc4d32046bc1b1a6b93e5a11d57d760` (fetched 2026-10-02); target: `design-the-target-daemon.md`, unsigned 2026-10-02 proposal, read whole before classification.
Coverage: **378/378 top-level definitions/classes**: 377 ordinary rows + `start()` replaced by **31 disjoint blocks/closures = 408 TSV rows**. `start()` is **932**, not 933 lines in this snapshot.
Method: AST `lineno..end_lineno` physical spans, including docstrings/comments within them; `start/` partitions every line 18245–19176 exactly once. **17,312 classified lines + 1,864 imports/constants/inter-definition lines = 19,176**. These are change footprints, not predicted deletions or executable LOC.
Reading: executable AST-rendered bodies and original targeted routing/hold paths; **25 long bodies remain structural skims**, explicitly marked `(skim)` in `why`. No verdict rests solely on a name. Table is a target allocation, not approval to remove current callers.
References: `callers_outside_daemon` counts matching **lines**, using whole-word symbol grep semantics in the other **117 direct `src/brr/*.py` files at the same SHA**; comments/imports/strings/homonyms count, not runtime calls. `start/` locals are zero. Packages and tests are excluded by the requested glob.

## Line totals
| Verdict | Rows | Lines |
|---|---:|---:|
| keep | 289 | 10996 |
| harness | 0 | 0 |
| collapse | 70 | 4421 |
| project | 41 | 1640 |
| delete | 8 | 255 |

| Family | keep | harness | collapse | project | delete |
|---|---:|---:|---:|---:|---:|
| mail | 2228 | 0 | 884 | 0 | 137 |
| strands | 2960 | 0 | 49 | 0 | 0 |
| holds | 371 | 0 | 2939 | 0 | 118 |
| git-project | 0 | 0 | 0 | 1640 | 0 |
| schedule-heartbeat | 623 | 0 | 292 | 0 | 0 |
| runner-quota | 2133 | 0 | 257 | 0 | 0 |
| outbox | 1420 | 0 | 0 | 0 | 0 |
| wake-prompt | 449 | 0 | 0 | 0 | 0 |
| startup | 605 | 0 | 0 | 0 | 0 |
| other | 207 | 0 | 0 | 0 | 0 |

## Largest 15 delete/collapse footprints
| Row (daemon.py line) | Lines | Verdict | Target concept |
|---|---:|---|---|
| `_fire_due_schedules` (11104) | 279 | collapse | Conversation-addressed schedules; quota pacing remains account preference |
| `_run_worker_and_finalize` (17660) | 275 | collapse | Body outcome separate from continuing conversation seat |
| `_mark_interrupted_runs` (14658) | 270 | collapse | Crash recovery into parked seat; retained owned mail |
| `_handle_resource_held_events` (17062) | 237 | collapse | Conversation router + typed wake predicates |
| `_pending_events_for_agent` (3640) | 186 | collapse | Conversation-owned mail; explicit strand dispatch edges |
| `_queue_respawn_request` (5645) | 181 | collapse | Carry-authored fresh-body handover |
| `_apply_dashboard_wake_request` (2245) | 167 | collapse | Body preference separate from project work placement |
| `_resolve_await_state` (4713) | 160 | collapse | Awaiting record with shared trigger evaluator |
| `_release_reset_holds_due` (16703) | 131 | collapse | Measured resource trigger on parked record |
| `_hold_ratio_facet` (8951) | 124 | collapse | Resource gauge; remove cost-based exit policy |
| `start/resident-dispatch` (19030) | 117 | collapse | Conversation dispatch under an account execution lease |
| `_apply_resource_hold_resume` (16526) | 115 | collapse | Exact seat wake + capability-checked resume/carry |
| `_finalize_halt` (16110) | 107 | collapse | Explicit ended transition or carry handover |
| `_emit_mirror_cards` (10790) | 103 | delete | Delete foreign-thread mirrors; each conversation owns its projection |
| `_apply_sticky_wake_profile` (2438) | 90 | collapse | Conversation-owned body preference, not correspondent-wide inheritance |
## Repo-first routing sites and adjacent continuity indexes
Coordinates below are `src/brr/`; all daemon seat selectors and their caller paths found by symbol/data-flow inspection are listed. Project-only git lookup is not a seat-routing violation.
| Coordinate | Repo/identity dependency to remove |
|---|---|
| daemon.py:1995, 2176 | `_repo_for_event` uses explicit repo, cloud run history, drawer or default before creating dispatch target. |
| daemon.py:2358, 2368 | Dashboard body claim rewrites target execution repo before seat lookup. |
| daemon.py:3693, 3723, 3771 | Live mail union is repo-filtered, allows foreign human mail, and resolves orphan manifest through repo; no seat-conversation predicate. |
| daemon.py:6867 | Orphan strand adoption requires same repo even when parent conversation matches. |
| daemon.py:11313, 11346 | Schedule creates a conversation but stamps current default repo; hold router later chooses which seat it wakes. |
| daemon.py:15761, 15769, 15823 | Arm uses home/repo key; supersedes all other resident holds in that repo without conversation comparison. |
| daemon.py:16268, 16290, 16335 | Repo manifests select latest held resident; older conversations are superseded. |
| daemon.py:16325, 16330; shuttle.py:41, 73 | Account home (or repo `.brr`) yields one seat key/file, not one per conversation. |
| daemon.py:16566, 16590; 16377, 16458 | Arriving non-internal thread becomes seat conversation; internal/accumulated letters are rekeyed into it. |
| daemon.py:16723, 16739, 16796 | Refill sweep discovers parked bodies through repo runs, then arms shared home resume claim. |
| daemon.py:17043, 17086–17098, 17179 | Strand-return resolver searches target repo; hold router groups by repo, prefers account shuttle's standing repo, compares home seat key. |
| daemon.py:18519, 19031, 19073, 19128 | One account execution slot picks oldest repo target; burst delay covers all pending conversations before selecting lead. |
| pending_resume.py:58, 82, 170; worker/prepare.py:294, 302 | One claim file per home; conversation is checked only after claim. Handover clears the shared claim. |
| worker/prepare.py:479; worker/finalize.py:140; outbox/verbs.py:740 | Preparation, completion and await mutate the same account/repo Shuttle file. |

## Holds → wake_on
`M` = mail addressed to this conversation; `S` = explicitly addressed schedule; `C` = owned-strand return; `H` = authorized carry handover; `R(pool,floor,freshness)` = measured resource predicate; `T(deadline)` = timer; `U` = explicit owner control. Sets are semantic trigger descriptors, not unqualified event names.
Every row ultimately arms at daemon.py:15729 and finalizes at :15936; standard release is :16643, native claim at :16620, retained letters restored at :16636. Dashboard end/respawn overrides are :16864/:16949; they are controls, not implicit messages.
| Current kind/reason | Arm producer | Current release paths | Proposed wake_on |
|---|---|---|---|
| operator / quota_exhausted | daemon.py:15651 (Codex usage-limit failure) | :17062 ordinary correspondent; :17110 H; dashboard | `{M,H,U}`; resource wall still gates next call |
| reset / resident_requested (reason is free text) | outbox/verbs.py:825–880; missing deadline becomes refill | daemon.py:16703 timer; :17062 M/H; dashboard | `{T(measured deadline),M,H,U}` + availability gate |
| refill / quota_starved or resident reason | daemon.py:8615, 8644, 15651; outbox/verbs.py:869 | :16703 fresh refill/alternate pool; :17202 onward M-triggered probe, force/stop/respawn; H | `{R(bound pool,refill floor,freshness),H,U(force/stop/respawn)}`; M queues/probes, never unconditional wake |
| strands / waiting_on_strands | daemon.py:9117 accepted phase handoff | :17034/:17132 owned C; :17110 H; :17121 S; M; dashboard | `{C(parent/edge),M,S,H,U}` |
| any / turn_ended | daemon.py:8435; worker/boundary.py:357 | :17062 M/S/C/H; dashboard | `{M,S,C(parent/edge),H,U}` |
| any / daemon_restarted | daemon.py:8467, 14658 boot recovery | Same router and override paths | `{M,S,C(parent/edge),H,U}` with last valid checkpoint |
| any / hold_costlier_than_boot | daemon.py:8951 opt-in ratio branch | Same router and overrides | Remove cost-driven arm; retain gauge; existing records migrate to addressed triggers |
| raise / stake_cut | daemon.py:8716, 8815 | :17486 validates owner raise; :17552 retains other mail; dashboard | **Delete held_raise**, retain stake gauge/account wall policy; no raise-specific state |
Ownership/wall rules currently live in resource_hold.py:339, 347, 360, 392, 442, 475. Await's event/file/deadline/initiative triggers are daemon.py:4713 and await_verb.evaluate; use the same evaluator with separate process lease status. Parked children retain their dispatch edge (:6485/:7359); refill without queued mail synthesizes a resident wake only, deliberately excluding strands (:16817).

## Findings where the target needs correction or a stronger contract
1. **The present seat is not simply repo-owned.** `shuttle.py:41,73` is account-wide; repo-local holds and one global resume claim surround it. Conversation routing needs conversation-keyed storage/claims as well as dispatch, or the overwrite remains. One global execution lease may remain an account policy; conversation ownership does not require simultaneous resident processes.
2. **A bare `wake_on: set` cannot encode today's walls safely.** Refill carries pool/model, threshold and freshness; strand return carries parent/edge identity; force and raise involve authority (daemon.py:17362, resource_hold.py:475, daemon.py:17486). Define serializable typed predicates and control authorization; do not flatten everything to `message`/`quota` names.
3. **Parked needs more than `{wake_on,reason,handover}`.** Native session id/provider, capability validity, queued event identities and atomic resume generation still have owners. `_native_session_id_for` (:16247) permits Claude warm resume only in host, Codex via thread id, and returns none for other bodies. Cold recovery after SIGKILL needs a previously written checkpoint: the dead process cannot author its required carry.
4. **Await is zero model spend only while one call remains blocked.** CLI :6140–6150 returns `pending` at lease/Shell caps; another model boundary follows. `_hold_ratio_facet` (:8951) meters that idle stretch. Universal zero-cost/12× claims are not established by this static audit; use measured per-Shell boot/read cost and provider-limit experiments before prescribing await near every wall.
5. **No whole definition was proven to duplicate a native Shell feature: harness = 0.** Session readers/compatibility adapters (:3346, :15584, :16247) use native resume, not reimplement it. `_invoke_with_heartbeat` (:3366) services external mail while tools block. The target's claim that most of this file compensates for a second harness overstates the evidence at function granularity; audit hooks/runner separately before pricing harness removals.
6. **The requested caller column underprices extracted code.** Including packages yields 194 other Python files: `_finalize_halt` has 0 direct-file references but 4 including packages; `_resolve_await_state` 3 versus 5. `worker/` and `outbox/` own live callers; zero in this TSV is never permission to delete. Counts remain textual, not an execution call graph.
7. **End, failure, handover and parked-strand return need explicit semantics.** Normal worker completion releases Shuttle (`worker/finalize.py:145`) while release of a hold marks the old Run done (:16643); neither proves conversation ended. Fresh dashboard respawn generates generic recovery prose (:16949), not the target's authored carry. Preserve body outcome vs letter status, isolate foreign-thread mail, and specify adoption of unfinished strands after an ended seat.

## Independently shippable rewrite order
1. Add versioned conversation seat records and one-shot session claims beside existing records; shadow-read. Check: two conversations on one repo and one conversation across two repos retain distinct records/claims after restart; no legacy dispatch change.
2. Introduce typed wake descriptors and adapter from all six legacy conditions/reasons. Check: replay arm/release truth tables with foreign child, stale gauge, missing reset, owner override and both inboxes; same wake/no-wake outcomes, no lost letters.
3. Route inbound mail to exact conversation seat before work-repo resolution; keep global execution lease if configured. Check: A's wall cannot park B's letter; B's schedule cannot rekey A; same-conversation project switch remains resumable.
4. Move branch/worktree/publish/salvage and forge observation behind project placement service. Check: identical branch/report/publish receipts for strand work, protected branch refusal and interrupted salvage; no seat lookup needs a project path.
5. Consolidate arm/release/restart/finalization on parked record and shared trigger evaluator; migrate owned mail atomically. Check: kill between claim/queue writes and restart; each letter and session claim is consumed once, parked child remains steerable/stoppable.
6. Require carry/checkpoint for fresh handover and cold fallback; use native compatible resume adapter for warm path. Check: Shell switch never receives another Shell's session id; invalid/deleted session falls back to explicit checkpoint with obligations intact.
7. Remove held_raise, ratio-exit policy and foreign mirror-card callers only after migrations; retain gauges. Check: stake wall cannot spend beyond account contract, every continuing conversation keeps its identity after body completion, and codebase-wide reference scan has no removed symbols.

## What this audit could not verify
Native warm resume validity across versions/environments, provider-limit behavior during a blocked wait, cache prices, and concurrent delivery races require deliberate live Shell experiments. No such experiment or daemon restart occurred; this wake's daemon image is stale. Skim-marked rows require full implementation review before surgery. This is complete definition coverage, not a runtime proof or a whole-repository routing audit.
Validation: 8 exact TSV columns, legal family/verdict enums, 15-word why limit including `(skim)`, unique names, exact AST coverage, disjoint 932-line start partition and reconciled totals. Source blob SHA-256: `253fb16a4c84eef9aa3253a6e1887d5b364bfb1dbee4f21622b7b0221ad64c45`.
