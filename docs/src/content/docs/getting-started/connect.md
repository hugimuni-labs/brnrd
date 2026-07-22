---
title: Connect
description: Connect a managed account or self-host a gate to your local daemon.
---

Pick one door. Both routes run the agent on your machine.

## Managed: one account across repos

From the repository you want the resident to know:

```bash
brnrd account connect       # pair this machine with brnrd.dev
brnrd account add .         # add the current repo
brnrd daemon install        # install and start the user service
```

The managed connection relays messages and status between brnrd.dev and your
daemon. It does not move run execution to hosted compute. See
[Security & privacy](../../security/) for the derived-knowledge mirror used by
the dashboard.

## Self-hosted: bring your own gate

Telegram is the shortest setup path:

```bash
brnrd gate setup telegram   # authenticate and bind this repo
brnrd daemon install
```

The CLI also recognizes `slack`, `github`, and `cloud` gate names. Use
`brnrd gate list` to inspect the gates configured for the current repo.

:::caution[Know who can ring the doorbell]
GitHub and Telegram are default-closed per sender. The self-hosted GitHub gate
verifies `write`, `maintain`, or `admin` permission; the managed webhook requires
GitHub's signed `OWNER`, `MEMBER`, or `COLLABORATOR` association; both also accept
explicitly allowlisted logins. Telegram accepts the paired user plus explicitly
allowlisted user ids. A public commenter or another group member cannot trigger a
run merely by reaching the channel.
Slack uses its admin-installed app and the configured channel as the boundary:
ordinary conversation is ignored, and a channel member must explicitly mention
the app to submit work. Slack senders run as collaborators, never as the owner;
use a deliberately chosen channel and set `trust.collaborator_env=solitary` when
those members should not inherit your normal runtime authority.
:::

## Keep it in the foreground

While setting up, you can run the daemon in the terminal instead of installing
the service:

```bash
brnrd up --foreground
```

Next: [send the first task](../first-task/).
