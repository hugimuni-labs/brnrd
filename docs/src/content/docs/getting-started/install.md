---
title: Install
description: Install the brnrd command, and check that it can see a coding-agent Runner.
---

New to terminal tooling? Start with the short [Prerequisites](../prerequisites/)
checklist, then come back here.

Use whichever command matches the tooling you already have — just the one.
Each installs the same program and leaves you with a `brnrd` command on
your `PATH`.

```bash
curl -LsSf https://hugimuni-labs.github.io/brnrd/install.sh | sh
```

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
Claude Code (`claude`), Codex (`codex`), Grok Build (`grok`), or Mistral Vibe
(`vibe`) — authenticated with your own subscription or API key. brnrd drives
that CLI; it never asks you for a model key of its own.

Self-hosted gates and local execution are free. No brnrd account is needed to
use them.

## Run the loom

The loom is brnrd's runtime: one process that holds your threads, starts a
coding-agent body for each, and hands them letters. It runs as a login
service, launchd on macOS and systemd --user on Linux:

```bash
uv tool install brnrd
brnrd loom install
```

That is the whole install. `brnrd loom install` ends with one receipt:

```text
interpreter: /Users/you/.local/share/uv/tools/brnrd/bin/python3
brnrd: (the version you installed)
home: /Users/you/brnrd (default)
body: claude/sonnet (picked: claude has fuel, session 91% week 76%; written to /Users/you/brnrd/loom/config.toml)
service: /Users/you/Library/LaunchAgents/dev.brnrd.loom.plist
replaced: none
pid: 4242
```

- **interpreter** is the Python the service runs. It is the one brnrd was
  installed into; install refuses a source checkout (see
  [Troubleshooting](../../guides/troubleshooting/#loom-install-refuses-a-source-checkout)).
- **home** is found for you: the installed service's home, else the one loom
  home on this machine (`~/brnrd`, or a home inside a brnrd account), else a
  new `~/brnrd`. When more than one could be meant, install lists them and asks
  for `--home`.
- **body** is the coding agent the loom runs, as shell/core. Unset, install
  asks Claude Code, then Codex (with the model in your `~/.codex/config.toml`),
  for its quota and writes the first one with fuel left. `brnrd loom config
  edit` changes it.
- **replaced** names the loom install stopped to make way. The service's own
  loom is reloaded; a loom running outside the service is sent SIGTERM, which
  ends only its loop. Bodies are never stopped: they keep their sessions, and
  the new loom picks them up.

The service freezes the installing shell's `PATH`: run `install` from the
shell whose tools (`claude`, `codex`, `gh`, …) your bodies should see.
`brnrd loom install --dry-run` prints the receipt without writing or starting
anything; `--no-start` writes the service and leaves it for the next login.
`brnrd loom status` and `brnrd loom logs` show it running; `stop`, `start` and
`uninstall` do what they say. `brnrd setup` pairs a channel such as Telegram,
so letters reach you away from the terminal.

To upgrade, run `uv tool upgrade brnrd` (or the route you installed with). The
running loom sees its files change and re-execs itself; its bodies resume from
the letters they held.

:::note[The loom or the daemon]
The loom (`brnrd loom …`) is the runtime brnrd's own resident runs on. The
daemon (`brnrd daemon install`, and the guided setup below) is the earlier one:
gates bound to repositories, optionally a brnrd account. They are separate
services; you need only one.
:::

## Or: the daemon's guided setup

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
synchronous, in the terminal, with no account or door required. If the
[GitHub CLI](https://cli.github.com/) (`gh`) is installed and signed in,
init reads that identity — the same `gh auth token` / `gh api user`
resolution the GitHub gate uses — and states who you are before
continuing; `gh` missing or signed out just means it continues without
stating one. Reach for init when you want one repo adopted without any of
the steps above. See [CLI reference](../../reference/cli/).
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

That durability is what makes the npm route a real install: `brnrd loom
install` and `brnrd daemon install` write a systemd/launchd unit pointing into that virtualenv, and it is
still there tomorrow.

If no Python is on the machine, the launcher downloads a checksum-pinned
[uv](https://github.com/astral-sh/uv) release and lets uv provision CPython,
under the same directory. It never pipes a script into a shell.

:::caution[Not `uvx brnrd` or `pipx run brnrd`]
Both run from a disposable, per-invocation environment. `brnrd loom install`
or `brnrd daemon install` would pin the background service to a binary path that disappears with it. Use a
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
