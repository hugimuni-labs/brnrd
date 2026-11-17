# Local tool journal (record-only, opt-in)

The journal records the existing Claude/Grok jack tool hooks. **Codex calls are
not captured.** This is partial local evidence, never proof that every act was
captured or authorized. Capture defaults off; this change enables it nowhere.

A home may opt in with `audit = "on"` in `loom/config.toml`, then restart its
loom. The loom reads the setting at start: when on, it starts the writer, which
writes `loom/audit-endpoint.json`; when off, it removes any endpoint a dead
loom left. A hook reads only the endpoint, so switching off takes effect at the
next loom start, and capture off costs a hook one failed open (measured below). No grants, citations, wake review, witness, push,
display, trust number, payload storage, encryption or Codex interception ship
here. Existing guards retain their own behavior.

## Storage and acknowledgements

`<home>/audit/<install-id>/<epoch>/entries.jsonl` sits outside rooms and their
reaper. A single persistent writer thread owns the installation lock. Concurrent
rooms send digest-only events over authenticated loopback TCP; its random token
and ephemeral port live in `loom/audit-endpoint.json` with mode 0600. This is
coordination, not a boundary against a body with home access.

Each writer lifetime opens a new epoch, ordered by an ordinal rather than wall
clock, linking the preceding epoch's committed hash. `epochs.json` commits their
ordered names. `head.json` commits the epoch's sequence, hash and byte count.
Every append writes the line, fsyncs the journal, writes/fsyncs a temporary head,
replaces the head and fsyncs its directory before acknowledging. Epoch creation
also syncs the directory.

Start-up never reads the history. It reads the manifest, the epoch directories,
the predecessor's `head.json` and the one row that head names (at most 64 KiB
read from the end), and links the new epoch to that committed hash. The full
walk is the verb's. A new epoch opens whatever the predecessor left: when it did
not end clean (a crash between the journal fsync and the head replacement, a
partial line, a failed fsync, a missing or unreadable head, an epoch created but
never added to the manifest, an unreadable manifest), the new epoch's first
entry carries `found`: the predecessor's name, its committed head as read, its
actual file size and whether the manifest listed it. Old bytes are never
truncated, rewritten or repaired; the unacknowledged tail stays where it fell.
Nothing past a committed head was ever acknowledged, so a tail is never an
event a hook was told was recorded.

The writer serializes append operations. A hook waits at most a 5 ms socket
budget for acknowledgement and never retries. Config parsing and hashing are
additional CPU work; this is not a hard real-time latency guarantee. An ack lost
after persistence may still leave the event in the journal. Capture errors do
not refuse tools. A persistent `loom/audit-warning` suppresses repeated warnings
within one outage; the first failure writes it and prints to stderr, and every
writer start removes it, so the next outage warns again. If storage cannot even
create that marker, a single warning per outage cannot be guaranteed.

The writer drops a request it cannot read (garbage, a half-sent or timed-out
request, valid JSON of the wrong shape, an event it cannot encode or whose id
or digests are malformed) or cannot authenticate (constant-time token compare),
and goes on serving. Only a failed journal append stops it: it never appends
past a write it could not complete, and every later hook sees no
acknowledgement and warns.

The loom reports the same two states through its health guard, as the `audit`
step: when a home asked for capture and the writer did not start, or a failed
append stopped it, the step fails, a `loom.degraded` fact is written, and past
the guard's notice delay the seat gets a degraded notice. The next good start's
pass writes `loom.recovered`. Capture off, the step always passes. This adds no
display and no new mechanism; the hook's own warning stays as it is. A missing
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

## Doors and sensors

A door is where an effect leaves what the room can rebuild. Three doors are
brnrd's own, and each has a sensor:

| Door | Sensor sits | Recorded act |
|---|---|---|
| `send` | `write_send`, for a letter to a `channel:` (not a `--note`) | `letter`, target the channel |
| `publish` | `publish()`, after the push it records | `push`, target `<repo> <branch> <commit>` |
| `forge` | the room's `gh` shim, after its gates | `pr merge`, `pr close`, `pr comment`, `issue close`, `issue comment`, `release create` |

The sentence is the body's `BRNRD_SAY` value at the passage: what it is about
to do, why, and whose words allow it. One environment value serves all three
doors, including `gh`, which would reject a foreign flag. A sensor records the
passage and the sentence (or `null`) as a `door` entry in the same journal. It
refuses nothing and judges nothing.

