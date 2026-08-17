---
title: Install
description: Install the brnrd command, and check that it can see a coding-agent Runner.
---

New to terminal tooling? Start with the short [Prerequisites](../prerequisites/)
checklist, then come back here.

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

## Run `brnrd`

```bash
brnrd
```

Run this once, from the repository you want brnrd to work in. Bare `brnrd`
is the guided setup: it narrates each step as it runs it, prints the exact
command for anything it can't do without you, and never redoes a step
that's already standing. In order:

- checks for a coding-agent CLI on your `PATH`;
- pairs this machine with your brnrd account — a link you approve in your
  browser — then installs and starts the background service;
- picks where resident memory lives;
- offers a door (Telegram, Slack, or Signal) so brnrd can reach you without
  a terminal open;
- queues the first run: a conversation, not a form, that writes the
  repository's contract (`AGENTS.md`) with you.

If the [GitHub CLI](https://cli.github.com/) (`gh`) is already installed and
signed in, that first run reads that identity — the same `gh auth token` /
`gh api user` resolution the GitHub gate uses — and states who you are
before continuing. No brnrd account, no token entry, and no separate step:
`gh` not installed or not signed in just means it continues without stating
an identity, same as any other optional step here.

Re-run `brnrd` any time; it resumes from whatever step is still standing,
and a finished repo gets a four-line receipt instead of repeating setup.
Execution never leaves your machine.

A quick sanity check, once a coding-agent CLI is on `PATH`, that doesn't
wait on any of the above:

```bash
brnrd run "summarize the test layout; do not change files"
```

runs it right there, in the terminal — nothing else installed or connected.

:::note[A single repo, no account]
`brnrd init` is the narrower verb bare `brnrd` replaced as the default
teaching path here — it still works: the same `.brr/` setup and interview,
synchronous, in the terminal, with no account or door required. Reach for
it when you want one repo adopted without any of the steps above. See
[CLI reference](../../reference/cli/).
:::

## Next

Continue to [Connect](../connect/) for a persistent channel — your phone, a
chat gate, an identity that isn't you — or skip straight to
[First task](../first-task/) and keep working from the terminal.

## Trying it without installing

```bash
npx brnrd
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
