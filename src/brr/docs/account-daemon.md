# brnrd Home Selection

The local daemon stores durable resident/run/control state in a git-backed
**brnrd home**. The home can be project-local or account-scoped; both lanes use
the same daemon loop, file protocol, run-node paths, repo-tagged resident
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

`brnrd account disconnect` removes the local cloud-gate identity and its
derived GitHub credential pointer. It keeps the account home, repo registry,
knowledge, and resident memory; reconnecting later resumes from those durable
stores rather than deleting them.

### Dashboard publishing controls

The connected daemon's `.brr/config` controls what it collects for the
dashboard:

- `publish.layers` is a comma-separated allowlist. Unset means all seven
  lanes. `none` disables all publishing; `corpus` enables all three corpus
  slices; `authored`, `knowledge`, and `runs` enable individual corpus slices;
  `runners`, `live_runs`, `activity`, `quota`, `pr_review_queue`, and
  `run_ledger` enable those lanes. Unknown tokens fail closed and are reported.
- `publish.runs_window_days` defaults to `14` and bounds only the corpus
  lane's `runs` slice. `0` excludes that slice. It does not bound the activity
  lane or the 256-row run ledger.

These local controls compose with the independent server-side publish scope
chosen when a repo is connected. The narrower side wins. New server-side
connections default to `none`; the local unset default remains all lanes.

## Explicit Home

Set `BRNRD_HOME=/path/to/home` or `home.path=/path/to/home` in `.brr/config`
when you want to pin the selected home. `BRNRD_HOME` points at the home root
itself, not at a nested `dominion/` path.

## Home Layout

Current durable paths under a home:

- `account/repos.json` — repo registry for account homes;
- `dispatch/inbox/` and `dispatch/responses/` — account-dispatch queues;
- `repos/<repo>/dominion/` — resident-owned repo memory;
- `runs/<repo>/<run>/state.md` — daemon-attested run frame;
- `runs/<repo>/<run>/body.md` — resident-authored run body (when written);
- `runs/<repo>/<run>/messages/` — receipted edge traffic;
- `surface/` — the single discovered user/resident-authored orientation root;
  its seed links `plans/<repo>/active.md`, `workflow.md`, and
  `ledger/decisions.md`, while arbitrary Markdown pages join by existing;
- `runner-policy/...` — stored runner preferences and proposals;
- `knowledge/` — home knowledge used before repo `kb/` and repo docs.

The wake and dashboard discover Markdown below `surface/`; adding a shared
page does not add a prompt block, API field, or dashboard mount. Daemon-attested
state such as `runs/*/state.md` remains outside this authored layer.

Remote durability is explicit. brnrd does not create a GitHub repo, gist, or
forge object by default; point the home git repo at a remote only when you want
off-machine storage.
