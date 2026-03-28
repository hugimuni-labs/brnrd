# Principles

This document captures the core values and design laws that guide the
development of **brr**.  Following these principles helps keep the
implementation focused and avoids unnecessary complexity.

## Extreme simplicity

brr adds as few files, commands and moving parts as possible.  Durable
knowledge lives in plain text in your repository.  There is no separate
database or complicated metadata layer.  Anything that can be done with
a prompt and a minimal helper script will be done that way.

## Executor‑owned execution

brr is not a coding agent.  It orchestrates runs but delegates all code
generation, editing and reasoning to the configured executor (Codex,
Claude Code, Gemini, custom shells, etc.).  You can swap out the executor
without rewriting the project.

## Repo‑native state

All long‑lived project knowledge belongs in the Git repository where it
can be versioned, branched and reviewed.  Runtime junk (credentials,
offsets, PIDs, caches) lives in `.brr.local/` and is ignored by Git.  When
in doubt, store durable information in `AGENTS.md` or `agent_state.md` and
leave everything else out.

## Minimal runtime code

The code in this repository is deliberately small.  It handles
git‑related safety, CLI parsing, chat integration and subprocess
invocation.  Almost all domain knowledge is in prompts or in the
repository itself.  The goal is to have nothing to compile, nothing
heavy to install and no hidden control plane.

## Adopt, then normalize

When `brr init` runs, it scans your existing files (`AGENTS.md`,
`CLAUDE.md`, `GEMINI.md`, TODO files, CI configurations) and extracts the
semantics that matter.  It preserves your intent but rewrites the
instructions into a known, polished structure.  This normalization makes
future runs reproducible and clear.

## Git‑centric, GitHub‑friendly

brr works on any Git repository.  It does not require GitHub, but it
integrates smoothly with GitHub topics, template repositories and issue
forms.  The intent is to respect existing Git workflows and
collaboration practices rather than impose a new system.