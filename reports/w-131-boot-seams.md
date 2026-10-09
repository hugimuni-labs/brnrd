# w-131 — three seams in a resident's wake bundle

Status: the three seams are fixed on `brr/boot-seams-w131`, targeted tests green against this worktree (`brr.__file__` → this tree's `src/brr/__init__.py`).

Observed on run-261007-1126-9926 after the restart, and again on this strand's own wake: the bundle listed nine letters while `inbox.json` had `events: []` and `pending_event_count: 0`.

## 1. Inbox — other pending events

Cause. `prompts.py:6058` only renders the `pending_events` it is given. Daemon2's only caller (`runtime.py`, `once`) passed `events`, the list from `door.pending()` captured at the top of the dispatch (`runtime.py` ~1470). That list is every dispatchable letter in every queue. `inbox.json` is written later from `_visible` (`runtime.py:1256`, `portals.write_live_inbox`). `_visible` starts at `door.pending()`, which already projects letter facts (`doors.py:53` `_project`, `doors.py:70` `pending`): `answered` → `done`, `retired` → `noted`/`done`, and those rows drop out. It then keeps only letters this seat can see.

The live files do not match "the dead seat answered them." The schedule letters in that wake have no letter facts and `status: pending`. `evt-…-v2l4` is `claimed`, not `answered`. They were absent from `inbox.json` because a seat does not see another thread's collaborator mail (and a strand sees only its own `dispatch_message`). The bundle also listed the waking event itself, which `inbox.json` excludes.

Change. The bundle now receives `_visible(...)` (`runtime.py:1624`), the same list the inbox writer uses. One projection, both surfaces.

Test. `tests/test_daemon2_runtime.py::test_bundle_inbox_matches_visible_projection_not_raw_files`. Files stay `pending`. Facts say `answered` and `retired` for two siblings. A same-thread letter still pending is listed. The other-thread collaborator is not. The rendered ids equal `inbox.json`.

## 2. A waking letter that was already answered

Cause. On checkpoint resume the task text was only `checkpoint` / `carry` / `obligations` / `queued_letters` (`runtime.py:1603`, the old "Recovery checkpoint (previous Shell stopped)" dump). `seat.recover` and `seat.wake` replace the seat's `run_id` with the new dispatch (`seat.py:318`, `seat.py:302`), so the id of the seat that just died was gone before the prompt was built. Delivered replies are message-store rows (`status: delivered`, `delivered_at`, `target_thread`) under that run's node, not `.asks.jsonl` (those rows are what the seat claimed, not what a gate acknowledged).

Change. The run id is copied off the seat record before `recover` / `wake` overwrites it (`runtime.py:1579`, `runtime.py:1590`). The checkpoint JSON gains three facts: `previous_run_id`, `replies_delivered`, `last_reply_at`. Count is `status: delivered` rows whose `target_thread` is this conversation. `last_reply_at` is the latest `delivered_at` (else `created_at`). No account home, or no such row, is `0` and `null`. `collected` is not counted: that receipt is the dispatch edge, not a reply delivered on the thread.

Test. `tests/test_daemon2_runtime.py::test_recovery_checkpoint_names_previous_run_and_delivered_replies`. A seat left `running` as `run-dead`, two delivered rows on the thread, one delivered row on another thread, one pending row. The checkpoint says `run-dead`, `2`, and the later on-thread `delivered_at`.

## 3. Your last run

Cause. `_prior_run_node` skips a node `_node_is_strand` can prove (`prompts.py:7136`). That proof was `parent_run_id` only. `_persist_run_state_doc` writes `parent_run_id` from `task.meta["spawn_parent_run_id"]` (`daemon.py:13714`). Daemon2's spawn letter carries `parent_run_id`, which is not that meta key, so the node often has no parent line. It does have `source: spawn`. `run-261007-1108-uanc` is that shape: `source: spawn`, `status: error`, `stage: finished`, `runner_name: vibe-glm-5-3`, no `parent_run_id`, and the same `conversation_key` as the seat that was shown it. A conversation filter would not have dropped it. The frame line already named the outcome and the runner; the heading still said the seat wrote it.

Change. `_node_is_strand` is also true when `source` is `spawn` (`prompts.py:7138`). That is the fact `continuity._dispatched_as_strand` already skips, and it is on the nodes already on disk. A resident respawn keeps its gate (`cloud`, `telegram`, `schedule`) and stays eligible. The smaller change is this guard, not a new selector and not a retitle of the block.

Test. `tests/test_prompts.py::test_prior_run_block_skips_a_failed_foreign_body_strand_without_parent_id`. Two nodes. The newer one is `source: spawn`, no `parent_run_id`, `status: error`, runner `vibe-glm-5-3`. The block names the older resident node.

## Open forks

A resident run on a different shell — `source` not `spawn`, a respawn of this seat — still surfaces as "Your last run". The frame already names that runner. Whether to demote it when `runner_shell` differs from the body now waking is open. I did not guess: the node that misled the seat was a strand, and conversation-scoping would have kept it.
