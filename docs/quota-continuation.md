# Continue work after a subscription runs out

Automatic continuation is off unless explicitly allowed in `loom/preferences.toml`.
When a body fails on quota, its thread remains parked until the same subscription
refills. A person can still say `wake <shell>/<core>` in their direct chat to
choose another body manually.

To allow automatic continuation, list permitted target bodies in priority order:

```toml
quota.default = ["codex"]
quota.inbox = []
quota.pitch-deck = []
quota.batch = ["codex", "claude/sonnet"]
```

A thread's `quota.<thread>` overrides `quota.inbox` for the inbox seat, then
`quota.default`. An empty list disables moves for that role. Missing permissions
disable them everywhere. Existing `model.*`, `bodies.order`, and `spend.*`
preferences do not grant permission to move parked work. For Claude-only lead
work, leave quota permission unset or use an empty list for those threads.

The target must be a different shell, enabled in `bodies.order`, installed on
this machine, and have a fresh successful quota reading with every window above
`hold_floor_pct`. Unknown, stale, failed, or exhausted targets leave the thread
parked. A source that has refilled retries its original shell.

Continuation starts a fresh body from the landed thread README and owed letters.
It does not convert a live model session or change the thread's model preference.
A subsequent ordinary birth follows its usual preference again. Paid relay
credits are not a continuation target.

The ledger records one `body.moved` fact per source generation, naming the
thread, source strand/generation, `from_shell`/`from_core`, `to_shell`/`to_core`,
exhausted `window`, `left_pct`, reset estimate `until`, and source `park` fact id.
That decision survives a loom restart. A manual wake takes precedence. A passive
notice in the thread says which subscription ran out and where the fresh body
will continue; the next lease and `body.started` facts confirm actual startup.
