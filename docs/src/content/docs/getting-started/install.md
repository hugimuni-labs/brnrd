---
title: Install
description: Install brnrd and verify a local coding-agent Runner.
---

brnrd needs Python 3.10 or newer, git, and at least one supported coding-agent
CLI on `PATH`: Claude Code (`claude`) or Codex (`codex`). Authenticate that
CLI with your own subscription or API key first.

Install brnrd with the tool manager you already use:

```bash
uv tool install brnrd        # recommended when uv is already present
# or: pipx install brnrd
# or: npx brnrd init -i
```

`npx brnrd` is a bootstrapper for the Python package, not a JavaScript port. It
keeps its own environment and leaves your system Python alone.

Check the installation:

```bash
brnrd --version
brnrd --help
```

## Development install

```bash
git clone https://github.com/hugimuni-labs/brnrd
cd brnrd
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

Continue to [Connect](../connect/) and choose your door.
