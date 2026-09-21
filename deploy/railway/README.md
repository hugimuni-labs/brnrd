# brnrd on Railway — the daemon on a small always-on box

**What this is:** the brnrd **daemon** (`brnrd up --foreground`) plus git and the
Claude Code CLI, in one container with one volume. It is the same thing as
[No always-on machine? Use a small VPS](../../docs/src/content/docs/guides/vps-install.md),
minus the SSH. **What it is not:** a hosted brnrd. Execution happens in *your*
Railway service, on *your* login; brnrd.dev only relays messages and status
(`connect.md`). Your model credential and GitHub token live in your Railway
project's variables and volume.

**No web app, no port.** The daemon serves no public dashboard: its only local
listener (`brnrd loom`) binds `127.0.0.1` only (`src/brr/loom/server.py`), and
the dashboard people mean is the hosted one at brnrd.dev. So the template has
**public networking off** and no healthcheck. The image at the repo root is the
hosted backend (uvicorn on `PORT`); `src/brr/Dockerfile` is the strand runner
image. Neither runs the daemon, so this directory carries its own `Dockerfile`.

## Files

| file | what | source |
| --- | --- | --- |
| `railway.json` | build with `deploy/railway/Dockerfile`; restart `ALWAYS` | Railway config-as-code: `build.builder`, `build.dockerfilePath`, `deploy.restartPolicyType`, `$schema` https://railway.com/railway.schema.json ([reference](https://docs.railway.com/reference/config-as-code)) |
| `Dockerfile` | node 22 + git + Claude Code + `pip install brnrd` | install routes: `install.md` |
| `start.sh` | clone repo once, pair once, `exec brnrd up --foreground` | `connect.md`, `cmd_up` in `src/brr/cli.py` |
| `template.json` | volume + variable list (not a Railway file) | see below |

Railway's config-as-code has **no volume or variable fields**, and is marked
deprecated (files supported through 2026-12-01) — so volume and variables are
declared in the template composer, from `template.json`.

## Variables

| key | why |
| --- | --- |
| `BRNRD_REPO_URL` (required) | the repo the resident works in; cloned to `/data/repo` on first boot |
| `GH_TOKEN` | clone a private repo, publish branches; the runner's publishing identity (`envs.md`) |
| `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` | model login on a box with no browser — one of these, or log in through a shell (step 3). The OAuth-token route is `[verify on first deploy]`; `vps-install.md` itself says headless login is CLI-specific and not re-verified |
| `BRNRD_URL` | pairing base URL, default `https://brnrd.dev` (`cli.py`) |

Volume: one, mounted at `/data` (Railway allows one per service). `HOME=/data/home`
(runner login), `XDG_STATE_HOME=/data/state` (account home; `account.py`
`state_root()` honours it), `/data/repo` (checkout). The entrypoint starts as root
to `chown` the volume, then drops to user `brnrd` because Claude Code refuses
`--dangerously-skip-permissions` as root and brnrd's claude runner passes it
(`runners.toml`). `[verify on first deploy]`: that Railway's volume ownership +
`setpriv` behaves as written.

## After deploy — three steps

1. **Open the deploy log and approve the pairing link.** First boot runs
   `brnrd account connect --no-service --defaults`, which prints a link you
   approve in a browser (`vps-install.md` §4: "a link you approve, not a
   localhost callback"). `[verify on first deploy]`: that connect prints the link
   and waits without a TTY.
2. **Connect a door.** Managed: the account you just paired reaches you via the
   dashboard/Telegram at brnrd.dev. Self-hosted Telegram: in a Railway shell
   run `brnrd gate setup telegram` (`connect.md`); it polls outbound-only, so no
   inbound port is needed (`vps-install.md`). Senders are default-closed.
3. **Log the runner in**, if you did not set a token variable: open a Railway
   shell (`railway ssh`, or the service's shell in the dashboard) and run
   `claude` login there; follow its own headless instructions
   (`vps-install.md` §2). `[verify on first deploy]`.

Then [send the first task](../../docs/src/content/docs/getting-started/first-task.md).

## Publishing the template — for the account owner (nothing here was deployed)

I could not verify Railway's composer UI from docs (the reference page describes
templates but not the composer's fields), so these steps are `[verify]` against
the live composer:

1. Push this branch's merge to `main` of `hugimuni-labs/brnrd`.
2. In Railway: Workspace → **Templates** → **New Template**.
3. Add a service from the GitHub repo `hugimuni-labs/brnrd`; set config path to
   `deploy/railway/railway.json` (custom config-file path in service settings).
4. Attach a volume, mount path `/data`. Leave **public networking off**.
5. Add each variable from `template.json` with its description;
   mark `BRNRD_REPO_URL` required.
6. **Publish**; then put the deploy button link on the docs VPS page and README.
