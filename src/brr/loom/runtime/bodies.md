| state | observed | next | commands |
|---|---|---|---|
| wanted, parked | closed | ended | end_settled, drain, drained, release |
| wanted, parked | failure | ended | exit, end_failed, drain, drained, release |
| parked | before until | parked | none |
| parked | gauge refreshing | parked | none |
| wanted | gauge refreshing | wanted | pending |
| wanted, parked | gauge empty | parked | park |
| wanted, parked | gauge ok | running | prepare, shown, spawn, started |
| running, leaving, stopping, unidentified | not alive | ended | collect, drain, exit, end, drain, drained, release |
| running | alive, open | running | none |
| running | alive, closed | leaving | leaving |
| leaving, unidentified | alive, reopened | running | stayed |
| leaving | alive, before deadline | leaving | none |
| leaving | alive, due, proven | stopping | stopped, signal |
| leaving | alive, due, unproven | unidentified | unidentified |
| stopping | alive, proven | stopping | signal |
| stopping | alive, not proven | stopping | none |
| unidentified | alive, closed | unidentified | none |
| exited | any | ended | end, drain, drained, release |
| ended | not released | ended | drain, drained, release |


A body folder's optional `cache` file declares its prompt-cache lifetime in
seconds, or `none`. Missing files mean `none`. The jack holds until five
minutes before that lifetime; without one it waits for letters until the
stop-hook ceiling, then re-holds once. `port/wait` overrides either default.
Lifetimes at or below five minutes cannot buy a safe keepalive and use the
ceiling too. Home body folders override the whole bundled folder, including
this declaration.

Claude declares 3600 seconds. Codex declares `none`: OpenAI's
[prompt caching documentation](https://developers.openai.com/api/docs/guides/prompt-caching#cache-lifetime)
describes model-dependent retention (30 minutes for GPT-5.6 and later,
different policies for older models), not one lifetime for every Codex CLI
model and authentication mode. Grok declares `none`: xAI's
[cache FAQ](https://docs.x.ai/developers/advanced-api-usage/prompt-caching/best-practices)
says entries may be evicted at any time and gives no fixed lifetime. Vibe
and the test shell declare `none` too.
