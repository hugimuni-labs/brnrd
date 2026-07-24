<!--
Status: engineering draft, not legally reviewed. Prepared for review by a
French/EU lawyer before meaningful paid consumer exposure (#569, #672).
Every factual claim below is cited to the code that enforces it or to
SECURITY.md; a claim that could not be verified this way is listed at the
bottom of this comment instead of being asserted in the body.

Companion documents (Terms of Service, /privacy, /legal-notice) live on
sibling branches (`brr/the-legal-pack`) not yet merged to `main` as of this
draft (2026-07-24). The entity identifiers used below (SIREN, RCS,
registered office, VAT number, share capital) are sourced from
`src/frontend/src/lib/legalNotice.ts` on that branch, itself sourced from
the K-bis supplied by the maintainer 2026-07-24. If that branch changes
before merge, re-sync this Annex I block against it.

Claims from research-legal-review-hosted-vs-oss-2026-07-24.md that this
draft could NOT independently confirm in the code, and therefore does not
assert as fact:
  - Upsun/Platform.sh hosting region pinned to the EU. No region directive
    exists in `.upsun/config.yaml` or `src/brnrd/config.py`; Upsun regions
    are set at project-creation time outside version control. Flagged in
    §14 and Annex III as needing operational confirmation, not asserted.
  - GitHub App installation-token lifetime of "1 hour". The code mints the
    token (`src/brnrd/platforms/github_app.py:72`) but the expiry is set by
    GitHub's API, not brnrd's code, so it is a platform fact, not a repo
    fact. Stated in Annex II as GitHub's documented behaviour, not as
    something brnrd enforces.
  - An internal 72-hour supervisory-authority notification runbook step for
    breaches (research page item 5). The code and SECURITY.md give a
    contact address (security@hugimuni.fr) and nothing more procedural;
    §11 below commits to GDPR's own "without undue delay" standard rather
    than inventing an internal SLA the org does not yet have.
  - A live sub-processor-change notification mechanism. §9 commits
    HugiMuni to updating Annex III and giving notice before a material
    change — achievable today by editing this document — but no automated
    notification tooling exists in the codebase. Stated as a procedural
    commitment, not a shipped feature.
-->

# Data Processing Agreement

**brnrd.dev — Article 28 GDPR**

This Data Processing Agreement ("**Agreement**") is entered into between
the Customer identified by its brnrd.dev account ("**Controller**", "**you**")
and **HugiMuni SAS**, a French *société par actions simplifiée* with share
capital of €500, registered at RCS Tarascon under SIREN 104 156 260,
registered office 6 rue de la Verdière, 13200 Arles, France, VAT number
FR 73 104 156 260 ("**Processor**", "**HugiMuni**", "**we**"),
<!-- entity block: src/frontend/src/lib/legalNotice.ts PUBLISHER fields, K-bis supplied 2026-07-24, brr/the-legal-pack (not yet merged) -->
governing HugiMuni's processing of personal data on the Controller's behalf
in connection with the brnrd.dev hosted service (the "**Service**").

This Agreement is incorporated into and forms part of brnrd's Terms of
Service by reference. Where a conflict exists between this Agreement and
the Terms on a data-protection matter, this Agreement controls. Until the
Terms formally cross-reference it, this Agreement stands on its own and is
available to any Customer on request.

## 0. The boundary this Agreement draws

brnrd.dev's roles are mixed, and getting the boundary right is this
document's whole job.

- **HugiMuni is Controller** for account identity (GitHub id, login,
  email), terms-acceptance records, billing and subscription data, and the
  operational telemetry a connected daemon reports about itself (runner
  catalogue, quota figures, live-run status). That processing is described
  in HugiMuni's privacy notice, **not** this Agreement, and Customer's
  personal data used for those purposes is processed for HugiMuni's own
  purposes as controller, on a lawful basis HugiMuni establishes itself
  (principally contract performance).
- **HugiMuni is Processor** — and this Agreement applies — only for
  **Customer Content**: the message bodies relayed between a connected
  gate (Telegram, GitHub, Slack) and Customer's own self-hosted daemon; the
  work-surface, knowledge-base, and run pages that daemon chooses to
  publish to the dashboard mirror; and code-review packs relayed for
  diffense. Customer is Controller of whatever personal data Customer
  Content contains, including third-party personal data it did not
  generate itself — commit authors, issue or PR commenters, other members
  of a paired chat.
- **What never becomes HugiMuni's processing at all.** Agent execution —
  reading Customer's repository, calling a model provider, writing
  commits — happens on Customer's own machine, under Customer's own
  credentials and model subscription, and never reaches HugiMuni's
  infrastructure unless Customer's own daemon chooses to publish it.
  HugiMuni holds no model-provider credential and makes no model call of
  its own against Customer Content.
  <!-- SECURITY.md "The backend makes zero LLM calls." + "What stays local" bullet -->

## 1. Definitions

Terms defined in Regulation (EU) 2016/679 ("**GDPR**") — controller,
processor, personal data, processing, data subject, personal data breach,
sub-processor, supervisory authority — carry their GDPR meaning here.
"**Customer Content**" has the meaning given in §0. "**Services**" means
the relay, dashboard-mirroring, and review-pack-relay functions of
brnrd.dev described in Annex I.

## 2. Subject-matter and duration

The subject-matter of the processing is Customer Content, to the extent it
contains personal data, processed by HugiMuni solely to provide the
Services. Processing runs for as long as HugiMuni processes Customer
Content on Customer's behalf — in practice, for the life of Customer's
connected repository or account — and ends when the underlying Terms end
or Customer disconnects the last connected repository, whichever is
sooner, subject to §12 (deletion or return).

## 3. Nature and purpose of processing

The nature of the processing is automatic: store-and-forward relay of
inbound and outbound messages through a queue; verbatim mirroring of pages
a Customer's own daemon publishes, for the sole purpose of rendering that
Customer's own dashboard; and transient in-memory relay of review packs
behind an unguessable link. HugiMuni does not analyse, mine, profile, or
otherwise use Customer Content beyond delivering it to the party the
Customer's own configuration names, or rendering it back to the Customer's
own account.

The purposes are exactly three:

1. Deliver messages between a Customer-chosen gate (Telegram, GitHub,
   Slack) and that Customer's own self-hosted daemon.
2. Render the dashboard at brnrd.dev for a Customer who has opted in with
   `brnrd account connect`.
3. Relay a code-review pack to a reviewer via a bearer-token link, for
   Customers using the diffense review flow.

## 4. Types of personal data processed

- **Relayed message text** — free text a Customer's own users or
  collaborators wrote, forwarded verbatim between gate and daemon.
  <!-- Event.body, src/brnrd/models.py:167 -->
- **Message routing metadata** — for Telegram: chat id, topic id, message
  id, sender id, username; for GitHub: repository, issue/PR number,
  comment id, comment URL, and the commenting author's login.
  <!-- src/brnrd/routers/webhooks.py:103 (telegram reply_to), :214 (github reply_to) -->
- **Free-form prose an agent writes**, mirrored verbatim when Customer
  opts into dashboard publishing: work-surface pages, knowledge-base
  pages, per-run bodies, run summaries, and live progress notes. This
  layer routinely quotes the code an agent was working on, and may
  incidentally carry personal data the Customer's own agent chose to
  write down.
  <!-- SECURITY.md "What dashboard publishing mirrors" table, Corpus / Run ledger / Live runs rows -->
- **File paths, commit subjects, and branch names** on Customer's own
  machine, carried in the run ledger a Customer's daemon publishes.
  <!-- SECURITY.md "Run ledger" row -->
- **Review-pack content** — file paths, line numbers, and agent prose
  about a reviewed diff (not the diff itself).
  <!-- src/brnrd/pack_relay.py:1-20 module docstring -->
- **Image-attachment pointers** (`file_id`, filename, kind) — never the
  image bytes themselves.
  <!-- src/brnrd/models.py:184-189 -->

HugiMuni does not intentionally collect special-category data (GDPR Art
9) and the Services are not designed to process it, but because Customer
Content is free text authored by Customer's own users, HugiMuni cannot
technically filter it out; Customer determines what enters Customer
Content and is responsible for having a lawful basis to send it.

## 5. Categories of data subjects

- Customer's own personnel and collaborators who send messages through a
  connected gate.
- Third parties whose personal data appears in Customer's repositories or
  conversations and is relayed or mirrored incident to Customer's use of
  the Services — commit authors, issue or PR commenters, other members of
  a paired chat. Customer is Controller for these individuals; HugiMuni
  processes their data only as Customer's instructions (via Customer's own
  daemon configuration) direct.

