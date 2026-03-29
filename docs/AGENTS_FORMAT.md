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
| `default_executor` | yes      | `auto`, `claude`, `codex`, `gemini`, or a path|
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
