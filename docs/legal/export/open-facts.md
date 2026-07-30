# Operational facts to complete before sending

These questions cannot be answered from the repository. The maintainer—not
counsel—should fill each blank and attach the named evidence where available.
No blank is an implied “yes”.

Three of the eight are answered as of 2026-07-30 (1, 2, 4), and two of those
three were closed by *building the reading*, not by typing an answer: the
hosting region came from a live `upsun project:list`, and the webhook-secret
state from the production configuration fingerprint the backend now serves and
every wake re-reads. Where an open fact can become a live read, prefer that —
a fact typed into this file is true on the day it is typed.

## Hosting and transport

### 1. Is the production Upsun project pinned to an EU hosting region?

Answer: **No. The live project reports a Swiss region; Switzerland is outside
the EU.**

Region/project identifier: `ch-1.platform.sh` / `6yxqptrmlmxuo`

Evidence attached (Upsun console or contract export): `upsun project:list
--format csv`, read 2026-07-28 in `run-260728-1227-soax`

Why this matters: the DPA's international-transfer analysis currently treats
the hosting location as an open fact. Switzerland has an
[EU adequacy decision](https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/adequacy-decisions_en),
but it remains a non-EU transfer that the DPA should identify accurately;
`.upsun/config.yaml` does not contain the project-creation-time region.

### 2. Is the production GitHub webhook secret set?

Answer: **Yes**, read from production rather than assumed.

Secret/configuration name verified (do not paste the secret):
`BRNRD_GITHUB_WEBHOOK_SECRET` — reported as set, never as a value.

Evidence/date checked: 2026-07-30. The deployed backend serves its own
configuration fingerprint (`src/brnrd/routers/daemons.py:705`,
`webhook_secret_set=bool(settings.github_webhook_secret)`); the daemon fetches
it and renders it into every wake (`src/brr/forge_state.py:653`). On
2026-07-30 the production reading was `webhook secret set · bot token set`
against a backend built at 14:51 UTC. This answer is therefore not a one-time
observation but a **standing** one: it is re-read on every wake and would flip
to `webhook secret unset` in the same line if the variable were ever dropped.

Why this matters: `src/brnrd/config.py` permits configuration through
`BRNRD_GITHUB_WEBHOOK_SECRET`/`GITHUB_WEBHOOK_SECRET`, but version control
cannot show production environment values. What closed this question was not a
document but a *mechanism* — the fingerprint endpoint shipped in #883. Where an
open fact can be turned into a live read, that is strictly better than an
answer typed once into this file.

### 3. What is the production Stripe state?

Live mode enabled: ___________________________________________________________

HugiMuni/Stripe KYB approved and unrestricted: ________________________________

Managed Payments / merchant-of-record agreement active for these products:
_____________________________________________________________________________

Evidence/date checked: _______________________________________________________

Why this matters: the repository implements Stripe Managed Payments, but API
keys, account status, contractual allocation, and product activation live in
Stripe.

### 4. Does production enforce HSTS?

Answer: **Yes, as probed after the deployment.**

Observed `Strict-Transport-Security` header and max-age:
`strict-transport-security: max-age=31536000`

Evidence/date checked: `curl -sSI https://brnrd.dev`; 2026-07-28,
`run-260728-1227-soax`. The edge now serves the one-year max-age enabled by
#832; `includeSubDomains` and `preload` remain deliberately unset.

Why this matters: application source and deployment configuration do not prove
the headers served by the production edge.

### 5. Does the published telephone number reach HugiMuni SAS?

Answer: ________________________________________________

Number called: _______________________________________________________________

Date and result: _____________________________________________________________

Why this matters: the Legal Notice publishes `+33 6 85 74 01 04` from
`src/frontend/src/lib/legalNotice.ts`; the repository proves publication, not
that the line reaches the company.

## Checkout observations

### 6. What does the live Stripe-hosted consumer checkout show about withdrawal?

Does it display the 14-day withdrawal notice: _________________________________

Does it request immediate performance: _______________________________________

Does it obtain express consent and acknowledgment of loss of the right where
applicable: _________________________________________________________________

Does it provide/confirm the withdrawal information after checkout: __________

Screenshot or test-session reference: ________________________________________

This is an observation, not a request for legal advice. If the checkout renders
the necessary Stripe-MoR presentation, the repository's current attribution
may hold. If it does not, counsel must identify what HugiMuni must add before
the payment step.

### 7. What consumer-mediation information does Stripe provide for this sale?

Named mediation body: ________________________________________________________

Where displayed (checkout, Stripe terms, receipt, support page): ______________

Evidence/date checked: _______________________________________________________

### 8. Do the production pages match the hashed repository snapshot?

Terms: __________________  Privacy: __________________  Legal Notice: __________

Hosted-Execution Beta Terms: __________________  Sub-processors: ______________

Revision deployed: ___________________________________________________________

Evidence/date checked: _______________________________________________________
