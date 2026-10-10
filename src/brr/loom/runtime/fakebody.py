"""A body that calls the jack the way Claude Code does. Policies are pure functions of what it read."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .port import LetterView, parse_boundary

CALLS = 15
TOOL_S = 0.2
_THREAD = re.compile(r"on thread ([A-Za-z0-9._-]+)")


class JackView:
    def __init__(self, stdout: str):
        self.raw = stdout
        self.kind = "empty"
        self.letters: list[LetterView] = []
        text = stdout.strip()
        if not text:
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            self.kind = "text"
            self.letters = parse_boundary(stdout).letters
            return
        if "decision" in payload:
            self.kind = "block" if payload.get("decision") == "block" else "allow"
            reason = payload.get("reason") or ""
            if reason.strip():
                self.letters = parse_boundary(reason).letters
            return
        context = ((payload.get("hookSpecificOutput") or {}).get("additionalContext")) or ""
        self.kind = "post"
        if context.strip():
            self.letters = parse_boundary(context).letters


class Ctx:
    def __init__(self, room: Path):
        self.room = Path(room)
        self.env = os.environ.copy()

    def wake(self) -> str:
        return (self.room / "port" / "wake.md").read_text()

    def owed(self) -> list[LetterView]:
        # Wakes are the self's recipe, not the port wire format. The jack
        # delivers the same full owed list at start, whatever that recipe is.
        return self.jack("start").letters

    def jack(self, event: str) -> JackView:
        proc = subprocess.run(
            [sys.executable, "-m", "brr.loom.runtime", "jack",
             "--event", event, "--room", str(self.room)],
            input=json.dumps({"hookEventName": event}),
            capture_output=True, text=True, env=self.env, cwd=self.room,
        )
        return JackView(proc.stdout)

    def send(self, to: str, body: str = "", *, re: str | None = None,
             note: str | None = None) -> str:
        cmd = [sys.executable, "-m", "brr.loom.runtime", "send",
               "--room", str(self.room), "--to", to]
        if re:
            cmd.extend(["--re", re])
        if note is not None:
            cmd.extend(["--note", note])
        else:
            cmd.append(body)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=self.env, cwd=self.room,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"send exited {proc.returncode}")
        return proc.stdout.strip()

    def molt(self, why: str) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "brr.loom.runtime", "molt",
             "--room", str(self.room), "--why", why],
            capture_output=True, text=True, env=self.env, cwd=self.room,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "molt failed")

    def trace(self, payload: dict) -> None:
        path = self.room / "port" / "trace.jsonl"
        with path.open("a") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _own_thread(wake: str) -> str:
    match = _THREAD.search(wake)
    if match is None:
        raise RuntimeError("wake does not name the thread")
    return match.group(1)


def _target(wake: str) -> str:
    for line in wake.splitlines():
        if line.startswith("target:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("wake has no target:")


def answer_pings(ctx: Ctx) -> None:
    """Fifteen tool calls. A ping seen on one beat is answered on the next, so a kill can land between."""
    answered: set[str] = set()
    own = _own_thread(ctx.wake())

    def consume(letters: list[LetterView]) -> None:
        for letter in letters:
            if letter.id in answered:
                continue
            body = letter.body.strip()
            if body.startswith("ping-"):
                if not letter.reply.startswith("thread:"):
                    raise RuntimeError(f"{letter.id} has no reply thread ({letter.reply})")
                ctx.send(letter.reply, body[::-1], re=letter.id)
            else:
                ctx.send(f"thread:{own}", note="not a ping", re=letter.id)
            answered.add(letter.id)

    consume(ctx.owed())
    pending: list[LetterView] = []
    for n in range(1, CALLS + 1):
        time.sleep(TOOL_S)
        consume(pending)
        view = ctx.jack("post")
        ctx.trace({"n": n, "ids": [letter.id for letter in view.letters]})
        pending = view.letters
    time.sleep(TOOL_S)
    consume(pending)
    ctx.jack("stop")


def ping_two(ctx: Ctx) -> None:
    wake = ctx.wake()
    target = _target(wake)
    own = _own_thread(wake)
    openers = [letter for letter in ctx.owed()
               if not letter.body.strip().startswith("ping-")]
    for letter in openers:
        ctx.send(f"thread:{own}", note="opener", re=letter.id)
    opener_ids = {letter.id for letter in openers}
    for _ in range(80):
        view = ctx.jack("post")
        if not any(letter.id in opener_ids for letter in view.letters):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("opener still owed")
    ctx.send(f"thread:{target}", "ping-1")
    seen = ctx.jack("stop")
    ctx.trace({"event": "stop", "ids": [letter.id for letter in seen.letters],
               "kind": seen.kind})
    if seen.kind != "block":
        raise SystemExit(3)
    for letter in seen.letters:
        ctx.send(f"thread:{own}", note="seen", re=letter.id)
    ctx.send(f"thread:{target}", "ping-2")
    later = ctx.jack("stop")
    if later.kind == "block":
        for letter in later.letters:
            ctx.send(f"thread:{own}", note="seen", re=letter.id)


def molt_once(ctx: Ctx) -> None:
    flag = ctx.room / "port" / "molted"
    if flag.exists():
        (ctx.room / "port" / "wake-seen").write_text(ctx.wake())
        while True:
            time.sleep(0.2)
    flag.write_text("1\n")
    ctx.molt("fresh body")
    ctx.jack("stop")


def die_now(ctx: Ctx) -> None:
    del ctx
    raise SystemExit(1)


def quit_now(ctx) -> int:
    """Exit 0 at once without answering: a body that gives up politely."""
    return 0


def hold(ctx: Ctx) -> None:
    """Stay alive without answering, so a failover can see the flock held."""
    ctx.jack("start")
    while True:
        time.sleep(0.2)


def answer_fast(ctx: Ctx) -> None:
    """Answer every owed letter once, then stop."""
    own = _own_thread(ctx.wake())
    seen: set[str] = set()
    letters = ctx.owed()
    for letter in letters:
        if letter.id in seen:
            continue
        seen.add(letter.id)
        ctx.send(f"thread:{own}", note="ok", re=letter.id)
    ctx.jack("stop")


def wall_once(ctx: Ctx) -> None:
    """The first body ends on a provider limit line; the next one answers."""
    mark = ctx.room / "walled-once"
    if not mark.exists():
        mark.write_text("")
        print("You've hit your session limit · resets 3:40am (Europe/Paris)", flush=True)
        raise SystemExit(1)
    answer_fast(ctx)


def die_citing_limit(ctx: Ctx) -> None:
    """A crash whose output mentions a limit long before it ends."""
    del ctx
    print("retrying after rate limit\n" + "Traceback: boom\n" * 60, flush=True)
    raise SystemExit(1)


def emit_channel(ctx: Ctx) -> None:
    """Ten letters to the fake channel, then hold. A kill can land in the middle."""
    own = _own_thread(ctx.wake())
    for letter in ctx.owed():
        ctx.send(f"thread:{own}", note="opener", re=letter.id)
    for n in range(10):
        ctx.send("channel:fake", f"msg-{n}")
        time.sleep(0.05)
    while True:
        time.sleep(0.1)


def die_on_unrunnable(ctx: Ctx) -> None:
    """Die after being shown a letter whose body is ``unrunnable``. Answer the rest."""
    own = _own_thread(ctx.wake())
    letters = ctx.jack("start").letters
    if any(letter.body.strip() == "unrunnable" for letter in letters):
        raise SystemExit(1)
    for letter in letters:
        ctx.send(f"thread:{own}", note="ok", re=letter.id)
    ctx.jack("stop")


def self_commit(ctx: Ctx) -> None:
    """Read the live wake, commit in the clone and send it through immune."""
    from .selfrepo import git
    clone = Path.cwd()
    identity = (clone / "core" / "identity.md").read_text()
    if identity not in ctx.wake():
        raise RuntimeError("live wake omitted the self's identity")
    if "send-self --room ." not in ctx.wake():
        raise RuntimeError("live wake did not teach send-self")
    ctx.owed()
    # Let the loom ingest shown before the merge derives this body's label.
    for _ in range(100):
        letters = parse_boundary((ctx.room / "port" / "in" / "boundary.md").read_text()).letters
        if letters and all(letter.compact for letter in letters):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("shown letters were not ingested")
    (clone / "memory" / "moves" / "live-body.md").write_text("# A live body saw its self.\n")
    git(clone, "add", "memory/moves/live-body.md")
    git(clone, "commit", "-m", "A live body carries its self forward.")
    proc = subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", "send-self", "--room", "."],
        capture_output=True, text=True, env=ctx.env, cwd=clone,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    ctx.trace({"merged": proc.stdout.strip(), "cwd": str(clone)})
    answer_fast(ctx)


def echo_wake(ctx: Ctx) -> None:
    """Tell the person exactly what this live body was woken with."""
    for letter in ctx.owed():
        ctx.send("channel:fake", ctx.wake(), re=letter.id)
    ctx.jack("stop")


POLICIES = {
    "answer-pings": answer_pings,
    "ping-two": ping_two,
    "molt-once": molt_once,
    "die-now": die_now,
    "quit-now": quit_now,
    "hold": hold,
    "answer-fast": answer_fast,
    "wall-once": wall_once,
    "die-citing-limit": die_citing_limit,
    "emit-channel": emit_channel,
    "die-on-unrunnable": die_on_unrunnable,
    "self-commit": self_commit,
    "echo-wake": echo_wake,
}


def _ensure_src() -> None:
    src = str(Path(__file__).resolve().parents[3])
    parts = [part for part in os.environ.get("PYTHONPATH", "").split(os.pathsep) if part]
    if src not in parts:
        parts.insert(0, src)
        os.environ["PYTHONPATH"] = os.pathsep.join(parts)


def main(argv: list[str] | None = None) -> int:
    _ensure_src()
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime.fakebody")
    parser.add_argument("--room", required=True)
    parser.add_argument("--policy", required=True)
    args = parser.parse_args(argv)
    policy = POLICIES.get(args.policy)
    if policy is None:
        sys.stderr.write(f"fakebody: unknown policy {args.policy}\n")
        return 2
    os.environ["BRNRD_ROOM"] = str(Path(args.room).resolve())
    try:
        policy(Ctx(Path(args.room)))
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
