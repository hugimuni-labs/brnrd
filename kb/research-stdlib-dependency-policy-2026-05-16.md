# Research: stdlib dependency policy, 2026-05-16

Question: should brr keep the "pure stdlib Python, zero runtime
dependencies" rule, or is that now the wrong constraint given Docker,
remote gates, and future user modules?

## Short answer

Keep the stdlib idea as the **core default**, but stop treating it as
an absolute product law. The useful policy is:

> brr core should stay dependency-free until a dependency deletes real
> edge complexity. Integrations, envs, gates, and user modules may carry
> their own dependencies behind explicit extras or separate plugin
> packages.

The current hard wording in [`AGENTS.md`](../AGENTS.md) and
[`README.md`](../README.md) overstates the portability promise for the
full daemon product. brr already depends operationally on Git, runner
CLIs, hosted APIs for gates, optional Docker, and a Linux-shaped runner
image. The no-dependency rule still buys a simple install and a small
supply-chain surface, but it is not what makes the project extensible.
The extension boundary does.

## Current contradiction

There are two separate promises mixed together:

- **Install simplicity.** `pip install brr` currently installs a package
  with `dependencies = []`, which is a real adoption advantage for the
  playbook-only layer and for self-hosted daemon users.
- **Runs everywhere / easy to extend.** This is weaker in practice.
  The full daemon path expects Git, a runner CLI, gate credentials, and
  often Docker or a project-specific image. The code also carries
  Unix-shaped assumptions such as timed setup input via `SIGALRM`, PID
  shutdown via `SIGTERM`, and the repo-root `AGENTS.md` symlink used in
  brr's own checkout.

The sharper extensibility contradiction is in the env plugin design:
[`subject-envs.md`](subject-envs.md) and
[`design-env-interface.md`](design-env-interface.md) describe Python
entry-point envs and script envs, but the shipped
[`envs.get_env`](../src/brr/envs/__init__.py) only resolves the three
built-ins (`host`, `worktree`, `docker`). If the product goal is "users
can add ad hoc modules", implementing or downgrading that plugin promise
matters more than arguing about whether brr itself imports third-party
packages.

## What stdlib-only is still buying

- **Frictionless base install.** No resolver failures, no optional C
  extension surprises, no dependency pins to explain, and an easier path
  for `pip install git+...` or editable installs.
- **Auditable runtime.** brr is a daemon that handles tokens, repo
  checkouts, and bot channels. A small dependency surface is a security
  and maintenance advantage.
- **Simple mental model.** The file protocol, git CLI boundary, flat
  config, and runner subprocess model are understandable because the
  project avoided framework-shaped abstractions.
- **Plugin friendliness.** A dependency-free core lets third-party env
  packages bring their own SDKs without making every brr user pay for
  every integration.

## Where the constraint actually costs

The biggest cost is not core orchestration. Most of the code is daemon
state, git/worktree finalization, prompt assembly, kb preflight, and
progress projection. Libraries will not delete that.

The cost appears at the edges:

| Area | Current shape | What a dependency could save | Keep stdlib? |
| --- | --- | --- | --- |
| HTTP gates | Telegram, Slack, and GitHub each hand-roll `urllib` request helpers, JSON bodies, API errors, and polling backoff. | A shared client built on `requests` or `httpx` could reduce duplicate request/error code and make sessions, headers, timeouts, proxies, and tests cleaner. | Maybe. This is the strongest candidate. |
| Config | `.brr/config` is a 52-line flat `key=value` parser. | TOML would support nested structure, but Python's `tomllib` is read-only and Python 3.11+, while brr supports 3.10. A writer still needs a dependency. | Yes, for now. |
| Frontmatter | `protocol.parse_frontmatter` parses a small YAML-like subset used by events, tasks, and runner profiles. | PyYAML or python-frontmatter would remove custom parsing but import YAML complexity and unsafe-loader footguns. | Yes, until the schema needs real YAML. |
| CLI | `cli.py` is 122 lines of `argparse`. | Click/Typer would help once commands grow around overlays, env init, plugin management, or services. | Yes, for now. |
| Data validation | Dataclasses plus loose dict metadata. | Pydantic could validate API payloads and env/plugin contracts, but would add magic and a Rust-backed runtime dependency for a small internal model layer. | Yes, for now. |
| Plugin discovery | Designed as entry points plus script envs. | No dependency required: `importlib.metadata` is stdlib on supported Python. `pluggy` is only useful if brr grows a hook graph. | Yes, implement the stdlib path first. |
| Cross-platform user dirs | Future overlays / registry want `~/.config` and `~/.local/state` equivalents. | `platformdirs` would reduce platform-specific path policy. | Add when Windows/macOS polish becomes real work. |
| Git | brr shells out to Git. | GitPython still depends on the git executable for most operations and would not remove the core branch/worktree semantics. | Yes. Keep the CLI boundary. |

