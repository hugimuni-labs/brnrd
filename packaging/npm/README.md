# brnrd

```bash
npm install -g brnrd
brnrd setup
```

This package is a launcher for brnrd's one install: a git checkout of brnrd at `~/.brnrd/src` that your
resident can change, with a stable command at `~/.brnrd/bin/brnrd`. The first run executes the installer
shipped inside this package (the same `install.sh` as `curl -fsSL https://brnrd.dev/install | sh`),
pinned to this package's release: it clones the tag `v<version>` on a branch `stable` and sets
`git config brnrd.update stable`. Every run after that hands the call to `~/.brnrd/bin/brnrd`.

`npx brnrd <command>` makes the same install. npx puts no `brnrd` on your PATH; the installer prints the
line that adds `~/.brnrd/bin`.

**Updating** is the loom's job, not npm's. With `update = stable` it offers each new release once its
checks are green, and `brnrd runtime update take` (or the resident) merges it into the checkout, alongside any changes of your own.
`npm update -g brnrd` changes only this launcher.

Requirements: macOS or Linux (on Windows, inside WSL), git, and `uv`. When uv is missing, the installer
downloads uv's official installer to a temporary file and runs it. Nothing is piped from the network into
a shell.

Upgrading from a launcher up to 0.7.x: those installed brnrd from PyPI into
`~/.local/share/brnrd/venv`. That venv is left in place and no longer used; the launcher says so once.

Source and docs: https://github.com/hugimuni-labs/brnrd