## 6. Processing only on the Controller's documented instructions

HugiMuni processes Customer Content only on Customer's documented
instructions. Those instructions are given through:

1. This Agreement and the Terms of Service;
2. Customer's own daemon configuration (`.brr/config` — e.g.
   `publish.layers`, which gates are configured, whether
   `brnrd account connect` has been run at all), which determines what
   Customer Content reaches the Service in the first place; and
3. The specific messages Customer's own daemon transmits to the Service's
   API endpoints.

Because Customer's daemon runs on Customer's own infrastructure under
Customer's own control, the decision of what Customer Content reaches
HugiMuni at all is Customer's, not HugiMuni's: HugiMuni receives only what
Customer's own software is configured to send, and has no mechanism to
compel a daemon to send more than it is configured to.
<!-- publish.layers governs collection, not just transmission, per SECURITY.md "Turning a lane off stops collection, not merely transmission" -->

If HugiMuni believes an instruction infringes the GDPR or another
applicable data-protection provision, it will inform Customer without
undue delay. HugiMuni will not process Customer Content for any purpose
outside these instructions, except where required to do so by European
Union or Member State law, in which case HugiMuni will inform Customer of
that legal requirement before processing, unless that law prohibits such
information on important grounds of public interest.

## 7. Confidentiality of personnel

