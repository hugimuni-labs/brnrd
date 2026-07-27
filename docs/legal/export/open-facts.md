# Operational facts to complete before sending

These questions cannot be answered from the repository. The maintainer—not
counsel—should fill each blank and attach the named evidence where available.
No blank is an implied “yes”.

## Hosting and transport

### 1. Is the production Upsun project pinned to an EU hosting region?

Answer: ________________________________________________

Region/project identifier: ______________________________

Evidence attached (Upsun console or contract export): _________________________

Why this matters: the DPA's international-transfer analysis treats the hosting
location as an open fact; `.upsun/config.yaml` does not contain the
project-creation-time region.

### 2. Is the production GitHub webhook secret set?

Answer: ________________________________________________

Secret/configuration name verified (do not paste the secret): _________________

Evidence/date checked: _______________________________________________________

Why this matters: `src/brnrd/config.py` permits configuration through
`BRNRD_GITHUB_WEBHOOK_SECRET`/`GITHUB_WEBHOOK_SECRET`, but version control
cannot show production environment values.

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

Answer: **No, as probed — fix in flight.** `https://brnrd.dev` served no
`Strict-Transport-Security` header when checked live.

Observed `Strict-Transport-Security` header and max-age: absent (no header
in the response).

Evidence/date checked: `curl -sI https://brnrd.dev` → `HTTP/2 200`, no
`strict-transport-security` line; 2026-07-27, run-260727-2005-f5u5. Fix:
this same change set enables `tls.strict_transport_security.enabled: true`
on the sole Upsun route (1-year max-age; `include_subdomains`/`preload`
left off as operator decisions). **Re-verify after the merge deploys** —
this answer describes the pre-fix edge, and the fix is not proof of itself.

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
