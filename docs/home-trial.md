# Home trial clock

The server records a 14-day clock once per account and exposes its UTC dates at
`GET /v1/accounts/home-trial`. This stage observes connection state; expiry does
not change access, subscription status, prices or the existing product limits.

Sign-in, creating a repository, starting a pairing and browser approval do not
start the clock. The computer collecting the approved token does. An existing
credential starts it on successful daemon registration or an accepted loom
snapshot/parts upload. Reconnecting, reinstalling or using another computer
keeps the same account timestamp. Failed or withheld uploads do not start it.
The parts route checks activity-lane consent before calling `_store_loom_parts`,
so a withheld parts upload cannot reach the trial write.
The conditional account update participates in the connection transaction, so
concurrent connections cannot replace an existing start date.

The account endpoint accepts the account's API token or browser session; daemon
credentials cannot read it. It returns `status` (`not_started`, `active`, or
`expired`), nullable `started_at` and `ends_at`, and `enforcement_enabled: false`.
At the exact end instant the status is `expired`. This endpoint is intended for
the account page; it does not replace the subscription endpoint or decide
whether a subscriber can use a service.

Deployment adds one nullable account column through the existing PostgreSQL
startup migration. New databases get it through model creation. Existing users
are not backdated from sign-in or historical activity. Their first successful
connection after deployment records the timestamp. A later enforcement rollout
must supply notice and a fresh transition window for existing free hosted users;
this observational timestamp alone is not permission to cut anyone off.

Service gates, transition notices, cleanup and sign-in/pricing claims are later
changes. Every existing subscriber, including historical prices, retains the
current access rules throughout this stage. No Stripe state is written here.

## The home gate (off by default)

`brnrd/home_access.py` holds the one entitlement decision every refusal calls.
It is off while `BRNRD_HOME_GATE_FROM` is unset, unparseable or in the future:
then it reads nothing and no response changes. Set to an ISO instant, it turns
on from that instant, and the instant also restarts every earlier clock, so an
account's window ends 14 days after the later of its start and the switch-on.
`GET /v1/accounts/home-trial` then reports that window and
`enforcement_enabled: true`.

Admitted while on: household GitHub logins configured by the operator, any
live subscription (`Account.tier` subscribed, which any non-canceled Stripe
subscription on any price sets, past due included), an
account whose clock has not started, an account inside its window, and any
account whose billing state cannot be read.

Refused otherwise: hosted board reads and sends (`/v1/loom/*`, the board's
`/v1/dashboard/*` reads, letters, wake requests, respawns), new messenger
pairing codes, every device upload and relay route behind the install ceiling,
the inbox long-poll, attachment downloads, device configuration proposals,
`/door` and the managed GitHub token.
Both `brnrd account connect` and relay setup use the same device handshake.
The server checks entitlement at browser approval and again before handing
out the approved device token. Registration checks it for existing tokens.
The initial anonymous pairing request has no account or service credential.
Always open: account, billing, deletion, terms, sign-in,
`whoami`, `revoke`, paired-chat listing and revocation, repo settings, config
approval, run stop and release.

Browser routes and approved-token pickup answer 402. Authenticated device
service routes answer 429 with `Retry-After`
(`BRNRD_HOME_GATE_RETRY_AFTER_S`, 900), after holding the request
`BRNRD_HOME_GATE_HOLD_S` (20) seconds, or the long-poll's own timeout for the
inbox, so an installed client that retries without backoff is paced by the
server. At most `BRNRD_HOME_GATE_MAX_HOLDS` (256) holds are open at once in
the process; past that a refusal answers at once. Both carry `{reason: home_subscription_required, message, account_url}`.
