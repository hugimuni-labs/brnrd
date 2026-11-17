# brnrd

brnrd is no longer installed from PyPI. This is the last release published here.

Install it with one line:

```bash
curl -fsSL https://brnrd.dev/install | sh
```

or, if you have Node, `npm install -g brnrd`, which runs the same installer pinned to its release.

Both make one kind of install: a git checkout of brnrd at `~/.brnrd/src` that your resident can change,
with a stable command at `~/.brnrd/bin/brnrd`. The loom keeps it up to date, following `main` or, with
`update = "stable"`, the releases.

Coming from `uv tool install` or `pipx`: run the installer above. A pipx brnrd stops it before any
write, with `pipx uninstall brnrd` to run first; a uv tool brnrd that a login shell still finds first is
named at the end, with `uv tool uninstall brnrd`. It never removes either for you.

Docs: https://hugimuni-labs.github.io/brnrd/ · Source: https://github.com/hugimuni-labs/brnrd
