---
title: Install
description: Install the brnrd command, and check that it can see a coding-agent Runner.
---

Use whichever command matches the tooling you already have — just the one;
all three install the same program and leave you with a `brnrd` command on
your `PATH`.

If you have Node:

```bash
npm install -g brnrd
```

If you have uv:

```bash
uv tool install brnrd
```

If you have pipx:

```bash
pipx install brnrd
```

Check it:

```bash
brnrd --version
brnrd --help
```

brnrd also needs **git**, and at least one coding-agent CLI on your `PATH` —
Claude Code (`claude`) or Codex (`codex`) — authenticated with your own
subscription or API key. brnrd drives that CLI; it never asks you for a model
key of its own.

Self-hosted gates and local execution are free. No brnrd account is needed to
use them.

## Next

Continue to [Connect](../connect/) and choose your door.

## Trying it without installing

```bash
npx brnrd init
```

`npx` runs brnrd once without adding anything to your `PATH`, so there is no
`brnrd` command afterwards — every later command has to be `npx brnrd …` as
well. Good for a first look; if you keep using it, install it properly with one
of the lines above.

:::note[Managed account, one line]
`npx brnrd account connect` bootstraps, pairs, and starts the daemon in a single
command — no separate install step.
:::

## The Python part, for when it matters

brnrd is a Python program. The npm package is a launcher: on first run it builds
a private, durable virtualenv under `~/.local/share/brnrd` (or `$BRNRD_HOME`),
installs brnrd from PyPI into it, and hands over. Every run after that is just a
launch. Your system Python is not touched and your `PATH` is not rewritten by
the launcher itself — only by npm, for the `brnrd` command.

That durability is what makes the npm route a real install: `brnrd daemon
install` writes a systemd/launchd unit pointing into that virtualenv, and it is
still there tomorrow.

If no Python is on the machine, the launcher downloads a checksum-pinned
[uv](https://github.com/astral-sh/uv) release and lets uv provision CPython,
under the same directory. It never pipes a script into a shell.

:::caution[Not `uvx brnrd` or `pipx run brnrd`]
Both run from a disposable, per-invocation environment. `brnrd daemon install`
would pin the background service to a binary path that disappears with it. Use a
persistent install, then install the service.
:::

## Development install

```bash
git clone https://github.com/hugimuni-labs/brnrd
cd brnrd
pip install -e ".[dev]"
pytest
```

For remote-assisted brr development — running the daemon against your own
editable checkout so it re-execs itself between tasks as you change brr's own
code:

```bash
brnrd up --dev-reload
```