HugiMuni ensures that any person it authorises to process Customer
Content is under an appropriate obligation of confidentiality, whether
contractual or statutory.

## 8. Security measures (Article 32)

HugiMuni implements the technical and organisational measures described
in **Annex II**, appropriate to the risk presented by processing free-text
message content and mirrored agent output at the scale of a beta-stage
service. This Agreement states plainly where a measure does not exist —
notably, HugiMuni applies no application-level encryption at rest beyond
what its hosting platform provides — rather than implying broader coverage
than Annex II supports.

## 9. Sub-processors

Customer gives HugiMuni **general written authorisation** to engage the
sub-processors listed in **Annex III** for the purposes described there.

Before engaging a new sub-processor or replacing an existing one, HugiMuni
will update the published sub-processors page at
<https://brnrd.dev/sub-processors> — the authoritative, current list — and
give Customer notice of the change there, dating the change. Customer
may object to a new sub-processor on reasonable data-protection grounds
within a reasonable period after notice; if HugiMuni and Customer cannot
resolve the objection, Customer's remedy is to stop using the feature of
the Services that depends on that sub-processor, or to terminate the
Agreement as to the affected processing.

HugiMuni imposes data-protection obligations on each sub-processor that
are no less protective than those in this Agreement, by contract, and
remains liable to Customer for a sub-processor's performance of those
obligations to the same extent HugiMuni is liable for its own.

## 10. Assistance with data-subject rights

HugiMuni assists Customer, taking into account the nature of the
processing, in responding to requests from data subjects to exercise
their GDPR rights over Customer Content, through:

- **Self-service.** Disconnecting a repository immediately deletes that
  repository's queued messages, activity rows, chat pairings, and tokens.
  <!-- src/brnrd/routers/_session.py:339-362 `_disconnect_repo_core`, the delete loop at :347-348 -->
  When Customer's last connected repository is disconnected, the
  dashboard mirror for that account is emptied as well.
  <!-- src/brnrd/routers/_session.py:353-360 -->
- **Account deletion.** Deleting the account itself (dashboard settings,
  "danger zone") sweeps every Customer Content store the repo-disconnect
  path above does, for every connected repository at once, plus the
  account-identity and billing-mirror stores this Agreement's §0 places
  outside its own Processor scope — that broader erasure is a
  controller-role undertaking, not this Agreement's, but is named here so
  the self-service picture is complete. Cancels any live subscription
  immediately. The append-only billing ledger is retained; see the Art 30
  record's "Billing and payment" row for the statutory-retention caveat.
  <!-- src/brnrd/account_deletion.py, POST /v1/accounts/delete (src/brnrd/routers/accounts.py) -->
- **Manual assistance.** For anything the self-service paths above don't
  cover, HugiMuni responds to requests sent to **security@hugimuni.fr**.

This is a narrower undertaking than HugiMuni's own controller-role rights
process (see the privacy notice): as processor, HugiMuni cannot itself
decide whether a request is founded, since Customer Content belongs to
Customer's own data relationships. HugiMuni will forward a data subject
request it receives directly, in error, to Customer without responding to
it substantively.

## 11. Assistance with Articles 32–36

HugiMuni assists Customer, taking into account the information available
to HugiMuni and the nature of the processing, with:

- Maintaining security appropriate to the risk (Art 32) — Annex II is
  that record, kept current as the measures it lists change.
