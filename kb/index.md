# Knowledge Base Index

Pages are grouped by **subject area** — Environments, Tasks &
branching, Conversations & responses, Documentation strategy, Fleet &
overlays, KB itself, Reviews, Research. The grouping is editorial: the kb is
ultimately a graph (see [`AGENTS.md`](../AGENTS.md) → "Knowledge base
shape" and [`decision-kb-shape.md`](decision-kb-shape.md)). The index
is the canonical entry point; once a subject accretes a real hub
page, link it at the top of its section.

Tool-level documentation (how brr itself works, pipeline / artifact
map, internals) ships with the package. Run `brr docs` to list it.
This index covers only this repo's project knowledge.

Lifecycle markers on a link reflect the page's current status:

- *active* — current state of thinking; safe to follow.
- *shipped* — the work has landed; the page is now context for the
  decisions that survive in the codebase.
- *blocked* / *paused* — held behind another piece of work; the page
  says what would unblock it.

Pages without a marker are reference (research, decisions, the
dive-in map) and are stable until something contradicts them.

## Architecture & orientation

- [Repo Dive-In Map](repo-dive-in-map.md) — bottom-up source map for
  understanding the repo file by file, with branch-neutral relative
  links, core entity cross-references, runtime invariants, and
  recommended reading paths.
- **Hub: [daemon and process lifecycle](subject-daemon.md)** —
  synthesis of the foreground `brr up` process, gate/file-protocol
  boundary, serial worker lifecycle, local process control, and where
  developer reload fits without becoming broad product UX.
- [Git layer rework design](design-git-layer-rework.md) — *shipped
  on 2026-05-15*. Reframes the deleted tasks-folder gate around what
  it was conflating: daemon-side freshness (pre-task fetch+ff with
  the seed-ref invariant), a real GitHub gate (built-in, stdlib,
  polling, label + mention triggers, PR-comment events carrying
  `branch_target`), and a prompt-level mitigation for runner
  thoughtfulness on design-loaded tasks (revisit-signal section in
  the run prompt + a self-review bullet).
- [Developer daemon reload design](design-daemon-dev-reload.md) —
  *shipped*. Opt-in brr self-development reload mode: editable install
  plus quiescent re-exec between tasks when brr package files change;
  kept explicit via `--dev-reload` / `dev_reload=true`, not a default.
- [Agent ergonomics observability design](design-agent-ergonomics.md) —
  *active (probe + log/response slices shipped; owner routing 2026-06-03)*.
  A three-layer back-channel routed by **run ownership** (a
  launcher-stamped `RunContext.owner`), not a free-form knob:
  deterministic probes bounded by a **vantage rule** (only host/operator
  facts the sandboxed agent can't see — image staleness, auth
  resolvability, worktree/disk/doc drift), runtime telemetry
  (retry/exit/phase data piggybacking on `run_progress`), and agent
  reflection. The user-facing knob is `ergonomics=off|log|local`
  (default `log` — a quiet daemon log for user-owned runs). Shipped
  proxies are `NullErgoProxy`, `LogErgoProxy`, and `LocalErgoProxy`;
  `BrnrdErgoProxy` remains the designed operator-owned sink. User-owned
  runs default to `LogErgoProxy` and honour the knob (`off`→null,
  `local`→on-disk store + `brr ergonomics` CLI). Operator-owned runs
  ignore the knob and currently short-circuit to `NullErgoProxy` until
  the brnrd proxy/endpoint lands; they never put ergonomics in the
  reply. (The visible-reflection `response` mode was retired 2026-06-08
  with the resident reshape — reflection now feeds the dominion journal,
  not a reply footer.) The brnrd dashboard's project + fleet ergonomics
  views are designed, not built.
- [Environment shaping loop](design-environment-shaping.md) —
  *proposed (2026-06-04); prior reasoning since 2026-06-08 — substrate
  absorbed into [`design-agent-dominion.md`](design-agent-dominion.md)*.
  Unifies the ergonomics back-channel, the
  kb-as-memory layer, and brr's interactivity into one **observe → remember
  → shape → retire** loop. Frames the two design axes (interactivity ×
  agency), the robustness=retrieval-cost hierarchy, a **salience** ("pain")
  triage on ergonomics records, **layered-control routing** (rings 0–3 for
  who fixes what), gates as a conversation medium, observability via
  transient relay (preserves data-min), and agent-satisfaction-as-operating-
  principle with its alignment guardrail. (Its failure-memory first slice
  now lives in the dominion, not as a kb marker.)
- [Agent dominion — the resident agent](design-agent-dominion.md) —
  *accepted on 2026-06-08*. The substrate companion to the environment-shaping
  loop, sequenced as the next work (pre-release). Reshapes brr from
  spawn-per-event into a **resident agent**: the agent *is* its durable memory,
  a *thought* is a runner woken by an event or self-scheduled cron, execution is
  **single-flight** (reflex/deliberation split, replacing the threaded pool),
  and durable memory splits into a **forge-backed orphan-branch dominion**
  (owned, auto-injected digest) plus the curated kb, joined by a promotion
  bridge. Folds the **playbook** as the convergence point (multi-response,
  ownership, pain-evaluation input, wake-as-action-and-growth). Reshapes
  [`design-concurrent-execution.md`](design-concurrent-execution.md).
- [Runtime dependency stance](decision-runtime-dependencies.md) —
  *accepted on 2026-05-22*. Drops zero runtime dependencies as a
  project value, allows small runtime deps that do not require native
  compilation when they pay for themselves, and accepts `requests` for
  the built-in gates while deferring per-forge SDKs.
- [`AGENTS.md`](../AGENTS.md) — universal agent playbook (canonical
  copy lives at `src/brr/AGENTS.md`, symlinked here).

## Environments

- **Hub: [environments](subject-envs.md)** — synthesis of the `Env`
  Protocol (three-phase `prepare → invoke → finalize`), the durability
  contract enforced from the host, the outcome-aware salvage rule,
  decentralised fast-forward merging, and which envs ship today
  (`host` / `worktree` / `docker`) versus designed-but-pending
  (`ssh` / `devcontainer`).
- [Env protocol design](design-env-interface.md) — *accepted on
  2026-05-06*. Full protocol, per-env mechanics, response-path split,
  plugin / script-env model, and configuration surface. Tactical
  companion to the env slice of the fleet deck.
- [Concurrent Worktrees Plan](plan-concurrent-worktrees.md) —
  *superseded on 2026-05-16 by*
  [`design-concurrent-execution.md`](design-concurrent-execution.md).
  Preserved for the reasoning that informed the current `worktree.py`
  + env protocol shape; the merge-coordinator design described there
  was abandoned and never came back.
- [Concurrent execution design](design-concurrent-execution.md) —
  *superseded on 2026-06-08 by*
  [`design-agent-dominion.md`](design-agent-dominion.md). The threaded
  daemon loop is reversed to single-flight by the resident-agent reshape;
  the partitioned per-event/per-task state + per-task worktree isolation it
  built on survive in `subject-tasks-branching` / `subject-daemon`.

## Tasks & branching

- **Hub: [tasks and branching](subject-tasks-branching.md)** —
  synthesis of mechanical task construction, environment resolution,
  agent-owned runtime branching, the 4-state finalize outcome table,
  and the publish kernel that ships the agent's branch in one step.
- [Publish kernel design](design-publish-kernel.md) —
  *accepted on 2026-05-21*. Agent leaves work on a branch; daemon
  publishes that branch. Collapses the predecessor land+push pipeline
  into one publish step (5-arm decision table), unifies metadata around
  `publish_branch` + `publish_status`, drops the `current` fallback.
- [Daemon branch intent design](design-daemon-landing-branch.md) —
  *superseded by [`design-publish-kernel.md`](design-publish-kernel.md) on 2026-05-21*.
  Predecessor landing-branch design (separate land + push, `BranchPlan`
  with `auto_land_branch`, metadata triple); preserved for context on
  the constraints the kernel inherits.
- [Branch Modes Plan](plan-branch-modes.md) — *shipped, with
  revisions*. Branch and env are task properties, the agent owns
  branching at runtime. Triage and `needs_context` were reversed —
  see the decision below.
- [Remove the triage stage](decision-remove-triage.md) — why the
  LLM-driven triage step and the frontmatter-as-stdout contract were
  removed in favour of mechanical task construction, agent-decided
  branching, and plain-text responses.

## Conversations & responses

- [Drop streams; conversations are routing+history, not identity](decision-drop-streams.md) —
  why the workstream layer was removed and replaced with a thin
  per-conversation log; lessons from the 2026-05-05 frozen-intent
  incident.
- [Conversations bundled doc](../src/brr/docs/conversations.md) —
  package documentation for the per-gate-thread conversation log.

## Documentation strategy

- [Bundled Docs Location](decision-bundled-docs.md) — why tool-level
  docs live in `src/brr/docs/` and ship with the package rather than
  in `kb/`.

## Fleet & overlays *(managed mode active; overlays paused)*

- **Hub: [fleet and overlays](subject-fleet-overlays.md)** —
  synthesis of the three-axis split: overlays as user-level steering,
  the fleet operator (originally codenamed `brnrd`, now the kept
  name for the whole hosted product), and environments as the
  active axis now handled by the env hub. The fleet axis itself
  collapsed into the managed-mode hub on 2026-05-25.
- **Hub: [managed mode](subject-managed-mode.md)** — *active*.
  The `brnrd` hosted product at `brnrd.dev`: managed dispatcher,
  managed compute, and subscriber-only BYO compute for the cloud
  envs brnrd also offers as managed. Launch pricing is Free
  (3 projects, 100 events/month, 10-credit one-time signup
  bonus) plus Subscribed ($5 supporter / $7 public, 25 projects
  until the $10 top-up unlock, 300 included compute credits,
  full dashboard, BYO compute opt-in). Data minimization stays
  explicit: brnrd does not need users' code; cross-gate
  continuity is metadata graph + on-demand gate-history fetch;
  the encrypted credential vault covers AI-runner, docker-
  registry, and subscriber-gated cloud-platform credentials.
  Lineage: promoted from
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) on
  2026-05-22, then locked on 2026-05-26 after pricing, BYO,
  naming, monorepo, and dashboard decisions converged.
