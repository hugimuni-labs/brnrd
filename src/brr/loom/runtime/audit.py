"""Opt-in, record-only local tool journal. See docs/audit.md for the byte protocol.

One loom-owned thread writes all rooms. Hooks send digests, wait at most
DEADLINE seconds, and fail open. No arguments or result bodies cross the socket.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

from .home import Home, home_for, home_of_room
from .refusals import Parser, refuse

DOMAIN = b"brnrd.audit.entry.v1\0"
PAYLOAD = b"brnrd.audit.payload.v1\0"
ZERO = "0" * 64
DEADLINE = .005
MAX_REQUEST = 16384


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                      allow_nan=False).encode("ascii")


def digest(value: object) -> str:
    return hashlib.sha256(PAYLOAD + canonical(value)).hexdigest()


def entry_hash(value: dict) -> str:
    return hashlib.sha256(DOMAIN + canonical(value)).hexdigest()


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(path: Path, value: object) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    _sync_dir(path.parent)


def _rows(path: Path, size: int):
    with open(path, "rb") as stream:
        remaining = size
        while remaining:
            line = stream.readline(remaining)
            if not line:
                raise ValueError("journal shorter than committed bytes")
            remaining -= len(line)
            row = json.loads(line)
            if canonical(row) + b"\n" != line:
                raise ValueError(f"noncanonical bytes: {path}")
            yield row


def checkpoint(base: Path) -> dict:
    names = json.loads((base / "epochs.json").read_bytes())
    if not isinstance(names, list) or any(not isinstance(n, str) or Path(n).name != n for n in names):
        raise ValueError("invalid epoch manifest")
    if names != sorted(set(names)):
        raise ValueError("duplicate or reordered epochs")
    return {name: json.loads((base / name / "head.json").read_bytes()) for name in names}


def verify(base: Path, snapshot: dict | None = None) -> dict:
    """Read-only. The local committed head also detects a removed last entry.

    A crash between journal fsync and head replacement is reported, never repaired.
    Local rewrites of both files, or loss of the entire root, need a future witness.
    """
    live = snapshot is not None
    epochs = sorted(p for p in base.iterdir() if p.is_dir())
    if snapshot is None:
        snapshot = checkpoint(base)
    if not live and list(snapshot) != [p.name for p in epochs]:
        raise ValueError("epoch manifest disagrees (missing or extra epoch)")
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("no journal epochs")
    previous = ZERO
    seen = set()
    count = 0
    for name, expected in snapshot.items():
        if Path(name).name != name:
            raise ValueError("invalid epoch name")
        epoch = base / name
        path = epoch / "entries.jsonl"
        if not isinstance(expected, dict) or type(expected.get("bytes")) is not int or expected["bytes"] <= 0:
            raise ValueError("invalid committed byte count")
        if not live and path.stat().st_size != expected["bytes"]:
            raise ValueError("journal size disagrees with committed head")
        seq, head = 0, ZERO
        for row in _rows(path, expected["bytes"]):
            given = dict(row)
            claimed = given.pop("hash")
            if claimed != entry_hash(given) or given["prev"] != head or given["seq"] != seq:
                raise ValueError(f"broken chain: {epoch.name}:{seq}")
            if given["epoch"] != epoch.name or given["install"] != base.name:
                raise ValueError("wrong installation/epoch")
            if seq == 0:
                if given["kind"] != "epoch" or given["previous_epoch"] != previous:
                    raise ValueError("broken epoch link")
            else:
                if given["kind"] != "tool":
                    raise ValueError("unknown entry kind")
                event = given["event"]
                ident = event["id"]
                if not isinstance(ident, str) or not ident:
                    raise ValueError("invalid event id")
                if ident in seen:
                    raise ValueError("duplicate event id")
                seen.add(ident)
                for name in ("input_digest", "output_digest", "source_digest"):
                    value = event[name]
                    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                        raise ValueError("invalid payload digest")
                count += 1
            seq, head = seq + 1, claimed
        if not seq or expected != {"seq": seq - 1, "hash": head, "bytes": expected["bytes"]}:
            raise ValueError("committed head disagrees (loss or interrupted append)")
        previous = head
    if not epochs:
        raise ValueError("no journal epochs")
    return {"entries": count, "epochs": len(snapshot), "hash": previous}


class Journal:
    """Called only by the single writer; the lock fences other loom processes."""
    def __init__(self, home: Home):
        self.base = home.root / "audit" / home.install_id()
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = open(self.base / "writer.lock", "ab")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            epochs = sorted(p for p in self.base.iterdir() if p.is_dir())
            previous = verify(self.base)["hash"] if epochs or (self.base / "epochs.json").exists() else ZERO
            # New writer lifetime, explicit link to its predecessor. Never reopen
            # a corrupt tail or silently reset a missing committed head.
            self.epoch = self.base / f"{len(epochs):020d}-{secrets.token_hex(4)}"
            self.epoch.mkdir(mode=0o700)
            _sync_dir(self.base)
            self.stream = open(self.epoch / "entries.jsonl", "ab", buffering=0)
            self.seq, self.head = 0, ZERO
            self.append({"kind": "epoch", "previous_epoch": previous})
            _replace(self.base / "epochs.json", [p.name for p in epochs] + [self.epoch.name])
        except BaseException:
            if hasattr(self, "stream"):
                self.stream.close()
            self.lock.close()
            raise

    def append(self, value: dict) -> str:
        row = dict(value, seq=self.seq, prev=self.head, epoch=self.epoch.name, install=self.base.name)
        head = entry_hash(row)
        row["hash"] = head
        raw = canonical(row) + b"\n"
        if self.stream.write(raw) != len(raw):
            raise OSError("short journal write")
        os.fsync(self.stream.fileno())
        _replace(self.epoch / "head.json", {"seq": self.seq, "hash": head, "bytes": self.stream.tell()})
        self.seq, self.head = self.seq + 1, head
        return head

    def close(self):
        self.stream.close()
        self.lock.close()


def _endpoint(home: Home) -> Path:
    return home.root / "loom" / "audit-endpoint.json"


class Writer:
    def __init__(self, home: Home):
        self.home = home
        self.journal = Journal(home)
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(64)
        self.socket.settimeout(.1)
        self.token = secrets.token_hex(32)
        self.stop = threading.Event()
        self.error = None
        path = _endpoint(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace(path, {"port": self.socket.getsockname()[1], "token": self.token})
        path.chmod(0o600)
        self.thread = threading.Thread(target=self.run, name="audit-writer", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.socket.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(DEADLINE)
                try:
                    raw = b""
                    while not raw.endswith(b"\n") and len(raw) < MAX_REQUEST:
                        part = conn.recv(MAX_REQUEST - len(raw))
                        if not part:
                            raise ValueError("incomplete request")
                        raw += part
                    request = json.loads(raw)
                    if request["token"] != self.token or self.error:
                        continue
                    if request.get("action") == "verify":
                        conn.sendall(canonical(checkpoint(self.journal.base)) + b"\n")
                        continue
                    # No retries: an ack lost after fsync leaves one captured event.
                    try:
                        self.journal.append({"kind": "tool", "event": request["event"]})
                    except Exception as exc:
                        self.error = str(exc)
                        raise
                    conn.sendall(b"ok\n")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    # A client timeout after a successful append is harmless. A
                    # journal failure poisons this writer; never append past it.
                    if not isinstance(exc, (BrokenPipeError, ConnectionResetError, socket.timeout)):
                        self.error = str(exc)

    def close(self):
        self.stop.set()
        self.thread.join()
        self.socket.close()
        _endpoint(self.home).unlink(missing_ok=True)
        self.journal.close()


def _warn(home: Home) -> None:
    """Once per home, persistent across hook subprocesses; never a refusal."""
    try:
        try:
            fd = os.open(home.root / "loom" / "audit-warning", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        except OSError:
            fd = None
        if fd is not None:
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write("capture unavailable; calls continue; journal coverage is incomplete\n")
            except OSError:
                pass
        sys.stderr.write("audit: capture unavailable; calls continue; journal coverage is incomplete\n")
    except Exception:
        pass  # Even reporting a failed append must not fail the caller.



def note(room: Path, stdin_text: str, phase: str) -> None:
    home = None
    try:
        home = home_of_room(room)
        from .config import load_config
        if load_config(home.root).audit != "on":
            return
        payload = json.loads(stdin_text)
        if not isinstance(payload, dict) or not payload.get("tool_name"):
            return
        from .port import parse_boundary
        # Session id and the loom generation distinguish resumed and successor bodies.
        boundary = room / "port" / "in" / "boundary.md"
        gen = parse_boundary(boundary.read_text()).gen
        event = {"id": secrets.token_hex(16), "at_ns": time.time_ns(), "phase": phase,
                 "identity": payload.get("agent_id") or room.name,
                 "thread": os.environ.get("BRNRD_THREAD", ""), "strand": room.name,
                 "wake": gen, "session": payload.get("session_id", ""),
                 "agent": payload.get("agent_id", ""), "call": payload.get("tool_use_id", ""),
                 "engine": os.environ.get("BRNRD_BODY_SHELL", "unknown"), "mode": "pre-dispatch-hook",
                 "tool": payload["tool_name"], "cwd": payload.get("cwd", ""),
                 "input_digest": digest(payload.get("tool_input")),
                 "output_digest": digest(payload.get("tool_response")),
                 "source_digest": digest(payload),
                 "outcome": "refused" if phase == "refused" else
                     "failed" if payload.get("hook_event_name") == "PostToolUseFailure" else
                     "reported" if phase == "post" else "pending"}
        endpoint = json.loads(_endpoint(home).read_bytes())
        raw = canonical({"token": endpoint["token"], "event": event}) + b"\n"
        deadline = time.monotonic() + DEADLINE
        with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=DEADLINE) as conn:
            conn.settimeout(max(.000001, deadline - time.monotonic()))
            conn.sendall(raw)
            conn.settimeout(max(.000001, deadline - time.monotonic()))
            if conn.recv(3) != b"ok\n":
                raise OSError("no durable acknowledgement")
    except Exception:
        if home is not None:
            _warn(home)


def main(argv=None) -> int:
    parser = Parser(verb="audit", description="Verify the local record-only tool journal (no witness).")
    parser.add_argument("action", choices=["verify"])
    parser.add_argument("--home")
    parser.add_argument("--room")
    args = parser.parse_args(argv)
    home = home_for(args.home, args.room)
    try:
        endpoint_path = _endpoint(home)
        if endpoint_path.exists():
            endpoint = json.loads(endpoint_path.read_bytes())
            with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=30) as conn:
                conn.sendall(canonical({"token": endpoint["token"], "action": "verify"}) + b"\n")
                with conn.makefile("rb") as stream:
                    snapshot = json.loads(stream.readline(MAX_REQUEST))
            result = verify(home.root / "audit" / home.install_id(), snapshot)
        else:
            base = home.root / "audit" / home.install_id()
            with open(base / "writer.lock", "rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                result = verify(base)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return refuse("audit", exc, prefix="audit: incomplete or corrupt: ")
