# Design: GitHub gate (OSS) vs brnrd GitHub App (managed)

**Status: accepted 2026-05-27.** Boundary doc, not a build plan — codifies
what runs where, what each side owns exclusively, and what they share so
the managed slice in [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
can lean on the OSS gate's structure instead of re-implementing it.

Two GitHub integrations co-exist in the brnrd ecosystem and both will
keep co-existing post-launch:

- The **OSS gate** ([`src/brr/gates/github/`](../src/brr/gates/github/)),
  a polling adapter that turns GitHub activity into inbox events on a
  laptop-resident `brr` daemon. PAT-authenticated, single-repo, byo-setup.
- The **managed GitHub App**, hosted by brnrd ([`src/brnrd/`](../src/brnrd/)
  per [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md) Slice 1
  — not started). Webhook-driven, multi-tenant, installation-scoped,
  one-click setup via OAuth + App install.

They are not redundant. Different identity model, different setup cost,
different latency, different blast radius — see "Why both survive launch."
The contract this doc locks down is the **code seam**: which modules each
side owns alone, and which modules ship in `brr.gates.github` *because
brnrd will import them*.

## What OSS owns and keeps owning

The laptop daemon's GitHub gate. Stays in
[`src/brr/gates/github/`](../src/brr/gates/github/) as a built-in gate
behind `brr gate github setup`. Five concrete surfaces:

- **PAT authentication.** Token comes from `gh auth token`, env
  (`GITHUB_TOKEN` / `GH_TOKEN`), or interactive paste; resolution order
  is stored > `gh` CLI > env. No JWT minting, no installation-token
  refresh, no public key crypto — that's exactly the dep stack that
  disqualifies it from "zero red flags" for casual `pip install brr`
  users (see
  [`decision-runtime-dependencies.md`](decision-runtime-dependencies.md)).
  Token validation hits `GET /user` once at setup.
- **Four-trigger polling.** `label-on-issue`, `mention-in-comment`,
  `opened` (new issue / PR), and `any` (Watch-all). `opened` is the
  bounded maintainer-inbox mode: it sees newly created issues and PRs
  without subscribing to every comment, while `any` remains the explicit
  high-volume mode for every new issue, PR, and comment. Polling cycle is
  60s with conditional requests (ETag / If-None-Match per endpoint), so
  the steady-state cost on a quiet repo is roughly zero against the REST
  rate limit. Includes inline PR review comments (`/pulls/comments`) and
  PR review summaries (fetched lazily when a line comment surfaces a new
  `pull_request_review_id`).
- **Single-repo binding.** One repo per `.brr/`, set by
  `brr gate github setup` at adoption time. Multi-repo support would
  add binding-table complexity that's already paid for on the brnrd
  side (`repo_project_bindings`); no point duplicating it.
- **Response posting.** Top-level PR/issue comments via
  `/issues/{n}/comments`, inline review-thread replies via
  `/pulls/{n}/comments/{cid}/replies`, edits via PATCH for the live
  progress card. Quote-pointer preface mirrors GitHub's "Quote reply"
  UX so the conversation thread stays legible.
- **Live progress card.** A `task_created` packet posts a fresh
  comment and stores its ID; subsequent packets PATCH the same
  comment, with a fall-through to a fresh post if the original was
  deleted. State at `.brr/gates/github/progress/<task-id>.json`.

What OSS deliberately does **not** ship: webhooks (require a public URL
+ signature verification + reverse-proxy setup — exactly the friction
the managed path is designed to remove), GH App auth (native crypto
deps disqualify it from the zero-red-flags goal), reactions-as-signal
([deferred follow-up](#deferred-follow-ups) below), and standalone
summary-only reviews with no line comments (undiscoverable by polling
without per-PR scans that would explode the API budget).

## What brnrd owns exclusively

Lives at `src/brnrd/` per
[`decision-monorepo-structure.md`](decision-monorepo-structure.md).
None of the surfaces below land in `src/brr/`:

- **GitHub App registration + JWT minting + installation-token refresh.**
  brnrd holds the GH App private key in its credential vault (separate
  from the per-user vault — this is brnrd's own infrastructure
  credential, not a user's PAT). JWT crypto runs server-side, so the
  `cryptography` / `pyjwt[crypto]` stack stays out of the OSS daemon's
  install footprint.
- **Webhook receipt + signature verification + payload normalisation.**
  brnrd's FastAPI app accepts `installation`,
  `installation_repositories`, `issue_comment`,
  `pull_request_review_comment`, and (Slice 1+) `pull_request_review`
  webhooks, verifies the `X-Hub-Signature-256` HMAC against the
  per-installation secret, and normalises payloads to the event shape
  daemons receive via the `cloud` gate adapter (see
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) → Gates).
- **Multi-project routing.** Resolves
  `(installation_id, repo_full_name) → project_id` via
  `repo_project_bindings`; auto-binds on install,
  re-bindable from CLI / dashboard. One brnrd-hosted bot serves a
  user's N repos with no per-repo configuration.
- **Permission-prompt comment UX.** When the daemon raises a
  permission prompt (managed-compute spawn cost approval per
  [`plan-failover-compute.md`](plan-failover-compute.md)), brnrd
  posts the prompt as a PR/issue comment and watches for
  `@brr-bot approve` / `@brr-bot queue` replies. The OSS gate
  doesn't surface prompts — the failover dispatcher is brnrd's.
- **Hosted bot identity.** The `@brr-bot` (or whatever the App is
  named on the GitHub side) acting account belongs to brnrd. OSS
  PAT operators bring their own login.

What brnrd does **not** poll: brnrd is webhook-only on GitHub.
Conditional polling is the OSS gate's strategy for a bring-your-own
network surface; brnrd has a stable public URL so it doesn't need
the rate-limit dance at all.

## What both share — reused from `brr.gates.github/`

The package split is what makes the reuse structural rather than
accidental. Three modules ship in `brr.gates.github` *because brnrd
will import them*, and stay transport-agnostic:

- [**`paths`**](../src/brr/gates/github/paths.py) — endpoint path
  builders (`repo_issues`, `repo_issue_comments`, `repo_pulls_comments`,
  `pull`, `pull_review`, `issue_comments`, `issue_comment`,
  `pull_comment_replies`). Pure functions, no transport, no I/O. The
  wire contract; both sides MUST agree on these strings so the
  request-response shape stays uniform whether it came from a poll or
  a webhook.
- [**`cache`**](../src/brr/gates/github/cache.py) — polling cursor /
  ETag helpers (`_format_iso`, `_initial_since`). brnrd doesn't poll,
  but its event-replay tooling uses the same ISO formatting so a
  brnrd-managed inbox and an OSS-managed inbox stay diff-able. The
  ETag store shape lives here too in case a future brnrd worker wants
  to back-fill from REST on installation (one-shot bulk reads benefit
  from conditional requests just as polling does).
- [**`parse`**](../src/brr/gates/github/parse.py) — payload
  normalisation. `parse_origin_url` (OSS-only — repo autodetection from
  a git remote), but the meat is `_extract_issue_number`,
  `_extract_pr_number`, `_format_event_body`,
  `_format_review_comment_body`, `_login_to_skip_for_mention_trigger`,
  `_skip_mention_comment_author`. brnrd's webhook receiver normalises
  the *same* event meta (`github_kind`, `github_pr_number`,
  `github_path`, `github_line`, `branch_target`, …) so the daemon's
  task-construction code path works identically on cloud-gate events
  and OSS-gate events.

The reusable-core illusion is the obvious failure mode here: easy to
declare modules "reusable" without ever testing that brnrd can actually
use them. The discipline that closes that gap is brnrd's first commit:
it imports `paths` / `cache` / `parse` from `brr.gates.github`. If the
import doesn't take, the modules get refactored at that point, not
left as documentation theatre.

What brnrd does **not** import from `brr.gates.github`:

- `client` — sync `requests`, OSS-only. brnrd uses its own async
  `httpx[http2]` client server-side (and a likely retries wrapper),
  invoked against the same `paths`. Different transport, identical
  wire surface.
- `state` / `wizard` — interactive auth, PAT storage, gh-CLI / env
  fallback. brnrd doesn't paste tokens; it mints them from the App
  installation.
- `polling` / `loop` — the OSS gate's reason for existing. brnrd's
  webhook handler replaces this entire surface.
- `delivery` / `progress` — the OSS gate posts comments via the same
  PAT it polls with. brnrd posts via an installation token (different
  auth header, identical paths from `paths`).

## Why both survive launch

The managed path doesn't obsolete the OSS gate. They have different
setup costs, different identity models, different latency targets, and
different blast radius.

| Axis | OSS gate | Managed GH App |
|------|----------|----------------|
| Setup cost | One PAT paste + repo confirm | Install GH App in browser (no token paste) |
| Identity | User's PAT login (or a service account they own) | brnrd-hosted `@brr-bot` |
| Network surface | None (laptop polls outward) | Public webhook URL (brnrd's) |
| Latency | 60s poll cycle, 0s when ETag hits 304 | Sub-second webhook delivery |
| Multi-repo | One per `.brr/`, multi-repo via multi-daemon | One install serves N repos |
| Blast radius on bug | Per-laptop | Per-installation across all brnrd tenants |
| Trust model | User trusts their own machine | User trusts brnrd's ops + security posture |
| OSS self-host? | Yes (this is the OSS path) | Yes — self-host brnrd at any URL |

The OSS gate stays the **default zero-friction path for solo
developers** and the **lowest-trust posture for teams who don't want a
third party in the loop**. The managed App is the path for users who
want one bot serving all their repos and don't want to operate a
daemon that has to be running for GitHub activity to be acknowledged
quickly.

Both survive launch. Both stay supported. The decision is the user's,
not the project's.

## Deferred follow-ups

- **Reactions as signal.** `POST /repos/{repo}/issues/{n}/reactions`
  with `+1` could give a lightweight "approve" gesture for the future
  permission-prompt UX without the user typing
  `@brr-bot approve`. Cost is N more API calls per active prompt
  conversation. Filed as a follow-up; not in the current OSS slice and
  brnrd's webhook path handles `issue_comment` reactions for free,
  so the cost asymmetry favours implementing it brnrd-side first.
- **Reviews-with-summary-but-no-line-comments.** A reviewer who clicks
  "Approve" with a written-out message and zero inline comments
  produces a `pull_request_review` payload that neither
  `/issues/comments` nor `/pulls/comments` surfaces. The OSS gate
  can't catch it without polling every PR's `/reviews` (which would
  blow the API budget on busy repos). The managed App receives the
  `pull_request_review` webhook directly and forwards as a `pr-review`
  event — closes the gap with zero polling cost. Documented here so
  the asymmetry isn't a surprise.
- **GH App in OSS.** Would require crypto deps + a public URL +
  signature verification on the laptop side. Disqualified for the OSS
  path; the managed App exists precisely to remove those requirements
  from the user.

## Read next

- [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md) —
  the build sequence for the managed side; Slice 1 stands up the
  GitHub App adapter on top of the brnrd backend.
- [`design-brnrd-protocol.md`](design-brnrd-protocol.md) — the
  inbox-as-service wire format both `cloud` and `github` gates feed
  into, including the normalised event shape that makes
  `paths`/`cache`/`parse` reuse possible.
- [`subject-managed-mode.md`](subject-managed-mode.md) — strategic
  context for why brnrd exists at all alongside the OSS daemon.
- [`decision-runtime-dependencies.md`](decision-runtime-dependencies.md) —
  why `requests` is the OSS gate's dependency floor and why GH App
  crypto stays brnrd-side.
- [`subject-daemon.md`](subject-daemon.md) — where the OSS gate
  plugs into the daemon's loop (the `is_configured` / `run_loop` /
  `render_update` protocol).
