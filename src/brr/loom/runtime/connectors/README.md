A connector speaks native: its in/out files contain only its own chat and user ids.
The loom owns names: it adds and strips `channel:<instance>/` at the folder boundary.
`channels.toml` maps `instance:user` → person.

An optional executable `presence` receives a JSON array on stdin. Each operation
is `{"verb":"seen","letter":{...}}` or
`{"verb":"working","on":true|false,"letter":{...}}`. The letter carries its
native id (`native`) and chat (`chat`), alongside its admitted/booted progress
row. The loom calls seen after admission and working after a shown letter's
body actually starts. First word, body end or lease loss turns working off;
while working, the loom renews it every four seconds. A stalled loom's snapshot
expires after ten seconds. Calls run outside the tick and are best effort.
Platforms without an off primitive let their indicator expire. GitHub has
seen only. A connector without this executable needs no placeholder.

`letter.presence` facts carry `letter`, `step` (`admitted`, `stamped`, `booted`,
`first-word`) and numeric Unix `at`, using the original event time. Booted rows
also name `strand` and `gen`; connector rows carry `channel`, `native`, `chat`.
The first-word clock consumes these rows, deriving the same steps directly
when the send has arrived before the observer's next tick. Feedback success
is never evidence that a reply was delivered.
