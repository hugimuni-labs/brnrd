# Installing

```bash
pip install brr
```

Or run from a local checkout while developing or customizing brr itself:

```bash
git clone https://github.com/Gurio/brr
/path/to/brr/brnrd init
```

For an editable install:

```bash
pip install -e /path/to/brr
```

Forks work with normal Python packaging too:

```bash
pip install git+https://github.com/you/brr.git
```

## Requirements

- Python 3.11+
- At least one AI CLI on `PATH` that brr can drive as a runner —
  `claude` (Claude Code), `codex`, or `gemini` are the built-in profiles.
  You bring your own subscription/API key for whichever one you use.
- git, for the branch/PR workflow.

## Development install

```bash
git clone https://github.com/Gurio/brr
cd brr
pip install -e ".[dev]"
pytest
```

For remote-assisted brr development — running the daemon against your
own editable checkout so it re-execs itself between tasks as you change
brr's own code:

```bash
brnrd up --dev-reload
```

## Next

Continue to [Quickstart](quickstart.md) to initialize a repo and run
your first task.
