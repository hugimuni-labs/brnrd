# Grok billing capture, 2026-10-17

The positive HTTP fixture [grok_billing_2026_10_17.json](grok_billing_2026_10_17.json)
is the response to a read-only GET using the locally stored unexpired access
key. No credential is included in this fixture. The request returned HTTP 200;
a SHA-256 comparison of `auth.json` before and after the request was equal.

## Request provenance

The path was captured from the installed Grok 1.0.60 macOS/aarch64 client,
not inferred from binary strings. A loopback HTTP server received the actual
client requests using the documented `GROK_CLI_CHAT_PROXY_BASE_URL` override.
The client ran in an isolated temporary HOME/GROK_HOME with a synthetic access
key, no refresh token, and no model prompt. Its startup and `/usage` billing
requests were:

```
GET /v1/billing?format=credits
Authorization: Bearer <synthetic key>
x-xai-token-auth: xai-grok-cli
x-userid: <account coordinate, omitted>
x-grok-client-version: 1.0.60
x-grok-client-mode: interactive
```

The same captured path on `https://cli-chat-proxy.grok.com` returned the linked
fixture: `config.creditUsagePercent = 55.0`, a declared weekly `currentPeriod`,
and an exact reset timestamp. The existing client log's `billing: fetched
credits config` agreed with those values. The payload does not declare a plan
name, so the collector does not invent one.

## Why interactive startup is wrong for a gauge

The client performs authentication before the usage command is typed. An ACP
`initialize` comparison against disposable auth stores, with no refresh tokens
and no `authenticate` request, returned:

| Stored key | auth methods | defaultAuthMethodId |
|---|---|---|
| Unexpired | cached_token, grok.com | cached_token |
| Expired | grok.com | null |
| Absent | grok.com | null |

The TUI's own log records `pager eager auth method selected`; a gauge that
starts this UI inherits its sign-in selection, including browser login when
cached auth is unavailable. The valid-key capture used the same temporary cwd
shape as the old probe and selected cached auth: temporary cwd alone does not
cause the popup. The installed client's authentication documentation also
states that expired credentials can lead to interactive re-authentication.

The retained local log starts after the reported incident and contains valid
cached-token startups. It does **not** prove whether that earlier popup was
caused by expired credentials, a different auth path, or a failed refresh.
That historical distinction remains unverified; no refresh was attempted to
reproduce it. Removing interactive startup eliminates all those browser paths.

## Vibe

`brr.vibe_usage` reads one invocation's session token journals. Those have no
account quota limit or reset and cannot supply a fuel percentage. The bundled
vibe usage script participates in the sweep and reports an explicit unavailable
reason with no windows. It never converts session totals into account fuel.
