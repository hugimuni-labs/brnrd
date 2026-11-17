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
