# Continue work after a subscription runs out

Automatic continuation on another subscription is off unless explicitly allowed
in `loom/preferences.toml`. A failed core is held independently of its shell gauge.
If that shell still has fuel, the loom tries the next lower ranked bundled core
(for Claude: fable → opus → sonnet → haiku), skipping cores already held dry.
Unknown custom cores have no inferred order and remain parked. Each change names
the source, destination, setting that chose it, and the wake word to go back.
With no remaining core or permitted subscription, the thread stays parked. A
person can still say `wake <shell>/<core>` in their direct chat to choose another
body manually. A bare shell uses its recorded model, or its harness default.

To allow automatic continuation, list permitted target bodies in priority order:

```toml
quota.default = ["codex"]
quota.inbox = []
quota.pitch-deck = []
quota.batch = ["codex", "claude/sonnet"]
```

The inbox seat follows only its own `quota.<thread>` or `quota.inbox` key.
A seat with a pinned `body:` follows only its own `quota.<thread>` key.
Neither inherits `quota.default`; other threads use their own key, then the
default. An empty list disables moves for that role. Missing permissions
disable them everywhere. Existing `model.*`, `bodies.order`, and `spend.*`
preferences do not grant permission to move parked work. For Claude-only lead
work, leave quota permission unset or use an empty list for those threads.

The target must be a different shell, enabled in `bodies.order`, installed on
this machine, and have a fresh successful quota reading with every window above
`hold_floor_pct`. Unknown, stale, failed, or exhausted targets leave the thread
parked. A quota permission target equal to the source shell is skipped; same-shell core
recovery uses the bundled ranking independently.
A source that has refilled retries its original shell.

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


Exact `wake`, `wake <shell>[/<core>]`, and `reincarnate` commands in the enrolled
person's direct chat are consumed by the loom in every body state. A malformed
body argument gets the valid choices; ordinary sentences reach the seat. A
composer model choice continues to accompany its work and is applied when idle,
parked, or held. A one-off command never writes a seat pin.

Every lease records `body_source`: wake word, seat pin, the exact `model.*`
preference, config body, runtime default, or quota recovery. Failed and switched
notices expose that source. Quota failure evidence in `attempt.ended` records
shell, core and reset estimate; it survives restart. The provider's stated local
clock reset is used when present; an unknown reset holds the core for 15 minutes
before probing again. Transport failures keep the existing one retry; explicit
authentication failures notify immediately.
