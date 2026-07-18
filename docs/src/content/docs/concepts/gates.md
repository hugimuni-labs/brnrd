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

The critical rule is simple: **gates currently authorize the channel or trigger,
not the person**.

| Gate | Who can trigger a run today |
|---|---|
| Managed one-to-one Telegram | The paired user. This is the dogfooded path. |
| Self-hosted Telegram | Any chat that can reach an unbound bot; after binding, any member of that chat. |
| Self-hosted Slack | Any member of the polled channel. |
| GitHub | Any commenter who uses the trigger on a connected repo. On a public repo, that means anyone. |

[Gurio/brr#408](https://github.com/Gurio/brr/issues/408) tracks per-commenter
GitHub authorization. [Gurio/brr#409](https://github.com/Gurio/brr/issues/409)
tracks per-sender chat authorization. Both are release blockers.

Until they land:

- connect GitHub gates only to private repos;
- prefer managed one-to-one Telegram;
- do not treat a group chat as a trusted personal channel;
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
brnrd config.
