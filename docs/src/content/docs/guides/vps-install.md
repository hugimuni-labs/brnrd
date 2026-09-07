---
title: No always-on machine? Use a small VPS
description: brnrd is a daemon — it needs a machine that stays on. A cheap VPS is the fastest way to get one.
---

brnrd runs as a background daemon on hardware you own. If your laptop sleeps
when you close the lid, the daemon sleeps with it — there is no "always on"
without a machine that stays on. A Mac mini or a home server both work; so
does the smallest tier of any VPS provider, usually **$4–6/month** (Hetzner,
DigitalOcean, Vultr, and similar all sell one — check current pricing, it
moves). This page is the same install as [Install](../../getting-started/install/),
run over SSH instead of a local terminal, plus the two steps that are
specific to a headless box.

Nothing below is a brnrd-specific product — it's a normal small Linux VPS
running brnrd the same way it would run any other daemon.

## 1. Get a box and SSH in

Any small Ubuntu or Debian instance works. brnrd itself only needs **git**
and **Python 3.10+** (the npm and uv install routes provision Python for you
if it's missing — see [Install](../../getting-started/install/) → "The
Python part"). A stock Ubuntu image already has both, or close enough that
the installer's fallback handles the gap.

```bash
ssh you@your-vps-ip
```

## 2. Install a coding-agent CLI, and authenticate it

brnrd drives Claude Code or Codex — it doesn't replace them, so one has to
be on the box first, authenticated with your own subscription or API key.

:::caution[Headless auth is CLI-specific — not fully verified here]
Both CLIs' normal login flow opens a browser on the same machine. A VPS has
no browser. As of this writing, each vendor has *some* path around that —
pasting a printed login URL into a browser on your phone or laptop, or an
API-key environment variable — but the exact flag and prompts change
between CLI versions. Run `claude --help` or `codex --help` on the box and
follow its own headless-login instructions; this guide doesn't reproduce
them because they weren't re-verified for this page.
:::

## 3. Install brnrd

Same three routes as any machine:

```bash
npm install -g brnrd         # if you have Node
# or: uv tool install brnrd
# or: pipx install brnrd
brnrd --version
```

## 4. Run it

```bash
brnrd
```

The guided setup pairs this VPS with your brnrd account and prints a
pairing link. **Open that link on your phone or laptop** — it's a link you
approve, not a localhost callback, so it doesn't need a browser on the VPS
itself. From there it installs and starts the background service
(a systemd unit on Linux) the same way it would on a desktop.

Prefer no account at all? `brnrd gate setup telegram && brnrd daemon install`
wires a self-hosted Telegram gate directly, with no brnrd account or payment
involved (see [Connect](../../getting-started/connect/)).

## What you don't need to open

Self-hosted Telegram polls Telegram for updates (`getUpdates`) instead of
receiving a webhook, so it needs outbound internet access only — no inbound
firewall rule or open port to configure for that gate. (Other gates may
differ; this guide only checks the one most people reach for first.)

## Next

[Send your first task](../../getting-started/first-task/) — from the VPS
this works exactly like it does anywhere else; the daemon doesn't know or
care that its machine happens to be rented by the hour.