- Notifying Customer **without undue delay** after becoming aware of a
  personal data breach affecting Customer Content (Art 33), by email to
  the account's registered address and, in parallel, to
  **security@hugimuni.fr** as the inbound channel for anyone reporting a
  suspected incident. The notice will describe, to the extent known: the
  nature of the breach, the categories and approximate number of data
  subjects and records concerned, the likely consequences, and the
  measures taken or proposed. HugiMuni does not currently commit to a
  fixed internal hour count for its own onward regulatory notification;
  it commits to GDPR's own "without undue delay" standard rather than a
  number the organisation has not yet operationalised.
- Enabling Customer to notify its own supervisory authority and affected
  data subjects (Art 34), by providing the information above.
- Data protection impact assessments and prior consultation with a
  supervisory authority (Arts 35–36), where Customer's own assessment
  requires information only HugiMuni holds — principally the contents of
  Annex I and Annex II.

## 12. Deletion or return of personal data at the end of the provision of services

On termination of the Services as to a given repository or account,
HugiMuni deletes the Customer Content it holds as processor:

- Disconnecting a repository deletes that repository's queued messages,
  activity rows, chat pairings, and tokens immediately.
  <!-- src/brnrd/routers/_session.py:347-348 -->
- Disconnecting the last repository on an account empties the dashboard
  mirror for that account.
  <!-- src/brnrd/routers/_session.py:353-360 -->

Deletion of relayed message content is also **independent of any Customer
action**: a message body is nulled the moment the reply is delivered, and
in any case no later than 14 days after receipt if never answered; the
routing row it belonged to is deleted at 90 days. This runs automatically
whether or not Customer ever disconnects.
<!-- record_response body-null: src/brnrd/inbox.py:311 · TTLs: src/brnrd/inbox.py:327-328 (_EVENT_BODY_TTL, _EVENT_ROW_TTL) · sweep: src/brnrd/inbox.py:338-361 (gc_events) -->

HugiMuni does not offer a "return" of Customer Content in a structured
export format at this time; Customer's durable copies are its own repository,
its own knowledge-base checkout, and its own `.brr/` runtime directory,
which the mirror only ever reflected. Nothing in this Agreement requires
HugiMuni to retain a copy of Customer Content beyond what the privacy
notice separately requires for HugiMuni's own controller-role account and
billing records ("Your account record and billing ledger," kept while the
account exists), which are out of this Agreement's scope.

## 13. Audit and information rights

HugiMuni makes available to Customer the information necessary to
demonstrate compliance with this Agreement's Article 28 obligations,
principally through this Agreement's Annexes and HugiMuni's public
`SECURITY.md`, which HugiMuni keeps current as the system it describes
changes.

HugiMuni permits, and contributes to, audits — including inspections —
conducted by Customer or an auditor Customer mandates, on reasonable prior
written notice, no more than once in any 12-month period absent a
reasonably suspected breach, subject to the auditor's confidentiality
undertaking and to not unreasonably disrupting HugiMuni's operations or
other customers. Each party bears its own costs unless an audit
establishes material non-compliance by HugiMuni, in which case HugiMuni
bears the reasonable cost of that audit.

## 14. International transfers

Sub-processor transfers outside the European Economic Area, and their
stated basis, are listed in Annex III. In summary:

- **Stripe** and **GitHub** (United States) — transfers rely on the EU-US
  Data Privacy Framework and/or Standard Contractual Clauses.
- **Telegram** — outside the EEA and outside a jurisdiction the European
  Commission has found adequate. This transfer occurs only if Customer
  elects a Telegram gate, and the transport is inherent to that choice:
  a message sent through Telegram has already passed through Telegram's
  own infrastructure, under Telegram's own privacy policy, before it
  reaches HugiMuni. HugiMuni flags this to Customer's counsel as the
  least tidy transfer in this list, worth an explicit sentence rather
  than folding it into the general clause above.
- **HugiMuni's own infrastructure** — hosted on Upsun/Platform.sh.
  HugiMuni intends this hosting to be pinned to an EU region, but no
  region directive exists in the repository's committed configuration
  (`.upsun/config.yaml`) to verify that from code; this is an operational
  setting to confirm and, if not already so, pin, ahead of relying on
  this clause.

## 15. Precedence

If a provision of this Agreement conflicts with the Terms of Service on a
matter of data protection, this Agreement controls. On every other
matter, the Terms of Service control. This Agreement does not itself
create warranties, liability caps, or other commercial terms; those are
addressed in the Terms of Service.

---

## Annex I — Description of the processing

