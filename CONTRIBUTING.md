# Contributing

Thanks for looking at brnrd. This is short on purpose — dev setup and what we
expect from a PR.

## Dev setup

Python 3.10+ and git are required.

```bash
git clone https://github.com/hugimuni-labs/brnrd
cd brnrd
pip install -e ".[dev]"   # or: uv pip install -e ".[dev]"
pytest
```

Frontend (`src/frontend/`, its own SvelteKit project, AGPLv3-licensed):

```bash
cd src/frontend
npm install
npm test
```

The repo dogfoods brnrd. Run `brnrd up --dev-reload` while changing the daemon
so the next task picks up new code without a restart ritual.

## PR expectations

- Keep diffs small and focused — one change, one PR.
- Tests green: `pytest` for the backend, `npm test` for anything touching
  `src/frontend/`.
- Explain the *why* in the PR description, not just the *what*.
- If you're touching something with an open design doc or issue, link it.

## Contributor agreement

A pull request needs the contributor agreement in [CLA.md](CLA.md). You keep
the copyright. You license the contribution to HugiMuni SAS, and the
contribution also stays under the license of the package you touched: MIT for
`src/brr/`, AGPLv3 for `src/brnrd/` and `src/frontend/`. That license to
HugiMuni SAS is what keeps a commercial license of the backend possible
without changing the terms anyone already has. Sign it the way `CLA.md`
describes. The reason for the two licenses is in
[LICENSE-OVERVIEW.md](LICENSE-OVERVIEW.md).

## Security

Found a security issue? Don't open a public issue — see
[SECURITY.md](SECURITY.md) for how to report it privately.
