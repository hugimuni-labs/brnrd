# Slice A: connector folders

## Bulk test

**New interface: 189 physical lines. Moved/adapted: 176. Net runtime: +22 lines.**

Counts include comments and blank lines within each named function/class. Module imports and spacing are counted separately where a module is split. The nested `speak.run` function is counted in the interface and subtracted from the speech wrapper. Config and caller edits are outside these component subtotals.

### New interface

| Component | Lines |
| --- | ---: |
| `adapters.resolve` | 9 |
| `connector.carries` | 14 |
| `connector.Inputs` | 37 |
| `connector.environment` | 10 |
| `connector.py` imports and inter-component spacing | 17 |
| `source.drain` | 35 |
| `source._refuse` | 14 |
| `source.py` imports and inter-component spacing | 21 |
| `speak.run` — the `out` call and exit/receipt handling | 15 |
| `home._CHANNEL` / `home.channel_parts` | 1 / 16 |
| **New interface total** | **189** |
| Contract ceiling | 150 |

### Moved/adapted

| Component | Lines |
| --- | ---: |
| Routing and person lookup — `channel.py` | 63 |
| Source step — `source._letter` / `source.ingest` | 11 / 49 |
| At-most-once wrapper — `speak.py` excluding nested `run` | 53 |
| **Moved/adapted total** | **176** |

### Transport and runtime totals

| Measure | Lines | Contract |
| --- | ---: | ---: |
| `relay/in` | 136 | ~250 |
| `relay/out` | 48 | ~80 |
| Shared `relay/common.py` | 50 | — |
| All runtime files under `src/brr/loom/`, base / current | 9,569 / 9,591 | — |
| **Net runtime** | **+22** | <0 |

Runtime totals count newline bytes against `origin/main` at `8c753369`. Component spans are measured from the Python AST; the shared client's 50 lines are counted once.

## Departures

| Contract or oracle case | Result and argument |
| --- | --- |
| Home `in` executable and `<home>/loom/connectors/<name>/in/` drop directory | These cannot occupy the same pathname. Drops are `<home>/loom/connectors/<name>/state/in/` for every connector. `BRNRD_STATE` is its parent; home overrides and bundled folders follow the same rule. |
| Home folder overrides bundled folder | A home `carries` file identifies an override. Automatically created runtime/state directories must not shadow bundled code. Body lookup retains its previous directory/run validation. |
| `relay_state` enabled polling | Only `connectors = ["relay"]` enables relay. `relay_state` remains in the declared config as the relay folder's credential/account path, read through `BRNRD_CONFIG`; keeping credentials does not implicitly enable a transport. Default connectors are empty. Tests explicitly enable their fake/relay folders. |
| At-most-once per part | One persistent effect per letter. A failure after part 1 stops the connector, returns maybe-sent, records no successful whole-letter receipt and never retries that key. Successful output records one `speech.part` per returned id. Legacy single/first-part send-store evidence also blocks a new attempt. Exit 3 persists not-sent plus stderr, and a new letter key may retry. Reversing fork 1 remains confined to receipt streaming and the speech wrapper. |
| Event ids versus message ids | Relay ids are `<platform>:<percent-encoded-chat>:<percent-encoded-message-id>`. GitHub repository/issue chats may contain `/`; empty, `.` and `..` path components remain forbidden. Incoming `id`, incoming `re` and outgoing returned ids use this same construction. An incoming message without a transport message id uses its stable event id. `state/messages.json` maps canonical incoming ids back to response event ids. Message ids from different chats/platforms cannot collide. Tests now use distinct transport ids instead of giving unrelated fixture events the same message id. |
| Failed ingest | Input processing has its own exception boundary before body ticks. A valid drop whose ingest fails is retried twice, then quarantined on its third failure with its attachments, persisted failure count and one notice. |
| Unsupported fresh sends | WhatsApp and GitHub return exit 3 before any request when any part would need `/messages`, including multipart event answers. The GitHub reason directs the thread to `gh`. |
| Cursor commit after ledger append | The durable boundary is now complete attachment files plus the renamed letter file, before the relay cursor advances. Ledger ingestion happens in the loom; a cursor-write crash leaves the original drop to ingest. Source/partial-blob/letter-append crashes still recover from the original durable source. Replays do not redownload emitted attachments. Drop processing follows file arrival time, with a deterministic name tie-break. |
| Relay-shaped source data | Source chat ids are strings, raw reply ids become canonical `re`, and a source stores its channel and derived label. Transport `topic` is not a separate generic source column. The relay event id remains in connector state for `/responses` to use its original server-side reply metadata. The letter still cites the source, carries the original body and routes from the original source snapshot on recovery. |
| Only `to` and `re` added to outgoing letter | The existing loom-authored speaker/thread prefix is retained to preserve the old user-visible origin behavior. Sender/clean/refs/edit fields survive the fact-to-letter seam; relay/event translation and splitting are entirely in the folder. Removing the prefix is a small independent policy change for review. |
| Fresh relay send previously omitted chat | `out` now supplies both `platform` and `chat_id`. The current server still selects the last chat and does not honor `chat_id`; Slice B must land before live cutover. No server code is changed here. |
| Holding integration timing | This case passes on the base snapshot but consistently observes only one expiry after its fixed 2.2-second pause here. Its assertions still require two actual hold expiries and one live body; setup now waits for those observable expiries (bounded at six seconds) instead of assuming hook/collector launch time. No relay-oracle expectation was relaxed. |
| Report: “the path above” | The body omitted the pathname, but the runtime supplies it as `BRR_REPORT_PATH` (`.brr/reports/loom-connector-folders.md` in the host checkout). The completed report is written there; `reports/loom-connector-folders.md` is its versioned branch copy. The first submit refused the missing declared file; the corrected handoff uses the runtime path. |