- [brnrd protocol design](design-brnrd-protocol.md) —
  *accepted 2026-05-26*. The wire format between brr daemons and `brnrd`.
  Covers gates (managed-gates path), failover dispatch (decision
  tree with `docker login` step for private images AND a BYO
  branch on `cloud-platform` credential presence), generalised
  credential vault — three domains (AI-runner with api-key +
  dir-tarball shapes; docker-registry credentials; and
  `cloud-platform` credentials for BYO compute, subscriber-
  gated), subscription endpoints
  (`/v1/accounts/subscription[/checkout|cancel|resume|portal]`,
  with state values `tier=subscribed|subscribed_past_due|free`
  and plan codes `monthly|annual`), multi-project routing,
  permission-prompt API, data minimization principle,
  conversation context for failover and dashboard (metadata
  graph + git trailer + on-demand fetch + TG ring buffer), and
  Upsun deployment notes. Originally `design-managed-gates.md`;
  renamed to `design-brr-run-protocol.md` on 2026-05-22 when
  spawn-compute joined the protocol; renamed to
  `design-brnrd-protocol.md` on 2026-05-25 with the
  brnrd-naming-keep decision.
- [brnrd GitHub OAuth identity decision](decision-brnrd-github-oauth-identity.md) —
  *accepted on 2026-06-03*. brnrd accounts are GitHub identities via
  the managed GitHub App / OAuth web flow; email+password signup and
  login are removed before launch, while brnrd's hashed bearer tokens
  remain the API/session/daemon authorization primitive.
