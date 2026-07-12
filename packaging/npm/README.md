# brnrd

```bash
npx brnrd init
```

**brnrd is a Python program.** This npm package is a bootstrapping installer: on
first run it creates a durable virtualenv, installs `brnrd` from PyPI into it,
and hands over. If Python is absent, it downloads a checksum-verified `uv` and
lets uv provision CPython. Every run after that is just a launch.

It exists because brnrd's users already live in npx — that is how the AI coding
tools ship — and most of them have Node without having `uv` or `pipx`. A
launcher that only forwarded to `uvx` would be useless to exactly the person it
was written for.

The install is **durable, not ephemeral** (`~/.local/share/brnrd/venv`, or
`$BRNRD_HOME`), so `npx brnrd daemon install` works: the service unit points at
a directory that will still be there tomorrow.

It never pipes a script into a shell, modifies your system Python, or changes
your PATH. An existing Python remains the fast path; an existing `uv` is used as
is; otherwise the launcher fetches a pinned official uv release and verifies its
SHA256 before execution. The downloaded uv, managed CPython, caches, virtualenv,
and brnrd install all stay under `$BRNRD_HOME`.

Equivalent, if you'd rather not go through npm at all:

```bash
pip install brnrd
uvx brnrd            # zero-install run, if you have uv
```

The launcher's version *is* the payload's version: `npx brnrd@0.1.0` installs
brnrd 0.1.0.

Source and docs: https://github.com/Gurio/brr
