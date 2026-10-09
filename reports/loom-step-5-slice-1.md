# Loom step 5 slice 1: relay → ledger

Status: implemented, locally verified, and published for parent review.
Branch: `brr/loom-step-5-slice-1`, seeded from `origin/main` at `f26c7399`.
PR: [#2240 — loom step 5 slice 1: relay → ledger](https://github.com/hugimuni-labs/brnrd/pull/2240).

## Contract mapping

The spec is `plan-loom-step-5.md` §Contract → Slice 1. This PR adds the callable adapter only; it does not enable a poller or perform the live cutover.

| Slice 1 requirement | Implementation and evidence |
| --- | --- |
| `pull_once(home, client, cursor)` performs one long-poll | `channels/relay.py:pull_once` calls injected `client.pull(cursor)` once. `RelayClient` delegates to `cloud._request` with the existing `/v1/daemons/inbox`, `since`, and `wait`. |
| Dedupe by `source:relay:<event_id>` | Sources are read from the union ledger. Complete events skip on identity, including repeat polls, repeated events within a response, and cursor reset to zero. |
| Verified sender mapping | `_sender` reads `home/self/people/<name>/channels.md`, one exact `platform:user_id` per line; identity comes from authenticated origin metadata. Unknown or absent IDs remain `stranger:<platform>:<user_id>`; duplicate mappings refuse ingress. Text, display names, and usernames cannot confer person identity. |
| Blob store and per-attachment facts | `_blob` downloads by event/index through the existing cloud proxy seam, hashes actual bytes, fsyncs a temporary file, renames to `home/blobs/<sha256>`, and fsyncs the directory. Existing hashes keep their file unchanged. Each attachment gets a deterministic `blob:relay:<event_id>#<n>` fact with actual byte size and origin. |
| Source fact | Records origin, platform, chat, topic, sender, exact body text, and ordered blob hashes. MIME comes from an optional pointer `mime_type`; today's photo pointers lack it, so unknown MIME is `application/octet-stream`. |
| Letter through existing append path and labels | `_letter` uses `ledger.append`, as `_ingest` does. It appends a deterministic `letter:relay:<event_id>` with `id`, `from`, `to`, `body`, `cites`, and an ingress-computed label, causally after the source. The letter is actionable in the existing projection, renders through the port boundary, and taints a strand when shown. |
| `route_bare` precedence | Joins existing confirmed `speech` receipts by `key` to outbound letters and accepted strand leases. A reply bound to a sent letter in the same person chat takes its thread even after release; otherwise the last confirmed speaker's live lease wins; otherwise `thread:inbox`. Chat keys are `platform/chat_id`; routes use the router's existing `thread:<name>` address form. |
| Cursor after append; trust lower server cursor | `read_cursor` reads `home/loom/relay-cursor.json` (missing means zero); `_write_cursor` uses existing fsync/rename `atomic_write` after all event facts. Server cursor decreases are accepted. No cursor advances on a download/append failure. |
| Injectable thin relay client | `RelayClient(state_dir)` reads the existing cloud state/token through `cloud._load_state_from_dir`; download delegates to `cloud._download_attachment`. Tests use fakes or patched transport seams and make no network calls. |

## Validation

`pytest tests/test_loom_relay.py tests/loom/test_router_fold.py tests/loom/test_labels.py tests/loom/test_tails.py -q`

**35 passed**: 20 new adapter tests and 15 neighboring projection/label/ledger-tail tests. `git diff --check` is clean. An isolated export of `origin/main` with the new test file fails collection with `ModuleNotFoundError: brr.loom.runtime.channels`, confirming that the baseline does not provide this adapter.

Required regressions compare concrete values using the event shape in `test_cloud_gate.py` and `webhooks._enqueue_telegram_event`: three-event replay twice plus cursor reset, crash after facts before cursor write, text/from spoof remaining stranger and tainting a shown letter, reply binding beating last spoke, expired bare default falling back to inbox, and identical attachment bytes yielding one file/two exact blob facts. Extra coverage includes partial source/letter and blob appends, ambiguous people mappings, anonymous IDs, download failure/retry, cross-chat routing, router renewals/fencing, and an outgoing `from` claim failing to steal another thread's default.

## Departures, argued

1. **Two existing seams need small extensions.** `ledger.fact_filename` places loom-owned `source`/`blob` facts in `loom.jsonl` instead of demanding a strand file. `project.fold` accepts generation-free loom ingress letters citing relay sources. Without those extensions the existing ledger cannot store the required records and the router silently drops stranger letters. Existing strand source placement and generation fencing remain intact.
2. **A source records an additional `to` field.** The selected destination is an ingress intent, persisted before appending the letter. It makes recovery independent of later speech, release/renewal, changed mappings, and newly synchronized facts. Reconstructing from today's fold or source HLC alone can send an interrupted letter to the wrong thread; saving one address avoids another journal or a ledger transaction abstraction.
3. **An existing source repairs a missing deterministic letter instead of blindly skipping.** A source append and a letter append are two durable writes. Strict unconditional skipping can lose the letter forever after a crash between them. Completed sources still skip without redownloading or recomputing identity.
4. **The lease deadline is the router generation's window.** Current thread `lease` facts have no `until`; only `router` and `router.renewed` carry deadlines. A bare default therefore requires the same current thread holder/gen, the current router generation, and `now < until`. Legacy step-1 leases remain live until release. This conservative interpretation can send a bare message to inbox while a body under an old router is still alive; resolving that product choice or adding thread deadlines is outside this slice.
5. **Attachment failures abort the poll before advancing its cursor.** The cloud gate uses best-effort downloads; this adapter cannot truthfully append the contract's blob/source records without the bytes. Keeping the event replayable avoids silently losing a file. A permanently unavailable attachment can block later cursor advancement and needs an explicit disposition policy before live use.
6. **The contract's live Telegram reply loop lacks a wire fact today.** `webhooks._enqueue_telegram_event` preserves the incoming `message_id` but neither the replied-to Telegram message ID nor a loom letter ID; `ParsedMessage` also omits that target. `route_bare` implements and tests binding when given a letter ID. `pull_once` carries optional verified `reply_to.reply_to_letter` metadata, tested separately as an extension, and never guesses a binding from message text or the incoming `message_id`. Live binding cannot work on the current wire. The assertion that no relay/server change is needed must be narrowed: preservation of reply-target metadata, plus slice 2's sent-message→letter correlation, is required. This PR does not change that server path.

## Unverified and remaining edges

- Live Telegram reply binding is **not proven** and is blocked on the ingress metadata above. A test-group round trip after slice 2 must verify the real wire and message-ID correlation.
- The adapter is deliberately not wired into `loom.run`; slice 3 must establish the shared consumer lock before enabling it. No live cloud cursor or token was changed by this task.
- Full suite/CI, sending/splitting, edits/deletes, and consumer mutual exclusion are not part of this local gate; slices 2–3 own those checks. No PR merge or deployment is performed here.
- Exact event identity dedupe freezes a complete source's first attachment list. Today's cloud gate reconciles late album growth under the same ID; this slice follows the contract's skip rule and does not ingest late growth.
- File-content dedupe/durable appends were exercised via exceptions, not process termination or power-loss fault injection. No LFS backend or blob retention policy is added.
