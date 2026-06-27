# brnrd GitHub App + bot user model

Status: active design, accepted after the GitHub callsign/autocomplete research on 2026-06-27

## Decision

Use two GitHub identities:

```text
brnrd-dev
  GitHub App
  repository-scoped access, webhooks, installation tokens, backend actions

brnrd-bot
  GitHub user / machine account
  human-facing GitHub UI: mentions, autocomplete, collaborators, review requests, assignments
```

The GitHub App remains the authority for repo access and event delivery. The bot user exists to make GitHub's native UI feel right.

## Why the App alone is not enough

The `brnrd-dev[bot]` issue-seeding experiment proved that an installed GitHub App can author repo activity, but it did not make the App appear in normal `@` autocomplete. That matches the public model: App-authored actions are attributed to the App installation, while collaborator-like UI surfaces are user/team-oriented.

In practice, products that need a smooth GitHub UX split identities:

- Charlie Labs documents `CharlieCreates` as the GitHub App and `CharlieHelps` as the GitHub user for mentions, review requests, assignments, and autocomplete.
- Cursor publicly has a GitHub App identity and a separate `@cursoragent` user identity.
- Claude has both `/apps/claude` and `/claude`, but that should be treated as a naming/ownership arrangement, not as proof that an App alone becomes a collaborator-style identity.

## Product implication

When a user enables a repo in brnrd:

```text
1. GitHub App installation gives brnrd backend access.
2. brnrd creates the repo record.
3. brnrd invites the bot user to the repo.
4. The local daemon pairs against that repo.
```

The webhook parser can still be simple: the App receives issue/PR comments and string-matches `@brnrd-bot`, `/brnrd`, and `brnrd:`. GitHub does not need to route the mention semantically to brnrd. The bot user is there so GitHub users can discover and type the callsign naturally.

## Permissions

Auto-inviting the bot user requires the GitHub App installation token to call the repository collaborator API. That means the GitHub App needs repository **Administration: write** permission.

The invited bot user's repository permission should be the smallest permission that gives the intended UI affordance. `triage` is the current default because the bot user is a UI handle, not the code-writing actor. The App remains the privileged repo actor.

Relevant env vars:

```text
BRNRD_GITHUB_BOT_LOGIN=brnrd-bot
BRNRD_GITHUB_BOT_USER_LOGIN=brnrd-bot
BRNRD_GITHUB_BOT_COLLABORATOR_PERMISSION=triage
```

`BRNRD_GITHUB_BOT_LOGIN` is the callsign brnrd matches in comments. `BRNRD_GITHUB_BOT_USER_LOGIN` is the GitHub user to invite as collaborator. They should usually be the same value.

## Invitation acceptance

The GitHub App can create or refresh the repository invitation. The bot user may still need to accept the invitation in GitHub before it appears as a full collaborator and starts showing in all native UI surfaces.

For organizations, an alternate path is to add the bot user to the organization or team that already has access to the repos where brnrd should be mentionable.

## Open questions

- Should brnrd later automate invitation acceptance using a bot-user token, or keep that as an explicit operator step?
- Is `triage` sufficient for autocomplete and mention discovery across private repos, or do review-request workflows need a stronger permission?
- Should the dashboard show bot invitation state once we add collaborator-status reads?