- [Managed-mode delivery design](design-managed-delivery.md) —
  *accepted 2026-06-01 (shape H)*. One daemon-side delivery driver
  (card lifecycle + per-platform presentation + gist/truncate
  overflow), two transports: direct (OSS gates → platform with the
  user's own token) and brnrd relay (cloud gate → brnrd → managed
  token). Locks **H** — brnrd keeps formatting the final answer, the
  daemon pre-handles overflow, and a thin additive `/v1/daemons/card`
  relays the live progress card — over **U** (brnrd a formatting-free
  pipe). Keeps gists daemon-side and generalises to remote-env (Fly)
  daemon-equivalents.
- [Pricing shape decision](decision-pricing-shape.md) —
  *accepted 2026-05-26*. **Subscription for the platform + metered credits
  for compute.** Two tiers at launch: Free (3 projects, 100
  events/month, **10 spawn-credit one-time signup bonus
  (30-day expiry)**, basic dashboard with allowance gauges,
  7-day audit, managed-compute-only) + Subscribed (**$5/month
  for the first 200 supporters → $7/month for the public
  cohort afterward**; or $50 / $70 annual; **25 projects
  (unlimited after $10 of cumulative top-ups)**, 10K
  events/month, 300 spawn-credits/month included, full
  dashboard, 90-day audit, email support, **BYO compute
  opt-in for cloud envs we ship managed**). Subscription tier
  deliberately unnamed (no "Plus" / "Pro" branding). Metered
  compute top-ups on either tier ($0.01/credit, Stripe
  Checkout one-shot, no card-on-file except opt-in auto-topup).
  **Credit buckets formalised** with per-source expiry:
  `free_signup_bonus` one-time on Free signup with 30-day
  expiry, `subscriber_monthly` use-it-or-lose-it end-of-cycle,
  `purchased` never expires (account-dormancy-bounded at
  24mo pause / 36mo prompt; deletion only on explicit request
  or GDPR), `promotional` future-proofed. **Multi-account
  abuse mitigation via binding uniqueness** (one repo / chat
  bindable to one account at a time — needed for routing
  correctness anyway). **Dashboard nudges + transparency**
  policy: honest banners on threshold-crossing, never modal,
  always-signposted throttling, gate-side one-line subscribe
  footer on throttle / cap / out-of-credit events. Self-
  hosted brnrd stays always-free with full feature parity;
  per-seat team tier is deferred to v-next. Lineage:
  2026-05-25 replaced the credits-only model because it could
  not sustainably carry the platform; 2026-05-26 locked the
  supporter/public price step, BYO-for-subscribers rule,
  per-source credit expiry, one-time Free signup bonus, and
  soft-throttle event overage defaults.
