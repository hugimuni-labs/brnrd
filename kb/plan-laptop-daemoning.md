# Plan: laptop-side daemoning — macOS + Linux native install

**Status: accepted 2026-05-26** (locked in PR #40 MR review,
locking pass IV — **reshaped from per-project to machine-scoped
multi-project**: one `brr daemon` process per machine serves
all brr-init'd repos discovered via
`~/.config/brr/projects.toml`; one supervised systemd /
launchd unit per machine; brnrd account binding lives at
machine scope so `brnrd connect` from a second repo is a one-
tap on already-known credentials). Fluid past the unit shapes
— implementation feedback may reshape the registry format or
discovery cadence. Lifts the "go add it to your startup
scripts" friction by giving `brr daemon install` first-class
behaviour on macOS and Linux: write the OS's native service
unit, register it, hand the user back a daemon that survives
reboots without sudo. Companion to
[`subject-managed-mode.md`](subject-managed-mode.md) → "Daemon
hosting" (the strategic frame), to
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
(the *cloud-host* daemoning story; this page is the *laptop-host*
counterpart),
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) → "The
protocol shape, at a glance" (the diagram this page implements
on the laptop side) and
[`decision-cli-shape.md`](decision-cli-shape.md) (which lists
`brr daemon install | uninstall` as siblings of `up | down |
status`). Tracked at
[issue #29](https://github.com/Gurio/brr/issues/29).

## Why this exists separately from the cloud-host plan

The deployment-templates plan addresses *cloud-first* users —
people who want brr's home on a free-tier Fly app or a Hetzner VPS,
not a laptop. That's a niche audience after the
[`plan-failover-compute.md`](plan-failover-compute.md) reframe
made brnrd-spawned failover the load-bearing answer to
laptop-down dispatch.

This page addresses the *common* case: users whose home is their
laptop, who want the daemon to start on boot, survive logout
gracefully, and not require `tmux` / `screen` / `nohup` rituals
to be production-friendly. Managed mode lowers the urgency
(failover spawns cover the gaps) but doesn't remove the want —
having brr "just be running" on the laptop is the baseline good
experience.

## Decision

`brr daemon install` writes and registers **one per-user
service unit per machine** — that single daemon process serves
**all** brr-init'd repos on the machine, discovered via a
machine-scoped registry. `brr daemon uninstall` removes the
unit. No sudo. No system-wide installation. The unit runs as
the invoking user, lives in their home dir, survives reboots,
restarts on crash, and integrates with the OS's logging.

| Platform | Service manager | Unit location | Auto-start at boot |
|----------|----------------|---------------|-------------------|
| **Linux** | systemd (user instance) | `~/.config/systemd/user/brr.service` | Via `loginctl enable-linger $USER` (one-time, run by `install`) |
| **macOS** | launchd (LaunchAgent) | `~/Library/LaunchAgents/dev.brnrd.brr.plist` | Native — LaunchAgents auto-load at user login |
| **Windows** | — | — | **Deferred at launch.** See [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §4. |

When `brr daemon install` is followed by `brr daemon up`, the
verb operates the registered service (`systemctl --user start brr`
or `launchctl bootstrap`). When the service isn't installed, the
verb falls back to today's direct foreground/PID-file supervisor.
Users who prefer `tmux` / manual control aren't forced into
service registration.

`brr daemon status` reports both modes uniformly: service +
"managed by systemd, last started: …" line OR foreground +
"running directly under PID …" line. Either mode lists the
brr-init'd projects the daemon is currently serving (read
from the registry).

`brr daemon uninstall` stops the service, removes the unit file,
and (on Linux) prompts about `loginctl disable-linger` if no
other systemd user units exist.

## Project registry — `~/.config/brr/projects.toml`

The machine-scoped daemon discovers brr-init'd repos via a
**user-owned registry file** at `~/.config/brr/projects.toml`
(XDG-base-dir-aware: respects `$XDG_CONFIG_HOME` if set).

```toml
# ~/.config/brr/projects.toml
# Auto-managed by `brr init` / `brr daemon adopt` / `brr daemon forget`.
# Hand-edit at your own risk; format is stable but not contract.

[[projects]]
path = "/Users/arseni/code/brr"
added_at = "2026-05-12T14:20:00Z"
enabled = true

[[projects]]
path = "/Users/arseni/code/hugimuni-web"
added_at = "2026-05-18T09:01:00Z"
enabled = true

[[projects]]
path = "/Users/arseni/experiments/throwaway"
added_at = "2026-05-22T11:43:00Z"
enabled = false   # temporarily disabled without removing
```

The registry is the **machine source of truth** for which
projects the daemon serves. Operations on it:

- **`brr init`** in a directory that's not yet in the registry
  appends an entry (`enabled = true`) after running the
  per-project setup. On a directory that's already registered,
  `brr init` is a no-op for the registry (just runs the
  project-level setup steps).
- **`brr daemon adopt [<path>]`** retroactively adds a project
  (default: cwd) to the registry — useful for repos that were
  brr-init'd before the daemon was installed.
- **`brr daemon forget [<path>]`** removes a project from the
  registry without touching the project's `.brr/` files
  (project remains brr-init'd, just no longer served by this
  machine's daemon).
- **`brr daemon list`** prints the registry's currently-enabled
  projects + their per-project status (poller running yes/no,
  last event seen, last task spawned).
- **Daemon picks up registry changes** within ~30s (cheap
  re-scan) OR immediately if the CLI signals via the local IPC
  socket on add / remove. Editing the registry by hand and
  waiting works; no daemon restart needed.

## Account binding lives at machine scope

The brnrd account binding (auth token, subscription status,
brnrd URL, cached account-scope config) lives at
**`~/.local/state/brr/account/`** — not per project. When the
user runs `brnrd connect` (or `brr brnrd connect`) from a
second project's directory, the connect verb sees the existing
machine binding and skips the account-pair step, going
straight to the project-create + gate-pair phases. See
[`design-config-layout.md`](design-config-layout.md) §
"Account scope" for the file layout.

The first-time flow is:
```
$ cd ~/code/project-a && brnrd connect
> No brnrd account paired on this machine yet.
> Opening browser to https://brnrd.dev/pair?code=ABC123
> ✓ Paired as account: arseni@hugimuni.fr (machine: hostname)
> ✓ Project "project-a" created.
> ✓ GitHub App installed.
```

The second-time flow is:
```
$ cd ~/code/project-b && brnrd connect
> Already paired as: arseni@hugimuni.fr (since 2026-05-12)
> ✓ Project "project-b" created.
> ✓ GitHub App already installed for Gurio org — auto-bound.
```

The same shape applies to **selective re-binding across
projects**: `brnrd projects list` shows projects scattered
across the registry vs the brnrd-side account, and
`brnrd projects bind <project_id>` (or `brr brnrd projects bind`,
equivalent) attaches an already-known machine to a project the
account owns. The daemon picks up new bindings via the IPC
socket signal as part of `brnrd connect`'s success path.

## Unit shapes

### Linux — systemd user unit

`~/.config/systemd/user/brr.service`:

```ini
[Unit]
Description=brr daemon (machine-scoped multi-project multiplexer)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env brr daemon up --foreground
Restart=on-failure
RestartSec=5s
Environment=BRR_INSTALL_MANAGED=1

[Install]
WantedBy=default.target
```

**No `WorkingDirectory`** — the daemon serves multiple
projects and shouldn't be pinned to any one of them; it
operates on absolute paths read from the registry.
`Restart=on-failure` covers daemon crashes; the daemon itself
gates its own per-project re-spawn / re-init logic.

`loginctl enable-linger $USER` (run once by `install`, with the
user prompted before the change) makes user services run before
login and continue after logout — the equivalent of the
boot-survives-reboot guarantee a normal service would have.

### macOS — launchd LaunchAgent

`~/Library/LaunchAgents/dev.brnrd.brr.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dev.brnrd.brr</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/brr</string>
        <string>daemon</string>
        <string>up</string>
        <string>--foreground</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/<user>/Library/Logs/brr/brr.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/<user>/Library/Logs/brr/brr.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>BRR_INSTALL_MANAGED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
```

**No `WorkingDirectory`** key — same reasoning as the systemd
unit. `KeepAlive` with `SuccessfulExit: false` mirrors
`Restart=on-failure`. `RunAtLoad: true` loads on login. Logs
land in `~/Library/Logs/brr/` per macOS convention; `brr daemon
logs` tails them.

The bundle identifier `dev.brnrd.brr` matches the eventual
`brnrd.dev` domain in reverse-DNS form — consistent with macOS
conventions and matches the product brand.

## What `install` does mechanically

```
brr daemon install

  1. Detect OS (Linux → systemd; macOS → launchd; other → error
     with a useful message).
  2. Resolve absolute path to `brr` binary (via shutil.which)
     for ExecStart — survives PATH changes.
  3. Ensure `~/.config/brr/` exists; create an empty
     `projects.toml` if missing (so the daemon starts up cleanly
     even before any `brr init`).
  4. Generate the unit file from the template (substituting
     brr binary path).
  5. Write the unit file to the OS-correct location.
  6. Linux only: check if linger is enabled for the user.
     If not, prompt: "Enable linger? (lets brr start at boot
     before you log in; one-time setting per user; uses sudo)"
     [Y/n]. If Y: `sudo loginctl enable-linger $USER`. If N:
     warn that the daemon won't run before first login.
  7. Register the unit:
       Linux:  `systemctl --user daemon-reload && systemctl --user enable brr.service`
       macOS:  `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/dev.brnrd.brr.plist`
  8. Start the unit immediately (unless --no-start):
       Linux:  `systemctl --user start brr.service`
       macOS:  `launchctl kickstart gui/$UID/dev.brnrd.brr`
  9. If any projects are already in the registry, print a
     summary of what the daemon will serve. Otherwise print
     "no projects registered yet — run `brr init` in a repo
     to add one."
 10. Print next-steps: `brr daemon status` to verify;
     `brr daemon logs` to tail; `brr daemon uninstall` to
     remove; `brr init` in any repo to add it to the
     daemon's project list.
```

The whole flow is idempotent — re-running `brr daemon install`
on an already-installed unit refreshes the file (paths may have
changed) and re-registers without churn.

There is no `--name` flag and no per-project install. One
machine = one daemon = one unit. Multi-project comes from the
registry, not from multiple units.

## What `uninstall` does

```
brr daemon uninstall

  1. Detect OS.
  2. Stop the unit if running:
       Linux:  `systemctl --user stop brr.service` (ignore "inactive")
       macOS:  `launchctl bootout gui/$UID/dev.brnrd.brr` (ignore "no such")
  3. Disable the unit:
       Linux:  `systemctl --user disable brr.service`
       macOS:  (handled by bootout)
  4. Remove the unit file from disk.
  5. Linux only: if linger was enabled by us AND no other
     user-services need it, prompt "Disable linger?" [y/N].
     Defaults to N (user may use it for other services).
  6. Leave `~/.config/brr/projects.toml` + the account
     binding at `~/.local/state/brr/account/` in place —
     uninstalling the supervisor doesn't reset which projects
     are brr-init'd or which brnrd account is paired. User
     can re-install later with no further setup. To wipe
     everything, see `brr daemon purge` (out of scope at
     launch; manual `rm -rf` of those two paths is the
     escape hatch).
  7. Print confirmation.
```

## Done definition

- `brr daemon install` works on macOS 12+ and any systemd-based
  Linux distro (Ubuntu 22.04+, Fedora 36+, Debian 12+, Arch).
- `brr daemon uninstall` cleanly tears down what `install`
  created; safe to re-run.
- `brr daemon status` reports OS-service-managed vs.
  foreground-supervisor modes uniformly, and lists the
  enabled projects from the registry.
- `brr daemon logs` tails the service's stdout/stderr — uses
  `journalctl --user -u brr -f` on Linux, `tail -F
  ~/Library/Logs/brr/brr.*.log` on macOS.
- `brr daemon list | adopt | forget` operate the project
  registry as described above.
- `brr init` appends to the registry; daemon picks up new
  entries within ~30s or immediately via IPC signal.
- The daemon runs per-project asyncio inbox-pollers off a
  single `httpx.AsyncClient` (per
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) §
  "Runtime profile: async, httpx, ASGI").
- Tests cover unit-file generation (no real `systemctl` /
  `launchctl` calls in CI — those go in a manual install
  matrix), registry round-trip (add / remove / list), and
  IPC-signal pick-up.
- README "Quickstart" updated to mention `brr daemon install`
  for the persistent setup AND `brr init` for per-project
  adoption.

## Out of scope

- **Windows daemon supervision.** Deferred per
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §4.
  Windows users at launch run `brr daemon up` in a terminal /
  Windows Terminal pane or wrap it manually.
- **System-wide install** (`/etc/systemd/system/`, `/Library/
  LaunchDaemons/`). Per-user is correct for this audience;
  system-wide would need sudo and is rarely the right shape.
- **Linux non-systemd distros** (Devuan, Void, Alpine, Gentoo
  with OpenRC). Tracked separately if a user asks; per-user
  init systems on those are less standardised. systemd-on-WSL
  works for the WSL crowd.
- **launchd LoginItems** for GUI-app-style "show in menubar"
  presence. Not the right shape for a daemon.
- **Daemon auto-update.** Out of scope; handled by `pip install
  --upgrade brr` + `brr daemon down && brr daemon up` (or
  `brr daemon restart`, future). Auto-updating system services
  is an antipattern.

## Why systemd + launchd, not a brr-rolled supervisor

The brr-rolled supervisor (today's `brr up` foreground process
with `--detach` for backgrounding) is fine for "I want to run
brr ad-hoc" but doesn't survive reboot, doesn't restart on
crash without scripting, doesn't integrate with the OS's
logging / status / introspection. Replacing it would mean
re-implementing systemd's `--user` mode in Python — a long road
that buys little over standing on the platforms' own service
managers.

The trade-off accepted: two unit-file templates (one per OS) to
maintain. Per-platform logic concentrated in
`src/brr/daemon_install/` (~200 LOC including detection,
templating, install / uninstall, status reporting). Smaller
maintenance surface than re-implementing supervision and more
predictable behaviour for users who already know systemd /
launchd.

## Open questions

- **macOS `LSUIElement` / dock presence.** A LaunchAgent
  doesn't show in the dock by default, which is correct for a
  daemon. If we ever ship a GUI menubar companion, that's a
  separate `dev.brnrd.brr-bar.plist` with its own controls.
- **First-run permission prompts on macOS.** macOS prompts on
  first network access ("brr wants to accept incoming
  connections"). Worth a README note; nothing to do in code.
- **`loginctl enable-linger` UX.** Requires sudo, which breaks
  the no-sudo promise. Acceptable because it's one-time, fully
  optional (skipping just delays daemon start to first login),
  and surfaced prominently in the install prompt.
- **Registry-watching mechanism.** Current sketch: ~30s
  re-scan OR immediate IPC signal from the CLI. Could also
  use inotify (Linux) / FSEvents (macOS) for zero-latency
  pick-up. Defer until the 30s-or-IPC shape ships and we see
  whether it's perceptibly slow in practice.

## Estimate

~200-300 LOC for the install / uninstall / status surface
(`src/brr/daemon_install/{__init__,linux.py,macos.py,detect.py}`),
~100 LOC for the unit-file templates, ~150 LOC for tests, ~80
LOC of CLI wiring under `brr daemon install | uninstall`,
plus ~150 LOC for the project registry + `brr daemon list |
adopt | forget` verbs and ~100 LOC for the IPC-signal
pick-up. Estimate adjusted for the machine-scoped reshape
(extra ~250 LOC for registry + adopt/forget); landed
alongside the async-runtime migration described in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) §
"Runtime profile: async, httpx, ASGI" (one coherent slice
rather than two transitional ones). Roughly a focused week
total; can land before or after the CLI reshape per
[`decision-cli-shape.md`](decision-cli-shape.md).

## Read next

1. [`decision-cli-shape.md`](decision-cli-shape.md) for the
   `brr daemon install | uninstall` verb placement in the
   noun-first CLI taxonomy.
2. [`subject-managed-mode.md`](subject-managed-mode.md) →
   "Daemon hosting" for the strategic frame (laptop is home;
   managed mode covers the gaps; cloud-first templates are
   niche).
3. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   for the *cloud-host* daemoning story this page is the
   laptop-host counterpart to.
4. [`plan-failover-compute.md`](plan-failover-compute.md) for
   the brnrd-managed-compute path that reduces the urgency of
   always-on laptop daemons.
5. [issue #29](https://github.com/Gurio/brr/issues/29) — the
   GitHub-side tracker for the cross-platform daemoning work.

## Lineage

- 2026-05-25 — drafted alongside the broader pass-4 follow-up
  reshape after the user reiterated "we need them for mac and
  linux, ideally natively installable" while reviewing the
  managed-mode launch shape. Replaces the placeholder reference
  to `plan-install-service.md` in
  [`subject-managed-mode.md`](subject-managed-mode.md) and
  [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md).
  Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (pass-4 follow-up — second wave).
- 2026-05-26 (locking pass IV — reshaped from per-project to
  machine-scoped multi-project daemon). The original shape
  had one systemd / launchd unit per brr-init'd project
  (`brr daemon install --name <project>` produced
  `brr-<project>.service` etc.), with each unit running a
  daemon pinned to a single repo via `WorkingDirectory`.
  Reshape:
  - **One unit per machine**, no per-project naming, no
    `--name` flag, no `WorkingDirectory` pinning. The daemon
    process serves all brr-init'd repos on the machine.
  - **New project registry** at `~/.config/brr/projects.toml`
    is the machine source of truth for what the daemon
    serves; the registry stays in place across daemon
    uninstall / reinstall.
  - **Account binding lives at `~/.local/state/brr/account/`**
    (machine-scoped, per
    [`design-config-layout.md`](design-config-layout.md) §
    "Account scope"). The `brnrd connect` flow on a second
    project skips the account-pair step on already-paired
    machines and goes straight to project-create +
    gate-pair.
  - **New verbs** `brr daemon list | adopt | forget` operate
    the registry; `brr init` appends to it automatically.
  - **Daemon picks up registry changes** within ~30s OR
    immediately via IPC signal from the CLI on add / remove.
  - Estimate revised up by ~250 LOC (registry + verbs +
    IPC); landed alongside the async-runtime migration in
    one coherent slice per
    [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
    § "Runtime profile: async, httpx, ASGI".
  Driven by the user's "probably the local daemon should
  serve all local projects and connect to the brnrd... if a
  user has configured brnrd once for a project already, we
  should pickup at least the account binding, subscription
  status, brnrd url, etc." The per-project shape would have
  duplicated account binding across projects and forced
  N supervisor units per developer with N repos — both
  user-friction wins move in the machine-scoped direction.
