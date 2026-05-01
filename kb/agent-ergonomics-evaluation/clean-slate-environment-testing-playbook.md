# Clean-Slate Environment Testing Playbook

Date: 2026-05-01
Purpose: manual evaluation plan for brr environment ergonomics on fresh repos.

## Purpose

Use this playbook when testing how well an agent orients itself inside brr's
execution environments without accumulated project memory. The goal is not to
automate a benchmark. The goal is to run a few comparable manual checks and
record where the agent flows, hesitates, or gets misled.

For this pass, "clean slate" means:

- no existing `AGENTS.md`
- no existing `kb/`
- no prior `.brr/` runtime state
- no meaningful git history unless the environment needs a seed commit

Do not use the brr development checkout as the target repo. Create throwaway
fixtures outside this repository, for example under `/tmp/brr-env-ergonomics/`.

## Current Environment Reality

As of this playbook, brr has two executable environment backends:

- `local`: runs in the main checkout and is appropriate for `branch: current`
- `worktree`: runs in a git worktree and needs a non-current branch strategy

The names `docker`, `devcontainer`, and `ssh` are useful negative tests today.
They may appear in task metadata and design docs, but they should fail clearly
before the task runs unless an implementation or plugin has been added.

There are two important clean-slate constraints:

- Worktree runs need at least one seed commit because git worktrees need a real
  branch base.
- The public daemon path currently expects `AGENTS.md`. If a daemon run refuses
  to start in a clean repo, count that as an ergonomics finding instead of
  hiding it.

## Fixture Setup

Create one fixture per scenario so failures and runtime artifacts do not bleed
between runs.

### `local-empty`

Use this for local/current-branch checks.

```bash
mkdir -p /tmp/brr-env-ergonomics/local-empty
cd /tmp/brr-env-ergonomics/local-empty
git init
```

Leave out `AGENTS.md`, `kb/`, and commits. If the test path requires `.brr/`,
create only the minimum runtime/config state needed for that run.

### `worktree-seeded`

Use this for worktree and branch-isolation checks.

```bash
mkdir -p /tmp/brr-env-ergonomics/worktree-seeded
cd /tmp/brr-env-ergonomics/worktree-seeded
git init
printf '# Worktree Seed\n' > README.md
git add README.md
git commit -m 'chore: seed clean repo'
```

Do not add `AGENTS.md` or `kb/`. The single commit is only there so git can
create a worktree.

### `initialized-control`

Use this once as a control run for the fully bootstrapped experience.

```bash
mkdir -p /tmp/brr-env-ergonomics/initialized-control
cd /tmp/brr-env-ergonomics/initialized-control
git init
brr init
```

This fixture is not clean slate. It answers a different question: how much
better is orientation when the normal brr playbook and KB exist?

## Environment Matrix

Keep the matrix small. The value comes from comparable observations, not from
covering every permutation.

| Fixture | Branch | Env | Expected result |
| --- | --- | --- | --- |
| `local-empty` | `current` | `auto` | resolves to `local` |
| `local-empty` | `current` | `local` | runs in the main checkout |
| `worktree-seeded` | `task` or `auto` | `auto` | resolves to `worktree` |
| `worktree-seeded` | `task` | `worktree` | runs in a task worktree |
| `worktree-seeded` | `task` | `local` | coerces to `worktree` |
| `worktree-seeded` | any | `docker` | clear unsupported-env failure |
| `worktree-seeded` | any | `devcontainer` | clear unsupported-env failure |
| `worktree-seeded` | any | `ssh` | clear unsupported-env failure |
| `initialized-control` | normal triage choice | `auto` | normal public CLI/daemon flow |

Optional extra: run one named-branch worktree case after the core matrix if
branch preservation needs attention.

## Repeatable Test Prompts

Use small tasks that make orientation visible without depending on project
knowledge.

### Orientation Only

```text
Orient yourself in this fresh repo. Report what context is missing before
making changes. Do not create files unless the task environment requires a
response artifact.
```

### Simple Durable Edit

```text
Add a one-paragraph notes.md explaining what files exist in this repo, then
write a concise final response.
```

### Environment Failure Probe

```text
This task must run in <env>. If that environment is unavailable, stop and
report the blocker clearly.
```

Run the same prompt shape across environments. Change only the requested
branch/env metadata or the `<env>` placeholder.

## What To Capture

For each run, collect just enough evidence to explain the score:

- requested branch/env and actual backend
- runner used
- task prompt
- first files or commands the agent reached for
- whether `AGENTS.md` or `kb/` being absent caused confusion
- cwd/repo root/branch/response path shown to the agent
- final response location
- git status and branch state after the run
- task file, run context file, and trace path when available
- exact unsupported-env error for negative tests

Avoid copying large trace bodies into the findings. Reference paths and quote
only the lines that explain the outcome.

## Scoring Rubric

Score each category from 1 to 5 and add one sentence of evidence.

- Orientation: Did the agent understand the task, execution root, and response
  contract?
- Clean-slate friction: Did missing `AGENTS.md`/`kb/` lead to useful caution,
  unnecessary wandering, or failure?
- Environment clarity: Were cwd, branch, runtime dir, and response path clear?
- Durability: Did useful output land in a commit, branch, or response file
  rather than disappearing in scratch state?
- Git behavior: Did the agent avoid unsafe history edits, branch retargeting,
  and unexplained commits?
- Recovery: If the run failed, was the next action obvious from the message,
  task metadata, or inspection output?

Suggested interpretation:

- `5`: smooth; no meaningful ambiguity
- `4`: minor friction, easy recovery
- `3`: workable, but the agent had to infer important context
- `2`: confusing or brittle; a human would need to intervene
- `1`: failed in a way that obscured the cause or stranded useful work

## Run Note Template

Copy this block once per run.

```markdown
### <fixture> / branch=<branch> / env=<env>

- Runner:
- Requested branch/env:
- Backend actually used:
- Prompt:
- Outcome:
- Scores:
  - Orientation:
  - Clean-slate friction:
  - Environment clarity:
  - Durability:
  - Git behavior:
  - Recovery:
- Evidence:
- Follow-up recommendation:
```

## Findings Summary Template

After the matrix, summarize the result in one short section.

```markdown
## Summary

Overall clean-slate experience:

Highest-severity findings:

1.
2.
3.

What already works:

- 

Recommended brr changes:

- 
```

## Things To Treat As Findings

Record these explicitly if they happen:

- `brr up` refuses to start because `AGENTS.md` is missing.
- The agent follows `run.md` and tries to read missing `AGENTS.md`/`kb/` without
  adapting.
- The prompt does not make the active backend or cwd obvious.
- A worktree run leaves changes only in scratch state with no clear recovery
  path.
- Unsupported envs fail after the runner starts, instead of before execution.
- The agent commits bootstrap files just to satisfy brr when the test was meant
  to stay clean slate.

## Stop Conditions

Stop the run and record the blocker instead of improvising if:

- the runner cannot be invoked non-interactively
- the fixture accidentally gains `AGENTS.md` or `kb/` before the clean-slate run
- the environment cannot be selected without changing brr code
- the result depends on secrets or external services

The playbook is useful when it preserves what the agent actually experienced,
including the sharp edges.
