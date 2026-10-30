---
title: A second machine
description: Join a laptop to a resident that already runs on another machine, so the loom fails over to it and threads can be pinned to it.
---

A resident can run on two machines at once. One of them leads: it holds the
self's `refs/leases/inbox` lease, routes letters and runs the connectors. If
the leader stops renewing the lease (its network drops, its lid shuts), the
other machine takes the lease when it expires. A thread can also be pinned to
one machine, so only that machine runs it.

There is no `join` command yet. Every setup path seeds a new self, so the
second machine is joined by hand. Do these steps in order.

`<home>` below is the loom home on each machine: the directory that holds
`self/`, `loom/` and `ledger/`.

## 1. The machine that already runs

Point the lease at the self's GitHub repository, in `<home>/loom/config.toml`:

```toml
lease_remote = "https://github.com/<you>/<self-repo>.git"
```

The lease lasts `router_ttl`, and at least 90 s. `lease_mode` decides what a
leader does when it cannot reach GitHub. With `contested`, the default, it
keeps routing unless another machine's ledger moved in the last 10 minutes.
With `strict` it stops when its lease runs out, even when it is the only
machine.

Restart the loom. Each install's ledger then travels on its own
`refs/ledger/<install>` ref of the self. A machine without `lease_remote`
leads only itself and shares no ledger.

## 2. The new machine

1. Install brnrd and the harnesses you use, and log each one in. Check them:
   `claude auth status` and `codex login status`.
2. Clone the existing self into `<home>/self` **before** any setup:

   ```bash
   git clone https://github.com/<you>/<self-repo>.git <home>/self
   ```

   Setup never overwrites a self that is already a git checkout. Without one
   it seeds a new self and would try to push it over yours.
3. Do not copy `<home>/loom/` from the first machine. `loom/install-id` must
   be this machine's own. Two machines with the same id read each other's
   lease as their own, and nothing refuses that. The new home writes a fresh
   id the first time it runs.
4. Write the same `lease_remote` line into this machine's
   `<home>/loom/config.toml`. `config.toml` is per machine, and the self does
   not carry it.
5. Install and start the service: `brnrd loom install`.

## 3. Check it

- On either machine, `<home>/ledger/facts/` should hold a folder for each
  install within a minute: the shuttle fetches the peer's ledger every 60 s.
- `brnrd runtime machines prefer <install>`, run on one machine with the
  other's id, refuses with "no ledger from that install here" until that
  ledger has arrived. Once it is accepted, the leader hands the inbox lease
  over at its next renew.

## Moving work between machines

- `brnrd runtime machines prefer <install>`: hand the lead to that machine.
- `brnrd runtime machines pin <thread> <install>`: only that machine runs
  the thread. A body running the thread elsewhere leaves. Inbox cannot be
  pinned: it follows the lead.
- `brnrd runtime machines unpin <thread>`: the thread follows the lead again.

A pin travels as a ledger fact, so the leader may take up to 60 s to see it.

## Not checked yet

These steps follow the code. They have not yet been run end to end on a
second machine:

- whether `brnrd setup` on the new machine, with the self already cloned,
  connects the same chat channel without pairing a new one;
- whether a brnrd cloud login on the second machine needs anything beyond
  logging in to the same account.
