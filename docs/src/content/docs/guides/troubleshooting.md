---
title: Troubleshooting
description: Diagnose the daemon, gates, Runners, updates, and service lifecycle.
---

## Is the daemon running?

```bash
brnrd daemon status
brnrd daemon logs --no-follow
```

Drop `--no-follow` to keep following the service log. Use `-n 200` to change
the number of existing lines shown first.

For setup and development, run it in the foreground:

```bash
brnrd up --foreground
```

## Is the gate configured?

```bash
brnrd gate list
brnrd account status
```

`account status` is useful for managed setups; `gate list` shows gate state for
the current repo.

## Can brnrd see a Runner?

```bash
brnrd runners list
brnrd runners list --all
```

Authenticate the selected CLI outside brnrd, then retry.

## A repo card reads "not initialised" or "init state unknown"

Two different facts wear these labels on `/repos`:

- **"not initialised"** — a daemon measured the checkout and found
  `AGENTS.md` or the knowledge base missing. The advice on the card is
  the fix: run `brnrd` in that checkout.
- **"init state unknown"** — no daemon has reported for this repository
  yet. Running `brnrd` in the checkout will not change this line, because
  nothing is watching that tree. Check `brnrd daemon status` in the
  checkout the daemon runs from, and confirm the repository is registered
  with `brnrd account add <path>` — a machine's one daemon serves every
  repository it has been told about, and reports each one separately.

If the second line persists after the daemon restarts, the daemon predates
per-repository reporting — update it (`npm install -g brnrd`).

## A run died and the error mentions sleep

If a run comes back with something like *"your computer went to sleep
mid-response"*, take it literally. The agent CLI is reporting that its own
process was suspended, and it is usually right.

On macOS, `pmset -g log | grep -E "Sleep|DarkWake"` shows the transitions with
timestamps — line up a dead run's start and end against them. A machine set to
idle-sleep will wake briefly every fifteen minutes or so for maintenance, and a
daemon that starts a run inside one of those windows gets about forty-five
seconds before the machine sleeps on top of it. The symptom looks like an
unstable agent or a flaky network; the cause is one setting.

The fix is in [Prerequisites](../../getting-started/prerequisites/#5-a-machine-that-stays-awake):

```bash
sudo pmset -a sleep 0 disksleep 0
```

While the machine is asleep, its daemon publishes no heartbeat, so the chat bot
may also tell you no daemon is online and suggest re-pairing. Do not re-pair —
queued messages drain by themselves as soon as the machine is awake again.

## Update

There is no `brnrd update` command. The loom updates its own checkout: it
offers a green main (or, with `update = "stable"`, the newest release) and
`brnrd runtime update take` merges it. See
[Install](../../getting-started/install/#updates-from-main).
`npm update -g brnrd` updates only the npm launcher.

## `brnrd: command not found`

The installer puts the command at `~/.brnrd/bin/brnrd` and ends with the line
that adds `~/.brnrd/bin` to your `PATH`. Add it to your shell profile, open a
new shell, or call `~/.brnrd/bin/brnrd` directly.

## `loom install` refuses a source checkout

```text
loom install: /…/brnrd/.venv/bin/python3 imports brr from /…/brnrd/src/brr, a source checkout, …
```

The `brnrd` your shell found is not the installer's `~/.brnrd/bin/brnrd`: an activated virtualenv (or `PYTHONPATH`) puts a source
checkout's `brnrd` first. A service installed from it would keep running that
checkout, at whatever commit it is on, long after you upgraded. Leave the
virtualenv and let the shell find the installed command again:

```bash
deactivate; hash -r
brnrd --version        # the version you installed
brnrd loom install
```

Pass `--dev` when running the checkout is what you mean; the receipt says
`(source checkout, --dev)` next to the version.

## Stop or uninstall the service

```bash
brnrd daemon down
brnrd daemon uninstall
```

On Linux, uninstall may ask whether to disable systemd linger if brnrd enabled
it earlier. The command has explicit `--yes-disable-linger` and
`--no-disable-linger` choices for non-interactive use.

If the problem persists, report the command, output, operating system, and
`brnrd --version` at [hugimuni-labs/brnrd/issues](https://github.com/hugimuni-labs/brnrd/issues).
