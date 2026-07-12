# Gates — file protocol spec

A gate connects brnrd to an external channel (Telegram, Slack, GitHub,
or anything else).  Gates communicate with the daemon exclusively
through the filesystem — no shared memory, no function calls.

## File protocol

### Input: `.brr/inbox/`

Create an event file:

```
---
id: evt-<timestamp>-<rand>
source: <gate-name>
status: pending
<gate_specific_key>: <value>
created: <ISO timestamp>
---
<message body>
```

The daemon picks up `pending` events and hands them to the runner.

An event may reference downloaded image attachments (a Telegram photo,
an inline screenshot embedded in a GitHub issue/PR/comment body) via an
optional `attachments:` field — a comma-joined list of bare filenames,
resolved against a sibling directory `<inbox_dir>/<event_id>.attachments/`:

```
---
id: evt-<timestamp>-<rand>
source: telegram
status: pending
attachments: photo.jpg
created: <ISO timestamp>
---
<caption, or empty>
```

See `protocol.create_event`'s `attachment_files` param (writes this
shape) and `protocol.event_attachment_paths` (resolves it back to real
paths) — both gates that support attachments (`telegram.py`,
`gates/github/attachments.py`) converge on this one mechanism so the
resident's `Read` tool opens an inbound image the same way regardless of
which channel it arrived on. `protocol.cleanup` removes the directory
alongside the event and response files.

### Output: `.brr/responses/`

The daemon captures the runner's final stdout and writes a response file:

```
<response body>
```

The daemon sets the event status to `done` as soon as the response is
validated.  The gate reads the response and delivers it through its
channel, then cleans up both files.  Daemon housekeeping such as kb
maintenance, environment finalization, and branch push can continue
after the response is already deliverable.

## Writing a gate

A gate is anything that:
1. Creates event files in `.brr/inbox/`
2. Reads response files from `.brr/responses/`
3. Cleans up after delivery

It can be a Python module, a bash script, a Go binary — anything.

### Minimal bash example

```bash
#!/bin/bash
INBOX=".brr/inbox"
RESPONSES=".brr/responses"
ID="evt-$(date +%s)-$(head -c 4 /dev/urandom | xxd -p | head -c 4)"

cat > "$INBOX/$ID.md" << EOF
---
id: $ID
source: my-gate
status: pending
created: $(date -u +%Y-%m-%dT%H:%M:%SZ)
---
$1
EOF

echo "Event $ID created. Waiting for response..."
while [ ! -f "$RESPONSES/$ID.md" ]; do sleep 2; done
tail -n +4 "$RESPONSES/$ID.md"
rm "$INBOX/$ID.md" "$RESPONSES/$ID.md"
```

### Python gate module

If you want your gate to run as a daemon thread, implement:

```python
def is_configured(brr_dir: Path) -> bool: ...
def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None: ...
```

For CLI configuration, implement `setup` for the normal one-step flow:

```python
def setup(brr_dir: Path) -> None: ...
```

`auth` and `bind` remain available for credentials and channel, chat, or watch
selection when a gate wants to expose them separately.  If `setup` is missing,
the CLI falls back to calling `auth` and then `bind`.

The daemon imports configured gates and calls `run_loop` in a thread.  See
`telegram.py` or `slack.py` for examples.

### Folder-watcher gates

If you want a "drop a markdown file into a folder and brnrd picks it up"
flow — for example over Obsidian git-sync, the GitHub web editor, or a
script that drops files into a watched dir — write it as a bash script
gate using the protocol above. The minimal example earlier in this file
covers everything needed: the file watcher and the diff/commit logic
should reflect your actual workflow rather than being hidden behind a
generic built-in.
