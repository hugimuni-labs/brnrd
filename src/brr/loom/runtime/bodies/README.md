# Body cache lifetimes

The optional cache file is JSON: default is the shell fallback; models maps
an exact seated core to a row. Each row has seconds (number or null, meaning
none), status (documented, measured, unknown), and reason. Optional access
maps effective TTLs reported by the CLI to different seconds.

The loom passes the actual launched core in BRNRD_BODY_CORE; editing a seat
later cannot retime that body. The jack uses its model row, falling back to
default only if no model row exists. Unknown/malformed rows, missing files,
bad JSON, invalid bodies and non-finite values mean no lifetime. Legacy
scalar seconds/none files still work. A home body folder overrides the whole
bundled folder, including cache.

The interval is lifetime minus 300 seconds, preserving #2331's margin.
Lifetimes at most 300 seconds cannot buy a safe keepalive: hold to the hook
ceiling, then re-hold (or release an idle thread-opened worker). port/wait
wins. Fuel parking still takes precedence over cache warmth. No new
subscription-specific keepalive policy is introduced.

Claude rows default conservatively to 300 seconds. The hook reads the latest
cache-write buckets in the transcript's final 200 KB. An ephemeral_1h write
permits 3600; an ephemeral_5m write wins if both occur. Missing/unreadable
usage retains 300. Subscription credits/settings cannot silently inherit an
hour from a login check. No TTL is forced and no paid option enabled.

## Inventory and sources