## Alternatives

### 1. Keep the hard rule

This preserves the cleanest install story and the smallest supply-chain
surface. It also keeps pushing complexity into custom parsers, custom
HTTP clients, and future "please install this SDK in your script env"
workarounds.

This is defensible only if brr's product line stays "small local
daemon, file protocol, Git, runners, no built-in service integrations
beyond the current gates."

### 2. Recommended: stdlib core, dependency-tolerant edges

This matches the shape the repo already wants:

- `brr` base package remains dependency-free while the core is small.
- Optional extras can carry dependencies for built-in features that
  genuinely need them, using normal `pyproject.toml`
  `optional-dependencies`.
- Third-party envs/gates ship as separate packages, discovered by
  entry points, and own their own dependencies.
- Drop-in script envs stay available for "one local executable" use
  cases where no packaging is desired.

This keeps the no-lock-in posture without making stdlib a tax on every
edge integration.

### 3. Add one base dependency for HTTP gates

If a runtime dependency becomes worth it soon, make it an HTTP client
and no more. The current built-in gates are the clearest place where
stdlib hurts readability and test ergonomics.

`requests` is the conservative sync choice. `httpx` is the better
choice only if brr expects async gates, long-lived clients, or typed
client APIs to matter soon; its own docs list several required
transitive dependencies, so it is not "one tiny import" in practice.

### 4. Move to a conventional modern stack

A stack like Click/Typer, HTTPX, PyYAML, Pydantic, platformdirs, and
pluggy would be familiar to Python developers. It would also import a
lot of dependency policy without deleting the code that currently makes
brr hard: daemon lifecycle, branch finalization, progress projection,
kb maintenance, prompt contracts, and remote-run durability.

This is not the right move now.

## Recommended next moves

1. **Change the policy wording only after an explicit decision.**
   Replace "zero runtime dependencies is a hard constraint" with
   "dependency-free core; dependencies allowed at explicit edges when
   they delete real complexity." Do not silently mutate `AGENTS.md` in a
   research task, because that changes the project's contribution
   contract.
2. **Fix the plugin promise before adding dependencies.** Either
   implement the accepted env plugin lookup (`.brr/envs/<name>/`,
   user-wide script envs, and `brr.envs` entry points) or rewrite
   `subject-envs.md` / `design-env-interface.md` to mark those pieces
   pending. This directly supports the user's "ad hoc modules" goal.
3. **If choosing a first dependency, run an HTTP-gate spike.** Build a
   shared gate HTTP helper around `requests` or `httpx`, port one gate,
   and measure deleted code plus test simplification. Do not add a
   dependency unless the diff is obviously smaller and clearer.
4. **Keep the current custom frontmatter/config/git layers.** They are
   small, project-shaped, and not where the maintenance cost is coming
   from.
5. **Use `platformdirs` only when overlays or a repo registry ship with
   serious cross-platform support.** Until then, the current Linux/XDG
   notes are enough.

## External references checked

- Python Packaging User Guide on
  [`optional-dependencies`](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#dependencies-and-requirements).
- PyPA
  [entry points specification](https://packaging.python.org/en/latest/specifications/entry-points/)
  and Python's
  [`importlib.metadata`](https://docs.python.org/3/library/importlib.metadata.html)
  entry-point API.
- Python
  [`tomllib`](https://docs.python.org/3.11/library/tomllib.html)
  docs: stdlib TOML parsing is Python 3.11+ and read-only.
- Python
  [`urllib.request`](https://docs.python.org/3/library/urllib.request.html)
  docs, which point readers at Requests for a higher-level HTTP client.
- [HTTPX](https://www.python-httpx.org/) docs for sync/async HTTP and
  listed transitive dependencies.
- [Click](https://click.palletsprojects.com/en/stable/) docs for CLI
  composition.
- [PyYAML](https://pyyaml.org/wiki/PyYAMLDocumentation) docs for
  `safe_load` and the unsafe arbitrary-object loader warning.
- [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/)
  docs for validation/serialization tradeoffs.
- [platformdirs](https://platformdirs.readthedocs.io/en/latest/) docs
  for cross-platform user config/state/cache paths.
