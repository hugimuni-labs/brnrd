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
Gates currently authorize a channel or trigger, not each sender. Until
[#408](https://github.com/hugimuni-labs/brnrd/issues/408) and
[#409](https://github.com/hugimuni-labs/brnrd/issues/409) land, use private repos
only and prefer the managed one-to-one Telegram path. Do not connect a
public-repo GitHub gate or trust a group chat.
:::

## Keep it in the foreground

While setting up, you can run the daemon in the terminal instead of installing
the service:

```bash
brnrd up --foreground
```

Next: [send the first task](../first-task/).
