---
title: Runs & environments
description: Choose host, worktree, or Docker execution with an honest trust boundary.
---

The daemon assembles the current repo context, chooses the configured execution
environment, starts a CLI runner, keeps the conversation live, and preserves
the outcome in git or the reply thread.

Every project chooses one of three shipped environments. These modes isolate
different kinds of friction; none is a cage for a hostile agent.

| Mode | What it isolates | Use it when |
|---|---|---|
| `host` | Nothing beyond your shell. Changes hit the working tree immediately. | You trust the agent and want minimum friction. |
| `worktree` | A separate worktree and branch. It still shares `.git`, credentials, network, and the host filesystem. | You want code runs kept off the main working tree. |
| `docker` | Dependencies and network; host-file visibility is narrowed to the repo and mounted credential paths. The repo is read-write, credentials cross in, and network is on by default. | You want a clean toolchain or network control as defense in depth. |

Select the mode in `.brr/config`:

```ini
environment=worktree
```

For Docker, set an image and optionally disable networking:

```ini
environment=docker
docker.image=your-runner-image:tag
docker.network=none
```

## Durability

For modifying work, the reliable receipt is a git commit on the run branch.
The current-thread response is also preserved. Failed or conflicted worktree
and Docker runs keep their recovery surfaces for inspection; clean successful
runs can remove scratch state.

## Trust boundary

The runner executes commands with the authority brnrd grants it, and its
approval prompts are bypassed deliberately. `worktree` protects your working
tree, not your credentials or machine. Docker narrows filesystem visibility but
is not a credential or containment boundary. Read [Security & privacy](../../security/)
before accepting tasks from other people.

## Source-trust tiering

The environment isn't only a static config choice — it's also picked per event
by the **trust of the source**, resolved deterministically when the run
manifest is built (no LLM in the loop). An untrusted commenter's run no longer
executes with the same authority as your own. Three tiers:

- **owner** — you: the paired chat, the bound account, and every owner-only
  path (schedule wakes, self-wakes, CLI). Gets the configured default
  environment — today's behaviour, unchanged.
- **collaborator** — a repo write+ collaborator, an allowlisted sender, a
  member of a configured room. Gets the configured default too, tightenable to
  a stricter environment with `trust.collaborator_env`.
- **untrusted** — anything else that still reaches the queue. Routed to
  `trust.untrusted_env` (default `solitary`, the hardened preset), or
  **refused** outright — failing closed — when `solitary` can't back it (no
  `docker.image`) or `trust.untrusted=refuse`.

An event's own `environment` key can never lift an untrusted event out of its
tier: the tier wins.

Config keys (in `.brr/config`):

```
trust.collaborator_env=solitary   # optional: tighten the collaborator env
trust.untrusted_env=solitary      # default: env for untrusted sources
trust.untrusted=refuse            # or: refuse untrusted runs outright (default: solitary)
```

At zero config, owner and collaborator behave exactly as before; untrusted
fails closed. The resolved tier rides the run metadata (`trust_tier`), so
surfaces can show which trust level a run executed at.
