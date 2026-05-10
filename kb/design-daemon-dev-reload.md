# Design: developer reload for the brr daemon

Status: shipped on 2026-05-10

Design for making brr self-development less clumsy without adding a
general user-facing "restart brr" product feature. This page hangs off
the daemon subject hub, [`subject-daemon.md`](subject-daemon.md), and
the recent live-run ergonomics review,
[`research-runner-context-ergonomics-2026-05-09.md`](research-runner-context-ergonomics-2026-05-09.md).

## Problem

When developing brr itself, the long-running `brr up` process keeps the
old Python modules in memory. After an agent lands a bigger change, the
operator currently has to:

1. reinstall brr into the venv with `pip install .`;
2. switch to the terminal running `brr up`;
3. press Ctrl-C;
4. start `brr up` again.

That is tolerable for occasional package releases, but it is poor for
remote-assisted development of brr itself. The running daemon is the
thing receiving the Telegram/Slack task, so an agent cannot safely
restart it mid-task; killing the process before stdout capture, response
delivery, kb preflight, finalize, and push would break the task that
requested the reload.

## Goals

- Remove the normal need to reinstall brr after source-only changes.
- Remove the normal need to manually Ctrl-C and restart the foreground
  `brr up` terminal after each landed code change.
- Restart only after the current task has reached a safe boundary:
  response captured, event marked terminal, env finalized, kb maintenance
  done, and push attempted.
- Keep the normal user-facing daemon surface small: `brr up` and
  `brr down` remain the public lifecycle model.
- Stay stdlib-only.

## Non-goals

- Do not add a chat command like "restart brr".
- Do not let agents run `brr down`, `brr restart`, or lifecycle control
  commands from inside daemon tasks.
- Do not build a production supervisor. systemd, launchd, Docker, tmux,
  and future `brnrd` supervision are separate concerns.
- Do not attempt Python `importlib.reload()` inside the live daemon.
  Threads, module globals, class identity, and already-imported function
  references make in-process reload a worse abstraction than replacing
  the process image.
- Do not automate `pip install .`. Package installation mutates the
  operator's environment and should stay explicit.

## Recommendation

Use an editable install for brr development and add an opt-in
development reload mode to the foreground daemon:

```bash
pip install -e ".[dev]"       # once per venv, or after packaging metadata changes
brr up --dev-reload           # opt-in brr-development mode
```

`--dev-reload` watches brr's installed package directory for source and
package-data changes. When a change is detected before the next task
starts, the daemon immediately re-execs itself. If a task changes brr's
package files, the daemon waits until the task has fully finalized and
the push path has run, then re-execs.

The shipped implementation also accepts `dev_reload=true` in
`.brr/config` for operators who always want this behaviour in a brr
self-development checkout.

The re-exec keeps the same terminal session and can keep the same PID by
calling `os.execv`/`os.execve`; there is no shell supervisor required.
The new process imports brr from disk again, so an editable install
picks up source changes that were fast-forwarded into the main checkout
by the completed task.

This is intentionally a development mode, not a new product workflow.
It can be documented in brr's own Development section and omitted from
the quick-start command table. If exposed in argparse help, the label
should be explicit: "developer: re-exec daemon when brr package files
change".

## Option vs default

The implementation keeps reload explicit instead of making it an
unconditional `brr up` default. The deciding boundary is process
lifecycle: normal users and external supervisors should be able to treat
`brr up` as a stable foreground daemon that only stops on signal or
crash. Developer reload deliberately replaces the process image when
package files change, which is useful in an editable brr checkout but
surprising in packaged installs, supervised deployments, or repos where
the operator is not actively developing brr itself.

Auto-detecting editable installs was rejected as the default because it
turns local packaging shape into hidden lifecycle policy. A developer
who wants the behaviour can choose `--dev-reload` or `dev_reload=true`;
everyone else keeps the small public model: `brr up`, `brr down`, and
an external supervisor for uptime policy.

## Why editable install is the first fix

With a normal `pip install .`, Python imports brr from copied files in
`site-packages`. Source changes in the checkout do not affect the
running package until the operator reinstalls. No daemon restart design
can erase that packaging fact.