## Files changed

- `runtime/adapters.py`, `config.py`, `home.py`, `service.py`: shared lookup, declared connector arrays, enabled-channel checking and list rendering. The existing atomic writer now accepts bytes and can sync the parent directory for blobs; blob writes reuse it.
- `runtime/connector.py`, `source.py`, `channel.py`: declarations, duplicate-carrier refusal, child environment/supervision, generic drop ingestion with a three-failure quarantine, sender lookup and conversation routing.
- `runtime/speak.py`, `router.py`, `loom.py`, `project.py`, `port.py`: one executable effect, exit-status handling, generic source reply ids, input supervision and edit preservation. The effect registry, limits table, loom splitter and relay arming/disarming functions are gone.
- `runtime/connectors/relay/{in,out,common.py,carries}`, `runtime/connectors/fake/{out,carries,in/.keep}`: transport folders. Relay sends disable automatic HTTP retries. Connector subprocesses use the loom interpreter's PATH and the running tree's PYTHONPATH.
- `runtime/channels/`: deleted the relay implementation and its now-empty package.
- `pyproject.toml`: include connector folder assets in the distribution, explicitly including the passive fake input marker.
- The three relay oracle files retain every original case, with the departures above made explicit. `tests/connector_support.py` replaces wire I/O while retaining production drop and outbound callers. Tests under `tests/loom/` use declared folders instead of the removed registry.
- `tests/test_loom_connector_folders.py`: real executable output, collision refusal, config validation, passive input, quarantine, exit 3, uncertainty, legacy intent and a localhost HTTP photo → answer → reply round trip through both actual relay executables. `tests/loom/test_connector_inputs.py` proves poisoned drops do not stall body ticks; the speech oracle covers both unsupported fresh-send platforms and multipart answers.

Verification: **72 passed in 75.21 seconds** for the review fixes. Command: `pytest -q tests/test_loom_relay.py tests/test_loom_speak_relay.py tests/test_loom_relay_cutover.py tests/test_loom_connector_folders.py tests/loom/test_connector_inputs.py tests/loom/test_lifecycle.py tests/loom/test_letter_owners_live.py --disable-warnings --maxfail=2`.

The prior broad targeted run completed with **418 passed, 1 skipped, 4 xfailed** (903.82 seconds); the later transport recheck completed with **56 passed** (18.09 seconds). Changed Python source/test files and executable scripts parse with Python 3.10 grammar.

## Open questions

1. The plan's four maintainer forks remain open; implementation follows the recommendations. Relay does not advertise `edit`, and no card/typing/menu server API is invented.
2. Actual Telegram/WhatsApp phone delivery is unverified. The real-process localhost relay test covers attachment bytes, response-event translation, returned ids and reply-to-thread binding; a phone drive and Slice B are still required for live cutover. Current `/responses` does not return a message id, so a reply to its first part cannot bind via a receipt until Slice B returns that id.
3. Wheel construction is unverified here: the host environment has neither `build` nor `setuptools.build_meta`; the isolated snapshot attempts stopped at those missing build tools. CI must verify the distribution assets. No shared environment was modified.
4. Old relay message-id receipts use the former namespace. Their reply bindings are not migrated; a pre-cutover drive against historical ledger data should decide whether a migration is needed. Legacy send intent is conservatively fenced to avoid duplicate sends.
