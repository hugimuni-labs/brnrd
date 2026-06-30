# Account Daemon

The local daemon is moving from one foreground process per repo to one
foreground process per account, with repo-scoped runs underneath it.

In the CS4 slice, `brr up` resolves an account context before dispatching work:

- The current checkout remains the default repo, so existing single-repo installs
  keep working.
- Extra repos can be registered with flat config keys:
  `account.repo.<label>=/absolute/path/to/repo`.
- `account.default_repo=<label>` picks the fallback repo for message events that
  do not name a target.
- `account.dominion_path=/path/to/account-home` designates an existing local
  account dominion repo. Without it, a real git checkout auto-creates one under
  the local account state directory.
- Message events dropped into the account dispatch inbox can carry
  `repo: <label>` and are run in that registered checkout.
- Forge events can stay in the target repo's own `.brr/inbox`; the account
  daemon keeps that direct route.

The account dominion repo currently owns:

- `account/repos.json` — account id, repo registry, default repo;
- `dispatch/inbox/` and `dispatch/responses/` — account-scoped message dispatch;
- `run-state/<repo>/<run>.md` — durable run-state documents.

## Moving This Repo's Current Dominion

The old repo-local dominion is the orphan branch/worktree materialized at
`.brr/dominion`. Once the account daemon is running with a chosen
`account.dominion_path`, move this repo's resident memory into a repo-tagged
area of the account dominion:

1. Stop the daemon.
2. Ensure the account home exists by running `brr up` once, or configure
   `account.dominion_path` and create that repo explicitly.
3. Copy `.brr/dominion/` into the account home under a repo tag such as
   `repos/Gurio__brr/dominion/`.
4. Preserve `self-inject`, `playbook.md`, `pitfalls.md`, `schedule.md`, and any
   resident notes you still want injected.
5. Commit the account dominion repo.
6. Restart `brr up` and verify the Run Context Bundle points at the account
   dominion before deleting the old repo-local dominion worktree.

Do not delete the old `.brr/dominion` until wake-time injection has been moved to
the account dominion path; early CS4 still keeps the old path readable for
compatibility.
