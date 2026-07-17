# brnrd Home Selection

The local daemon stores durable resident/run/control state in a git-backed
**brnrd home**. The home can be project-local or account-scoped; both lanes use
the same daemon loop, file protocol, run-state paths, repo-tagged resident
memory, and runner policy machinery.

## Project Lane

For a repo with no brnrd service connection and no explicit account identity,
`brnrd up` selects a project home:

```text
$XDG_STATE_HOME/brnrd/projects/<repo-slug>-<path-hash>/home/
```

The repo slug comes from repo config or forge remote when available; the path
hash keeps two local repos with the same basename from colliding. There is no
silent `accounts/default` fallback.

Use this lane for local dogfooding and one-repo bots:

```bash
brnrd gate bind . telegram
brnrd up
```

The gate remains repo-local in `.brr/gates/...`; durable state lands in the
project home.

## Account Lane

For multi-repo routing through brnrd service, connect once and add repos:

```bash
brnrd account connect https://brnrd.dev
brnrd account add .
brnrd up
```

`brnrd account connect` persists the connected `account_id` in the repo's cloud gate
state. `brnrd account add <repo>` registers the target repo in:

```text
$XDG_STATE_HOME/brnrd/accounts/<account-id>/home/account/repos.json
```

That registry carries the default repo and any additional repo labels. Remote
chat events can then route by repo identity; forge events stay naturally
repo-addressed.

## Explicit Home

Set `BRNRD_HOME=/path/to/home` or `home.path=/path/to/home` in `.brr/config`
when you want to pin the selected home. `BRNRD_HOME` points at the home root
itself, not at a nested `dominion/` path.

## Home Layout

Current durable paths under a home:

- `account/repos.json` — repo registry for account homes;
- `dispatch/inbox/` and `dispatch/responses/` — account-dispatch queues;
- `repos/<repo>/dominion/` — resident-owned repo memory;
- `run-state/<repo>/<run>.md` — durable run-state documents;
- `surface/` — the single discovered user/resident-authored orientation root;
  its seed links `plans/<repo>/active.md`, `workflow.md`, and
  `ledger/decisions.md`, while arbitrary Markdown pages join by existing;
- `runner-policy/...` — stored runner preferences and proposals;
- `knowledge/` — home knowledge used before repo `kb/` and repo docs.

The wake and dashboard discover Markdown below `surface/`; adding a shared
page does not add a prompt block, API field, or dashboard mount. Daemon-attested
state such as `run-state/` remains outside this authored layer.

Remote durability is explicit. brnrd does not create a GitHub repo, gist, or
forge object by default; point the home git repo at a remote only when you want
off-machine storage.
