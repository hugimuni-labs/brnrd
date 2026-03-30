# AGENTS.md format

This is what a populated `AGENTS.md` looks like after `brr init` runs
against a real project.

## Example

```markdown
---
brr:
  version: 1
  mode: live
  default_executor: claude
  auto_approve: true
  commands:
    build: "npm run build"
    test: "npm test"
    verify: "npm run lint && npm test"
    start: "npm run dev"
  task_sources: [TODO.md, docs/roadmap.md]
  state_file: agent_state.md
  commit_policy: commit-at-end-if-material
---

# Project

API server for the widgets platform. Express + TypeScript, Postgres
via Prisma, deployed to Fly.io.

## Build and run

- `npm install` to set up dependencies.
- `npm run dev` starts the dev server on port 3000.
- `npm run build` compiles TypeScript.
- `npm test` runs Jest tests. All tests must pass before committing.

## Code guidelines

- TypeScript strict mode. No `any` types without a comment explaining why.
- Database migrations go in `prisma/migrations/`. Run `npx prisma migrate dev`
  after schema changes.
- Error responses use the `ApiError` class from `src/errors.ts`.
- Commit messages: imperative mood, under 72 chars. Reference issue
  numbers when applicable.

## Constraints

- Do not modify `src/auth/` without explicit approval — it handles
  payment tokens.
- Keep dependencies minimal. Prefer stdlib or existing deps over new ones.
- All API endpoints need an integration test in `tests/api/`.
```

## Header fields

| Field              | Required | Values                                        |
|--------------------|----------|-----------------------------------------------|
| `version`          | yes      | `1`                                           |
| `mode`             | yes      | `paused`, `incubating`, `live`                |
| `default_executor` | yes      | `auto`, `claude`, `codex`, `gemini`, or any custom name |
| `auto_approve`     | no       | `true` to pass auto-approve flags to executor (default: `false`) |
| `executor_cmd`     | no       | command template override, e.g. `["my-tool", "-p", "{prompt}"]` |
| `commands`         | no       | key-value map of named commands               |
| `task_sources`     | no       | list of files/paths the agent checks for work |
| `state_file`       | no       | path to agent_state.md (default: repo root)   |
| `commit_policy`    | no       | when to commit (default: commit-at-end-if-material) |

## Body

The body is freeform Markdown. It should tell an AI executor:

1. What the project is and does (one paragraph).
2. How to build, test, and run it (exact commands).
3. Code style and commit conventions.
4. Constraints — things the executor must not do or must get approval for.

Keep it under a page. Link to deeper docs rather than inlining them.

## Custom executors

brr ships with built-in profiles for `claude`, `codex`, and `gemini`.
To use anything else, drop a file in the executors directory and set
`default_executor` to its name.

### Search order

For each directory (repo-local first, then user-global):

1. `.brr.local/executors/<name>` — executable file (any language)
2. `.brr.local/executors/<name>.py` — Python module
3. `~/.config/brr/executors/<name>` — same, user-global
4. `~/.config/brr/executors/<name>.py`
5. `brr.executors` entry-point group — pip-installed packages

### Executable (any language)

The simplest option.  Receives the prompt on **stdin**, writes output
to **stdout**.  Exit 0 = success.  The file must be `chmod +x`.

Environment variables set by brr:

- `BRR_AUTO_APPROVE=1` — present when auto-approve is enabled

`.brr.local/executors/aider`:

```bash
#!/bin/bash
prompt=$(cat)
cmd=(aider --message "$prompt")
[ "$BRR_AUTO_APPROVE" = "1" ] && cmd+=(--yes)
exec "${cmd[@]}"
```

### Python module

For when you need more control (API calls, chaining, output parsing).
The file must expose a `run()` function:

```python
def run(prompt: str, *, cwd: str, auto_approve: bool = False) -> str:
    """Execute the prompt and return the output text.

    Raise RuntimeError on failure.
    """
```

`.brr.local/executors/aider.py`:

```python
import subprocess

def run(prompt: str, *, cwd: str, auto_approve: bool = False) -> str:
    cmd = ["aider", "--message", prompt]
    if auto_approve:
        cmd.append("--yes")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout
```

Then in `AGENTS.md`: `default_executor: aider`.

### Entry-point registration (pip packages)

A pip package can register an executor via `pyproject.toml`:

```toml
[project.entry-points."brr.executors"]
myexec = "my_package.executor:run"
```