- [Billing design](design-billing.md) — *accepted 2026-05-26*. **Two
  billing legs**: subscription (Stripe recurring,
  monthly/annual, Customer Portal for self-service) and credit
  wallet (one-shot Stripe Checkout top-ups). Subscription
  mechanics: $5/month, prorated start, cancel-at-period-end,
  subscriber credit grant (300/month) vs Free's **10-credit
  one-time signup bonus (30-day expiry)**. Wallet mechanics:
  top-up flow, debit-at-finalize, zero-balance UX with
  enqueue + gate notify, opt-in auto-topup, pro-rata refund
  policy. **Credit bucket ledger** with per-source expiry:
  `free_signup_bonus` one-time on Free signup (30-day expiry
  OR full consumption), `subscriber_monthly` use-it-or-lose-it
  end-of-cycle, `purchased` never expires (account-dormancy
  bounded), `promotional` future-proofed. Debit priority is
  grants first, purchased last (FIFO within bucket). **BYO
  compute bypasses the wallet** for subscribers (subscribers
  who BYO contribute pure subscription revenue; `spawn_byo`
  audit op replaces `debit_spawn`). **Cumulative purchase
  tracking** drives the subscriber project cap unlock
  (`cumulative_purchased_usd_lifetime >= 10` → `project_cap_unlocked
  = true`, permanent on the account). **Account dormancy
  policy** bounds the "purchased never expires" tail (24mo
  pause / 36mo prompt; deletion only on explicit user
  request or GDPR). **Deferred-revenue accounting** framing
  for the implementer + accountant: purchased credits +
  subscription fees are deferred revenue under French GAAP /
  IFRS (Stripe Revenue Recognition automates the daily
  proration); grants are NOT deferred revenue (they're
  operational COGS); HugiMuni SAS chart-of-accounts sketch
  included; bank-account separation called out as treasury
  hygiene at ≥€10K MRR, not a legal requirement. Audit log
  entries cover every billing operation including the new
  promotional / dormancy / project-cap-unlock ops. Stripe
  integration shape (HugiMuni SAS + Stripe France + Qonto
  payouts + Stripe Tax for EU VAT + OSS scheme + SCA via
  Checkout) applies to both legs under one Stripe account.
  **Locking pass IV (2026-05-26)** added the **overdraft
  envelope**: spawn-start gate is `current_balance >= 0` AND
  `estimated_spawn_cost <= current_balance + max_overdraft_credits`;
  per-account `max_overdraft_credits` setting (default 0;
  Subscribed can raise within
  `BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS` = 500 credits = $5
  default cap). The last spawn of the cycle can dip the
  balance negative within the envelope; next spawn waits for
  a top-up to clear back to ≥ 0. Three new audit ops
  (`overdraft_settings_changed`, `overdraft_consumed`,
  `overdraft_cleared`).
- [CLI shape decision](decision-cli-shape.md) — *accepted 2026-05-26*.
  Seven top-level verbs (`init` / `run` / `daemon` / `gate` /
  `brnrd` / `config` / `kb`) with subcommands. Collapses today's
  `up` / `down` into `brr daemon up|down|status|install|
  uninstall|logs`; collapses today's `auth` / `bind` / `setup`
  into `brr gate <name> <verb>`; adds the load-bearing `brr
  brnrd` namespace for hosted-service management (`connect` /
  `creds` / `policy` / `topup` / `balance` / `projects` /
  `subscription [status|start|cancel|resume|portal]` + `brr
  brnrd subscribe` shortcut for the subscription / ...); adds
  `brr config list|get|set|doc|template|validate` for three-
  scope (project / local / account) parameter introspection;
  adds `brr kb status|pages|proposed|log|check|doc` as the kb
  read surface for users and non-brr agents. `brr brnrd creds
  add` accepts both AI-runner kinds and `docker-registry`
  (registry credentials for private images go in the same
  encrypted vault as AI creds). Every
  sub-verb supports `--json`. Rejects the earlier `brr accounts`
  placeholder. `brr brnrd connect [url]` is a three-layer smart
  bootstrap defaulting to `https://brnrd.dev` and accepting any
  URL for first-class self-hosting. **Locking pass IV
  (2026-05-26)**: **`brnrd` promoted to a sibling top-level
  binary** (same package, two `[project.scripts]` entries);
  `brr brnrd <subcmd>` retained as a convenience alias.
  Permission-prompt scope clarified — applies to **managed
  compute only**; other credit-eating features (voice,
  vector / semantic stores, visual graphs, …) use one-time
  enablement consent instead of per-call prompts.
- [Connectors layering decision](decision-connectors-layering.md) —
  *accepted 2026-05-26*. Names the gates vs connectors split: gates are
  per-project / inbound (existing shape); connectors are
  per-account / outbound / proactive (for the future
  agentic-secretary layer). No connectors ship at launch; the
  split lives here so the future agentic-mode upgrade doesn't
  have to retrofit the gate API. **BYO-for-subscribers pre-
  applies to connectors when they land** — same credentials
  table, new `kind` value, same subscriber gate; one pattern
  for cloud envs + connectors + any future subscriber-only
  credential surface.
- [Monorepo structure decision](decision-monorepo-structure.md) —
  *accepted 2026-05-26*. Single `brr` pip package + optional extras.
  `src/brr/` (daemon) + `src/brnrd/` (backend) + `src/brnrd_web/`
  (dashboard) + `src/brr/envs/<name>/` for first-party cloud
  envs gated by extras (`pip install brr[fly,modal,...]`).
  Third-party envs use the existing `brr.envs` entry-point
  mechanism. Envs split out to their own `brr-env-<name>` pypi
  package when their maintainer cadence diverges or their
  install footprint grows. The package boundary doubles as
  the license boundary (MIT daemon + AGPLv3 backend /
  dashboard) per
  [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md).
