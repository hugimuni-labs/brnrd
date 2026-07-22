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
| GitHub (managed) | GitHub's signed `OWNER`, `MEMBER`, or `COLLABORATOR` author association, plus explicitly allowlisted logins. |

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
