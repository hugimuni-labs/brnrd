# CLI reference

| Command | What it does |
|---------|--------------|
| `brnrd init [url]` | Create `AGENTS.md` + `kb/`, detect runner |
| `brnrd run "<task>"` | Run a task via the configured runner |
| `brnrd bind <repo> <gate>` | Bind a repo-local gate |
| `brnrd connect [url]` | Connect this daemon to the brnrd hosted service |
| `brnrd add <repo>` | Add a repo to the connected account home |
| `brnrd kb "<query>"` | Search home/repo knowledge |
| `brnrd up` | Start the daemon (foreground) |
| `brnrd down` | Stop the foreground daemon |
| `brnrd daemon up` | Start the installed daemon service, or foreground daemon if no service is installed |
| `brnrd daemon down` | Stop the installed daemon service, or foreground daemon if no service is installed |
| `brnrd daemon status` | Show service and foreground daemon status |
| `brnrd daemon install` | Install the native user service (systemd or LaunchAgent) |
| `brnrd daemon uninstall` | Remove the native user service |
| `brnrd daemon logs` | Tail native service logs |

Gates: `telegram`, `slack`, `github`.

## `.brr/config`

Lightweight runtime choices live in `.brr/config` as `key=value` lines —
the two most common:

- `runner=<name>` — pick a built-in runner profile (`claude`, `codex`,
  `gemini`) or a name defined in `.brr/runners.md`.
- `environment=<auto|host|worktree|docker>` — the daemon backend a task
  runs in. `auto` prefers configured Docker isolation, then falls back to
  worktree behavior. `host` runs directly in your checkout; `worktree`
  isolates each task onto its own git worktree; `docker` runs in a
  container with your AI CLI's host credentials (`~/.claude/`,
  `~/.codex/`, `~/.gemini/`) bind-mounted in automatically.

Deep customization (new gates, new runner profiles, environment plugins)
belongs in a local checkout, editable install, or fork — `.brr/config`
is intentionally limited to the choices most repos actually need to
change.

## Platform notes

On macOS, the first daemon run that opens network sockets can trigger the
system "accept incoming network connections" prompt. Allow it if you want
gates and managed brnrd traffic to reach the local daemon.
