# brnrd (npx launcher)

**brnrd is a Python tool.** This npm package is a launcher: it exists so that
`npx brnrd` lands on something honest instead of a 404 or a squatter.

```bash
npx brnrd init      # hands off to `uvx brnrd` (or `pipx run brnrd`)
```

The real installs, in preference order:

```bash
pip install brnrd   # normal install — required for `brnrd daemon install`
uvx brnrd           # zero-install run, straight from PyPI
```

The launcher will refuse `daemon install`: a long-lived service unit must not
point at an ephemeral environment.

Source, docs, and the actual product: https://github.com/Gurio/brr