- [Licensing and competitive-defense decision](decision-licensing-and-defense.md) —
  *accepted 2026-05-26*. Three concrete moves that protect the brnrd
  hosted business without crippling the OSS posture:
  **(1) license split** — `src/brr/` stays MIT (daemon
  maximises community goodwill); `src/brnrd/` +
  `src/brnrd_web/` ship **AGPLv3** (closes the "Big Cloud
  rehosts our OSS as managed service" attack while keeping
  self-hosters fully unaffected); **(2) early-adopter
  pricing** — first 200 subscribers at $5 / month
  grandfathered forever on Stripe, then $7 / month for the
  public cohort (loyalty + long-tail revenue headroom in one
  step); **(3) trademark on `brr` + `brnrd`** — deferred for
  budget but post-launch priority, EU registration via EUIPO
  through HugiMuni SAS at €800-1500 total, triggered by
  launch+12-months OR €10K cumulative revenue OR first
  observed competitor (whichever first). Explicitly rejects
  BUSL/ELv2/SSPL (community-goodwill cost > defense gain at
  current scale), gating any feature behind hosted-only
  (breaks the always-free-self-host promise), racing to the
  bottom on price, and   pre-buying defensive domains
  (trademark + UDRP covers the actual attack pattern at
  lower ongoing cost). **Anti-pattern surface expanded
  2026-05-26 with "don't lock subscribers into brnrd's
  cloud" — subscribers can BYO their own cloud-platform
  tokens for any env we ship managed, parallel-shipped per
  cloud. The BYO posture doubles as a moat amplifier: a
  competing fork can't out-open us on credentials without
  giving up revenue their model can't afford.**