| | |
|---|---|
| **Controller** | The Customer identified by its brnrd.dev account. |
| **Processor** | HugiMuni SAS, 6 rue de la Verdière, 13200 Arles, France (SIREN 104 156 260, RCS Tarascon). |
| **Subject-matter** | Relay and mirroring of Customer Content in connection with the Service — see §0, §2. |
| **Duration** | For the life of the connected repository/account, or until this Agreement or the Terms end (§2, §12). |
| **Nature of processing** | Automatic store-and-forward relay; verbatim mirroring/caching for dashboard rendering; transient in-memory relay of review packs. No analysis, mining, profiling, or model training on Customer Content. |
| **Purpose of processing** | (1) Message delivery between a Customer-chosen gate and Customer's own daemon. (2) Dashboard rendering for an opted-in account. (3) Review-pack relay for diffense. |
| **Categories of data subjects** | Customer's own personnel/collaborators; third parties whose personal data appears in Customer's repositories or conversations (commit authors, commenters, other chat members). |
| **Categories of personal data** | See §4: relayed message text; message routing metadata (platform ids, usernames, comment URLs); free-form agent prose in mirrored pages; file paths/commit subjects/branch names in the run ledger; review-pack file paths and prose; image-attachment pointers. |
| **Special categories of data** | Not intentionally collected; not technically filterable from free-text Customer Content (§4). |
| **Retention** | Automatic, code-enforced schedule — see Annex II §III. |
| **Competent supervisory authority** | Commission Nationale de l'Informatique et des Libertés (CNIL), France. |

## Annex II — Technical and organisational measures (Article 32)

Each measure below is stated only where it is verifiable in the code as
shipped, with a coordinate. Where a control does not exist, that is
stated too — an inflated Annex II is a liability this document exists to
avoid.

### I. Confidentiality — access and authenticity controls

- **Bearer tokens** (session, API-key, daemon) are stored as a SHA-256
  lookup hash only; the raw token is never persisted and cannot be
  recovered from the database.
  <!-- src/brnrd/security.py:12-14 (hash_token); src/brnrd/models.py:63 (Token.token_hash) -->
- **Session cookies** are `HttpOnly`, `SameSite=Lax`, marked `Secure` when
  served over HTTPS, and expire 30 days after issuance.
  <!-- src/brnrd/routers/web_auth.py:257; SESSION_TTL at src/brnrd/routers/accounts.py:22 -->
- **GitHub webhook payloads** are authenticated via an HMAC-SHA256
  signature comparison before any event is enqueued.
  <!-- src/brnrd/routers/webhooks.py:139-140 -->
- **Telegram webhook requests** are authenticated via a constant-time
  comparison of a shared secret token.
  <!-- src/brnrd/routers/webhooks.py:331 -->
- **The GitHub credential minted for a managed run** is a repository-scoped
  App installation access token, not a broad personal-access token.
  <!-- src/brnrd/platforms/github_app.py:72 -->
  GitHub documents these installation tokens as expiring after 1 hour;
  that expiry is set by GitHub's API, not by brnrd's own code, so it is
  stated here as the platform's behaviour rather than a control brnrd
  itself enforces.
- **No application-level encryption at rest.** brnrd applies no
  additional encryption layer of its own over what its hosting platform
  (Upsun/Platform.sh, PostgreSQL 15) provides natively. A search of
  `src/brnrd/` for encryption/cipher primitives (Fernet, AES, etc.)
  outside the SHA-256 token-hashing above found none. Stated as fact, not
  omitted.

### II. Data minimisation

- **Image attachments** are never stored as bytes server-side: the
  database holds only a pointer (`file_id`, filename, kind), and the
  actual bytes stream from Customer's own daemon on request through a
  dedicated endpoint.
  <!-- pointer fields: src/brnrd/models.py:184-189; streaming endpoint: src/brnrd/routers/daemons.py:541 -->
- **Review packs are never written to the database or to disk.** They
  live only in process memory, behind an unguessable token
  (`secrets.token_urlsafe(24)`), for a bounded TTL (default 3,600
  seconds, configurable via `BRNRD_PACK_RELAY_TTL_S`), and are dropped on
  expiry or process restart.
  <!-- src/brnrd/pack_relay.py:1-20 (module docstring), :39 (default_ttl_s), :47 (token minting); default wired at src/brnrd/config.py:62 -->

### III. Storage limitation — the retention schedule this Annex enforces

