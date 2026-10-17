# Real CLI cache observations

Sources accessed 2026-10-17; calls measured 2026-10-16–17. Status of the **counts** below: measured. Status of a safe fixed subscription **lifetime**: unknown. All timestamps are UTC; gaps are from the previous call completion to this call start, not requested timers.

The prior body stopped before its 12m/32m timers fired. Continuation calls ran about 3.3 hours later. Thus the two actual gaps straddle a proposed 30m Codex lifetime, but do not locate an expiry. No 12m or 32m result is claimed.

The same prompt and CLI arguments were repeated in each model's same working directory, with no resume/session flag and no tools requested. Codex used `codex exec --ignore-user-config --model CORE --skip-git-repo-check --json -c model_reasoning_effort="low" -- PROMPT`; prompt: 180 repeated inert notebook records. Grok used `grok -m CORE --prompt-file FILE --output-format json --permission-mode bypassPermissions --trust --no-auto-update --rules RULES`; prompt: 300 inert reference records. These reproduce the real CLI access path, though not a held conversation: distinct sessions, hidden CLI prompts, routing and provider accounting can change.

## Codex

`usage` is from the CLI's `turn.completed` JSON event. `input_tokens` includes cached input; `cache_write_input_tokens` was zero in every observation.

| Core | Start UTC | Gap seconds | Input | Cached input | Output |
| --- | --- | ---: | ---: | ---: | ---: |
| gpt-5.6-luna | 2026-10-16T23:54:02+00:00 | — | 14576 | 10240 | 5 |
| gpt-5.6-luna | 2026-10-16T23:56:06+00:00 | 120.01 | 15770 | 10240 | 16 |
| gpt-5.6-luna | 2026-10-17T03:11:36+00:00 | 11725.20 | 14576 | 10240 | 5 |
| gpt-5.6-sol | 2026-10-16T23:54:02+00:00 | — | 17301 | 11264 | 5 |
| gpt-5.6-sol | 2026-10-16T23:56:07+00:00 | 120.01 | 17301 | 11264 | 5 |
| gpt-5.6-sol | 2026-10-17T03:11:35+00:00 | 11722.68 | 16107 | 11264 | 5 |
| gpt-5.6-terra | 2026-10-16T23:54:02+00:00 | — | 17303 | 6912 | 5 |
| gpt-5.6-terra | 2026-10-16T23:56:07+00:00 | 120.01 | 15901 | 6912 | 5 |
| gpt-5.6-terra | 2026-10-17T03:11:36+00:00 | 11724.18 | 16109 | 6912 | 5 |
| gpt-6-astra | 2026-10-16T23:54:02+00:00 | — | 16987 | 12288 | 5 |
| gpt-6-astra | 2026-10-16T23:56:09+00:00 | 120.01 | 16987 | 12288 | 5 |
| gpt-6-astra | 2026-10-17T03:11:31+00:00 | 11718.77 | 16987 | 12288 | 5 |
| gpt-6-luna | 2026-10-16T23:54:02+00:00 | — | 17553 | 11648 | 5 |
| gpt-6-luna | 2026-10-16T23:56:06+00:00 | 120.01 | 17553 | 11648 | 5 |
| gpt-6-luna | 2026-10-17T03:11:31+00:00 | 11720.03 | 16359 | 11648 | 5 |
| gpt-6-sol | 2026-10-16T23:54:02+00:00 | — | 17730 | 8064 | 5 |
| gpt-6-sol | 2026-10-16T23:56:07+00:00 | 120.01 | 16536 | 11776 | 5 |
| gpt-6-sol | 2026-10-17T03:11:31+00:00 | 11720.16 | 16536 | 11776 | 5 |
| gpt-6.1-sol | 2026-10-16T23:54:02+00:00 | — | 17053 | 12288 | 5 |
| gpt-6.1-sol | 2026-10-16T23:56:07+00:00 | 120.01 | 17053 | 12288 | 5 |
| gpt-6.1-sol | 2026-10-17T03:11:31+00:00 | 11720.68 | 17053 | 12288 | 5 |

Every model reports cached prefixes even after the long gap. Some input sizes differ despite identical user text, and cached counts already appear on baseline calls. These can be shared system-prefix hits; the CLI counters do not identify which prefix survived. Therefore this does **not** establish a minimum lifetime for the conversation or justify assigning the API's 1800s policy to the subscription path. A controlled same-session prefix study or a provider subscription guarantee would resolve it.

## Grok

Counters and cost come from the CLI JSON `usage` and `total_cost_usd`. Here `input_tokens` is uncached input; cached input is a separate counter.

| Core | Start UTC | Gap seconds | Input | Cache read | Output | Cost USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| grok-4.5 | 2026-10-16T23:51:53+00:00 | — | 22950 | 128 | 35 | 0.01569046 |
| grok-4.5 | 2026-10-16T23:54:00+00:00 | 120.01 | 22308 | 128 | 18 | 0.01521922 |
| grok-4.5 | 2026-10-17T03:11:39+00:00 | 11840.02 | 21283 | 1152 | 18 | 0.01462666 |
| grok-4.6 | 2026-10-16T23:51:53+00:00 | — | 21155 | 1280 | 24 | 0.01465196 |
| grok-4.6 | 2026-10-16T23:54:00+00:00 | 120.01 | 22434 | 0 | 24 | 0.01530408 |
| grok-4.6 | 2026-10-17T03:11:39+00:00 | 11840.61 | 22304 | 128 | 25 | 0.01523948 |
| grok-4.7 | 2026-10-16T23:51:53+00:00 | — | 22453 | 1664 | 51 | 0.01565496 |
| grok-4.7 | 2026-10-16T23:54:00+00:00 | 120.01 | 21165 | 2304 | 48 | 0.01488180 |
| grok-4.7 | 2026-10-17T03:11:40+00:00 | 11842.97 | 21168 | 2304 | 49 | 0.01488588 |
| grok-4.7-build-fast | 2026-10-16T23:51:53+00:00 | — | 22477 | 1664 | 49 | 0.03133440 |
| grok-4.7-build-fast | 2026-10-16T23:54:04+00:00 | 120.02 | 21813 | 1664 | 49 | 0.03043136 |
| grok-4.7-build-fast | 2026-10-17T03:11:40+00:00 | 11841.21 | 21175 | 2304 | 47 | 0.02977312 |

Cache creation counters were zero throughout. These are small partial-prefix hits, including a miss at two minutes for grok-4.6; they do not show retention of the long user prefix. Requests used fresh CLI sessions, so conversation routing may differ. No safe cache-driven hold interval follows. Build-fast calls cost roughly twice the other Grok calls for this workload; these are CLI-reported costs, not a subscription-quota conversion.

## Vibe and remaining limits

Both `mistral-medium-3.5` and `glm-5-3` were attempted at baseline and after 120s through `VIBE_ACTIVE_MODEL=CORE vibe -p --auto-approve --trust --output json --max-turns 1 --disabled-tools ".*"`. Each returned HTTP 402 before model usage, so no TTL measurement exists. The `local` alias timed out without usage at both attempts (100s invocation ceiling). No billing settings or credentials were changed to continue. A funded authorized access path or a running local server is needed for measurements.

Claude has an access-specific documented TTL and was not given a synthetic lifetime from probes. Its production transcript reports `cache_creation.ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`; the jack uses that evidence conservatively. API-key paths were researched in provider docs, not exercised using substitute credentials.

Raw CLI receipts contain local paths/session identifiers and stay outside the repository. This table deliberately exports usage and timestamps only. Primary sources and the unresolved subscription keepalive cost decision are in [README.md](README.md).
