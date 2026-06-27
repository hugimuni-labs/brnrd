# Decision: brnrd is repo-first

Status: accepted on 2026-06-27

## Context

brnrd started with a generic `Project` object because the first managed-mode slice needed a simple mailbox for account-owned events.

That model now hides the actual product. brnrd is a control plane for local repo agents:

```text
Telegram / GitHub / web prompt
  -> brnrd account control plane
  -> repo-scoped work target
  -> local brr daemon or parent dispatcher
  -> local CLI agent
```

The durable user-facing object is the repository. Runtime selection is increasingly a dispatch decision, not the stable identity of the work target.

## Decision

Use **Repo** as the primary brnrd product object.

Retire `Project` as a user-facing noun and data-model center:

```text
Project -> Repo
projects -> repos
project_id -> repo_id
/v1/accounts/projects -> /v1/accounts/repos
```

The UI should say **Repo**, **Channel**, **Runtime**, and **Account**. It should not ask users to understand generic brnrd projects.

## Consequences

- The dashboard becomes repo-card-first.
- Daemon pairing approves a local daemon against a repo.
- Telegram route selection becomes active repo selection.
- GitHub issue/PR comments route by repo identity.
- `Project` compatibility is deliberately not preserved before launch.

## Companion pages

- [`design-brnrd-channel-routing.md`](design-brnrd-channel-routing.md) — account-owned channels that route into repos.
- [`design-brnrd-github-installation-sync.md`](design-brnrd-github-installation-sync.md) — GitHub App installation auth and installed-repo sync.