`doors = "on"` in `loom/config.toml` turns the sensors on. It needs
`audit = "on"`: the loom writes the switch into `loom/audit-endpoint.json`, so a
door reads one file and never the config. With sensors on, a passage with no
sentence sends the person one loom notice on their direct channel. One notice
covers each 15-minute window, so a burst is one note.

While the sensors are on, the wake's loom briefing carries one line naming
`BRNRD_SAY`; with them off it adds nothing, so a body is never counted for a
sentence it was never asked for.

The forge passage is recorded before `gh` runs (the shim `exec`s it), so a
failed `gh` call still counts as a passage; `send` and `publish` record after
the act. `pr create`, `pr review` and `issue create` leave through the shim
unsensed.

`brnrd runtime audit doors [--since T] [--until T]` prints, per door, the
passages seen and the passages declared, read from committed bytes only.

The forge sensor is as wide as the shim. The shim exists only where the home
sets `forge_token`; elsewhere `gh` is the machine's own and there is no forge
door. `gh api` calls and a shell using the exported `GH_TOKEN` directly pass
no sensor.

## Verification and limits

`brnrd runtime audit verify --home PATH` returns JSON with entry/epoch counts,
the checked head hash and `unclean`, or exits 1 with every fault it found. It
checks canonical bytes, continuity, entry hashes, event-id uniqueness, digest
shape, committed size/head, installation/epoch binding and predecessor links. An
epoch its successor recorded as `found` is reported in `unclean` with its
committed and actual byte counts; its committed bytes are still checked, and so
is every epoch after it. If such an epoch changed after it was found (size or
head no longer what the successor recorded), or is shorter than its committed
head, that is a fault, not an unclean stop. A mismatch in the newest epoch with
no successor yet (a crash not followed by a loom start, or tampering) is a
fault. A fault in one epoch does not stop the walk. Offline verification takes a
shared lock. With a live writer it requests a serialized checkpoint, then checks
that committed prefix outside the writer, so a long scan does not hold up tool
appends. Newer events may exist by the time the command returns; the returned
hash identifies the prefix checked. A dead writer's stale endpoint is an error.

Tests change a byte, remove an interior or final entry, reorder entries, duplicate
an event id, corrupt a digest, remove an epoch and interrupt an append. They
cut the power in each crash window (after the row's fsync before its head; after
entry 0 before the manifest; after the epoch directory before any head) and
read that capture continues, the old bytes are unchanged, the new epoch says
what it found and the verb reports it unclean while checking what follows; a
start-up with `_rows`/`verify` replaced by a failure proves it reads no history.
Garbage, half-sent, wrong-shape, wrong-token and unencodable requests each leave
the next real event captured. Tests also exercise the jack's existing
pre/refusal/post path, off-by-default capture, a dead endpoint removed at start,
the warning re-armed by a writer start, writer exclusion, failed writes, and
verification while capture continues.

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

- After the review fixes (same machine, 2026-11-16): 500 calls, 1,000 entries,
  mean 1.49 ms/call, p95 1.87 ms, p99 2.47 ms, max 8.98 ms; no warning.
- Start-up with a 1 GiB journal (1,239,739 entries, synthetic, valid): 1.7 ms
  per loom start (5.5 ms first, cold). The full walk the earlier version ran at
  every start takes 43.7 s on the same journal; it is now only the verb's.
- A hook with capture off: 16 µs mean (one failed open of the endpoint), where
  the earlier home-and-config read cost 49 µs. In-process; the hook process's
  own start-up is not counted.

Reproduce with the checkout's `src` explicitly on the import path:

```
PYTHONPATH=src python scripts/bench_audit.py --scratch <room>/tmp
PYTHONPATH=src python scripts/bench_audit.py --scratch <room>/tmp --trace <body>/port/tools.jsonl
PYTHONPATH=src python scripts/bench_audit.py --scratch <room>/tmp --startup-bytes 1073741824
PYTHONPATH=src python scripts/bench_audit.py --scratch <room>/tmp --off --calls 2000
```

The script prints the loaded module path, sample counts, latency percentiles,
actual journal bytes and warning state. The optional trace needs at least an
hour of source events; no source content is copied into this repository.
