# Article 30 GDPR — Record of Processing Activities

**Prepared for:** HugiMuni SAS, France  
**Service:** brnrd.dev  
**Date:** 2026-07-24  

This is the internal record of processing activities maintained under GDPR Article 30. It describes processing carried out by or on behalf of HugiMuni SAS through the brnrd.dev hosted service.

## A. Controller Processing Activities

HugiMuni SAS is the **data controller** for the following processing activities.

| Activity | Purpose | Data Subjects | Personal Data Categories | Recipients/Sub-processors | Transfer Mechanism | Retention | Security Measures |
|----------|---------|---|---|---|---|---|---|
| Account management | Account creation, authentication, identity persistence | Users (developers, individuals) | GitHub user ID, login, email address (nullable), name (from GitHub) | Internal; Upsun/Platform.sh (hosting, FR); GitHub (identity verification, US; DPF/SCCs) | GitHub OAuth 2.0; data stored on Upsun | Account data retained indefinitely until account deletion; no automatic deletion path currently (Art. 17 gap noted) | Password-free via GitHub OAuth; email stored encrypted in database; session tokens SHA-256 hashed (`models.py:53`) |
| Billing and payment | Subscription management, invoice generation, payment processing | Account holders (payers) | Email address, Stripe customer ID, billing records (pricing, subscription status, payment events) | Stripe (payments processor, US; DPF/SCCs; Managed Payments – merchant of record) | Stripe REST API; append-only ledger | Billing records retained per French commercial law (~6 years); Stripe retains per its contract | PCI DSS compliance via Stripe (card data does not touch brnrd.dev); HTTPS encryption; audit logging |
| Terms acceptance | Contract formation, consent records for hosted-execution beta | Account holders | Account ID, timestamp of acceptance, terms version accepted | Internal; Upsun/Platform.sh (hosting, FR) | Stored in `Account` row; version checked on session load (`_session.py:377`) | Until account deletion or terms version revocation | Plain-text record in database; version tracking prevents silent changes |
| Operational telemetry & service diagnostics | System health, daemon connectivity, quota tracking, feature usage | Users (via daemons) | Account ID, daemon runner/quota information, billing figures, timezone, run counts | Internal; Upsun/Platform.sh (hosting, FR) | Stored in `Daemon` model (`models.py:70`); wholesale replaced per publish cycle | Replaced on every publish event (~25s cadence); old records discarded | Database-level access controls |

## B. Processor Processing Activities

HugiMuni SAS is a **data processor** acting on the instructions of user-controllers. Users control the purposes; brnrd relays and stores the content at their direction.

| Activity | Instruction Source | Purpose | Data Subjects | Personal Data Categories | Recipients/Sub-processors | Transfer Mechanism | Retention | Security Measures |
|----------|---|---|---|---|---|---|---|---|
| Message relay (GitHub + Telegram) | User: connects GitHub repositories and/or Telegram bots to daemon | Relay messages between user's chat/issue threads and their locally-running daemon | GitHub: repository collaborators, issue commenters, PR authors; Telegram: chat members | GitHub: author ID, avatar URL, comment text, issue/PR metadata; Telegram: chat ID, user ID, username, message text | GitHub App (US; DPF/SCCs); Telegram bot (UAE; user-elected); Upsun/Platform.sh (hosting, FR) | Events stored in `Event` row (`models.py:158`); body field contains message text | Body nulled on user reply (`inbox.py:311`; cite line 311); never-answered body nulled at 14d (`inbox.py:327` _EVENT_BODY_TTL); entire row deleted at 90d (`inbox.py:328` _EVENT_ROW_TTL) | HTTPS; database encryption at rest (platform-level); access logs |
| Corpus mirror & dashboard publishing | User: configures publish scope for brnrd daemon | Render cache: store verbatim copies of repository pages (code, commit history, issues, etc.) for dashboard display | Repository collaborators, public contributors, any persons named in repo history | Repository content including file paths, code, prose, commit authors, issue commenters, PR authors, any third-party data mirrored in the repo | Upsun/Platform.sh (hosting, FR); GitHub (source pull, read-only API; US; DPF/SCCs) | Stored in `Account.surface_json` field; mirror is replaceable render cache | Mirror emptied when last connected repository is disconnected (`_session.py:350`); user can disable publishing with `publish.layers=none` | HTTPS; database encryption at rest (platform-level); access logs; user control via feature flag |
| Run content (logs, diffs, ephemeral state) | User: approves each run | Store run outputs (card text, summaries, logs, diffs, build artifacts) transiently for dashboard display | Developers (users and their team members with access to runs) | Card text (user-supplied task descriptions, run status), summaries, file paths, code diffs, log output | Upsun/Platform.sh (hosting, FR) | Stored in `Daemon` card_text field; `ActivityRecord` stores summary; review packs in RAM only (`pack_relay.py`) | Replaced/pruned on subsequent publish; review packs RAM-only with 3600s TTL (`config.py:62` pack_relay_ttl_s); `ActivityRecord` pruned on publish; activity data deleted on repo disconnect | HTTPS; database encryption at rest (platform-level); review packs never persisted to disk |
| Router identity routing & channel configuration | User: configures which chat/issue systems route to which daemons | Store mappings between user's external channels (GitHub repos, Telegram chat) and daemon identity (paired_user_id) | Users | Channel IDs, paired user IDs, routing metadata | Upsun/Platform.sh (hosting, FR) | Stored in `ChannelRoute` rows (`models.py:192`) | Deleted on repo disconnect | Database-level access controls |
| Activity record aggregation | System: tracks publishing/query activity for telemetry | Record summary statistics about daemon activity for service diagnostics | Developers (users) | Conversation key, activity summary (prose) | Upsun/Platform.sh (hosting, FR) | Stored in `ActivityRecord` rows (`models.py:128`) | Pruned on publish; deleted on disconnect | Database-level access controls |
| Session token issuance & validation | System: manage authenticated access to dashboard | Issue and validate short-lived access tokens | Account holders using the dashboard | Session token (hashed SHA-256; `models.py:53`), user ID, token creation time | Upsun/Platform.sh (hosting, FR); user's browser (cookie) | Tokens are hashed; cookie has httponly, samesite=lax, secure flags (`web_auth.py:257`); max_age set to SESSION_TTL (30 days, `accounts.py:22`) | 30d session TTL (`accounts.py:22` SESSION_TTL); token deleted on logout | HTTPS-only cookies; SHA-256 hash; httponly + secure flags prevent XSS exfiltration |

