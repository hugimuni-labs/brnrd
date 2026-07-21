# Public documentation

The site deploys from this directory to GitHub Pages through
`.github/workflows/docs.yml`.

Preview it locally from the repository root:

```bash
cd docs
npm ci
npm run dev
```

`npm run check` runs Astro diagnostics, a production build, and the generated
site's internal-link check. The GitHub Pages deployment serves `docs/dist/` at
`https://hugimuni-labs.github.io/brnrd/`.

Documentation changes ride in the same pull request as the code change that
caused them.
