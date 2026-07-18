---
title: Self-hosting brnrd
description: Operate the daemon, coding-agent CLIs, gates, and credentials on a machine you control.
---

Self-hosting means keeping the brnrd daemon and coding-agent CLIs on a machine
you control. That can be your workstation or an always-on host with access to
the repositories and credentials the resident needs.

Start with the normal [Install](../getting-started/install/), then configure a
self-hosted gate:

```bash
brnrd gate setup telegram
brnrd daemon install
brnrd daemon status
```

The installer uses a systemd user service on Linux or a LaunchAgent on macOS.
Use `brnrd daemon logs` for service output and `brnrd daemon uninstall` to
remove it.

## What you operate

- the host and its network exposure;
- the Claude Code, Codex, or Gemini CLI login;
- gate credentials and authorization choices;
- repository and resident-state backups;
- updates to this alpha software.

Project work and runner execution remain local to that host. A self-hosted
Telegram, Slack, or GitHub gate still carries messages through the transport's
own service.

Read [Security & privacy](../security/) before exposing a gate. In particular,
the currently shipped gates authorize rooms or trigger syntax rather than each
sender.

For managed service availability and pricing, see [brnrd.dev](https://brnrd.dev).
This page makes no parity or hosted-compute claim.
