#!/bin/sh
# Two looms, one home, one haiku strand. Not CI.
#
# Starts both installs together, waits until a body is up, SIGKILLs the
# router, then prints the attention view and the lease fold. router_ttl is
# 8s here so the successor can show up before this script gives up; the
# code default stays 30s.
#
# Usage: scripts/loom-demo-router.sh [home-dir]
# Requires `claude` on PATH. Exit 2 if it is missing, 3 if no body started.
set -eu
if ! command -v claude >/dev/null 2>&1; then
  echo "claude is not on PATH; the live demo did not run" >&2
  exit 2
fi
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
HOME_DIR=${1:-${TMPDIR:-/tmp}/brnrd-loom-demo-router}
rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/loom" "$HOME_DIR/self/threads/demo"
cat > "$HOME_DIR/loom/config.toml" <<'EOF'
name = "brnrd"
router_ttl = 8
max_skew = 2
margin = 1
EOF
cat > "$HOME_DIR/self/threads/demo/README.md" <<'EOF'
---
id: demo
status: open
tense: plan
for: brnrd
---
# Demo
Say one sentence, then stop.
EOF
printf '30\n' > "$HOME_DIR/self/threads/demo/wait"
PY=${PYTHON:-python3}
exec "$PY" - "$HOME_DIR" <<'PY'
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from brr.loom.runtime.attention import view
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts

home = Path(sys.argv[1])
py = sys.executable
procs = {}
logs = {}


def facts():
    return read_facts(Home(home))


def kill_tree(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def cleanup() -> None:
    for proc in procs.values():
        if proc.poll() is None:
            kill_tree(proc.pid)
    try:
        seen = facts()
    except Exception:
        seen = []
    for fact in seen:
        if fact.kind != "body.started":
            continue
        try:
            kill_tree(int(fact.data["pid"]))
        except (KeyError, TypeError, ValueError):
            pass
    for handle in logs.values():
        handle.close()


def loom(install: str) -> None:
    handle = open(home / "loom" / f"{install}.err", "w")
    logs[install] = handle
    procs[install] = subprocess.Popen(
        [py, "-m", "brr.loom.runtime", "loom",
         "--home", str(home), "--install", install,
         "--adapter", "claude", "--core", "haiku", "--tick", "0.5"],
        env=os.environ.copy(), stdout=handle, stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def dump(title: str) -> None:
    print(f"--- {title} ---")
    try:
        seen = facts()
    except Exception as exc:
        print(f"facts unreadable: {exc}")
        seen = []
    print("attention:")
    for row in view(seen):
        print(row.render())
    keep = {
        "router", "router.renewed", "lease", "released",
        "body.started", "body.died", "body.exited",
        "note", "reply", "letter", "speech", "attention",
    }
    print("fold:")
    for fact in seen:
        if fact.kind in keep:
            print(f"{fact.at} {fact.kind} {fact.id} {fact.data}")
    for install, handle in logs.items():
        handle.flush()
        path = home / "loom" / f"{install}.err"
        text = path.read_text(errors="replace")[-1200:]
        if text.strip():
            print(f"loom {install} log:\n{text}")


try:
    letter = inject_letter(home, to="thread:demo", body="ping-1")
    print(f"letter {letter}")
    loom("aaaa")
    loom("bbbb")
    deadline = time.time() + 90
    started = False
    while time.time() < deadline:
        if any(fact.kind == "body.started" for fact in facts()):
            started = True
            break
        if procs and all(proc.poll() is not None for proc in procs.values()):
            break
        time.sleep(0.5)
    if not started:
        dump("no body started")
        sys.exit(3)
    time.sleep(2)
    seen = facts()
    routers = [fact for fact in seen if fact.kind == "router"]
    holder = str(routers[-1].data.get("install")) if routers else ""
    print(f"router {holder or '?'} — killing it while the strand is up")
    if holder in procs:
        kill_tree(procs[holder].pid)
    else:
        print("no router fact; leaving both looms up for the fold")
    succ = time.time() + 20
    while time.time() < succ:
        later = [fact for fact in facts() if fact.kind == "router"]
        if any(str(fact.data.get("install")) not in {holder, ""} for fact in later):
            break
        time.sleep(0.5)
    dump("after the kill")
finally:
    cleanup()
PY