| Data | Retention | Coordinate |
|---|---|---|
| Relayed message body | Nulled the moment the reply is delivered; nulled at 14 days if never answered | `inbox.py:311` (null on response), `inbox.py:327` (`_EVENT_BODY_TTL`) |
| Message routing row | Deleted at 90 days from arrival | `inbox.py:328` (`_EVENT_ROW_TTL`), sweep at `inbox.py:338-361` (`gc_events`) |
| Image-attachment pointer | Cleared with the message body, same schedule | `inbox.py:314`, `models.py:188` |
| Session token | Expires 30 days after issuance | `routers/accounts.py:22` (`SESSION_TTL`) |
| Review pack | Held in memory 3,600 seconds by default, then dropped; never persisted | `pack_relay.py:39`, `config.py:62` |
| Pending/running task row | Replaced on every publish tick; dropped after 10 minutes of no report | `activity_records.py:11` (`ACTIVITY_STALE_TTL`) |
| Dashboard corpus mirror | No fixed TTL — replaced wholesale roughly every 3 seconds on publish; deleted in full when the account's last repository disconnects | `routers/_session.py:353-360`; publish cadence per `SECURITY.md` §"What dashboard publishing mirrors" |
| Run pages within the mirror | Customer's own daemon stops mirroring run pages older than 14 days by default (Customer-configurable, `publish.runs_window_days`) | `brr/gates/cloud.py:1194` (`_RUNS_WINDOW_DAYS_DEFAULT`) |

The retention numbers published on HugiMuni's public `/privacy` notice
(companion branch, not yet merged) are drawn from the same six constants
cited above and are checked by a driven test suite that ages real rows
and runs the real deletion sweep rather than asserting literal numbers —
so drift in the code trips that suite before it can silently become a
misstatement on this page.
<!-- tests/test_privacy_notice.py, branch brr/the-legal-pack -->

### IV. Purpose limitation — no secondary use

- **No analytics of any kind.** A search of the backend and frontend
  source for analytics/telemetry SDKs (Mixpanel, Segment, PostHog,
  Google Analytics, Amplitude) found none in use — the only match was the
  unrelated English word "segment" (URL path segments).
- **No advertising, no data broker, no model training.** HugiMuni holds no
  model-provider credential and makes no model call against Customer
  Content (§0); nothing here is used to train a model, by HugiMuni or on
  its behalf.
- **No outbound email service.** A search of the backend for an email
  integration (SMTP, SendGrid, Mailgun, Postmark, etc.) found none;
  HugiMuni sends no email of any kind from this codebase as shipped.

### V. Availability and resilience

Availability is provided by the hosting sub-processor (Annex III);
HugiMuni's own code adds no independent backup or disaster-recovery layer
beyond what that platform provides. This is stated for completeness
rather than asserted as a HugiMuni-side control this Annex can otherwise
evidence.

## Annex III — Sub-processors

The **authoritative, current sub-processor list is the published page at
<https://brnrd.dev/sub-processors>** (see §9 for the notice mechanism); the
table below is a snapshot as of the date of this Agreement and is not
separately maintained. General written authorisation (§9) covers the listed
sub-processors. "Processes" states which role's data each sub-processor
actually touches, so the boundary in §0 stays intact through this table too
— not every entry below handles Customer Content.

| Sub-processor | Role | Location | Transfer basis | Processes |
|---|---|---|---|---|
| **Upsun** (Platform.sh SAS) | Hosting and the managed PostgreSQL database — the sole datastore for everything in Annex I | France (registered office Paris; hosting region intended EU, not pinned in committed config — see §14) | N/A if EU-hosted; to be confirmed | All Customer Content described in Annex I, and all HugiMuni controller-role data |
| **GitHub** | (a) OAuth sign-in identity; (b) delivery of relayed replies to Customer's own issues/PRs, and the webhook source of inbound Customer Content | United States | EU-US Data Privacy Framework / Standard Contractual Clauses | (a) is controller-role account data, out of this Agreement's scope; (b) is Customer Content, in scope |
| **Telegram** | Message transport for Customers who elect a Telegram gate — both inbound and outbound | Outside the EEA / outside an adequacy decision | None stated; transport inherent to Customer's choice of gate (see §14) | Customer Content — applicable only if Customer connects a Telegram gate |
| **Stripe** | Payments, as merchant of record | United States | EU-US Data Privacy Framework / Standard Contractual Clauses | Controller-role billing data only (Customer's email address and brnrd account id); Stripe does not receive Customer Content under this Agreement |

Sub-processor changes are notified per §9.
