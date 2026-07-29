---
title: Gates & authorization
description: Understand how channels reach the daemon and who can trigger work today.
---

A gate is the door between a channel and the daemon on your machine. Telegram,
Slack, GitHub, and the managed cloud path carry requests in and replies out.
The dashboard is another way to watch and steer the same local work.

While a run is active, its portals carry the live progress card, interim
replies, follow-up messages, and final handoffs. That makes a long task
observable and correctable instead of silent.

## Authorization today

Authorization happens before enqueue. GitHub and Telegram bind it to a person;
Slack still binds it to the configured channel.

| Gate | Who can trigger a run today |
|---|---|
| Managed or self-hosted Telegram | The paired user plus explicitly allowlisted user ids. Other group members and unattributed senders are denied. |
| Self-hosted Slack | Any member of the polled channel. |
| GitHub (self-hosted) | Logins with `write`, `maintain`, or `admin` permission, plus explicitly allowlisted logins. Public commenters and read-only users are denied. |
| GitHub (managed) | Addressed comments require GitHub's signed `OWNER`, `MEMBER`, or `COLLABORATOR` author association, or an explicitly allowlisted login. Applying the `brnrd` label is the universal assignment signal. Assigning an issue or pull request to the optional marker account is also a summon, and so is requesting a review from it on a pull request. |

The operating rules follow from that boundary:

- keep GitHub and Telegram allowlists narrow;
- remember that a Telegram group does not authorize its whole membership by default;
- use Slack only when every member of the configured channel may drive the daemon;
- set `trust.collaborator_env=solitary` when authorized collaborators should not
  inherit the operator's normal runtime authority;
- remember that every inbound message becomes potential instruction to an
  approval-bypassed coding agent.

See [Connect](../../getting-started/connect/) for setup commands and
[Security & privacy](../../security/) for the full trust posture.

## Separate the door from the author

A gate credential owns ingress and replies for that channel. Runner-authored
GitHub produce—branches, pull requests, issue comments—is a separate identity.
Set a dedicated account's token as `GH_TOKEN` in the daemon environment to
make that identity authoritative for runner subprocesses; inherited
`GITHUB_TOKEN` credentials are then withheld from the runner. Use the narrowest
token type and repository permissions GitHub supports for the ownership model.

Git commit attribution is independent of API authentication. Configure
`user.name` and an email verified by the dedicated account if commits should
also appear as authored by it. Never write either token into the repository or
brnrd config. The dedicated account needs Write access to create branches; a
comment-only or Triage collaborator cannot publish the runner's work.

Managed mode keeps the visible summons marker separate from the acting
identity. The installed `brnrd-dev` GitHub App creates a `brnrd` label when it
first discovers a repository; applying that label or mentioning `@brnrd-bot`
summons the resident without granting a second account repository access. The
App receives the signed event and posts replies, branches, and pull requests
with its short-lived installation token.

The `brnrd-bot` user account may additionally occupy GitHub's assignee or
reviewer slots after a repository owner grants it access. That is an optional
GitHub affordance, not the universal route: automating the collaborator
invitation would require giving the App repository **Administration: write**
solely to manage the marker account. brnrd deliberately keeps that broader
permission out of the install contract; the App-native label carries the same
repo-centered intent with the existing Issues permission.

**The two summons verbs need different grants on the marker account**, and the
difference is easy to get wrong:

- **assignment** — the marker must have **Write** permission, or be an
  organization member with Read. GitHub's assignable set is exactly that; a
  Read-only outside collaborator never appears in the assignee picker, and the
  refusal looks like a missing account rather than a missing grant.
- **review request** — **Read** is enough. Anyone with read access to the
  repository can be requested as a reviewer.

So a review request is the cheaper summons: it lets the marker stay a
read-only account. (An earlier revision of this page claimed Read sufficed for
the assignee slot. It does not; corrected 2026-07-29.)
