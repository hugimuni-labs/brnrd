---
title: A second machine
description: Join a laptop to a resident that already runs on another machine, so the loom fails over to it and threads can be pinned to it.
---

A resident can run on two machines at once. One of them leads: it holds the
self's `refs/leases/inbox` lease, routes letters and runs the connectors. If
the leader stops renewing the lease (its network drops, its lid shuts), the
other machine takes the lease when it expires. A thread can also be pinned to
one machine, so only that machine runs it.

The second machine joins with `brnrd runtime machines join`, which clones
the existing self instead of seeding a new one.

`<home>` below is the loom home on each machine: the directory that holds
`self/`, `loom/` and `ledger/`.

## 1. The machine that already runs

Enable the shared lease on the existing machine:

```sh
brnrd runtime machines --home <home> enable https://github.com/<you>/<self-repo>.git
```

This writes `lease_remote`, `lease_mode = "strict"` and `router_ttl = 180`,
preserving other settings. Strict mode stops outward acts when the lease
expires during a partition. Joining a second machine writes the same settings
there. The lease renews every 60 s; followers poll every 40 s, with a 10 s
expiry margin. Healthy expiry takeover takes about 190–230 s after the last
renewal the follower observed. Slow Git calls and ticks add to that allowance.

Restart the loom. Each install's ledger then travels on its own
`refs/ledger/<install>` ref of the self. A machine without `lease_remote`
leads only itself and shares no ledger.

## 2. The new machine

1. Install brnrd and the harnesses you use, and log each one in. Check them:
   `claude auth status` and `codex login status`.
2. Join, with the same URL as `lease_remote`:

   ```bash
   brnrd runtime machines join https://github.com/<you>/<self-repo>.git
   ```

   It clones the self into an empty home (`--home`, `BRNRD_HOME`, or
   `~/brnrd`, as setup picks), mints an install id that no ledger on the
   remote holds, and writes `lease_remote` into this machine's
   `loom/config.toml`, with strict mode and a 180 s TTL. It refuses a home
   that already has a self, and a
   remote that has no `refs/leases/inbox` yet: that means step 1 is not done,
   and the first machine still leads on its own local lease.
3. Run `brnrd setup` for this machine's channel and logins. It finds the
   self and seeds nothing.
4. Install and start the service: `brnrd loom install`.

Do not copy `<home>/loom/` from the first machine. Each install id is bound
to the machine and home that minted it (`loom/install-host`), and every
ledger push carries that binding. A loom whose id was minted elsewhere, or
whose `refs/ledger/<install>` was pushed from another machine, does not
start. Its log says `not starting:` and why.

## 3. Check it

- On either machine, `<home>/ledger/facts/` should hold a folder for each
  install within a minute: the shuttle fetches the peer's ledger every 60 s.
- `brnrd runtime machines prefer <install>`, run on one machine with the
  other's id, refuses with "no ledger from that install here" until that
  ledger has arrived. Once it is accepted, the leader hands the inbox lease
  over at its next renew.

## Keep the laptop preferred

After its ledger has arrived, run once on either machine:

```sh
brnrd runtime machines --home <home> priority <laptop-install-id>
```

The preference lives in the shared lease ref and survives both machines'
restarts. When the laptop is absent the other machine leads. On returning,
the laptop requests handback; the current holder fences its outward acts
before releasing. With healthy ticks and GitHub, allow one TTL (180 s): a
40 s follower poll, a 60 s holder renew and another 40 s poll. No live lease
is seized. If the holder cannot respond, strict expiry remains the fallback.
The old holder also fences as soon as any poll observes a newer holder.

`prefer` remains a one-use handover. A persistent priority can subsequently
return the lead to its named machine; change `priority` to change that policy.
Both machines must run a version supporting priority before enabling it.

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
