# brnrd

```bash
npx brnrd init
```

**brnrd is a Python program.** This npm package is a bootstrapping installer: on
first run it creates a durable virtualenv, installs `brnrd` from PyPI into it,
and hands over. Every run after that is just a launch.

It exists because brnrd's users already live in npx — that is how the AI coding
tools ship — and most of them have Node without having `uv` or `pipx`. A
launcher that only forwarded to `uvx` would be useless to exactly the person it
was written for.

The install is **durable, not ephemeral** (`~/.local/share/brnrd/venv`, or
`$BRNRD_HOME`), so `npx brnrd daemon install` works: the service unit points at
a directory that will still be there tomorrow.

It never downloads a Python and never pipes a script into a shell. If no Python
is present, it says so and stops — that is a requirement no launcher can conjure
away. If `uv` happens to be installed it is used as an accelerator; the result is
identical.

Equivalent, if you'd rather not go through npm at all:

```bash
pip install brnrd
uvx brnrd            # zero-install run, if you have uv
```

The launcher's version *is* the payload's version: `npx brnrd@0.1.0` installs
brnrd 0.1.0.

Source and docs: https://github.com/Gurio/brr
