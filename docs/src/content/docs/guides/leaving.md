---
title: Leaving brnrd
description: Wipe a machine, or end an account, and know what each step leaves behind.
---

There are two sides to leaving: the machine, and brnrd.dev. Do the machine
first, because one of its steps talks to brnrd.dev while the token still
works.

## The machine

```bash
brnrd account disconnect     # revoke this machine's token on brnrd.dev, then forget it here
brnrd loom uninstall         # stop the loom and remove its login service
brnrd daemon uninstall       # only if you ever installed the daemon
```

`account disconnect` revokes the machine's token on brnrd.dev and marks the
machine offline there. It also deletes the gate state under the loom home
(`--home`, `BRNRD_HOME`, or `~/brnrd`) and under the current repo. It prints
which machines went offline. If brnrd.dev did not answer, it says so, and the
token may still work until you delete the account.

What is left after that is files. The self is a git repository at
`~/brnrd/self`. Unless it has a remote, this is the only copy of the
resident's memory, so push or copy it before you delete anything you want to
keep. Then:

```bash
rm -rf ~/brnrd ~/.brnrd
uv tool uninstall brnrd      # or the route you installed with
```

## brnrd.dev

On the dashboard you can take things out one at a time:

- **Telegram and other chats**: *Repos* → messenger doors → **revoke** on each
  paired chat. Messages from that chat stop reaching the account.
- **A repo or self** (shown as `local/self-…`): *Repos* → **disconnect**.
- **A machine**: run `brnrd account disconnect` on it, as above. The
  dashboard has no button for this yet. A machine you can no longer reach
  stays registered until the account is deleted.

To remove everything at once, delete the account from the dashboard and type
your GitHub login to confirm. That one step erases the
machines, every token, repos, paired chats, pairing codes and the message
relay. It also cancels a live subscription. Only the billing ledger is kept, for
accounting, and the confirmation says so.

Deleting the account does not touch GitHub. Repositories the resident created
under your account, and the brnrd GitHub App installation, stay until you
remove them on github.com.