Checked 2026-10-17: all five bodies/*/run adapters; runner_cores.py registry;
Codex models_cache.json fetched 2026-10-17T03:11:38Z; Grok models_cache.json
fetched 2026-10-17T03:10:41Z; Vibe 2.25.8 defaults. These adapters accept
arbitrary pins; this is a dated inventory, not an allowlist. The loom reads
literal shell/core seats via adapters.at_birth, not the older runner's role
resolver. Unlisted/future pins use the shell default.

| Shell / exact cores | Access path and lifetime | Evidence |
| --- | --- | --- |
| claude: haiku, sonnet, opus, fable | Claude Code -p: subscription included main usage 3600s; API key/credits default 300s. Effective CLI write bucket selects the row's 5m/1h value. | documented C |
| codex: gpt-6.1-sol, gpt-6-astra, gpt-6-sol, gpt-6-luna, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna | codex exec --ignore-user-config --model: none for the subscription CLI; API model policy is 1800s. No retention flag added and no API promise imported into the subscription path. | unknown CLI lifetime; documented API policy O; CLI observations below |
| grok: grok-4.5, grok-4.6, grok-4.7, grok-4.7-build-fast | Build subscription proxy or API key: none. API allows eviction at any time; subscription TTL unpublished. | unknown TTL X; observed counts in measurements |
| vibe: mistral-medium-3.5, glm-5-3 | Subscription or API key: none; no published TTL, subscription probes rejected with exhausted budget. Installed medium alias maps to mistral-vibe-cli-latest. | unknown M |
| vibe: local | Installed alias maps to devstral on localhost:8080; user server has no portable TTL. | unknown; no local service measured |
| fake: any core (default haiku) | Test process makes no provider calls and has no prompt cache. | documented by fake/run |

Hidden Codex feed entries gpt-reserve and codex-auto-review are excluded by
runner_cores._parse_codex_models; neither has a selectable catalog row. A
literal seat can still pin either: unknown fallback. Historical models
absent from the feed use the same fallback.

Primary sources, all accessed 2026-10-17:

| ID | Source | Finding |
| --- | --- | --- |
| C | [Claude Code caching](https://code.claude.com/docs/en/prompt-caching#cache-lifetime) | Main -p calls: included subscription 1h, API/credits 5m. Overrides exist; usage reports effective cache-write buckets. |
| O | [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching#cache-lifetime) | GPT-5.6+ minimum 30m after write/reuse. Earlier in_memory typically 5–10m, up to 1h; extended typically 30m, up to 24h. 24h is not a 24-hour minimum. Earlier extended models: gpt-5.5, gpt-5.5-pro, gpt-5.4, gpt-5.2, gpt-5.1-codex-max, gpt-5.1, gpt-5.1-codex, gpt-5.1-codex-mini, gpt-5.1-chat-latest, gpt-5, gpt-5-codex, gpt-4.1. 5.5/pro only support extended; other eligible defaults depend on organization ZDR. |
| X | [xAI cache FAQ](https://docs.x.ai/developers/advanced-api-usage/prompt-caching/best-practices) | All Grok language models support caching; eviction can occur at any time. No fixed TTL. |
| H | [Grok headless](https://docs.x.ai/build/cli/headless-scripting) | Model selection, sessions, JSON output and subscription/API-key paths. |
| M | [Mistral caching](https://docs.mistral.ai/studio/conversations/advanced/prompt-caching) | Prefix caching, cached-token counters and 0.1x input billing; no TTL. |
| P | [Claude API cache pricing](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#pricing) | 5m/1h writes 1.25x/2x input; reads Fable 5.1 0.025x, Opus/Sonnet 5.5 0.05x, other models 0.1x. |
| B | [Mistral subscriptions](https://docs.mistral.ai/admin/billing-usage/subscriptions) | Vibe, Studio and API share monthly allowance, then optional prepaid PAYG. |

## Measurements and limits

See [cache-measurements.md](cache-measurements.md) for sanitized real CLI
receipts. Hits at a gap are observations, not a guaranteed lifetime. Misses
can be routing, not expiration. No fixed TTL is assigned from an unbracketed
series. Failed credentials/exhausted quota are blockers, not proof of no cache.

The largest unresolved boundary is Codex subscription versus documented API
retention: the adapter adds no policy, but provider docs do not separately
promise a subscription proxy TTL. Timed probes show prefix hits across two
gaps but cannot establish a stable CLI lifetime; these rows therefore stay
unknown/none. The bundled Codex adapter does not attest which backend handled
a request, so API-key installations also use this conservative fallback; a
home override may declare 1800s when the direct API path is established.
Custom gateways/arbitrary pins need
their own home declaration. Claude falls back to 5m if a large transcript
record puts usage outside its tail window.

## Open decision: pay idle subscription keepalives?

Still open. A keepalive is a model request. For idle time H, interval I,
cached context C, new input A and output O, approximately N=floor(H/I)
keepalives cost N*(C*cached_rate + A*input_rate + O*output_rate), plus writes.
Cold resumption pays a fresh prefill/write of C once if work resumes. A
never-resumed body buys no avoided prefill. Subscription quota is not
converted to token dollars without evidence.

| Shell | Keep alive | Let expire (cache policy) |
| --- | --- | --- |
| Claude | Confirmed 1h: every 55m, about 26 requests/day. API reads: Fable 5.1 0.025x, Opus/Sonnet 5.5 0.05x, other models 0.1x; writes 1.25x/2x (P). Subscription quota conversion unpublished; requests consume allowance. | No cache-driven idle requests; next work pays cold write/prefill. A 5m TTL cannot clear our 5m margin. |
| Codex | If the API 30m lifetime were established for this path: every 25m, about 57 requests/day. The bundled CLI rows stay none. GPT-5.6+ API reads 0.1x input (GPT-6.1 Sol 0.05x), writes 1.25x. Subscription quota conversion unestablished. | No cache-driven idle requests; possible cold prefill/write at next work. |
| Grok | No safe interval established. Requests consume subscription allowance or API spend; build-fast is costlier (receipts). | No cache-driven idle calls; possible cold prefill later. |
| Vibe | No safe interval established. Requests consume shared monthly allowance or PAYG; API cached input 0.1x base input. | No cache-driven idle calls; possible cold prefill later. |
| Fake | No provider/quota charge. | No provider/quota charge. |

No-lifetime bodies may still re-hold at the hook ceiling: a hook liveness
requirement, not an established cache keepalive interval.