## C. Sub-processor Details

| Name | Role | Jurisdiction | Legal Basis for Transfer | DPA/Contract |
|------|------|---|---|---|
| Upsun / Platform.sh | Cloud hosting provider | France (EU) | Data localized within EU; no international transfer | Platform.sh Terms of Service; Uptime SLA |
| Stripe | Payment processor | United States | Data Protection Framework (DPF); Standard Contractual Clauses (SCCs) | Stripe Data Processing Addendum |
| GitHub | Identity provider + API partner | United States | Data Protection Framework (DPF); Standard Contractual Clauses (SCCs) | GitHub App terms; GitHub Enterprise Agreement (if applicable) |
| Telegram | Message transport (user-elected) | United Arab Emirates | User explicitly elects Telegram channel; message transport inherent to service selection | Telegram Terms of Service and Privacy Policy (user responsible) |

## D. Data Subject Rights

Data subjects may exercise the following rights:

- **Access (Art. 15)**: Request copies of personal data by writing security@hugimuni.fr
- **Rectification (Art. 16)**: Incorrect email or GitHub identity can be updated via dashboard settings or by request to security@hugimuni.fr
- **Erasure (Art. 17)**: Account deletion is not yet available via self-service. Erasure requests should be directed to security@hugimuni.fr. Repo-scoped data (ChannelRoute, mirror, ActivityRecord) is automatically deleted on repo disconnect.
- **Restriction (Art. 18)**: By request to security@hugimuni.fr
- **Portability (Art. 20)**: By request to security@hugimuni.fr
- **Objection (Art. 21)**: By request to security@hugimuni.fr

## E. Data Security Measures

- **Encryption in transit**: All traffic over HTTPS; TLS 1.2 minimum
- **Encryption at rest**: PostgreSQL database on Upsun/Platform.sh with platform-level encryption
- **Access controls**: Role-based access to production database; audit logging
- **Authentication**: GitHub OAuth 2.0 for user identity; session tokens are SHA-256 hashed
- **Tokenization**: Session tokens hashed; card data never stored (Stripe Managed Payments)
- **Incident response**: Contact security@hugimuni.fr for breach reports; internal 72h notification SLA (Art. 33, GDPR)

## F. Notes and Gaps

1. **Account deletion path (Art. 17 gap)**: No self-service deletion endpoint currently exists. Manual process via security@hugimuni.fr is documented in privacy notice but should be formalized in runbook.
2. **Telegram transfer basis**: Transfer to UAE (Telegram's servers) occurs only when user explicitly elects Telegram as an output channel. This is a user-directed transfer, documented in privacy notice.
3. **Review scope**: This record reflects the code state as of 2026-07-24 (commit aaca33d6). Code changes affecting retention times, recipients, or data flows should trigger a review and update of this record.

---

**Prepared by**: Automated analysis  
**Last reviewed**: 2026-07-24  
**Next review**: Upon code changes affecting data handling or sub-processor list
