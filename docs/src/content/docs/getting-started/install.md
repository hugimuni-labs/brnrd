---
title: Install
description: Install the brnrd command, and check that it can see a coding-agent Runner.
---

New to terminal tooling? Start with the short [Prerequisites](../prerequisites/)
checklist, then come back here.

Install the resident's own checkout:

```bash
curl -fsSL https://brnrd.dev/install | sh
```

The installer clones `main` to `~/.brnrd/src` (`BRNRD_DEV_DIR` moves it),
prepares an editable install in a separate environment under `~/.brnrd/envs`,
and switches `~/.brnrd/current` only after source and board checks. It fetches
uv when needed. Node is not required: the matching board is downloaded and
verified against its commit, frontend tree and SHA256 digest.

A fresh install ends with `brnrd setup`; it does not start a service. An existing
loom service is reinstalled through the candidate interpreter and checked with
`loom status`; failed handoff restores its unit and launcher, and the failed
run's environment is removed. After a successful run, `~/.brnrd/envs` keeps the
current environment and the one before it; older installer environments are
removed, and nothing outside `~/.brnrd/envs` is touched. Reinstallation preserves
service environment variables and adds the new shell's tool directories to PATH.
The installer ends by checking which `brnrd` a new login shell runs; if an older
install (such as an old `uv tool install brnrd`) comes first, it names it
and the command that removes it, and leaves that to you.

Reruns never move checkout HEAD, switch branches or discard edits. A foreign
origin or non-git directory is refused. A machine whose loom already runs from
another checkout is refused before any write, with the line that keeps it:
`BRNRD_DEV_DIR=<that checkout>` before the install command. Pipx and global npm installs are detected
before writes; the installer gives the uninstall command and leaves it to you.
`--dev` is accepted for compatibility and selects the same installation.

