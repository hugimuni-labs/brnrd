# Repo-first brnrd model

Status: accepted
Date: 2026-06-27

brnrd is not a generic project dashboard. It is a control plane for local repo agents.

```text
Telegram / GitHub / web prompt
  -> brnrd account control plane
  -> repo-scoped work target
  -> local brr daemon or parent dispatcher
  -> local CLI agent
```

## Decision

Use **Repo** as the primary product object.

Retire `Project` as a user-facing noun and data-model center. The old `Project` table was a useful mailbox for the inbox prototype, but it now hides the product truth: the user connects repositories, runs local agents for those repositories, and routes remote events into them.

## Core objects

### Account

Identity, ownership, billing, and connected gates.

Account owns GitHub OAuth identity, Telegram identity, GitHub App installations, subscription state, and future workspace membership.

Account is the ingress authority. It answers who is allowed to send work and which external identities are connected.

### Repo

Stable user-facing work target.

Suggested fields:

```text
id
account_id
forge
repo_full_name
repo_owner
repo_name
forge_repo_id
default_branch
created_at
updated_at
```

The dashboard should center repo cards, not projects.

### Channel route

An account-owned input or output channel routed to a repo.

Examples:

```text
Telegram chat -> active repo
Telegram topic -> active repo
GitHub issue or PR event -> repo by repo_full_name
web UI selected repo -> repo
```

Telegram binds to the account first. It then needs a selected active repo per chat or topic, otherwise free-form messages are ambiguous.

### Runtime

A local execution capability, not the durable user-facing work target.

Examples:

```text
repo-local brr daemon
account parent daemon dispatching across repos
local Codex / Claude / Gemini runner
runtime capability profile
online / offline / last_seen
```

Runtime choice can be made per event by policy or by a cheap router/planner agent.

### Dispatch / event

A concrete task routed to a repo, then optionally assigned to a runtime.

```text
incoming event
  -> account and channel
  -> repo
  -> optional runtime / model choice
  -> local execution
```

`runtime_id` can be nullable at enqueue time if the dispatch decision happens after triage.

## Cost-aware routing

A cheap local model or CLI agent may inspect a task and either complete it or reschedule/escalate:

```text
cheap local agent
  -> complete simple work
  -> or reschedule to stronger model/runtime
```

This is why `RepoRuntime` is probably too narrow as the durable noun. The repo is stable; runtime is an execution allocation.

## GitHub installation sync

GitHub App installation and GitHub user authorization are different things.

brnrd should store installations and sync the repositories available to each installation.

Required env:

```text
BRNRD_GITHUB_APP_ID
BRNRD_GITHUB_APP_PRIVATE_KEY_B64
BRNRD_GITHUB_APP_SLUG
BRNRD_GITHUB_BOT_LOGIN
BRNRD_GITHUB_WEBHOOK_SECRET
```

Flow:

```text
App ID + private key
  -> sign GitHub App JWT
  -> POST /app/installations/{installation_id}/access_tokens
  -> GET /installation/repositories
  -> upsert installed repos
  -> reconcile with local repos/channels/runtimes
```

Suggested tables:

```text
github_installations
  id
  account_id
  installation_id
  target_login
  target_type
  created_at
  last_synced_at

github_installed_repos
  id
  github_installation_id
  repo_full_name
  forge_repo_id
  private
  default_branch
  last_seen_at
```

## Telegram routing

Telegram should be account-bound, then repo-routed.

User-facing commands should move away from `project`:

```text
/repos
/repo Gurio/brr
/status
```

A chat or topic has an active repo. Smart routing can be added later, but explicit active repo selection is the first reliable UX.

## Dominion and teams

Dominion remains a pluggable agent-owned continuity backend.

The current orphan-branch dominion is excellent for a solo developer because it survives workstation loss, migration, and repo checkout churn.

Team mode should not assume that teammates use a maintainer's local Codex or Claude. Each developer can run their own local runtime. Team value comes from repo access, routing, policy, and shared context, not hosted compute.

Possible team shape:

```text
Repo
  shared KB
  per-user or per-agent dominion backend
  runtime policies
  channel policies
```

Avoid forcing dominion into the Repo table now. Keep it pluggable via repo config/runtime policy.

## Migration intent

Because there are no external users yet, do not maintain a compatibility layer around the wrong noun.

Rename the public model directly:

```text
Project -> Repo
projects -> repos
project_id -> repo_id
/v1/accounts/projects -> /v1/accounts/repos
```

Update visible UI copy:

```text
Project -> Repo
Project selector -> Active repo selector
Project bindings -> Connected repos
```

The old `Project` table can be migrated destructively in production with explicit SQL during deployment.
