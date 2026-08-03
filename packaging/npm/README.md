# brnrd

```bash
npm install -g brnrd
brnrd --version
```

That gives you a real `brnrd` command, and every command in the docs works as
written. `npx brnrd <command>` also works if you just want a look — but npx puts
nothing on your PATH, so there is no `brnrd` command afterwards and every later
command has to be `npx brnrd …` too.

**brnrd is a Python program.** This npm package is a launcher: on first run it
creates a durable virtualenv, installs `brnrd` from PyPI into it, and hands over.
If Python is absent, it downloads a checksum-verified `uv` and lets uv provision
CPython. Every run after that is just a launch.

It exists because brnrd's users already live in npm — that is how the AI coding
tools ship — and most of them have Node without having `uv` or `pipx`. A
launcher that only forwarded to `uvx` would be useless to exactly the person it
was written for.

The install is **durable, not ephemeral** (`~/.local/share/brnrd/venv`, or
`$BRNRD_HOME`), so `brnrd daemon install` works: the service unit points at a
directory that will still be there tomorrow. That also makes `brnrd account
connect` a full one-command cold start: bootstrap, pair with brnrd, and start
the native service, all in one line.

It never pipes a script into a shell, modifies your system Python, or changes
your PATH itself — only npm does, for the `brnrd` command. An existing Python
remains the fast path; an existing `uv` is used as is; otherwise the launcher
fetches a pinned official uv release and verifies its SHA256 before execution.
The downloaded uv, managed CPython, caches, virtualenv, and brnrd install all
stay under `$BRNRD_HOME`.

Equivalent, if you'd rather not go through npm at all:

```bash
uv tool install brnrd
pipx install brnrd
pip install brnrd
```

Skip `uvx brnrd` and `pipx run brnrd` before `daemon install`, though: both run
from a disposable, per-invocation environment, and the systemd/launchd service
would end up pinned to a binary path that vanishes with it. Any of the installs
above gives the service a stable target.

Updating: `npm update -g brnrd`. The launcher's version *is* the payload's
version — a version-pinned `brnrd@<version>` installs that same brnrd version.

Source and docs: https://github.com/hugimuni-labs/brnrd