There is one install kind. npm is a launcher over this installer (see
[Through npm](#through-npm)); the PyPI wheel, `uv tool install` and `pipx`
routes are retired. Following releases instead of main is a setting of the
same checkout, not another install.

To install a release instead of main, name its tag:
`curl -fsSL https://brnrd.dev/install | BRNRD_REF=vX.Y.Z sh`. The clone sits on a
local branch `stable` and follows releases.

Check it (in a new shell, `brnrd --version` does the same once the PATH line is in your profile):

```bash
~/.brnrd/bin/brnrd --version
~/.brnrd/bin/brnrd --help
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
brnrd loom install
```

That is the whole install. `brnrd loom install` ends with one receipt:

```text
interpreter: /Users/you/.brnrd/envs/install-XXXXXXXX/bin/python
brnrd: (the version you installed)
home: /Users/you/brnrd (default)
body: claude/sonnet (picked: claude has fuel, session 91% week 76%; written to /Users/you/brnrd/loom/config.toml)
service: /Users/you/Library/LaunchAgents/dev.brnrd.loom.plist
replaced: none
pid: 4242
```

- **interpreter** is the Python the service runs. It is the one brnrd was
  installed into. Installer-owned checkouts are accepted; a separate development
  checkout requires `--dev`.
- **home** is found for you: the installed service's home, else the one loom
  home on this machine (`~/brnrd`, or a home inside a brnrd account), else a
  new `~/brnrd`. When more than one could be meant, install lists them and asks
  for `--home`.
- **body** is the coding agent the loom runs: a shell such as `claude`, which
  uses that agent's own model choice, or shell/core to pin a model
  (`claude/sonnet`). Unset, install asks Claude Code, then Codex (with the model
  in your `~/.codex/config.toml`), for its quota and writes the first one with
  fuel left. `brnrd loom config edit` changes it.
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

## Add a project

From the project's folder, after `brnrd setup`:

```bash
brnrd add        # or: brnrd add .   or: brnrd add ~/code/app
```

It adds the project to the brnrd that setup made, in the same home, with no
second pairing. A checkout with an `origin` is added by that URL, so brnrd
works on it in its own copy and sends changes as pull requests; a folder
without one is added by its path. Running it again changes nothing. Then ask
brnrd for work in it by name.

## Open the local board

The board is the resident's page on this computer: its threads and what each
one is for, the letters in them, and a box to write to it. The loom serves it
on loopback only (`127.0.0.1`), so nothing outside this machine can reach it.
A fresh install leaves it off. One command turns it on:

```bash
brnrd setup channel --kind web --wait 0
```

Setup picks a free loopback port (4777 when it is free), prints the page's
address, and starts or restarts the loom. Open that address in your browser:

```text
http://127.0.0.1:4777
```

The port stays the same across setups, so a bookmark keeps working. To find the
address later, `brnrd loom status` prints it on its `board:` line, or says the
board is off and gives the command above; the loom log also records
`board: serving http://127.0.0.1:<port>` each time the board starts. A `board:`
line that ends in `(not answering)` means the port is set but the loom is not
serving it: `brnrd loom start`, then `brnrd loom logs`.

Setup picks `--home`, then `BRNRD_HOME`, the installed service's home, the only
home found or `~/brnrd`; several homes require `--home PATH`.

The installer fetches the board for its upstream commit. Frontend edits rebuild
in the background when npm is available; otherwise the last good board stays
served and the loom says once that npm is needed. A missing published board
does not block installation: it tries a supported local npm build, then the
newest verified ancestor asset with an older-commit notice. If none is available,
installation continues without a board and prints the command to fetch it later.
A changed frontend tree is built at once, whether or not the loom is busy; only
the retry of a missing asset waits until the loom is idle, at most once every
five minutes. Digest-mismatched archives are never served.
`BRNRD_BOARD_BASE` points the board download at a mirror of the `board-builds`
release assets; the commit, tree and digest checks are the same.

This is the loom board, separate from the older daemon's dashboard.

If multiple distinct `brnrd` executables are on `PATH`, the CLI warns on
stderr for person-facing commands: plain `--help`, setup (including bare
`brnrd`), and the loom service commands (including status). Runtime, launcher and hook
calls stay quiet. The warning names the invoked entry, imported Python
package, and executable paths in search order. Symlinks to the same executable
count once. The warning
does not run, remove or rewrite other installations; use the paths to check
which installation your shell is selecting before upgrading.

To refresh the interpreter, rerun the curl installer. To update source, the
resident accepts an update offer as described below; reinstalling never moves HEAD.

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

## Through npm

```bash
npm install -g brnrd
brnrd --version
```

The npm package is a launcher over the same installer. Its first run, when
there is no `~/.brnrd/bin/brnrd` yet, runs the `install.sh` it ships, pinned to
its own release (`BRNRD_REF=v<version>`): the checkout is a clone of that
release tag on a local branch `stable`, set to follow releases (see
"Following releases instead of main" below). Every run after that hands over
to `~/.brnrd/bin/brnrd`. `npx brnrd` does the same, so it installs too.

`npm update -g brnrd` updates only the launcher; the loom follows releases by
itself. On Windows, use WSL: the loom has never run on native Windows, and the
launcher says so. A virtualenv an older launcher left under
`~/.local/share/brnrd` is named once and left alone.

## Updates from main

The default checkout follows main without a config line. On a green `main` CI it
sends the resident a letter with merged PR numbers, titles, links and description excerpts,
its own commit count, and whether tracked files have uncommitted changes.
Descriptions use the first paragraph, at most 300 characters, and say when cut.
The letter and stored offer share at most 4,500 bytes of PR details; an omitted
count points to `runtime update`, which lists every landing from git.
A missing PR description falls back to its commit subject. It waits for the resident's decision:

```bash
brnrd runtime update                  # read the current offer, including a newer green tip
brnrd runtime update take             # merge, check imports/config, then load at an idle tick
brnrd runtime update hold --why 'I need to reconcile my changes first'
```

Take works on any branch: a fast-forward when possible, otherwise a merge
over the resident's own commits. Commit tracked edits first. A conflict aborts
the merge and names the files; reconcile by hand, commit, and run take again.
A failed import/config check rolls back the merge. That check does not start
connectors or prove a full service start; launcher recovery and dependency
reinstallation are separate work. A hold settles the letter; the next green
tip offers again. While an offer is open a newer green tip updates its target
without a second letter. How the resident asks or tells its person follows
their agreement, with no new config setting. `update = "off"` stays off.

### Following releases instead of main

Releases are a setting of the same mechanism, not another install:

```toml
# loom/config.toml in the home (e.g. ~/brnrd/loom/config.toml)
update = "stable"
```

The same checkout is then offered origin's newest release tag (`vX.Y.Z`;
a pre-release such as `v0.8.0rc1` is never offered) instead of main's tip.
The tag is judged by main's own CI gate, and the letter, `update take`, the
import/config check, its rollback and the idle reload are the ones above.
A checkout already ahead of every release, as a fresh install of main is,
stays where it is until a newer release passes it.

The installer warns about `PYTHONPATH`, missing coding-agent CLIs, and missing
or signed-out `gh`. Run it from the login shell whose tools the service should
see. Updating source uses the resident-controlled merge above, independently of
refreshing its Python environment.

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
