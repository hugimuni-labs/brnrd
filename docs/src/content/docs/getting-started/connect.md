---
title: Connect
description: Connect a managed account or self-host a gate to your local daemon.
---

Connect gives the resident a *persistent* channel: one that reaches it
without a terminal open, from your phone or a chat app, any time.

Pick one door. Both routes run the agent on your machine. Self-hosted needs no
brnrd account and no payment; managed layers a connected account on top. They
compose — run a hosted and a self-hosted gate at once — only the repository
identity underneath is either/or.

## Managed: one account across repos

From the repository you want the resident to know:

```bash
brnrd account connect       # pair, install, and start
```

Nothing installed yet? `npx brnrd account connect` does the whole cold start in
one line — it bootstraps a durable install first, then runs the command. It
leaves no `brnrd` command on your `PATH` though, so `npm install -g brnrd`
afterwards (or instead) is worth the four seconds.

The managed connection relays messages and status between brnrd.dev and your
daemon. It does not move run execution to hosted compute. See
[Security & privacy](../../security/) for the derived-knowledge mirror used by
the dashboard.

Connect also prepares the resident's memory as account infrastructure. It
creates the local home and a separate knowledge Git repository, prepares the
repository's `.brnrd-kb/` working checkout, and uses the signed-in GitHub CLI
to adopt or create private `brnrd-home` and `brnrd-knowledge` remotes and push
both. Existing private remotes are reused. Pass `--local-memory` to make the
explicit local-only choice; the local home, knowledge repo, and checkout still
exist, but no GitHub remotes are created or linked. If `gh` is unavailable or
signed out, connect finishes locally and prints the `brnrd home link --yes`
command that resumes durability later.

GitHub organization installations are supported. A personal installation is
bound only when its target matches the signed-in GitHub login; an organization
installation is bound only after the App verifies that login as an active
organization owner. The ownership check uses the installation's short-lived
token and the App's Members-read permission—no user OAuth token is persisted.

The command installs a systemd user service on Linux or a LaunchAgent on macOS
and starts it immediately. Use `--no-service` when you want to keep the daemon
in a foreground terminal instead. Add further repositories to the same account
later with `brnrd account add <path>`.

## Self-hosted: bring your own gate

Telegram is the shortest setup path:

```bash
brnrd gate setup telegram   # authenticate and bind this repo
brnrd daemon install
```

The CLI also recognizes `slack`, `github`, `signal`, and `cloud` gate names. Use
`brnrd gate list` to inspect the gates configured for the current repo. Signal
needs a locally running
[signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api)
container first — see [Gates & authorization](../../concepts/gates/#signal-self-hosted)
for the linking steps and what v1 does not yet do (groups, attachments, a
live progress card).

:::caution[Know who can ring the doorbell]
GitHub, Telegram, and Signal are default-closed per sender. The self-hosted
GitHub gate verifies `write`, `maintain`, or `admin` permission; the managed
webhook requires GitHub's signed `OWNER`, `MEMBER`, or `COLLABORATOR`
association; both also accept explicitly allowlisted logins. Telegram accepts
the paired user plus explicitly allowlisted user ids; Signal accepts the
paired number plus explicitly allowlisted numbers. A public commenter,
another group member, or an unlisted sender cannot trigger a run merely by
reaching the channel.
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
