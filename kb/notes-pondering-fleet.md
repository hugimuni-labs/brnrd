# Notes: Fleet, Managed Mode & Steering — open pondering

**Status: paused (older strands), active capture for managed mode.**
Companion to [`subject-fleet-overlays.md`](subject-fleet-overlays.md)
and [`deck-brr-fleet-steering.md`](deck-brr-fleet-steering.md). The
overlay and `brnrd` strands remain paused behind the active env axis;
the new dominant pondering strand is **managed mode** (hosted gates +
cloud execution backends), driven by two concrete forces:

- Real user need today: running brr jobs while the laptop is down,
  which the local-daemon-only shape does not cover.
- Adoption / sustainability: shipping the first paid tier at the same
  time as the public release, so early adopters see the OSS / paid
  split as the deal they signed up for rather than as something that
  appeared after they invested.

The page is still capture-only — nothing here is committed. When any
of the strands crystallises into something actionable, promote it
into a design or plan page and link from
[`kb/index.md`](index.md).

Reading order: §1 (managed-mode synthesis), §2 (cloud execution
platform research) carry the new thinking. §3-§5 are the older
overlay / registry / brnrd / supervisor strands, lightly updated to
the current state. §6 is the re-promotion guide.

---

## 1. Managed mode — the two dimensions