With `pip install -e ".[dev]"`, the venv imports brr from the checkout's
`src/brr`. When the daemon fast-forwards an agent's committed changes
back into the base checkout, a process restart is enough to load them.
Reinstall is only needed when packaging metadata changes, dependencies
change, or the editable install itself is missing.

## Implementation sketch

The shipped code uses a small `dev_reload.py` helper:

```python
class DevReloadWatcher:
    def __init__(self, package_dir: Path, extra_paths: list[Path] = ...):
        self._snapshot = snapshot(package_dir, extra_paths)

    def changed(self) -> bool:
        current = snapshot(...)
        if current == self._snapshot:
            return False
        self._snapshot = current
        return True
```

Snapshot entries should be stable tuples of relative path, size, and
`st_mtime_ns` for files under `Path(__file__).resolve().parent` with
extensions brr actually loads (`.py`, `.md`, `Dockerfile`, and maybe
package data without an extension). Include `pyproject.toml` when the
repo root appears to be the brr source checkout. The point is not a
perfect file watcher; it is a cheap polling guard evaluated on the same
cadence as the daemon inbox scan.

Daemon loop shape:

1. `brr up` accepts `--dev-reload` and passes `dev_reload=True` to
   `daemon.start`.
2. `daemon.start` creates the watcher only when `dev_reload` is set or
   `dev_reload=true` appears in `.brr/config`.
3. Each loop checks `watcher.changed()` before claiming a pending event.
   A change at this boundary re-execs before the next task starts.
4. After each task completes, after `_push_if_needed`, the daemon checks
   the watcher again and re-execs if the task changed brr package files.
5. Re-exec uses the same Python and argv:

   ```python
   env = os.environ.copy()
   env["BRR_REEXEC"] = "1"
   os.execve(sys.executable, [sys.executable, *sys.argv], env)
   ```

6. On startup, allow the existing PID file only when
   `BRR_REEXEC=1` and the PID in `.brr/daemon.pid` equals the current
   `os.getpid()`. Then rewrite the PID file and continue normally.
   Other duplicate-daemon checks stay strict.

The existing signal behavior should remain unchanged. Ctrl-C and
`brr down` still mean "drain and stop", not "restart".

## Tests

Covered by focused tests rather than an end-to-end self-reexec test:

- watcher detects `.py` and package-data changes and ignores unchanged
  snapshots;
- `daemon.start` permits an existing PID only for the current PID during
  `BRR_REEXEC`;
- `daemon.start` still rejects a different running PID;
- `--dev-reload` does not call the re-exec helper while `_run_worker` is
  active;
- when a change is detected during a task, the re-exec helper is called
  only after event status, finalize, kb maintenance, and push hooks have
  run;
- regular `brr down` / Ctrl-C behavior remains drain-and-stop.

The live Docker runner image used for brr self-work currently lacks
Python, pytest, and `rg` in some sessions; that means this feature
should be verified from a host/worktree brr development environment or
a project-layered Docker image, as noted in
[`research-runner-context-ergonomics-2026-05-09.md`](research-runner-context-ergonomics-2026-05-09.md).

## Rejected alternatives

| Alternative | Why not |
| ----------- | ------- |
| Public `brr restart` | Adds broad product surface for a development-only pain. Also needs wait/drain semantics and still does not solve non-editable installs. |
| Telegram/Slack "restart" command | Lets remote input control the process responsible for delivering that same remote response. Too easy to make self-referential and unsafe. |
| Agent writes `.brr/restart` marker | Puts lifecycle control into gitignored runtime scratch and invites agents to edit `.brr/`, which the playbook currently tells them not to do. |
| `importlib.reload()` | In-process reload is fragile with threads, module globals, and already-bound functions/classes. Re-exec is simpler and closer to how daemons normally reload code. |
| systemd/launchd/brnrd supervisor now | Correct layer for production uptime and fleet management, but too much ceremony for one-repo development reload. |

## Operator workflow

For brr self-development:

```bash
cd /path/to/brr
pip install -e ".[dev]"
brr up --dev-reload
```

Then leave the terminal alone. When an agent lands a source change,
the daemon finishes that task, pushes the result, notices the package
tree changed, and re-execs before processing the next event.

For normal brr users, nothing changes: install the package, configure a
gate, run `brr up`, stop it with `brr down` or Ctrl-C, and use an
external supervisor if they want always-on uptime.
