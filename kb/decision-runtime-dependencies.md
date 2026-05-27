# Runtime Dependency Stance

Status: accepted on 2026-05-22

This decision accepts the runtime-dependency slice of
[`research-positioning-and-runtime-deps-2026-05-21.md`](research-positioning-and-runtime-deps-2026-05-21.md).
It supersedes the earlier zero-runtime-dependencies constraint in
[`src/brr/AGENTS.md`](../src/brr/AGENTS.md) and the README value prop.

## Decision

`brr` no longer treats zero runtime dependencies as a project value or a
hard constraint. Prefer stdlib for small, local code, but allow small
runtime dependencies that do not require native compilation when they
remove real maintenance burden or improve operator-facing failures.
Avoid native-extension-heavy packages unless a later task explicitly
settles that install trade-off.

`requests` is the first accepted runtime dependency. The built-in
Telegram, Slack, and GitHub gates use it for HTTP calls; the package
declares it in [`pyproject.toml`](../pyproject.toml). Per-forge SDKs
such as PyGithub, python-telegram-bot, and slack_sdk remain deferred.

## Rationale

The old zero-deps stance was protecting install ergonomics and the
absence of native compilation, but those goals are better expressed as
"keep dependencies small and avoid native compilation requirements." It
was not a meaningful adoption moat for the AI-tooling audience, and it
forced the gates to carry hand-written `urllib` request construction,
JSON body handling, and error parsing.

`requests` is a bounded improvement: it replaces bespoke gate HTTP glue
without changing the file-protocol boundary, gate setup surface, or
daemon model. SDKs are a separate question because they would move
pagination, retries, and API object models into larger, more
opinionated dependencies.

## Current Shape

- [`README.md`](../README.md) leads with brr's playbook and remote
  execution value, not dependency count.
- [`src/brr/AGENTS.md`](../src/brr/AGENTS.md) says small runtime
  dependencies that do not require native compilation are acceptable
  when they pay for themselves.
- [`src/brr/gates/telegram.py`](../src/brr/gates/telegram.py),
  [`src/brr/gates/slack.py`](../src/brr/gates/slack.py), and
  [`src/brr/gates/github/`](../src/brr/gates/github/) use
  `requests`.