- [Two-website decision](decision-websites.md) — *accepted
  2026-05-26*. Two distinct web properties at two distinct
  URLs: **brr.dev** (OSS landing — what brr is, docs,
  contributor info, self-hosted-brnrd guide; static-site
  simplicity, no auth, no payments) + **brnrd.dev** (hosted
  product — signup, pricing, dashboard, billing portal;
  live web app, Stripe auth + payments). Cross-linking is
  the trust signal: brr.dev points at brnrd.dev as the
  hosted option ("Don't want to host yourself? brnrd.dev,
  same software, hosted"); brnrd.dev points at brr.dev as
  the OSS truth ("Powered by the open-source brr, full
  feature parity on self-hosted"). Two URLs, each
  acknowledging the other as a real alternative, make the
  "we charge for ops, not for crippled OSS" trust pitch
  visible rather than something the user has to take on
  faith. brr.dev MVP is a static landing page; brnrd.dev
  hosts the eight-view dashboard from the dashboard-MVP
  plan + the marketing pages.
- [Cloud envs research](research-cloud-envs.md) —
  cross-env patterns (credential / repo / result delivery,
  cold-start budgets, network policy) for envs that execute
  remotely, the caller axis (same env class invoked from laptop
  daemon AND from brnrd server-side managed compute, with
  brnrd doing a daemon-equivalent bootstrap first), and
  per-platform briefs (Fly Machines, Modal, Daytona, E2B,
  Codespaces, vanilla VMs). Renamed from
  `research-cloud-runner-patterns.md` on 2026-05-25 (pass 4)
  with the "cloud runners are envs" unification.
  Promoted from `notes-pondering-fleet.md` §2; refreshed 2026-05-25
  to reflect that only Fly Machines wires up server-side at
  launch (managed Fly + BYO Fly ship together for subscribers
  per the locking pass; other clouds parallel-ship managed +
  BYO when added).
- [Managed gates launch plan](plan-managed-gates-launch.md) —
  *accepted 2026-05-26; not started*. Surface A. Three slices: GH App adapter +
  backend skeleton + auto-binding (first, largest pain relief);
  TG bot adapter + multi-project routing UX (fast-follow);
  permission-prompt API + gate-side integration (third). Backend
  lives at `src/brnrd/` in the monorepo.
- [brnrd inbox-as-service prototype](plan-brnrd-inbox-prototype.md) —
  *in flight (started 2026-05-27)*. The executable `src/brnrd/`
  prototype unblocking the managed-gates launch. FastAPI +
  SQLAlchemy (SQLite) backend. **Slice 1:** accounts / projects /
  device-flow connect + the daemon-facing register / long-poll /
  respond / deregister loop, with a `cloud` gate
  (`src/brr/gates/cloud.py`) built on a shared gate runtime
  (`src/brr/gates/runtime.py`) extracted from the Slack + Telegram
  gates; response bodies are forwarded out and never persisted
  (data-min). **Slice 2 (2026-05-31):** real `POST
  /v1/webhooks/telegram` ingress (secret-header auth, chat→project
  pairing, platform-dispatching forwarder) + a thin `src/brnrd_web/`
  dashboard (login + the device-flow approve page) so connect is
  human-completable. AGPLv3 per the license split. Deferred: GitHub
  webhook, fuller dashboard, caps/billing, failover.
- [GitHub gate vs brnrd GitHub App design](design-github-gate-vs-brnrd-app.md) —
  *accepted 2026-05-27*. Boundary doc for the GitHub side: what the
  OSS polling gate owns and keeps owning (PAT auth, three-trigger
  polling, single-repo binding, response posting, live progress
  card), what brnrd owns exclusively (GH App JWT minting, webhook
  receipt + signature verification, multi-project routing,
  permission-prompt UX, hosted bot identity), and what both share
  via the package split (`paths` / `cache` / `parse` reused behind
  brnrd's async httpx). Closes the "does managed obsolete OSS"
  question with a definite no — different identity, setup, latency,
  blast radius.
- [Failover compute plan](plan-failover-compute.md) —
  *accepted 2026-05-26; not started*. Compute spawn (managed + BYO) for subscribers, on
  brnrd-owned Fly pool for the managed path and on the
  subscriber's own Fly account for the BYO path: generalised
  credential vault (AI runner + docker-registry + cloud-platform
  for subscribers, encrypted at rest), dispatcher decision tree
  with branch on BYO-cred presence, permission-prompt-resolving
  spawn invocation, audit log (with `spawn_byo` for BYO wallet
  bypass), and the CLI surface for the `brr brnrd` verbs (creds
  / policy / audit / balance / topup / subscription). **BYO Fly
  Machines ships at launch** as a subscriber feature parallel-
  shipped with managed Fly; subsequent clouds get BYO when they
  get managed.
- [Conversation_id propagation plan](plan-conversation-id-propagation.md) —
  *accepted 2026-05-26, not yet started*. Locking-pass-IV
  clarifications: scope is **identity propagation only**
  (the daemon already injects rich context — kb/log tail +
  Task Context Bundle + recent conversation records — this
  plan adds none of that); **`conversation_id` =
  `conversation_key`** (the existing human-readable
  gate-fingerprint string already implemented in
  `src/brr/conversations.py`), not a new ULID; token-budget
  discipline flagged inline for future context-rich features
  (not a separate plan). Small daemon-side enabler:
  `Brnrd-Conversation-Id`
  git commit trailer + `conversation_id` field on the
  `/v1/daemons/responses` POST. Gates brnrd's metadata-only
  conversation graph from being meaningful in practice so
  cross-gate continuity for failover can actually work without
  brnrd holding conversation contents. ~80 LOC daemon-side.
- [Dashboard MVP plan](plan-brnrd-dashboard-mvp.md) —
  *accepted 2026-05-26; not started*. Eight views (accounts/projects, project detail,
  task detail, conversation proxy, credentials vault (AI +
  docker registry), failover policy + cost chart, audit log,
  **allowance + usage** with bucket-breakdown + nudge
  banners). HTMX-first to keep build/maintenance cost down;
  SPA later if interactivity demands it. **Honest-nudge UX**
  policy: dismissible inline banners on threshold-crossing,
  no modals, no cancellation friction, always-signposted
  throttling, single-line gate-side subscribe footer on
  throttle / cap / out-of-credit events.
- [Fly Machines env plan](plan-env-fly-machines.md) —
  *accepted 2026-05-26; not started*. First cloud env; lives at `src/brr/envs/fly_machines/`
  gated by the `brr[fly]` extra. Used by the laptop daemon
  (user's Fly account, BYO via `FLY_API_TOKEN`) and by brnrd
  server-side (brnrd's Fly account, managed compute) — same env
  class, two callers; see "Caller axis" in the research page.
- [Daemon deployment templates plan](plan-daemon-deployment-templates.md) —
  *demoted to launch-nice-to-have on 2026-05-22*. Earlier framing
  positioned the always-on-host as the preferred laptop-down
  answer; the failover-compute path replaced it. These templates
  remain useful for the niche cloud-first audience. The Upsun
  template shares its read-only-container shape with the brnrd
  backend Upsun deployment.
- [Laptop daemoning plan](plan-laptop-daemoning.md) —
  *accepted 2026-05-26; Linux systemd and macOS LaunchAgent service
  slices shipped 2026-05-26*.
  Accepted target shape is machine-scoped multi-project: one
  `brr daemon` process per machine serves all brr-init'd repos
  from `~/.config/brr/projects.toml`; account binding lives at
  machine scope; one supervised systemd / launchd unit per
  machine (no `WorkingDirectory` pinning, no `--name` flag).
  The shipped service-lifecycle surface writes a per-user systemd unit
  (`~/.config/systemd/user/brr.service` + optional
  `loginctl enable-linger`) on Linux and a LaunchAgent
  (`~/Library/LaunchAgents/dev.brnrd.brr.plist`) on macOS,
  then wires `brr daemon up | down | status | logs | uninstall`
  through the native service manager when installed, falling back
  to the foreground supervisor when not.
  Registry-aware runtime, `brr init` registry writes,
  `brr daemon list|adopt|forget`, and machine account binding
  remain follow-up work; Windows is deferred. Tracked at
  [issue #29](https://github.com/Gurio/brr/issues/29).
- [Config layout design](design-config-layout.md) —
  *accepted 2026-05-26*. **Locking pass IV** added the
  "per-branch overrides — embraced, not avoided" framing
  (`brr.toml` is git-tracked → per-branch by construction;
  feature-branch policy overrides are useful), the daemon's
  three-step working-branch rule (`event.branch_target` →
  `daemon.last_spawned_branch[project_id]` → repo default),
  and the machine-scoped account-binding layout at
  `~/.local/state/brr/account/` (binding / subscription /
  cached settings). Three-scope config model: `project` (`brr.toml` at repo root,
  committed — teammates + brnrd-side spawns see it), `local`
  (`.brr/config`, gitignored, this machine only), `account`
  (brnrd-side store via `/v1/accounts/settings`, all the user's
  daemons see it). TOML format both files. Merge precedence
  `local > project > account > defaults`. Per-key schema
  declares scope; `brr config list/get/set/doc/template/
  validate` operate over it. Lets brnrd-side spawns pick up
  project preferences (Docker image, runner choice, env
  default) from the cloned repo. The account-scope
  `credentials.*` entry covers all three credential vault
  kinds (AI runner + docker-registry + cloud-platform);
  `cloud-platform` writes / reads are subscriber-gated at
  the brnrd endpoint level.
- [KB subcommand plan](plan-kb-subcommand.md) — *accepted 2026-05-26; not started*.
  `brr kb` as the seventh top-level verb, addressing
  [issue #41](https://github.com/Gurio/brr/issues/41). Six
  sub-verbs (`status` / `pages [filters]` / `proposed` / `log`
  / `check` / `doc`) shared between human users (who get
  "what needs my review?") and non-brr agents (who get
  `--json` health + check output without rolling their own kb
  walker). `brr kb check` validates reachability, cross-
  references, status-marker syntax, stale-active warnings,
  aspirational-drift and sibling-drift smells; non-zero exit
  on errors. AGENTS.md → "Health checks" collapses to "run
  `brr kb check`" once shipped.
- [Deck: brr fleet & steering](deck-brr-fleet-steering.md) —
  *roadmap (env axis partly shipped, overlays/brnrd paused)*. Three-axis
  framing (overlays · brnrd · environments); read for the strategic
  shape, not as a current spec — see decision pages and the env
  design for the live state.
- [Overlays plan](plan-overlays.md) — *blocked* on the env work and
  a research gate for single-file vs multi-file overlays.
- [Notes: Fleet, managed mode & steering](notes-pondering-fleet.md) —
  *partially promoted*. §1 (managed-mode synthesis) and §2 (cloud
  execution candidates) are now provenance for the managed-mode
  page family above; older overlay / registry / brnrd / supervisor
  notes still live here as §3-§6 capture-only. Reshape history is
  preserved.

## Knowledge base itself

- **Hub: [the kb itself](subject-kb.md)** — synthesis of the kb
  pattern in brr today: four memory layers, graph topology with
  index reachability and lifecycle markers, when to create a subject
  hub, cross-tool maintenance via AGENTS.md schema + brr's
  preflight + LLM redundancy pass, what was tried and rejected.
- [kb shape decision](decision-kb-shape.md) — four memory layers
  (raw / episodic-thin / semantic+decisional / schema), kb as a graph
  with explicit linking discipline, lifecycle markers, the subject
  genesis rule, brr's daemon kb-maintenance reframed as a redundancy
  pass; staged execution plan.
- [State-first kb maintenance plan](plan-kb-state-first-maintenance.md) —
  *active*. Refine the kb shape around current-state synthesis plus
  short breadcrumbs to git history, and replace hidden post-task LLM
  cleanup with explicit, first-class maintenance tasks.
- [Agent orientation layering](plan-agent-orientation-layering.md) —
  *active (slices 1+2 shipped 2026-05-16)*. Synthesis of the two
  same-day ergonomics reviews into a four-layer model (repository
  contract / stage overlay / runtime state packet / subject
  knowledge), with shipped, in-flight, and open follow-up slices
  marked.
- [LLM Wiki framing](llm-wiki.md) — the source framing this project
  takes inspiration from for the wiki/synthesis layer.

## Reviews

- [diffense — kb-first PR review experience](design-diffense.md) —
  *accepted 2026-05-29, format refined 2026-05-31 (passes 6–10)*. The review
  surface for brr-generated PRs, built around the half-of-a-brr-PR-is-kb
  pain. Inspect-mode model: reviews are a **zoomable graph of cards**
  (item / walkthrough / uncertainty kinds) with two navigation axes —
  lateral edges and zoom (gloss → detail → ground-truth leaf, where
  leaves are the real diff/file/rendered-page and summaries are
  clamp-gated). Two-axis lore (what-it-is + what-it-enables), per-kind
  stat blocks, code **locators**, and tests-grounded demos. A JSON
  **review pack** (generated at publish time, `brr review --check`'d) is
  the contract. Build is **web-first**: one light, brnrd-independent
  responsive-web renderer with a terminal aesthetic (ascii-looking cards;
  opening a nested card collapses its parent to a heading bar, nesting
  into a breadcrumb stack), built before brnrd for the self-hosting
  story; CLI/TUI and hosted brnrd are follow-ups, the PR-body a lossy
  fallback. The **feedback loop** closes through the shipped
  `pr-review-comment` gate (flag a card → anchored comment → task →
  re-pack). Six discipline clamps keep cards sharp; agent **uncertainty
  cards** (incl. `follow-up` + tension references) read first; the
  "entertaining" goal is framed as removing *accidental* burden, not
  gamification. Folds with the [ergo proxy](design-agent-ergonomics.md)
  as shared-source / split-audience. A **renderer spike**
  ([src/brr/diffense/](../src/brr/diffense)) validated the read model and
  resolved the two interaction questions — lateral nav and zoom-drills
  share one breadcrumb stack; a code leaf is jump-to-forge. A later pass
  added the **state / data / invariant** triad: an **invariant** axis (the
  conserved frame; a *threatened* invariant is what a tension points at), a
  **data-shape delta** distinct from the signature, **entry stats as
  visual rolled-up distributions** (bars / meters / heat, size demoted),
  **data-trace** walkthroughs (follow the datum, steppable so animation is
  a renderer-only upgrade), and **kb-native axes**. Transport corrected:
  brnrd is a *transient relay*, never a pack store (matches its
  data-ownership stance). The pack schema is **locked as the
  `brr review --check` contract**
  ([src/brr/diffense/pack.py](../src/brr/diffense/pack.py)); the runner
  emits packs (Producer B, gated fragment) and the publish kernel opens a
  PR with the body projected from the pack
  ([prbody.py](../src/brr/diffense/prbody.py), `_maybe_open_pr`), both **on
  by default**. In managed mode the pack is relayed to brnrd's transient
  RAM-only renderer (`POST /v1/daemons/pack` + public `/r/{token}`,
  [pack_relay.py](../src/brnrd/pack_relay.py)) and an interactive link
  rides the PR body — never persisted. All shipped 2026-06-01.
- [diffense prototype — hand-authored pack for PR #64](diffense-prototype-pr64.md)
  — *2026-05-29*. The first concrete pack
  ([JSON](diffense-prototype-pr64-pack.json)), rendered as cards, that
  pressure-tests the schema against a real braided PR (fix + refactor +
  feature). Ten cards stand in for 23 files. Findings that sharpen the
  schema before it locks: a missing `code-module-split` kind, `--check`
  must resolve locators (it would have caught the design's invented
  `cache.get_with_etag`), edges need `{card|locator}` targets,
  uncertainty needs an `honest_nuance` slot. A second pass folded the
  *shape* back into the design: a **summary / on-ramp card**, an **open
  card-kind taxonomy** (agent declares `custom` + raises a meta concern;
  `code-module-split` promoted), and **gloss-first** uncertainty cards.
  Now rendered live by the [renderer spike](../src/brr/diffense)
  (`render.py` inlines this pack into a self-contained HTML page); pass 10
  extended the pack to demonstrate the visual entry-stat distributions,
  the invariant axis, the data-trace walkthrough, and kb-native axes.

## Research

- [Cursor orientation ergonomics, 2026-05-16](research-cursor-orientation-ergonomics-2026-05-16.md) —
  *shipped*. External Cursor session view: AGENTS.md mode-blindness,
  the orientation read cost (~4,200 lines for a session that uses
  ~25-30%), specific redundancy across README / AGENTS.md / index /
  log / dive-in-map. Headline recommendations absorbed into
  [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md).
- [Cursor orientation ergonomics — follow-up, 2026-05-16](research-cursor-orientation-ergonomics-followup-2026-05-16.md) —
  *active*. Same-day second-pass review after slices 1+2 shipped.
  Surfaces a Cursor workspace-rule cache that delivers a stale
  `AGENTS.md` to the agent, confirms the user-flagged
  README ↔ AGENTS.md elevator-pitch / Build-and-run duplication, and
  recommends dropping the plan's slice 3 (snapshot test) as low ROI.
- [Runner orientation ergonomics, 2026-05-16](research-runner-orientation-ergonomics-2026-05-16.md) —
  *shipped*. Same-day daemon-launched-runner view of the same
  problem from inside Docker: pinpoints the stage-vs-environment
  axis confusion, the missing Mode block on the Task Context
  Bundle, and the run-context-file duplication. Converged
  independently with the Cursor review.
- [Test suite grooming, 2026-05-16](research-test-suite-grooming-2026-05-16.md) —
  *shipped*. Map of bloat, cross-file helper duplication, and
  intent-quality gaps in `tests/`; the high-leverage moves
  (`test_integration.py` removal, `tests/_helpers.py` extraction,
  `_forge_view_url` stub-based rewrite, docker-mounts parametrize)
  were executed in the same pass.
- [Branch plan simplification, 2026-05-12](research-branch-plan-simplification-2026-05-12.md) —
  follow-up critique of the accepted branch-intent implementation:
  preserve the mechanical seed/finalization contract that later fed
  the publish kernel, but shrink branch planning back to explicit
  event targets and stop treating inferred conversation branch history
  as hidden publish authority.
- [Daemon runner context ergonomics, 2026-05-09](research-runner-context-ergonomics-2026-05-09.md) —
  point-in-time review of a live brr daemon run: how much context the
  agent had to read, which prompt/runtime surfaces helped, where the
  Task Context Bundle was noisy, stale bundled-doc contradictions, and
  Docker image tooling gaps for brr self-work.
- [brr vs gh-aw](research-brr-vs-gh-aw.md) — deep comparison with
  GitHub Agentic Workflows: substrate / transport / durability /
  security / fleet axes, market fit for the remote-controlled
  repo-first CLI runner use case, which gh-aw ideas brr could
  credibly adopt vs. not.
- [Positioning and runtime dependencies, 2026-05-21](research-positioning-and-runtime-deps-2026-05-21.md) —
  reframes the zero-dependency constraint as one symptom of a broader
  positioning question. Per-candidate cost-benefit (`dulwich`: pass;
  `requests`: take; per-forge SDKs: defer — the `requests` slice was
  accepted in [`decision-runtime-dependencies.md`](decision-runtime-dependencies.md)),
  then a Part 2 on what brr has to do to "pop" with the AI-tool
  creator crowd — tagline, README compression, `uvx`-first install,
  demo video shot-list, and ranked moves. The highest-leverage
  remaining adoption move is a 60-90s Telegram demo video, not code.
