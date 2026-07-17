# Public documentation

The site deploys from this directory to GitHub Pages through
`.github/workflows/docs.yml`.

Preview it locally from the repository root:

```bash
python -m venv .venv-docs
. .venv-docs/bin/activate
python -m pip install -r docs/requirements.txt
mkdocs serve -f docs/mkdocs.yml
```

Documentation changes ride in the same pull request as the code change that
caused them.