> **PROMOTED on 2026-05-22 — see
> [`subject-managed-mode.md`](subject-managed-mode.md) (hub),
> [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
> (locked protocol; originally `design-managed-gates.md`,
> renamed once when spawn-compute joined its scope, and again
> on 2026-05-25 when the hosted product settled on the brnrd
> name), and the plan and decision
> pages
> ([`plan-managed-gates-launch.md`](plan-managed-gates-launch.md),
> [`plan-failover-compute.md`](plan-failover-compute.md),
> [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md),
> [`plan-env-fly-machines.md`](plan-env-fly-machines.md),
> [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md),
> [`decision-pricing-shape.md`](decision-pricing-shape.md),
> [`decision-connectors-layering.md`](decision-connectors-layering.md),
> [`decision-monorepo-structure.md`](decision-monorepo-structure.md)).
> Body below retained as provenance — the agreed shape lives in the
> promoted pages.**
>
> **2026-05-22 reframe.** The original two-dimension synthesis
> below treated "where does the daemon live" as an orthogonal
> concern and positioned an always-on-host as the *preferred BYO
> answer to laptop-down dispatch* (see also §4 + §1.3 below). On
> reflection that strayed from the work-continuity pitch — making
> the user operate a third thing for what is mostly a paper
> benefit (30% utilisation at 100% cost). The current shape
> instead frames brnrd itself as the always-on dispatcher:
> laptop online → forward to laptop; laptop offline AND failover
> enabled → brnrd spawns a per-task ephemeral sandbox in the
> user's cloud (BYO) or its own (managed compute), execute the
> task, push the branch home, tear the sandbox down. The
> always-on-host model survives as a niche path for cloud-first
> users only. Surfaces A / B / C (managed gates, BYO failover
> compute, managed compute) all ride the same dispatcher; see
> [`subject-managed-mode.md`](subject-managed-mode.md) for the
> current synthesis.
>
> **2026-05-25 reframe — second pass.** Shape reworked again
> after a deeper pass on what brnrd actually has to do at
> launch vs what's actually defensible to ship:
>
> - **BYO compute (Surface B) deferred from launch.** The wire
>   protocol still supports it (preserved in
>   [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
>   "BYO compute — designed, deferred"), but the
>   per-platform credential storage UI, per-platform onboarding
>   docs, dispatcher branching, and partial-support-matrix
>   maintenance burden didn't justify shipping it day one for
>   the ~5% of launch users who'd care. Add-back is small when
>   usage justifies; daemon-side cloud-runner adapters (laptop
>   fans out to user's cloud via a `brr-env-*` plugin) remain
>   independent of managed mode entirely.
> - **One product, one name.** brnrd was useful when we thought
>   it was a separate operator-agent product; once it collapsed
>   into brr.run (the dashboard angle of the same product), one
>   name beat two. At this point of pass 2 we picked `brr.run`
>   as the kept name (concrete domain, descriptive); the pass-3
>   breadcrumb below flipped this to `brnrd` on cost-and-brand
>   grounds — the rest of the pass-2 logic stands.
> - **Multi-project routing protocol added.** One managed bot
>   per platform serves all of a user's projects via
>   chat-binding + per-message prefix override (for TG/Slack/
>   Discord) or repo-binding (for GH). Spec in
>   [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
>   "Multi-project routing"; UX integration in
>   [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
>   Slice 2.
> - **Permission-prompt API added.** Cost-transparency before
>   each failover spawn: prompt via the gate carries est cost,
>   est runtime, current-month usage, two action buttons
>   (Approve / Queue), optional "Never ask under $X." Mode
>   defaults to `ask`. Spec in
>   [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
>   "Permission-prompt endpoints"; integration in
>   [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
>   Slice 3.
> - **AI-credential vault preserved across both shapes.** Both
>   API-key and credential-directory-tarball payload shapes
>   accepted on the same `POST /v1/accounts/ai-credentials`
>   endpoint, so subscription-auth users (Claude Pro, Codex
>   Plus, Gemini OAuth) get failover without provisioning API
>   keys — same UX as the local docker env's mounted-dir flow.
> - **Free-tier failover spawn cap revised down: 100/month**
>   (was 200). The cap is framed as a fallback feature, not a
>   free continuous-execution SaaS. Math at ~$0.28/user/month
>   worst-case cloud cost makes this sustainable with a small
>   percentage of paying users on top. See
>   [`decision-pricing-shape.md`](decision-pricing-shape.md).
> - **Data minimization principle promoted to load-bearing**
>   across the design and pricing. brnrd is a thin
>   dispatcher + a credential vault; user content (prompts,
>   code, responses, conversation history, repo state) lives
>   on the daemon side and is never mirrored to brnrd. Event
>   bodies dropped after dispatch; response bodies pass through
>   without storage; AI credentials encrypted at rest with
>   per-account envelope keys; audit log metadata-only. Trust
>   signal on the pricing page is "we don't have your code."
>   Full principle in
>   [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
>   "Data minimization."
> - **Monorepo structure decided.** brr core + brnrd backend +
>   dashboard + first-party plugins live in `src/brr/`,
>   `src/brnrd/`, `src/brnrd_web/`, `src/brr_env_*/` in one
>   monorepo, sharing the kb. Plugin packages split into their
>   own repos when they mature. Decision in
>   [`decision-monorepo-structure.md`](decision-monorepo-structure.md).
> - **Gates vs connectors split named.** Gates are
>   per-project / inbound (existing shape); connectors are
>   per-account / outbound / proactive (for the future
>   agentic-secretary layer). No connectors ship at launch; the
>   split lives in
>   [`decision-connectors-layering.md`](decision-connectors-layering.md)
>   so the future agentic-mode upgrade doesn't have to retrofit
>   the gate API.
> - **Upsun is the prototype hosting environment** for the
>   brnrd backend; read-only-app-container constraints
>   handled via the build-vs-deploy split, declared writable
>   mounts, postgres add-on, and Upsun-secret-store for the
>   pool tokens. Spec in
>   [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
>   "Upsun deployment notes."
>
> Net effect: the launch shape is **two surfaces** (free
> dispatcher inc. 100 managed-compute spawns/month, plus
> usage-based managed compute over cap) on a **thin** brnrd
> (data minimization), hosted on **Upsun**, exposing a
> **dashboard MVP**, with **multi-project routing** + **cost-
> transparent permission prompts** baked in, and a **monorepo**
> layout that keeps brr core / backend / dashboard / plugins
> coherent. See [`subject-managed-mode.md`](subject-managed-mode.md)
> for the current synthesis.
>
> **2026-05-25 reframe — third pass.** Two more revisions
> after surveying domain economics and confronting the
> "conversation history" gap honestly:
>
> - **brnrd kept as the name; canonical domain `brnrd.dev`.**
>   Pass 2 had retired `brnrd` in favour of `brr.run` ("one
>   product, one name; brr.run wins"). Once domain costs
>   surfaced (`brr.run` runs ~$120/yr as a premium domain;
>   `brnrd.dev` ~$15/yr), the brand-asset value of the
>   `brr → brnrd → ⟍brr` reflection-palindrome animation, and
>   the sibling-name fit with "brr", the choice flipped:
>   `brnrd` is the product name, `brnrd.dev` is the canonical
>   domain. The pass-2 reasoning about *collapsing two names
>   into one* still stands — only which name survives changed.
>   The hub's "brnrd as the product" section carries the
>   updated framing.
> - **Cross-gate conversation context via metadata-only graph
>   + on-demand fetch.** Pass 2 left conversation history as
>   "lives on the daemon; gone when daemon is offline" which
>   loses cross-gate continuity (e.g., user asked about a
>   deploy in TG; later commented on the resulting GH PR;
>   failover spawn would see only the GH PR). Pass 3 closes
>   this with a metadata-only graph on brnrd
>   (`event_id ↔ conversation_id ↔ branch_name`, no body,
>   30-day TTL; conversation_id sourced from a
>   `Brnrd-Conversation-Id` git commit trailer the daemon
>   writes, plus the response POST), three-source spawn-context
>   assembly (originating event + gate-side history fetch from
>   the platform's own API + git log replay), and one named
>   concession — Telegram's Bot API has no retroactive history,
>   so brnrd holds a per-chat ring buffer (50 msgs × 72h,
>   encrypted, audited, drops on `/disconnect`). Slack /
>   Discord don't need a ring buffer; their APIs expose history
>   natively.
> - **"What we DO hold" promoted to a load-bearing
>   subsection** of the data-minimization principle, with every
>   persistent surface listed (scope + TTL + reason). The
>   conversation graph and TG ring buffer are named there, not
>   hidden. Trust signal stays "we don't have your code" with
>   the held surfaces explicit on the pricing page.
> - **New small plan: daemon-side conversation_id propagation.**
>   `Brnrd-Conversation-Id` git commit trailer + conversation_id
>   field on the response POST. ~80 LOC daemon-side; gates
>   brnrd's metadata-graph machinery from being meaningful in
>   practice. See
>   [`plan-conversation-id-propagation.md`](plan-conversation-id-propagation.md).
>
> Net effect after pass 3: same two-surface launch shape; the
> hosted product is `brnrd` at `brnrd.dev`; cross-gate
> continuity for failover is preserved without brnrd holding
> conversation contents (only the metadata table-of-contents
> graph + TG ring buffer). See
> [`subject-managed-mode.md`](subject-managed-mode.md) for the
> current synthesis.
>
> **2026-05-25 reframe — fourth pass.** Five concrete
> mechanics shifts after the user surfaced specific
> productisation gaps (legal entity ready in France; envs vs
> cloud-runners architectural mismatch; plugin-packaging
> over-engineering; CLI verb taxonomy; cross-platform
> daemoning; self-hosting friction):
>
> - **Credits-wallet billing adopted** (companion design page:
>   `design-billing.md`). One credit = $0.01; top-up via Stripe
>   Checkout (no card-on-file by default); debit at spawn-
>   finalize; opt-in auto-topup; pro-rata refund on unused paid
>   credits within 30 days; free-tier monthly grant of ~300
>   credits (≈100 worst-case spawns). Stripe France handles the
>   payment + Stripe Tax for EU VAT; payouts to Qonto. HugiMuni
>   SAS as the legal entity. Pricing decision page updated to
>   reflect the wallet model; "no card-on-file by default" added
>   as the fourth trust signal on the pricing page.
> - **Plugin packaging collapsed: extras over separate pypi
>   names.** `brr-env-fly-machines` (separate pypi) dropped in
>   favour of `pip install brr[fly]` (extras-gated env in
>   `src/brr/envs/fly_machines/`). Single version surface, one
>   repo, simpler discovery. Third-party envs still use the
>   `brr.envs` entry-point mechanism. First-party envs can split
>   out later via the same entry-point path if their cadence /
>   user base diverges. Monorepo decision page reshaped; env
>   interface page got a clarifying "first-party (extras)
>   vs third-party (entry points)" subsection.
> - **Cloud runs ARE envs — full unification.** Dropped the
>   separate "cloud-runner adapter" framing; cloud envs
>   implement the existing `EnvBackend` Protocol like every
>   other env. The brnrd backend invokes the same env class
>   the daemon would use; brnrd just does a daemon-equivalent
>   bootstrap (clone repo with per-spawn GH App token,
>   materialise AI creds, construct a `RunContext`) before
>   calling `envs.get_env("fly_machines")`. One implementation,
>   two callers. `research-cloud-runner-patterns.md` renamed
>   to `research-cloud-envs.md` and reframed accordingly;
>   `design-env-interface.md` grew a "brnrd server-side caller"
>   subsection; `plan-env-fly-machines.md` reshaped from
>   "first plugin package" to "first cloud env (extras-gated)";
>   `design-brnrd-protocol.md` dropped the cloud-runner-adapter
>   framing in its spawn step + BYO-deferred section.
> - **CLI shape decision page added** (`decision-cli-shape.md`).
>   Six top-level verbs (`init` / `run` / `daemon` / `gate` /
>   `brnrd` / `config`); collapses today's `up` / `down` into
>   `brr daemon up|down|status`; collapses today's `auth` /
>   `bind` / `setup` into `brr gate <name> <verb>`; adds
>   `brr brnrd <subcommand>` namespace for hosted-service
>   management (`connect` / `creds` / `policy` / `topup` /
>   `balance` / `projects` / ...); adds `brr config
>   list|get|set|doc` for parameter introspection across local
>   + remote. `brr accounts` (placeholder in earlier drafts)
>   dropped. `brr brnrd connect [url]` defaults to
>   `https://brnrd.dev` and accepts any URL — self-hosting is a
>   first-class path with no extra CLI hoops (deployment
>   friction is its own friction; CLI shouldn't add to it).
> - **Cross-platform daemoning tracked at issue #29.** Managed
>   mode reduces the urgency (failover compute covers gaps when
>   the daemon isn't running); the systemd-first track at #29
>   proceeds independently of the deployment-templates plan
>   here. `plan-daemon-deployment-templates.md` got a small
>   cross-reference; no new architectural commitment.
>
> Net effect after pass 4: launch shape settled with concrete
> billing mechanics (credit wallet + Stripe + HugiMuni SAS),
> envs and cloud-runners unified into one architectural concept
> (envs that happen to run remotely), packaging simplified to
> extras, CLI reshaped to a 6-verb noun-first taxonomy, and
> the laptop-side daemoning roadmap pointed at #29. See
> [`subject-managed-mode.md`](subject-managed-mode.md) for the
> current synthesis;
> [`decision-cli-shape.md`](decision-cli-shape.md),
> [`design-billing.md`](design-billing.md),
> [`research-cloud-envs.md`](research-cloud-envs.md) for the
> three new / reshaped page focuses this pass.
>
> **2026-05-25 follow-up to pass 4.** Two narrow shape
> clarifications after the user read through:
>
> - **`brr brnrd connect` becomes a three-layer smart bootstrap**
>   (account-pair → project-create → gate-pair), not just
>   account-pair. Each layer idempotent + skippable if already
>   done; Layer 3 gate-pair fires via mechanical detectors
>   (`git remote get-url origin` for GH; existing `.brr/config`
>   for TG). Each layer is also a standalone verb (`brr brnrd
>   pair <gate>`, etc.); the walkthrough sequences existing
>   code paths, doesn't invent verbs. Non-interactive flags
>   for scripts (`--account-only`, `--no-auto-pair`, `--pair`,
>   `--yes`, `--project`). Details in
>   [`decision-cli-shape.md`](decision-cli-shape.md) →
>   "three-layer smart bootstrap" + protocol-side endpoints in
>   [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
>   "Pairing flow."
> - **Stripe EU specifics formalised** in
>   [`design-billing.md`](design-billing.md). Five things to
>   enable that most independent vendors miss: SCA via
>   Checkout (zero code), Stripe Tax add-on (0.5%/txn,
>   mandatory for VAT calc), OSS scheme registration via
>   DGFiP (not optional for cross-EU digital services),
>   EU-local payment methods toggled day-one (SEPA / iDEAL /
>   Bancontact / EPS / Giropay / P24 / Apple-Google Pay), and
>   TVA intracommunautaire on B2B invoices. Headline 30-50%
>   managed-compute margin lands at **27-47% net of Stripe +
>   Stripe Tax** with worked examples for French card vs
>   German SEPA.
>
> Three pages updated; no new pages. The substantive launch
> shape from pass 4 is unchanged; this is purely
> implementation-detail surfacing.
>
> **2026-05-25 pass-4 follow-up — second wave.** Three
> substantive additions in one pass after the user re-raised
> three orthogonal concerns while reviewing the pass-4 result:
> cross-platform daemoning, kb maintenance for non-brr agents,
> and config visibility + sync to brnrd-side spawns.
>
> - **Seventh top-level CLI verb `brr kb`** (six sub-verbs:
>   `status` / `pages [filters]` / `proposed` / `log` / `check`
>   / `doc`, all with `--json`). Same surface for users (who
>   get "what needs my review?") and non-brr agents (who get
>   structured kb health). Addresses
>   [#41](https://github.com/Gurio/brr/issues/41). Bends the
>   "six minimal verbs" promise by one; justified because kb
>   is half the project's identity and burying it under
>   `config` is friction every time. Details in
>   [`plan-kb-subcommand.md`](plan-kb-subcommand.md) and
>   [`decision-cli-shape.md`](decision-cli-shape.md).
> - **`brr daemon install | uninstall | logs`** for
>   cross-platform laptop daemoning. Linux: systemd user unit
>   at `~/.config/systemd/user/brr.service` + optional one-time
>   `loginctl enable-linger`. macOS: launchd LaunchAgent at
>   `~/Library/LaunchAgents/dev.brnrd.brr.plist`. Per-user
>   (no sudo for the unit file itself); survives reboot;
>   integrates with the OS's logging / status / restart
>   mechanisms. Falls back to today's foreground supervisor
>   when not installed. Windows deferred. Details in
>   [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md);
>   tracked at [#29](https://github.com/Gurio/brr/issues/29).
> - **Three-scope config model** (`project` / `local` /
>   `account`) replaces the single gitignored `.brr/config`.
>   `brr.toml` at repo root (committed) carries project-scope
>   settings; `.brr/config` (TOML now) stays gitignored for
>   local overrides; account-scope lives on brnrd via new
>   `/v1/accounts/settings` endpoints. Merge precedence
>   `local > project > account > defaults`. Per-key schema
>   declares scope; `brr config template | validate` round out
>   the existing list/get/set/doc verbs. **brnrd-side spawn
>   bootstrap now reads `brr.toml`** from the cloned repo,
>   layered with account-scope settings — project preferences
>   (Docker image, runner choice, env default) flow to spawns
>   automatically, no protocol push. The repo IS the message.
>   Private docker images flagged as launch-blocker for the
>   spawn path (clear gate-side error); generic credential-
>   vault extension deferred. Details in
>   [`design-config-layout.md`](design-config-layout.md) and
>   [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
>   "Account-scope settings endpoints" + "Failover dispatch"
>   step 6.
> - **BYO cloud env vs managed compute clarified** in
>   `subject-managed-mode.md` as orthogonal coexisting paths
>   (table comparing caller / cloud account / when it fires /
>   who pays). Same env class serves both callers per the
>   envs unification; the routes never compete because their
>   trigger conditions are mutually exclusive.
>
> Three new pages, six updates. Same pattern as the first
> wave: no code; all designs and plans remain
> `Status: proposed`. Implementation order suggested by the
> page set: config-layout first (unlocks brnrd-side preference
> reading), then `brr kb` (highest agent-experience leverage,
> lowest coupling), then `brr daemon install` (can ship
> anytime, no upstream dependencies).
>
> **2026-05-25 pass-4 follow-up — third wave.** Pricing
> reframe + credential vault generalisation, driven by two
> threads of user feedback that landed at the same time:
> "actually want the [private] images and also the credential
> dir mounting (stored encrypted as we discussed)" and "the
> current pricing won't make this project successful — we
> need to reframe it slightly to be more coherent, more
> sustainable, yet still ideally friendly."
>
> - **Pricing reframe**: the "free dispatcher + paid managed
>   compute (credits)" shape was self-defeating — active users
>   wouldn't hit the compute cap, casual users wouldn't hit
>   anything, nobody would pay, project starves. Adopted
>   **subscription for the platform + metered credits for
>   compute**: Free (1 project, 100 events/month, 5 credits) +
>   **Brnrd Plus $9/month** (up to 10 projects, 10K events/month,
>   500 credits included, full dashboard, 90-day audit) +
>   metered compute overage via existing wallet. Multi-project
>   routing is the load-bearing premium gate (honest because
>   multi-project genuinely has more implementation + support
>   surface than single-project); Plus also bundles the full
>   dashboard, 90-day audit, and email support. Self-hosted
>   brnrd stays always-free with full feature parity. Event
>   overages on either tier are soft-throttle + notify, not
>   metered. Per-seat team tier deferred to v-next. Details in
>   [`decision-pricing-shape.md`](decision-pricing-shape.md).
> - **Plus subscription billing leg** added alongside the
>   credit wallet: Stripe recurring subscription (monthly +
>   annual variants), Customer Portal for self-service,
>   prorated upgrade, cancel-at-period-end downgrade, dunning
>   grace, monthly Plus credit grant (500 credits, expires
>   end-of-month). EU compliance machinery (Stripe France,
>   HugiMuni SAS, Qonto, Stripe Tax + OSS, SCA via Checkout)
>   applies to the subscription product identically. New
>   `/v1/accounts/subscription` endpoint family; subscription
>   state mirrored to account-scope settings as
>   `subscription.tier` for in-band reads by daemon +
>   dispatcher. Details in
>   [`design-billing.md`](design-billing.md) and
>   [`design-brnrd-protocol.md`](design-brnrd-protocol.md).
> - **Credential vault generalised**: `/v1/accounts/
>   ai-credentials` → `/v1/accounts/credentials` with a `kind`
>   discriminator covering both AI-runner credentials
>   (Anthropic / OpenAI / Google / GitHub — preserving the
>   `dir-tarball` shape for Claude Pro / Codex Plus / Gemini
>   OAuth) AND docker-registry credentials (ghcr.io /
>   docker.io / etc.). Same encryption, same audit, same
>   revoke. Failover dispatch step 6 now performs `docker
>   login` before `docker pull` for private images —
>   **resolves the "private image launch-blocker" open
>   question** that the second wave deferred. Registry creds
>   live only in the build worker's `~/.docker/config.json`
>   for the spawn's duration; sandbox itself never sees them.
> - **`brr brnrd plus` sub-verb family** added to the CLI
>   (`status | upgrade | downgrade | resume | portal`) wrapping
>   the new subscription endpoints. `brr brnrd creds add`
>   accepts both AI-runner kinds and `docker-registry`.
>   Seven-verb top-level taxonomy unchanged.
>
> No new pages; six pages modified
> (`decision-pricing-shape.md`, `design-billing.md`,
> `design-brnrd-protocol.md`, `decision-cli-shape.md`,
> `design-config-layout.md`, `subject-managed-mode.md`), plus
> index + log + this breadcrumb. All designs remain
> `Status: proposed`. Implementation order: credential vault
> generalisation first (smallest extension; unlocks private
> images at launch), then Plus subscription endpoints + Stripe
> product setup (largest piece; unblocks revenue model), then
> `brr brnrd plus` CLI verbs (thin wrapper over the
> endpoints).

> **2026-05-26 — third-wave refinement (naming + pricing).**
> User pushback on the just-proposed third-wave shape: "I
> don't like the plus as a name for the subscription and
> neither as a subcommand verb; we could offer the
> subscription even at 5 a month, and give the fallback
> compute credits to make up for it; I'm not sure we want to
> limit to 1 project — maybe a properly tweaked Free tier
> limit will do the job for a real hobbyist."
>
> - **Subscription tier deliberately unnamed.** No "Plus" /
>   "Pro" / "Premium" branding; UI + docs say "Subscribed"
>   / "Subscriber" / "Subscription tier." A brand name can
>   be retro-fitted post-launch with market data; un-naming
>   a launched tier is painful.
> - **Subscription price set to $5/month** ($50/year, ~17%
>   off) — was $9 in the third-wave draft. Sub-$5 threshold
>   bias toward conversion volume vs sub-$10 "is this worth
>   it?" friction.
> - **Included compute set to 300 credits/month** ($3 of
>   compute) — was 500 in the third-wave draft. Leaves $2/mo
>   true platform-fee headroom over included compute.
> - **Free tier project cap raised from 1 → 3** for
>   community reception. 1-project Free reads as "trial
>   mode" (HN / dev-twitter audience); 3 captures the "side
>   project + day-job + scratchpad" hobbyist cleanly; the
>   "generous-but-bounded Free" pattern Plausible / Supabase
>   / PostHog / Cal.com all use earned their adoption from
>   that posture. Subscription cap unchanged at 10
>   (still 3.3× headroom + the rest of the bundle).
> - **CLI verb family renamed.** `brr brnrd plus
>   [status|upgrade|downgrade|resume|portal]` → noun-first
>   `brr brnrd subscription [status|start|cancel|resume|
>   portal]` + `brr brnrd subscribe` as a shortcut for
>   `subscription start`. `upgrade` → `start`, `downgrade`
>   → `cancel`.
> - **Subscription state value names finalised.** Tier value
>   `plus` → `subscribed`, `plus_past_due` →
>   `subscribed_past_due`, plan codes `plus_monthly` /
>   `plus_annual` → `monthly` / `annual`, wallet sub-bucket
>   `plus_monthly` → `subscriber_monthly`.
>
> Nine pages refined (pricing-shape, billing, brnrd-
> protocol, cli-shape, config-layout, managed-mode subject,
> failover-compute plan, managed-gates-launch plan, index),
> plus log + this breadcrumb. Implementation order from the
> third wave still holds; this pass refines externally-
> visible surfaces (price, name, project cap) without
> touching the implementation surface — vault, endpoints,
> Stripe product, dispatcher are all the same shape; only
> labels + numbers + a few enum values changed.

> **2026-05-26 — locking pass: licensing + competitive-
> defense posture.** User asked: "yeah lets add a few notes
> to lock it. 5 for early adopters (six seven :D for the
> afterparty) sounds great. the license also is a right
> thing. don't have money on the trademark yet, but we need
> to have it as a prio post launch." Driven by the question
> "what stops a competitor from cloning the OSS and
> undercutting us at $4?"
>
> New page: **`kb/decision-licensing-and-defense.md`**.
> Locks three concrete moves into canonical, defensible form:
>
> - **License split**: `src/brr/` (daemon) stays **MIT** —
>   maximum community goodwill, fork freely. `src/brnrd/`
>   + `src/brnrd_web/` (backend + dashboard) ship
>   **AGPLv3** — neutralises the "Big Cloud rehosts our
>   OSS as a competing managed service" attack while
>   keeping self-hosters fully unaffected (running
>   unmodified brnrd has no AGPL obligations beyond
>   copyright notice + source availability, which we
>   publish ourselves). AGPL chosen over BUSL / ELv2 / SSPL
>   specifically because it preserves OSI-approved status
>   + community trust + protects against the realistic
>   attacker. Per-package `LICENSE` files; the package
>   boundary from `decision-monorepo-structure.md` doubles
>   as the license boundary.
> - **Early-adopter pricing**: first **200 subscribers
>   at $5 / month**, **grandfathered forever** on Stripe
>   (existing subs never migrate `Price` IDs), then **$7 /
>   month** for the public cohort (joiners after the 200th
>   sub OR after launch+12-months, whichever first).
>   Annual variants $50 / $70 (~17% off in both phases).
>   Two `Price` IDs on one Stripe Product; atomic counter
>   on brnrd gates the supporter boundary. The user's "5
>   for early adopters, six seven for the afterparty"
>   framing in canonical form. Loyalty + long-tail revenue
>   headroom in one step (adds ~$600/mo at 500 subs,
>   ~$1,600/mo at 1,000 subs vs an all-supporter universe).
> - **Trademark `brr` + `brnrd`**: deferred at launch for
>   budget reasons (€800-1500 via EUIPO through HugiMuni
>   SAS / French IP lawyer; classes 9 + 42). Post-launch
>   priority with explicit trigger: **first of**
>   launch+12-months OR first €10K cumulative revenue OR
>   first observed competitor. Single highest-leverage
>   defensive move per euro spent; only #3 in priority due
>   to budget timing.
>
> Explicit anti-patterns named: don't go BUSL / ELv2 /
> SSPL (community-goodwill cost > defense gain at our
> scale); don't gate any feature behind hosted-only (breaks
> the always-free-self-host promise); don't race to bottom
> on price; don't pre-buy defensive look-alike domains
> (trademark + UDRP procedure covers the actual attack
> pattern at lower ongoing cost); don't require a CLA at
> launch.
>
> Pages modified beyond the new file:
> `decision-pricing-shape.md` (tier table shows both
> `Price` variants + new "Early-adopter price step" section
> + sustainability math re-run with blended pricing),
> `decision-monorepo-structure.md` (new "License boundary
> aligns with the package boundary" section), `index.md`
> (pricing-shape blurb updated + new
> licensing-and-defense entry + monorepo license-boundary
> callout), `log.md`, this breadcrumb. Implementation
> impact at launch is small (top-level `LICENSE-OVERVIEW.md`
> + per-package `LICENSE` files ~30min with the monorepo
> restructuring PR; two Stripe `Price` IDs + atomic
> supporter counter ~half-day during Stripe product setup;
> trademark is post-launch). The defensive posture is
> overwhelmingly already-built; this pass just locks the
> implicit moves into explicit, defensible form before
> launch reveals them to the world.

> **2026-05-26 — locking pass: BYO-for-subscribers +
> credit-bucket / per-source expiry policy.** User asked:
> "we probably also gonna have to expire granted credits
> somehow, unless you think it would be perceived
> negatively — what's the right shape?" plus "agree on no
> BYO for Free + per-paying-customer language" plus "yes I
> want to lock this in." Driven by the realisation that the
> "BYO deferred forever" working assumption inherited from
> earlier reframes was inconsistent with the "open and
> honest" trust posture the $5/$7 community-trust moat
> requires.
>
> **BYO-everything-for-subscribers.** BYO compute is no
> longer a separately-deferred surface — it's a subscriber-
> opt-in sub-option of Surface B that **parallel-ships with
> managed support for each cloud env**, one-for-one. At
> launch only Fly Machines ships managed → only BYO Fly
> ships at launch. Each subsequent managed cloud (Modal /
> Daytona / Codespaces / …) unlocks BYO for that env in the
> same release. Free stays managed-only on purpose: BYO is
> structurally a cost-saving feature, subscribing is the
> cost-saving move, Free's role is "try it without setup
> friction." The credential vault grows a third `kind`
> (`cloud-platform` with a `provider` discriminator);
> writes + reads gate on `subscription.tier == "subscribed"`.
> The dispatcher branches on BYO-cred presence at dispatch
> time (same env class, two callers per the "Caller axis"
> pattern). Same BYO-for-subscribers principle pre-applies
> to future agentic-secretary connectors via the same
> `credentials` table, different `kind`. Anti-pattern "don't
> lock subscribers into brnrd's cloud" promoted to load-
> bearing in `decision-licensing-and-defense.md`; BYO-
> everything-for-subscribers added as a fifth adjacent
> defense move (a competing fork can't out-open us without
> giving up revenue their model can't bear).
>
> **Credit buckets formalised** with per-source expiry —
> the "temporal grouped resources" abstraction solved with
> the standard bucketed-ledger shape (OpenAI / Anthropic /
> AWS / GCP / Stripe Customer Balance pattern). Four
> buckets: `free_monthly` + `subscriber_monthly` (use-it-or-
> lose-it at cycle boundary; Free is activity-gated —
> dormant Free accounts don't accumulate grant cost),
> `purchased` (never expires, account-dormancy bounded at
> 24mo pause / 36mo prompt / deletion only on explicit user
> request or GDPR), `promotional` (future-proofing for
> signup bonuses / referrals / support-issued goodwill,
> per-grant `expires_at`). Debit priority: grants first
> (soonest-expiring within grants), `purchased` last (FIFO).
> Sub-bucket name `paid` → `purchased` everywhere (audit
> ops, debit-spawn `sub_bucket`, refund op). Dashboard never
> says "credits expired" — says "your monthly allowance
> refreshes on &lt;date&gt;"; same mechanic, opposite
> emotional valence. "Reimbursement" framing on the
> subscriber grant explicitly rejected in favour of "$5
> platform fee + $3 of bundled compute on the house" — the
> grant is a grant, not a refund. Open-question entry on
> Free-grant-size-at-scale (5 credits × 100K Free accounts
> = $5K/mo of compute) added with the knobs (tighten grant
> / convert to one-time signup / accept as CAC).
>
> **Why this matters.** The pricing-shape doc already
> hinted at BYO + grants in passing, but the inconsistencies
> were sneaking in: "BYO deferred forever" vs. "open and
> honest" trust posture; "subscriber gets 300 credits
> reimbursed" vs. "$5/mo for the platform + $3 of bundled
> compute" framings competing; "purchased credits never
> expire" vs. unbounded dormant-account liability tail. This
> pass reconciles by tying BYO 1:1 to managed support per
> cloud (so the per-cloud cost stays small), by formalising
> the bucketed ledger with explicit per-source expiry (so
> the dashboard / ledger / refund policy / audit log all
> agree on what each credit is), and by bounding the
> dormant-account tail with an account-dormancy state
> machine separate from the ledger (so the "purchased
> credits are immortal data" property stays clean and
> auditable from the schema alone).
>
> Pages updated: `decision-pricing-shape.md` (new "Compute:
> managed vs BYO" section + "Credit buckets and expiry
> policy" subsection + BYO section reframed + open
> questions extended); `design-billing.md` (new "Credit
> buckets and expiry policy" section subsumes prior
> "Monthly credit grants"; audit ops renamed; "BYO-compute
> spawns — wallet bypass" section added); `design-brnrd-
> protocol.md` (third credential domain `cloud-platform`
> with subscriber gate; "BYO compute — designed, deferred"
> section rewritten as "BYO compute — subscriber feature,
> parallel-shipped with managed"); `plan-failover-compute.md`
> (ship-order updated; BYO Fly at launch); `decision-
> licensing-and-defense.md` (new anti-pattern + new adjacent
> defense move); `decision-connectors-layering.md` (BYO-for-
> subscribers pre-applies); `subject-managed-mode.md`
> (Surface table reshaped; the prior "Surface C — deferred"
> collapsed into Surface B's BYO sub-option); `design-config-
> layout.md` (`credentials.*` schema entry covers the third
> `kind`); `index.md` + `log.md` + this breadcrumb.
> Implementation cost over already-planned work is small:
> one new credential `kind` (~30 LOC), one dispatcher branch
> (~20 LOC), a small dormancy-state machine (~150 LOC), and
> the bucket-rename / activity-gate work is mostly already-
> designed.

> **2026-05-26 — locking pass II: Free signup bonus +
> subscriber project cap unlock + honest-nudge UX +
> deferred-revenue accounting.** User asked: "lets allow
> subscribers to have unlimited as soon as they spent smth
> small but reasonable on credits, otherwise capped at smth
> high like 25" + "the one time grant on free is probably
> good" + "a dashboard to show the allowance consumption in
> events and credits, and a nudge to go subscribe if anything
> got above the allowance — that's not too mean, right?" +
> "throttling is a good idea, like it." Driven by the
> realisation that the "5/month activity-gated recurring"
> Free grant from locking pass I had unbounded long-tail cost
> shape (cost grows linearly with active Free user count, not
> total signup count) and the flat "10 projects on
> Subscribed" cap was both too low for power users AND
> insufficient as a value-signal for sustained payers.
>
> **Free signup bonus replaces recurring grant.** 10 credits
> one-time on Free account creation, expires 30 days from
> creation OR on full consumption. Bounded by signup count
> rather than active-user retention — 100K signups total =
> $10K of compute total (one-time, not per year). The
> activity-gating logic is removed entirely. "Start stingy,
> relax later" — tightening reads as betrayal, loosening
> reads as winning. Selling Free as "the managed dispatcher,
> free" is honest; selling Free as "$0.05/mo of free
> compute" muddled the value prop.
>
> **Subscriber project cap reshaped from flat 10 to tiered
> 25 / unlimited.** Default 25 projects; unlocks to unlimited
> after $10 of cumulative top-ups
> (`cumulative_purchased_usd_lifetime >= 10`). The unlock is a
> permanent flag (`project_cap_unlocked`) on the account —
> survives subscription cancel + re-subscribe. 25 covers
> almost every solo developer; the spend-gated unlock rewards
> sustained-usage power users with no rent-seeking tier
> ladder. $10 = two typical top-ups → signals real usage
> without being punitive.
>
> **Multi-account abuse mitigation via binding uniqueness,
> not fingerprinting.** Database UNIQUE constraints on
> `(platform, chat_id)` + `repo_full_name` enforce that one
> resource binds to one account at a time. Needed anyway for
> routing correctness; framing it as abuse-mitigation gives
> ~95% of the value at zero incremental cost. Without it, a
> user could create N Free accounts × N signup bonuses + N ×
> 3 projects bound to the same chats / repos. With it: extra
> accounts can only host unbound "projects" with zero managed-
> gate routing value. Explicitly no fingerprinting / IP
> velocity / "suspicious account" flagging at launch —
> overengineering at our scale.
>
> **Dashboard nudges + transparency UX codified.** Eighth
> view (Allowance + usage) added as a first-class anchor for
> the nudge UX — events bar / credits bar with bucket
> breakdown / projects bar (with unlock-progress delta) /
> throttle banner / spend chart. Inline gauges across other
> views (top-nav status dot, project list header, failover
> view). Banner-nudge triggers + copy table covers Free 80%
> / 100% events, bonus-consumed, bonus-expiring, subscriber
> 80% credits, 25-project cap, 80% event cap. Anti-patterns
> explicitly named: no modals, no cancellation friction, no
> countdown timers, **no silent throttling**, no nudge spam.
> Gate-side single-line subscribe footer ONLY on throttle /
> cap / out-of-credit events — never on successful
> responses. "Throttling is always surfaced" is the load-
> bearing honest pattern — silent throttling is the actually-
> mean version.
>
> **Deferred-revenue accounting framing locked in.**
> Purchased credits + subscription fees are deferred revenue
> under French GAAP / IFRS (Stripe Revenue Recognition
> automates daily proration on subscriptions + per-debit
> recognition on purchased credits); grants are NOT deferred
> revenue (they're operational COGS); HugiMuni SAS chart-of-
> accounts sketch included for the launch-stage accountant;
> bank-account separation (operating vs reserve) called out
> as treasury hygiene at ≥€10K MRR, NOT a legal requirement
> at launch. No legal segregation needed for SaaS prepaid
> balances in France.
>
> **Why this matters.** Locking pass II answers two questions
> that pass I left implicit: "how do we charge sustainably
> for Free at scale without the recurring grant becoming a
> liability tail?" (answer: bound by signup count, not active
> users) and "how do we nudge users to subscribe without
> being mean?" (answer: every throttle is signposted, no
> modals, dismissal equal-weight to subscribe action). The
> deferred-revenue framing tells the accountant + implementer
> how the purchased-credits-never-expire promise is held
> safely on the books at launch, and at what scale operating-
> vs-reserve bank separation becomes warranted.
>
> Pages updated: `decision-pricing-shape.md` (tier table +
> two new sections + bucket table rename + binding-uniqueness
> section + dashboard-nudges section); `design-billing.md`
> (bucket rename `free_monthly` → `free_signup_bonus` with
> new mechanics + audit ops + cumulative-purchase tracking
> section + deferred-revenue accounting section); `design-
> brnrd-protocol.md` (project-creation cap enforcement +
> binding-uniqueness section + "What we DO hold" row);
> `design-config-layout.md` (three new read-only derived
> keys: `subscription.project_cap`,
> `subscription.project_cap_unlocked`,
> `cumulative_purchased_usd_lifetime`); `plan-failover-
> compute.md` (Free compute math reframed); `plan-brnrd-
> dashboard-mvp.md` (eight views + allowance gauges section +
> slice 3 extended); `subject-managed-mode.md` (Surface A +
> Surface B captions + Dashboard section updated to eight
> views); `index.md` + `log.md` + this breadcrumb.
> Implementation cost over already-planned work: bucket
> rename + activity-gate removal + 30-day expiry is ~50 LOC;
> cumulative-purchase counter + cap-unlock flag is a few
> columns + ~30 LOC; binding uniqueness is two DB UNIQUE
> constraints + ~20 LOC; allowance view + gauge + banner
> components are ~800 LOC. Total ~1K LOC across the slice-3
> dashboard work + the already-planned billing + protocol
> slices.

> **2026-05-26 — locking pass III: open questions closed,
> soft-throttle reframed, duplication groomed, knobs locked
> with `BRNRD_*` env vars.** User walked through the pass-II
> MR and asked: "do you think it is currently shaped
> optimally? I think there's a lot of data duplication...
> especially pay attention to contradicting / contention
> topics, they need to be resolved or bubbled up. ... could
> you make a summary of which decisions do I have yet to
> take?" Audit produced: 6 stale claims pruned across 6
> pages (BYO-deferred mentions + manual-invoicing claims),
> 10 open questions across the decision pages catalogued, 6
> duplication hot spots named. User locked 7 of the 8
> pricing-shape open questions in one MR-review pass; the
> remaining open question is the post-launch brand-name
> question.
>
> **Locked launch defaults + `BRNRD_*` env knobs.** Each
> launch-shape number gets a config key so ops can re-tune
> without a code release: Free signup bonus = 10,
> project-cap unlock = $10, included compute = 300,
> supporter cohort = 200, Free project cap = 3, dormancy =
> 24/36, soft-throttle rates (Free 1/hr, Subscribed
> 1/sec). New `BRNRD_FREE_SIGNUP_BONUS_CREDITS`,
> `BRNRD_PROJECT_CAP_UNLOCK_USD`,
> `BRNRD_SUBSCRIBER_MONTHLY_CREDITS`, etc.
>
> **Sixth permission-prompt mode: `auto-approve-below-
> monthly-limit`.** Auto-approves any spawn whose estimated
> cost fits inside the user's remaining monthly grant +
> purchased balance; falls back to `ask` once exhausted.
> Per-tier launch defaults: Free = `ask` (no monthly
> envelope → conservative), Subscribed =
> `auto-approve-below-monthly-limit` (the 300-credit grant
> is the natural envelope, prompts appear only at the upsell
> moment when exhausted). User's framing: "yeah auto-approve-
> below-monthly-limit is a good idea for a user facing config
> property."
>
> **Event-cap overage reframed: speed limit, not wall.** The
> pass-II shape had Free events HARD queue at 100/mo until
> the monthly reset. User clarified: "the nudge itself wasn't
> meant as a payment bait anyway, rather a way to resolve a
> throttled events flow situation... we still should probably
> dispatch events, just far less frequently i guess." Reframed
> to a soft throttle: events still flow at ~1/hour post-cap,
> with a gate-side footer on each throttled reply explaining
> the slowdown + how to lift it (subscribe / wait for cycle /
> self-host). Free users can keep using brnrd indefinitely at
> the slow rate without subscribing. Matches the existing
> Subscribed soft-throttle (~1 event/sec at 10K/mo) — both
> tiers now follow the same "speed limit, not wall" shape.
> The gate footer is the *resolution* to the user-facing
> situation, not the paywall.
>
> **Duplication grooming.** Three hot spots fixed: the
> nudge trigger / copy / anti-patterns table (was in both
> pricing-shape + dashboard-mvp; canonical home is now
> pricing-shape, dashboard-mvp delegates with a pointer);
> the Surface A/B tier captions (was duplicating
> pricing-shape's tier table; reduced to surfaces-only
> columns); the dashboard view list (was duplicating
> plan-brnrd-dashboard-mvp; reduced to a one-line summary
> + delegate). At the code level, banner copy + gate footer
> strings will live in a single `src/brnrd_web/nudges.py`
> module that both the dashboard AND the gate adapter read
> from, eliminating drift at the implementation level too.
>
> **Stripe-integrated callout** added near the top of
> `decision-pricing-shape.md` to prevent future drift
> between the policy page and the `design-billing.md`
> implementation page — "no manual-invoicing fallback at
> launch; edits to this page and design-billing should
> move together."
>
> Pages updated: `decision-pricing-shape.md` (Stripe
> callout + soft-throttle reframe + open-questions →
> launch-tunable-knobs + post-launch-tuning checklist +
> single remaining open question); `plan-failover-compute.md`
> (sixth approval mode + per-tier defaults); `plan-brnrd-
> dashboard-mvp.md` (allowance / nudge section trimmed to
> build-side only); `subject-managed-mode.md` (Surface
> table reduced; dashboard view list reduced); `decision-
> cli-shape.md` (new mode in `brr brnrd policy` help text);
> `design-billing.md` (env-knobs section); `index.md` +
> `log.md` + this breadcrumb.
> Implementation cost over already-planned work: ~0 LOC —
> the locking pass is all policy + organisational work, no
> new code paths beyond the `nudges.py` module which was
> already implied by the pass-II shape.

`brnrd` is not the right framing for "managed brr" — it's an operator
agent (a Cursor-Agents-window-shaped product) that *uses* brrs.
`brnrd` is one product axis; managed-brr is a different one.

Managed brr separates cleanly along **two dimensions**, each of which
can ship independently and both of which preserve the OSS
self-hosted path 1:1:

| Dimension | What it manages | Driver |
|-----------|----------------|--------|
| **A. Gates / IO** | Telegram, Slack, GitHub, … bots and tokens | Removes the per-user-token setup friction; lets a single hosted bot serve many users |
| **B. Cloud execution** | Where the runner actually runs when the user's laptop is down or busy | Lets brr keep working when the operator is offline; opens BYO-cloud and fully-managed compute tiers |

These two dimensions are independent. A user can take managed gates
without managed execution (their daemon stays local, just talks to
brnrd for transport); they can take BYO cloud execution without
managed gates (their tokens stay theirs, but tasks fan out to their
cloud); or both. Bundling them at the product level is a marketing
decision, not an architectural one.

### 1.1. Why this surfaces now

Older notes treated "scale brr" as overlays + `brnrd` + envs.
Overlays answer steering. `brnrd` answers fleet-as-an-object. Envs
answer task execution. Managed mode is orthogonal to all three: it
answers *where the moving pieces of brr itself run when they don't
run on the user's box*. Several pressures push it from "future" to
"plan now":

- **Laptop-down jobs.** Today a brr job needs the operator's box
  online. The natural recovery is "send the work somewhere else when
  the box is down". That capability is the smallest possible managed
  offering — a hosted runner — and it directly serves how the
  operator uses brr personally.
- **Sustainability at release.** OSS projects that introduce paid
  tiers *after* their adopter base is locked in lose adopter goodwill
  even when the OSS path stays intact. Adopters who joined because
  there was no commercial story feel co-opted. Shipping a clearly
  labelled paid tier at launch — with the OSS path unambiguously
  first-class — avoids the bait-and-switch reading.
- **First-mile friction.** The slowest part of brr setup today is
  token provisioning (Telegram bot, Slack app, GitHub PAT, runner
  CLI subscriptions). A managed-gates tier removes that friction in
  one verb, which is a credible paid wedge that does not gate
  capability.

### 1.2. Dimension A — managed gates / IO

The current gate model is per-user: each adopter creates a Telegram
bot via @BotFather, gets a token, runs `brr setup telegram`. Same
shape for Slack apps and GitHub apps / PATs. Setup is the longest
single friction in adoption.

Managed-gates shape:

- brnrd operates **one bot per channel kind**: `@brr_bot` on
  Telegram, a single brr Slack app, a single brr GitHub App.
- Users `/invite @brr_bot` to a channel or install the GitHub App on
  a repo. No tokens to manage.
- The hosted bot writes events to an inbox-as-service on brnrd,
  scoped to the user.
- The user's local daemon long-polls (or websocket-connects) brnrd
  for events and posts responses back the same way.
- The daemon still runs the runner on the user's hardware. Repo
  contents never leave the user's box.

Architectural fit with the current code:

- The existing protocol contract is "anything that writes to
  `.brr/inbox/` is a gate". A `cloud` gate that long-polls a remote
  inbox is just one more gate adapter — the daemon side is unchanged.
  See [`src/brr/gates/README.md`](../src/brr/gates/README.md) for
  the file protocol.
- The "remote gate stub idea" in older notes is exactly this — see
  §3.3 (brnrd's outline) for the original framing; managed mode is
  that idea operationalised as a user-facing product instead of a
  brnrd internal.
- New surface needed on brr's side: one CLI verb (`brr connect`,
  `brr connect brnrd`, or similar) that authenticates the local
  daemon to the hosted inbox and starts the cloud-gate thread. No
  per-bot setup verbs.

What this does *not* change:

- Token-based, fully self-hosted gates stay first-class. The OSS
  path remains `brr setup telegram` with the user's own bot. The
  managed tier is one alternative gate, not a replacement.
- No code execution happens on brnrd in this tier. It is pure
  transport — the security model is "we route messages; we never see
  repos".

### 1.3. Dimension B — cloud execution

Where Dimension A solves "tokens are annoying", Dimension B solves
"my laptop is offline". The model:

- The user runs a brr daemon somewhere (laptop, home server,
  occasional VM). When it's online, tasks run locally.
- When it's offline (or busy, or the user explicitly routes a task
  off-box), the task runs on **a cloud sandbox** the user has
  authorised brr to launch on their behalf.
- The sandbox holds the repo for the task duration, runs the runner,
  pushes the branch back to the user's remote, returns the response
  file, and self-destructs.

Two product tiers nest cleanly here:

- **BYO cloud key.** User adds a Fly token / Modal token / Daytona
  key / SSH host to `.brr/config`. brr launches sandboxes on the
  user's account. brnrd charges nothing per sandbox; it charges
  for the orchestration convenience (the scheduling, the gate
  routing, the receipt of "your task is done" notifications). User
  pays platform directly. **This is the near-term target** because
  it preserves the self-hosted ideology and it's what the user wants
  for the laptop-down case right now.
- **Fully managed compute.** brnrd runs sandboxes on its own infra
  (most likely Fly Machines or similar). User pays brnrd a
  per-task / per-second rate with margin. This is the higher-margin
  tier and the higher-trust ask (we touch their code), so it ships
  second.

Both tiers fit through the *same env-protocol shape* — see §2.1.

The OSS path: the same env-protocol adapter that talks to Fly /
Modal / Daytona / SSH for managed-mode is the same adapter an OSS
user runs locally with their own keys. Who operates what differs;
the code does not:

- **OSS / BYO compute**: user operates the daemon (on a box of their
  choice — see §4), holds the cloud token, the daemon hits the
  platform API directly. brnrd is out of the per-task path; its
  only role for BYO users is the gates side (Dimension A, optional).
- **Fully managed compute**: brnrd operates the compute on its own
  infra. User pays per-task with margin. Adds a brnrd-side
  scheduler that is its own v-next thing.

**Who dispatches when the laptop is down?** Not brnrd, in the BYO
case — the answer is "the daemon doesn't run on the laptop in the
first place". It runs on an always-on host the user controls, which
long-polls brnrd for gates events and dispatches tasks the usual
way. See §4 for the deployment-targets list. brnrd holding the
user's cloud token and spawning sandboxes on their behalf is a
v-next convenience layer, not the primary BYO answer — the
always-on-host model is simpler operationally and keeps brnrd's
scope tight.

### 1.4. Monetisation timing — ship the paid tier at launch

Three constraints push this:

- **Goodwill.** Adopters who joined because there was no commercial
  story will feel rug-pulled when one appears, even when nothing
  about their use changes. Shipping the paid tier visibly at launch
  removes the bait-and-switch reading.
- **Funding the maintenance.** Solo OSS maintenance is a known
  burnout pattern. A small recurring revenue stream from the
  managed-gates tier funds review / support / docs hours without
  needing a foundation or VC angle.
- **Product clarity.** "OSS thing that is also a SaaS" is a 2026
  pattern users recognise (Supabase, Plausible, PostHog). Shipping
  the SaaS surface at launch slots brr into that recognisable
  pattern. Shipping it later forces an audience reset.

What "ship at launch" means concretely, minimum:

- **Free tier.** Local daemon. Self-managed bots. Self-managed
  execution. Everything brr currently does.
- **Paid tier v0 — managed gates only.** Hosted `@brr_bot` on
  Telegram (the easiest one to operate; one bot scales to thousands
  of users). User installs `brr connect brnrd`, the daemon
  long-polls. Charged at maybe $10-30/mo flat — priced as
  convenience, not as compute.
- **Defer to v1.** BYO cloud execution. Fully managed execution.
  Slack / GitHub gate hosting. Per-call pricing models.

The launch-day paid tier doesn't have to be impressive. It has to
exist, be honest about what it costs and what it gives you, and
have a one-line description ("we run the bot so you don't have to
deal with tokens"). That's the goodwill cover.

### 1.5. Where this fits with the existing env protocol

The accepted env protocol — `prepare → invoke → finalize` from
[`design-env-interface.md`](design-env-interface.md) — already
generalises beyond local execution. The `ssh` env that page sketches
is conceptually a cloud runner with SSH as its transport:

- `prepare` provisions a remote scratch dir and copies the repo to
  it.
- `invoke` runs the runner over the transport (`ssh remote 'cd …
  && runner …'`).
- `finalize` retrieves the branch (git bundle + scp + git fetch) and
  the response file (scp), then tears down the scratch dir.

Every cloud platform in §2 below is the same shape with a different
transport:

| Env | Transport for create / upload / exec / download / destroy |
|-----|---------------------------------------------------------|
| `ssh` (designed) | OpenSSH client; rsync; ssh exec; scp |
| `fly-machine` | Fly Machines REST API; image registry; SSH or Fly exec; SSH or volume mount + download |
| `modal` | Modal Python SDK; image build; `sandbox.exec`; `sandbox.open` / `snapshotFilesystem` |
| `daytona` | Daytona REST API or SDK; image / snapshot; `sandbox.process.executeCommand`; Daytona FS API |
| `e2b` | E2B Python SDK; template (Dockerfile-derived); `sandbox.commands.run`; sandbox file API |
| `codespaces` | `gh codespace create`; devcontainer-bound; `gh codespace ssh`; `gh codespace cp` |

So the architectural question is **not** "do we need a new
abstraction?" — the env protocol already covers it. The questions
are about each adapter individually: how much code, how much
operating cost, what's the credential delivery story per platform,
what's the per-task cold-start time, what's the price floor for a
typical brr task (a few minutes of one shared CPU).

### 1.6. What still needs research

Concrete unknowns to resolve before crystallising any of this into a
design page:

- **Per-platform "what brr has to add" delta.** §2 starts the audit
  with the candidates we know matter most.
- **Credential delivery beyond env vars.** Brr's local docker env
  forwards `ANTHROPIC_API_KEY` etc. through `-e` and bind-mounts
  `~/.claude/`, `~/.codex/`, `~/.gemini/` (and `~/.config/gh`,
  `~/.ssh`) — see `_DOCKER_DEFAULT_CRED_PATHS` and
  `_DOCKER_DEFAULT_PASSTHROUGH_ENV` in
  [`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py). Remote
  sandboxes have no bind-mount equivalent — credential dirs have to
  be uploaded somehow (encrypted platform secrets, file upload over
  the platform SDK, or a one-off `git bundle`-style upload). Each
  adapter needs to pick the right vehicle.
- **Repo delivery.** Three patterns, each with trade-offs:
  - `git clone https://${TOKEN}@github.com/<owner>/<repo>` in the
    sandbox `prepare`. Cleanest; assumes the sandbox can reach the
    remote and the operator has provisioned a per-task token.
  - `git bundle create` locally then upload + `git fetch`. Works
    over any transport; expensive for large repos.
  - Platform-native volume / snapshot reuse (Daytona snapshots, Fly
    volumes pinned to a host). Lowest per-task cost; highest
    coupling to the platform.
- **Result delivery.** Branch back: push to the remote from inside
  the sandbox (simplest, assumes the remote is reachable and the
  token has push). Response file back: SDK file-download (Modal,
  E2B, Daytona), `gh codespace cp`, scp, or "stream it back over
  stdout from `invoke`" (the runner's stdout already carries the
  response — `runner.invoke_runner` captures it before any
  finalize step touches files).
- **Cold start budget.** Brr tasks tend to be 1-15 minutes. A
  60-second cold start is acceptable; a 5-minute cold start is not.
  Fly Machines claim ~300ms boot from a warm image; Daytona claims
  ~90ms from snapshot; E2B and Modal land in the seconds range. SSH
  to an always-on box is zero cold start but the highest standing
  cost. The right default for managed-tier-v1 falls out of this.
- **Per-task cost floor at brr usage shape.** For a 1 shared vCPU /
  1 GB RAM box running for 5 minutes (a typical small brr task),
  what's the realistic platform cost? Order-of-magnitude estimates
  before any commitment, not final pricing.
- **Network egress.** Each runner CLI calls home (Anthropic / OpenAI
  / Google) and the sandbox calls the git remote. Most platforms
  default to open egress; some let users restrict it (Daytona's
  network allow-list, Modal's `outbound_cidr_allowlist`). Brr
  probably wants permissive default with an opt-in tightening.
- **AGPL exposure.** Daytona is AGPL-3.0; if brnrd uses Daytona
  as an upstream sandbox backend, AGPL covers only modifications to
  Daytona itself (which we wouldn't need to make — we'd be an API
  client). Worth confirming with a lawyer-or-equivalent before
  committing.

---

## 2. Cloud execution candidates — what brr has to add per platform

> **PROMOTED on 2026-05-22 — see
> [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
> for the durable reference (cross-adapter patterns + per-platform
> briefs), and [`plan-env-fly-machines.md`](plan-env-fly-machines.md)
> for the first concrete adapter plan. Body below retained as
> provenance — the structured form lives in the promoted pages.**

This rewrites the older §10 plugin-candidate list around a single
question: *for each platform, what is the minimum brr has to add to
support it through the existing env protocol?* The audit is
necessarily incomplete until each plugin is actually drafted, but
the per-platform shape is concrete enough today to pick which
adapters to write first.

The list excludes anything that violates brr's positioning: SaaS
agents-as-a-service (Devin, Cognition) that own the loop, anything
that requires brr to hand them the repo plus the prompt and trust
their orchestration. Brr keeps the loop.

### 2.1. The minimum protocol delta — what every cloud-execution adapter needs

Every adapter implements the existing `EnvBackend` Protocol from
[`design-env-interface.md`](design-env-interface.md). The per-phase
work is:

| Phase | What the adapter has to do |
|-------|---------------------------|
| `prepare` | Create a sandbox / VM / workspace on the platform; choose or build the image; upload the repo (clone or bundle); upload credentials (env vars + auth dirs); record the handle in `ctx.env_state` |
| `invoke` | Exec the runner CLI inside the sandbox; stream stdout/stderr back to the host trace; honour the task timeout |
| `finalize` | Push the branch back (from inside the sandbox) or bundle-and-fetch (from the host); pull the response file to `response_path_host`; destroy the sandbox on clean done; preserve it on `status ∈ {error, conflict}` per the salvage rule |

The runner CLI install is **not** brr's problem per task — it ships
inside the image. The bundled image at
[`src/brr/Dockerfile`](../src/brr/Dockerfile) already builds a
practical runner image with claude / codex / gemini installed and
the dev tools brr expects. The same image is the right starting
point for every cloud-execution adapter; per-platform customisation
is choosing where to host it and how to point the platform at it.

The credential delivery layer is the single load-bearing complexity
across all adapters. Local docker uses bind-mounts; remote sandboxes
cannot. The realistic options, ranked from least operationally
demanding to most:

1. **Env vars only.** Forward `ANTHROPIC_API_KEY` /
   `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY` /
   `GITHUB_TOKEN` through the platform's env / secret system.
   Sufficient when the runner CLI supports keyed auth and the
   operator pays via direct API key.
2. **Env vars + platform secret store for credential dirs.** Tar
   `~/.claude/` / `~/.codex/` / `~/.gemini/` into the platform's
   encrypted secret store; expand at sandbox start. Needed when the
   runner CLI uses subscription auth (Claude Pro, Codex Plus, Gemini
   OAuth) — those credentials live in directories the CLI reads from
   `$HOME` and don't reduce to env vars.
3. **One-shot upload at task start.** Use the platform SDK's file
   API (Daytona, E2B, Modal) or `scp` (Fly Machines via SSH,
   Codespaces via `gh codespace cp`) to drop the credential dirs in
   the sandbox before `invoke`. Slower per task; simpler to reason
   about than option 2.

Brr's existing
`_DOCKER_DEFAULT_CRED_PATHS = (".claude", ".claude.json", ".codex",
".gemini", ".gitconfig", ".config/gh", ".ssh")` is the canonical
list; remote adapters should consume the same list rather than
inventing per-platform variants.

### 2.2. Fly Machines

- **Why it's first.** Fastest cold start of the credible options
  (~300ms boot from a warm image); the smallest VM is a few cents
  per hour, so a 5-minute task is under a cent; pure REST API with
  no SDK lock-in; per-second billing of running compute means brr
  pays for what it uses; `auto_destroy: true` matches brr's
  ephemeral-by-construction contract directly.
- **Adapter shape.** `prepare` calls `POST /v1/apps/{app}/machines`
  with `config.image = <our runner image>`, `auto_destroy: true`,
  and an env block containing the credential keys. Repo gets in via
  `git clone https://${TOKEN}@github.com/...` in the image's entry
  command (or as a `cmd` override). `invoke` is `POST .../exec` or
  SSH into the machine via WireGuard. `finalize` pushes the branch
  from inside the machine; the response file is captured from
  `invoke`'s stdout the same way every other env does it.
- **What brr needs to add.** A `FlyMachineEnv` adapter (~300-400
  LOC informed estimate; lots of the surface is already in
  `DockerEnv`'s shape); the bundled runner image needs to be
  published to a Fly-reachable registry (Docker Hub or
  `registry.fly.io`). Credential delivery via env-var path covers
  API-key users; subscription-auth users need the
  tarball-via-secret path.
- **Open question.** Volumes are pinned to physical hosts. For
  per-task ephemeral machines this is fine — no volume — but if a
  managed-mode user wants persistent caches (pip / npm), the volume
  pinning forces region-locked tasks. Not a blocker, just shapes
  the v1 default.

### 2.3. Modal Sandboxes

- **Why interesting.** SDK-first API (Python and JS) with the
  cleanest "create a sandbox from this image with this command and
  these env vars" surface; per-second billing; mature filesystem
  snapshot support (`snapshotFilesystem`) which would let brr cache
  repo state across tasks if that ever becomes worth the complexity;
  experimental "Docker-in-sandbox" mode if brr ever wants to host
  user-supplied dev container images inside the sandbox.
- **Adapter shape.** `prepare` uses `modal.Sandbox.create(app,
  image, command=..., env=..., timeout=...)`. `invoke` uses
  `sandbox.exec(...)`. `finalize` uses `sandbox.terminate()` plus
  push-from-sandbox for the branch. Image is `image =
  Image.from_registry(<our image>)` or built ad-hoc.
- **What brr needs to add.** A `ModalEnv` adapter (~400-500 LOC).
  Brings the Modal SDK as a runtime dep when this env is in use —
  acceptable as a plugin (optional dep) but not as a built-in.
  Credential delivery via Modal secrets is the natural path; the
  SDK has first-class `Secret.from_dict(...)` for this.
- **Open question.** Modal's pricing model rewards always-on
  apps with autoscaling sandboxes. Brr's "one task, then destroy"
  pattern is fine but is not Modal's primary use case; cold start
  is closer to seconds than to Fly's hundreds-of-ms. Probably the
  right backend for users who already use Modal, not the default.

### 2.4. Daytona (self-hosted and SaaS)

- **Why interesting.** Explicitly purpose-built for "run AI-agent
  code in isolated sandboxes"; ~90ms sandbox creation from snapshot;
  has both a SaaS (app.daytona.io) and a Docker-Compose-deployed
  self-hosted stack, so it fits brr's BYO-cloud-key tier *and*
  brr's self-hosted ideology without forcing a platform commitment;
  full REST + SDK + CLI; per-sandbox network allow-lists out of the
  box.
- **Adapter shape.** Same pattern as Fly / Modal: API call to
  create a sandbox from an image or snapshot, exec the runner via
  `sandbox.process.executeCommand(...)`, push the branch back,
  pull the response file via the FS API, destroy.
- **What brr needs to add.** A `DaytonaEnv` adapter that can target
  either the SaaS endpoint or a self-hosted Daytona instance via a
  configurable base URL. Probably ships with the Python SDK as a
  runtime dep. The runner image either lives in a registry Daytona
  can pull from, or is snapshotted ahead of time via Daytona's
  snapshot API for the faster cold start.
- **Open question — AGPL.** Daytona's AGPL-3.0 licence affects
  *modifications to Daytona itself* offered over a network. Brr as
  an API consumer doesn't trigger AGPL (we're not modifying
  Daytona's source). If managed brr ever extends Daytona itself
  (e.g., custom runner types upstream), that work would be AGPL-
  bound. Worth a one-line legal check before committing — not a
  blocker.

### 2.5. E2B Sandboxes

- **Fit.** Closely matches brr's pattern: Python SDK,
  `Sandbox.create()`, custom templates built from a Debian-based
  `e2b.Dockerfile`, file API for upload / download, command exec.
  The product is explicitly framed around "AI-generated code
  execution" so the security and isolation defaults are sensible.
- **Adapter shape.** Build a brr-specific E2B template once (the
  runner image as an `e2b.Dockerfile`), then `Sandbox.create(template_id)`
  per task. Repo upload via the file API or via `git clone` in the
  startup script. `sandbox.commands.run(...)` for invoke.
  Destroy on close.
- **What brr needs to add.** An `E2BEnv` adapter (~300 LOC).
  Template build is a one-off operator step, not per-task. SDK is a
  runtime dep when this env is in use.
- **Open question.** E2B's main muscle is short-lived
  code-interpreter sandboxes (max ~24h default). Brr's per-task
  shape is well within that window. Less clear how it handles
  persistent caches; probably bring-a-clean-template-every-time is
  the right default for v1.

### 2.6. GitHub Codespaces

- **Fit.** Devcontainer-native, so users with a
  `.devcontainer/devcontainer.json` already in their repo get the
  cloud runner with no extra image. `gh codespace create -r
  owner/repo -b branch` boots a codespace, `gh codespace ssh -c
  <name>` execs commands, `gh codespace cp` moves files. Free tier
  is generous for personal use (120 core-hours/mo); paid tier is
  billed to the GitHub account / org.
- **Adapter shape.** `prepare` runs `gh codespace create ...` with
  the right devcontainer path. `invoke` runs `gh codespace ssh -c
  <name> -- <runner-cmd>`. `finalize` pushes the branch from inside
  the codespace, `gh codespace cp <name>:<path> <host-path>` for
  the response file, `gh codespace delete <name>`. Cleanest CLI
  story of the set.
- **What brr needs to add.** A `CodespacesEnv` adapter — mostly
  subprocess shelling to `gh codespace …`, no SDK dep. Easiest
  adapter to write; arguably belongs as a near-term plugin since
  the audience overlap is high (every brr user with GitHub is a
  candidate).
- **Open question.** Codespaces are inherently GitHub-coupled. For
  brr's "cross-SCM" positioning, this is fine as one option among
  several — not a default. Cold start is slower than Fly / Daytona
  (typically tens of seconds, sometimes minutes for fresh
  codespaces).

### 2.7. Hetzner-style vanilla VMs (cloud-init or SSH bootstrap)

- **Why include it.** Some users will want managed-mode cost ceiling
  bound by *their cheapest cloud option*, not by a managed-runtime
  platform's per-second pricing. A vanilla VM on a budget host
  (Hetzner Cloud, low-end OVH, even a Raspberry Pi reachable over
  ssh) is the floor.
- **Adapter shape.** Identical to the designed `ssh` env in
  [`design-env-interface.md`](design-env-interface.md): provision
  via cloud-init at first use (or "bring your already-provisioned
  box"), rsync repo to scratch, ssh exec, git bundle back, scp
  response, ssh destroy / rsync clean.
- **What brr needs to add.** Mostly already designed — implement
  the `ssh` env. The "cloud-init bootstrap" variant is a thin
  prepare wrapper around the existing `ssh` shape that calls the
  platform's "create server" API (Hetzner Cloud API,
  vultr / digitalocean / etc.) once.
- **Open question.** "Pay-per-task ephemeral VM" on these platforms
  is poorly priced (you pay for the hour even when the task takes
  three minutes). Better used in long-lived-box mode: one always-on
  cheap VM that brr ssh's into. That's the operator's BYO-laptop
  replacement, not really a managed-mode offering.

### 2.8. What we are explicitly *not* building

- **Devin / Cognition / Lovable.** SaaS agents that own the loop.
  Brr's loop is brr's loop.
- **CI-as-runner (GitHub Actions, GitLab CI, CircleCI, Buildkite).**
  CI runners are great for triggered jobs; they're poorly shaped for
  brr's "the agent takes 7 minutes and then maybe asks a clarifying
  question" pattern. The `gh-aw` comparison in
  [`research-brr-vs-gh-aw.md`](research-brr-vs-gh-aw.md) covers this
  in depth.
- **Per-cloud platform built-ins.** Fly / Modal / Daytona / E2B /
  Codespaces all ship as **plugins**, not as built-ins. The
  rule-of-thumb from older §10 still applies: anything that needs an
  account, a CLI install, or an SDK install belongs in a plugin.
  Brr core ships `host`, `worktree`, `docker` and the protocol; the
  rest are opt-in.
- **PaaS platforms with read-only application containers (Heroku,
  Upsun, Render, Railway, App Platform).** Designed for always-on
  web apps with writes limited to declared mount paths; no per-task
  ephemeral sandbox API, no bring-your-own-OCI-image, and the
  read-only `/app` blocks `git worktree`-style operations brr's envs
  do. Wrong shape for the per-task sandbox role.

  These same platforms ARE viable as **daemon-hosting** targets —
  the brr daemon is exactly the always-on-web-app shape they were
  designed for, with a writable mount for `.brr/` and repo clones.
  See §4 for the deployment-templates promise; the read-only PaaS
  templates would be one row in that list. Per-task fan-out from a
  daemon hosted there uses the cloud-runner envs above, not the
  local `docker` env (which doesn't work without docker-in-docker).

---

## 3. brnrd — separate product, further postponed than managed mode

The framing has tightened since the older notes: **brnrd is not "the
managed-mode product"**. brnrd is closer to "Cursor's Agents window
for your fleet of brrs" — an operator-as-agent layer that orchestrates
brrs and other agentic surfaces, has its own brain, its own UI, its
own memory. Managed mode is *infrastructure brnrd would use*, not
brnrd itself.

That separation makes both directions easier to reason about:

- **Managed mode** is a property of brr — same code, hosted gates
  and / or hosted execution as opt-in tiers. Ships earlier because
  the OSS shape and the managed shape are 1:1.
- **brnrd** is a separate product with its own brain, kb, UI,
  multi-channel surface. Ships much later, after brr itself has
  proven traction and managed mode has stabilised. brnrd consumes
  brr (and probably the brnrd managed APIs), it does not extend
  brr's own runtime.

What brr should ship now to leave room for brnrd later:

- The same three small things from the older brnrd notes —
  machine-readable repo health, a self-maintaining registry, the
  "remote gate stub" idea — are still cheap groundwork. They were
  framed for brnrd; they happen to be useful for managed mode too
  (the cloud-gate is exactly the remote-gate stub).

What brr should *not* ship for brnrd:

- Cross-repo task coordination, fleet-level scheduling, shared
  memory across repos. All brnrd-side concerns. Brr stays per-repo
  at runtime.

The earlier "fully-managed brnrd is the commercial play" framing
from older §7 is misleading and worth retiring: the commercial play
that ships first is **managed-brr**, not managed-brnrd. brnrd is a
v-next product, on its own clock, possibly its own monetisation.

---

## 4. Cross-platform daemon supervision

The older table (systemd / launchd / Task Scheduler / Docker / tmux)
still stands as the survey. Updates since:

- **Linux is non-negotiable.** It's the operator's daily driver.
  systemd-user is the obvious target; brr would ship a template and
  a `brr install-service` verb that generates it.
- **macOS-first beyond Linux.** The audience for brr (AI-tool
  creator crowd, see
  [`research-positioning-and-runtime-deps-2026-05-21.md`](research-positioning-and-runtime-deps-2026-05-21.md))
  skews Mac. launchd plist generation is the right macOS path.
- **Windows is on the list but later.** Real Windows support needs
  more than a Task Scheduler / NSSM template — the signal-handling
  in [`src/brr/daemon.py`](../src/brr/daemon.py) and the bash gate
  example are the more interesting blockers. Set expectations in
  the README ("Linux and macOS first; Windows planned but not yet
  supported") to avoid the WSL hand-wave reading.
- **Docker as the universal escape hatch.** `docker compose up -d
  brr` survives restarts, works on every platform that runs Docker,
  and uses the runner image brr already ships. Pair it with
  per-platform native paths, not as a replacement.
- **Two-layer daemon hosting (the managed-mode angle).** *2026-05-22
  reframe: the "always-on host as the preferred BYO answer to
  laptop-down" claim below was demoted. The current preferred
  answer is brnrd-as-failover-dispatcher (Surface B in
  [`subject-managed-mode.md`](subject-managed-mode.md)) — the
  always-on host survives as a niche path for cloud-first users
  only. Body below retained as provenance.* In Dimension B the
  daemon's restart-survival problem partially
  disappears: if the daemon itself runs on a small always-on host
  the user controls, the laptop-down case is solved by the box-up
  case. This was originally framed as the preferred answer for
  BYO compute dispatch (§1.3) — simpler than brnrd spawning
  sandboxes on the user's account, and OSS-pure end-to-end. The
  model is two-layer:
  - *Always-on daemon host* = where the brr process lives.
    Long-polls brnrd for gates events; dispatches tasks via
    whatever env is configured.
  - *Per-task sandbox host* = where individual tasks fan out when
    bigger compute / GPU / stronger isolation is needed. Optional;
    uses the cloud-runner envs from §2.

  Deployment targets worth shipping templates for, ranked by setup
  ease:

  | Target | Setup | Trade-offs |
  |--------|-------|-----------|
  | Free-tier always-on cloud apps (Fly app, Render free worker, Railway) | `flyctl launch` from template / one-click | Cheapest; the "deploy brr in 30 seconds" path. Free-tier resource caps. |
  | Read-only PaaS templates (Heroku, Upsun, Render Blueprint, Railway, App Platform — see §2.8) | One-click deploy button | Broadest reach into developer audiences already on these platforms. Daemon runs fine on read-only `/app` if `.brr/` and repo clones live on a writable mount; per-task work must fan out to cloud-runner envs (no `docker` env without docker-in-docker). |
  | Cheap always-on VPS (Hetzner CX11 €3.79/mo, Oracle Free Tier ARM, low-end OVH / DO / Vultr) | `docker compose up -d brr` + systemd unit | Most flexible (full `docker` env support); cheapest at scale. |
  | Laptop / home server | `brr install-service` for macOS + Linux | Existing default; the install-service verb removes the "go add it to your startup scripts" friction. |

  The deployment-templates folder is a small package of
  `deploy/{fly,render,heroku,upsun,vps,docker-compose}/` examples
  that all reference the same `brr/daemon` Docker image. That image
  is a thin split of the existing bundled Dockerfile — the daemon
  variant drops the runner CLIs (claude / codex / gemini) since
  cloud-hosted daemons typically fan tasks out to per-task envs
  anyway, keeping the image small and dependency-light.

Earlier note "stays out of v1" for `brr install-service` is no
longer obviously right — for macOS + Linux it's cheap enough to be
part of the launch shape, and the smooth-startup UX shifts brr from
"indie hack" to "tool I'd recommend".

---

## 5. Self-maintaining registry

Unchanged from older §5:

- `~/.local/state/brr/repos.json` — XDG state dir.
- `brr init` appends `{path, created_at}`.
- `brr down` or future `brr forget` removes its entry.
- An external operator (`brnrd`, or a fleet-aware managed-mode
  service) reads this file; prunes entries whose path no longer
  exists.

Still trivial; still useful; still not urgent. The managed-mode
angle adds one consumer: a hosted brnrd dashboard that pulls the
registry over the daemon's authenticated API to render "your repos"
without scanning anything.

---

## 6. Overlay shape — capture-only

The overlay strands from older §1, §2, §3, §4 collapsed into
[`plan-overlays.md`](plan-overlays.md), which is blocked behind the
overlay-shape research gate (single-file vs multi-file). What
remains here as capture:

- **Where overlays belong.** `~/.config/brr/` (XDG-respecting,
  override via `BRR_CONFIG_HOME`) is fine. Self-hosted-friendly
  because the user's machine *is* the source of truth.
  Git-cloning that dir for remote-edit is the obvious nice-to-have.
- **Machine vs user scope.** The git-clone trick collapses both —
  one repo, N machines, each cloned into `~/.config/brr/`. Use git
  branches per machine if divergence is needed; default = main
  everywhere.
- **Setup ergonomics.** `brr init` already runs without an overlay.
  An eventual `brr overlay init` (with optional `--git=<url>`) is
  the *only* extra knob needed for the spinal-brain user. Without
  it, brr behaves exactly as today.

Promote into a design page if `plan-overlays.md` unblocks.

---

## 7. Re-promotion guide

Priorities have shifted since the older guide. The current order,
highest priority first:

- **Managed mode (Dimension A — managed gates).** Promote into a
  small page family: `subject-managed-mode.md` (hub covering
  Surfaces A / B / C + daemon hosting), `design-brnrd-protocol.md`
  (the wire format spanning gates + failover dispatch + cloud-
  token security; renamed from `design-managed-gates.md` once
  spawn-compute joined its scope), and
  `plan-managed-gates-launch.md` with two slices — GH App adapter
  first (largest BYO-setup pain relief), TG bot adapter as
  fast-follow (same backend, additional webhook endpoint + event
  parser). Backend skeleton is a FastAPI app + postgres; sized in
  the chat thread that produced this guide.
- **Managed mode (Dimension B — BYO cloud execution).** Promote
  `research-cloud-runner-patterns.md` (lifting §2 of this page into
  a durable reference: credential / repo / result-delivery patterns
  plus per-platform deltas) and the first adapter
  `plan-env-fly-machines.md`. **No new design page needed** —
  [`design-env-interface.md`](design-env-interface.md) already
  covers the protocol, cloud adapters are variations of the `ssh`
  env shape. Codespaces adapter as fast-follow (cheapest second
  adapter, biggest audience overlap).
- **Daemon hosting deployment templates (§4).** Promote into
  `plan-daemon-deployment-templates.md`. Mostly content / template
  work — Dockerfile split (daemon vs runner) + the
  `deploy/{fly,render,heroku,upsun,vps,docker-compose}/` examples
  folder + a "deploying brr" docs page. The "deploy brr in 30
  seconds" promise is what cashes out the BYO compute story without
  brnrd having to hold cloud credentials.
- **`brr daemon install` for macOS + Linux.** *Promoted on
  2026-05-25 (pass-4 follow-up — second wave)* into
  [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md). The
  earlier sketch called it `brr install-service`; the verb
  name shifted to `brr daemon install` to fit the noun-first
  CLI taxonomy from
  [`decision-cli-shape.md`](decision-cli-shape.md).
- **Self-maintaining registry (§5).** Trivial; promote into a
  small plan page when convenient. Useful for managed-mode
  inspection as well as for brnrd.
- **Overlays (§6 → `plan-overlays.md`).** Still blocked behind the
  research gate; nothing new here.
- **brnrd (§3).** Separate product, further postponed. Don't start
  until brr is publicly launched, managed mode has hands-on
  feedback, and overlays have shipped.
- **Cross-platform supervisor (§4) — Windows.** Defer until a
  Windows user complains *and* the daemon model can support it
  honestly.
- **Decentralised merge (older §8).** Shipped; absorbed in
  [`design-env-interface.md`](design-env-interface.md) and
  [`subject-envs.md`](subject-envs.md). No re-promotion needed.

Until any of the active items above promotes, **no code changes
here**. The point of this page is still to keep the side-channel
from getting lost while the active strands ship.

The old §7 (brnrd-as-service productisation note), §8 (decentralised
merge concrete examples), §9 (older re-promotion guide), and §10
(plugin candidates for `brr.envs`) sections were promoted out as
part of the 2026-05-22 reshape: §10 lifted into
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
(with the Daytona dogfood candidate joined by Fly Machines / Modal /
E2B / Codespaces / vanilla VMs); §7-§9 absorbed into the new §7
re-promotion guide above and the
[`subject-managed-mode.md`](subject-managed-mode.md) hub's "Boundary"
section.
