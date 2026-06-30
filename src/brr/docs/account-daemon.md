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
  account dominion repo. Without it, a real git checkout auto-creates a
  **local-only** git repo under the local account state directory
  (`$XDG_STATE_HOME/brnrd/accounts/<account>/dominion`, usually
  `~/.local/state/brnrd/accounts/default/dominion`). An existing legacy
  `$XDG_STATE_HOME/brr/accounts/<account>/dominion` is still read as a
  migration fallback when the `brnrd` path does not exist.
- Remote durability is explicit. brr does not create a GitHub repo, gist, or
  forge object by default; point the account dominion repo at an existing git
  remote, a new user-approved forge repo, or a future backend when you want
  off-machine storage.
- Message events dropped into the account dispatch inbox can carry
  `repo: <label>` and are run in that registered checkout.
- Forge events can stay in the target repo's own `.brr/inbox`; the account
  daemon keeps that direct route.

The account dominion repo currently owns:

- `account/repos.json` — account id, repo registry, default repo;
- `dispatch/inbox/` and `dispatch/responses/` — account-scoped message dispatch;
- `repos/<repo>/dominion/` — the resident's repo-scoped working memory
  (`self-inject`, playbook, pitfalls, schedule, notes);
- `run-state/<repo>/<run>.md` — durable run-state documents.

## Moving This Repo's Current Dominion

The old repo-local dominion is the orphan branch/worktree materialized at
`.brr/dominion`. The account-scoped path is now the primary wake-time source,
with the old path kept only as a migration fallback. Move this repo's resident
memory into a repo-tagged area of the account dominion:

1. Stop the daemon.
2. Ensure the account home exists by running `brr up` once, or configure
   `account.dominion_path` and create that local git repo explicitly.
3. Copy `.brr/dominion/` into the account home under a repo tag such as
   `repos/Gurio__brr/dominion/`.
4. Preserve `self-inject`, `playbook.md`, `pitfalls.md`, `schedule.md`, and any
   resident notes you still want injected.
5. Commit the account dominion repo. Add a git remote only if you want
   off-machine durability now.
6. Restart `brr up` and verify the Run Context Bundle points at the account
   dominion before deleting the old repo-local dominion worktree.

If you already followed the early CS4 instructions under
`~/.local/state/brr/...`, the copy shape was right. The namespace was the stale
part: either leave it as the legacy fallback for now, move the account directory
to `~/.local/state/brnrd/...`, or set `account.dominion_path` to the exact path
you want.
