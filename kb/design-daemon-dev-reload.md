# Design: developer reload for the brr daemon

Status: shipped on 2026-05-10

Developer reload is an opt-in brr self-development mode. It solves one
problem: the foreground `brr up` process keeps imported Python modules in
memory, so source changes landed by an agent are not visible until the
daemon process restarts. The shipped path pairs an editable install with a
quiescent re-exec between tasks.

Current synthesis lives in the daemon hub,
[`subject-daemon.md`](subject-daemon.md). The live-run ergonomics context
that motivated this design is in
[`research-runner-context-ergonomics-2026-05-09.md`](research-runner-context-ergonomics-2026-05-09.md).

## Current Shape

For brr self-development, run:

```bash
pip install -e ".[dev]"
brr up --dev-reload
```

`--dev-reload` watches brr's installed package directory for files brr
actually imports or ships as package data (`.py`, `.md`, `Dockerfile`, and
`pyproject.toml` when the source checkout is detectable). Operators who
always want this behavior in a brr checkout can set `dev_reload=true` in
`.brr/config`.

When a package-file change is detected before the daemon claims the next
pending event, the daemon re-execs immediately. When the current task
changes brr package files, the daemon waits until the task is fully safe:
stdout response captured, event marked terminal, kb maintenance complete,
environment finalized, and push attempted. Only then does it replace the
process image with the same Python executable and argv.

Normal lifecycle stays small and explicit:

- `brr up` starts a foreground daemon.
- `brr down` or Ctrl-C asks it to drain and stop.
- External supervisors own uptime policy.
- Remote gates and agents cannot request a daemon restart.

## Why It Is Opt-In

Re-exec is correct for brr development but surprising as a default. Normal
users and service supervisors should be able to treat `brr up` as a stable
foreground process that stops only on signal or crash. Editable-install
detection is also packaging shape, not lifecycle policy; a developer who
wants automatic reload chooses `--dev-reload` or `dev_reload=true`.

Editable install is the other half of the contract. With a normal
`pip install .`, Python imports brr from copied files in `site-packages`;
source changes in the checkout do not affect the running package until the
operator reinstalls. With `pip install -e ".[dev]"`, the venv imports from
`src/brr`, so a process restart is enough for source/package-data changes.
Reinstall is still needed when packaging metadata, dependencies, or the
editable install itself change.

## Mechanics

The implementation uses `DevReloadWatcher` in
[`dev_reload.py`](../src/brr/dev_reload.py). The watcher snapshots relative
path, size, and `st_mtime_ns` for the package files. `changed()` compares a
fresh snapshot to the stored one, updates the stored snapshot when a change
is found, and returns a boolean. This is intentionally a cheap polling
guard, not a filesystem notification subsystem.

Daemon integration is narrow:

1. `brr up --dev-reload` passes `dev_reload=True` to `daemon.start`.
2. `daemon.start` creates the watcher only when the flag is set or
   `.brr/config` contains `dev_reload=true`.
3. Each loop checks `watcher.changed()` before claiming a pending event.
4. After each task completes and `_push_if_needed` has run, the daemon
   checks the watcher again.
5. Re-exec uses the same Python and argv:

   ```python
   env = os.environ.copy()
   env["BRR_REEXEC"] = "1"
   os.execve(sys.executable, [sys.executable, *sys.argv], env)
   ```

6. On startup, the PID-file check permits the existing PID only when
   `BRR_REEXEC=1` and the PID in `.brr/daemon.pid` equals
   `os.getpid()`. Duplicate-daemon checks stay strict otherwise.

The existing signal behavior is unchanged. Ctrl-C and `brr down` still mean
"drain and stop", not "restart".

## Tests

Coverage is focused rather than trying to self-reexec the test process:

- watcher snapshots detect `.py` and package-data changes and ignore
  unchanged trees;
- `daemon.start` permits an existing PID only for the current PID during
  `BRR_REEXEC`;
- `daemon.start` still rejects a different running PID;
- dev reload does not call the re-exec helper while `_run_worker` is
  active;
- changes detected during a task call the re-exec helper only after event
  status, finalize, kb maintenance, and push hooks have run;
- regular `brr down` / Ctrl-C behavior remains drain-and-stop.

Older live Docker runner images used for brr self-work lacked Python,
pytest, and `rg` in some sessions. The bundled runner Dockerfile now
includes the baseline tools needed to run brr's normal dev install inside
the container, but verify against a freshly rebuilt image; stale local
`brr-runner:*` tags can still reproduce the old limitation noted in
[`research-runner-context-ergonomics-2026-05-09.md`](research-runner-context-ergonomics-2026-05-09.md).

## Rejected Alternatives

| Alternative | Why not |
| ----------- | ------- |
| Public `brr restart` | Adds broad product surface for a development-only pain. It also needs wait/drain semantics and still does not solve non-editable installs. |
| Telegram/Slack "restart" command | Lets remote input control the process responsible for delivering that same remote response. |
| Agent writes `.brr/restart` marker | Puts lifecycle control into gitignored runtime scratch and invites agents to edit `.brr/`. |
| `importlib.reload()` | In-process reload is fragile with threads, module globals, and already-bound functions/classes. Re-exec is simpler and closer to daemon reload behavior. |
| systemd/launchd/brnrd supervisor now | Correct layer for production uptime and fleet management, but too much ceremony for one-repo development reload. |

## Operator Workflow

For brr self-development:

```bash
cd /path/to/brr
pip install -e ".[dev]"
brr up --dev-reload
```

Then leave the terminal alone. When an agent lands a source change, the
daemon finishes that task, pushes the result, notices the package tree
changed, and re-execs before processing the next event.

For normal brr users, nothing changes: install the package, configure a
gate, run `brr up`, stop it with `brr down` or Ctrl-C, and use an external
supervisor if they want always-on uptime.
