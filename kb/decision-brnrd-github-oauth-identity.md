# Decision: brnrd identity uses GitHub OAuth

Status: accepted on 2026-06-03

## Decision

brnrd accounts are GitHub identities, not local email/password
identities. The hosted product uses the managed brnrd GitHub App's
OAuth web flow as "Sign in with GitHub"; self-hosted brnrd operators
configure their own GitHub App / OAuth client. Email/password signup
and login are removed before launch, with no fallback path.

The existing brnrd bearer-token model stays. Session tokens, future
account API keys, and daemon tokens remain high-entropy random
strings with SHA-256 lookup hashes and kind/scope checks in brnrd.
OAuth proves the user identity; brnrd tokens authorize brnrd API,
dashboard, and daemon operations after that identity is known.

## Rationale

brr's likely brnrd users already have GitHub accounts, and GitHub is
also the repo host brnrd integrates with first. Reusing GitHub removes
the launch burden of password reset, email verification, bot-signup
defence, and password storage choices. It also aligns the dashboard
identity with the managed GitHub App install flow instead of asking a
developer to create one more account before connecting the repo they
came from.

Dropping the fallback is intentional. A fallback would keep the exact
operational liabilities this decision is meant to remove, while adding
account-linking edge cases (`github_id` plus password identity, email
collision, recovery ambiguity). If GitHub is unavailable, brnrd login
waits; daemon bearer tokens that were already minted keep working.

## Current Shape

- Account rows key on stable `github_id`, with `github_login` refreshed
  on each login and optional verified email retained as billing /
  contact metadata.
- The OAuth callback creates the account on first login, seeds a
  `default` project, issues a normal brnrd session token, and sets the
  dashboard session cookie.
- The web flow uses a random state cookie plus PKCE. brnrd exchanges
  the callback code with GitHub and fetches `/user`; when the public
  user payload has no email, it asks GitHub's email endpoint and keeps
  the primary verified email when available.
- brnrd does not store GitHub OAuth refresh tokens for identity. The
  GitHub user token is used only during login to resolve identity, then
  discarded.
- `brr brnrd connect` still starts an unauthenticated device-style
  pair request. The browser approval page requires the GitHub-backed
  brnrd session before it can mint a project-scoped daemon token.

## Consequences

- Legacy prototype databases with email/password account rows are not a
  migration target; brnrd is still pre-launch and uses `create_all`
  rather than Alembic. The Postgres/migration cutover should start from
  the GitHub identity schema.
- Account API keys can still exist as brnrd bearer-token credentials,
  but they must be issued from an authenticated GitHub-backed account
  surface; there is no anonymous `POST /v1/accounts` bootstrap.
- Any future non-GitHub identity provider must justify the account-
  linking and support burden explicitly. Until then, GitHub is the only
  brnrd user identity provider.

## Links

- [`subject-managed-mode.md`](subject-managed-mode.md) — hub for the
  hosted brnrd product and managed dispatcher.
- [`design-brnrd-protocol.md`](design-brnrd-protocol.md) — protocol
  and data-minimization surface updated to reflect GitHub identity.
- [`plan-brnrd-inbox-prototype.md`](plan-brnrd-inbox-prototype.md) —
  executable prototype now uses GitHub login for account creation.
