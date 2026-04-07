# Gates — file protocol spec

A gate connects brr to an external channel (Telegram, Slack, Git, or
anything else).  Gates communicate with the daemon exclusively through
the filesystem — no shared memory, no function calls.

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

### Output: `.brr/responses/`

The runner writes a response file:

```
---
event_id: evt-<timestamp>-<rand>
---
<response body>
```

The daemon sets the event status to `done`.  The gate reads the
response and delivers it through its channel, then cleans up both files.

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

The daemon will import your module and call `run_loop` in a thread.
See `telegram.py`, `slack.py`, or `git_gate.py` for examples.
