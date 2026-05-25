# Plan: laptop-side daemoning — macOS + Linux native install

**Status: proposed, not yet accepted on 2026-05-25.** Lifts the
"go add it to your startup scripts" friction by giving `brr daemon
install` first-class behaviour on macOS and Linux: write the OS's
native service unit, register it, hand the user back a daemon that
survives reboots without sudo. Companion to
[`subject-managed-mode.md`](subject-managed-mode.md) → "Daemon
hosting" (the strategic frame), to
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
(the *cloud-host* daemoning story; this page is the *laptop-host*
counterpart), and to
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

`brr daemon install` writes and registers a per-user service unit
in the OS's native service manager. `brr daemon uninstall` removes
it. No sudo. No system-wide installation. The unit runs as the
invoking user, lives in their home dir, survives reboots, restarts
on crash, and integrates with the OS's logging.

| Platform | Service manager | Unit location | Auto-start at boot |
|----------|----------------|---------------|-------------------|
| **Linux** | systemd (user instance) | `~/.config/systemd/user/brr.service` | Via `loginctl enable-linger $USER` (one-time, run by `install`) |
| **macOS** | launchd (LaunchAgent) | `~/Library/LaunchAgents/dev.brnrd.brr.plist` | Native — LaunchAgents auto-load at user login |
| **Windows** | — | — | **Deferred at launch.** See
   [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §4. |

When `brr daemon install` is followed by `brr daemon up`, the
verb operates the registered service (`systemctl --user start brr`
or `launchctl bootstrap`). When the service isn't installed, the
verb falls back to today's direct foreground/PID-file supervisor.
Users who prefer `tmux` / manual control aren't forced into
service registration.

`brr daemon status` reports both modes uniformly: service +
"managed by systemd, last started: …" line OR foreground +
"running directly under PID …" line.

`brr daemon uninstall` stops the service, removes the unit file,
and (on Linux) prompts about `loginctl disable-linger` if no
other systemd user units exist.

## Unit shapes

### Linux — systemd user unit

`~/.config/systemd/user/brr.service`:

```ini
[Unit]
Description=brr daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env brr daemon up --foreground
Restart=on-failure
RestartSec=5s
WorkingDirectory=%h/<configured project dir>
Environment=BRR_INSTALL_MANAGED=1

[Install]
WantedBy=default.target
```

`Restart=on-failure` covers daemon crashes; the daemon itself
gates its own re-spawn / re-init logic. `WorkingDirectory` is
set per-project at install time (the daemon is one-per-project
per [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Configuration"); users running N projects install N units
(`brr daemon install --name <project>` produces
`brr-<project>.service` to disambiguate).

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
    <key>WorkingDirectory</key>
    <string>/Users/<user>/<project>/</string>
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

`KeepAlive` with `SuccessfulExit: false` mirrors systemd's
`Restart=on-failure`. `RunAtLoad: true` loads on login. Logs land
in `~/Library/Logs/brr/` per macOS convention; `brr daemon logs`
(future) tails them.

The bundle identifier `dev.brnrd.brr` matches the eventual
`brnrd.dev` domain in reverse-DNS form — consistent with macOS
conventions and matches the product brand.

## What `install` does mechanically

```
brr daemon install [--name <project>]

  1. Detect OS (Linux → systemd; macOS → launchd; other → error
     with a useful message).
  2. Resolve project dir (cwd by default, or --project=<path>
     to install for a different project).
  3. Resolve absolute path to `brr` binary (via shutil.which) for
     ExecStart — survives PATH changes.
  4. Generate the unit file from the template (substituting
     project path, brr binary path, daemon name).
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
  9. Print next-steps: `brr daemon status` to verify;
     `brr daemon logs` to tail; `brr daemon uninstall` to remove.
```

The whole flow is idempotent — re-running `brr daemon install`
on an already-installed unit refreshes the file (paths may have
changed) and re-registers without churn.

## What `uninstall` does

```
brr daemon uninstall [--name <project>]

  1. Detect OS.
  2. Stop the unit if running:
       Linux:  `systemctl --user stop brr.service` (ignore "inactive")
       macOS:  `launchctl bootout gui/$UID/dev.brnrd.brr` (ignore "no such")
  3. Disable the unit:
       Linux:  `systemctl --user disable brr.service`
       macOS:  (handled by bootout)
  4. Remove the unit file from disk.
  5. Linux only: if no other brr-* units remain AND linger was
     enabled by us, prompt "Disable linger?" [y/N]. Defaults to
     N (user may use it for other services).
  6. Print confirmation.
```

## Multi-project on one machine

A user running brr for two projects from the same laptop runs:

```
cd ~/proj-alpha && brr daemon install --name alpha
cd ~/proj-beta  && brr daemon install --name beta
```

Generates `brr-alpha.service` / `dev.brnrd.brr.alpha.plist` and
`brr-beta.service` / `dev.brnrd.brr.beta.plist`, each registered
independently. `brr daemon status` shows all brr-* units.
`brr daemon up | down` without `--name` operates the current
project's unit (resolved from cwd).

## Done definition

- `brr daemon install` works on macOS 12+ and any systemd-based
  Linux distro (Ubuntu 22.04+, Fedora 36+, Debian 12+, Arch).
- `brr daemon uninstall` cleanly tears down what `install`
  created; safe to re-run.
- `brr daemon status` reports OS-service-managed vs.
  foreground-supervisor modes uniformly.
- `brr daemon logs` (sibling subcommand; new) tails the
  service's stdout/stderr — uses `journalctl --user -u brr -f`
  on Linux, `tail -F ~/Library/Logs/brr/brr.*.log` on macOS.
- Multi-project install with `--name` produces non-colliding
  units.
- Tests cover unit-file generation (no real `systemctl` /
  `launchctl` calls in CI — those go in a manual install matrix).
- README "Quickstart" updated to mention `brr daemon install`
  for the persistent setup.

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

- **Project-dir resolution at install time vs runtime.** Current
  sketch resolves project dir at install time (writes the path
  into the unit file). Alternative: resolve at start time via
  a small wrapper that reads a config pointer. Install-time is
  simpler and matches the "install per project" model; only
  matters if users routinely move project directories on disk
  (rare).
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

## Estimate

~200-300 LOC for the install / uninstall / status surface
(`src/brr/daemon_install/{__init__,linux.py,macos.py,detect.py}`),
~100 LOC for the unit-file templates, ~150 LOC for tests, ~80
LOC of CLI wiring under `brr daemon install | uninstall`.
Half-week of focused work; can land before or after the CLI
reshape per
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
