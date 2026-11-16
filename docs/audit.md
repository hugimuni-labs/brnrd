# Local tool journal (record-only, opt-in)

The journal records the existing Claude/Grok jack tool hooks. **Codex calls are
not captured.** This is partial local evidence, never proof that every act was
captured or authorized. Capture defaults off; this change enables it nowhere.

A home may opt in with `audit = "on"` in `loom/config.toml`, then restart its
loom. Switching it off stops hook submissions immediately; turning it on needs
a writer started by the loom. No grants, citations, wake review, witness, push,
display, trust number, payload storage, encryption or Codex interception ship
here. Existing guards retain their own behavior.

## Storage and acknowledgements

`<home>/audit/<install-id>/<epoch>/entries.jsonl` sits outside rooms and their
reaper. A single persistent writer thread owns the installation lock. Concurrent
rooms send digest-only events over authenticated loopback TCP; its random token
and ephemeral port live in `loom/audit-endpoint.json` with mode 0600. This is
coordination, not a boundary against a body with home access.

Each writer lifetime opens a new epoch, ordered by an ordinal rather than wall
clock, linking the preceding epoch's final hash. `epochs.json` commits their
ordered names. `head.json` commits the epoch's sequence, hash and byte count.
Every append writes the line, fsyncs the journal, writes/fsyncs a temporary head,
replaces the head and fsyncs its directory before acknowledging. Epoch creation
also syncs the directory. A partial write, failed fsync or crash between these
steps leaves a detectable discrepancy; no automatic repair, truncation, reset or
continuation past a corrupt tail occurs. Recovery requires a later design.

The writer serializes append operations. A hook waits at most a 5 ms socket
budget for acknowledgement and never retries. Config parsing and hashing are
additional CPU work; this is not a hard real-time latency guarantee. An ack lost
after persistence may still leave the event in the journal. Capture errors do
not refuse tools. A persistent `loom/audit-warning` suppresses repeated warnings;
the first failure writes it and prints to stderr. If storage cannot even create
that marker, a once-per-installation warning cannot be guaranteed. A missing
ack means coverage is uncertain. No automatic gap count is claimed.

The pre hook records attempted dispatch, including calls later refused by an
existing guard. Refusal is a separate event because those calls receive no post
hook. PostToolUse and PostToolUseFailure record reported completion/failure.
Missing post events stay open evidence; an exit status or actual external effect
is not inferred from a digest. Delegate identity is preserved. Room/generation,
session/call ids, thread, tool, cwd, engine and mode identify each observation.
Each event has its own random id; a call id can correctly occur in several phases.

## Byte protocol v1

Each physical record is one canonical JSON object followed by ASCII LF. Canonical
JSON here means Python `json.dumps(sort_keys=True, ensure_ascii=True,
separators=(",", ":"), allow_nan=False)`, encoded as ASCII, with no whitespace
or Unicode normalization. Nested object keys are strings; JSON payload number
formatting follows that Python JSON encoder (this is not RFC 8785). The golden
test pins escaping, key order, null/boolean/integer encoding and both hash domains.
No cross-language encoder may assume a different number representation.

The entry body contains `install`, `epoch`, zero-based `seq`, `prev` and `kind`.
An epoch starts at seq 0 with a 64-zero `prev` and `previous_epoch` equal to the
previous epoch's final hash (64 zeroes for the first). Later entries contain an
`event`. The `hash` is SHA-256 of ASCII `brnrd.audit.entry.v1` + NUL + the
canonical body **without** its `hash` member. Store that hex hash in the record;
the next body's `prev` must equal it. The entry's domain commits the v1 format.

`input_digest`, `output_digest` and `source_digest` are SHA-256 of ASCII
`brnrd.audit.payload.v1` + NUL + canonical JSON of the original input, response,
and entire hook payload respectively. An absent input/response hashes JSON null.
No raw arguments, command source, results, errors or transcript contents are
stored. The verifier checks hash continuity and digest syntax; without payloads
it cannot recompute payload digests or recover what the tool did. Tool names and
cwd are metadata retained in cleartext and may themselves contain private names.

## Verification and limits

`brnrd runtime audit verify --home PATH` returns JSON with entry/epoch counts and
the checked head hash, or exits 1 with an error. It checks canonical bytes,
continuity, entry hashes, event-id uniqueness, digest shape, committed size/head,
installation/epoch binding and predecessor links. Offline verification takes a
shared lock. With a live writer it requests a serialized checkpoint, then checks
that committed prefix outside the writer, so a long scan does not hold up tool
appends. Newer events may exist by the time the command returns; the returned
hash identifies the prefix checked. A dead writer's stale endpoint is an error.

Tests change a byte, remove an interior or final entry, reorder entries, duplicate
an event id, corrupt a digest, remove an epoch and interrupt an append. Tests also
exercise the jack's existing pre/refusal/post path, off-by-default capture,
writer exclusion, failed writes, and verification while capture continues.

The resident can change this installation's logger, hooks and verifier, so it can
suppress future capture or rewrite an unwitnessed local history. This record
helps detect corruption and inspect captured acts, but cannot establish complete
capture or resist that resident without independently retained checkpoints.

## Measurement

On the development machine, Python 3.13, using a temporary home under the room's
scratch directory (the live home stays off):

- Initial direct-writer measurement: 100 calls, two durable entries each;
  mean 1.84 ms/call, p95 2.22 ms, max 6.15 ms.
- Final full `audit.note` client measurement: 500 calls, 1,000 persisted entries;
  mean 2.42 ms/call, p95 3.19 ms, p99 3.84 ms, max 7.94 ms. No missing entries
  or capture warnings. This measures added audit work, not the rest of the jack
  or runner process startup. A previous run measured 1.58 ms mean / 1.75 ms p95;
  filesystem/scheduling variability matters. No performance optimization applied.
- Replay of one working body's final recorded wall-clock hour: 91 starts and
  90 completions, 181 captured events, **146,817 journal bytes/hour** (143.4 KiB).
  Source window ends at Unix 1794762654.768. This replays the display trace's
  metadata into digest envelopes; it does not enable capture on that body or
  reconstruct original tool arguments. Rate is workload-specific, not a capacity
  guarantee. Head/manifest/endpoint files add a small fixed overhead.

Reproduce with the checkout's `src` explicitly on the import path:

```
PYTHONPATH=src python scripts/bench_audit.py --scratch <room>/tmp
PYTHONPATH=src python scripts/bench_audit.py --scratch <room>/tmp --trace <body>/port/tools.jsonl
```

The script prints the loaded module path, sample counts, latency percentiles,
actual journal bytes and warning state. The optional trace needs at least an
hour of source events; no source content is copied into this repository.
