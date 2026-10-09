#!/bin/sh
# The step-1 scenario on real Claude bodies: sleep 2, fifteen times, one kill.
# Writes the fold to /tmp/loom-demo-fold.md. Not part of CI.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
HOME_DIR=$(mktemp -d "${TMPDIR:-/tmp}/loom-demo.XXXXXX")
printf '%s\n' "$HOME_DIR" > /tmp/loom-demo-home.path
cd "$ROOT"

python3 - "$HOME_DIR" <<'PY'
import sys
from pathlib import Path
from brr.loom.runtime.ledger import inject_letter

root = Path(sys.argv[1])

def thread(name, readme, wait):
    directory = root / "self" / "threads" / name
    directory.mkdir(parents=True)
    (directory / "README.md").write_text(readme)
    (directory / "wait").write_text(wait)

thread("ta", """\
thread ta
Party A. Use Bash to run `sleep 2` exactly 15 times, one call each. Do not batch them.
After a tool call the hook shows owed letters. A letter whose body starts with ping- is answered on the NEXT sleep, not in the turn you first see it, by reversing the body (ping-1 -> 1-gnip):
python -m brr.loom.runtime send --re <id> --to <reply thread printed beside the letter> "<reversed>"
Any other owed letter, note it once:
python -m brr.loom.runtime send --re <id> --to thread:ta --note "not a ping"
After the 15th sleep, stop.
""", "90\n")
thread("tb", """\
thread tb
target: ta
Party B. Note each owed letter whose body does not start with ping-:
python -m brr.loom.runtime send --re <id> --to thread:tb --note opener
Then send ping-1 and stop. The jack blocks until the answer is shown. Note that answer:
python -m brr.loom.runtime send --re <id> --to thread:tb --note seen
Then send ping-2, stop, and note the answer the same way.
python -m brr.loom.runtime send --to thread:ta ping-1
""", "120\n")
inject_letter(root, to="thread:ta", body="begin")
inject_letter(root, to="thread:tb", body="begin")
PY

python3 -m brr.loom.runtime loom --home "$HOME_DIR" --adapter claude --core haiku --tick 0.2 \
  >"$HOME_DIR/loom.stdout" 2>"$HOME_DIR/loom.stderr" &
LOOM=$!
printf '%s\n' "$LOOM" > "$HOME_DIR/loom.pid"

python3 - "$HOME_DIR" "$LOOM" <<'PY'
import json, os, signal, sys, time
from pathlib import Path
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.project import holder

root = Path(sys.argv[1])
loom_pid = int(sys.argv[2])
home = Home(root)
deadline = time.monotonic() + 420
killed = False
wake = None
kill_pid = None

def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

def facts():
    try:
        return read_facts(home)
    except Exception:
        return []

def body_letters(rows, text):
    return [f for f in rows if f.kind == "letter" and f.data.get("body") == text]

def replies(rows, letter_id, text):
    return [f for f in rows if f.kind == "letter" and f.data.get("re") == letter_id
            and f.data.get("body") == text]

def shown(rows, letter_id, strand):
    return [f for f in rows if f.kind == "shown" and f.data.get("strand") == strand
            and letter_id in (f.data.get("ids") or [])]

def file_has(room, letter_id, kind):
    out = room / "port" / "out"
    if not out.is_dir():
        return False
    for path in out.glob("*.md"):
        text = path.read_text(errors="replace")
        if f"kind: {kind}" in text and letter_id in text:
            return True
    return False

while time.monotonic() < deadline:
    if not alive(loom_pid):
        break
    rows = facts()
    held = holder(rows, "ta")
    if held:
        strand = held[0]
        room = root / "rooms" / strand
        starts = [f for f in rows if f.kind == "body.started" and f.data.get("strand") == strand]
        if len(starts) >= 2 and wake is None and (room / "port" / "wake.md").is_file():
            wake = (room / "port" / "wake.md").read_text()
        pings = body_letters(rows, "ping-2")
        if (not killed and len(starts) == 1 and pings
                and not replies(rows, pings[0].data["id"], "2-gnip")
                and not file_has(room, pings[0].data["id"], "letter")
                and (shown(rows, pings[0].data["id"], strand)
                     or file_has(room, pings[0].data["id"], "shown"))):
            kill_pid = int(starts[0].data["pid"])
            try:
                os.killpg(kill_pid, signal.SIGKILL)
                killed = True
            except ProcessLookupError:
                killed = True
        ping1 = body_letters(rows, "ping-1")
        if (killed and wake is not None and len(ping1) == 1
                and len(replies(rows, ping1[0].data["id"], "1-gnip")) == 1
                and pings and len(replies(rows, pings[0].data["id"], "2-gnip")) == 1):
            break
    time.sleep(0.05)

rows = facts()
lines = ["# loom demo fold", "", f"home: {root}", f"killed: {killed} pid: {kill_pid}", ""]
if wake:
    lines += ["## successor wake", "", "```", wake.rstrip(), "```", ""]
lines.append("## facts")
lines.append("")
lines.append("| at | kind | id | data |")
lines.append("| --- | --- | --- | --- |")
for fact in rows:
    data = json.dumps(fact.data, sort_keys=True)
    if len(data) > 240:
        data = data[:240] + "…"
    lines.append(f"| {fact.at} | {fact.kind} | {fact.id} | `{data}` |")
lines.append("")
log = root / "loom" / "loom.log"
if log.is_file():
    lines += ["## loom.log", "", "```", log.read_text(errors="replace")[-4000:], "```", ""]
rooms = sorted((root / "rooms").glob("*")) if (root / "rooms").is_dir() else []
for room in rooms:
    body = room / "port" / "body.log"
    if body.is_file():
        lines += [f"## {room.name} body.log", "", "```", body.read_text(errors="replace")[-4000:], "```", ""]
Path("/tmp/loom-demo-fold.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines[:40]))
PY

kill -TERM "$LOOM" 2>/dev/null || true
# The loom reaps its own sessions once the stop lands. Give it a beat, then force it.
i=0
while kill -0 "$LOOM" 2>/dev/null && [ "$i" -lt 50 ]; do
  i=$((i + 1))
  sleep 0.1
done
kill -KILL "$LOOM" 2>/dev/null || true
wait "$LOOM" 2>/dev/null || true
