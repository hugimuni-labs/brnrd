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

Authenticate the selected Claude Code or Codex CLI outside brnrd, then
retry.

## Update

There is no `brnrd update` command. Use the installer that owns the tool:

```bash
uv tool upgrade brnrd
# or: pipx upgrade brnrd
```

If you used the npm-shaped bootstrapper, rerun `npx brnrd init -i`.

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
